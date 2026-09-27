import { useState, type FormEvent, type ReactNode } from "react"
import {
  AlertTriangle, Bot, Check, FileText, Globe, KeyRound, ListChecks, Loader2, Mail, Bell, NotebookPen, ShieldCheck,
  ShieldQuestion, UserCheck, UserX, Wallet, X,
} from "lucide-react"
import { toast } from "sonner"
import { api, type OutboxItem, type RequestAction, type RequestView, type Requester, type Task, type WalletStatus } from "@/api/client"
import { POLL_MS, usePoll } from "@/api/poll"
import { Page } from "@/components/shared/Page"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { cn } from "@/components/lib/utils"
import { ANSWER, TONE, bytes, claimText, resultText, when, type AnswerType } from "./format"

const ACTION_LABEL: Record<RequestAction, string> = {
  approve: "Approve",
  answer: "Answer anyway",
  decline: "Decline",
  deny: "Deny",
}
const TOOL_ICON = { draft_email: Mail, create_reminder: Bell, fill_rental_form: FileText, save_note: NotebookPen }

function useAction() {
  const [busy, setBusy] = useState<string | null>(null)
  async function run<T>(key: string, fn: () => Promise<T>, ok: (v: T) => string, after: () => void) {
    setBusy(key)
    try {
      toast.success(ok(await fn()))
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
      after()
    }
  }
  return { busy, run }
}

export default function Queue() {
  const queue = usePoll(api.queue, POLL_MS.queue)
  const outbox = usePoll(api.outbox, 4000)
  const { busy, run } = useAction()
  const refresh = () => {
    queue.refresh()
    outbox.refresh()
  }

  const q = queue.data
  const pendingRequesters = q?.requesters.filter((r) => r.status === "pending") ?? []
  const knownRequesters = q?.requesters.filter((r) => r.status !== "pending") ?? []
  const names = new Map(q?.requesters.map((r) => [r.fingerprint, r.name]) ?? [])
  const waiting = q ? pendingRequesters.length + q.requests.filter((r) => r.status === "pending").length + q.tasks.length : 0

  return (
    <Page
      title="Queue"
      description="Nothing leaves this laptop until you say so: new requesters, disclosure requests and planned tasks."
      actions={q && <Badge variant={waiting ? "default" : "secondary"}>{waiting} waiting</Badge>}
    >
      {queue.error && !q && <p className="text-sm text-destructive">{queue.error.message}</p>}
      {q && <WalletStrip wallet={q.wallet} />}

      <Section icon={<KeyRound className="size-4" />} title="New requesters" hint="Only paired keys ever get answers.">
        {pendingRequesters.length === 0 && <Empty>No one new is asking.</Empty>}
        {pendingRequesters.map((r) => (
          <PairingCard key={r.fingerprint} requester={r} busy={busy} onDecide={(approve) =>
            run(`p:${r.fingerprint}`, () => api.decideRequester(r.fingerprint, approve),
              (v) => (v.status === "paired" ? `Paired with ${v.name}` : `Blocked ${v.name}`), refresh)} />
        ))}
        {knownRequesters.length > 0 && (
          <div className="flex flex-wrap gap-2 pt-1">
            {knownRequesters.map((r) => (
              <Badge key={r.fingerprint} variant="outline" className={cn("gap-1", r.status === "blocked" && "text-destructive")}>
                {r.status === "paired" ? <UserCheck /> : <UserX />} {r.name}
                <span className="font-mono opacity-60">{r.fingerprint.slice(0, 8)}</span>
              </Badge>
            ))}
          </div>
        )}
      </Section>

      <Section icon={<ShieldQuestion className="size-4" />} title="Disclosure requests"
        hint="You see KAVACH's proposal; code, not the model, decided it.">
        {(q?.requests.length ?? 0) === 0 && <Empty>No open requests.</Empty>}
        {q?.requests.map((r) => (
          <RequestCard key={r.request_id} request={r} name={r.requester_name ?? names.get(r.requester_fp)} busy={busy}
            onAction={(action) => run(`r:${r.request_id}`, () => api.decideRequest(r.request_id, action),
              (v) => `${ACTION_LABEL[action]}: ${ANSWER[(v.answer_type ?? "DECLINED") as AnswerType].label}`, refresh)} />
        ))}
      </Section>

      <Section icon={<ListChecks className="size-4" />} title="Planned tasks" hint="Tools run only after you approve, and only locally.">
        <NewTask onCreated={refresh} />
        {(q?.tasks.length ?? 0) === 0 && <Empty>No planned tasks.</Empty>}
        {q?.tasks.map((t) => (
          <TaskCard key={t.task_id} task={t} busy={busy} onDecide={(approve) =>
            run(`t:${t.task_id}`, () => api.decideTask(t.task_id, approve), (v) => taskToast(v), refresh)} />
        ))}
        <OutboxList items={outbox.data ?? []} />
      </Section>
    </Page>
  )
}

