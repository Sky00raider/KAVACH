import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react"
import {
  ArrowLeft,
  ArrowRight,
  Eye,
  EyeOff,
  FileSearch,
  Loader2,
  Maximize,
  Search,
  ShieldAlert,
  ShieldCheck,
  Shuffle,
  Sparkles,
  Waypoints,
  X,
} from "lucide-react"
import { api, type Document, type Entity, type Graph } from "@/api/client"
import { usePoll } from "@/api/poll"
import { Page } from "@/components/shared/Page"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "@/components/lib/utils"
import { basename, timeAgo } from "./ask/answer"
import { useIngest } from "./ask/IngestStrip"
import { SourceIcon, SourcePopover } from "./ask/Sources"
import { clearLastAnswer, loadLastAnswer, type LastAnswer } from "./lastAnswer"
import {
  DEFAULT_HIDDEN_TYPES,
  OWNER_COLOR,
  OWNER_ID,
  TYPE_ORDER,
  TYPE_STYLE,
  degrees,
  displayName,
  docEntityIds,
  docFilterNotice,
  neighbours,
  relLabel,
  signatureView,
  unlinkedIds,
  type EntityType,
} from "./graph/graph"
import type { VaultGraphHandle } from "./graph/VaultGraph"

// The canvas graph library is the largest dependency; only the Vault page loads it.
const VaultGraph = lazy(() => import("./graph/VaultGraph").then((m) => ({ default: m.VaultGraph })))

/** Same object while the payload is unchanged, so a refetch does not restart the layout. */
function useStable<T>(value: T | undefined): T | undefined {
  const ref = useRef<{ key: string; value: T }>(undefined)
  if (value === undefined) return ref.current?.value
  const key = JSON.stringify(value)
  if (ref.current?.key !== key) ref.current = { key, value }
  return ref.current.value
}

function Dot({ type, owner, className }: { type: EntityType; owner?: boolean; className?: string }) {
  return (
    <span
      className={cn("inline-block size-2.5 shrink-0 rounded-full", className)}
      style={{ backgroundColor: owner ? OWNER_COLOR : TYPE_STYLE[type].color }}
    />
  )
}

function SignaturePill({ doc }: { doc: Document }) {
  const v = signatureView(doc)
  if (v.tone === "muted") return <span className="text-[11px] text-muted-foreground">{v.label}</span>
  const Icon = v.tone === "success" ? ShieldCheck : ShieldAlert
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[11px] font-medium",
        v.tone === "success" ? "bg-success/12 text-success" : "bg-destructive/12 text-destructive",
      )}
    >
      <Icon className="size-3" />
      {v.label}
    </span>
  )
}

