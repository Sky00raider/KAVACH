import { NotBuilt, Page } from "@/components/shared/Page"

export default function Verify() {
  return (
    <Page title="Verify" description="Ask the owner a question and check the proof you get back.">
      <NotBuilt track="TRUST" step="BUILD_PLAN §6.3 step 4" />
    </Page>
  )
}
