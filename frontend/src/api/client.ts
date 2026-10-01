// The only way pages talk to a backend (CONTRACT §9, §10, §12, §14).
// Owner calls carry X-Owner-Token from window.__KAVACH__; requester calls (/r/*) carry nothing.
// With VITE_USE_FIXTURES=1 every call is answered from fixtures/api instead.
import { fixtureMode, ownerToken } from "./boot"
import { createSSEParser } from "./sse"
import type { components } from "./types"
import type { components as RequesterComponents } from "./requester-types"

type S = components["schemas"]
type R = RequesterComponents["schemas"]

// Generated types (npm run gen:types), re-exported under their schema names.
export type Health = S["Health"]
export type Document = S["Document"]
export type HolderStatus = NonNullable<Document["holder_status"]>
export type Identity = S["Identity"]
export type Entity = S["Entity"]
export type Fact = S["Fact"]
export type FactVersion = S["FactVersion"]
export type Graph = S["Graph"]
export type Chunk = S["Chunk"]
export type Citation = S["Citation"]
export type ChatTurn = S["ChatTurn"]
export type ChatResult = S["ChatResult"]
export type MemoryCandidate = S["MemoryCandidate"]
export type TeachResult = S["TeachResult"]
export type CandidateDecisionOut = S["CandidateDecisionOut"]
export type Task = S["Task"]
export type QueueOut = S["QueueOut"]
export type Requester = S["Requester"]
export type RequestView = S["RequestView"]
export type WalletStatus = S["WalletStatus"]
export type AuditOut = S["AuditOut"]
export type AuditEntry = S["AuditEntry"]
export type OutboxItem = S["OutboxItem"]
export type IngestEvents = S["IngestEvents"]
export type IngestEvent = S["IngestEvent"]
export type IngestUploadOut = S["IngestUploadOut"]
export type IngestSyncOut = S["IngestSyncOut"]
export type ClaimsOut = S["ClaimsOut"]
export type RequestAction = S["RequestDecisionIn"]["action"]

export type RIdentity = R["RIdentity"]
export type RAskOut = R["RAskOut"]
export type RRequest = R["RRequest"]
export type RStorage = R["RStorage"]
export type VerifierOutput = R["VerifierOutput"]

// §10 stream payloads, documented on /api/chat/stream in the OpenAPI schema.
export type ChunkRef = S["ChunkRef"]
export type ChatMeta = S["ChatMetaData"]
export type ChatFinal = S["ChatFinal"]
export type ChatDone = S["ChatDoneData"]

export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(status: number, detail: unknown, message: string) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.detail = detail
  }

  static async from(res: Response): Promise<ApiError> {
    let detail: unknown = null
    try {
      detail = await res.json()
    } catch {
      detail = null
    }
    const msg = (detail as { detail?: unknown } | null)?.detail
    const hint = res.status === 401 && !ownerToken() ? " (no owner token: open the UI from this machine)" : ""
    return new ApiError(res.status, detail, `${res.status} ${typeof msg === "string" ? msg : res.statusText}${hint}`)
  }
}

type Query = Record<string, string | number | boolean | null | undefined>

function withQuery(path: string, query?: Query): string {
  if (!query) return path
  const params = new URLSearchParams()
  for (const [k, v] of Object.entries(query)) if (v !== undefined && v !== null) params.set(k, String(v))
  const qs = params.toString()
  return qs ? `${path}?${qs}` : path
}

/** Guarded by the build-time env var so a normal build drops the fixtures chunk entirely. */
function loadFixtures() {
  if (import.meta.env.VITE_USE_FIXTURES !== "1") throw new Error("fixture mode is off")
  return import("./fixtures")
}

function ownerHeaders(): Record<string, string> {
  const token = ownerToken()
  return token ? { "X-Owner-Token": token } : {}
}

interface CallOptions {
  query?: Query
  json?: unknown
  form?: FormData
  owner?: boolean
}

async function call<T>(method: "GET" | "POST" | "DELETE", path: string, opts: CallOptions = {}): Promise<T> {
  const url = withQuery(path, opts.query)
  if (fixtureMode()) {
    const { fixtureResponse } = await loadFixtures()
    return fixtureResponse(method, url, opts.form ?? opts.json) as T
  }
  const headers: Record<string, string> = opts.owner ? ownerHeaders() : {}
  let body: BodyInit | undefined
  if (opts.form) body = opts.form
  else if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json"
    body = JSON.stringify(opts.json)
  }
  const res = await fetch(url, { method, headers, body })
  if (!res.ok) throw await ApiError.from(res)
  return (await res.json()) as T
}

const get = <T>(path: string, query?: Query) => call<T>("GET", path, { query, owner: true })
const post = <T>(path: string, json?: unknown) => call<T>("POST", path, { json: json ?? {}, owner: true })
const seg = encodeURIComponent

