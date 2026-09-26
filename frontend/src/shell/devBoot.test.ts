import { describe, expect, it } from "vitest"
import { devBootScript, isLoopbackBind, isLoopbackHostHeader } from "./devBoot"

const ok = { bindHost: "localhost", remoteAddress: "127.0.0.1", hostHeader: "localhost:5173", token: "tok" }

function boot(script: string): { mode: string; token?: string } {
  return JSON.parse(script.replace(/^window\.__KAVACH__=/, "").replace(/;\s*$/, ""))
}

describe("devBootScript", () => {
  it("injects the token for a loopback client of a loopback-bound server", () => {
    const out = devBootScript(ok)
    expect(out.injected).toBe(true)
    expect(out.warning).toBeNull()
    expect(boot(out.script)).toEqual({ mode: "owner", token: "tok" })
  })

  it.each(["::1", "::ffff:127.0.0.1"])("accepts loopback address %s", (remoteAddress) => {
    expect(devBootScript({ ...ok, remoteAddress, hostHeader: "[::1]:5173" }).injected).toBe(true)
  })

  it.each([true, "0.0.0.0", "192.168.1.20", "kavach.local"] as const)("refuses when bound to %s (--host)", (bindHost) => {
    const out = devBootScript({ ...ok, bindHost })
    expect(out.injected).toBe(false)
    expect(out.warning).toMatch(/not bound to localhost/)
    expect(boot(out.script)).toEqual({ mode: "owner" })
  })

  it("refuses a non-loopback client", () => {
    const out = devBootScript({ ...ok, remoteAddress: "192.168.1.30" })
    expect(out.injected).toBe(false)
    expect(out.warning).toMatch(/non-loopback address/)
  })

  it("refuses a non-loopback Host header (DNS rebinding)", () => {
    for (const hostHeader of ["evil.example:5173", "localhost.evil.example", undefined]) {
      expect(devBootScript({ ...ok, hostHeader }).injected).toBe(false)
    }
  })

  it("warns when there is no token yet", () => {
    const out = devBootScript({ ...ok, token: null })
    expect(out.injected).toBe(false)
    expect(out.warning).toMatch(/owner API/)
  })

  it("cannot break out of the script", () => {
    expect(devBootScript({ ...ok, token: "</script><script>x" }).script).not.toContain("<")
  })
})

describe("helpers", () => {
  it("treats an unset host as localhost", () => {
    expect(isLoopbackBind(undefined)).toBe(true)
    expect(isLoopbackBind("127.0.0.1")).toBe(true)
  })

  it("strips ports from Host headers", () => {
    expect(isLoopbackHostHeader("127.0.0.1:5173")).toBe(true)
    expect(isLoopbackHostHeader("LOCALHOST")).toBe(true)
    expect(isLoopbackHostHeader("[::1]")).toBe(true)
  })
})
