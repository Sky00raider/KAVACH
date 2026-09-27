import type { Health } from "@/api/client"

/** The last /api/health answer, so a page load shows the last known status instead of "checking…". */
export const HEALTH_KEY = "kavach:last-health"

type Store = Pick<Storage, "getItem" | "setItem">

function browserStore(): Store | undefined {
  try {
    return globalThis.localStorage ?? undefined
  } catch {
    return undefined // storage blocked (private mode, sandboxed frame)
  }
}

export function loadHealth(store: Store | undefined = browserStore()): Health | undefined {
  try {
    const raw = store?.getItem(HEALTH_KEY)
    const parsed = raw ? JSON.parse(raw) : undefined
    return parsed && typeof parsed === "object" && typeof parsed.model_loaded === "object" ? (parsed as Health) : undefined
  } catch {
    return undefined
  }
}

export function saveHealth(health: Health, store: Store | undefined = browserStore()): void {
  try {
    store?.setItem(HEALTH_KEY, JSON.stringify(health))
  } catch {
    // quota or blocked storage: the pill just starts from "checking…" next time
  }
}
