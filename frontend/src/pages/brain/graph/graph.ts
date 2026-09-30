/**
 * Vault graph logic, kept free of React and canvas so vitest covers it: which nodes and links are shown,
 * which labels are drawn, the label-aware collision force that keeps always-on labels apart, and fitting.
 * Geometry is in graph ("world") units: labels scale with the zoom, so a layout without overlaps stays
 * without overlaps at any zoom level.
 */
import type { Document, Entity, Graph } from "@/api/client"

export type EntityType = Entity["type"]
export type GraphEdge = Graph["edges"][number]
export type GraphNodeIn = Graph["nodes"][number]

export const OWNER_ID = "e_owner"

/** Legend and entity-list order. */
export const TYPE_ORDER: EntityType[] = [
  "PERSON", "PROJECT", "DECISION", "CONCEPT", "OBLIGATION", "EVENT", "ORG", "PLACE", "DOCUMENT",
]

export const TYPE_STYLE: Record<EntityType, { label: string; plural: string; color: string }> = {
  PERSON: { label: "Person", plural: "People", color: "#60a5fa" },
  PROJECT: { label: "Project", plural: "Projects", color: "#fbbf24" },
  DECISION: { label: "Decision", plural: "Decisions", color: "#f472b6" },
  CONCEPT: { label: "Concept", plural: "Concepts", color: "#c084fc" },
  OBLIGATION: { label: "Obligation", plural: "Obligations", color: "#a3e635" },
  EVENT: { label: "Event", plural: "Events", color: "#fb923c" },
  ORG: { label: "Organisation", plural: "Organisations", color: "#818cf8" },
  PLACE: { label: "Place", plural: "Places", color: "#34d399" },
  DOCUMENT: { label: "Document", plural: "Documents", color: "#94a3b8" },
}
export const OWNER_COLOR = "#5eead4"

/** Labels always drawn for these types and the owner; the rest on hover, selection or zoom. */
export const LABELLED_TYPES: ReadonlySet<EntityType> = new Set<EntityType>(["PERSON", "PROJECT", "DECISION"])
export const DEFAULT_HIDDEN_TYPES: readonly EntityType[] = ["DOCUMENT"]

export const FONT = 5
export const NODE_R = 4
export const OWNER_R = 7.5
export const LABEL_GAP = 1.6
export const LABEL_MAX = 32
/** How tall "fit to screen" lets labels get: 14 px on a laptop canvas, up to 19 px on a 1080p one. */
export function fitLabelPx(canvasHeight: number): number {
  return Math.min(19, Math.max(14, canvasHeight / 46))
}
/** Other labels appear once they are this tall on screen, i.e. only after zooming in past the fit. */
export function zoomLabelPx(canvasHeight: number): number {
  return fitLabelPx(canvasHeight) * 1.3
}

export interface VNode {
  id: string
  type: EntityType
  name: string
  /** Truncated name for always-on labels. */
  label: string
  owner: boolean
  always: boolean
  r: number
  x?: number
  y?: number
  vx?: number
  vy?: number
  fx?: number
  fy?: number
}

/** A passage an edge was read from. */
export interface EdgeSource {
  chunk_id: string
  doc_id: string
}

/** Every stored edge with the same (src, rel, dst), shown as one line: each document keeps its own edge. */
export interface EdgeGroup {
  key: string
  src: string
  rel: GraphEdge["rel"]
  dst: string
  edges: GraphEdge[]
  /** Distinct source passages, in edge order. */
  sources: EdgeSource[]
  docIds: string[]
  /** Every edge of the group is closed. */
  closed: boolean
}

export interface VLink {
  id: string
  source: string | VNode
  target: string | VNode
  rel: GraphEdge["rel"]
  closed: boolean
  docIds: string[]
}

export function isAlwaysLabelled(n: { id: string; type: EntityType }): boolean {
  return n.id === OWNER_ID || LABELLED_TYPES.has(n.type)
}

export function shortLabel(name: string, max = LABEL_MAX): string {
  return name.length <= max ? name : `${name.slice(0, max - 1).trimEnd()}…`
}

export function displayName(n: { id: string; name: string }): string {
  return n.id === OWNER_ID ? `${n.name} (you)` : n.name
}

/** `LANDLORD_OF` -> "landlord of". */
export function relLabel(rel: string): string {
  return rel.toLowerCase().replace(/_/g, " ")
}

export function isCurrent(e: Pick<GraphEdge, "valid_to">): boolean {
  return e.valid_to == null
}

