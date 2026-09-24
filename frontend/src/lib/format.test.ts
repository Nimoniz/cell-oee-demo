import { describe, expect, it } from "vitest";

import { fmtClock, fmtDurationS, fmtKa, fmtNumber, fmtPct } from "./format";

describe("null vs. zero", () => {
  it("renders null as an em dash, never as 0 or 0 %", () => {
    expect(fmtPct(null)).toBe("—");
    expect(fmtNumber(null)).toBe("—");
    expect(fmtKa(null)).toBe("—");
    expect(fmtDurationS(null)).toBe("—");
    expect(fmtClock(null)).toBe("—");
  });

  it("renders a genuine zero as 0, distinct from null", () => {
    expect(fmtPct(0)).toBe("0,0 %");
    expect(fmtNumber(0)).toBe("0");
    expect(fmtDurationS(0)).toBe("0 s");
  });
});

describe("fmtPct", () => {
  it("formats a ratio as a French percentage", () => {
    expect(fmtPct(0.8256)).toBe("82,6 %");
    expect(fmtPct(1)).toBe("100,0 %");
  });
});

describe("fmtDurationS", () => {
  it("picks the coarsest unit that fits", () => {
    expect(fmtDurationS(45)).toBe("45 s");
    expect(fmtDurationS(125)).toBe("2 min 05 s");
    expect(fmtDurationS(3725)).toBe("1 h 02 min");
  });
});

describe("fmtClock", () => {
  it("formats a compact MM:SS, or H:MM:SS past an hour", () => {
    expect(fmtClock(90)).toBe("01:30");
    expect(fmtClock(3725)).toBe("1:02:05");
  });
});
