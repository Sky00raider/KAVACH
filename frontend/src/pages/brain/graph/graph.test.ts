import { describe, expect, it } from "vitest"
import type { Graph } from "@/api/client"
import graphFixture from "@fixtures/api/graph.json"
import {
  LABEL_MAX,
  NODE_R,
  OWNER_ID,
  OWNER_R,
  approxMeasure,
  boxesOverlap,
  buildView,
  collideBoxes,
  countOverlaps,
  degrees,
  docEntityIds,
  docFilterNotice,
  fitLabelPx,
  fitTransform,
  gravity,
  groupEdges,
  isAlwaysLabelled,
  issuerName,
  labelRect,
  neighbours,
  nodeBox,
  pickLabels,
  relLabel,
  shortLabel,
  signatureView,
  zoomLabelPx,
  type VNode,
} from "./graph"

const edge = (id: string, src: string, dst: string, extra: Partial<Graph["edges"][number]> = {}): Graph["edges"][number] => ({
  id, src, dst, rel: "RELATES_TO", valid_from: null, valid_to: null, source_chunk_id: `c_${id}`, doc_id: "d_1", ...extra,
})

const GRAPH: Graph = {
  nodes: [
    { id: OWNER_ID, type: "PERSON", name: "Ananya Iyer" },
    { id: "e_ravi", type: "PERSON", name: "Ravi Kumar" },
    { id: "e_flat", type: "PROJECT", name: "Flat move 2026" },
    { id: "e_rent", type: "CONCEPT", name: "rent" },
    { id: "e_note", type: "DOCUMENT", name: "flat_move_2026.md" },
    { id: "e_lone", type: "CONCEPT", name: "salary" },
  ],
  edges: [
    edge("x_1", "e_ravi", OWNER_ID, { rel: "LANDLORD_OF", doc_id: "d_chat" }),
    edge("x_2", OWNER_ID, "e_flat", { rel: "WORKS_ON" }),
    edge("x_3", "e_flat", "e_note", { rel: "MENTIONED_IN", doc_id: "d_note" }),
    edge("x_4", "e_rent", "e_flat", { rel: "PART_OF", doc_id: "d_note" }),
    edge("x_5", "e_ravi", "e_rent", { valid_to: "2026-09-20", doc_id: null, source_chunk_id: "c_gone" }),
  ],
}

