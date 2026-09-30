import { useEffect, useMemo, useRef, useState, type ReactNode } from "react"
import { Popover } from "radix-ui"
import { Brain, CalendarClock, ChevronDown, History, Loader2, Search, ShieldAlert, ShieldCheck, Sparkles, UserRound } from "lucide-react"
import { toast } from "sonner"
import { api, ApiError, type Document, type FactVersion } from "@/api/client"
import { usePoll } from "@/api/poll"
import { Page } from "@/components/shared/Page"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/components/lib/utils"
import { useIngest } from "./ask/IngestStrip"
import { SourceIcon } from "./ask/Sources"
import {
  fieldLabel,
  formatDate,
  formatValue,
  groupTimeline,
  ownersVersions,
  matchesFilter,
  periodText,
  sourceView,
  taughtText,
  timelineOf,
  todayIso,
  untilText,
  type FieldGroup,
} from "./memory/memory"

type DocIndex = Map<string, Document>

const TEACH_EXAMPLE = "My gym fee went up to ₹1,500 a month from November"

function teachError(e: unknown): string {
  if (e instanceof ApiError && e.status === 503) return "The local model is not running, so KAVACH can't read that yet."
  if (e instanceof ApiError && e.status >= 500) return "Couldn't find a value to remember in that. Try \"My <thing> is <value>\"."
  return e instanceof Error ? e.message : String(e)
}

/** Source chip; click shows the exact sentence the value was read from. */
function SourceChip({ v, doc }: { v: FactVersion; doc?: Document }) {
  const s = sourceView(v, doc)
  const Icon = s.tone === "signed" ? ShieldCheck : s.tone === "owner" ? UserRound : s.tone === "other_holder" ? ShieldAlert : null
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          type="button"
          title="Show where this came from"
          className={cn(
            "inline-flex max-w-full items-center gap-1 rounded-full px-1.5 py-0.5 text-[11px] font-medium transition-colors",
            s.tone === "signed" && "bg-success/12 text-success hover:bg-success/20",
            s.tone === "owner" && "bg-primary/12 text-primary hover:bg-primary/20",
            s.tone === "doc" && "bg-muted text-muted-foreground hover:bg-accent hover:text-foreground",
            s.tone === "other_holder" && "bg-warning/12 text-warning hover:bg-warning/20",
          )}
        >
          {Icon ? <Icon className="size-3 shrink-0" /> : <SourceIcon source={doc?.source} className="size-3" />}
          <span className="truncate">{s.file ? `${s.label} · ${s.file}` : s.label}</span>
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          side="top"
          sideOffset={6}
          collisionPadding={12}
          className="z-50 w-[24rem] max-w-[calc(100vw-2rem)] space-y-2 rounded-lg border bg-popover p-3 text-popover-foreground shadow-xl outline-none animate-in fade-in-0 zoom-in-95"
        >
          <p className="text-xs text-muted-foreground">
            {s.label}
            {s.file && <span className="font-mono"> · {doc?.path}</span>}
            {v.confidence === "low" && " · the sentence doesn't clearly state this value"}
          </p>
          {v.quote && (
            <blockquote className="rounded-md border bg-muted/40 p-2.5 text-[13px] leading-relaxed">“{v.quote}”</blockquote>
          )}
          {v.source_type === "owner_stated" && (
            <p className="text-[11px] text-muted-foreground">Used when you chat with KAVACH. Never used to answer anyone else.</p>
          )}
          <Popover.Arrow className="fill-popover" />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}

function Support({ items, docs }: { items?: FactVersion[]; docs: DocIndex }) {
  if (!items?.length) return null
  return (
    <p className="flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground">
      <span>Also stated in</span>
      {items.map((s) => (
        <SourceChip key={s.fact_id} v={s} doc={s.doc_id ? docs.get(s.doc_id) : undefined} />
      ))}
    </p>
  )
}

function LowConfidence({ v }: { v: FactVersion }) {
  if (v.confidence !== "low") return null
  return <span className="rounded-full bg-warning/15 px-1.5 py-0.5 text-[11px] font-medium text-warning">unsure</span>
}

const SEGMENT_STYLE = {
  past: "border-border bg-muted text-muted-foreground",
  current: "border-primary/60 bg-primary/20 text-foreground font-medium",
  scheduled: "border-warning/60 bg-warning/15 text-warning font-medium",
} as const

