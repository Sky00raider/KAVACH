// Computed citations (TS mirror of kavach/brain/chat.py `_fact_text` / `condition_chunks`): when an answer cites
// a known-fact or condition-check line, the citation's quote is that whole line. The popover shows it as a
// "Checked in code" card above the real passage the fact came from.

export interface SourceBit {
  label: string
  file?: string
}

export interface FactLine {
  kind: "fact"
  field: string
  value: string
  source: SourceBit
  since?: string
  /** A scheduled change named on today's line. */
  next?: { value: string; on: string }
}

export interface ScheduledLine {
  kind: "scheduled"
  field: string
  value: string
  on: string
  /** Today's value it replaces, when there is one. */
  from?: string
  source: SourceBit
}

export interface ConditionRow {
  when: "today" | string
  value?: string
  source?: SourceBit
  met: boolean | null
}

export interface ConditionLine {
  kind: "condition"
  decision: string
  field: string
  op: string
  amount: string
  outcome: string
  /** The date the condition stops being met, when the outcome says so. */
  breaksOn?: string
  rows: ConditionRow[]
}

export type ComputedLine = FactLine | ScheduledLine | ConditionLine

const FACT = /^([a-z][a-z0-9_]*) today = (.+?) \((.*)\)$/
const SCHEDULED = /^([a-z][a-z0-9_]*) from (\d{4}-\d{2}-\d{2}) = (.+?) \((.*)\)$/
const CONDITION = /^condition check \(computed\) for "(.+)" \(([a-z][a-z0-9_]*) (<=|>=|<|>) (\d+)\): outcome: (.+?)\. (.*)$/
const ROW_TODAY = /^[a-z][a-z0-9_]* today = (.+?) \((.*)\) -> (met|NOT met|can't compare)$/
const ROW_LATER = /^from (\d{4}-\d{2}-\d{2}) = (.+?) \((.*)\) -> (met|NOT met|can't compare)$/

/** "bank-signed statement, bank_statement_signed.pdf" -> label + file (a file name has a dot and no spaces). */
function sourceBit(parts: string[]): SourceBit {
  const [label = "", maybeFile] = parts
  return maybeFile && /^[^\s]+\.[a-z0-9]+$/i.test(maybeFile) ? { label, file: maybeFile } : { label }
}

const verdict = (s: string): boolean | null => (s === "met" ? true : s === "NOT met" ? false : null)

export function parseComputed(quote: string | undefined): ComputedLine | null {
  if (!quote) return null
  let m = quote.match(CONDITION)
  if (m) {
    const [, decision, field, op, amount, outcome, rest] = m
    const rows: ConditionRow[] = []
    for (const part of rest.split("; ")) {
      const t = part.match(ROW_TODAY)
      const l = part.match(ROW_LATER)
      if (t) rows.push({ when: "today", value: t[1], source: sourceBit(t[2].split(", ")), met: verdict(t[3]) })
      else if (l) rows.push({ when: l[1], value: l[2], source: sourceBit(l[3].split(", ")), met: verdict(l[4]) })
    }
    const breaks = outcome.match(/stops being met on (\d{4}-\d{2}-\d{2})/)
    return { kind: "condition", decision, field, op, amount, outcome, breaksOn: breaks?.[1], rows }
  }
  m = quote.match(SCHEDULED)
  if (m) {
    const [, field, on, value, inner] = m
    const [change, rest = ""] = inner.split("; ")
    const from = change.match(/from today's (.+)$/)?.[1]
    return { kind: "scheduled", field, on, value, from, source: sourceBit(rest.split(", ")) }
  }
  m = quote.match(FACT)
  if (m) {
    const [, field, value, inner] = m
    const parts = inner.split(", ")
    const since = parts.find((p) => p.startsWith("since "))?.slice(6)
    const change = parts.map((p) => p.match(/^changes to (.+) on (\d{4}-\d{2}-\d{2})$/)).find(Boolean)
    const plain = parts.filter((p) => !p.startsWith("since ") && !/^changes to /.test(p))
    return { kind: "fact", field, value, since, next: change ? { value: change[1], on: change[2] } : undefined,
             source: sourceBit(plain) }
  }
  return null
}

/** Strings to highlight in the real passage: the decision's words, else the value as it may be written there
 *  (14500 -> "14,500", "14500"; the first one found wins). */
export function highlightCandidates(line: ComputedLine): string[] {
  if (line.kind === "condition") return [line.decision, line.decision.replace(/\.$/, "")]
  const v = line.value
  if (/^\d+$/.test(v)) {
    const n = Number(v)
    const variants = [n.toLocaleString("en-IN"), n.toLocaleString("en-US"), v]
    if (n >= 1000 && n % 1000 === 0) variants.push(`${n / 1000}k`)
    return [...new Set(variants)]
  }
  return [v]
}

/** The first candidate found in `text` (case-insensitive), as [start, end), else null. */
export function findHighlight(text: string, candidates: string[]): [number, number] | null {
  const low = text.toLowerCase()
  for (const c of candidates) {
    if (!c) continue
    const at = low.indexOf(c.toLowerCase())
    if (at >= 0) return [at, at + c.length]
  }
  return null
}
