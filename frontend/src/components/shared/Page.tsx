import type { ReactNode } from "react"
import { Construction } from "lucide-react"
import { cn } from "@/components/lib/utils"

/** Standard page frame: title row plus content, used by every page. */
export function Page({ title, description, actions, className, children }: {
  title: string
  description?: string
  actions?: ReactNode
  /** Extra classes for the frame, e.g. a full-width, full-height page. */
  className?: string
  children?: ReactNode
}) {
  return (
    <div className={cn("mx-auto flex w-full max-w-6xl flex-col gap-6 px-8 py-8", className)}>
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
          {description && <p className="mt-1 text-sm text-muted-foreground">{description}</p>}
        </div>
        {actions}
      </header>
      {children}
    </div>
  )
}

/** Placeholder for a page its track has not built yet. */
export function NotBuilt({ track, step }: { track: "BRAIN" | "TRUST"; step: string }) {
  return (
    <div className="grid place-items-center rounded-xl border border-dashed py-20 text-center">
      <Construction className="mb-3 size-6 text-muted-foreground" />
      <p className="text-sm font-medium">Not built yet</p>
      <p className="mt-1 text-sm text-muted-foreground">
        {track} track, {step}
      </p>
    </div>
  )
}
