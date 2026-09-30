import { describe, expect, it } from "vitest"
import type { ChatFinal, ChatMeta, Document, IngestEvent, MemoryCandidate } from "@/api/client"
import {
  answerBlocks,
  buildHistory,
  candidateLabel,
  citedNumbers,
  flagView,
  footerText,
  formatMs,
  mergeEvents,
  prettyText,
  saysNotInVault,
  sentenceSpans,
  sourceLabel,
  sourceParts,
  sourceMap,
  splitCitations,
  storedView,
  timeAgo,
  type Turn,
} from "./answer"

const sentencesOf = (text: string) => sentenceSpans(text).map(({ start, end }) => text.slice(start, end))
const ref = (n: number, locator = `page ${n}`) => ({ n, chunk_id: `c_${n}`, doc_id: "d_1", locator })

function turn(over: Partial<Turn> = {}): Turn {
  return { id: "t", question: "q?", phase: "done", startedAt: 0, text: "", ...over }
}

function final(over: Partial<ChatFinal> = {}): ChatFinal {
  return { answer: "a [1].", citations: [], citation_ok: true, flags: [], memory_candidates: [], ...over }
}

describe("splitCitations", () => {
  it("handles [1], [1][2] and [1, 2] like the server", () => {
    expect(splitCitations("Rent is 15000 [1][2]. Ends March [1, 3].")).toEqual([
      { kind: "text", text: "Rent is 15000 " },
      { kind: "cite", n: 1 },
      { kind: "cite", n: 2 },
      { kind: "text", text: ". Ends March " },
      { kind: "cite", n: 1 },
      { kind: "cite", n: 3 },
      { kind: "text", text: "." },
    ])
  })

  it("leaves non-numeric brackets and partial markers as text", () => {
    expect(splitCitations("See [note] and [")).toEqual([{ kind: "text", text: "See [note] and [" }])
    expect(citedNumbers("a [2] b [4,5]")).toEqual([2, 4, 5])
  })
})

describe("sentenceSpans", () => {
  it("splits on sentence ends and newlines, joining citation-only fragments", () => {
    expect(sentencesOf("Rent is 15000. [1]\nIt ends in March [2]! Why? Because.")).toEqual([
      "Rent is 15000. [1]",
      "It ends in March [2]!",
      "Why?",
      "Because.",
    ])
  })

  it("does not split before a lowercase word or a number", () => {
    expect(sentencesOf("Paid Rs. 15,000 on 5. march [1].")).toEqual(["Paid Rs. 15,000 on 5. march [1]."])
  })

  it("keeps offsets into the original text", () => {
    const text = "  One [1].  Two [2].  "
    const spans = sentenceSpans(text)
    expect(spans.map(({ start, end }) => text.slice(start, end))).toEqual(["One [1].", "Two [2]."])
  })
})

describe("answerBlocks", () => {
  const text = "Your rent is 15000 [1]. The landlord is kind. I don't have that detail."

  it("marks uncited sentences only when the server flagged uncited_sentence", () => {
    expect(answerBlocks(text).some((b) => b.uncited)).toBe(false)
    const blocks = answerBlocks(text, ["uncited_sentence"])
    const marked = blocks.filter((b) => b.uncited).map((b) => b.pieces.map((p) => (p.kind === "text" ? p.text : `[${p.n}]`)).join(""))
    expect(marked).toEqual(["The landlord is kind."])
  })

  it("reassembles to the original text, with amounts shown as rupees", () => {
    const joined = answerBlocks(text, ["uncited_sentence"])
      .flatMap((b) => b.pieces)
      .map((p) => (p.kind === "text" ? p.text : `[${p.n}]`))
      .join("")
    expect(joined).toBe(text.replace("15000", "₹15,000"))
  })
})

describe("saysNotInVault", () => {
  it("ignores case, apostrophes and punctuation", () => {
    expect(saysNotInVault("I DON’T have that in your vault.")).toBe(true)
    expect(saysNotInVault("I do not have that, sorry")).toBe(true)
    expect(saysNotInVault("I have that [1].")).toBe(false)
  })
})

