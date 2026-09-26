// Dev-server-only boot for `npm run dev` (CONTRACT §14). Mirrors api.py's index.html injection:
// the owner token goes only to a loopback client of a dev server that is itself bound to loopback,
// and only when the Host header names loopback (defeats DNS rebinding). Never part of a build.
// Pure functions, so vite.config.ts and the tests share them.

export const DEV_BOOT_PATH = "/__kavach_boot.js"

const LOOPBACK_NAMES = new Set(["localhost", "127.0.0.1", "::1", "[::1]"])
const LOOPBACK_ADDRS = new Set(["127.0.0.1", "::1", "::ffff:127.0.0.1"])

/** Vite's `server.host`: undefined means localhost; `true`, "0.0.0.0" or a LAN name exposes the server. */
export function isLoopbackBind(host: string | boolean | undefined): boolean {
  if (host === undefined) return true
  return typeof host === "string" && LOOPBACK_NAMES.has(host)
}

export function isLoopbackAddress(addr: string | undefined): boolean {
  return addr !== undefined && LOOPBACK_ADDRS.has(addr)
}

/** Host header without the port: "localhost:5173" -> "localhost", "[::1]:5173" -> "[::1]". */
export function isLoopbackHostHeader(host: string | undefined): boolean {
  if (!host) return false
  const name = host.startsWith("[") ? host.slice(0, host.indexOf("]") + 1) : host.split(":")[0]
  return LOOPBACK_NAMES.has(name.toLowerCase())
}

export interface DevBootInput {
  bindHost: string | boolean | undefined
  remoteAddress: string | undefined
  hostHeader: string | undefined
  token: string | null
}

export interface DevBootOutput {
  script: string
  injected: boolean
  warning: string | null
}

export function devBootScript(input: DevBootInput): DevBootOutput {
  const boot: { mode: "owner"; token?: string } = { mode: "owner" }
  let warning: string | null = null
  if (!isLoopbackBind(input.bindHost)) {
    warning = "dev server is not bound to localhost (--host?); owner token NOT injected"
  } else if (!isLoopbackAddress(input.remoteAddress)) {
    warning = `request from non-loopback address ${input.remoteAddress ?? "?"}; owner token NOT injected`
  } else if (!isLoopbackHostHeader(input.hostHeader)) {
    warning = `request with non-loopback Host ${input.hostHeader ?? "?"}; owner token NOT injected`
  } else if (!input.token) {
    warning = "no owner token yet: start the owner API once (it creates keys/owner_token) or set OWNER_TOKEN, then reload"
  } else {
    boot.token = input.token
  }
  const json = JSON.stringify(boot).replace(/</g, "\\u003c")
  return { script: `window.__KAVACH__=${json};\n`, injected: boot.token !== undefined, warning }
}
