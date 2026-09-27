import { useCallback, useEffect, useRef, useState } from "react"
import { ChevronDown, Loader2, Plus } from "lucide-react"
import { toast } from "sonner"
import { api, ApiError, type IngestEvent } from "@/api/client"
import { POLL_MS, usePoll } from "@/api/poll"
import { Button } from "@/components/ui/button"
import { cn } from "@/components/lib/utils"
import { basename, mergeEvents, timeAgo } from "./answer"
import { SignatureBadge, SourceIcon } from "./Sources"

const PENDING_TIMEOUT_MS = 30_000
const COLLAPSED_KEY = "kavach.ask.ingestCollapsed"
export const ACCEPT = ".pdf,.md,.txt"

const SOURCE_BY_DIR: Record<string, string> = { pdfs: "pdf", notes: "note", chats: "chat" }

export interface IngestState {
  events: IngestEvent[]
  /** seqs that arrived after the first load (highlighted). */
  fresh: Set<number>
  pending: string[]
  lastSeq: number
  error?: Error
  upload: (files: FileList | File[]) => void
}

function uploadError(name: string, e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 409) return `A different file named ${name} is already in your vault`
    if (e.status === 415) return `${name}: only PDFs, Markdown notes (.md) and chat exports (.txt)`
  }
  return `${name}: ${e instanceof Error ? e.message : String(e)}`
}

/** Polls /api/ingest/events (CONTRACT §14, every 2 s) and uploads files into the vault (the watcher ingests). */
export function useIngest(): IngestState {
  const [events, setEvents] = useState<IngestEvent[]>([])
  const [fresh, setFresh] = useState<Set<number>>(new Set())
  const [pending, setPending] = useState<string[]>([])
  const since = useRef(0)
  const loaded = useRef(false)
  const poll = usePoll(() => api.ingestEvents(since.current), POLL_MS.ingestEvents)

  useEffect(() => {
    const out = poll.data
    if (!out) return
    since.current = Math.max(since.current, out.last_seq)
    if (out.events.length) {
      setEvents((prev) => mergeEvents(prev, out.events))
      const paths = new Set(out.events.map((e) => e.path))
      setPending((prev) => prev.filter((p) => !paths.has(p)))
      if (loaded.current) {
        setFresh((prev) => new Set([...prev, ...out.events.map((e) => e.seq)]))
        for (const e of out.events) toast.success(`Ingested ${basename(e.path)}`, { description: e.path })
      }
    }
    loaded.current = true
  }, [poll.data])

  const upload = useCallback((files: FileList | File[]) => {
    for (const file of Array.from(files)) {
      api.upload(file).then(
        ({ path }) => {
          setPending((prev) => (prev.includes(path) ? prev : [...prev, path]))
          setTimeout(() => setPending((prev) => prev.filter((p) => p !== path)), PENDING_TIMEOUT_MS)
        },
        (e) => toast.error(uploadError(file.name, e)),
      )
    }
  }, [])

  return { events, fresh, pending, lastSeq: events[0]?.seq ?? 0, error: poll.error, upload }
}

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSED_KEY) === "1"
  } catch {
    return false
  }
}

function EventChip({ event, fresh }: { event: IngestEvent; fresh: boolean }) {
  const counts = [
    event.entities_added ? `+${event.entities_added} entit${event.entities_added === 1 ? "y" : "ies"}` : "",
    event.facts_added ? `+${event.facts_added} fact${event.facts_added === 1 ? "" : "s"}` : "",
  ].filter(Boolean)
  return (
    <li
      title={`${event.path} · ingested ${new Date(event.ts).toLocaleString()}`}
      className={cn(
        "flex max-w-64 shrink-0 items-center gap-2 rounded-md border bg-card px-2.5 py-1.5 text-xs",
        fresh && "border-primary/50 bg-primary/5 animate-in fade-in-0 slide-in-from-left-2 duration-500",
      )}
    >
      <SourceIcon source={SOURCE_BY_DIR[event.path.split("/")[0]]} className="text-muted-foreground" />
      <span className="min-w-0">
        <span className="block truncate font-medium">{basename(event.path)}</span>
        <span className="block truncate text-[11px] text-muted-foreground">
          {[timeAgo(event.ts), ...counts].join(" · ")}
        </span>
      </span>
      <SignatureBadge status={event.signature_status} />
    </li>
  )
}

/** Live auto-ingest strip: what the vault watcher picked up, newest first. Compact and collapsible. */
export function IngestStrip({ state, onPick }: { state: IngestState; onPick: () => void }) {
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const toggle = () => {
    setCollapsed((c) => {
      try {
        localStorage.setItem(COLLAPSED_KEY, c ? "0" : "1")
      } catch {
        // per-viewer convenience only
      }
      return !c
    })
  }
  const latest = state.events[0]
  const down = Boolean(state.error)

  return (
    <section className="rounded-lg border bg-card/60" aria-label="Vault activity">
      <div className="flex items-center gap-3 px-3 py-2">
        <button
          type="button"
          onClick={toggle}
          aria-expanded={!collapsed}
          className="flex min-w-0 flex-1 items-center gap-2.5 text-left text-xs"
        >
          <span className="relative flex size-2 shrink-0">
            {!down && <span className="absolute inset-0 animate-ping rounded-full bg-success/60" />}
            <span className={cn("relative size-2 rounded-full", down ? "bg-destructive" : "bg-success")} />
          </span>
          <span className="font-medium">{down ? "Vault feed unreachable" : "Watching your vault"}</span>
          {!down && (state.pending.length > 0 || latest) && (
            <span className="truncate text-muted-foreground">
              {state.pending.length > 0
                ? `ingesting ${state.pending.map(basename).join(", ")}…`
                : `latest: ${basename(latest!.path)}, ${timeAgo(latest!.ts)}`}
            </span>
          )}
          <ChevronDown className={cn("ml-auto size-3.5 shrink-0 text-muted-foreground transition-transform", collapsed && "-rotate-90")} />
        </button>
        <Button size="sm" variant="ghost" className="h-7 gap-1 px-2 text-xs" onClick={onPick}>
          <Plus className="size-3.5" /> Add file
        </Button>
      </div>
      {!collapsed && (
        <ul className="flex gap-2 overflow-x-auto px-3 pb-2.5">
          {state.pending.map((p) => (
            <li key={p} className="flex shrink-0 items-center gap-2 rounded-md border border-dashed px-2.5 py-1.5 text-xs text-muted-foreground">
              <Loader2 className="size-3.5 animate-spin" />
              <span className="max-w-48 truncate">{basename(p)}</span>
              <span className="text-[11px]">waiting for the watcher</span>
            </li>
          ))}
          {state.events.map((e) => (
            <EventChip key={e.seq} event={e} fresh={state.fresh.has(e.seq)} />
          ))}
          {state.events.length === 0 && state.pending.length === 0 && (
            <li className="py-1 text-xs text-muted-foreground">
              Nothing ingested yet. Drop PDFs, notes or chat exports anywhere on this page.
            </li>
          )}
        </ul>
      )}
    </section>
  )
}
