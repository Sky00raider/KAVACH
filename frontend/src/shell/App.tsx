import { Navigate, Route, Routes } from "react-router"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import Ask from "@/pages/brain/Ask"
import Memory from "@/pages/brain/Memory"
import Vault from "@/pages/brain/Vault"
import Audit from "@/pages/trust/Audit"
import Queue from "@/pages/trust/Queue"
import Verify from "@/pages/trust/Verify"
import { Layout } from "./Layout"
import { ModeProvider, useMode } from "./mode"
import { HOME } from "./nav"

/** CONTRACT §14: owner routes and the requester route never mount together. */
function AppRoutes() {
  const { mode } = useMode()
  return (
    <Routes>
      <Route element={<Layout />}>
        {mode === "owner" ? (
          <>
            <Route index element={<Ask />} />
            <Route path="vault" element={<Vault />} />
            <Route path="memory" element={<Memory />} />
            <Route path="queue" element={<Queue />} />
            <Route path="audit" element={<Audit />} />
          </>
        ) : (
          <Route path="verify" element={<Verify />} />
        )}
        <Route path="*" element={<Navigate to={HOME[mode]} replace />} />
      </Route>
    </Routes>
  )
}

export function App() {
  return (
    <ModeProvider>
      <TooltipProvider delayDuration={200}>
        <AppRoutes />
        <Toaster position="bottom-right" />
      </TooltipProvider>
    </ModeProvider>
  )
}
