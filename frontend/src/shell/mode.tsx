import { createContext, useContext, useState, type ReactNode } from "react"
import { injectedMode, modeSwitchAllowed, type Mode } from "@/api/boot"

const STORAGE_KEY = "kavach.devMode"

interface ModeState {
  mode: Mode
  /** false when the serving backend fixed the mode (production); then setMode is a no-op. */
  switchable: boolean
  setMode: (mode: Mode) => void
}

const ModeContext = createContext<ModeState | null>(null)

function initialMode(): Mode {
  if (modeSwitchAllowed()) {
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (saved === "owner" || saved === "requester") return saved
    } catch {
      // storage blocked: fall through
    }
  }
  return injectedMode() ?? "owner"
}

export function ModeProvider({ children }: { children: ReactNode }) {
  const switchable = modeSwitchAllowed()
  const [mode, setModeState] = useState<Mode>(initialMode)
  const setMode = (next: Mode) => {
    if (!switchable) return
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // storage blocked: switch for this session only
    }
    setModeState(next)
  }
  return <ModeContext.Provider value={{ mode, switchable, setMode }}>{children}</ModeContext.Provider>
}

export function useMode(): ModeState {
  const ctx = useContext(ModeContext)
  if (!ctx) throw new Error("useMode outside ModeProvider")
  return ctx
}
