import { describe, expect, it } from "vitest";

import { productionDayStart, sevenDaysBefore } from "./periods";

describe("productionDayStart", () => {
  it("returns 05:00 on the same date when now is after 05:00", () => {
    expect(productionDayStart("2026-01-05T09:30:00Z")).toBe("2026-01-05T05:00:00.000Z");
    expect(productionDayStart("2026-01-05T23:59:59Z")).toBe("2026-01-05T05:00:00.000Z");
  });

  it("returns 05:00 the day before when now is before 05:00 (the night shift)", () => {
    expect(productionDayStart("2026-01-05T02:00:00Z")).toBe("2026-01-04T05:00:00.000Z");
  });

  it("is exactly the boundary at 05:00:00", () => {
    expect(productionDayStart("2026-01-05T05:00:00Z")).toBe("2026-01-05T05:00:00.000Z");
  });
});

describe("sevenDaysBefore", () => {
  it("subtracts exactly 7 x 24 h", () => {
    expect(sevenDaysBefore("2026-01-12T09:00:00Z")).toBe("2026-01-05T09:00:00.000Z");
  });
});
