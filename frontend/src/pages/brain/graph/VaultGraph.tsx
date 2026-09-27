import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react"
import ForceGraph2D, { type ForceGraphMethods, type LinkObject, type NodeObject } from "react-force-graph-2d"
import type { Graph } from "@/api/client"
import {
  FONT,
  OWNER_COLOR,
  TYPE_STYLE,
  boxesOverlap,
  buildView,
  collideBoxes,
  displayName,
  fitLabelPx,
  fitTransform,
  gravity,
  labelRect,
  nodeBox,
  pickLabels,
  relLabel,
  zoomLabelPx,
  type EntityType,
  type LabelCandidate,
  type Measure,
  type VLink,
  type VNode,
} from "./graph"

type N = NodeObject<VNode>
type L = LinkObject<VNode, VLink>

export interface VaultGraphHandle {
  /** Fit the visible nodes and their labels to the canvas. */
  fit: (ms?: number) => void
  /** Pan to one node. */
  focus: (id: string) => void
  /** Unpin every node and lay the graph out again. */
  rearrange: () => void
}

const FONT_CSS = `500 ${FONT}px "Inter Variable", ui-sans-serif, system-ui, sans-serif`
const COOLDOWN_TICKS = 220
const WARMUP_TICKS = 40
const PRIMARY = "#5eead4"
const TEXT = "rgba(226, 232, 240, 0.95)"
const HALO = "rgba(22, 25, 33, 0.9)"
/** Screen pixels kept free when fitting: the legend and buttons sit on top, the hint at the bottom. */
const FIT_PAD = { top: 84, right: 32, bottom: 40, left: 32 }
/** Width of the entity panel over the canvas's right side, plus its margin. */
const PANEL_PX = 332

let measureCtx: CanvasRenderingContext2D | null | undefined
const measure: Measure = (text) => {
  if (measureCtx === undefined) measureCtx = document.createElement("canvas").getContext("2d")
  if (!measureCtx) return text.length * FONT * 0.56
  measureCtx.font = FONT_CSS
  return measureCtx.measureText(text).width
}

function endId(end: string | VNode | undefined): string {
  return typeof end === "object" ? end.id : (end ?? "")
}