describe("graph view", () => {
  it("always labels people, projects, decisions and the owner", () => {
    expect(isAlwaysLabelled({ id: OWNER_ID, type: "PERSON" })).toBe(true)
    expect(isAlwaysLabelled({ id: "e_d", type: "DECISION" })).toBe(true)
    expect(isAlwaysLabelled({ id: "e_p", type: "PROJECT" })).toBe(true)
    expect(isAlwaysLabelled({ id: "e_c", type: "CONCEPT" })).toBe(false)
    expect(isAlwaysLabelled({ id: "e_x", type: "DOCUMENT" })).toBe(false)
  })

  it("leaves hidden types and closed edges out, marks the owner", () => {
    const v = buildView(GRAPH, { hiddenTypes: new Set(["DOCUMENT"]), showClosed: false })
    expect(v.nodes.map((n) => n.id)).toEqual([OWNER_ID, "e_ravi", "e_flat", "e_rent", "e_lone"])
    expect(v.links.map((l) => l.id)).toEqual(["e_ravi|LANDLORD_OF|e_owner", "e_owner|WORKS_ON|e_flat", "e_rent|PART_OF|e_flat"])
    const owner = v.nodes[0]
    expect(owner).toMatchObject({ owner: true, always: true, r: OWNER_R, label: "Ananya Iyer (you)" })
    expect(v.nodes[3]).toMatchObject({ owner: false, always: false, r: NODE_R })

    const all = buildView(GRAPH, { hiddenTypes: new Set(), showClosed: true })
    expect(all.links).toHaveLength(5)
    expect(all.links.find((l) => l.id === "e_ravi|RELATES_TO|e_rent")).toMatchObject({ closed: true, docIds: [] })
  })

  it("keeps positions and pins across rebuilds and starts new nodes next to a placed neighbour", () => {
    const first = buildView(GRAPH, { hiddenTypes: new Set(["DOCUMENT"]), showClosed: false })
    first.nodes.forEach((n, i) => Object.assign(n, { x: i * 50, y: 10, fx: i * 50, fy: 10 }))
    const prev = new Map(first.nodes.map((n) => [n.id, n]))
    const next = buildView(GRAPH, { hiddenTypes: new Set(), showClosed: false }, prev)
    const flat = next.nodes.find((n) => n.id === "e_flat")!
    expect(flat).toMatchObject({ x: 100, y: 10, fx: 100, fy: 10 })
    const note = next.nodes.find((n) => n.id === "e_note")!
    expect(note.fx).toBeUndefined()
    expect(Math.hypot(note.x! - 100, note.y! - 10)).toBeCloseTo(12, 5)
    // deterministic
    const again = buildView(GRAPH, { hiddenTypes: new Set(), showClosed: false }, prev)
    expect(again.nodes.find((n) => n.id === "e_note")).toMatchObject({ x: note.x, y: note.y })
  })

  it("collapses duplicate (src, rel, dst) edges into one link that keeps every source", () => {
    const dup: Graph = {
      nodes: GRAPH.nodes,
      edges: [
        edge("x_1", "e_ravi", OWNER_ID, { rel: "LANDLORD_OF", doc_id: "d_note", source_chunk_id: "c_a" }),
        edge("x_2", "e_ravi", OWNER_ID, { rel: "LANDLORD_OF", doc_id: "d_pdf", source_chunk_id: "c_b" }),
        edge("x_3", "e_ravi", OWNER_ID, { rel: "LANDLORD_OF", doc_id: "d_pdf", source_chunk_id: "c_b" }),
        edge("x_4", "e_ravi", OWNER_ID, { rel: "LANDLORD_OF", doc_id: null, source_chunk_id: "c_gone", valid_to: "2026-09-01" }),
        edge("x_5", OWNER_ID, "e_ravi", { rel: "PAID", doc_id: "d_pdf" }),              // other direction and rel
      ],
    }
    const groups = groupEdges(dup.edges)
    expect(groups.map((g) => [g.key, g.edges.length])).toEqual([["e_ravi|LANDLORD_OF|e_owner", 3], ["e_owner|PAID|e_ravi", 1]])
    expect(groups[0].sources).toEqual([{ chunk_id: "c_a", doc_id: "d_note" }, { chunk_id: "c_b", doc_id: "d_pdf" }])
    expect(groups[0]).toMatchObject({ docIds: ["d_note", "d_pdf"], closed: false })
    const withClosed = groupEdges(dup.edges, true)
    expect(withClosed[0]).toMatchObject({ closed: false })
    expect(withClosed[0].edges).toHaveLength(4)
    expect(groupEdges([dup.edges[3]], true)[0].closed).toBe(true)

    const v = buildView(dup, { hiddenTypes: new Set(), showClosed: false })
    expect(v.links.map((l) => [l.rel, l.docIds])).toEqual([["LANDLORD_OF", ["d_note", "d_pdf"]], ["PAID", ["d_pdf"]]])
    const n = neighbours(dup, "e_ravi")
    expect(n.map((x) => [x.group.rel, x.group.sources.length])).toEqual([["LANDLORD_OF", 2], ["PAID", 1]])
    expect(degrees(dup).get("e_ravi")).toBe(2)
  })

  it("maps a document to the entities its edges touch", () => {
    expect([...docEntityIds(GRAPH, "d_note")].sort()).toEqual(["e_flat", "e_note", "e_rent"])
    expect([...docEntityIds(GRAPH, "d_chat")].sort()).toEqual(["e_owner", "e_ravi"])
    expect(docEntityIds(GRAPH, "d_tampered").size).toBe(0)
  })

  it("says why a document filter is empty", () => {
    expect(docFilterNotice({ signature_status: "invalid" }, 0)).toEqual({ tone: "error", text: "Nothing extracted: signature check failed" })
    expect(docFilterNotice({ signature_status: "invalid" }, 3)?.tone).toBe("error")
    expect(docFilterNotice({ signature_status: "issuer_signed" }, 0)).toEqual({ tone: "muted", text: "No entities extracted from this document" })
    expect(docFilterNotice({ signature_status: "unsigned" }, 2)).toBeNull()
  })

  it("lists neighbours with direction, current only unless asked", () => {
    const n = neighbours(GRAPH, "e_ravi")
    expect(n.map((x) => [x.group.rel, x.other.id, x.outgoing])).toEqual([["LANDLORD_OF", OWNER_ID, true]])
    expect(neighbours(GRAPH, "e_ravi", true)).toHaveLength(2)
    expect(neighbours(GRAPH, "e_flat").map((x) => x.outgoing)).toEqual([true, false, false])
    expect(degrees(GRAPH).get("e_flat")).toBe(3)
    expect(degrees(GRAPH).get("e_lone")).toBeUndefined()
  })

  it("words", () => {
    expect(relLabel("LANDLORD_OF")).toBe("landlord of")
    expect(issuerName("mock_bank")).toBe("Mock Bank")
    expect(signatureView({ signature_status: "issuer_signed", iss: "mock_govt" })).toEqual({ tone: "success", label: "Signed by Mock Govt" })
    expect(signatureView({ signature_status: "invalid", iss: "mock_bank" })).toEqual({ tone: "error", label: "Signature check failed" })
    expect(signatureView({ signature_status: "unsigned", iss: null }).tone).toBe("muted")
    const long = "I decided to renew only if rent stays under ₹15,000"
    expect(shortLabel(long)).toHaveLength(LABEL_MAX)
    expect(shortLabel(long).endsWith("…")).toBe(true)
    expect(shortLabel("rent")).toBe("rent")
  })

  it("validates the committed graph fixture shape", () => {
    const g = graphFixture as Graph
    const ids = new Set(g.nodes.map((n) => n.id))
    for (const e of g.edges) expect(ids.has(e.src) && ids.has(e.dst)).toBe(true)
    expect(g.edges.some((e) => e.doc_id)).toBe(true)
  })
})

