import { useEffect, useState, type ReactNode } from "react"
import { Popover } from "radix-ui"
import {
  Calculator,
  CalendarClock,
  CircleCheck,
  CircleX,
  FileText,
  Loader2,
  MessageCircle,
  NotebookPen,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react"
import { api, type Chunk, type Document } from "@/api/client"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/components/lib/utils"
import { holderWarning, issuerName } from "../graph/graph"
import { fieldLabel, formatDate, formatValue } from "../memory/memory"
import { basename, sourceLabel, sourceParts, type SourceRef } from "./answer"
import { findHighlight, highlightCandidates, parseComputed, type ComputedLine, type SourceBit } from "./computed"

export type DocIndex = Map<string, Document>

const chunkCache = new Map<string, Promise<Chunk>>()

function loadChunk(id: string): Promise<Chunk> {
  let p = chunkCache.get(id)
  if (!p) {
    p = api.chunk(id)
    chunkCache.set(id, p)
    p.catch(() => chunkCache.delete(id))
  }
  return p
}

export function SourceIcon({ source, className }: { source?: string; className?: string }) {
  const Icon = source === "note" ? NotebookPen : source === "chat" ? MessageCircle : FileText
  return <Icon className={cn("size-3.5 shrink-0", className)} />
}

export function SignatureBadge({ status, iss, holder }: { status?: string; iss?: string | null; holder?: string | null }) {
  const warning = holderWarning(status, holder)
  if (warning) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-warning/12 px-1.5 py-0.5 text-[11px] font-medium text-warning">
        <ShieldAlert className="size-3" />
        {warning}
      </span>
    )
  }
  if (status === "issuer_signed") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-success/12 px-1.5 py-0.5 text-[11px] font-medium text-success">
        <ShieldCheck className="size-3" />
        Signed{iss ? ` by ${issuerName(iss)}` : ""}
      </span>
    )
  }
  if (status === "invalid") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-destructive/12 px-1.5 py-0.5 text-[11px] font-medium text-destructive">
        <ShieldAlert className="size-3" />
        Signature invalid
      </span>
    )
  }
  return null
}

/** The chunk text with the citation quote (an exact substring, from the server) or a given range highlighted. */
function Highlighted({ text, quote, range }: { text: string; quote?: string; range?: [number, number] | null }) {
  const at = quote ? text.indexOf(quote) : -1
  const [start, end] = range ?? (quote && at >= 0 ? [at, at + quote.length] : [-1, -1])
  if (start < 0) return <>{text}</>
  return (
    <>
      {text.slice(0, start)}
      <mark className="rounded-sm bg-primary/20 px-0.5 text-foreground">{text.slice(start, end)}</mark>
      {text.slice(end)}
    </>
  )
}

const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s)
const OP_TEXT: Record<string, string> = { "<": "under", "<=": "at most", ">": "over", ">=": "at least" }

function SourceLine({ source }: { source?: SourceBit }) {
  if (!source?.label) return null
  return (
    <span className="text-muted-foreground">
      {cap(source.label)}
      {source.file && <span className="font-mono text-[11px]"> · {source.file}</span>}
    </span>
  )
}

function Verdict({ met }: { met: boolean | null }) {
  if (met === null) return <span className="size-4 shrink-0" />
  const Icon = met ? CircleCheck : CircleX
  return <Icon className={cn("mt-0.5 size-4 shrink-0", met ? "text-success" : "text-destructive")} />
}

