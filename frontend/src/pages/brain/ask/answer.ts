// Display logic for the Ask page, kept free of React so it can be unit tested.
// The citation check itself runs on the server (brain/chat.py); this only mirrors its sentence and
// citation parsing so the UI can place citation chips and mark the sentences the server flagged.
import type { ChatDone, ChatFinal, ChatMeta, ChatTurn, Citation, ChunkRef, Document, IngestEvent } from "@/api/client"

const CITE = /\[(\d+(?:\s*,\s*\d+)*)\]/g
const SENTENCE_END = /(?<=[.!?])\s+(?=[A-Z"'(\[])|\n+/g
const WORD = /\w/
const DONT_HAVE = /\b(?:dont|do not) have that\b/

export type Piece = { kind: "text"; text: string } | { kind: "cite"; n: number }

/** Text with `[1]`, `[1][2]` and `[1, 2]` markers turned into one `cite` piece per number. */
export function splitCitations(text: string): Piece[] {
  const out: Piece[] = []
  let last = 0
  for (const m of text.matchAll(CITE)) {
    if (m.index > last) out.push({ kind: "text", text: text.slice(last, m.index) })
    for (const n of m[1].split(",")) out.push({ kind: "cite", n: Number(n.trim()) })
    last = m.index + m[0].length
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) })
  return out
}

export function citedNumbers(text: string): number[] {
  return [...text.matchAll(CITE)].flatMap((m) => m[1].split(",").map((n) => Number(n.trim())))
}

export function saysNotInVault(text: string): boolean {
  const flat = text.toLowerCase().replaceAll("’", "'").replaceAll("'", "").replace(/[^a-z0-9]+/g, " ").trim()
  return DONT_HAVE.test(flat)
}

/**
 * Sentence spans over `text`, as brain/chat.py `sentences()` splits them: on sentence ends and newlines,
 * with a citation-only fragment joined to the sentence before it. Offsets index into the original text.
 */
export function sentenceSpans(text: string): { start: number; end: number }[] {
  const spans: { start: number; end: number }[] = []
  const push = (from: number, to: number) => {
    const raw = text.slice(from, to)
    const start = from + (raw.length - raw.trimStart().length)
    const end = to - (raw.length - raw.trimEnd().length)
    if (start >= end) return
    if (spans.length && !WORD.test(text.slice(start, end).replace(CITE, ""))) spans[spans.length - 1].end = end
    else spans.push({ start, end })
  }
  let last = 0
  for (const m of text.matchAll(SENTENCE_END)) {
    push(last, m.index)
    last = m.index + m[0].length
  }
  push(last, text.length)
  return spans
}

export interface Block {
  pieces: Piece[]
  /** A sentence the server's `uncited_sentence` flag refers to. */
  uncited: boolean
}

/**
 * The answer as blocks: sentences plus the whitespace between them. Sentences are marked uncited only when
 * the server raised `uncited_sentence`, using the same rule it applies (no `[n]`, not a "don't have that").
 */
export function answerBlocks(text: string, flags: string[] = []): Block[] {
  const mark = flags.includes("uncited_sentence")
  const blocks: Block[] = []
  let last = 0
  for (const { start, end } of sentenceSpans(text)) {
    if (start > last) blocks.push({ pieces: [{ kind: "text", text: text.slice(last, start) }], uncited: false })
    const sentence = text.slice(start, end)
    const uncited = mark && citedNumbers(sentence).length === 0 && !saysNotInVault(sentence)
    blocks.push({ pieces: splitCitations(sentence), uncited })
    last = end
  }
  if (last < text.length) blocks.push({ pieces: [{ kind: "text", text: text.slice(last) }], uncited: false })
  return blocks
}

export type Phase = "searching" | "reading" | "answering" | "done" | "stopped" | "error"

