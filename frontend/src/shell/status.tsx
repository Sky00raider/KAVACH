import { api, probeOwner, requesterApi } from "@/api/client"
import { usePoll } from "@/api/poll"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { cn } from "@/components/lib/utils"

const HEALTH_MS = 10_000
const OWNER_PROBE_MS = 5_000

type Level = "ok" | "warn" | "down" | "unknown"

const DOT: Record<Level, string> = {
  ok: "bg-success",
  warn: "bg-warning",
  down: "bg-destructive",
  unknown: "bg-muted-foreground/50",
}

function StatusRow({ level, title, subtitle, children }: {
  level: Level
  title: string
  subtitle?: string
  children: React.ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className="flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-xs hover:bg-sidebar-accent"
        >
          <span className={cn("size-2 shrink-0 rounded-full", DOT[level], level === "ok" && "shadow-[0_0_6px] shadow-success/60")} />
          <span className="min-w-0 flex-1">
            <span className="block truncate font-medium text-sidebar-foreground">{title}</span>
            {subtitle && <span className="block truncate text-muted-foreground">{subtitle}</span>}
          </span>
        </button>
      </TooltipTrigger>
      <TooltipContent side="right" className="max-w-72">
        {children}
      </TooltipContent>
    </Tooltip>
  )
}

/** Owner mode: Ollama, per-role models and the DB, from /api/health. */
export function OwnerHealth() {
  const { data, error } = usePoll(api.health, HEALTH_MS)
  let level: Level = "unknown"
  let subtitle = "checking…"
  if (error) {
    level = "down"
    subtitle = "owner API unreachable"
  } else if (data) {
    const cold = Object.entries(data.model_loaded).filter(([, loaded]) => !loaded).map(([role]) => role)
    if (!data.ollama || !data.db) {
      level = "down"
      subtitle = !data.ollama ? "Ollama not running" : "database unavailable"
    } else if (cold.length) {
      level = "warn"
      subtitle = `cold: ${cold.join(", ")}`
    } else {
      level = "ok"
      subtitle = "all models loaded"
    }
  }
  return (
    <StatusRow level={level} title="Local AI" subtitle={subtitle}>
      {error ? (
        <p>{error.message}</p>
      ) : data ? (
        <dl className="grid grid-cols-[auto_1fr_auto] gap-x-3 gap-y-1">
          {Object.entries(data.models).map(([role, name]) => (
            <div key={role} className="contents">
              <dt className="opacity-70">{role}</dt>
              <dd className="font-mono">{name}</dd>
              <dd>{data.model_loaded[role] ? "loaded" : "cold"}</dd>
            </div>
          ))}
          <dt className="opacity-70">ollama</dt>
          <dd className="col-span-2">{data.ollama ? "running" : "down"}</dd>
          <dt className="opacity-70">db</dt>
          <dd className="col-span-2">{data.db ? "ok" : "unavailable"}</dd>
          <dt className="opacity-70">vault</dt>
          <dd className="col-span-2 font-mono">{data.vault_dir}</dd>
        </dl>
      ) : (
        <p>Checking /api/health…</p>
      )}
    </StatusRow>
  )
}

/** Requester mode: which owner this laptop talks to, and whether it answers. */
export function OwnerConnection() {
  const identity = usePoll(requesterApi.identity, null)
  const ownerUrl = identity.data?.owner_url
  const reachable = usePoll(() => (ownerUrl ? probeOwner(ownerUrl) : Promise.resolve(null)), OWNER_PROBE_MS, [ownerUrl])
  let level: Level = "unknown"
  let subtitle = "checking…"
  if (identity.error) {
    level = "down"
    subtitle = "requester backend unreachable"
  } else if (ownerUrl && reachable.data !== undefined && reachable.data !== null) {
    level = reachable.data ? "ok" : "down"
    subtitle = reachable.data ? "reachable" : "unreachable"
  }
  const host = ownerUrl ? ownerUrl.replace(/^https?:\/\//, "") : "owner"
  return (
    <StatusRow level={level} title={`Owner ${host}`} subtitle={subtitle}>
      {identity.error ? (
        <p>{identity.error.message}</p>
      ) : identity.data ? (
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
          <dt className="opacity-70">owner</dt>
          <dd className="font-mono">{identity.data.owner_url}</dd>
          <dt className="opacity-70">status</dt>
          <dd>{subtitle}</dd>
          <dt className="opacity-70">you</dt>
          <dd>
            {identity.data.name} ({identity.data.type})
          </dd>
          <dt className="opacity-70">key</dt>
          <dd className="font-mono">{identity.data.fingerprint}</dd>
        </dl>
      ) : (
        <p>Loading /r/identity…</p>
      )}
    </StatusRow>
  )
}