/** Group edges by (src, rel, dst); closed edges are left out unless `showClosed`. */
export function groupEdges(edges: GraphEdge[], showClosed = false): EdgeGroup[] {
  const groups = new Map<string, EdgeGroup>()
  for (const e of edges) {
    if (!showClosed && !isCurrent(e)) continue
    const key = `${e.src}|${e.rel}|${e.dst}`
    let g = groups.get(key)
    if (!g) {
      g = { key, src: e.src, rel: e.rel, dst: e.dst, edges: [], sources: [], docIds: [], closed: true }
      groups.set(key, g)
    }
    g.edges.push(e)
    g.closed &&= !isCurrent(e)
    if (e.source_chunk_id && e.doc_id && !g.sources.some((s) => s.chunk_id === e.source_chunk_id)) {
      g.sources.push({ chunk_id: e.source_chunk_id, doc_id: e.doc_id })
    }
    if (e.doc_id && !g.docIds.includes(e.doc_id)) g.docIds.push(e.doc_id)
  }
  return [...groups.values()]
}

function jitter(id: string): [number, number] {
  let h = 2166136261
  for (let i = 0; i < id.length; i++) h = Math.imul(h ^ id.charCodeAt(i), 16777619)
  const a = ((h >>> 0) % 360) * (Math.PI / 180)
  return [Math.cos(a) * 12, Math.sin(a) * 12]
}

export interface ViewOptions {
  hiddenTypes: ReadonlySet<EntityType>
  showClosed: boolean
  /** Also show entities with no edge to another shown entity (default true). */
  showUnlinked?: boolean
}

/**
 * Entities of shown types with no shown edge to another shown entity (hidden types and, unless
 * `showClosed`, closed edges do not count). The owner is never listed: it is always on the canvas.
 */
export function unlinkedIds(graph: Graph, opts: Omit<ViewOptions, "showUnlinked">): Set<string> {
  const shown = new Set(graph.nodes.filter((n) => !opts.hiddenTypes.has(n.type)).map((n) => n.id))
  const linked = new Set<string>([OWNER_ID])
  for (const g of groupEdges(graph.edges, opts.showClosed)) {
    if (g.src !== g.dst && shown.has(g.src) && shown.has(g.dst)) {
      linked.add(g.src)
      linked.add(g.dst)
    }
  }
  return new Set([...shown].filter((id) => !linked.has(id)))
}

/**
 * Nodes and links for the canvas. Hidden types (and unlinked entities unless `showUnlinked`) are left
 * out of the simulation, so they take no space. Nodes seen before keep their position and pin (`prev`,
 * keyed by id), so a new file or a toggle only moves the new nodes; a new node starts next to an
 * already placed neighbour.
 */
export function buildView(graph: Graph, opts: ViewOptions, prev: ReadonlyMap<string, VNode> = new Map()): { nodes: VNode[]; links: VLink[] } {
  const nodes: VNode[] = []
  const byId = new Map<string, VNode>()
  const unlinked = opts.showUnlinked === false ? unlinkedIds(graph, opts) : new Set<string>()
  for (const n of graph.nodes) {
    if (opts.hiddenTypes.has(n.type) || unlinked.has(n.id)) continue
    const owner = n.id === OWNER_ID
    const label = shortLabel(displayName(n))
    const node: VNode = { id: n.id, type: n.type, name: n.name, label, owner, always: isAlwaysLabelled(n), r: owner ? OWNER_R : NODE_R }
    const old = prev.get(n.id)
    if (old) Object.assign(node, { x: old.x, y: old.y, fx: old.fx, fy: old.fy })
    nodes.push(node)
    byId.set(n.id, node)
  }
  const links: VLink[] = []
  for (const g of groupEdges(graph.edges, opts.showClosed)) {
    if (!byId.has(g.src) || !byId.has(g.dst)) continue
    links.push({ id: g.key, source: g.src, target: g.dst, rel: g.rel, closed: g.closed, docIds: g.docIds })
  }
  for (const n of nodes) {
    if (n.x !== undefined) continue
    for (const l of links) {
      const other = l.source === n.id ? byId.get(l.target as string) : l.target === n.id ? byId.get(l.source as string) : undefined
      if (other?.x !== undefined && other.y !== undefined) {
        const [dx, dy] = jitter(n.id)
        n.x = other.x + dx
        n.y = other.y + dy
        break
      }
    }
  }
  return { nodes, links }
}