describe("flagView", () => {
  it("treats not_in_vault as a calm notice and citation problems as warnings", () => {
    expect(flagView(["not_in_vault"])).toEqual({ notice: "not_in_vault", warnings: [] })
    expect(flagView(["no_context", "not_in_vault"]).notice).toBe("no_context")
    const v = flagView(["no_citation", "invalid_citation", "uncited_sentence"])
    expect(v.notice).toBeNull()
    expect(v.warnings.map((w) => w.flag)).toEqual(["no_citation", "invalid_citation"])
  })
})

describe("sourceMap", () => {
  it("prefers final citations (with quotes) over meta refs", () => {
    const meta: ChatMeta = { entities_used: [], chunks: [ref(1), ref(2)] }
    const m = sourceMap(meta, final({ citations: [{ ...ref(2), quote: "q" }] }))
    expect(m.get(1)).toEqual(ref(1))
    expect(m.get(2)?.quote).toBe("q")
    expect(m.get(3)).toBeUndefined()
  })
})

describe("buildHistory", () => {
  it("keeps completed turns only", () => {
    const turns = [
      turn({ question: "a?", final: final({ answer: "A [1]." }) }),
      turn({ question: "b?", phase: "stopped", text: "partial" }),
      turn({ question: "c?", phase: "error" }),
    ]
    expect(buildHistory(turns)).toEqual([
      { role: "user", content: "a?" },
      { role: "assistant", content: "A [1]." },
    ])
  })
})

describe("footerText", () => {
  it("states only measured facts", () => {
    expect(footerText(turn())).toBeNull()
    const done = { latency_ms: 4120, first_token_ms: 910 }
    expect(footerText(turn({ model: "qwen2.5:7b", local: true, final: final(), done }))).toBe(
      "Answered on this device · qwen2.5:7b · 910 ms first token / 4.1 s total",
    )
    expect(footerText(turn({ model: "qwen2.5:7b", local: false, final: final(), done }))).toBe(
      "Answered on your self-hosted server · qwen2.5:7b · 910 ms first token / 4.1 s total",
    )
    // health unavailable: no model name and no claim about where it ran
    expect(footerText(turn({ final: final(), done }))).toBe("Answered · 910 ms first token / 4.1 s total")
    const none = turn({ model: "m", local: true, final: final({ flags: ["no_context", "not_in_vault"] }), done: { latency_ms: 80, first_token_ms: 80 } })
    expect(footerText(none)).toBe("Answered on this device · nothing matched, no model call · 80 ms")
  })

  it("formats durations", () => {
    expect(formatMs(999)).toBe("999 ms")
    expect(formatMs(1000)).toBe("1.0 s")
  })
})

describe("prettyText", () => {
  it.each([
    // the demo answers as qwen2.5:3b writes them
    ["Your rent is 14500 today", "Your rent is ₹14,500 today"],
    ["It changes to 16000 on 2027-01-01", "It changes to ₹16,000 on 1 Jan 2027", "Your rent is 14500."],
    ["Your salary is 120000 a month", "Your salary is ₹1,20,000 a month"],
    ["You pay 16000 rupees", "You pay ₹16,000"],
    ["the deposit is ₹50000", "the deposit is ₹50,000"],
    ["Rs 62000 was credited", "₹62,000 was credited"],
    // left alone
    ["Your rent is ₹14,500 today", "Your rent is ₹14,500 today"],
    ["The agreement ends in December 2026", "The agreement ends in December 2026"],
    ["Rent from 2026 onwards", "Rent from 2026 onwards"],
    ["Your marks total 82.4 percent", "Your marks total 82.4 percent"],
    ["Pay to account number 48213392", "Pay to account number 48213392"],
    ["The rent PIN code is 560038", "The rent PIN code is 560038"],
    ["Ravi's phone is 9845012345", "Ravi's phone is 9845012345"],
    ["Paid 62,000.00 on 2026-06-01", "Paid 62,000.00 on 1 Jun 2026"],
    ["Card XXXX4821 was used", "Card XXXX4821 was used"],
    ["Rent between 14000-16000", "Rent between 14000-16000"],
  ])("%s", (text, expected, previous = "") => {
    expect(prettyText(text, `${previous} ${text}`)).toBe(expected)
  })

  it("is applied per sentence in answer blocks, citations untouched", () => {
    const [a, b] = answerBlocks("Your rent is 14500 today [2]. It changes to 16000 on 2027-01-01 [3].")
      .filter((blk) => blk.pieces.some((p) => p.kind === "cite"))
    expect(a.pieces).toEqual([{ kind: "text", text: "Your rent is ₹14,500 today " }, { kind: "cite", n: 2 }, { kind: "text", text: "." }])
    expect(b.pieces[0]).toEqual({ kind: "text", text: "It changes to ₹16,000 on 1 Jan 2027 " })
  })
})

