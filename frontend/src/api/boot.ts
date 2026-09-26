// window.__KAVACH__ is injected into index.html by api.py (owner), requester/app.py (requester)
// or, under `npm run dev`, by the kavach-dev-boot Vite plugin (CONTRACT §9, §12, §14).

export type Mode = "owner" | "requester"

export interface Boot {
  mode?: Mode
  token?: string
}

declare global {
  interface Window {
    __KAVACH__?: Boot
  }
}

export function boot(): Boot {
  return (typeof window !== "undefined" && window.__KAVACH__) || {}
}

export function injectedMode(): Mode | null {
  const mode = boot().mode
  return mode === "owner" || mode === "requester" ? mode : null
}

export function ownerToken(): string | null {
  return boot().token ?? null
}

/** VITE_USE_FIXTURES=1: every API call returns fixtures/api/*.json, no backend needed. */
export function fixtureMode(): boolean {
  return import.meta.env.VITE_USE_FIXTURES === "1"
}

/** The owner/requester switch exists only on the dev server or when no server fixed the mode. */
export function modeSwitchAllowed(): boolean {
  return import.meta.env.DEV || injectedMode() === null
}
