// Fixture mode (VITE_USE_FIXTURES=1, CONTRACT §14, §15): answers every API call from fixtures/api/*.
// client.ts imports this lazily, so a normal build never loads it.
import chatStreamJsonl from "@fixtures/api/chat_stream.jsonl?raw"
import type { components } from "./types"

type S = components["schemas"]

const modules = import.meta.glob<unknown>("@fixtures/api/*.json", { eager: true, import: "default" })

export const fixtures: Record<string, unknown> = Object.fromEntries(
  Object.entries(modules).map(([file, value]) => [file.split("/").pop()!.replace(/\.json$/, ""), value]),
)

export function fixture<T>(name: string): T {
  if (!(name in fixtures)) throw new Error(`no fixture fixtures/api/${name}.json`)
  return structuredClone(fixtures[name]) as T
}

export const chatStreamEvents: { event: string; data: unknown }[] = chatStreamJsonl
  .split(/\r?\n/)
  .filter((line) => line.trim() !== "")
  .map((line) => JSON.parse(line))

/** Read-only routes and the fixture that answers each. Every fixtures/api file is used here or below (tested). */
export const GET_FIXTURES: [RegExp, string][] = [
  [/^\/api\/health$/, "health"],
  [/^\/api\/documents$/, "documents"],
  [/^\/api\/entities$/, "entities"],
  [/^\/api\/facts$/, "facts"],
  [/^\/api\/graph$/, "graph"],
  [/^\/api\/chunks\/[^/]+$/, "chunk"],
  [/^\/api\/memory\/timeline$/, "memory_timeline"],
  [/^\/api\/queue$/, "queue"],
  [/^\/api\/wallet$/, "wallet"],
  [/^\/api\/tasks$/, "tasks"],
  [/^\/api\/audit$/, "audit"],
  [/^\/api\/ingest\/events$/, "ingest_events"],
  [/^\/api\/outbox$/, "outbox"],
  [/^\/r\/identity$/, "r_identity"],
  [/^\/r\/requests$/, "r_requests"],
  [/^\/r\/storage$/, "r_storage"],
]

/** Fixtures used by POST routes and the chat stream. */
export const OTHER_FIXTURES = ["chat", "task_planned", "chat_stream"]

const ANSWER_FOR_ACTION: Record<string, S["RequestView"]["answer_type"]> = {
  approve: "ISSUER_PROOF",
  answer: "OWNER_ATTESTED",
  decline: "DECLINED",
  deny: "REFUSED",
}

const UPLOAD_DIRS: Record<string, string> = { pdf: "pdfs", md: "notes", txt: "chats" }

function now(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z")
}

export function fixtureResponse(method: string, url: string, body?: unknown): unknown {
  const [path, query = ""] = url.split("?")
  const params = new URLSearchParams(query)
  const json = (body ?? {}) as Record<string, unknown>
  let m: RegExpMatchArray | null

  if (method === "GET") {
    if (path === "/api/ingest/events") {
      // Events arrive once, like the real feed: nothing new after the fixture's last seq.
      const out = fixture<S["IngestEvents"]>("ingest_events")
      const since = Number(params.get("since") ?? 0)
      const events = out.events.filter((e) => e.seq > since)
      return { events, last_seq: events.length ? Math.max(...events.map((e) => e.seq)) : since }
    }
    if (path === "/api/claims") return { claims: [] } satisfies S["ClaimsOut"]
    const hit = GET_FIXTURES.find(([re]) => re.test(path))
    if (hit) return fixture(hit[1])
  }

  if (method === "POST") {
    if (path === "/api/chat") return fixture("chat")
    if (path === "/api/tasks") return fixture("task_planned")
    if (path === "/api/ingest") {
      const file = body instanceof FormData ? body.get("file") : null
      const name = file instanceof File ? file.name : "upload.md"
      const dir = UPLOAD_DIRS[name.split(".").pop()?.toLowerCase() ?? ""] ?? "notes"
      return { path: `${dir}/${name}` } satisfies S["IngestUploadOut"]
    }
    if (path === "/api/ingest/sync") return { ingested: [] } satisfies S["IngestSyncOut"]
    if (path === "/api/memory") {
      return { fact: fixture<S["Fact"][]>("facts")[0], superseded: [] } satisfies S["TeachResult"]
    }
    if (/^\/api\/memory\/candidates\/[^/]+\/decision$/.test(path)) return { stored: null } satisfies S["CandidateDecisionOut"]
    if ((m = path.match(/^\/api\/tasks\/([^/]+)\/decision$/))) {
      const task = fixture<S["Task"]>("task_planned")
      return { ...task, task_id: m[1], status: json.approve ? "done" : "rejected", decided_at: now() } satisfies S["Task"]
    }
    if ((m = path.match(/^\/api\/requesters\/([^/]+)\/decision$/))) {
      const all = fixture<S["QueueOut"]>("queue").requesters
      const r = all.find((x) => x.fingerprint === m![1]) ?? all[0]
      return { ...r, status: json.approve ? "paired" : "blocked", paired_at: json.approve ? now() : null } satisfies S["Requester"]
    }
    if ((m = path.match(/^\/api\/requests\/([^/]+)\/decision$/))) {
      const all = fixture<S["QueueOut"]>("queue").requests
      const r = all.find((x) => x.request_id === m![1]) ?? all[0]
      const answer_type = ANSWER_FOR_ACTION[String(json.action)] ?? null
      return { ...r, status: "done", answer_type, decided_at: now() } satisfies S["RequestView"]
    }
    if (path === "/r/ask") {
      return { local_id: `l_${Date.now().toString(16)}`, request_id: "rq_fixture01", status: "pending" }
    }
  }

  throw new Error(`fixture mode: no fixture for ${method} ${path}`)
}
