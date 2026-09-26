import fs from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { defineConfig, searchForWorkspaceRoot, type Plugin } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import { DEV_BOOT_PATH, devBootScript, isLoopbackBind } from "./src/shell/devBoot.ts"

const here = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(here, "..")
const fixturesDir = path.join(repoRoot, "fixtures")

function readOwnerToken(): string | null {
  if (process.env.OWNER_TOKEN) return process.env.OWNER_TOKEN
  const keysDir = process.env.KEYS_DIR ? path.resolve(repoRoot, process.env.KEYS_DIR) : path.join(repoRoot, "keys")
  try {
    return fs.readFileSync(path.join(keysDir, "owner_token"), "utf-8").trim() || null
  } catch {
    return null
  }
}

/** `npm run dev` only: serves window.__KAVACH__ the way api.py injects it into the built index.html. */
function kavachDevBoot(): Plugin {
  return {
    name: "kavach-dev-boot",
    apply: "serve",
    transformIndexHtml: () => [{ tag: "script", attrs: { src: DEV_BOOT_PATH }, injectTo: "head-prepend" }],
    configureServer(server) {
      const bindHost = server.config.server.host
      if (!isLoopbackBind(bindHost)) {
        server.config.logger.warn(`[kavach] dev server bound to ${bindHost === true ? "all interfaces" : String(bindHost)}: owner token will NOT be injected`)
      }
      server.middlewares.use(DEV_BOOT_PATH, (req, res) => {
        const out = devBootScript({
          bindHost,
          remoteAddress: req.socket.remoteAddress,
          hostHeader: req.headers.host,
          token: readOwnerToken(),
        })
        if (out.warning) server.config.logger.warn(`[kavach] ${out.warning}`, { timestamp: true })
        res.setHeader("Content-Type", "text/javascript; charset=utf-8")
        res.setHeader("Cache-Control", "no-store")
        res.end(out.script)
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), kavachDevBoot()],
  resolve: {
    alias: {
      "@": path.join(here, "src"),
      "@fixtures": fixturesDir,
    },
  },
  server: {
    // Pinned to loopback: the proxy reaches the owner API from 127.0.0.1, so exposing the dev server
    // would make every LAN client look local. kavachDevBoot refuses the token if this is overridden.
    host: "localhost",
    port: 5173,
    strictPort: true,
    fs: { allow: [searchForWorkspaceRoot(here), fixturesDir] },
    proxy: {
      "/api/": { target: "http://127.0.0.1:8000" },
      "^/r/": { target: "http://127.0.0.1:9000" },
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
})
