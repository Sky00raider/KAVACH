import { NavLink, Outlet, useNavigate } from "react-router"
import { FlaskConical, ShieldHalf } from "lucide-react"
import { fixtureMode, type Mode } from "@/api/boot"
import { Separator } from "@/components/ui/separator"
import { cn } from "@/components/lib/utils"
import { useMode } from "./mode"
import { HOME, NAV } from "./nav"
import { OwnerConnection, OwnerHealth } from "./status"

function ModeSwitch() {
  const { mode, setMode } = useMode()
  const navigate = useNavigate()
  const pick = (next: Mode) => {
    setMode(next)
    navigate(HOME[next])
  }
  return (
    <div className="px-2">
      <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">Dev: view as</p>
      <div className="grid grid-cols-2 gap-1 rounded-md bg-background/60 p-1" role="radiogroup" aria-label="Mode">
        {(["owner", "requester"] as const).map((m) => (
          <button
            key={m}
            type="button"
            role="radio"
            aria-checked={mode === m}
            onClick={() => pick(m)}
            className={cn(
              "rounded px-2 py-1 text-xs capitalize transition-colors",
              mode === m ? "bg-sidebar-accent text-foreground" : "text-muted-foreground hover:text-foreground",
            )}
          >
            {m}
          </button>
        ))}
      </div>
    </div>
  )
}

function Sidebar() {
  const { mode, switchable } = useMode()
  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground">
      <div className="flex items-center gap-2.5 px-5 py-5">
        <div className="grid size-8 place-items-center rounded-lg bg-primary/15 text-primary">
          <ShieldHalf className="size-4.5" />
        </div>
        <div className="leading-tight">
          <p className="text-sm font-semibold tracking-wide">KAVACH</p>
          <p className="text-xs text-muted-foreground">{mode === "owner" ? "Your second brain" : "Verifier"}</p>
        </div>
      </div>

      <nav className="flex-1 space-y-5 px-3 py-2">
        {NAV[mode].map((group) => (
          <div key={group.label}>
            <p className="mb-1 px-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              {group.label}
            </p>
            <ul className="space-y-0.5">
              {group.items.map(({ to, label, icon: Icon }) => (
                <li key={to}>
                  <NavLink
                    to={to}
                    end
                    className={({ isActive }) =>
                      cn(
                        "flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm transition-colors",
                        isActive
                          ? "bg-sidebar-accent font-medium text-foreground"
                          : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
                      )
                    }
                  >
                    <Icon className="size-4" />
                    {label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>

      <div className="space-y-3 px-3 pb-4">
        {fixtureMode() && (
          <div className="flex items-center gap-2 rounded-md bg-warning/10 px-2 py-1.5 text-xs text-warning">
            <FlaskConical className="size-3.5" />
            Fixture data, no backend
          </div>
        )}
        {switchable && <ModeSwitch />}
        <Separator />
        {mode === "owner" ? <OwnerHealth /> : <OwnerConnection />}
      </div>
    </aside>
  )
}

export function Layout() {
  return (
    <div className="flex h-dvh overflow-hidden">
      <Sidebar />
      <main className="min-w-0 flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  )
}
