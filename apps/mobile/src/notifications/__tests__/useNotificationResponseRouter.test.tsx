/// <reference types="jest" />
import { act, create } from "react-test-renderer";
import { useNotificationResponseRouter } from "../useNotificationResponseRouter";

// Ein angetippter Push, der auf keinen Zweig trifft, ist eine tote
// Benachrichtigung: der Worker versendet sie, der Nutzer tippt sie an, und
// nichts geschieht. Genau das war mit dem P3-Typ
// `insurance_special_termination` der Fall - er stand in
// services/worker/worker/notifications/insurance_alerts.py, aber in keiner
// Typliste hier. Dieser Test haelt alle vier Versicherungstypen fest - der
// vierte, `insurance_claim_followup`, kam mit P5 dazu.
//
// Die Namen tragen das Praefix `mock`, weil jest.mock() seine Fabrik vor die
// Modulinitialisierung hebt und nur so benannte Variablen darin zulaesst.

let mockListener: ((response: unknown) => void) | null = null;
const mockRemove = jest.fn();
const mockPush = jest.fn();

jest.mock("expo-notifications", () => ({
  addNotificationResponseReceivedListener: (handler: (response: unknown) => void) => {
    mockListener = handler;
    return { remove: mockRemove };
  },
}));

jest.mock("expo-router", () => ({
  useRouter: () => ({ push: mockPush }),
}));

function Probe() {
  useNotificationResponseRouter();
  return null;
}

/** Tippt eine Benachrichtigung mit diesem Datenteil an. */
function antippen(data: Record<string, unknown>): void {
  act(() => {
    create(<Probe />);
  });
  expect(mockListener).not.toBeNull();
  act(() => {
    mockListener?.({ notification: { request: { content: { data } } } });
  });
}

beforeEach(() => {
  mockListener = null;
  mockPush.mockClear();
  mockRemove.mockClear();
});

describe("useNotificationResponseRouter – Versicherungen", () => {
  it.each([
    "insurance_deadline_ordinary",
    "insurance_main_due_question",
    "insurance_special_termination",
    "insurance_claim_followup",
  ])("fuehrt %s auf die Police, die der Datenteil nennt", (type) => {
    antippen({ type, policy_id: "p-1" });
    expect(mockPush).toHaveBeenCalledWith("/versicherung/p-1");
  });

  it("faengt einen Vorgang ohne eigene Police auf der Uebersicht auf", () => {
    // Ein Schadenvorgang kann ganz ohne Police bestehen - der Auffahrunfall,
    // bei dem die Gegenseite reguliert. Der Worker sendet dann bewusst gar
    // keine ID mit, und der Push darf trotzdem nicht ins Leere tippen.
    antippen({ type: "insurance_claim_followup" });
    expect(mockPush).toHaveBeenCalledWith("/mehr/versicherungen");
  });

  it("faengt eine Nachricht ohne Policen-ID auf der Uebersicht auf", () => {
    // Geloeschte Police, aeltere Nachricht: eine Detailseite zu einer
    // unbekannten ID waere eine Fehlermeldung.
    antippen({ type: "insurance_special_termination" });
    expect(mockPush).toHaveBeenCalledWith("/mehr/versicherungen");
  });

  it("meldet unerwartete Datenfelder, statt sie stillschweigend anzunehmen", () => {
    // Plan 7.1: was im Datenteil liegt, ist der Vorrat, aus dem eine kuenftige
    // lokale Benachrichtigung ihren Text anreichern koennte - und der stuende
    // dann auf dem Sperrbildschirm. Geloggt werden nur die FELDNAMEN.
    const warn = jest.spyOn(console, "warn").mockImplementation(() => {});
    antippen({
      type: "insurance_special_termination",
      policy_id: "p-1",
      new_amount_minor: 4800,
    });
    expect(warn).toHaveBeenCalledWith(
      expect.stringContaining("unerwartete Datenfelder"),
      "new_amount_minor",
    );
    expect(mockPush).toHaveBeenCalledWith("/versicherung/p-1");
    warn.mockRestore();
  });

  it("laesst einen unbekannten Typ unbeantwortet, statt irgendwohin zu springen", () => {
    antippen({ type: "insurance_something_new", policy_id: "p-1" });
    expect(mockPush).not.toHaveBeenCalled();
  });
});
