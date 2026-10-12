import { type ComponentType } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { OverviewPage } from "./pages/OverviewPage";
import { StatusPage } from "./pages/StatusPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { LoginPage } from "./pages/LoginPage";
import { AccountsPage } from "./pages/AccountsPage";
import { TransactionsPage } from "./pages/TransactionsPage";
import { ExplorerPage } from "./pages/ExplorerPage";
import { SettingsPage } from "./pages/SettingsPage";
import { ImportPage } from "./pages/ImportPage";
import { ReviewInboxPage } from "./pages/ReviewInboxPage";
import { RulesPage } from "./pages/RulesPage";
import { CategoriesPage } from "./pages/CategoriesPage";
import { ContractsPage } from "./pages/ContractsPage";
import { BudgetPage } from "./pages/BudgetPage";
import { ForecastPage } from "./pages/ForecastPage";
import { ReportsPage } from "./pages/ReportsPage";
import { InsightsPage } from "./pages/InsightsPage";
import { GoalAssignmentInboxPage } from "./pages/GoalAssignmentInboxPage";
import { SavingsPlanningPage } from "./pages/SavingsPlanningPage";
import { AssetsPage, DebtsPage } from "./pages/AssetsPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { ProjectDetailPage } from "./pages/ProjectDetailPage";
import { FoerderungenPage } from "./pages/FoerderungenPage";
import { LebensmittelPage } from "./pages/LebensmittelPage";
import { ReimbursementsRoutePage } from "./pages/ReimbursementsRoutePage";
import { InsuranceRoutePage } from "./pages/InsuranceRoutePage";
import { DatenbankPage } from "./pages/DatenbankPage";
import { JoinHouseholdPage } from "./pages/JoinHouseholdPage";
import { AssistantPage } from "./pages/AssistantPage";
import { RequireAuth } from "./auth/RequireAuth";
import { RequireHousehold } from "./household/RequireHousehold";
import { navItems } from "./nav";

const PAGE_COMPONENTS: Record<string, ComponentType> = {
  "/konten": AccountsPage,
  "/buchungen": TransactionsPage,
  "/explorer": ExplorerPage,
  "/einstellungen": SettingsPage,
  "/importe": ImportPage,
  "/review": ReviewInboxPage,
  "/review-inbox": ReviewInboxPage,
  "/regeln": RulesPage,
  "/kategorien": CategoriesPage,
  "/fixkosten": ContractsPage,
  "/versicherungen": InsuranceRoutePage,
  "/budget": BudgetPage,
  "/vorschau": ForecastPage,
  "/berichte": ReportsPage,
  "/empfehlungen": InsightsPage,
  "/sparen": SavingsPlanningPage,
  "/vorhaben": ProjectsPage,
  "/foerderungen": FoerderungenPage,
  "/vermoegen": AssetsPage,
  "/schulden": DebtsPage,
  "/lebensmittel": LebensmittelPage,
  "/erstattungen": ReimbursementsRoutePage,
  "/datenbank": DatenbankPage,
  "/assistent": AssistantPage,
};

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />

      <Route element={<Layout />}>
        <Route path="status" element={<StatusPage />} />

        <Route element={<RequireAuth />}>
          <Route path="beitreten" element={<JoinHouseholdPage />} />
          <Route element={<RequireHousehold />}>
            <Route index element={<OverviewPage />} />
            <Route path="sparziele" element={<Navigate to="/sparen?tab=goals" replace />} />
            <Route path="wunschliste" element={<Navigate to="/sparen?tab=wishes" replace />} />
            <Route path="sparziele/zuordnen" element={<GoalAssignmentInboxPage />} />
            <Route path="vorhaben/:id" element={<ProjectDetailPage />} />
            {navItems
              .filter((item) => item.path !== "/")
              .map((item) => {
                const PageComponent = PAGE_COMPONENTS[item.path];
                return (
                  <Route
                    key={item.path}
                    path={item.path.slice(1)}
                    element={
                      PageComponent ? (
                        <PageComponent />
                      ) : (
                        <PlaceholderPage title={item.label} phase={item.phase} />
                      )
                    }
                  />
                );
              })}
          </Route>
        </Route>
      </Route>
    </Routes>
  );
}