function taskToast(t: Task): string {
  if (t.status === "rejected") return "Task rejected; nothing ran"
  const ok = t.result?.filter((r) => r.ok).length ?? 0
  return `${t.status === "done" ? "Done" : "Finished with problems"}: ${ok}/${t.result?.length ?? 0} steps written to the outbox`
}

function Section({ icon, title, hint, children }: { icon: ReactNode; title: string; hint: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-baseline gap-2">
        <h2 className="flex items-center gap-2 text-sm font-semibold">{icon} {title}</h2>
        <span className="text-xs text-muted-foreground">{hint}</span>
      </div>
      {children}
    </section>
  )
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-lg border border-dashed px-4 py-3 text-xs text-muted-foreground">{children}</p>
}

function WalletStrip({ wallet: w }: { wallet: WalletStatus }) {
  const wallet = { by_type: w.by_type, low: w.low ?? [] }
  return (
    <div className={cn("flex flex-wrap items-center gap-3 rounded-lg border px-4 py-2.5 text-xs", wallet.low.length ? TONE.warn : "bg-card")}>
      {wallet.low.length ? <AlertTriangle className="size-4" /> : <Wallet className="size-4 text-muted-foreground" />}
      <span className="font-medium">Credential copies</span>
      {wallet.by_type.length === 0 && <span className="text-muted-foreground">none imported yet (run scripts/reset_demo.py)</span>}
      {wallet.by_type.map((t) => (
        <span key={t.credential_type} className={cn(wallet.low.includes(t.credential_type) && "font-semibold")}>
          {t.credential_type} <span className="opacity-70">({t.iss})</span>: {t.unused}/{t.total}
        </span>
      ))}
      {wallet.low.length > 0 && <span className="ml-auto">Running low: ask the issuer for a new batch.</span>}
    </div>
  )
}

function PairingCard({ requester, busy, onDecide }: { requester: Requester; busy: string | null; onDecide: (approve: boolean) => void }) {
  const key = `p:${requester.fingerprint}`
  return (
    <Card className="gap-2 border-primary/40 py-4">
      <CardHeader className="px-4">
        <CardTitle className="text-sm">{requester.name} wants to ask you questions</CardTitle>
        <CardDescription className="text-xs">
          {requester.type} · key <span className="font-mono">{requester.fingerprint}</span>. Check the fingerprint with them
          before pairing.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex gap-2 px-4">
        <Button size="sm" disabled={busy === key} onClick={() => onDecide(true)}>
          {busy === key ? <Loader2 className="animate-spin" /> : <UserCheck />} Pair
        </Button>
        <Button size="sm" variant="outline" disabled={busy === key} onClick={() => onDecide(false)}>
          <UserX /> Block
        </Button>
      </CardContent>
    </Card>
  )
}

