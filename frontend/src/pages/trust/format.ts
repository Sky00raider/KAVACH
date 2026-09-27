// Shared wording for the TRUST pages (Queue, Verify, Audit). Owner-facing text never shows raw fact values.
import type { RequestView } from "@/api/client"

type Claim = NonNullable<RequestView["claim"]>
export type AnswerType = "ISSUER_PROOF" | "OWNER_ATTESTED" | "DECLINED" | "CANNOT_CONFIRM" | "REFUSED"

const inr = (n: number) => `₹${n.toLocaleString("en-IN")}`

const ISSUER_CLAIM_TEXT: Record<string, string> = {
  income_ge_25000: `monthly income ≥ ${inr(25000)}`,
  income_ge_50000: `monthly income ≥ ${inr(50000)}`,
  income_ge_75000: `monthly income ≥ ${inr(75000)}`,
  income_ge_100000: `monthly income ≥ ${inr(100000)}`,
  loan_default_12m: "loan default in the last 12 months",
  age_over_18: "age over 18",
  age_over_21: "age over 21",
  percentage_ge_60: "marks ≥ 60%",
  percentage_ge_75: "marks ≥ 75%",
  percentage_ge_90: "marks ≥ 90%",
  result_pass: "passed the exam",
  board: "examination board",
}

/** "income ge 60000" (verifier text) or an issuer claim name -> plain words. */
export function claimText(claim: Claim | string | null | undefined): string {
  if (!claim) return "—"
  if (typeof claim === "string") {
    if (ISSUER_CLAIM_TEXT[claim]) return ISSUER_CLAIM_TEXT[claim]
    const m = /^(\w+) (ge|is|eq) (.+)$/.exec(claim)
    if (!m) return claim === "unsupported" ? "not something KAVACH will answer" : claim
    const value = Number(m[3])
    return claimText({ claim: m[1] as Claim["claim"], op: m[2] as Claim["op"], value: Number.isNaN(value) ? m[3] : value })
  }
  if (claim.issuer_claim && ISSUER_CLAIM_TEXT[claim.issuer_claim]) return ISSUER_CLAIM_TEXT[claim.issuer_claim]
  const v = claim.value
  switch (claim.claim) {
    case "income":
      return typeof v === "number" ? `monthly income ≥ ${inr(v)}` : "monthly income"
    case "age":
      return typeof v === "number" ? `age ≥ ${v}` : "age"
    case "percentage":
      return typeof v === "number" ? `marks ≥ ${v}%` : "marks"
    case "loan_default_12m":
      return "loan default in the last 12 months"
    case "result":
      return "passed the exam"
    case "board":
      return "examination board"
    default:
      return "not something KAVACH will answer"
  }
}

export function resultText(result: unknown): string {
  if (result === true) return "Yes"
  if (result === false) return "No"
  if (result === null || result === undefined) return "—"
  return String(result)
}

export const ANSWER: Record<AnswerType, { label: string; tone: "good" | "owner" | "muted" | "bad"; blurb: string }> = {
  ISSUER_PROOF: { label: "Issuer-signed proof", tone: "good", blurb: "Signed by the issuer; the verifier checks the issuer's key." },
  OWNER_ATTESTED: { label: "Owner-attested", tone: "owner", blurb: "The owner's signed word, not the issuer's." },
  DECLINED: { label: "Declined", tone: "muted", blurb: "The owner chose not to answer." },
  CANNOT_CONFIRM: { label: "Cannot confirm", tone: "muted", blurb: "No issuer-backed data covers this question." },
  REFUSED: { label: "Refused", tone: "bad", blurb: "Not disclosable, or it would narrow what was already shared." },
}

export const TONE: Record<"good" | "owner" | "muted" | "bad" | "warn", string> = {
  good: "border-success/40 bg-success/10 text-success",
  owner: "border-warning/40 bg-warning/10 text-warning",
  warn: "border-warning/40 bg-warning/10 text-warning",
  muted: "border-border bg-muted text-muted-foreground",
  bad: "border-destructive/40 bg-destructive/10 text-destructive",
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "—"
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const secs = Math.round((Date.now() - d.getTime()) / 1000)
  if (secs < 60) return "just now"
  if (secs < 3600) return `${Math.floor(secs / 60)} min ago`
  if (secs < 86400) return `${Math.floor(secs / 3600)} h ago`
  return d.toLocaleString()
}

export function bytes(n: number): string {
  return n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`
}
