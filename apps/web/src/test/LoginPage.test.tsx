import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../auth/AuthProvider";
import { LoginPage } from "../pages/LoginPage";

const authMocks = vi.hoisted(() => ({
  signInWithPassword: vi.fn(),
  signUp: vi.fn(),
}));

vi.mock("../lib/supabase", () => ({
  supabase: {
    auth: {
      getSession: vi.fn(() => Promise.resolve({ data: { session: null } })),
      onAuthStateChange: vi.fn(() => ({
        data: { subscription: { unsubscribe: vi.fn() } },
      })),
      signInWithPassword: authMocks.signInWithPassword,
      signUp: authMocks.signUp,
      signOut: vi.fn(),
    },
  },
}));

function renderLoginPage() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <AuthProvider>
        <LoginPage />
      </AuthProvider>
    </MemoryRouter>,
  );
}

async function submitCredentials(password = "geheim123") {
  fireEvent.change(await screen.findByLabelText("E-Mail"), {
    target: { value: "test@example.com" },
  });
  fireEvent.change(screen.getByLabelText("Passwort"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: "Anmelden" }));
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("LoginPage", () => {
  it("meldet ein falsches Passwort als solches zurueck", async () => {
    authMocks.signInWithPassword.mockResolvedValue({
      data: { session: null, user: null },
      error: {
        name: "AuthApiError",
        message: "Invalid login credentials",
        status: 400,
        code: "invalid_credentials",
      },
    });

    renderLoginPage();
    await submitCredentials();

    expect(await screen.findByRole("alert")).toHaveTextContent("E-Mail oder Passwort ist falsch.");
  });

  it("uebersetzt einen nicht erreichbaren Anmeldedienst statt die Browser-Meldung zu zeigen", async () => {
    authMocks.signInWithPassword.mockResolvedValue({
      data: { session: null, user: null },
      error: {
        name: "AuthRetryableFetchError",
        message: "NetworkError when attempting to fetch resource.",
        status: 0,
      },
    });

    renderLoginPage();
    await submitCredentials();

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/nicht erreichbar/);
    expect(alert).not.toHaveTextContent(/NetworkError/);
  });

  it("faengt eine geworfene Ausnahme ab und laesst den Knopf wieder frei", async () => {
    authMocks.signInWithPassword.mockRejectedValue(new TypeError("Failed to fetch"));

    renderLoginPage();
    await submitCredentials();

    expect(await screen.findByRole("alert")).toHaveTextContent(/nicht erreichbar/);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Anmelden" })).not.toBeDisabled();
    });
  });

  it("schaltet die Sichtbarkeit des Passworts um", async () => {
    renderLoginPage();

    const passwordInput = await screen.findByLabelText("Passwort");
    expect(passwordInput).toHaveAttribute("type", "password");

    fireEvent.click(screen.getByRole("button", { name: "Anzeigen" }));
    expect(passwordInput).toHaveAttribute("type", "text");

    fireEvent.click(screen.getByRole("button", { name: "Verbergen" }));
    expect(passwordInput).toHaveAttribute("type", "password");
  });
});
