// Memory page logic (BRAIN step 11): /api/memory/timeline grouped per field, kept free of React for vitest.
import type { Document, FactVersion } from "@/api/client"
import { basename } from "../ask/answer"
import { issuerName } from "../graph/graph"

/** One field's history: today's value, known future changes, and what it used to be. */
export interface FieldGroup {
  field: string
  current?: FactVersion
  /** Open facts whose date has not come yet, soonest first. */
  scheduled: FactVersion[]
  /** Replaced values (a different value closed them), newest first. */
  past: FactVersion[]
  /** Facts restating a shown fact's value from another source, keyed by the shown fact's id. */
  support: Map<string, FactVersion[]>
}

const norm = (v: string) => v.split(/\s+/).filter(Boolean).join(" ").toLowerCase()

/** Local calendar date as ISO `YYYY-MM-DD` (what "today" means to the owner). */
export function todayIso(now = new Date()): string {
  const p = (n: number) => String(n).padStart(2, "0")
  return `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())}`
}

const isOpen = (v: FactVersion) => v.valid_to == null && v.superseded_by == null

/**
 * The fact a supporting fact backs: follow `superseded_by` while the value stays the same (the server closes a
 * restated value against the stronger source, CONTRACT §4). Returns the fact itself when it supports nothing.
 */
function supportRoot(v: FactVersion, byId: Map<string, FactVersion>): FactVersion {
  let cur = v
  const seen = new Set([v.fact_id])
  for (;;) {
    const next = cur.superseded_by ? byId.get(cur.superseded_by) : undefined
    if (!next || norm(next.value) !== norm(v.value) || seen.has(next.fact_id)) return cur
    seen.add(next.fact_id)
    cur = next
  }
}

export function groupTimeline(versions: FactVersion[], today = todayIso()): FieldGroup[] {
  const byId = new Map(versions.map((v) => [v.fact_id, v]))
  const groups = new Map<string, FieldGroup>()
  const group = (field: string) => {
    let g = groups.get(field)
    if (!g) groups.set(field, (g = { field, scheduled: [], past: [], support: new Map() }))
    return g
  }
  for (const v of versions) {
    const g = group(v.field)
    const root = supportRoot(v, byId)
    if (root !== v) {
      g.support.set(root.fact_id, [...(g.support.get(root.fact_id) ?? []), v])
    } else if (v.current) {
      g.current = v
    } else if (isOpen(v) && (v.valid_from ?? "") > today) {
      g.scheduled.push(v)
    } else {
      g.past.push(v)
    }
  }
  const learned = new Map<string, string>()
  for (const v of versions) if (v.created_at > (learned.get(v.field) ?? "")) learned.set(v.field, v.created_at)
  for (const g of groups.values()) {
    g.scheduled.sort((a, b) => (a.valid_from ?? "").localeCompare(b.valid_from ?? ""))
    g.past.sort((a, b) => (b.valid_from ?? "").localeCompare(a.valid_from ?? "") || b.created_at.localeCompare(a.created_at))
  }
  // a change coming first, then what KAVACH learned most recently (a dropped file's field moves to the top),
  // then by label
  return [...groups.values()].sort((a, b) =>
    Number(!a.scheduled.length) - Number(!b.scheduled.length)
    || (learned.get(b.field) ?? "").localeCompare(learned.get(a.field) ?? "")
    || fieldLabel(a.field).localeCompare(fieldLabel(b.field)))
}

const FIELD_LABEL: Record<string, string> = {
  monthly_income: "Monthly income",
  loan_default_12m: "Loan default (last 12 months)",
  date_of_birth: "Date of birth",
  percentage: "Percentage",
  result: "Result",
  board: "Exam board",
  rent_amount: "Rent",
  agreement_end_date: "Rent agreement ends",
  id_expiry: "ID card expires",
  emi_date: "EMI date",
  employer: "Employer",
  landlord: "Landlord",
}

export function fieldLabel(field: string): string {
  const s = FIELD_LABEL[field] ?? field.replaceAll("_", " ")
  return s[0].toUpperCase() + s.slice(1)
}

