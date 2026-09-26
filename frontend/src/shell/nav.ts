import { Brain, FolderLock, Inbox, MessageSquare, ScrollText, ShieldCheck, type LucideIcon } from "lucide-react"
import type { Mode } from "@/api/boot"

export interface NavItem {
  to: string
  label: string
  icon: LucideIcon
}

export interface NavGroup {
  label: string
  items: NavItem[]
}

/** CONTRACT §14 routes, grouped by the track that owns each page. */
export const NAV: Record<Mode, NavGroup[]> = {
  owner: [
    {
      label: "Brain",
      items: [
        { to: "/", label: "Ask", icon: MessageSquare },
        { to: "/vault", label: "Vault", icon: FolderLock },
        { to: "/memory", label: "Memory", icon: Brain },
      ],
    },
    {
      label: "Trust",
      items: [
        { to: "/queue", label: "Queue", icon: Inbox },
        { to: "/audit", label: "Audit", icon: ScrollText },
      ],
    },
  ],
  requester: [{ label: "Requester", items: [{ to: "/verify", label: "Verify", icon: ShieldCheck }] }],
}

export const HOME: Record<Mode, string> = { owner: "/", requester: "/verify" }
