import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { apiIsoToInput, fmtDate, fmtDateTime, fmtTime, inputToApiIso, parseIso } from "./time";

// Shifts are 05:00, 13:00, 21:00 plant time (CLAUDE.md). This must render identically whatever
// the browser's own time zone is — that is the whole point of formatting in UTC explicitly.
const TIMEZONES = ["UTC", "America/Los_Angeles", "Asia/Kolkata", "Pacific/Kiritimati"];

describe.each(TIMEZONES)("time formatting under TZ=%s", (tz) => {
  const originalTz = process.env.TZ;

  beforeEach(() => {
    process.env.TZ = tz;
  });

  afterEach(() => {
    process.env.TZ = originalTz;
  });

  it("formats shift boundaries at 05:00, 13:00 and 21:00, never converted", () => {
    expect(fmtTime("2026-01-05T05:00:00Z")).toBe("05:00:00");
    expect(fmtTime("2026-01-05T13:00:00Z")).toBe("13:00:00");
    expect(fmtTime("2026-01-05T21:00:00Z")).toBe("21:00:00");
  });

  it("formats the date as dd/MM/yyyy", () => {
    expect(fmtDate("2026-01-05T05:00:00Z")).toBe("05/01/2026");
  });

  it("formats a full timestamp", () => {
    expect(fmtDateTime("2026-01-05T21:00:07.5Z")).toBe("05/01/2026 21:00:07");
  });
});

describe("parseIso", () => {
  it("rejects an invalid value instead of silently returning 'Invalid Date'", () => {
    expect(() => parseIso("not a date")).toThrow(/invalid ISO datetime/);
  });
});

describe("input <-> API ISO round trip", () => {
  it("treats a datetime-local value as plant time, not the browser's local time", () => {
    expect(inputToApiIso("2026-01-05T05:00")).toBe("2026-01-05T05:00:00Z");
    expect(inputToApiIso("2026-01-05T05:00:30")).toBe("2026-01-05T05:00:30Z");
  });

  it("round-trips back to an input value", () => {
    expect(apiIsoToInput("2026-01-05T05:00:00Z")).toBe("2026-01-05T05:00");
  });
});
