/// <reference types="jest" />
import { lightTheme, darkTheme } from "./palette";
import { getServiceStatusColor, sortServices } from "./serviceStatus";
import type { ServiceHealth } from "@finanzplaner/contracts";

describe("getServiceStatusColor", () => {
  it("maps ok to the positive (forest) color", () => {
    expect(getServiceStatusColor("ok", lightTheme)).toBe(lightTheme.positive);
  });

  it("maps degraded to the highlight (gold) color", () => {
    expect(getServiceStatusColor("degraded", lightTheme)).toBe(lightTheme.highlight);
  });

  it("maps down to the warning (coral) color", () => {
    expect(getServiceStatusColor("down", darkTheme)).toBe(darkTheme.warning);
  });

  it("maps unknown to a neutral (textSecondary) color", () => {
    expect(getServiceStatusColor("unknown", lightTheme)).toBe(lightTheme.textSecondary);
  });
});

describe("sortServices", () => {
  it("orders services in the canonical order regardless of input order", () => {
    const now = new Date().toISOString();
    const services: ServiceHealth[] = [
      { service: "lm_studio", status: "ok", checkedAt: now },
      { service: "api", status: "ok", checkedAt: now },
      { service: "worker", status: "ok", checkedAt: now },
    ];

    expect(sortServices(services).map((s) => s.service)).toEqual(["api", "worker", "lm_studio"]);
  });
});
