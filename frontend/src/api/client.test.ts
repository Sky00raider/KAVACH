import fs from "node:fs"
import path from "node:path"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { api, chatStream, requesterApi, type ChatStreamHandlers } from "./client"
import { chatStreamEvents, fixtures, GET_FIXTURES, OTHER_FIXTURES } from "./fixtures"

const FIXTURE_DIR = path.resolve(__dirname, "../../../fixtures/api")

function recorder() {
  const events: [string, unknown][] = []
  const handlers: ChatStreamHandlers = {
    onMeta: (d) => events.push(["meta", d]),
    onToken: (d) => events.push(["token", d]),
    onFinal: (d) => events.push(["final", d]),
    onDone: (d) => events.push(["done", d]),
    onError: (d) => events.push(["error", d]),
  }
  return { events, handlers }
}

afterEach(() => {
  vi.unstubAllEnvs()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe("fixture mode", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_USE_FIXTURES", "1")
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("fixture mode must not fetch"))))
  })

  it("uses every file in fixtures/api", () => {
    const onDisk = fs.readdirSync(FIXTURE_DIR).map((f) => f.replace(/\.(json|jsonl)$/, ""))
    const used = new Set([...GET_FIXTURES.map(([, name]) => name), ...OTHER_FIXTURES])
    expect([...used].sort()).toEqual([...onDisk].sort())
    for (const name of used) if (name !== "chat_stream") expect(fixtures).toHaveProperty(name)
  })

  it("answers GET routes from their fixtures", async () => {
    expect(await api.health()).toEqual(fixtures.health)
    expect(await api.documents()).toEqual(fixtures.documents)
    expect(await api.identity()).toEqual(fixtures.identity)
    expect((await api.importAadhaar(new File(["zip"], "offlineaadhaar.zip"), "4821")).source).toBe("aadhaar_okyc")
    expect(await api.removeIdentity()).toEqual(fixtures.identity)
    expect(await api.entities("person")).toEqual(fixtures.entities)
    expect(await api.facts({ current: false })).toEqual(fixtures.facts)
    expect(await api.graph({ entity_id: "e_owner", hops: 2 })).toEqual(fixtures.graph)
    expect(await api.chunk("c_3f9a1b2c4d")).toEqual(fixtures.chunk)
    expect(await api.memoryTimeline()).toEqual(fixtures.memory_timeline)
    expect(await api.queue()).toEqual(fixtures.queue)
    expect(await api.wallet()).toEqual(fixtures.wallet)
    expect(await api.tasks()).toEqual(fixtures.tasks)
    expect(await api.audit()).toEqual(fixtures.audit)
    expect(await api.outbox()).toEqual(fixtures.outbox)
    expect(await requesterApi.identity()).toEqual(fixtures.r_identity)
    expect(await requesterApi.requests()).toEqual(fixtures.r_requests)
    expect(await requesterApi.storage()).toEqual(fixtures.r_storage)
  })

  it("returns copies, so pages cannot mutate fixtures", async () => {
    const docs = await api.documents()
    ;(docs as unknown[]).length = 0
    expect(await api.documents()).toEqual(fixtures.documents)
  })

  it("answers POST routes", async () => {
    expect(await api.chat("what is my rent?")).toEqual(fixtures.chat)
    expect(await api.createTask("email the landlord")).toEqual(fixtures.task_planned)
    expect((await api.decideTask("t_x", false)).status).toBe("rejected")
    const queue = await api.queue()
    const rq = queue.requests[0]
    expect(await api.decideRequest(rq.request_id, "decline")).toMatchObject({
      request_id: rq.request_id,
      status: "done",
      answer_type: "DECLINED",
    })
    const who = queue.requesters[0]
    expect((await api.decideRequester(who.fingerprint, false)).status).toBe("blocked")
    expect(await api.upload(new File(["x"], "stmt.pdf"))).toEqual({ path: "pdfs/stmt.pdf" })
    expect((await requesterApi.ask("is income over 50k?")).status).toBe("pending")
  })

  it("delivers ingest events once, like the live feed", async () => {
    const first = await api.ingestEvents(0)
    expect(first.events.length).toBeGreaterThan(0)
    const again = await api.ingestEvents(first.last_seq)
    expect(again).toEqual({ events: [], last_seq: first.last_seq })
  })

  it("replays the canned chat stream in order", async () => {
    const { events, handlers } = recorder()
    await chatStream("q", [], handlers, { fixtureDelayMs: 0 })
    expect(events).toEqual(chatStreamEvents.map((e) => [e.event, e.data]))
    expect(events[0][0]).toBe("meta")
    expect(events.at(-1)![0]).toBe("done")
  })
})