export const VaultGraph = forwardRef<VaultGraphHandle, {
  graph: Graph
  hiddenTypes: ReadonlySet<EntityType>
  showClosed: boolean
  /** Entities of the last answer: ringed, the rest dimmed. */
  highlight: ReadonlySet<string>
  /** Document filter: only these entities (and the document's edges) are shown. */
  docFilter: { docId: string; ids: ReadonlySet<string> } | null
  selected: string | null
  onSelect: (id: string | null) => void
}>(function VaultGraph({ graph, hiddenTypes, showClosed, highlight, docFilter, selected, onSelect }, ref) {
  const fg = useRef<ForceGraphMethods<N, L> | undefined>(undefined)
  const wrap = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  const positions = useRef(new Map<string, VNode>())
  const hover = useRef<string | null>(null)
  const needFit = useRef(true)
  /** Nodes the owner dragged: they stay where they were dropped until "Re-arrange". */
  const pinned = useRef(new Set<string>())
  // The collision force reads the highlight through a ref: highlighted labels are always drawn, so they
  // need room too, but a new highlight should not restart the layout.
  const highlightRef = useRef(highlight)
  highlightRef.current = highlight

  useEffect(() => {
    const el = wrap.current
    if (!el) return
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      setSize({ width: Math.floor(width), height: Math.floor(height) })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const hiddenKey = [...hiddenTypes].sort().join(",")
  const data = useMemo(() => {
    const prev = positions.current
    const view = buildView(graph, { hiddenTypes, showClosed }, prev)
    // New nodes (a new file, a type shown again) can settle off screen: fit again once they have.
    if (view.nodes.some((n) => !prev.has(n.id))) needFit.current = true
    // Anything that changes the nodes or links re-settles from the current positions (so the rest can make
    // room), then freezes again; dragged nodes stay put.
    for (const n of view.nodes) {
      if (pinned.current.has(n.id)) continue
      n.fx = undefined
      n.fy = undefined
    }
    positions.current = new Map(view.nodes.map((n) => [n.id, n]))
    return view
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, hiddenKey, showClosed])

  // Selection emphasises the node and its neighbours; otherwise the last answer's entities (not while a
  // document filter is on: then the filter is the lens).
  const emphasis = useMemo(() => {
    if (selected) {
      const s = new Set([selected])
      for (const l of data.links) {
        if (endId(l.source) === selected) s.add(endId(l.target))
        if (endId(l.target) === selected) s.add(endId(l.source))
      }
      return s
    }
    return highlight.size && !docFilter ? new Set(highlight) : null
  }, [selected, highlight, data, docFilter])

  const visible = useCallback((n: VNode) => !docFilter || docFilter.ids.has(n.id), [docFilter])

  const fit = useCallback((ms = 500) => {
    const g = fg.current
    if (!g || !size.width) return
    const boxes = data.nodes
      .filter((n) => visible(n) && n.x !== undefined)
      .map((n) => nodeBox(n, n.x!, n.y!, n.always || Boolean(emphasis?.has(n.id)), measure))
    const t = fitTransform(boxes, size.width, size.height, FIT_PAD, fitLabelPx(size.height) / FONT)
    if (!t) return
    g.centerAt(t.x, t.y, ms)
    g.zoom(t.k, ms)
  }, [data, visible, emphasis, size])

  useImperativeHandle(ref, () => ({
    fit,
    focus: (id) => {
      const n = positions.current.get(id)
      const g = fg.current
      if (!g || n?.x === undefined || n.y === undefined) return
      // Centre it in the part of the canvas the entity panel leaves free.
      g.centerAt(n.x + PANEL_PX / 2 / g.zoom(), n.y, 600)
    },
    rearrange: () => {
      pinned.current.clear()
      for (const n of data.nodes) {
        n.fx = undefined
        n.fy = undefined
      }
      needFit.current = true
      fg.current?.d3ReheatSimulation()
    },
  }), [fit, data])

  // Forces: label-aware collision, gentle gravity instead of the centring force, shorter links.
  useEffect(() => {
    const g = fg.current
    if (!g) return
    g.d3Force("center", null)
    g.d3Force("gravity", gravity(0.12) as never)
    g.d3Force("collide", collideBoxes(measure, (n) => n.always || highlightRef.current.has(n.id)) as never)
    // Short-range repulsion only, so unlinked entities are not pushed to the far edge (which would make
    // "fit to screen" zoom out until every label is tiny).
    const charge = g.d3Force("charge") as unknown as { strength: (s: number) => void; distanceMax: (d: number) => void } | undefined
    charge?.strength(-70)
    charge?.distanceMax(90)
    const link = g.d3Force("link") as unknown as { distance: (d: number) => void } | undefined
    link?.distance(30)
    g.d3ReheatSimulation()
  }, [size.width > 0])

  // Refit when the document filter changes (the filtered set can sit anywhere on the canvas).
  useEffect(() => {
    if (!needFit.current) fit(500)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docFilter?.docId])

  // Let the layout settle, then freeze every node so nothing drifts while recording.
  const onEngineStop = useCallback(() => {
    for (const n of data.nodes) {
      if (n.x === undefined || n.y === undefined) continue
      n.fx = n.x
      n.fy = n.y
    }
    if (needFit.current) {
      needFit.current = false
      fit(600)
    }
  }, [data, fit])

  const paintNode = useCallback((node: N, ctx: CanvasRenderingContext2D) => {
    const x = node.x ?? 0, y = node.y ?? 0
    const dim = emphasis !== null && !emphasis.has(node.id)
    ctx.globalAlpha = dim ? 0.22 : 1
    if (node.owner) {
      ctx.beginPath()
      ctx.arc(x, y, node.r + 2.6, 0, 2 * Math.PI)
      ctx.fillStyle = "rgba(94, 234, 212, 0.16)"
      ctx.fill()
    }
    ctx.beginPath()
    ctx.arc(x, y, node.r, 0, 2 * Math.PI)
    ctx.fillStyle = node.owner ? OWNER_COLOR : TYPE_STYLE[node.type].color
    ctx.fill()
    if (node.owner) {
      ctx.lineWidth = 1
      ctx.strokeStyle = "rgba(255, 255, 255, 0.85)"
      ctx.stroke()
    }
    if (highlight.has(node.id) && !dim) {
      ctx.beginPath()
      ctx.arc(x, y, node.r + 1.8, 0, 2 * Math.PI)
      ctx.lineWidth = 1.1
      ctx.strokeStyle = PRIMARY
      ctx.stroke()
    }
    if (node.id === selected) {
      ctx.beginPath()
      ctx.arc(x, y, node.r + (highlight.has(node.id) ? 3.4 : 1.8), 0, 2 * Math.PI)
      ctx.lineWidth = 1.1
      ctx.strokeStyle = "#ffffff"
      ctx.stroke()
    }
    ctx.globalAlpha = 1
  }, [emphasis, highlight, selected])

  const paintPointer = useCallback((node: N, color: string, ctx: CanvasRenderingContext2D) => {
    const x = node.x ?? 0, y = node.y ?? 0
    ctx.fillStyle = color
    ctx.beginPath()
    ctx.arc(x, y, node.r + 2, 0, 2 * Math.PI)
    ctx.fill()
    if (node.always) {
      const r = labelRect(node, x, y, measure(node.label))
      ctx.fillRect(r.left, r.top, r.right - r.left, r.bottom - r.top)
    }
  }, [])

  // Labels after all nodes. Forced: always-on types, the owner, the highlight, the selected and hovered
  // node. Optional, drawn only where they overlap no label and no node: the selection's neighbours, the
  // entities of a filtered document, and everything else once zoomed in. The overlap counts land on the
  // wrapper for the screenshot check.
  const paintLabels = useCallback((ctx: CanvasRenderingContext2D, k: number) => {
    const zoomed = FONT * k >= zoomLabelPx(size.height) || docFilter !== null
    const cands: (LabelCandidate & { node: VNode; text: string })[] = []
    const hovered = hover.current
    for (const n of data.nodes) {
      if (!visible(n) || n.x === undefined || n.y === undefined) continue
      const full = n.id === hovered || n.id === selected
      const force = full || n.always || (!docFilter && highlight.has(n.id))
      if (!force && !zoomed && !emphasis?.has(n.id)) continue
      const text = full ? displayName(n) : n.label
      cands.push({ id: n.id, node: n, text, force, rect: labelRect(n, n.x, n.y, measure(text)) })
    }
    // Hovered and selected last so they paint on top.
    cands.sort((a, b) => Number(a.id === hovered || a.id === selected) - Number(b.id === hovered || b.id === selected))
    const circles = data.nodes
      .filter((n) => visible(n) && n.x !== undefined)
      .map((n) => ({ id: n.id, box: nodeBox(n, n.x!, n.y!, false, measure) }))
    const { drawn, overlaps } = pickLabels(cands, circles)
    const keep = new Set(drawn)
    ctx.font = FONT_CSS
    ctx.textAlign = "center"
    ctx.textBaseline = "top"
    ctx.lineJoin = "round"
    for (const c of cands) {
      if (!keep.has(c.id)) continue
      const dim = emphasis !== null && !emphasis.has(c.id)
      ctx.globalAlpha = dim ? 0.3 : 1
      ctx.lineWidth = FONT * 0.4
      ctx.strokeStyle = HALO
      ctx.strokeText(c.text, c.node.x!, c.rect.top)
      ctx.fillStyle = c.node.owner ? OWNER_COLOR : TEXT
      ctx.fillText(c.text, c.node.x!, c.rect.top)
    }
    ctx.globalAlpha = 1
    const el = wrap.current
    if (el) {
      const onNodes = cands
        .filter((c) => keep.has(c.id))
        .reduce((sum, c) => sum + circles.filter((o) => o.id !== c.id && boxesOverlap(c.rect, o.box)).length, 0)
      el.dataset.nodes = String(circles.length)
      el.dataset.labels = String(drawn.length)
      el.dataset.labelOverlaps = String(overlaps)
      el.dataset.labelNodeOverlaps = String(onNodes)
      el.dataset.labelPx = (FONT * k).toFixed(1)
    }
  }, [data, visible, emphasis, selected, size.height, docFilter, highlight])

  const linkColor = useCallback((l: L) => {
    const s = endId(l.source), t = endId(l.target)
    if (emphasis === null) return l.closed ? "rgba(255, 255, 255, 0.1)" : "rgba(255, 255, 255, 0.2)"
    return emphasis.has(s) && emphasis.has(t) ? "rgba(94, 234, 212, 0.75)" : "rgba(255, 255, 255, 0.05)"
  }, [emphasis])

  return (
    <div ref={wrap} className="absolute inset-0" data-testid="vault-graph">
      {size.width > 0 && (
        <ForceGraph2D<VNode, VLink>
          ref={fg}
          width={size.width}
          height={size.height}
          graphData={data}
          backgroundColor="rgba(0,0,0,0)"
          nodeId="id"
          nodeCanvasObjectMode={() => "replace"}
          nodeCanvasObject={paintNode}
          nodePointerAreaPaint={paintPointer}
          nodeVisibility={visible}
          nodeLabel={() => ""}
          linkVisibility={(l) => !docFilter || l.docIds.includes(docFilter.docId)}
          linkColor={linkColor}
          linkWidth={(l) => (emphasis && emphasis.has(endId(l.source)) && emphasis.has(endId(l.target)) ? 1.6 : 1)}
          linkLineDash={(l) => (l.closed ? [2, 2] : null)}
          linkLabel={(l) => relLabel(l.rel)}
          onRenderFramePost={paintLabels}
          autoPauseRedraw={false}
          warmupTicks={WARMUP_TICKS}
          cooldownTicks={COOLDOWN_TICKS}
          onEngineStop={onEngineStop}
          onNodeHover={(n) => {
            hover.current = n?.id ?? null
          }}
          onNodeClick={(n) => onSelect(n.id)}
          onBackgroundClick={() => onSelect(null)}
          onNodeDragEnd={(n) => {
            pinned.current.add(n.id)
            n.fx = n.x
            n.fy = n.y
          }}
          minZoom={0.3}
          maxZoom={16}
        />
      )}
    </div>
  )
})
