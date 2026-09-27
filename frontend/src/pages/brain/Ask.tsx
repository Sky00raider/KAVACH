import { useEffect, useMemo, useRef, useState, type DragEvent, type KeyboardEvent } from "react"
import {
  AlertTriangle,
  ArrowUp,
  FileUp,
  Info,
  Laptop,
  RotateCcw,
  Server,
  ShieldAlert,
  Sparkles,
  Square,
  SquarePen,
} from "lucide-react"
import { api, chatStream, type Document } from "@/api/client"
import { usePoll } from "@/api/poll"
import { Page } from "@/components/shared/Page"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/components/lib/utils"
import {
  answerBlocks,
  basename,
  buildHistory,
  flagView,
  footerText,
  formatMs,
  sourceMap,
  type Block,
  type Turn,
} from "./ask/answer"
import { ACCEPT, IngestStrip, useIngest } from "./ask/IngestStrip"
import { CitationChip, SourcesRow, type DocIndex } from "./ask/Sources"
import { saveLastAnswer } from "./lastAnswer"

/** Empty-state suggestions. DATA: tune these to the demo vault. */
const SUGGESTED_QUESTIONS = [
  "How much is my rent, and when does the agreement end?",
  "What did I decide about renewing the flat?",
  "What salary was credited to my account last month?",
  "What did my landlord say in our last chat?",
]

const HEALTH_MS = 30_000
const STREAMING = new Set<Turn["phase"]>(["searching", "reading", "answering"])