/** A known-fact or condition-check line the answer cited, as the owner reads it (`computed.ts`). */
function ComputedCard({ line }: { line: ComputedLine }) {
  return (
    <div className="space-y-2 rounded-md border border-primary/30 bg-primary/5 p-2.5 text-[13px]">
      <p className="flex items-center gap-1.5 text-[11px] font-medium tracking-wide text-primary uppercase">
        <Calculator className="size-3.5" /> Checked in code · {fieldLabel(line.field)}
      </p>
      {line.kind === "fact" && (
        <>
          <p className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-lg font-semibold tabular-nums">{formatValue(line.field, line.value)}</span>
            <span className="text-muted-foreground">today{line.since ? `, since ${formatDate(line.since)}` : ""}</span>
          </p>
          <p><SourceLine source={line.source} /></p>
          {line.next && (
            <p className="flex items-center gap-1.5 text-warning">
              <CalendarClock className="size-3.5 shrink-0" />
              Changes to {formatValue(line.field, line.next.value)} on {formatDate(line.next.on)}
            </p>
          )}
        </>
      )}
      {line.kind === "scheduled" && (
        <>
          <p className="flex flex-wrap items-center gap-x-2 text-warning">
            <CalendarClock className="size-3.5 shrink-0" />
            <span>
              From {formatDate(line.on)}:{" "}
              <strong className="font-semibold tabular-nums">{formatValue(line.field, line.value)}</strong>
            </span>
            {line.from && <span className="text-muted-foreground">(today {formatValue(line.field, line.from)})</span>}
          </p>
          <p><SourceLine source={line.source} /></p>
        </>
      )}
      {line.kind === "condition" && (
        <>
          <p className="italic">“{line.decision}”</p>
          <p className="text-muted-foreground">
            Condition: {fieldLabel(line.field).toLowerCase()} {OP_TEXT[line.op] ?? line.op}{" "}
            {formatValue(line.field, line.amount)}
          </p>
          <ul className="space-y-1">
            {line.rows.map((r) => (
              <li key={r.when} className="flex items-start gap-1.5">
                <Verdict met={r.met} />
                <span>
                  {r.when === "today" ? "Today" : `From ${formatDate(r.when)}`}:{" "}
                  <strong className="font-semibold tabular-nums">{r.value ? formatValue(line.field, r.value) : "–"}</strong>
                  {r.met !== null && <span className="text-muted-foreground"> · {r.met ? "met" : "not met"}</span>}
                  <br />
                  <SourceLine source={r.source} />
                </span>
              </li>
            ))}
          </ul>
          {line.breaksOn && (
            <p className="font-medium text-warning">Stops being met on {formatDate(line.breaksOn)}.</p>
          )}
        </>
      )}
    </div>
  )
}

function SourceCard({ source, doc }: { source: SourceRef; doc?: Document }) {
  const [chunk, setChunk] = useState<Chunk>()
  const [error, setError] = useState<string>()
  useEffect(() => {
    let live = true
    loadChunk(source.chunk_id).then(
      (c) => live && setChunk(c),
      (e: Error) => live && setError(e.message),
    )
    return () => {
      live = false
    }
  }, [source.chunk_id])

  const computed = parseComputed(source.quote)
  return (
    <div className="space-y-2.5">
      {computed && <ComputedCard line={computed} />}
      <div className="flex items-start gap-2">
        {source.n > 0 && (
          <span className="mt-px grid size-5 shrink-0 place-items-center rounded bg-primary/15 text-[11px] font-semibold text-primary">
            {source.n}
          </span>
        )}
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-1.5 truncate text-sm font-medium">
            <SourceIcon source={doc?.source} className="text-muted-foreground" />
            <span className="truncate">{doc ? basename(doc.path) : sourceLabel(source)}</span>
          </p>
          <p className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span className="font-mono">{source.locator || chunk?.locator}</span>
            <SignatureBadge status={doc?.signature_status} iss={doc?.iss} holder={doc?.holder_status} />
          </p>
        </div>
      </div>
      <div className="max-h-64 overflow-y-auto rounded-md border bg-muted/40 p-2.5 text-[13px] leading-relaxed whitespace-pre-wrap">
        {chunk ? (
          computed ? (
            <Highlighted text={chunk.text} range={findHighlight(chunk.text, highlightCandidates(computed))} />
          ) : (
            <Highlighted text={chunk.text} quote={source.quote} />
          )
        ) : error ? (
          <span className="text-destructive">Could not load this passage: {error}</span>
        ) : (
          <span className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" /> Loading passage…
          </span>
        )}
      </div>
    </div>
  )
}

