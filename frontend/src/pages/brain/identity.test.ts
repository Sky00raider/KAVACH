import { describe, expect, it } from "vitest"
import { ApiError, type Identity } from "@/api/client"
import { aadhaarErrorText, identityView } from "./identity"

const base: Identity = { status: "verified", source: "signed_id", issuer: "mock_govt", name_initials: "A. I.", birth_year: 2003, doc_id: "d_1", verified_at: "2026-09-30T13:35:46Z" }

describe("identityView", () => {
  it("names the anchor's source, masked", () => {
    expect(identityView({ ...base, source: "aadhaar_okyc", issuer: "uidai", doc_id: null }))
      .toEqual({ tone: "uidai", title: "Verified by UIDAI", detail: "A. I. · born 2003" })
    expect(identityView(base)).toEqual({ tone: "signed", title: "From your ID signed by Mock Govt", detail: "A. I. · born 2003" })
    expect(identityView({ ...base, status: "not_verified", source: "config", issuer: null, birth_year: null }).tone).toBe("unverified")
  })
})

describe("aadhaarErrorText", () => {
  it("explains each server reason without echoing the file", () => {
    const err = (status: number, reason: string) => new ApiError(status, { detail: reason }, `${status} ${reason}`)
    expect(aadhaarErrorText(err(400, "wrong_share_code"))).toBe("That share code doesn't open the file.")
    expect(aadhaarErrorText(err(422, "bad_signature"))).toMatch(/UIDAI's signature doesn't match/)
    expect(aadhaarErrorText(err(400, "not_a_zip"))).toMatch(/isn't an Aadhaar offline e-KYC ZIP/)
    expect(aadhaarErrorText(err(500, "boom"))).toMatch(/couldn't check/)
  })
})
