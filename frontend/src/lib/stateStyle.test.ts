import { describe, expect, it } from "vitest";

import { CellState, COMM_LOST_STYLE, stateStyle } from "./stateStyle";

describe("stateStyle", () => {
  it("gives every cell state a label, a color and an icon", () => {
    for (const state of Object.values(CellState)) {
      const style = stateStyle(state, false);
      expect(style.label).toBeTruthy();
      expect(style.color).toMatch(/^#[0-9a-f]{6}$/i);
      expect(style.icon).toBeTruthy();
    }
  });

  it("setup and manual share one color (violet), distinguished by icon and label", () => {
    const setup = stateStyle(CellState.SETUP, false);
    const manual = stateStyle(CellState.MANUAL, false);
    expect(setup.color).toBe(manual.color);
    expect(setup.icon).not.toBe(manual.icon);
    expect(setup.label).not.toBe(manual.label);
    // ...and that color must not be the planned-stop blue (they must stay visually distinct).
    expect(setup.color).not.toBe(stateStyle(CellState.PLANNED_STOP, false).color);
  });

  it("comm_lost overrides whatever state is passed", () => {
    expect(stateStyle(CellState.PRODUCING, true)).toEqual(COMM_LOST_STYLE);
  });

  it("starved and blocked are both orange-family but visually distinct from each other", () => {
    const starved = stateStyle(CellState.STARVED, false);
    const blocked = stateStyle(CellState.BLOCKED, false);
    expect(starved.color).not.toBe(blocked.color);
    expect(starved.icon).not.toBe(blocked.icon);
  });
});
