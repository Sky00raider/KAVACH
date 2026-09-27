import { Fragment, useMemo, useState } from "react"
import { Link2, Link2Off, Search } from "lucide-react"
import { api, type AuditEntry } from "@/api/client"
import { usePoll } from "@/api/poll"
import { Page } from "@/components/shared/Page"
import { Input } from "@/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { cn } from "@/components/lib/utils"
import { TONE } from "./format"

const AUDIT_MS = 3000

type Group = "all" | "requests" | "disclosures" | "tasks" | "knowledge"
const GROUPS: Record<Group, (e: string) => boolean> = {
  all: () => true,
  requests: (e) => e.startsWith("request") || e.startsWith("requester"),
  disclosures: (e) => e.startsWith("disclosure") || e === "wallet_low",
  tasks: (e) => e.startsWith("task"),
  knowledge: (e) => e.startsWith("ingested") || e.startsWith("document") || e.startsWith("memory"),
}

function tone(event: string): string {
  if (/rejected|refused|signature_failed|blocked|denied|task_failed/.test(event)) return TONE.bad
  if (/answered|paired|executed|approved/.test(event)) return TONE.good
  if (/declined|cannot_confirm|wallet_low|pending/.test(event)) return TONE.warn
  return TONE.muted
}

export default function Audit() {
  const { data, error } = usePoll(() => api.audit(500), AUDIT_MS)
  const [group, setGroup] = useState<Group>("all")
  const [query, setQuery] = useState("")
  const [open, setOpen] = useState<number | null>(null)

  const entries = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (data?.entries ?? []).filter((e) => GROUPS[group](e.event) &&
      (!q || `${e.event} ${e.ref_id ?? ""} ${JSON.stringify(e.detail)}`.toLowerCase().includes(q)))
  }, [data, group, query])

  return (
    <Page
      title="Audit"
      description="Every request, decision and action, including refusals. Each entry hashes the one before it."
      actions={data && <ChainBadge intact={data.chain_intact} brokenAt={data.broken_at ?? null} count={data.entries.length} />}
    >
      {error && !data && <p className="text-sm text-destructive">{error.message}</p>}
      <div className="flex flex-wrap items-center gap-2">
        {(Object.keys(GROUPS) as Group[]).map((g) => (
          <button key={g} type="button" onClick={() => setGroup(g)}
            className={cn("rounded-full border px-3 py-1 text-xs capitalize", g === group ? "border-primary bg-primary/15 text-primary" : "text-muted-foreground hover:bg-accent")}>
            {g}
          </button>
        ))}
        <div className="relative ml-auto w-64">
          <Search className="absolute top-2.5 left-2.5 size-3.5 text-muted-foreground" />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter events, ids, details" className="h-8 pl-8 text-xs" />
        </div>
      </div>
      <div className="overflow-hidden rounded-xl border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-14">#</TableHead>
              <TableHead className="w-40">Time (UTC)</TableHead>
              <TableHead className="w-52">Event</TableHead>
              <TableHead>Reference and detail</TableHead>
              <TableHead className="w-28 text-right">Hash</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {entries.length === 0 && (
              <TableRow>
                <TableCell colSpan={5} className="py-10 text-center text-xs text-muted-foreground">
                  {data ? "No entries match." : "Loading…"}
                </TableCell>
              </TableRow>
            )}
            {entries.map((e) => (
              <Fragment key={e.seq}>
                <TableRow onClick={() => setOpen(open === e.seq ? null : e.seq)}
                  className={cn("cursor-pointer", data?.broken_at === e.seq && "bg-destructive/10")}>
                  <TableCell className="font-mono text-xs text-muted-foreground">{e.seq}</TableCell>
                  <TableCell className="font-mono text-xs">{e.ts.replace("T", " ").replace("Z", "")}</TableCell>
                  <TableCell>
                    <span className={cn("rounded-full border px-2 py-0.5 text-xs", tone(e.event))}>{e.event}</span>
                  </TableCell>
                  <TableCell className="max-w-0 truncate font-mono text-xs text-muted-foreground">
                    {e.ref_id && <span className="mr-2 text-foreground">{e.ref_id}</span>}
                    {summary(e)}
                  </TableCell>
                  <TableCell className="text-right font-mono text-xs text-muted-foreground">{e.entry_hash.slice(0, 10)}</TableCell>
                </TableRow>
                {open === e.seq && (
                  <TableRow className="bg-muted/30 hover:bg-muted/30">
                    <TableCell colSpan={5}>
                      <pre className="overflow-x-auto whitespace-pre-wrap break-all font-mono text-[11px] leading-relaxed">
                        {JSON.stringify(e.detail, null, 2)}
                      </pre>
                      <p className="mt-2 font-mono text-[10px] text-muted-foreground">
                        prev {e.prev_hash}
                        <br />
                        this {e.entry_hash}
                      </p>
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            ))}
          </TableBody>
        </Table>
      </div>
    </Page>
  )
}

function summary(e: AuditEntry): string {
  return Object.entries(e.detail)
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join("  ")
}

function ChainBadge({ intact, brokenAt, count }: { intact: boolean; brokenAt: number | null; count: number }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium", intact ? TONE.good : TONE.bad)}>
      {intact ? <Link2 className="size-3.5" /> : <Link2Off className="size-3.5" />}
      {intact ? `Chain intact ✓ · ${count} shown` : `Chain broken at #${brokenAt}`}
    </span>
  )
}