const AMOUNT_FIELD = /(^|_)(income|salary|rent|fee|fees|amount|deposit|emi|price|cost|budget|limit)($|_)/
const DATE_FIELD = new Set(["date_of_birth", "agreement_end_date", "id_expiry"])

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

/** `2027-01-01` -> "1 Jan 2027"; anything else unchanged. */
export function formatDate(iso: string | null | undefined): string {
  const m = iso?.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]} ${m[1]}` : (iso ?? "")
}

/** Value as the owner reads it: rupees with Indian grouping, percentages, dates, yes/no. */
export function formatValue(field: string, value: string): string {
  const v = value.trim()
  if (AMOUNT_FIELD.test(field) && /^\d+$/.test(v) && field !== "emi_date") return `₹${Number(v).toLocaleString("en-IN")}`
  if (field === "percentage" && /^\d+(\.\d+)?$/.test(v)) return `${v}%`
  if (DATE_FIELD.has(field) || (field === "emi_date" && /^\d{4}-\d{2}-\d{2}$/.test(v))) return formatDate(v)
  if (field === "emi_date" && /^\d{1,2}$/.test(v)) return `Day ${v} of the month`
  if (field === "result" || field === "loan_default_12m") return v[0]?.toUpperCase() + v.slice(1)
  return v
}

export type Tone = "signed" | "doc" | "owner"

/** Where a fact comes from, mirroring `chat.source_label` on the server. */
export function sourceView(v: Pick<FactVersion, "source_type">, doc?: Document): { label: string; tone: Tone; file?: string } {
  const file = doc ? basename(doc.path) : undefined
  if (v.source_type === "owner_stated") return { label: "You told me", tone: "owner" }
  if (v.source_type === "issuer_doc") {
    const who = doc?.iss ? ` by ${issuerName(doc.iss)}` : ""
    return { label: `Signed${who}`, tone: "signed", file }
  }
  const label = doc?.source === "chat" ? "WhatsApp chat" : doc?.source === "note" ? "Your notes" : "Unsigned document"
  return { label, tone: "doc", file }
}

/** Whole days from `today` to `iso` (both `YYYY-MM-DD`). */
export function daysUntil(iso: string, today = todayIso()): number {
  return Math.round((Date.parse(`${iso}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 86_400_000)
}

/** "in 3 months", "in 12 days", "tomorrow". */
export function untilText(iso: string, today = todayIso()): string {
  const d = daysUntil(iso, today)
  if (d <= 0) return "today"
  if (d === 1) return "tomorrow"
  if (d < 45) return `in ${d} days`
  const months = Math.round(d / 30.4)
  return months < 18 ? `in ${months} months` : `in ${Math.round(d / 365)} years`
}

/** "Since 5 Jun 2026" / "5 Jun 2026 – 30 Sep 2026" for a value's period. */
export function periodText(v: Pick<FactVersion, "valid_from" | "valid_to">): string {
  if (!v.valid_from) return ""
  if (!v.valid_to || v.valid_to === v.valid_from) return v.valid_to ? formatDate(v.valid_from) : `Since ${formatDate(v.valid_from)}`
  return `${formatDate(v.valid_from)} – ${formatDate(v.valid_to)}`
}

/** Search over label, field name and every shown value. */
export function matchesFilter(g: FieldGroup, q: string): boolean {
  const s = q.trim().toLowerCase()
  if (!s) return true
  const values = [g.current, ...g.scheduled, ...g.past].filter(Boolean).map((v) => formatValue(g.field, v!.value))
  return [fieldLabel(g.field), g.field, ...values].some((t) => t.toLowerCase().includes(s))
}

/** Toast text after teaching: "Remembered Rent: ₹16,000 from 1 Jan 2027 (replaces ₹14,500)". */
export function taughtText(fact: Pick<FactVersion, "field" | "value" | "valid_from">, replaced: { value: string }[], today = todayIso()): string {
  const when = fact.valid_from && fact.valid_from !== today ? ` from ${formatDate(fact.valid_from)}` : ""
  const rep = replaced.length ? ` (replaces ${replaced.map((r) => formatValue(fact.field, r.value)).join(", ")})` : ""
  return `Remembered ${fieldLabel(fact.field)}: ${formatValue(fact.field, fact.value)}${when}${rep}`
}