function DocumentList({ docs, counts, active, onPick }: {
  docs: Document[]
  counts: Map<string, number>
  active: string | null
  onPick: (docId: string) => void
}) {
  if (!docs.length) return <p className="px-3 py-6 text-center text-sm text-muted-foreground">No documents yet.</p>
  return (
    <ul className="space-y-1 p-1.5">
      {docs.map((d) => {
        const n = counts.get(d.doc_id) ?? 0
        const on = active === d.doc_id
        return (
          <li key={d.doc_id}>
            <button
              type="button"
              onClick={() => onPick(d.doc_id)}
              aria-pressed={on}
              title={on ? "Show the whole graph" : "Show only the entities from this document"}
              className={cn(
                "w-full rounded-md border border-transparent px-2.5 py-2 text-left transition-colors hover:bg-accent",
                on && "border-primary/50 bg-primary/8",
              )}
            >
              <span className="flex items-center gap-2 text-sm font-medium">
                <SourceIcon source={d.source} className="text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate" title={d.path}>{basename(d.path)}</span>
                <span className="shrink-0 font-mono text-[11px] text-muted-foreground tabular-nums" title="Entities from this document">
                  {d.signature_status === "invalid" ? "–" : n}
                </span>
              </span>
              <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 pl-5.5 text-[11px] text-muted-foreground">
                <SignaturePill doc={d} />
                {d.doc_type && <span>{d.doc_type.replace(/_/g, " ")}</span>}
                <span>{timeAgo(d.ingested_at)}</span>
              </span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}

function EntityList({ graph, degree, selected, onPick }: {
  graph: Graph
  degree: Map<string, number>
  selected: string | null
  onPick: (id: string, type: EntityType) => void
}) {
  const [q, setQ] = useState("")
  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return TYPE_ORDER.map((type) => ({
      type,
      items: graph.nodes
        .filter((n) => n.type === type && (!needle || n.name.toLowerCase().includes(needle)))
        .sort((a, b) => Number(b.id === OWNER_ID) - Number(a.id === OWNER_ID) || a.name.localeCompare(b.name)),
    })).filter((g) => g.items.length)
  }, [graph, q])

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="relative p-1.5">
        <Search className="pointer-events-none absolute top-1/2 left-4 size-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find an entity" aria-label="Find an entity" className="h-8 pl-8 text-sm" />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-2">
        {groups.map((g) => (
          <section key={g.type} className="mt-2">
            <h3 className="flex items-center gap-1.5 px-1.5 pb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
              <Dot type={g.type} className="size-2" />
              {TYPE_STYLE[g.type].plural}
              <span className="font-mono normal-case">{g.items.length}</span>
            </h3>
            <ul>
              {g.items.map((n) => (
                <li key={n.id}>
                  <button
                    type="button"
                    onClick={() => onPick(n.id, n.type)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-sm hover:bg-accent",
                      selected === n.id && "bg-primary/10 text-foreground",
                    )}
                  >
                    <span className="min-w-0 flex-1 truncate" title={n.name}>{displayName(n)}</span>
                    <span className="shrink-0 font-mono text-[11px] text-muted-foreground tabular-nums">{degree.get(n.id) ?? 0}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
        {!groups.length && <p className="px-2 py-6 text-center text-sm text-muted-foreground">No entity matches.</p>}
      </div>
    </div>
  )
}

function EntityPanel({ graph, entity, id, docs, showClosed, onSelect, onClose }: {
  graph: Graph
  entity?: Entity
  id: string
  docs: Map<string, Document>
  showClosed: boolean
  onSelect: (id: string) => void
  onClose: () => void
}) {
  const node = graph.nodes.find((n) => n.id === id)
  const links = useMemo(() => neighbours(graph, id, showClosed), [graph, id, showClosed])
  if (!node) return null
  const attrs = Object.entries(entity?.attrs ?? {})
  return (
    <div className="absolute top-14 right-3 bottom-3 z-10 flex w-80 flex-col overflow-hidden rounded-lg border bg-popover/95 shadow-xl backdrop-blur animate-in fade-in-0 slide-in-from-right-2">
      <div className="flex items-start gap-2 border-b p-3">
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
            <Dot type={node.type} owner={node.id === OWNER_ID} className="size-2" />
            {node.id === OWNER_ID ? "You" : TYPE_STYLE[node.type].label}
          </p>
          <p className="mt-1 text-sm leading-snug font-semibold break-words">{node.name}</p>
        </div>
        <Button size="icon" variant="ghost" className="size-7" aria-label="Close" onClick={onClose}>
          <X className="size-4" />
        </Button>
      </div>
      {attrs.length > 0 && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 border-b px-3 py-2 text-xs">
          {attrs.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted-foreground">{k}</dt>
              <dd className="font-mono break-all">{v}</dd>
            </div>
          ))}
        </dl>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        <p className="px-1 pb-1 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
          Connections <span className="font-mono normal-case">{links.length}</span>
        </p>
        {!links.length && <p className="px-1 text-xs text-muted-foreground">No connections yet.</p>}
        <ul className="space-y-0.5">
          {links.map(({ group, other, outgoing }) => {
            const Arrow = outgoing ? ArrowRight : ArrowLeft
            const closedOn = group.closed ? group.edges.map((e) => e.valid_to ?? "").sort().at(-1) : undefined
            return (
              <li key={group.key} className={cn("rounded px-1 py-1 text-xs hover:bg-accent/60", group.closed && "opacity-60")}>
                <div className="flex items-center gap-1.5">
                  <Arrow className="size-3 shrink-0 text-muted-foreground" />
                  <span className="shrink-0 text-muted-foreground">{relLabel(group.rel)}</span>
                  <button type="button" onClick={() => onSelect(other.id)} className="flex min-w-0 flex-1 items-center gap-1.5 text-left hover:underline">
                    <Dot type={other.type} owner={other.id === OWNER_ID} className="size-2" />
                    <span className="truncate" title={other.name}>{displayName(other)}</span>
                  </button>
                  {closedOn && <span className="shrink-0 text-[10px] text-muted-foreground">closed {closedOn}</span>}
                </div>
                {group.sources.length > 0 && (
                  <div className="mt-1 flex flex-wrap gap-1 pl-4.5">
                    {group.sources.map((src) => {
                      const doc = docs.get(src.doc_id)
                      return (
                        <SourcePopover key={src.chunk_id} source={{ n: 0, chunk_id: src.chunk_id, doc_id: src.doc_id, locator: "" }} doc={doc}>
                          <button
                            type="button"
                            className="inline-flex max-w-40 items-center gap-1 rounded border bg-card px-1.5 py-0.5 text-[11px] text-muted-foreground hover:border-primary/50 hover:text-foreground"
                            title={doc ? `Source: ${doc.path}` : "Source passage"}
                          >
                            <SourceIcon source={doc?.source} className="size-3" />
                            <span className="truncate">{doc ? basename(doc.path) : "source"}</span>
                          </button>
                        </SourcePopover>
                      )
                    })}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      </div>
    </div>
  )
}

export default function Vault() {
  const ingest = useIngest()
  const documents = usePoll(api.documents, null, [ingest.lastSeq])
  const entities = usePoll(() => api.entities(), null, [ingest.lastSeq])
  const graphPoll = usePoll(() => api.graph(), null, [ingest.lastSeq])
  const graph = useStable(graphPoll.data)

  const [hidden, setHidden] = useState<ReadonlySet<EntityType>>(() => new Set(DEFAULT_HIDDEN_TYPES))
  const [showClosed, setShowClosed] = useState(false)
  const [showUnlinked, setShowUnlinked] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  const [docId, setDocId] = useState<string | null>(null)
  const [last, setLast] = useState<LastAnswer | null>(loadLastAnswer)
  const [tab, setTab] = useState("documents")
  const graphRef = useRef<VaultGraphHandle>(null)

  const docs = useMemo(() => new Map((documents.data ?? []).map((d) => [d.doc_id, d])), [documents.data])
  const entityById = useMemo(() => new Map((entities.data ?? []).map((e) => [e.entity_id, e])), [entities.data])
  const nodeType = useMemo(() => new Map((graph?.nodes ?? []).map((n) => [n.id, n.type])), [graph])
  const degree = useMemo(() => (graph ? degrees(graph) : new Map<string, number>()), [graph])

  const typeCounts = useMemo(() => {
    const c = new Map<EntityType, number>()
    for (const n of graph?.nodes ?? []) c.set(n.type, (c.get(n.type) ?? 0) + 1)
    return c
  }, [graph])
  const closedCount = useMemo(() => (graph?.edges ?? []).filter((e) => e.valid_to != null).length, [graph])
  const unlinked = useMemo(
    () => (graph ? unlinkedIds(graph, { hiddenTypes: hidden, showClosed }) : new Set<string>()),
    [graph, hidden, showClosed],
  )
  /** On the canvas: a shown type, and linked unless unlinked entities are shown. */
  const onCanvas = (id: string) => !hidden.has(nodeType.get(id) ?? "DOCUMENT") && (showUnlinked || !unlinked.has(id))

  /** Per document: the entities its edges touch that the graph currently shows. */
  const docCounts = useMemo(() => {
    const out = new Map<string, number>()
    if (!graph) return out
    for (const d of documents.data ?? []) {
      const ids = docEntityIds(graph, d.doc_id, showClosed)
      out.set(d.doc_id, [...ids].filter(onCanvas).length)
    }
    return out
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, documents.data, showClosed, hidden, nodeType, unlinked, showUnlinked])

  const docFilter = useMemo(
    () => (graph && docId ? { docId, ids: docEntityIds(graph, docId, showClosed) } : null),
    [graph, docId, showClosed],
  )
  const filterDoc = docId ? docs.get(docId) : undefined
  const notice = docId ? docFilterNotice(filterDoc, docCounts.get(docId) ?? 0) : null

  const highlight = useMemo(() => {
    const ids = (last?.entities_used ?? []).filter((id) => nodeType.has(id) && onCanvas(id))
    return new Set(ids)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [last, nodeType, hidden, unlinked, showUnlinked])

  // Pick up an answer given in another tab or before this page mounted.
  useEffect(() => {
    const onFocus = () => setLast(loadLastAnswer())
    window.addEventListener("focus", onFocus)
    return () => window.removeEventListener("focus", onFocus)
  }, [])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelected(null)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  // A selection the filter or a hidden type no longer shows is dropped.
  useEffect(() => {
    if (!selected) return
    const t = nodeType.get(selected)
    if (!t || !onCanvas(selected) || (docFilter && !docFilter.ids.has(selected))) setSelected(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, nodeType, hidden, docFilter, unlinked, showUnlinked])

  function toggleType(t: EntityType) {
    setHidden((h) => {
      const next = new Set(h)
      if (next.has(t)) next.delete(t)
      else next.add(t)
      return next
    })
  }

  function pickEntity(id: string, type: EntityType) {
    if (hidden.has(type)) toggleType(type)
    if (unlinked.has(id) && !showUnlinked) setShowUnlinked(true)
    if (docFilter && !docFilter.ids.has(id)) setDocId(null)
    setSelected(id)
    requestAnimationFrame(() => graphRef.current?.focus(id))
  }

  const loading = graphPoll.loading && !graph
  const error = graphPoll.error && !graph ? graphPoll.error : undefined
  const legendTypes = TYPE_ORDER.filter((t) => typeCounts.get(t))

  return (
    <Page
      title="Vault"
      description="Your documents, their signatures, and the people, projects and decisions KAVACH found in them."
      className="h-full max-w-none min-h-[560px]"
    >
      {last && highlight.size > 0 && (
        <div className="-mt-2 flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/8 px-3 py-2 text-sm">
          <Sparkles className="size-4 shrink-0 text-primary" />
          <span className="min-w-0 flex-1 truncate">
            <span className="text-muted-foreground">From your last question: </span>
            <span className="font-medium">“{last.question}”</span>
            <span className="text-muted-foreground"> · {highlight.size} {highlight.size === 1 ? "entity" : "entities"} highlighted</span>
          </span>
          <Button
            size="sm"
            variant="ghost"
            className="h-7 gap-1"
            onClick={() => {
              clearLastAnswer()
              setLast(null)
            }}
          >
            <X className="size-3.5" /> Clear
          </Button>
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-[19rem_minmax(0,1fr)] gap-4">
        <Tabs value={tab} onValueChange={setTab} className="flex min-h-0 flex-col gap-0 overflow-hidden rounded-xl border bg-card">
          <TabsList className="m-1.5 grid w-auto grid-cols-2">
            <TabsTrigger value="documents">Documents <span className="ml-1 font-mono text-[11px] text-muted-foreground">{documents.data?.length ?? ""}</span></TabsTrigger>
            <TabsTrigger value="entities">Entities <span className="ml-1 font-mono text-[11px] text-muted-foreground">{graph?.nodes.length ?? ""}</span></TabsTrigger>
          </TabsList>
          <TabsContent value="documents" className="min-h-0 flex-1 overflow-y-auto">
            {documents.error && !documents.data ? (
              <p className="p-3 text-sm text-destructive">Could not load documents: {documents.error.message}</p>
            ) : (
              <DocumentList docs={documents.data ?? []} counts={docCounts} active={docId} onPick={(id) => setDocId((cur) => (cur === id ? null : id))} />
            )}
          </TabsContent>
          <TabsContent value="entities" className="flex min-h-0 flex-1 flex-col">
            {graph && <EntityList graph={graph} degree={degree} selected={selected} onPick={pickEntity} />}
          </TabsContent>
        </Tabs>

        <section className="relative min-h-0 overflow-hidden rounded-xl border bg-card" aria-label="Knowledge graph">
          {graph && graph.nodes.length > 0 && (
            <Suspense fallback={null}>
              <VaultGraph
                ref={graphRef}
                graph={graph}
                hiddenTypes={hidden}
                showClosed={showClosed}
                showUnlinked={showUnlinked}
                highlight={highlight}
                docFilter={docFilter}
                selected={selected}
                onSelect={setSelected}
              />
            </Suspense>
          )}

          <div className="pointer-events-none absolute inset-x-3 top-3 z-10 flex items-start justify-between gap-3">
            <div className="pointer-events-auto flex flex-wrap gap-1.5">
              {legendTypes.map((t) => {
                const off = hidden.has(t)
                return (
                  <button
                    key={t}
                    type="button"
                    onClick={() => toggleType(t)}
                    aria-pressed={!off}
                    title={off ? `Show ${TYPE_STYLE[t].plural.toLowerCase()}` : `Hide ${TYPE_STYLE[t].plural.toLowerCase()}`}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-full border bg-card/90 px-2 py-0.5 text-xs backdrop-blur transition-colors hover:border-primary/50",
                      off && "text-muted-foreground opacity-70",
                    )}
                  >
                    {off ? <EyeOff className="size-3" /> : <Dot type={t} className="size-2" />}
                    <span className={cn(off && "line-through decoration-muted-foreground/60")}>{TYPE_STYLE[t].plural}</span>
                    <span className="font-mono text-[11px] text-muted-foreground">{typeCounts.get(t)}</span>
                  </button>
                )
              })}
              {(unlinked.size > 0 || showUnlinked) && (
                <button
                  type="button"
                  onClick={() => setShowUnlinked((s) => !s)}
                  aria-pressed={showUnlinked}
                  title={showUnlinked ? "Hide entities with no link in the graph" : "Show entities with no link in the graph"}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-full border border-dashed bg-card/90 px-2 py-0.5 text-xs backdrop-blur transition-colors hover:border-primary/50",
                    !showUnlinked && "text-muted-foreground",
                  )}
                >
                  {showUnlinked ? <Eye className="size-3" /> : <EyeOff className="size-3" />}
                  <span className="font-mono text-[11px]">{unlinked.size}</span> unlinked
                </button>
              )}
            </div>
            <div className="pointer-events-auto flex shrink-0 gap-1.5">
              {closedCount > 0 && (
                <Button size="sm" variant="outline" className="h-7 gap-1 bg-card/90 text-xs" aria-pressed={showClosed} onClick={() => setShowClosed((s) => !s)}>
                  {showClosed ? <Eye className="size-3.5" /> : <EyeOff className="size-3.5" />} Closed links {closedCount}
                </Button>
              )}
              <Button size="sm" variant="outline" className="h-7 gap-1 bg-card/90 text-xs" title="Lay the graph out again (unpins every node)" onClick={() => graphRef.current?.rearrange()}>
                <Shuffle className="size-3.5" /> Re-arrange
              </Button>
              <Button size="sm" variant="outline" className="h-7 gap-1 bg-card/90 text-xs" onClick={() => graphRef.current?.fit()}>
                <Maximize className="size-3.5" /> Fit to screen
              </Button>
            </div>
          </div>

          {docId && (
            <div className="absolute bottom-3 left-3 z-10 flex max-w-[calc(100%-1.5rem)] items-center gap-2 rounded-full border bg-card/95 py-1 pr-1 pl-3 text-xs shadow-sm backdrop-blur">
              <FileSearch className="size-3.5 shrink-0 text-primary" />
              <span className="truncate">
                {notice ? "Filtered to" : `${docCounts.get(docId) ?? 0} entities from`} <span className="font-medium">{filterDoc ? basename(filterDoc.path) : docId}</span>
              </span>
              <Button size="icon" variant="ghost" className="size-6 rounded-full" aria-label="Show the whole graph" onClick={() => setDocId(null)}>
                <X className="size-3.5" />
              </Button>
            </div>
          )}

          {!docId && graph && graph.nodes.length > 0 && (
            <p className="pointer-events-none absolute right-3 bottom-3 text-[11px] text-muted-foreground/70">
              Click to inspect · drag to pin · scroll to zoom
            </p>
          )}

          {(notice || loading || error || (graph && graph.nodes.length === 0)) && (
            <div className="pointer-events-none absolute inset-0 grid place-items-center p-6 text-center">
              {notice ? (
                <p className={cn("flex items-center gap-2 text-sm font-medium", notice.tone === "error" ? "text-destructive" : "text-muted-foreground")}>
                  {notice.tone === "error" && <ShieldAlert className="size-4" />}
                  {notice.text}
                </p>
              ) : loading ? (
                <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" /> Loading the graph…</p>
              ) : error ? (
                <p className="text-sm text-destructive">Could not load the graph: {error.message}</p>
              ) : (
                <div className="max-w-sm">
                  <Waypoints className="mx-auto mb-3 size-6 text-muted-foreground" />
                  <p className="text-sm font-medium">No entities yet</p>
                  <p className="mt-1 text-sm text-muted-foreground">Add notes, chats or documents and KAVACH maps the people, projects and decisions in them.</p>
                </div>
              )}
            </div>
          )}

          {selected && graph && (
            <EntityPanel
              graph={graph}
              id={selected}
              entity={entityById.get(selected)}
              docs={docs}
              showClosed={showClosed}
              onSelect={(id) => pickEntity(id, nodeType.get(id) ?? "CONCEPT")}
              onClose={() => setSelected(null)}
            />
          )}
        </section>
      </div>
    </Page>
  )
}