/** Click target that opens the cited passage (GET /api/chunks/{id}). `n = 0` shows no number (graph edges). */
export function SourcePopover({ source, doc, children }: { source: SourceRef; doc?: Document; children: ReactNode }) {
  return (
    <Popover.Root>
      <Popover.Trigger asChild>{children}</Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          side="top"
          align="center"
          sideOffset={6}
          collisionPadding={12}
          className="z-50 w-[26rem] max-w-[calc(100vw-2rem)] rounded-lg border bg-popover p-3 text-popover-foreground shadow-xl outline-none animate-in fade-in-0 zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0"
        >
          <SourceCard source={source} doc={doc} />
          <Popover.Arrow className="fill-popover" />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}

/** Inline `[n]` in an answer. Numbers that match no retrieved chunk get a warning style and no popover. */
export function CitationChip({ n, source, doc }: { n: number; source?: SourceRef; doc?: Document }) {
  const base =
    "mx-0.5 inline-flex h-[1.15rem] min-w-[1.15rem] items-center justify-center rounded px-1 align-[0.1em] text-[11px] font-semibold leading-none"
  if (!source) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className={cn(base, "cursor-help bg-warning/15 text-warning")}>{n}</span>
        </TooltipTrigger>
        <TooltipContent>Source {n} was not retrieved for this question</TooltipContent>
      </Tooltip>
    )
  }
  return (
    <SourcePopover source={source} doc={doc}>
      <button
        type="button"
        aria-label={`Source ${n}: ${source.locator}`}
        className={cn(base, "bg-primary/15 text-primary transition-colors hover:bg-primary/30 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none")}
      >
        {n}
      </button>
    </SourcePopover>
  )
}

/** The retrieved chunks as chips. Once the answer is final, uncited ones fade back. */
export function SourcesRow({ sources, docs, cited }: {
  sources: SourceRef[]
  docs: DocIndex
  /** Cited numbers once the answer is final; undefined while streaming. */
  cited?: Set<number>
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {sources.map((s, i) => {
        const doc = docs.get(s.doc_id)
        const dim = cited !== undefined && !cited.has(s.n)
        const { name, page } = sourceParts(s, doc)
        const computed = parseComputed(s.quote)
        return (
          <SourcePopover key={s.n} source={s} doc={doc}>
            <button
              type="button"
              title={computed ? `${fieldLabel(computed.field)}, checked in code against ${name}` : undefined}
              style={{ animationDelay: `${i * 45}ms` }}
              className={cn(
                "inline-flex max-w-60 items-center gap-1.5 rounded-md border bg-card px-2 py-1 text-xs transition-all animate-in fade-in-0 slide-in-from-bottom-1 fill-mode-both hover:border-primary/50 hover:bg-accent",
                dim && "opacity-50 hover:opacity-100",
              )}
            >
              <span className={cn("font-semibold", dim ? "text-muted-foreground" : "text-primary")}>{s.n}</span>
              {computed ? (
                <Calculator className="size-3.5 shrink-0 text-primary" />
              ) : (
                <SourceIcon source={doc?.source} className="text-muted-foreground" />
              )}
              {computed && <span className="shrink-0 font-medium">{fieldLabel(computed.field)} ·</span>}
              <span className="truncate">{name}</span>
              {page && <span className="shrink-0 text-muted-foreground">{page}</span>}
              {doc?.signature_status === "issuer_signed" && (holderWarning(doc.signature_status, doc.holder_status)
                ? <ShieldAlert className="size-3 shrink-0 text-warning" />
                : <ShieldCheck className="size-3 shrink-0 text-success" />)}
              {doc?.signature_status === "invalid" && <ShieldAlert className="size-3 shrink-0 text-destructive" />}
            </button>
          </SourcePopover>
        )
      })}
    </div>
  )
}
