import { describe, expect, it } from "vitest"
import type { Document, FactVersion } from "@/api/client"
import {
  fieldLabel,
  formatDate,
  formatValue,
  groupTimeline,
  matchesFilter,
  periodText,
  shortMonth,
  sourceView,
  taughtText,
  timelineOf,
  todayIso,
  untilText,
} from "./memory"

const TODAY = "2026-09-30"

function fv(id: string, value: string, over: Partial<FactVersion> = {}): FactVersion {
  return {
    fact_id: id,
    entity_id: "e_owner",
    field: "rent_amount",
    value,
    source_type: "extracted",
    doc_id: null,
    quote: value,
    valid_from: "2026-09-18",
    valid_to: null,
    superseded_by: null,
    confidence: "high",
    created_at: "2026-09-30T10:00:00Z",
    current: false,
    ...over,
  } as FactVersion
}

// The demo rent chain after inbox_note.md (CONTRACT §4): the signed statement is current, notes and the chat
// restate it (closed against it), the inbox note schedules ₹16,000 for January, an old value was replaced.
const bank = fv("f_bank", "14500", { source_type: "issuer_doc", doc_id: "d_bank", valid_from: "2026-06-05", current: true })
const chat = fv("f_chat", "14500", { doc_id: "d_chat", valid_to: "2026-09-18", superseded_by: "f_bank" })
const note = fv("f_note", "14500", { doc_id: "d_note", valid_from: "2026-09-27", valid_to: "2026-09-27", superseded_by: "f_chat" })
const inbox = fv("f_inbox", "16000", { doc_id: "d_inbox", valid_from: "2027-01-01" })
const old = fv("f_old", "13000", { valid_from: "2025-06-01", valid_to: "2026-06-05", superseded_by: "f_bank" })
const landlord = fv("f_ll", "Ravi Kumar", { field: "landlord", current: true })

describe("groupTimeline", () => {
  const [rent, ll] = groupTimeline([old, note, inbox, chat, bank, landlord], TODAY)

  it("splits a field into current, scheduled, past and supporting sources", () => {
    expect(rent.field).toBe("rent_amount")
    expect(rent.current?.fact_id).toBe("f_bank")
    expect(rent.scheduled.map((v) => v.fact_id)).toEqual(["f_inbox"])
    expect(rent.past.map((v) => v.fact_id)).toEqual(["f_old"])
  })

  it("follows a same-value chain to the fact it supports", () => {
    expect(rent.support.get("f_bank")?.map((v) => v.fact_id).sort()).toEqual(["f_chat", "f_note"])
  })

  it("orders fields: a change coming, then most recently learned, then by label", () => {
    expect(ll.field).toBe("landlord")  // rent has the January change, so it comes first
    const later = (f: FactVersion, created_at: string) => ({ ...f, created_at })
    const income = fv("f_inc", "62000", { field: "monthly_income", current: true })
    expect(groupTimeline([landlord, later(income, "2026-09-30T12:00:00Z")], TODAY).map((g) => g.field))
      .toEqual(["monthly_income", "landlord"])
    expect(groupTimeline([later(income, landlord.created_at), landlord], TODAY).map((g) => g.field))
      .toEqual(["landlord", "monthly_income"])
  })

  it("a scheduled date that has passed is no longer scheduled", () => {
    const [g] = groupTimeline([inbox], "2027-01-02")
    expect(g.scheduled).toEqual([])
    expect(g.past.map((v) => v.fact_id)).toEqual(["f_inbox"])
  })
})

