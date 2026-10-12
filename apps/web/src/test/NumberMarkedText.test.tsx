import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { NumberMarkedText } from "../components/assistant/NumberMarkedText";
import type { NumberCheckItem } from "../lib/assistantTypes";

function item(
  text: string,
  start: number,
  status: NumberCheckItem["status"],
  sources: string[] = [],
): NumberCheckItem {
  return { start, end: start + text.length, text, status, sources };
}

const marks = (container: HTMLElement) =>
  [...container.querySelectorAll<HTMLElement>("mark.assistant-number")].map((mark) => ({
    text: mark.firstChild?.textContent,
    status: mark.dataset.status,
  }));

describe("Antwort mit markierten Zahlen", () => {
  it("markiert belegte und unbelegte Zahlen und nennt die Quellen im Tooltip", () => {
    const content = "Im September 1.234,56 € für Miete, davon 12 % mehr als im August.";
    const { container } = render(
      <NumberMarkedText
        content={content}
        numbers={{
          items: [item("1.234,56 €", 13, "belegt", ["W1", "W3"]), item("12 %", 41, "unbelegt")],
          unverified: 1,
        }}
      />,
    );

    expect(marks(container)).toEqual([
      { text: "1.234,56 €", status: "belegt" },
      { text: "12 %", status: "unbelegt" },
    ]);
    const belegt = container.querySelector(".assistant-number--belegt")!;
    expect(belegt).toHaveAttribute("title", expect.stringContaining("Belegt durch W1, W3"));
    const unbelegt = container.querySelector(".assistant-number--unbelegt")!;
    expect(unbelegt).toHaveAttribute("title", expect.stringContaining("Ohne Beleg"));
    // Fuer Screenreader steht der Befund auch im Text.
    expect(unbelegt).toHaveTextContent("12 % (ohne Beleg)");

    // Der Text bleibt vollstaendig und in der Reihenfolge erhalten.
    const text = container.querySelector(".assistant-answer__text")!;
    expect(text.textContent!.replace(" (ohne Beleg)", "")).toBe(content);
    expect(screen.getByText(/1 Zahl ohne Beleg/)).toBeInTheDocument();
  });

  it("rechnet Positionen in Codepunkten (Server) auf UTF-16 um", () => {
    // Das Emoji ist ein Codepunkt, aber zwei UTF-16-Einheiten: ab hier
    // weichen die Zaehlweisen um eins ab.
    const content = "📈 Plus 250 € im Monat";
    const { container } = render(
      <NumberMarkedText
        content={content}
        numbers={{ items: [item("250 €", 7, "belegt", ["W2"])], unverified: 0 }}
      />,
    );
    expect(marks(container)).toEqual([{ text: "250 €", status: "belegt" }]);
    expect(screen.getByText("Alle Zahlen sind durch Abfragen belegt.")).toBeInTheDocument();
  });

  it("findet den Zahltext bei verschobenen Positionen und laesst Ueberlappungen unmarkiert", () => {
    const content = "Summe 980 € und 120 €.";
    const { container } = render(
      <NumberMarkedText
        content={content}
        numbers={{
          items: [
            { start: 0, end: 5, text: "980 €", status: "belegt", sources: ["W1"] },
            // ueberlappt den ersten Eintrag
            { start: 7, end: 11, text: "80 €", status: "unbelegt", sources: [] },
            item("120 €", 16, "unbelegt"),
            // gibt es im Text nicht
            item("999 €", 0, "unbelegt"),
          ],
          unverified: 3,
        }}
      />,
    );
    expect(marks(container)).toEqual([
      { text: "980 €", status: "belegt" },
      { text: "120 €", status: "unbelegt" },
    ]);
  });

  it("zeigt ohne Zahlenpruefung den Text ohne Markierung und ohne Befund", () => {
    const { container } = render(<NumberMarkedText content="Kein Befund: 42" numbers={null} />);
    expect(container.querySelectorAll("mark")).toHaveLength(0);
    expect(container).toHaveTextContent("Kein Befund: 42");
    expect(container.querySelector(".assistant-answer__check")).toBeNull();
  });
});

describe("Markdown der Antwort", () => {
  it("zeigt fett, Listen und Tabellen statt roher Zeichen und behaelt die Zahlenmarkierung", () => {
    const content = [
      "Hier die Antwort:",
      "",
      "**Lebensmittel:** insgesamt **774,00 €** ausgegeben.",
      "",
      "- Juli: 250,00 €",
      "- August: 262,00 €",
      "",
      "| Monat | Betrag |",
      "|---|---|",
      "| September | 262,00 € |",
    ].join("\n");
    const at = (text: string, from = 0) => content.indexOf(text, from);
    const { container } = render(
      <NumberMarkedText
        content={content}
        numbers={{
          items: [
            item("774,00 €", at("774,00 €"), "belegt", ["W3"]),
            item("250,00 €", at("250,00 €"), "belegt", ["W4"]),
            item("262,00 €", at("262,00 €", at("| September")), "unbelegt"),
          ],
          unverified: 1,
        }}
      />,
    );

    const text = container.querySelector(".assistant-answer__text")!;
    expect(text.textContent).not.toContain("**");
    expect(text.textContent).not.toContain("|---|");
    expect(container.querySelector("strong")).toHaveTextContent("Lebensmittel:");
    expect(container.querySelectorAll("ul li")).toHaveLength(2);
    expect(container.querySelector("table td")).toHaveTextContent("September");
    // Die fette Zahl ist markiert, ebenso die in Liste und Tabelle.
    expect(container.querySelector("strong mark.assistant-number--belegt")).toHaveTextContent(
      "774,00 €",
    );
    expect(container.querySelector("li mark")).toHaveTextContent("250,00 €");
    expect(container.querySelector("td mark.assistant-number--unbelegt")).toHaveTextContent(
      "262,00 €",
    );
  });

  it("zeigt HTML des Modells als Text und laesst einzelne Sternchen stehen", () => {
    const content = 'Achtung <img src=x onerror="alert(1)"> und 3 * 4 = 12 und **offen';
    const { container } = render(<NumberMarkedText content={content} numbers={null} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector(".assistant-answer__text")!.textContent).toBe(content);
  });
});
