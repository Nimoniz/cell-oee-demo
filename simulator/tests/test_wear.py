"""Electrode wear calibration: ~1.5 % scrap when new, 3-4 % at the end of cap life."""

from __future__ import annotations

import random

from conftest import make_config

from cell_sim.config import SimConfig
from cell_sim.wear import ElectrodeWear


def scrap_by_dressing_interval(cfg: SimConfig, caps: int = 100) -> tuple[list[float], float]:
    """Run the wear model alone over many cap lives, dressing on schedule.

    Returns the scrap rate per dressing interval (position in cap life) and the mean number of
    "two consecutive weld-NOK parts" events per cap (i.e. fault 201 candidates).
    """
    weld = cfg.weld
    dress, cap_parts = weld.tip_dressing.every_parts, weld.cap_change.every_parts
    quality = random.Random(2)
    bins = [[0, 0] for _ in range(cap_parts // dress)]
    pairs = 0
    for cap in range(caps):
        wear = ElectrodeWear(weld, random.Random(f"weld-{cap}"))
        previous_nok = False
        for part in range(cap_parts):
            part_ok = all([wear.weld_point().ok for _ in range(weld.points_per_part)])
            wear.part_done()
            scrap = (not part_ok) or quality.random() < cfg.cell.base_scrap_rate
            b = bins[part // dress]
            b[0] += 1
            b[1] += scrap
            pairs += (not part_ok) and previous_nok
            previous_nok = not part_ok
            if wear.state.parts_since_dressing >= dress:
                wear.dress()
    return [scrapped / total for total, scrapped in bins], pairs / caps


def test_scrap_rate_calibration() -> None:
    rates, pairs_per_cap = scrap_by_dressing_interval(make_config())
    assert 0.012 <= rates[0] <= 0.019  # new caps: base scrap only
    assert 0.030 <= rates[-1] <= 0.042  # end of cap life
    assert rates[-1] > 1.8 * rates[0]
    assert sum(rates[-2:]) > sum(rates[:2]) * 1.5  # degradation is progressive, not a cliff
    assert 0.3 <= pairs_per_cap <= 3  # fault 201 shows up late in cap life, not every hour


def _weld_parts(wear: ElectrodeWear, parts: int, points: int) -> None:
    for _ in range(parts):
        for _ in range(points):
            wear.weld_point()
        wear.part_done()


def test_dressing_partially_restores_and_cap_change_fully() -> None:
    cfg = make_config()
    wear = ElectrodeWear(cfg.weld, random.Random(0))
    ppp = cfg.weld.points_per_part

    _weld_parts(wear, 150, ppp)
    before = wear.drift()
    wear.dress()
    after = wear.drift()
    assert after < before  # recovers...
    assert after > 0  # ...but only partially: irreversible wear stays
    assert wear.state.parts_since_dressing == 0

    _weld_parts(wear, 1200, ppp)
    worn = wear.drift()
    assert worn > 2 * before
    assert wear.state.points_since_cap_change == 1350 * ppp

    wear.change_caps()
    assert wear.drift() == 0
    assert wear.state.points_since_cap_change == 0
    assert wear.state.parts_since_cap_change == 0


def test_wear_is_reproducible() -> None:
    cfg = make_config()
    a, b = ElectrodeWear(cfg.weld, random.Random(5)), ElectrodeWear(cfg.weld, random.Random(5))
    assert [a.weld_point() for _ in range(50)] == [b.weld_point() for _ in range(50)]