/** Entities an edge from this document touches (current edges unless `showClosed`). */
export function docEntityIds(graph: Graph, docId: string, showClosed = false): Set<string> {
  const out = new Set<string>()
  for (const e of graph.edges) {
    if (e.doc_id !== docId || (!showClosed && !isCurrent(e))) continue
    out.add(e.src)
    out.add(e.dst)
  }
  return out
}

export interface Neighbour {
  group: EdgeGroup
  other: GraphNodeIn
  outgoing: boolean
}

/** One entry per (rel, other end, direction), with every source of the collapsed edges. */
export function neighbours(graph: Graph, id: string, showClosed = false): Neighbour[] {
  const nodes = new Map(graph.nodes.map((n) => [n.id, n]))
  const out: Neighbour[] = []
  for (const g of groupEdges(graph.edges, showClosed)) {
    const outgoing = g.src === id
    if (!outgoing && g.dst !== id) continue
    const other = nodes.get(outgoing ? g.dst : g.src)
    if (other) out.push({ group: g, other, outgoing })
  }
  return out.sort((a, b) => a.group.rel.localeCompare(b.group.rel) || a.other.name.localeCompare(b.other.name))
}

/** Distinct current connections per entity (duplicate edges from several documents count once). */
export function degrees(graph: Graph): Map<string, number> {
  const out = new Map<string, number>()
  for (const g of groupEdges(graph.edges)) {
    out.set(g.src, (out.get(g.src) ?? 0) + 1)
    out.set(g.dst, (out.get(g.dst) ?? 0) + 1)
  }
  return out
}

/** What the graph says when a document filter shows nothing. */
export function docFilterNotice(doc: Pick<Document, "signature_status"> | undefined, shown: number): { tone: "error" | "muted"; text: string } | null {
  if (doc?.signature_status === "invalid") return { tone: "error", text: "Nothing extracted: signature check failed" }
  if (shown === 0) return { tone: "muted", text: "No entities extracted from this document" }
  return null
}

/** `mock_bank` -> "Mock Bank". */
export function issuerName(iss: string): string {
  return iss.split(/[_\s-]+/).filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1)).join(" ")
}

/** Amber label for an issuer-signed document that is not in the owner's name (CONTRACT §6.6), else null. */
export function holderWarning(status?: string | null, holder?: string | null): string | null {
  if (status !== "issuer_signed") return null
  if (holder === "mismatch") return "Signed, but not in your name"
  if (holder === "unknown") return "Signed, holder not confirmed"
  return null
}

export function signatureView(doc: Pick<Document, "signature_status" | "iss" | "holder_status">): { tone: "success" | "warning" | "error" | "muted"; label: string } {
  const warning = holderWarning(doc.signature_status, doc.holder_status)
  if (warning) return { tone: "warning", label: warning }
  if (doc.signature_status === "issuer_signed") return { tone: "success", label: doc.iss ? `Signed by ${issuerName(doc.iss)}` : "Issuer-signed" }
  if (doc.signature_status === "invalid") return { tone: "error", label: "Signature check failed" }
  return { tone: "muted", label: "Unsigned" }
}

// --- geometry ------------------------------------------------------------------------------------------

/** Text width in world units at `FONT`. */
export type Measure = (text: string) => number
export const approxMeasure: Measure = (t) => t.length * FONT * 0.56

export interface Box {
  left: number
  right: number
  top: number
  bottom: number
}

/** The label text's rectangle, centred under the node. */
export function labelRect(n: Pick<VNode, "r">, x: number, y: number, width: number): Box {
  const top = y + n.r + LABEL_GAP
  return { left: x - width / 2, right: x + width / 2, top, bottom: top + FONT * 1.2 }
}

/** Node circle plus, when labelled, its label: what must not overlap another node's box. */
export function nodeBox(n: Pick<VNode, "r" | "label">, x: number, y: number, labelled: boolean, measure: Measure): Box {
  const circle = { left: x - n.r, right: x + n.r, top: y - n.r, bottom: y + n.r }
  if (!labelled) return circle
  const l = labelRect(n, x, y, measure(n.label))
  return { left: Math.min(circle.left, l.left), right: Math.max(circle.right, l.right), top: circle.top, bottom: l.bottom }
}

export function boxesOverlap(a: Box, b: Box, pad = 0): boolean {
  return a.left < b.right + pad && b.left < a.right + pad && a.top < b.bottom + pad && b.top < a.bottom + pad
}

export function countOverlaps(boxes: Box[]): number {
  let n = 0
  for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) if (boxesOverlap(boxes[i], boxes[j])) n++
  return n
}