function newId(): string {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`
}

function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(() => performance.now())
  useEffect(() => {
    const id = setInterval(() => setNow(performance.now()), 100)
    return () => clearInterval(id)
  }, [])
  return <span className="font-mono tabular-nums">{formatMs(Math.max(0, now - since))}</span>
}

function Pulse() {
  return (
    <span className="relative flex size-2">
      <span className="absolute inset-0 animate-ping rounded-full bg-primary/60" />
      <span className="relative size-2 rounded-full bg-primary" />
    </span>
  )
}

function AnswerText({ blocks, sources, docs, muted, streaming }: {
  blocks: Block[]
  sources: ReturnType<typeof sourceMap>
  docs: DocIndex
  muted: boolean
  streaming: boolean
}) {
  return (
    <p className={cn("text-[15px] leading-7 whitespace-pre-wrap", muted && "text-muted-foreground")}>
      {blocks.map((block, i) => {
        const body = block.pieces.map((p, j) => {
          if (p.kind === "text") return <span key={j}>{p.text}</span>
          const source = sources.get(p.n)
          return <CitationChip key={j} n={p.n} source={source} doc={source && docs.get(source.doc_id)} />
        })
        if (!block.uncited) return <span key={i}>{body}</span>
        return (
          <Tooltip key={i}>
            <TooltipTrigger asChild>
              <span className="underline decoration-warning/50 decoration-dotted decoration-1 underline-offset-[5px]">{body}</span>
            </TooltipTrigger>
            <TooltipContent>No citation for this sentence</TooltipContent>
          </Tooltip>
        )
      })}
      {streaming && <span className="ml-0.5 inline-block h-4 w-[2px] translate-y-0.5 animate-pulse bg-primary" />}
    </p>
  )
}

function TurnView({ turn, docs, onRetry }: { turn: Turn; docs: DocIndex; onRetry: () => void }) {
  const sources = useMemo(() => sourceMap(turn.meta, turn.final), [turn.meta, turn.final])
  const { notice, warnings } = flagView(turn.final?.flags ?? [])
  const blocks = useMemo(() => answerBlocks(turn.text, turn.final?.flags), [turn.text, turn.final])
  const cited = turn.final ? new Set(turn.final.citations.map((c) => c.n)) : undefined
  const chunks = turn.meta?.chunks ?? []
  const excluded = turn.final?.excluded_docs ?? []
  const waiting = turn.phase === "searching" || turn.phase === "reading"
  const partlyAnswered = notice === "not_in_vault" && (turn.final?.citations.length ?? 0) > 0
  const footer = footerText(turn)

  return (
    <article className="space-y-4">
      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-2xl rounded-br-md bg-secondary px-4 py-2.5 text-[15px] whitespace-pre-wrap">{turn.question}</p>
      </div>

      <div className="space-y-3">
        {waiting && (
          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            <Pulse />
            {turn.phase === "searching" ? (
              <span>Searching your vault…</span>
            ) : (
              <span>
                Searching your vault · reading {chunks.length} passage{chunks.length === 1 ? "" : "s"}…
              </span>
            )}
            <Elapsed since={turn.startedAt} />
          </p>
        )}

        {chunks.length > 0 && <SourcesRow sources={chunks.map((c) => sources.get(c.n) ?? c)} docs={docs} cited={cited} />}

        {excluded.length > 0 && (
          <ul className="space-y-1">
            {excluded.map((path) => (
              <li key={path} title={path} className="flex items-center gap-1.5 text-xs font-medium text-destructive">
                <ShieldAlert className="size-3.5 shrink-0" />
                Ignored {basename(path)}: signature check failed
              </li>
            ))}
          </ul>
        )}

        {turn.phase === "reading" && (
          <div className="space-y-2 pt-1" aria-hidden>
            <div className="h-3 w-11/12 animate-pulse rounded bg-muted" />
            <div className="h-3 w-3/4 animate-pulse rounded bg-muted [animation-delay:150ms]" />
          </div>
        )}

        {turn.text && (
          <div className="flex gap-2.5">
            {notice && !partlyAnswered && <Info className="mt-1.5 size-4 shrink-0 text-muted-foreground" />}
            <AnswerText
              blocks={blocks}
              sources={sources}
              docs={docs}
              muted={Boolean(notice) && !partlyAnswered}
              streaming={turn.phase === "answering"}
            />
          </div>
        )}

        {notice && (
          <p className="text-xs text-muted-foreground">
            {notice === "no_context"
              ? excluded.length > 0
                ? "The only matching files failed their signature check, so KAVACH did not use them or guess."
                : "Nothing in your vault matched this question. KAVACH answers only from your files, so it did not guess."
              : partlyAnswered
                ? "Part of this question isn't covered by your vault; that part was left unanswered."
                : "KAVACH answers only from your files, so it did not guess."}
          </p>
        )}

        {warnings.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {warnings.map((w) => (
              <Tooltip key={w.flag}>
                <TooltipTrigger asChild>
                  <span className="inline-flex cursor-help items-center gap-1 rounded-full border border-warning/40 bg-warning/10 px-2 py-0.5 text-xs font-medium text-warning">
                    <AlertTriangle className="size-3" />
                    {w.label}
                  </span>
                </TooltipTrigger>
                <TooltipContent className="max-w-64">{w.hint}</TooltipContent>
              </Tooltip>
            ))}
          </div>
        )}

        {turn.phase === "stopped" && <p className="text-xs text-muted-foreground">Stopped.</p>}

        {turn.phase === "error" && (
          <div className="flex items-center gap-3 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm">
            <AlertTriangle className="size-4 shrink-0 text-destructive" />
            <span className="min-w-0 flex-1 break-words">{turn.error ?? "Something went wrong."}</span>
            <Button size="sm" variant="outline" className="h-7 gap-1" onClick={onRetry}>
              <RotateCcw className="size-3.5" /> Retry
            </Button>
          </div>
        )}

        {footer && (
          <p className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
            {turn.local === false ? <Server className="size-3" /> : <Laptop className="size-3" />}
            {footer}
          </p>
        )}
      </div>
    </article>
  )
}

function EmptyState({ onAsk }: { onAsk: (q: string) => void }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div className="mb-4 grid size-11 place-items-center rounded-xl bg-primary/12 text-primary">
        <Sparkles className="size-5" />
      </div>
      <h2 className="text-lg font-semibold tracking-tight">Ask about anything in your vault</h2>
      <p className="mt-1 max-w-md text-sm text-muted-foreground">
        Answers come only from your documents, notes and chats, with a citation you can open for every sentence.
      </p>
      <div className="mt-6 flex max-w-xl flex-wrap justify-center gap-2">
        {SUGGESTED_QUESTIONS.map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => onAsk(q)}
            className="rounded-full border bg-card px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:border-primary/50 hover:text-foreground"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  )
}

export default function Ask() {
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState("")
  const [dragging, setDragging] = useState(false)
  const turnsRef = useRef(turns)
  turnsRef.current = turns
  const ctrlRef = useRef<AbortController | null>(null)
  const forceScroll = useRef(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const dragDepth = useRef(0)

  const ingest = useIngest()
  const health = usePoll(api.health, HEALTH_MS)
  const healthRef = useRef(health.data)
  healthRef.current = health.data
  const documents = usePoll(api.documents, null, [ingest.lastSeq])
  const docs: DocIndex = useMemo(
    () => new Map((documents.data ?? []).map((d: Document) => [d.doc_id, d])),
    [documents.data],
  )

  const busy = turns.some((t) => STREAMING.has(t.phase))

  async function send(question: string) {
    const q = question.trim()
    if (!q || ctrlRef.current) return
    const id = newId()
    const history = buildHistory(turnsRef.current)
    const ctrl = new AbortController()
    ctrlRef.current = ctrl
    forceScroll.current = true
    setInput("")
    setTurns((ts) => [...ts, { id, question: q, phase: "searching", startedAt: performance.now(), text: "" }])
    const patch = (fn: (t: Turn) => Turn) => setTurns((ts) => ts.map((t) => (t.id === id ? fn(t) : t)))
    try {
      await chatStream(
        q,
        history,
        {
          onMeta: (meta) => {
            saveLastAnswer(q, meta.entities_used)
            patch((t) => ({ ...t, meta, phase: "reading" }))
          },
          onToken: ({ text }) => patch((t) => ({ ...t, text: t.text + text, phase: "answering" })),
          onFinal: (final) => patch((t) => ({ ...t, final, text: final.answer })),
          onDone: (done) =>
            patch((t) => ({
              ...t,
              done,
              phase: "done",
              model: healthRef.current?.models.llm,
              local: healthRef.current?.local_inference,
            })),
          onError: ({ message }) => patch((t) => ({ ...t, phase: "error", error: message })),
        },
        { signal: ctrl.signal },
      )
    } catch (e) {
      if (ctrl.signal.aborted) patch((t) => ({ ...t, phase: "stopped" }))
      else patch((t) => ({ ...t, phase: "error", error: e instanceof Error ? e.message : String(e) }))
    } finally {
      ctrlRef.current = null
    }
  }

  function retry(turn: Turn) {
    setTurns((ts) => ts.filter((t) => t.id !== turn.id))
    send(turn.question)
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      send(input)
    }
  }

  // Follow the streaming answer while the owner is at the bottom; always jump there on send.
  useEffect(() => {
    const el = bottomRef.current
    const main = el?.closest("main")
    if (!el || !main) return
    const near = main.scrollHeight - main.scrollTop - main.clientHeight < 200
    if (forceScroll.current || near) el.scrollIntoView({ block: "end" })
    forceScroll.current = false
  }, [turns])

  useEffect(() => () => ctrlRef.current?.abort(), [])

  // The whole page is a drop target for vault files.
  const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer.types).includes("Files")
  const dropHandlers = {
    onDragEnter: (e: DragEvent) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      dragDepth.current += 1
      setDragging(true)
    },
    onDragOver: (e: DragEvent) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      e.dataTransfer.dropEffect = "copy"
    },
    onDragLeave: (e: DragEvent) => {
      if (!hasFiles(e)) return
      dragDepth.current = Math.max(0, dragDepth.current - 1)
      if (dragDepth.current === 0) setDragging(false)
    },
    onDrop: (e: DragEvent) => {
      if (!hasFiles(e)) return
      e.preventDefault()
      dragDepth.current = 0
      setDragging(false)
      ingest.upload(e.dataTransfer.files)
    },
  }

  return (
    <div className="relative flex min-h-full flex-col *:flex-1" {...dropHandlers}>
      <Page
        title="Ask my vault"
        description="Answers from your documents, notes and chats, with citations."
        actions={
          turns.length > 0 && (
            <Button
              size="sm"
              variant="outline"
              className="gap-1.5"
              disabled={busy}
              onClick={() => setTurns([])}
            >
              <SquarePen className="size-3.5" /> New chat
            </Button>
          )
        }
      >
        <IngestStrip state={ingest} onPick={() => fileRef.current?.click()} />
        <input
          ref={fileRef}
          type="file"
          accept={ACCEPT}
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files?.length) ingest.upload(e.target.files)
            e.target.value = ""
          }}
        />

        <div className="mx-auto w-full max-w-3xl flex-1">
          {turns.length === 0 ? (
            <EmptyState onAsk={send} />
          ) : (
            <div className="space-y-10 pb-4">
              {turns.map((t) => (
                <TurnView key={t.id} turn={t} docs={docs} onRetry={() => retry(t)} />
              ))}
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <div className="sticky bottom-0 -mx-8 -mb-8 bg-gradient-to-t from-background from-70% to-transparent px-8 pt-4 pb-6">
          <form
            className="mx-auto flex w-full max-w-3xl items-end gap-2 rounded-xl border bg-card p-2 shadow-sm focus-within:border-primary/50"
            onSubmit={(e) => {
              e.preventDefault()
              send(input)
            }}
          >
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Ask your vault…"
              aria-label="Question"
              rows={1}
              className="max-h-40 min-h-9 resize-none border-0 bg-transparent px-2 py-1.5 shadow-none focus-visible:ring-0 dark:bg-transparent"
            />
            {busy ? (
              <Button type="button" size="icon" variant="secondary" aria-label="Stop" onClick={() => ctrlRef.current?.abort()}>
                <Square className="size-3.5 fill-current" />
              </Button>
            ) : (
              <Button type="submit" size="icon" aria-label="Ask" disabled={!input.trim()}>
                <ArrowUp className="size-4" />
              </Button>
            )}
          </form>
        </div>
      </Page>

      {dragging && (
        <div className="pointer-events-none absolute inset-0 z-40">
          <div className="sticky top-0 grid h-dvh place-items-center bg-background/70 p-6 backdrop-blur-sm">
            <div className="flex flex-col items-center gap-2 rounded-2xl border-2 border-dashed border-primary/60 px-16 py-12 text-center">
              <FileUp className="size-7 text-primary" />
              <p className="font-medium">Drop to add to your vault</p>
              <p className="text-sm text-muted-foreground">PDFs, Markdown notes and chat exports (.txt). Stays on this device.</p>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
