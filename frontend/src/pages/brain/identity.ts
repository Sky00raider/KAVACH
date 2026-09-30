// Identity anchor view (CONTRACT §6.6): whose name makes a signed document the owner's. Masked by the server.
import { ApiError, type Identity } from "@/api/client"
import { issuerName } from "./graph/graph"

export interface IdentityView {
  tone: "uidai" | "signed" | "unverified"
  title: string
  detail: string
}

export function identityView(id: Identity): IdentityView {
  const who = [id.name_initials, id.birth_year ? `born ${id.birth_year}` : null].filter(Boolean).join(" · ")
  if (id.source === "aadhaar_okyc") return { tone: "uidai", title: "Verified by UIDAI", detail: who }
  if (id.source === "signed_id") {
    return { tone: "signed", title: `From your ID signed by ${id.issuer ? issuerName(id.issuer) : "an issuer"}`, detail: who }
  }
  return { tone: "unverified", title: "Identity not verified", detail: `Using the configured name (${id.name_initials})` }
}

const AADHAAR_ERRORS: Record<string, string> = {
  wrong_share_code: "That share code doesn't open the file.",
  bad_signature: "UIDAI's signature doesn't match: the file was changed or isn't from UIDAI.",
  not_a_zip: "That isn't an Aadhaar offline e-KYC ZIP.",
  no_xml: "That isn't an Aadhaar offline e-KYC ZIP.",
  malformed: "That isn't an Aadhaar offline e-KYC ZIP.",
  too_large: "That file is too large to be an offline e-KYC ZIP.",
}

/** What to tell the owner when an import fails. Never echoes anything from the file. */
export function aadhaarErrorText(e: unknown): string {
  if (e instanceof ApiError) {
    const reason = (e.detail as { detail?: unknown } | null)?.detail ?? e.detail
    if (typeof reason === "string" && reason in AADHAAR_ERRORS) return AADHAAR_ERRORS[reason]
    if (e.status === 503 || e.status >= 500) return "KAVACH couldn't check the file. Try again."
  }
  return e instanceof Error ? e.message : String(e)
}