/** The field's values over time on one bar, with a marker for today (`timelineOf`). */
function TimelineBar({ g }: { g: FieldGroup }) {
  const t = useMemo(() => timelineOf(g), [g])
  if (!t) return null
  return (
    <div className="pt-4" aria-label={`${fieldLabel(g.field)} over time`}>
      <div className="relative h-6">
        {t.segments.map((s) => (
          <div
            key={s.fact.fact_id}
            title={`${formatValue(g.field, s.fact.value)} · ${formatDate(s.from)}${s.kind === "scheduled" ? " (coming)" : ""}`}
            className={cn("absolute top-0 h-full truncate rounded-[5px] border px-1.5 text-[11px] leading-[22px] tabular-nums",
                          SEGMENT_STYLE[s.kind])}
            style={{ left: `${s.left}%`, width: `calc(${s.width}% - 2px)` }}
          >
            {formatValue(g.field, s.fact.value)}
          </div>
        ))}
        <div className="pointer-events-none absolute -top-3 bottom-[-4px] w-px bg-foreground/70" style={{ left: `${t.today}%` }}>
          <span className="absolute -top-1 left-1 text-[10px] leading-none whitespace-nowrap text-foreground/80">Today</span>
        </div>
      </div>
      <div className="mt-1.5 flex justify-between text-[10px] text-muted-foreground">
        <span>{t.ticks[0].label}</span>
        <span>{t.ticks[1].label}</span>
      </div>
    </div>
  )
}