describe("sourceLabel", () => {
  const doc = { doc_id: "d_1", path: "pdfs/rent_agreement.pdf" } as Document
  it("names PDFs by file and page, notes by name", () => {
    expect(sourceLabel(ref(2), doc)).toBe("rent_agreement.pdf p.2")
    expect(sourceLabel(ref(2))).toBe("page 2")
    expect(sourceLabel(ref(1, "note: rent-renewal.md"))).toBe("rent-renewal.md")
    expect(sourceParts(ref(3), doc)).toEqual({ name: "rent_agreement.pdf", page: "p.3" })
  })
  it("names a WhatsApp window by file and first message time", () => {
    const chat = { doc_id: "d_2", path: "chats/landlord.txt" } as Document
    expect(sourceParts(ref(1, "chat: 2026-09-18 19:42"), chat)).toEqual({ name: "landlord.txt", page: "18 Sep 19:42" })
    expect(sourceLabel(ref(1, "chat: 2026-09-18 19:42"))).toBe("chat 18 Sep 19:42")
    expect(sourceLabel(ref(1, "chat: notes.txt"))).toBe("notes.txt")
  })
})

describe("mergeEvents", () => {
  const ev = (seq: number, path = `notes/${seq}.md`) =>
    ({ seq, ts: "2026-09-27T10:00:00Z", path, doc_id: `d_${seq}`, signature_status: "unsigned", entities_added: 0, facts_added: 0 }) as IngestEvent

  it("dedupes by seq, newest first, capped", () => {
    const out = mergeEvents([ev(1), ev(2)], [ev(2, "notes/new.md"), ev(3)], 2)
    expect(out.map((e) => e.seq)).toEqual([3, 2])
    expect(out[1].path).toBe("notes/new.md")
  })
})

describe("timeAgo", () => {
  it("rounds to a short relative time", () => {
    const now = Date.parse("2026-09-27T10:00:00Z")
    expect(timeAgo("2026-09-27T09:59:55Z", now)).toBe("just now")
    expect(timeAgo("2026-09-27T09:59:00Z", now)).toBe("1m ago")
    expect(timeAgo("2026-09-27T07:00:00Z", now)).toBe("3h ago")
  })
})

describe("candidateLabel", () => {
  const base = { candidate_id: "mc_1", statement: "By the way my rent went up to 16000 from January.",
    status: "pending", created_at: "2026-09-27T10:00:00Z" } as const

  it("shows field, value and date for a fact candidate, as the Memory page words them", () => {
    const c: MemoryCandidate = { ...base, kind: "fact", field: "rent_amount", value: "16000", valid_from: "2027-01-01" }
    expect(candidateLabel(c, "2026-09-30")).toBe("Rent: ₹16,000 from 1 Jan 2027")
    expect(candidateLabel({ ...c, field: "gym_fee", value: "1500", valid_from: "2026-09-30" }, "2026-09-30"))
      .toBe("Gym fee: ₹1,500")  // holds from today: no date
  })

  it("quotes the owner's own words for a decision candidate", () => {
    const c: MemoryCandidate = { ...base, kind: "decision", statement: "Decided to renew the lease." }
    expect(candidateLabel(c)).toBe("your decision “Decided to renew the lease”")
  })

  it("says what was stored and where to see it", () => {
    const fact = { fact_id: "f_1", entity_id: "e_owner", field: "rent_amount", value: "16000", source_type: "owner_stated",
      valid_from: "2027-01-01", confidence: "high" } as const
    expect(storedView(fact, "2026-09-30")).toEqual({ text: "Remembered Rent: ₹16,000 from 1 Jan 2027", to: "/memory",
      where: "Memory" })
    expect(storedView({ entity_id: "e_d", type: "DECISION", name: "Renew", attrs: {} }))
      .toEqual({ text: "Remembered your decision", to: "/vault", where: "Vault" })
    expect(storedView(null)).toEqual({ text: "Already handled" })
  })
})