function RequestCard({ request, name, busy, onAction }: {
  request: RequestView
  name: string | null | undefined
  busy: string | null
  onAction: (a: RequestAction) => void
}) {
  const p = request.proposal
  const key = `r:${request.request_id}`
  const meta = p ? ANSWER[p.answer_type as AnswerType] : null
  return (
    <Card className="gap-3 py-4">
      <CardHeader className="px-4">
        <div className="flex items-start justify-between gap-3">
          <CardTitle className="text-sm leading-snug">“{request.question}”</CardTitle>
          <Badge variant="outline" className="gap-1">
            {request.channel === "mcp" ? <Bot /> : <Globe />} {request.channel === "mcp" ? "AI agent · MCP" : "web"}
          </Badge>
        </div>
        <CardDescription className="text-xs">
          From {name ?? "unknown"} · <span className="font-mono">{request.requester_fp.slice(0, 8)}</span> · {when(request.created_at)}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 px-4">
        {request.status === "pending_pairing" || !p ? (
          <p className="text-xs text-muted-foreground">Waiting until you pair or block this requester.</p>
        ) : (
          <>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
              <dt className="text-muted-foreground">Asks about</dt>
              <dd>{claimText(request.claim)}</dd>
              <dt className="text-muted-foreground">Proposed answer</dt>
              <dd className="flex flex-wrap items-center gap-2">
                <strong>{resultText(p.result)}</strong>
                {p.favourable === false && <Badge variant="outline" className={TONE.warn}>unfavourable to you</Badge>}
              </dd>
              <dt className="text-muted-foreground">Trust level</dt>
              <dd>
                {meta && (
                  <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs", TONE[meta.tone])}>
                    {p.answer_type === "ISSUER_PROOF" ? <ShieldCheck className="size-3" /> : null} {meta.label}
                  </span>
                )}
                <span className="ml-2 text-xs text-muted-foreground">{meta?.blurb}</span>
              </dd>
              <dt className="text-muted-foreground">Why</dt>
              <dd className="text-xs text-muted-foreground">{p.reason}</dd>
            </dl>
            <div className="flex gap-2">
              {(p.actions ?? []).map((a) => (
                <Button key={a} size="sm" variant={a === "approve" || a === "answer" ? "default" : "outline"}
                  disabled={busy === key} onClick={() => onAction(a)}>
                  {busy === key && (a === "approve" || a === "answer") ? <Loader2 className="animate-spin" /> :
                    a === "approve" || a === "answer" ? <Check /> : <X />}
                  {ACTION_LABEL[a]}
                </Button>
              ))}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function NewTask({ onCreated }: { onCreated: () => void }) {
  const [text, setText] = useState("")
  const [busy, setBusy] = useState(false)
  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!text.trim() || busy) return
    setBusy(true)
    try {
      const t = await api.createTask(text.trim())
      toast.success(`Planned ${t.plan.calls.length} step(s). Review before approving.`)
      setText("")
      onCreated()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <form onSubmit={submit} className="flex gap-2">
      <Input value={text} onChange={(e) => setText(e.target.value)} maxLength={500}
        placeholder="Ask KAVACH to do something, e.g. “Reply to my landlord with the proof and remind me before the agreement ends”" />
      <Button type="submit" variant="secondary" disabled={busy || !text.trim()}>
        {busy ? <Loader2 className="animate-spin" /> : <ListChecks />} Plan
      </Button>
    </form>
  )
}

function TaskCard({ task, busy, onDecide }: { task: Task; busy: string | null; onDecide: (approve: boolean) => void }) {
  const key = `t:${task.task_id}`
  return (
    <Card className="gap-3 py-4">
      <CardHeader className="px-4">
        <CardTitle className="text-sm leading-snug">{task.instruction}</CardTitle>
        <CardDescription className="text-xs">{task.plan.calls.length} step(s) · planned {when(task.created_at)}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 px-4">
        <ol className="flex flex-col gap-2">
          {task.plan.calls.map((c, i) => {
            const Icon = TOOL_ICON[c.tool]
            return (
              <li key={i} className="flex gap-2 rounded-md border bg-muted/40 px-3 py-2 text-sm">
                <Icon className="mt-0.5 size-4 shrink-0 text-primary" />
                <span className="min-w-0">
                  <span className="font-mono text-xs text-muted-foreground">{c.tool}</span>
                  <span className="block whitespace-pre-wrap">{c.preview}</span>
                </span>
              </li>
            )
          })}
        </ol>
        {task.plan.warnings && task.plan.warnings.length > 0 && (
          <ul className={cn("rounded-md border px-3 py-2 text-xs", TONE.warn)}>
            {task.plan.warnings.map((w) => <li key={w}>{w}</li>)}
          </ul>
        )}
        <div className="flex gap-2">
          <Button size="sm" disabled={busy === key} onClick={() => onDecide(true)}>
            {busy === key ? <Loader2 className="animate-spin" /> : <Check />} Approve and run
          </Button>
          <Button size="sm" variant="outline" disabled={busy === key} onClick={() => onDecide(false)}>
            <X /> Reject
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function OutboxList({ items }: { items: OutboxItem[] }) {
  if (items.length === 0) return null
  return (
    <div className="rounded-lg border px-4 py-3">
      <p className="mb-2 text-xs font-medium">Outbox (local files, nothing is sent)</p>
      <ul className="grid gap-1 text-xs">
        {items.slice(0, 8).map((i) => (
          <li key={i.name} className="flex gap-3">
            <Badge variant="secondary" className="w-10 justify-center uppercase">{i.kind}</Badge>
            <span className="truncate font-mono">{i.name}</span>
            <span className="ml-auto shrink-0 text-muted-foreground">{bytes(i.size)} · {when(i.created_at)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
