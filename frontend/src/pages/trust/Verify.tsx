import { useState, type FormEvent } from "react"
import { Bot, CheckCircle2, Circle, FileJson, Globe, Loader2, Send, ShieldAlert, ShieldCheck, XCircle } from "lucide-react"
import { toast } from "sonner"
import { requesterApi, type RRequest, type VerifierOutput } from "@/api/client"
import { POLL_MS, usePoll } from "@/api/poll"
import { Page } from "@/components/shared/Page"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { cn } from "@/components/lib/utils"
import { ANSWER, TONE, bytes, claimText, resultText, type AnswerType } from "./format"

const SUGGESTED = [
  "Does the tenant earn at least ₹50,000 a month?",
  "Is the tenant over 21?",
  "Any loan default in the last 12 months?",
]

const STATUS_TEXT: Record<RRequest["status"], string> = {
  pending_pairing: "Waiting for the owner to accept your key",
  pending: "Waiting for the owner's decision",
  done: "Answered",
}

export default function Verify() {
  const requests = usePoll(requesterApi.requests, POLL_MS.requesterRequests)
  const storage = usePoll(requesterApi.storage, 3000)
  const [question, setQuestion] = useState("")
  const [sending, setSending] = useState(false)

  async function ask(e?: FormEvent, text = question) {
    e?.preventDefault()
    const q = text.trim()
    if (!q || sending) return
    setSending(true)
    try {
      await requesterApi.ask(q)
      setQuestion("")
      requests.refresh()
      toast.success("Question sent. The owner decides what, if anything, to disclose.")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setSending(false)
    }
  }

  const list = requests.data ?? []
  return (
    <Page title="Verify" description="Ask the owner a yes/no question and check the proof yourself. You never receive a document.">
      <Card>
        <CardContent className="flex flex-col gap-3">
          <form onSubmit={ask} className="flex gap-2">
            <Input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="Ask about income, age, marks, result or loan default…"
              aria-label="Question for the owner"
              maxLength={500}
            />
            <Button type="submit" disabled={sending || !question.trim()}>
              {sending ? <Loader2 className="animate-spin" /> : <Send />} Ask
            </Button>
          </form>
          <div className="flex flex-wrap gap-2">
            {SUGGESTED.map((s) => (
              <Button key={s} type="button" variant="outline" size="xs" disabled={sending} onClick={() => ask(undefined, s)}>
                {s}
              </Button>
            ))}
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
        <section className="flex flex-col gap-3" aria-label="Your requests">
          {requests.error && !requests.data && <p className="text-sm text-destructive">{requests.error.message}</p>}
          {!requests.loading && list.length === 0 && (
            <div className="rounded-xl border border-dashed py-14 text-center text-sm text-muted-foreground">
              No questions yet. Ask one above, or run the landlord agent.
            </div>
          )}
          {list.map((r) => (
            <RequestCard key={r.local_id} request={r} />
          ))}
        </section>
        <StoragePanel files={storage.data?.files ?? []} />
      </div>
    </Page>
  )
}

function RequestCard({ request }: { request: RRequest }) {
  const res = request.result
  return (
    <Card className="gap-3 py-4">
      <CardHeader className="px-4">
        <div className="flex items-start justify-between gap-3">
          <CardTitle className="text-sm leading-snug">{request.question}</CardTitle>
          <Badge variant="outline" className="gap-1">
            {request.via === "agent" ? <Bot /> : <Globe />} {request.via === "agent" ? "agent · MCP" : "web"}
          </Badge>
        </div>
        <CardDescription className="flex items-center gap-2 text-xs">
          {request.status !== "done" && <Loader2 className="size-3 animate-spin" />}
          {res ? claimText(res.claim) : STATUS_TEXT[request.status]}
          <span className="font-mono opacity-60">{request.request_id}</span>
        </CardDescription>
      </CardHeader>
      {res && (
        <CardContent className="px-4">
          <Verdict result={res} />
        </CardContent>
      )}
    </Card>
  )
}

function Verdict({ result }: { result: VerifierOutput }) {
  const meta = ANSWER[result.answer_type as AnswerType]
  const proof = result.answer_type === "ISSUER_PROOF" || result.answer_type === "OWNER_ATTESTED"
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium", TONE[meta.tone])}>
          {result.answer_type === "ISSUER_PROOF" ? <ShieldCheck className="size-3.5" /> : <ShieldAlert className="size-3.5" />}
          {meta.label}
        </span>
        {proof && (
          <span className="text-sm">
            Answer: <strong>{result.all_ok ? resultText(result.result) : "not verified"}</strong>
          </span>
        )}
        {proof && (
          <span className={cn("ml-auto text-xs font-medium", result.all_ok ? "text-success" : "text-destructive")}>
            {result.all_ok ? "All checks passed" : "Verification failed"}
          </span>
        )}
      </div>
      <p className="text-xs text-muted-foreground">{meta.blurb}</p>
      {result.answer_type === "OWNER_ATTESTED" && (
        <p className={cn("rounded-md border px-3 py-2 text-xs", TONE.owner)}>
          Owner-attested: this is the owner's signed statement, bound to your request. No bank or board signed it.
        </p>
      )}
      {result.checks.length > 0 && (
        <ul className="grid gap-1.5">
          {result.checks.map((c, i) => (
            <li
              key={c.name}
              className="flex items-start gap-2 text-sm animate-in fade-in slide-in-from-left-1"
              style={{ animationDelay: `${i * 220}ms`, animationDuration: "350ms", animationFillMode: "both" }}
            >
              {c.ok ? (
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" />
              ) : (
                <XCircle className="mt-0.5 size-4 shrink-0 text-destructive" />
              )}
              <span className="min-w-0">
                <span className="font-medium">{c.name}</span>
                {c.detail && <span className="text-muted-foreground"> — {c.detail}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function StoragePanel({ files }: { files: { name: string; size: number; preview_json: string }[] }) {
  const total = files.reduce((n, f) => n + f.size, 0)
  return (
    <Card className="h-fit gap-3 py-4">
      <CardHeader className="px-4">
        <CardTitle className="text-sm">What this laptop stores</CardTitle>
        <CardDescription className="text-xs">
          {files.length} small JSON file{files.length === 1 ? "" : "s"}, {bytes(total)} in total. No documents.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 px-4">
        {files.length === 0 && (
          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            <Circle className="size-3" /> Nothing yet
          </p>
        )}
        {files.map((f) => (
          <details key={f.name} className="group rounded-md border bg-muted/40 px-3 py-2">
            <summary className="flex cursor-pointer list-none items-center gap-2 text-xs">
              <FileJson className="size-3.5 text-primary" />
              <span className="font-mono">{f.name}</span>
              <span className="ml-auto text-muted-foreground">{bytes(f.size)}</span>
            </summary>
            <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap break-all font-mono text-[10px] leading-relaxed text-muted-foreground">
              {f.preview_json}
            </pre>
          </details>
        ))}
      </CardContent>
    </Card>
  )
}