/** Owner API (:8000), CONTRACT §9. */
export const api = {
  health: () => get<Health>("/api/health"),
  upload: (file: File) => {
    const form = new FormData()
    form.append("file", file)
    return call<IngestUploadOut>("POST", "/api/ingest", { form, owner: true })
  },
  ingestSync: () => post<IngestSyncOut>("/api/ingest/sync"),
  ingestEvents: (since = 0) => get<IngestEvents>("/api/ingest/events", { since }),
  documents: () => get<Document[]>("/api/documents"),
  identity: () => get<Identity>("/api/identity"),
  /** CONTRACT §6.6: the owner's UIDAI offline e-KYC ZIP + share code; 400/422 carry the reason as `detail`. */
  importAadhaar: (file: File, shareCode: string) => {
    const form = new FormData()
    form.append("file", file)
    form.append("share_code", shareCode)
    return call<Identity>("POST", "/api/identity/aadhaar", { form, owner: true })
  },
  /** Retire the Aadhaar anchor (409 when the anchor comes from a document). */
  removeIdentity: () => call<Identity>("DELETE", "/api/identity", { owner: true }),
  entities: (type?: string) => get<Entity[]>("/api/entities", { type }),
  facts: (opts: { field?: string; current?: boolean } = {}) => get<Fact[]>("/api/facts", opts),
  graph: (opts: { entity_id?: string; hops?: number } = {}) => get<Graph>("/api/graph", opts),
  chunk: (chunkId: string) => get<Chunk>(`/api/chunks/${seg(chunkId)}`),
  chat: (question: string, history: ChatTurn[] = []) => post<ChatResult>("/api/chat", { question, history }),
  teach: (statement: string) => post<TeachResult>("/api/memory", { statement }),
  decideCandidate: (id: string, remember: boolean) =>
    post<CandidateDecisionOut>(`/api/memory/candidates/${seg(id)}/decision`, { remember }),
  memoryTimeline: (field?: string) => get<FactVersion[]>("/api/memory/timeline", { field }),
  createTask: (instruction: string) => post<Task>("/api/tasks", { instruction }),
  decideTask: (id: string, approve: boolean) => post<Task>(`/api/tasks/${seg(id)}/decision`, { approve }),
  tasks: () => get<Task[]>("/api/tasks"),
  queue: () => get<QueueOut>("/api/queue"),
  decideRequester: (fp: string, approve: boolean) => post<Requester>(`/api/requesters/${seg(fp)}/decision`, { approve }),
  decideRequest: (id: string, action: RequestAction) => post<RequestView>(`/api/requests/${seg(id)}/decision`, { action }),
  wallet: () => get<WalletStatus>("/api/wallet"),
  audit: (limit = 200) => get<AuditOut>("/api/audit", { limit }),
  outbox: () => get<OutboxItem[]>("/api/outbox"),
  claims: () => call<ClaimsOut>("GET", "/api/claims"),
}

/** Requester backend (:9000), CONTRACT §12. No owner token. */
export const requesterApi = {
  identity: () => call<RIdentity>("GET", "/r/identity"),
  ask: (question: string) => call<RAskOut>("POST", "/r/ask", { json: { question } }),
  requests: () => call<RRequest[]>("GET", "/r/requests"),
  storage: () => call<RStorage>("GET", "/r/storage"),
}

/**
 * Is the owner API reachable from this (requester) browser? A no-cors GET of the public /api/claims:
 * the response is opaque, but it only resolves if the owner answered.
 */
export async function probeOwner(ownerUrl: string, timeoutMs = 3000): Promise<boolean> {
  if (fixtureMode()) return true
  try {
    await fetch(`${ownerUrl.replace(/\/+$/, "")}/api/claims`, {
      mode: "no-cors",
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    })
    return true
  } catch {
    return false
  }
}

export interface ChatStreamHandlers {
  onMeta?: (data: ChatMeta) => void
  onToken?: (data: { text: string }) => void
  onFinal?: (data: ChatFinal) => void
  onDone?: (data: ChatDone) => void
  /** Server `error` events, unparseable events, and a stream that ends without `done`. */
  onError?: (data: { message: string }) => void
}

export interface ChatStreamOptions {
  signal?: AbortSignal
  /** Fixture mode only: delay between replayed events (default 40 ms, tokens 25 ms). */
  fixtureDelayMs?: number
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason)
    const id = setTimeout(resolve, ms)
    signal?.addEventListener("abort", () => (clearTimeout(id), reject(signal.reason)), { once: true })
  })
}

/**
 * POST /api/chat/stream (CONTRACT §10), read with fetch + ReadableStream. Resolves when the stream ends;
 * rejects on HTTP errors or abort. Handlers fire in stream order.
 */
export async function chatStream(
  question: string,
  history: ChatTurn[],
  handlers: ChatStreamHandlers,
  opts: ChatStreamOptions = {},
): Promise<void> {
  let finished = false
  const dispatch = (event: string, data: unknown) => {
    switch (event) {
      case "meta":
        return handlers.onMeta?.(data as ChatMeta)
      case "token":
        return handlers.onToken?.(data as { text: string })
      case "final":
        return handlers.onFinal?.(data as ChatFinal)
      case "done":
        finished = true
        return handlers.onDone?.(data as ChatDone)
      case "error":
        finished = true
        return handlers.onError?.(data as { message: string })
    }
  }

  if (fixtureMode()) {
    const { chatStreamEvents } = await loadFixtures()
    for (const e of chatStreamEvents) {
      const base = opts.fixtureDelayMs ?? 40
      await sleep(e.event === "token" ? Math.min(base, 25) : base, opts.signal)
      dispatch(e.event, e.data)
    }
    return
  }

  const res = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { ...ownerHeaders(), "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ question, history }),
    signal: opts.signal,
  })
  if (!res.ok || !res.body) throw await ApiError.from(res)

  const parser = createSSEParser(({ event, data }) => {
    let parsed: unknown
    try {
      parsed = JSON.parse(data)
    } catch {
      return handlers.onError?.({ message: `unparseable '${event}' event` })
    }
    dispatch(event, parsed)
  })
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    parser.push(value)
  }
  parser.flush()
  if (!finished) handlers.onError?.({ message: "chat stream ended before 'done'" })
}