describe("live mode", () => {
  function sseResponse(chunks: string[]): Response {
    const enc = new TextEncoder()
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        chunks.forEach((s) => c.enqueue(enc.encode(s)))
        c.close()
      },
    })
    return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } })
  }

  it("sends X-Owner-Token on owner calls only", async () => {
    vi.stubGlobal("window", { __KAVACH__: { mode: "owner", token: "tok123" } })
    const fetchMock = vi.fn(async () => Response.json({}))
    vi.stubGlobal("fetch", fetchMock)
    await api.facts({ field: "rent", current: false })
    await requesterApi.identity()
    const [ownerUrl, ownerInit] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(ownerUrl).toBe("/api/facts?field=rent&current=false")
    expect(ownerInit.headers).toMatchObject({ "X-Owner-Token": "tok123" })
    const [rUrl, rInit] = fetchMock.mock.calls[1] as unknown as [string, RequestInit]
    expect(rUrl).toBe("/r/identity")
    expect(rInit.headers).not.toHaveProperty("X-Owner-Token")
  })

  it("throws ApiError with status and detail", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "bad token" }, { status: 401 })))
    await expect(api.queue()).rejects.toMatchObject({ name: "ApiError", status: 401, detail: { detail: "bad token" } })
  })

  it("streams chat events from a chunked SSE body", async () => {
    vi.stubGlobal("window", { __KAVACH__: { mode: "owner", token: "tok" } })
    const fetchMock = vi.fn(async () =>
      sseResponse([
        'event: meta\ndata: {"entities_used":["e_owner"],"chunks":[]}\n\nevent: tok',
        'en\ndata: {"text":"Hi"}\n\n',
        'event: final\ndata: {"answer":"Hi","citations":[],"citation_ok":false,"flags":[],"memory_candidates":[]}\n\n',
        'event: done\ndata: {"latency_ms":10,"first_token_ms":3}\n\n',
      ]),
    )
    vi.stubGlobal("fetch", fetchMock)
    const { events, handlers } = recorder()
    await chatStream("hi?", [], handlers)
    expect(events.map(([e]) => e)).toEqual(["meta", "token", "final", "done"])
    expect(events[1][1]).toEqual({ text: "Hi" })
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe("/api/chat/stream")
    expect(init.method).toBe("POST")
    expect(init.headers).toMatchObject({ "X-Owner-Token": "tok" })
    expect(JSON.parse(String(init.body))).toEqual({ question: "hi?", history: [] })
  })

  it("reports server error events and streams that end early", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => sseResponse(['event: error\ndata: {"message":"ollama down"}\n\n'])))
    let rec = recorder()
    await chatStream("q", [], rec.handlers)
    expect(rec.events).toEqual([["error", { message: "ollama down" }]])

    vi.stubGlobal("fetch", vi.fn(async () => sseResponse(['event: token\ndata: {"text":"a"}\n\n'])))
    rec = recorder()
    await chatStream("q", [], rec.handlers)
    expect(rec.events.at(-1)).toEqual(["error", { message: "chat stream ended before 'done'" }])
  })
})