describe("labels and layout", () => {
  it("draws forced labels and only non-overlapping optional ones", () => {
    const r = (left: number, top: number) => ({ left, right: left + 10, top, bottom: top + 5 })
    const out = pickLabels([
      { id: "opt", rect: r(2, 0), force: false },
      { id: "a", rect: r(0, 0), force: true },
      { id: "b", rect: r(30, 0), force: true },
      { id: "far", rect: r(60, 0), force: false },
    ])
    expect(out.drawn).toEqual(["a", "b", "far"])
    expect(out.overlaps).toBe(0)
    expect(pickLabels([{ id: "a", rect: r(0, 0), force: true }, { id: "b", rect: r(5, 2), force: true }]).overlaps).toBe(1)
  })

  it("optional labels also stay off other nodes, never their own", () => {
    const rect = { left: 0, right: 10, top: 0, bottom: 5 }
    const own = { id: "opt", box: { left: 3, right: 7, top: -6, bottom: 1 } }
    const other = { id: "e_2", box: { left: 8, right: 12, top: 2, bottom: 6 } }
    expect(pickLabels([{ id: "opt", rect, force: false }], [own]).drawn).toEqual(["opt"])
    expect(pickLabels([{ id: "opt", rect, force: false }], [own, other]).drawn).toEqual([])
    expect(pickLabels([{ id: "f", rect, force: true }], [other]).drawn).toEqual(["f"])
  })

  it("label box sits under the node and widens the node box", () => {
    const n = { r: 4, label: "Flat move 2026" }
    const box = nodeBox(n, 0, 0, true, approxMeasure)
    const l = labelRect(n, 0, 0, approxMeasure(n.label))
    expect(l.top).toBeGreaterThan(n.r)
    expect(box.bottom).toBe(l.bottom)
    expect(box.right - box.left).toBeCloseTo(approxMeasure(n.label))
    expect(nodeBox(n, 0, 0, false, approxMeasure)).toEqual({ left: -4, right: 4, top: -4, bottom: 4 })
  })

  it("the collision force separates always-on labels", () => {
    const names = ["Ananya Iyer (you)", "Ravi Kumar", "Flat move 2026", "Rent renewal", "I decided to renew only if…", "I will probably renew if th…", "rent", "budget", "salary"]
    const nodes: VNode[] = names.map((name, i) => ({
      id: `e_${i}`, type: i < 2 ? "PERSON" : i < 4 ? "PROJECT" : i < 6 ? "DECISION" : "CONCEPT", name, label: name,
      owner: i === 0, always: i < 6, r: i === 0 ? OWNER_R : NODE_R, x: (i % 3) * 2, y: Math.floor(i / 3) * 2, vx: 0, vy: 0,
    }))
    const boxesOf = () => nodes.filter((n) => n.always).map((n) => nodeBox(n, n.x!, n.y!, true, approxMeasure))
    expect(countOverlaps(boxesOf())).toBeGreaterThan(0)
    const collide = collideBoxes(approxMeasure)
    const pull = gravity()
    collide.initialize(nodes)
    pull.initialize(nodes)
    for (let tick = 0; tick < 300; tick++) {
      const alpha = Math.max(0.001, 1 - tick / 300)
      collide(alpha)
      pull(alpha)
      for (const n of nodes) {
        n.x! += n.vx!
        n.y! += n.vy!
        n.vx! *= 0.6
        n.vy! *= 0.6
      }
    }
    expect(countOverlaps(boxesOf())).toBe(0)
    // and the unlabelled circles stay off the labels too
    const all = nodes.map((n) => nodeBox(n, n.x!, n.y!, n.always, approxMeasure))
    expect(countOverlaps(all)).toBe(0)
  })

  it("the collision force gives room to extra labelled nodes (the highlight)", () => {
    const mk = (id: string, x: number): VNode => ({ id, type: "CONCEPT", name: "a long concept name", label: "a long concept name", owner: false, always: false, r: NODE_R, x, y: 0, vx: 0, vy: 0 })
    const run = (labelled?: (n: VNode) => boolean) => {
      const nodes = [mk("a", 0), mk("b", 12)]
      const f = collideBoxes(approxMeasure, labelled)
      f.initialize(nodes)
      for (let i = 0; i < 200; i++) {
        f(1)
        for (const n of nodes) {
          n.x! += n.vx!
          n.vx! *= 0.6
          n.y! += n.vy!
          n.vy! *= 0.6
        }
      }
      return nodes.map((n) => nodeBox(n, n.x!, n.y!, true, approxMeasure))
    }
    expect(countOverlaps(run())).toBe(1) // circles only: the labels still overlap
    expect(countOverlaps(run(() => true))).toBe(0)
  })

  it("fit label size grows with the canvas, zoom-only labels stay above it", () => {
    expect(fitLabelPx(560)).toBe(14)
    expect(fitLabelPx(880)).toBeCloseTo(19, 0)
    expect(fitLabelPx(2000)).toBe(19)
    for (const h of [400, 565, 880, 1400]) expect(zoomLabelPx(h)).toBeGreaterThan(fitLabelPx(h))
  })

  it("fits boxes to the canvas", () => {
    const t = fitTransform([{ left: -50, right: 50, top: -10, bottom: 30 }], 600, 400, 50)!
    expect(t).toEqual({ x: 0, y: 10, k: 5 })
    expect(fitTransform([], 600, 400)).toBeNull()
    // 100 px free on top: same zoom as 50/50 over 350 px, centre shifted up so the box sits lower
    const top = fitTransform([{ left: -50, right: 50, top: -10, bottom: 30 }], 600, 400, { top: 100, right: 50, bottom: 50, left: 50 })!
    expect(top.k).toBe(5)
    expect(top.y).toBeCloseTo(10 - 25 / 5)
    expect(top.x).toBe(0)
    // the box's top edge lands exactly at the top padding: screen y = h/2 + (graphY - centreY) * k
    expect(200 + (-10 - top.y) * top.k).toBeCloseTo(100 + (250 - 200) / 2)
    expect(fitTransform([{ left: 0, right: 0, top: 0, bottom: 0 }], 600, 400, 50, 8)!.k).toBe(8)
    expect(boxesOverlap({ left: 0, right: 1, top: 0, bottom: 1 }, { left: 1, right: 2, top: 0, bottom: 1 })).toBe(false)
  })
})
