import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { MutationCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { HouseholdProvider } from "./household/HouseholdProvider";
import { applyLayoutTokens } from "./styles/theme";
import "./styles/global.css";

// Die Liquiditaetsvorschau leitet sich aus Buchungen, Konten, Vertraegen,
// Budgets, Ruecklagen und Zielen zugleich ab. Ihren Cache in jeder einzelnen
// Mutation zu invalidieren waere zwangslaeufig unvollstaendig - vergessene
// Stellen zeigen dann stumm veraltete Zahlen, und das ist bei einer
// Warnfunktion schlimmer als ein Nachladen. Deshalb genau eine zentrale
// Regel: jede erfolgreiche Mutation verwirft die Vorschau
// (docs/EINNAHMEN-VORSCHAU-SZENARIEN-PLAN.md, Abschnitt 14, Pflichtschritt 2).
const queryClient = new QueryClient({
  mutationCache: new MutationCache({
    onSuccess: (_data, _variables, _context, mutation) => {
      // Die Simulation ist selbst eine Mutation und aendert nichts am
      // Bestand - sie darf die Basisvorschau nicht bei jeder Eingabe neu
      // laden lassen.
      if (mutation.options.mutationKey?.[0] === "forecast") return;
      void queryClient.invalidateQueries({ queryKey: ["forecast"] });
    },
  }),
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

// Farben setzt das Design (index.html, global.css); Schriften, Abstaende und
// Radien sind fuer alle Designs gleich und stehen einmal vor dem ersten Rendern.
applyLayoutTokens(document.documentElement);

const container = document.getElementById("root");
if (!container) {
  throw new Error("Root-Element '#root' wurde nicht gefunden.");
}

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <HouseholdProvider>
            <App />
          </HouseholdProvider>
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
