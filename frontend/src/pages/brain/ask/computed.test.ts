import { describe, expect, it } from "vitest"
import { findHighlight, highlightCandidates, parseComputed } from "./computed"

// Exact strings kavach/brain/chat.py produces (tests/test_chat.py, tests/test_conditions.py)
const FACT = "rent_amount today = 14500 (bank-signed statement, bank_statement_signed.pdf, since 2026-08-05, " +
  "changes to 16000 on 2027-01-01)"
const SCHEDULED = "rent_amount from 2027-01-01 = 16000 (future change from today's 14500; from your notes, inbox_note.md)"
const CONDITION = 'condition check (computed) for "I decided to renew only if rent stays under ₹15,000." ' +
  "(rent_amount < 15000): outcome: the condition stops being met on 2027-01-01. rent_amount today = 14500 " +
  "(bank-signed statement, bank_statement_signed.pdf) -> met; from 2027-01-01 = 16000 (from your notes, " +
  "inbox_note.md) -> NOT met"

describe("parseComputed", () => {
  it("reads today's fact line with its coming change", () => {
    expect(parseComputed(FACT)).toEqual({
      kind: "fact", field: "rent_amount", value: "14500", since: "2026-08-05",
      next: { value: "16000", on: "2027-01-01" },
      source: { label: "bank-signed statement", file: "bank_statement_signed.pdf" },
    })
    expect(parseComputed("gym_fee today = 1500 (you told me, since 2026-09-01)")).toMatchObject({
      kind: "fact", source: { label: "you told me" }, since: "2026-09-01", next: undefined })
  })

  it("reads a scheduled line", () => {
    expect(parseComputed(SCHEDULED)).toEqual({
      kind: "scheduled", field: "rent_amount", on: "2027-01-01", value: "16000", from: "14500",
      source: { label: "from your notes", file: "inbox_note.md" },
    })
    expect(parseComputed("rent_amount from 2027-01-01 = 16000 (future change; from your notes, inbox_note.md)"))
      .toMatchObject({ kind: "scheduled", from: undefined })
  })

  it("reads a condition check", () => {
    const c = parseComputed(CONDITION)
    expect(c).toMatchObject({
      kind: "condition", decision: "I decided to renew only if rent stays under ₹15,000.", field: "rent_amount",
      op: "<", amount: "15000", breaksOn: "2027-01-01",
    })
    expect(c?.kind === "condition" && c.rows).toEqual([
      { when: "today", value: "14500", met: true,
        source: { label: "bank-signed statement", file: "bank_statement_signed.pdf" } },
      { when: "2027-01-01", value: "16000", met: false, source: { label: "from your notes", file: "inbox_note.md" } },
    ])
  })

  it("leaves ordinary quotes alone", () => {
    expect(parseComputed("My monthly rent for the Indiranagar flat is ₹15,000.")).toBeNull()
    expect(parseComputed(undefined)).toBeNull()
  })
})

describe("highlighting the real passage", () => {
  it("finds the value as the passage writes it", () => {
    const text = "2026-08-05 UPI/RENT/RAVI KUMAR 14,500.00 65,750.00"
    const range = findHighlight(text, highlightCandidates(parseComputed(FACT)!))
    expect(range && text.slice(...range)).toBe("14,500")
    const note = "Landlord said rent goes to ₹16k from January."
    const r2 = findHighlight(note, highlightCandidates(parseComputed(SCHEDULED)!))
    expect(r2 && note.slice(...r2)).toBe("16k")
  })

  it("finds the decision's words", () => {
    const text = "# Rent Renewal Decision\n\nI decided to renew only if rent stays under ₹15,000. Otherwise I move."
    const range = findHighlight(text, highlightCandidates(parseComputed(CONDITION)!))
    expect(range && text.slice(...range)).toBe("I decided to renew only if rent stays under ₹15,000.")
    expect(findHighlight("nothing here", ["x"])).toBeNull()
  })
})