export interface LabelCandidate {
  id: string
  rect: Box
  /** Always drawn (always-on type, highlighted, selected, hovered); others only where they fit. */
  force: boolean
}

/**
 * Forced labels first, in the order given; optional labels only where they overlap no drawn label and
 * no other node (`obstacles`: node circles by id). `overlaps` counts overlapping pairs among the drawn
 * labels (0 when the layout kept them apart).
 */
export function pickLabels(cands: LabelCandidate[], obstacles: { id: string; box: Box }[] = []): { drawn: string[]; overlaps: number } {
  const drawn: LabelCandidate[] = []
  for (const c of cands.filter((c) => c.force)) drawn.push(c)
  for (const c of cands.filter((c) => !c.force)) {
    if (drawn.some((d) => boxesOverlap(d.rect, c.rect, 0.6))) continue
    if (obstacles.some((o) => o.id !== c.id && boxesOverlap(o.box, c.rect, 0.6))) continue
    drawn.push(c)
  }
  return { drawn: drawn.map((d) => d.id), overlaps: countOverlaps(drawn.map((d) => d.rect)) }
}

type SimNode = VNode & { index?: number }

/**
 * d3 force: pushes apart nodes whose boxes (circle, plus the label for `labelled` nodes) overlap, along
 * the axis of least overlap. O(n²), fine for a personal vault's few hundred entities.
 */
export function collideBoxes(measure: Measure, labelled: (n: VNode) => boolean = (n) => n.always, pad = 2, strength = 0.7) {
  let nodes: SimNode[] = []
  const force = (_alpha: number) => {
    const boxes = nodes.map((n) => nodeBox(n, n.x ?? 0, n.y ?? 0, labelled(n), measure))
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = boxes[i], b = boxes[j]
        const ox = Math.min(a.right, b.right) - Math.max(a.left, b.left) + pad
        const oy = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) + pad
        if (ox <= 0 || oy <= 0) continue
        const na = nodes[i], nb = nodes[j]
        if (ox < oy) {
          const s = Math.sign((nb.x ?? 0) - (na.x ?? 0)) || (i % 2 ? 1 : -1)
          const d = (ox / 2) * strength
          na.vx = (na.vx ?? 0) - s * d
          nb.vx = (nb.vx ?? 0) + s * d
        } else {
          const s = Math.sign((nb.y ?? 0) - (na.y ?? 0)) || (j % 2 ? 1 : -1)
          const d = (oy / 2) * strength
          na.vy = (na.vy ?? 0) - s * d
          nb.vy = (nb.vy ?? 0) + s * d
        }
      }
    }
  }
  force.initialize = (ns: SimNode[]) => {
    nodes = ns
  }
  return force
}

/** d3 force: a gentle pull to the origin so unlinked entities stay near the rest. */
export function gravity(strength = 0.05) {
  let nodes: SimNode[] = []
  const force = (alpha: number) => {
    for (const n of nodes) {
      n.vx = (n.vx ?? 0) - (n.x ?? 0) * strength * alpha
      n.vy = (n.vy ?? 0) - (n.y ?? 0) * strength * alpha
    }
  }
  force.initialize = (ns: SimNode[]) => {
    nodes = ns
  }
  return force
}

export type Pad = { top: number; right: number; bottom: number; left: number }

/**
 * Centre (graph units) and zoom that fit `boxes` into a width × height canvas, keeping `pad` screen
 * pixels free on each side (the legend sits on top). Uneven padding shifts the centre accordingly.
 */
export function fitTransform(boxes: Box[], width: number, height: number, pad: number | Pad = 48, maxZoom = 8): { x: number; y: number; k: number } | null {
  if (!boxes.length || width <= 0 || height <= 0) return null
  const p = typeof pad === "number" ? { top: pad, right: pad, bottom: pad, left: pad } : pad
  const left = Math.min(...boxes.map((b) => b.left))
  const right = Math.max(...boxes.map((b) => b.right))
  const top = Math.min(...boxes.map((b) => b.top))
  const bottom = Math.max(...boxes.map((b) => b.bottom))
  const kx = (width - p.left - p.right) / Math.max(right - left, 1)
  const ky = (height - p.top - p.bottom) / Math.max(bottom - top, 1)
  const k = Math.max(0.1, Math.min(kx, ky, maxZoom))
  // The canvas centre shows graph point (x, y); shift it so the boxes sit centred in the padded area.
  const x = (left + right) / 2 - (p.left - p.right) / 2 / k
  const y = (top + bottom) / 2 - (p.top - p.bottom) / 2 / k
  return { x, y, k }
}
