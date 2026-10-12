import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ContextMeter } from "../components/assistant/ContextMeter";
import type { ContextMeter as ContextMeterData } from "../lib/assistantTypes";

function meter(overrides: Partial<ContextMeterData> = {}): ContextMeterData {
  return {
    run_id: "run-1",
    call_nr: 3,
    input_tokens: 12_000,
    window_tokens: 32_768,
    reserve_tokens: 8_192,
    exact: true,
    ...overrides,
  };
}

describe("Kontextbalken", () => {
  it("zeigt Kontext N % · E / W mit Balken und gemessener Herkunft", () => {
    render(<ContextMeter meter={meter()} />);
    const element = screen.getByRole("meter", { name: "Kontext" });

    expect(element).toHaveTextContent("Kontext37 % · 12.000 / 32.768");
    expect(element).not.toHaveClass("voll");
    expect(element.querySelector<HTMLElement>(".assistant-meter__fill")!.style.width).toBe("37%");
    expect(element).toHaveAttribute("aria-valuenow", "12000");
    expect(element).toHaveAttribute("aria-valuemax", "32768");

    const tooltip = element.getAttribute("title") ?? "";
    expect(tooltip).toContain("Aufruf 3");
    expect(tooltip).toContain("vom Modellserver gemessen");
    expect(tooltip).toContain("Verdichtungsschwelle 24.576 Token");
  });

  it("ist voll, sobald die Eingabe in die Antwortreserve reicht (E > W − R)", () => {
    const { rerender } = render(<ContextMeter meter={meter({ input_tokens: 24_577 })} />);
    expect(screen.getByRole("meter")).toHaveClass("assistant-meter", "voll");

    // Genau auf der Schwelle ist noch Platz.
    rerender(<ContextMeter meter={meter({ input_tokens: 24_576 })} />);
    expect(screen.getByRole("meter")).not.toHaveClass("voll");
  });

  it("kappt den Balken bei 100 % und nennt eine geschaetzte Eingabe als solche", () => {
    render(
      <ContextMeter meter={meter({ input_tokens: 40_000, exact: false, reserve_tokens: null })} />,
    );
    const element = screen.getByRole("meter");

    expect(element).toHaveTextContent("122 % · 40.000 / 32.768");
    expect(element).toHaveTextContent("geschätzt");
    expect(element.querySelector<HTMLElement>(".assistant-meter__fill")!.style.width).toBe("100%");
    // Ohne bekannte Reserve gibt es keine Schwelle - und damit kein "voll".
    expect(element).not.toHaveClass("voll");
    expect(element.getAttribute("title")).toContain("geschätzt");
    expect(element.getAttribute("title")).toContain("Antwortreserve ist unbekannt");
  });

  it("zeigt ohne Messung nichts an statt zu raten", () => {
    const { container, rerender } = render(<ContextMeter meter={null} />);
    expect(container).toBeEmptyDOMElement();

    rerender(<ContextMeter meter={meter({ window_tokens: 0 })} />);
    expect(container).toBeEmptyDOMElement();
  });
});
