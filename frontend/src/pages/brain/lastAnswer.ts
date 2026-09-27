/**
 * The entities the last Ask answer used (§10 `meta.entities_used`), handed from Ask to Vault.
 * sessionStorage: survives page switches and reloads in this tab, gone when the tab closes.
 */

export interface LastAnswer {
  question: string
  entities_used: string[]
  /** UTC ISO timestamp of the answer. */
  at: string
}

export const LAST_ANSWER_KEY = "kavach:last-answer"

function storage(): Storage | undefined {
  try {
    return globalThis.sessionStorage ?? undefined
  } catch {
    return undefined
  }
}

export function saveLastAnswer(question: string, entitiesUsed: string[], now = new Date()): void {
  const value: LastAnswer = { question, entities_used: entitiesUsed, at: now.toISOString() }
  try {
    storage()?.setItem(LAST_ANSWER_KEY, JSON.stringify(value))
  } catch {
    // storage full or blocked: the Vault highlight is a convenience
  }
}

export function loadLastAnswer(): LastAnswer | null {
  try {
    const raw = storage()?.getItem(LAST_ANSWER_KEY)
    if (!raw) return null
    const v = JSON.parse(raw) as Partial<LastAnswer>
    if (typeof v.question !== "string" || typeof v.at !== "string" || !Array.isArray(v.entities_used)) return null
    return { question: v.question, at: v.at, entities_used: v.entities_used.filter((id) => typeof id === "string") }
  } catch {
    return null
  }
}

export function clearLastAnswer(): void {
  try {
    storage()?.removeItem(LAST_ANSWER_KEY)
  } catch {
    // ignore
  }
}
