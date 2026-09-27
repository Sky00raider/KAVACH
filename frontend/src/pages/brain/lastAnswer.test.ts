import { afterEach, describe, expect, it, vi } from "vitest"
import { LAST_ANSWER_KEY, clearLastAnswer, loadLastAnswer, saveLastAnswer } from "./lastAnswer"

function memoryStore() {
  const m = new Map<string, string>()
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
    m,
  }
}

afterEach(() => vi.unstubAllGlobals())

describe("last answer handoff (Ask -> Vault)", () => {
  it("round-trips and clears", () => {
    vi.stubGlobal("sessionStorage", memoryStore())
    expect(loadLastAnswer()).toBeNull()
    saveLastAnswer("Who is my landlord?", ["e_ravi", "e_owner"], new Date("2026-09-27T10:00:00Z"))
    expect(loadLastAnswer()).toEqual({ question: "Who is my landlord?", entities_used: ["e_ravi", "e_owner"], at: "2026-09-27T10:00:00.000Z" })
    clearLastAnswer()
    expect(loadLastAnswer()).toBeNull()
  })

  it("ignores junk and survives missing or throwing storage", () => {
    const store = memoryStore()
    vi.stubGlobal("sessionStorage", store)
    store.m.set(LAST_ANSWER_KEY, "{not json")
    expect(loadLastAnswer()).toBeNull()
    store.m.set(LAST_ANSWER_KEY, JSON.stringify({ question: "q", at: "t", entities_used: "e_1" }))
    expect(loadLastAnswer()).toBeNull()
    store.m.set(LAST_ANSWER_KEY, JSON.stringify({ question: "q", at: "t", entities_used: ["e_1", 3] }))
    expect(loadLastAnswer()?.entities_used).toEqual(["e_1"])

    vi.stubGlobal("sessionStorage", undefined)
    expect(loadLastAnswer()).toBeNull()
    expect(() => saveLastAnswer("q", [])).not.toThrow()

    vi.stubGlobal("sessionStorage", { getItem: () => { throw new Error("blocked") }, setItem: () => { throw new Error("full") }, removeItem: () => { throw new Error("x") } })
    expect(loadLastAnswer()).toBeNull()
    expect(() => saveLastAnswer("q", [])).not.toThrow()
    expect(() => clearLastAnswer()).not.toThrow()
  })
})