export interface Turn {
  id: string
  question: string
  phase: Phase
  startedAt: number
  meta?: ChatMeta
  text: string
  final?: ChatFinal
  done?: ChatDone
  error?: string
  /** LLM name from /api/health when the answer finished; absent when health was unavailable. */
  model?: string
  /** /api/health `local_inference` when the answer finished; absent when health was unavailable. */
  local?: boolean
}

/** Completed turns only, as the §10 `history`; stopped or failed answers are left out. */
export function buildHistory(turns: Turn[]): ChatTurn[] {
  return turns
    .filter((t) => t.phase === "done" && t.final)
    .flatMap((t): ChatTurn[] => [
      { role: "user", content: t.question },
      { role: "assistant", content: t.final!.answer },
    ])
}

export type SourceRef = ChunkRef & { quote?: string }

/** `n` -> what the chip shows: the final citation (with quote) once known, else the meta chunk ref. */
export function sourceMap(meta?: ChatMeta, final?: ChatFinal): Map<number, SourceRef> {
  const out = new Map<number, SourceRef>()
  for (const c of meta?.chunks ?? []) out.set(c.n, c)
  for (const c of final?.citations ?? []) out.set(c.n, c satisfies Citation)
  return out
}

export interface FlagView {
  /** Calm, informational: the vault has no answer. */
  notice: "no_context" | "not_in_vault" | null
  /** Warning badges. */
  warnings: { flag: string; label: string; hint: string }[]
}

const WARNINGS: Record<string, { label: string; hint: string }> = {
  no_citation: {
    label: "No citations",
    hint: "The answer cites none of your files. Treat it as unverified.",
  },
  invalid_citation: {
    label: "Unknown source cited",
    hint: "The answer cites a source number that was not retrieved for this question.",
  },
}

export function flagView(flags: string[]): FlagView {
  const notice = flags.includes("no_context") ? "no_context" : flags.includes("not_in_vault") ? "not_in_vault" : null
  const warnings = flags.filter((f) => f in WARNINGS).map((flag) => ({ flag, ...WARNINGS[flag] }))
  return { notice, warnings }
}

export function formatMs(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`
}

/** The trust footer: only what was measured. Where it ran comes from /api/health `local_inference`. */
export function footerText(turn: Turn): string | null {
  if (!turn.done) return null
  const where = turn.local === true ? " on this device" : turn.local === false ? " on your self-hosted server" : ""
  const parts = [`Answered${where}`]
  if (turn.final?.flags?.includes("no_context")) {
    parts.push("nothing matched, no model call", formatMs(turn.done.latency_ms))
  } else {
    if (turn.model) parts.push(turn.model)
    parts.push(`${formatMs(turn.done.first_token_ms)} first token / ${formatMs(turn.done.latency_ms)} total`)
  }
  return parts.join(" · ")
}

export function basename(path: string): string {
  return path.split(/[\\/]/).pop() || path
}

/** Chip label parts for a retrieved chunk: file name plus page for PDFs; notes and chats carry their name. */
export function sourceParts(ref: ChunkRef, doc?: Document): { name: string; page?: string } {
  const page = ref.locator.match(/^page (\d+)$/)
  if (page) return doc ? { name: basename(doc.path), page: `p.${page[1]}` } : { name: ref.locator }
  return { name: ref.locator.replace(/^(note|chat): /, "") }
}

export function sourceLabel(ref: ChunkRef, doc?: Document): string {
  const { name, page } = sourceParts(ref, doc)
  return page ? `${name} ${page}` : name
}

/** Newest first, one entry per seq, at most `cap`. */
export function mergeEvents(existing: IngestEvent[], incoming: IngestEvent[], cap = 12): IngestEvent[] {
  const bySeq = new Map<number, IngestEvent>()
  for (const e of [...existing, ...incoming]) bySeq.set(e.seq, e)
  return [...bySeq.values()].sort((a, b) => b.seq - a.seq).slice(0, cap)
}

export function timeAgo(iso: string, now = Date.now()): string {
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000))
  if (s < 10) return "just now"
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}