function FieldCard({ g, docs, fresh }: { g: FieldGroup; docs: DocIndex; fresh: Set<string> }) {
  const [open, setOpen] = useState(false)
  const doc = (v: FactVersion) => (v.doc_id ? docs.get(v.doc_id) : undefined)
  const isFresh = [g.current, ...g.scheduled].some((v) => v && fresh.has(v.fact_id))
  return (
    <section
      className={cn(
        "flex flex-col gap-3 rounded-xl border bg-card p-4 transition-colors",
        isFresh && "border-primary/60 bg-primary/5 animate-in fade-in-0 duration-500",
      )}
    >
      <header className="flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-medium">{fieldLabel(g.field)}</h2>
        <span className="font-mono text-[11px] text-muted-foreground">{g.field}</span>
      </header>
      <TimelineBar g={g} />

      {g.current ? (
        <div className="space-y-1.5">
          <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span className="text-2xl font-semibold tracking-tight tabular-nums">{formatValue(g.field, g.current.value)}</span>
            <span className="text-xs text-muted-foreground">{periodText(g.current)}</span>
            <LowConfidence v={g.current} />
          </p>
          <div className="flex flex-wrap items-center gap-1.5">
            <SourceChip v={g.current} doc={doc(g.current)} />
          </div>
          <Support items={g.support.get(g.current.fact_id)} docs={docs} />
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">No value in force today.</p>
      )}

      {g.scheduled.map((v) => (
        <div key={v.fact_id} className="space-y-1.5 rounded-lg border border-warning/40 bg-warning/8 p-2.5">
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
            <CalendarClock className="size-4 text-warning" />
            {g.current ? (
              <span>
                Changes to <strong className="font-semibold tabular-nums">{formatValue(g.field, v.value)}</strong> on{" "}
                {formatDate(v.valid_from)}
              </span>
            ) : (
              <span>
                From {formatDate(v.valid_from)}: <strong className="font-semibold tabular-nums">{formatValue(g.field, v.value)}</strong>
              </span>
            )}
            <span className="text-xs text-muted-foreground">{v.valid_from ? untilText(v.valid_from) : ""}</span>
            <LowConfidence v={v} />
          </p>
          <div className="flex flex-wrap items-center gap-1.5 pl-6">
            <SourceChip v={v} doc={doc(v)} />
          </div>
          <Support items={g.support.get(v.fact_id)} docs={docs} />
        </div>
      ))}

      {g.past.length > 0 && (
        <div className="border-t pt-2">
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            className="flex w-full items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
          >
            <History className="size-3.5" />
            {g.past.length} earlier value{g.past.length === 1 ? "" : "s"}
            <ChevronDown className={cn("ml-auto size-3.5 transition-transform", open && "rotate-180")} />
          </button>
          {open && (
            <ol className="mt-2 space-y-2">
              {g.past.map((v) => (
                <li key={v.fact_id} className="space-y-1 text-xs">
                  <p className="flex flex-wrap items-baseline gap-x-2">
                    <span className="text-sm text-muted-foreground line-through decoration-muted-foreground/60 tabular-nums">
                      {formatValue(g.field, v.value)}
                    </span>
                    <span className="text-muted-foreground">{periodText(v)}</span>
                    <LowConfidence v={v} />
                  </p>
                  <SourceChip v={v} doc={doc(v)} />
                  <Support items={g.support.get(v.fact_id)} docs={docs} />
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </section>
  )
}

function TeachBox({ onTaught }: { onTaught: (fact: FactVersion["fact_id"]) => void }) {
  const [text, setText] = useState("")
  const [busy, setBusy] = useState(false)
  const [last, setLast] = useState<string>()

  const submit = async () => {
    const statement = text.trim()
    if (!statement || busy) return
    setBusy(true)
    try {
      const res = await api.teach(statement)
      const msg = taughtText(res.fact, res.superseded ?? [], todayIso())
      setLast(msg)
      setText("")
      toast.success(msg)
      onTaught(res.fact.fact_id)
    } catch (e) {
      toast.error(teachError(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="rounded-xl border bg-card p-4">
      <div className="mb-2 flex items-center gap-2">
        <Sparkles className="size-4 text-primary" />
        <h2 className="text-sm font-medium">Teach KAVACH</h2>
      </div>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start">
        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault()
              submit()
            }
          }}
          placeholder={TEACH_EXAMPLE}
          rows={2}
          className="min-h-0 flex-1 resize-none"
          disabled={busy}
          aria-label="Something to remember"
        />
        <Button onClick={submit} disabled={!text.trim() || busy} className="sm:w-32">
          {busy ? <Loader2 className="size-4 animate-spin" /> : <Brain className="size-4" />}
          Remember
        </Button>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        {last ?? "Read on this device. What you teach is used when you chat, never to answer anyone else."}
      </p>
    </section>
  )
}

function Stat({ children }: { children: ReactNode }) {
  return <span className="rounded-full border px-2 py-0.5 text-xs text-muted-foreground">{children}</span>
}

export default function Memory() {
  const ingest = useIngest()
  const [tick, setTick] = useState(0)
  const timeline = usePoll(() => api.memoryTimeline(), null, [ingest.lastSeq, tick])
  const documents = usePoll(() => api.documents(), null, [ingest.lastSeq])
  const [query, setQuery] = useState("")

  // facts that appear after the first load (a dropped file, a taught value) are highlighted
  const known = useRef<Set<string> | null>(null)
  const [fresh, setFresh] = useState<Set<string>>(new Set())
  useEffect(() => {
    const data = timeline.data
    if (!data) return
    if (known.current) {
      const added = data.filter((v) => !known.current!.has(v.fact_id)).map((v) => v.fact_id)
      if (added.length) setFresh((prev) => new Set([...prev, ...added]))
    }
    known.current = new Set(data.map((v) => v.fact_id))
  }, [timeline.data])

  const today = todayIso()
  const docs: DocIndex = useMemo(() => new Map((documents.data ?? []).map((d) => [d.doc_id, d])), [documents.data])
  const owners = useMemo(() => ownersVersions(timeline.data ?? [], docs), [timeline.data, docs])
  const groups = useMemo(() => groupTimeline(owners.mine, today), [owners, today])
  const shown = groups.filter((g) => matchesFilter(g, query))
  const coming = groups.reduce((n, g) => n + g.scheduled.length, 0)
  const changed = groups.reduce((n, g) => n + g.past.length, 0)

  return (
    <Page title="Memory" description="What KAVACH remembers about you, where each value came from, and how it changed.">
      <TeachBox onTaught={() => setTick((t) => t + 1)} />

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-full max-w-xs">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter fields or values" className="h-8 pl-8 text-sm" />
        </div>
        {groups.length > 0 && (
          <>
            <Stat>{groups.length} field{groups.length === 1 ? "" : "s"}</Stat>
            {coming > 0 && <Stat>{coming} change{coming === 1 ? "" : "s"} coming</Stat>}
            {changed > 0 && <Stat>{changed} earlier value{changed === 1 ? "" : "s"}</Stat>}
          </>
        )}
        {owners.hidden > 0 && (
          <span className="text-xs text-muted-foreground" title="Read from issuer-signed documents that are not in your name">
            {owners.hidden} value{owners.hidden === 1 ? "" : "s"} from documents in someone else's name not shown
          </span>
        )}
      </div>

      {timeline.error && !timeline.data ? (
        <p className="rounded-xl border border-destructive/40 p-4 text-sm text-destructive">Could not load memory: {timeline.error.message}</p>
      ) : timeline.loading && !timeline.data ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading memory…
        </p>
      ) : groups.length === 0 ? (
        <div className="rounded-xl border border-dashed py-16 text-center text-sm text-muted-foreground">
          Nothing remembered yet. Add documents to your vault or teach KAVACH above.
        </div>
      ) : shown.length === 0 ? (
        <p className="text-sm text-muted-foreground">No field matches “{query}”.</p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {shown.map((g) => (
            <FieldCard key={g.field} g={g} docs={docs} fresh={fresh} />
          ))}
        </div>
      )}
    </Page>
  )
}
