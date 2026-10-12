/// <reference types="node" />
// Liest Dateien des Web-Pakets (index.html, Designkasten-Kopien) - daher Node-Typen.
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { DesignCard } from "../components/DesignCard";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const run = (rel: string) => new Function(readFileSync(path.join(WEB, rel), "utf8"))();

describe("DesignCard", () => {
  afterEach(() => {
    window.localStorage.clear();
    delete window.Designkasten;
    delete window.Designwahl;
  });

  it("meldet laut, wenn der Designkasten fehlt", () => {
    render(<DesignCard />);
    expect(screen.getByText(/Designwahl ist nicht geladen/)).toBeInTheDocument();
  });

  describe("mit geladenem Designkasten", () => {
    beforeEach(() => {
      run("public/designkasten/designs.js");
      run("public/designkasten/designwahl.js");
      const html = readFileSync(path.join(WEB, "index.html"), "utf8");
      new Function(/<script>([\s\S]*?)<\/script>/.exec(html)?.[1] ?? "")();
    });

    it("zeigt alle Designs, das aktive mit Haken, und schaltet per Klick um", async () => {
      const user = userEvent.setup();
      render(<DesignCard />);
      const items = screen.getAllByRole("menuitemradio");
      expect(items).toHaveLength(8);
      const klassisch = items.find((item) => item.dataset.design === "klassisch");
      expect(klassisch).toHaveAttribute("aria-checked", "true");
      expect(klassisch).toHaveTextContent("Standard");

      await user.click(screen.getByRole("menuitemradio", { name: /Dämmerung/ }));

      expect(document.documentElement.dataset.design).toBe("daemmerung");
      expect(document.documentElement.dataset.theme).toBe("dark");
      expect(screen.getByRole("menuitemradio", { name: /Dämmerung/ })).toHaveAttribute(
        "aria-checked",
        "true",
      );
      expect(window.localStorage.getItem("finanzplaner:web:design")).toBe("daemmerung");
    });
  });
});
