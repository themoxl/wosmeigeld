import { describe, expect, it } from "vitest";
import { ApiError } from "../lib/api";
import {
  AI_REASONS,
  aiReasonActions,
  aiReasonCodeIn,
  aiReasonFromError,
  describeAiReason,
  isAiReasonCode,
  isUnassessedVerdict,
} from "../lib/aiReasons";
import { describeError } from "../lib/assistantFormat";

describe("aiReasons", () => {
  it("beschreibt einen Grund wie describe() im Backend", () => {
    expect(describeAiReason("model_timeout", "Nach 120 s.")).toBe(
      "Der Modellaufruf hat das Zeitbudget überschritten. Nach 120 s. Abhilfe: Einstellungen › KI › Zeitbudget je Anfrage erhöhen oder ein schnelleres Modell wählen.",
    );
    expect(describeAiReason("model_http_error")).toBe(
      "Der Modellserver hat den Aufruf mit einem Fehler beantwortet.",
    );
  });

  it("laesst einen unbekannten Schluessel nie allein stehen", () => {
    expect(describeAiReason("llm_timeout")).toBe(
      "Die KI-Anfrage ist fehlgeschlagen (Kennung: llm_timeout).",
    );
    expect(isAiReasonCode("llm_timeout")).toBe(false);
    expect(isAiReasonCode("toString")).toBe(false);
  });

  it("ordnet jedem Grund Stufe und Klartext zu", () => {
    for (const [code, reason] of Object.entries(AI_REASONS)) {
      expect(reason.text, code).toMatch(/\.$/);
      expect(["info", "warnung", "fehler"]).toContain(reason.level);
    }
  });

  it("bietet die Aktionen an einer Stelle an", () => {
    expect(aiReasonActions("ai_disabled")).toMatchObject({ settings: true, loadModel: false });
    expect(aiReasonActions("model_settings_invalid").settings).toBe(true);
    expect(aiReasonActions("model_context_unknown").settings).toBe(true);
    expect(aiReasonActions("model_reasoning_unsupported").settings).toBe(true);
    expect(aiReasonActions("model_unavailable")).toMatchObject({
      settings: false,
      loadModel: true,
    });
    expect(aiReasonActions("model_missing")).toMatchObject({ loadModel: true });
    expect(aiReasonActions("model_budget_exhausted")).toMatchObject({
      settings: true,
      retryWithoutThinking: true,
    });
    expect(aiReasonActions("model_reserve_tight").compact).toBe(true);
    expect(aiReasonActions(null)).toEqual({
      settings: false,
      loadModel: false,
      compact: false,
      retryWithoutThinking: false,
    });
  });

  it("liest den Grund aus einer Fehlerantwort", () => {
    const error = new ApiError(
      "Die KI ist für diesen Haushalt ausgeschaltet.",
      409,
      false,
      "Die KI ist für diesen Haushalt ausgeschaltet.",
      "ai_disabled",
    );
    expect(aiReasonFromError(error)).toEqual({
      code: "ai_disabled",
      message: "Die KI ist für diesen Haushalt ausgeschaltet.",
    });
    expect(aiReasonFromError(new ApiError("Nicht gefunden.", 404))).toBeNull();
    expect(aiReasonFromError("model_timeout")).toBeNull();
  });

  it("findet den Grund in Kennung und Sammelmeldung", () => {
    expect(aiReasonCodeIn("Kein Modell. (Kennung: model_missing)")).toBe("model_missing");
    expect(
      aiReasonCodeIn("keine verwertbare Antwort (model_whatever, model_timeout ×2, ai_disabled)"),
    ).toBe("model_timeout");
    expect(aiReasonCodeIn("Alles gut.")).toBeNull();
    expect(aiReasonCodeIn(null)).toBeNull();
  });

  it("erkennt nicht beurteilte Paare eines Teilergebnisses", () => {
    expect(isUnassessedVerdict("unsure", "Nicht beurteilt: Zeitbudget überschritten.")).toBe(true);
    expect(isUnassessedVerdict("unsure", "Nicht bewertet: Zeitbudget. Bitte manuell prüfen.")).toBe(
      true,
    );
    expect(isUnassessedVerdict("unsure", "Datum passt, Zweck nicht.")).toBe(false);
    expect(isUnassessedVerdict("same", "Nicht beurteilt: x")).toBe(false);
  });
});

describe("KI-Feld: Fehleraktionen aus derselben Zuordnung", () => {
  it("verweist bei ungueltiger Einstellung auf Einstellungen › KI", () => {
    expect(describeError("failed", "model_settings_invalid").settings).toBe(true);
  });

  it("nennt bei fehlendem Modell LM Studio", () => {
    expect(describeError("failed", "model_missing").loadModelHint).toBe(
      "Modell in LM Studio laden.",
    );
    expect(describeError("cancelled", "model_missing").loadModelHint).toBeNull();
  });

  it("bietet Verdichten nur bei Platzgruenden an", () => {
    expect(describeError("failed", "model_reserve_tight").compact).toBe(true);
    expect(describeError("failed", "model_timeout").compact).toBe(false);
  });
});
