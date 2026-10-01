import { createContext, useContext, useEffect, useState, type ReactNode } from "react"

export type Theme = "dark" | "light"

// index.html reads the same key before first paint, so a light page never flashes dark.
const STORAGE_KEY = "kavach.theme"

interface ThemeState {
  theme: Theme
  setTheme: (theme: Theme) => void
  toggle: () => void
}

const ThemeContext = createContext<ThemeState | null>(null)

function initialTheme(): Theme {
  try {
    if (localStorage.getItem(STORAGE_KEY) === "light") return "light"
  } catch {
    // storage blocked: fall through
  }
  return "dark"
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(initialTheme)
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark")
  }, [theme])
  const setTheme = (next: Theme) => {
    try {
      localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // storage blocked: switch for this session only
    }
    setThemeState(next)
  }
  const toggle = () => setTheme(theme === "dark" ? "light" : "dark")
  return <ThemeContext.Provider value={{ theme, setTheme, toggle }}>{children}</ThemeContext.Provider>
}

export function useTheme(): ThemeState {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error("useTheme outside ThemeProvider")
  return ctx
}
