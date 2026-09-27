import { describe, expect, it } from "vitest"
import type { Health } from "@/api/client"
import { HEALTH_KEY, loadHealth, saveHealth } from "./lastHealth"

function memoryStore() {
  const m = new Map<string, string>()
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), m }
}

const HEALTH = {
  ollama: true, db: true, models: { llm: "qwen2.5:3b" }, model_loaded: { llm: true }, vault_dir: "vault",
} as unknown as Health

describe("last known health", () => {
  it("round-trips the last answer", () => {
    const store = memoryStore()
    expect(loadHealth(store)).toBeUndefined()
    saveHealth(HEALTH, store)
    expect(loadHealth(store)).toEqual(HEALTH)
  })

  it("ignores junk and missing storage", () => {
    const store = memoryStore()
    store.m.set(HEALTH_KEY, "{not json")
    expect(loadHealth(store)).toBeUndefined()
    store.m.set(HEALTH_KEY, JSON.stringify({ hello: 1 }))
    expect(loadHealth(store)).toBeUndefined()
    expect(loadHealth(undefined)).toBeUndefined()
    expect(() => saveHealth(HEALTH, undefined)).not.toThrow()
  })

  it("survives a store that throws", () => {
    const broken = { getItem: () => { throw new Error("blocked") }, setItem: () => { throw new Error("quota") } }
    expect(loadHealth(broken)).toBeUndefined()
    expect(() => saveHealth(HEALTH, broken)).not.toThrow()
  })
})