describe("formatting", () => {
  it("formats values by field", () => {
    expect(formatValue("rent_amount", "14500")).toBe("₹14,500")
    expect(formatValue("monthly_income", "120000")).toBe("₹1,20,000")
    expect(formatValue("gym_membership_fee", "1500")).toBe("₹1,500")
    expect(formatValue("percentage", "82.4")).toBe("82.4%")
    expect(formatValue("agreement_end_date", "2026-12-31")).toBe("31 Dec 2026")
    expect(formatValue("emi_date", "5")).toBe("Day 5 of the month")
    expect(formatValue("emi_date", "2026-10-05")).toBe("5 Oct 2026")
    expect(formatValue("result", "pass")).toBe("Pass")
    expect(formatValue("landlord", "Ravi Kumar")).toBe("Ravi Kumar")
  })

  it("labels fields", () => {
    expect(fieldLabel("rent_amount")).toBe("Rent")
    expect(fieldLabel("gym_membership_fee")).toBe("Gym membership fee")
  })

  it("dates and periods", () => {
    expect(formatDate("2027-01-01")).toBe("1 Jan 2027")
    expect(periodText({ valid_from: "2026-06-05", valid_to: null })).toBe("Since 5 Jun 2026")
    expect(periodText({ valid_from: "2025-06-01", valid_to: "2026-06-05" })).toBe("1 Jun 2025 – 5 Jun 2026")
    expect(untilText("2027-01-01", TODAY)).toBe("in 3 months")
    expect(untilText("2026-10-01", TODAY)).toBe("tomorrow")
    expect(untilText("2026-10-12", TODAY)).toBe("in 12 days")
    expect(todayIso(new Date(2026, 8, 30, 23, 59))).toBe("2026-09-30")
  })
})

describe("sourceView", () => {
  const doc = (source: string, path: string, iss: string | null = null) => ({ doc_id: "d", source, path, iss }) as Document
  it("names the source like chat does", () => {
    expect(sourceView(bank, doc("pdf", "pdfs/bank_statement_signed.pdf", "mock_bank"))).toEqual({
      label: "Signed by Mock Bank", tone: "signed", file: "bank_statement_signed.pdf" })
    expect(sourceView(chat, doc("chat", "chats/landlord.txt")).label).toBe("WhatsApp chat")
    expect(sourceView(note, doc("note", "notes/budget.md")).label).toBe("Your notes")
    expect(sourceView(fv("t", "1", { source_type: "owner_stated" }))).toEqual({ label: "You told me", tone: "owner" })
  })
})

describe("timelineOf", () => {
  it("lays out past, current and scheduled values on one bar with today marked", () => {
    const [rent] = groupTimeline([old, bank, inbox, chat, note], TODAY)
    const t = timelineOf(rent, TODAY)!
    expect(t.segments.map((s) => [s.fact.fact_id, s.kind, s.from, s.to])).toEqual([
      ["f_old", "past", "2025-06-01", "2026-06-05"],
      ["f_bank", "current", "2026-06-05", "2027-01-01"],
      ["f_inbox", "scheduled", "2027-01-01", t.end],
    ])
    // supporting sources are not segments of their own
    expect(t.segments.some((s) => s.fact.fact_id === "f_chat")).toBe(false)
    const [a, b, c] = t.segments
    expect(a.left).toBe(0)
    expect(b.left).toBeCloseTo(a.left + a.width, 5)
    expect(c.left + c.width).toBeCloseTo(100, 5)
    expect(t.today).toBeGreaterThan(b.left)
    expect(t.today).toBeLessThan(c.left)
    expect(t.ticks.map((x) => x.label)).toEqual(["Jun 2025", shortMonth(t.end)])
  })

  it("is null for a field with a single value", () => {
    const [ll] = groupTimeline([landlord], TODAY)
    expect(timelineOf(ll, TODAY)).toBeNull()
  })

  it("gives a short history a minimum span, so today never sits on the edge", () => {
    const now = fv("f_n", "14500", { valid_from: "2026-09-29", current: true })
    const soon = fv("f_s", "16000", { valid_from: "2026-10-01" })
    const t = timelineOf(groupTimeline([now, soon], TODAY)[0], TODAY)!
    expect(t.today).toBeGreaterThan(0)
    expect(t.today).toBeLessThan(10)
    expect(t.segments[1].width).toBeGreaterThan(50)
  })
})

describe("matchesFilter and taughtText", () => {
  const [rent] = groupTimeline([bank, inbox], TODAY)
  it("filters on label, field name and shown values", () => {
    expect(matchesFilter(rent, "rent")).toBe(true)
    expect(matchesFilter(rent, "16,000")).toBe(true)
    expect(matchesFilter(rent, "salary")).toBe(false)
  })
  it("says what was remembered and what it replaced", () => {
    expect(taughtText({ field: "rent_amount", value: "16000", valid_from: "2027-01-01" }, [{ value: "14500" }], TODAY))
      .toBe("Remembered Rent: ₹16,000 from 1 Jan 2027 (replaces ₹14,500)")
    expect(taughtText({ field: "gym_fee", value: "1500", valid_from: TODAY }, [], TODAY)).toBe("Remembered Gym fee: ₹1,500")
  })
})
