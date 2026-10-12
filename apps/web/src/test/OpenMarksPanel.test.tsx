import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { OpenMarksPanel } from "../components/reimbursements/OpenMarksPanel";
import type { OpenReimbursementMark } from "../lib/reimbursementsApi";

function mark(overrides: Partial<OpenReimbursementMark> = {}): OpenReimbursementMark {
  return {
    id: "m1",
    transaction_id: "t1",
    state: "expected",
    expectation_mode: "full",
    expectation_rate: null,
    expectation_minor: null,
    expected_minor: 25000,
    source: "manual",
    rule_id: null,
    converted_at: null,
    created_at: "2026-08-10T10:00:00Z",
    updated_at: "2026-08-10T10:00:00Z",
    booked_date: "2026-07-01",
    amount_minor: -25000,
    payee_label: "Dr. Müller",
    note: null,
    ...overrides,
  };
}

const PEOPLE = [{ id: "p1", name: "Kind" }];

describe("OpenMarksPanel", () => {
  it("nennt ohne Markierungen den Weg dorthin statt nur Leere", () => {
    render(<OpenMarksPanel marks={[]} people={PEOPLE} onRemove={vi.fn()} onConvert={vi.fn()} />);
    expect(screen.getByText(/Nichts offen/)).toBeInTheDocument();
    expect(screen.getByText(/Alle Buchungen/)).toBeInTheDocument();
  });

  it("zeigt Betrag, Erwartung und Herkunft je Zeile", () => {
    render(
      <OpenMarksPanel marks={[mark()]} people={PEOPLE} onRemove={vi.fn()} onConvert={vi.fn()} />,
    );
    expect(screen.getByText("Dr. Müller")).toBeInTheDocument();
    expect(screen.getByText("von Hand")).toBeInTheDocument();
    expect(screen.getAllByText(/250,00/).length).toBeGreaterThan(0);
  });

  it("nennt bei einer Quote den Prozentsatz", () => {
    render(
      <OpenMarksPanel
        marks={[
          mark({ expectation_mode: "quota", expectation_rate: "0.8", expected_minor: 23154 }),
        ]}
        people={PEOPLE}
        onRemove={vi.fn()}
        onConvert={vi.fn()}
      />,
    );
    expect(screen.getByText(/231,54.*80 %/)).toBeInTheDocument();
  });

  it("weist auf Marken ohne Hoehe hin, damit die Summe nicht vollstaendig wirkt", () => {
    render(
      <OpenMarksPanel
        marks={[mark(), mark({ id: "m2", transaction_id: "t2", expected_minor: null })]}
        people={PEOPLE}
        onRemove={vi.fn()}
        onConvert={vi.fn()}
      />,
    );
    expect(screen.getByText(/1 ohne Höhe/)).toBeInTheDocument();
    expect(screen.getByText("Höhe offen")).toBeInTheDocument();
  });

  it("meldet die Buchung, nicht die Marke, wenn entfernt wird", async () => {
    const onRemove = vi.fn();
    render(
      <OpenMarksPanel marks={[mark()]} people={PEOPLE} onRemove={onRemove} onConvert={vi.fn()} />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Markierung entfernen" }));
    expect(onRemove).toHaveBeenCalledWith("t1");
  });
});

describe("OpenMarksPanel: Vorgang anlegen", () => {
  it("meldet Buchung und gewaehlte Person", async () => {
    const onConvert = vi.fn();
    render(
      <OpenMarksPanel marks={[mark()]} people={PEOPLE} onRemove={vi.fn()} onConvert={onConvert} />,
    );
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: /Vorgang anlegen für Dr. Müller/ }),
      "p1",
    );
    expect(onConvert).toHaveBeenCalledWith("t1", "p1");
  });

  it("bietet ohne versicherte Person keine Umwandlung an", () => {
    render(<OpenMarksPanel marks={[mark()]} people={[]} onRemove={vi.fn()} onConvert={vi.fn()} />);
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  });
});
