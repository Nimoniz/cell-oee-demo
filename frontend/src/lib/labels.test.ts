import { describe, expect, it } from "vitest";

import { causeLabel, faultLabel, paretoKeyLabel } from "./labels";

describe("paretoKeyLabel", () => {
  it("resolves cause keys, with a dedicated label for the unqualified bucket", () => {
    expect(paretoKeyLabel("cause", "missing_parts")).toBe(causeLabel("missing_parts"));
    expect(paretoKeyLabel("cause", "unqualified")).toBe("Non qualifié");
  });

  it("resolves station keys to their short form", () => {
    expect(paretoKeyLabel("station", "OP20")).toBe("OP20");
  });

  it("resolves numeric fault codes", () => {
    expect(paretoKeyLabel("fault_code", "202")).toBe(faultLabel(202));
  });

  it("resolves state_<n> keys for losses without a fault code", () => {
    expect(paretoKeyLabel("fault_code", "state_4")).toContain("Réglage");
    expect(paretoKeyLabel("fault_code", "state_5")).toContain("amont");
  });

  it("falls back to a generic label for an unknown fault code", () => {
    expect(paretoKeyLabel("fault_code", "999")).toBe("Défaut 999");
  });
});
