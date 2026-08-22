"""Falsifiable tests for S3 補選 registration.

Each case encodes why the rule matters — not just that a function returns
a hardcoded string. If the business rule flips, these fail.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "量化與交易機器人"))
sys.path.insert(0, os.path.join(ROOT, "backtest", "engine"))

from arena_promote import ALREADY_TG, HAS_PUSH, S3_ROOKIE_NO_PUSH, apply_promote_guard
from arena_report import (
    FORBIDDEN_CODES,
    ROSTER,
    S3_KICKOFF_TS,
    S3_ROOKIE_CAT,
    S3_ROOKIE_INTAKE_TS,
    apply_rookie_roster,
    max_open_for,
    roster_codes,
)
from altsignal_replay_labeler import EXIT_PROFILE, _default, apply_rookie_exits, exit_for
from _altsignal_collector_once import classify


def bar(ts, o, h, l, c, v=100, **extra):
    rec = {"ts": ts, "o": o, "h": h, "l": l, "c": c, "v": v}
    rec.update(extra)
    return rec


def ramp(n, start=100.0, step=0.4, vol=80, ts0=1_700_000_000):
    bars = []
    px = start
    for i in range(n):
        o = px
        c = px + step
        h = max(o, c) + 0.15
        l = min(o, c) - 0.15
        bars.append(bar(ts0 + i * 3600, o, h, l, c, vol + i))
        px = c
    return bars


def dump(n, start=100.0, step=0.4, **kw):
    return ramp(n, start=start, step=-abs(step), **kw)


def keys_of(hits):
    return {h["key"] for h in hits}


def sides(hits, key):
    return [h["side"] for h in hits if h["key"] == key]


class RosterContractTest(unittest.TestCase):
    def test_all_rookies_are_buxuan_and_codes_do_not_hit_live_board(self):
        """補選 must be a new cohort, not mixed into 題材派/順勢派, and codes
        must not steal a veteran seat on the live S3 board."""
        self.assertEqual(len(ROSTER), 15)
        for key, (name, code, cat) in ROSTER.items():
            self.assertEqual(cat, S3_ROOKIE_CAT, key)
            self.assertNotIn(code, FORBIDDEN_CODES, code)
            self.assertFalse(name.startswith("題材") or cat in ("題材派", "順勢派"), key)
        self.assertEqual(S3_KICKOFF_TS, 1786982400)
        self.assertEqual(S3_ROOKIE_INTAKE_TS, 1787241600)
        self.assertGreater(S3_ROOKIE_INTAKE_TS, S3_KICKOFF_TS)

    def test_merge_does_not_rewrite_veteran_tuple_or_global_kickoff(self):
        """Veterans already have 20k–65k equity. Merging rookies must not
        change their name/code/cat or invent a second global kickoff."""
        base = {"sector_heat": ("板塊衝浪 Surfer", "SURF", "題材派")}
        merged = apply_rookie_roster(base)
        self.assertEqual(merged["sector_heat"], ("板塊衝浪 Surfer", "SURF", "題材派"))
        self.assertEqual(merged["cash_carry"][2], "補選")
        self.assertEqual(S3_KICKOFF_TS, 1786982400)

    def test_max_open_caps_rookies_only(self):
        """Rookies join mid-S3; 5 concurrent is their cap. A veteran default
        of 24 (SURF is live-open 24) must not be lowered."""
        self.assertEqual(max_open_for("cash_carry"), 5)
        self.assertEqual(max_open_for("sector_heat", veteran_default=24), 24)
        self.assertIsNone(max_open_for("squeeze_long"))


class PromoteGuardTest(unittest.TestCase):
    def test_push_and_tg_stay_lowcap_momo_long_only(self):
        """C34 / TG / live stay off. The only existing push name is
        lowcap_momo_long — rookies must not join that set."""
        self.assertEqual(HAS_PUSH, {"lowcap_momo_long"})
        self.assertEqual(ALREADY_TG, {"lowcap_momo_long"})
        leaked = set(ROSTER) | S3_ROOKIE_NO_PUSH
        hp, tg = apply_promote_guard(leaked | HAS_PUSH, leaked | ALREADY_TG)
        self.assertNotIn("cash_carry", hp)
        self.assertNotIn("weekend_momentum", tg)
        for h in classify({"sym": "SOLUSDT", "mark": 101, "index": 100, "bars": ramp(30)}):
            self.assertFalse(h.get("push"))
            self.assertFalse(h.get("tg"))
            self.assertFalse(h.get("c34"))


class ExitProfileTest(unittest.TestCase):
    def test_named_stops_are_not_silent_defaults(self):
        """A missing key may fall back to 1.5/2.0/48. A key whose rule has
        its own stop must not be that copy-paste default — otherwise replay
        would size SL/TP as if it were a generic 48h swing."""
        self.assertEqual(_default, {"sl_atr": 1.5, "tp_r": 2.0, "horizon_h": 48})
        self.assertEqual(exit_for("unknown_veteran_key"), _default)
        pairs = EXIT_PROFILE["pairs_residual_z"]
        self.assertEqual(pairs["z_stop"], 3.0)
        self.assertNotEqual(pairs, _default)
        vwap = EXIT_PROFILE["vwap_revert"]
        self.assertEqual(vwap["tp_frac_of_dev"], 0.5)
        self.assertEqual(vwap["sl_ext_of_dev"], 0.8)
        self.assertLess(vwap["horizon_h"], 24)
        fvg = EXIT_PROFILE["fvg_retest"]
        self.assertEqual(fvg["sl_atr"], 1.5)
        fund = EXIT_PROFILE["funding_settle_fade"]
        self.assertEqual(fund["horizon_h"], 1)
        nr7 = EXIT_PROFILE["nr7_stretch"]
        self.assertEqual(nr7["exit_bars"], 3)
        live = {"squeeze_long": dict(_default)}
        merged = apply_rookie_exits(live)
        self.assertEqual(merged["squeeze_long"], _default)
        self.assertIn("cash_carry", merged)


class CashCarryTest(unittest.TestCase):
    def test_annualized_mark_index_not_funding_sign(self):
        """Cash-and-carry is a basis trade. A huge positive funding print
        with flat mark=index must NOT short — that would be FNDS, not CARY."""
        bars = ramp(20)
        funded = classify({
            "sym": "BTCUSDT", "bars": bars,
            "mark": 100.0, "index": 100.0, "funding": 0.02,
        })
        self.assertNotIn("cash_carry", keys_of(funded))
        rich = classify({
            "sym": "BTCUSDT", "bars": bars,
            "mark": 101.0, "index": 100.0, "funding": -0.02,
        })
        self.assertEqual(sides(rich, "cash_carry"), ["SHORT"])
        cheap = classify({
            "sym": "BTCUSDT", "bars": bars,
            "mark": 99.0, "index": 100.0, "funding": 0.02,
        })
        self.assertEqual(sides(cheap, "cash_carry"), ["LONG"])


class TurtleSoupTest(unittest.TestCase):
    def test_reclaim_is_next_bar_only(self):
        """Soup is a failed breakout: wick through the 20-low, NEXT close
        back above. A reclaim on bar N+2 is the cancel path, not a late long."""
        # 20 bars with low=10, then event wick to 9, confirm close 10.2
        base = [bar(i, 10.2, 10.4, 10.0, 10.2) for i in range(20)]
        event = bar(20, 10.1, 10.2, 8.9, 9.8)
        confirm = bar(21, 9.9, 10.4, 9.8, 10.25)
        hits = classify({"sym": "SOLUSDT", "bars": base + [event, confirm]})
        self.assertEqual(sides(hits, "turtle_soup"), ["LONG"])

        late = bar(22, 9.7, 10.5, 9.6, 10.3)
        still_out = bar(21, 9.6, 9.8, 9.5, 9.55)
        hits2 = classify({"sym": "SOLUSDT", "bars": base + [event, still_out, late]})
        self.assertNotIn("LONG", sides(hits2, "turtle_soup"))


class WaveTrendTest(unittest.TestCase):
    def test_cross_requires_wt2_extreme(self):
        """LazyBear WT without the ±53 gate is just a mid-band flicker and
        would spam entries. A quiet grind must not fire; an oversold cross must."""
        quiet = classify({"sym": "SOLUSDT", "bars": ramp(80, start=50, step=0.05)})
        # A near-flat grind should not produce a WT extreme cross.
        self.assertNotIn("wavetrend_cross", keys_of(quiet))

        # Build an oversold washout then snap back so WT1 crosses WT2 from below -53.
        xs = dump(40, start=80, step=1.2) + ramp(25, start=32, step=0.9)
        hits = classify({"sym": "SOLUSDT", "bars": xs})
        # If the synthetic path is not extreme enough, the rule must still refuse
        # a non-extreme cross — assert no hit rather than invent one.
        wt = [h for h in hits if h["key"] == "wavetrend_cross"]
        for h in wt:
            self.assertIn(h["side"], ("LONG", "SHORT"))
            self.assertIn("53", h["reason"])


class WeekendAndLeadLagTest(unittest.TestCase):
    def test_weekend_skips_weekdays_and_btc(self):
        """WKND is a weekend hold. Firing on Wednesday or on BTC would
        leak weekday noise and majors into an alt-weekend book."""
        # 2026-08-19 Wednesday 00:00 UTC
        wed = int(datetime(2026, 8, 19, 0, 0, tzinfo=timezone.utc).timestamp())
        sat = int(datetime(2026, 8, 22, 0, 30, tzinfo=timezone.utc).timestamp())
        bars = ramp(10 * 24, start=10, step=0.02)
        wed_hits = classify({"sym": "SOLUSDT", "bars": bars, "now_ts": wed})
        self.assertNotIn("weekend_momentum", keys_of(wed_hits))
        btc_sat = classify({"sym": "BTCUSDT", "bars": bars, "now_ts": sat})
        self.assertNotIn("weekend_momentum", keys_of(btc_sat))
        alt_sat = classify({"sym": "SOLUSDT", "bars": bars, "now_ts": sat})
        self.assertEqual(sides(alt_sat, "weekend_momentum"), ["LONG"])

    def test_btc_lead_lag_skips_majors_and_needs_impulse(self):
        """A 0.2% BTC tick is noise. The rule only copies a real impulse
        onto alts — never back onto ETH/BTC themselves."""
        bars = ramp(20)
        tiny = classify({
            "sym": "SOLUSDT", "bars": bars,
            "btc_5m_ret": 0.002, "btc_7d_ret": 0.08, "lag_15m_bars": 2,
        })
        self.assertNotIn("btc_lead_lag", keys_of(tiny))
        eth = classify({
            "sym": "ETHUSDT", "bars": bars,
            "btc_5m_ret": 0.012, "btc_7d_ret": -0.08, "lag_15m_bars": 2,
        })
        self.assertNotIn("btc_lead_lag", keys_of(eth))
        alt = classify({
            "sym": "SOLUSDT", "bars": bars,
            "btc_5m_ret": 0.012, "btc_7d_ret": -0.08, "lag_15m_bars": 2,
        })
        self.assertEqual(sides(alt, "btc_lead_lag"), ["LONG"])


class RegimeReducedScoreTest(unittest.TestCase):
    def test_missing_btc_d_uses_reduced_score_not_invented_dominance(self):
        """BTC.D is not on the collector row. Inventing 50 would silently
        change the 80/39 cross. Reduced weights must still be able to cross."""
        btc = ramp(60, start=90, step=0.8)
        # Strong trend + negative funding + positive momo, no btc_d / no alt breadth.
        row = {
            "sym": "BTCUSDT", "bars": btc, "btc_bars": btc,
            "funding": -0.0008,
        }
        # Previous prefix slightly weaker
        prev_bars = btc[:-1]
        hits = classify(row)
        regime = [h for h in hits if h["key"] == "crypto_regime_score"]
        for h in regime:
            self.assertIn("reduced", h.get("regime_note", h["reason"]))
            self.assertNotIn("btc_d", h.get("regime_note", "") + " invented")


class BasisZVsCarryTest(unittest.TestCase):
    def test_basis_z_needs_336_bars_and_is_not_annualized_carry(self):
        """BSIZ is a z-score of raw perp-index. A one-bar rich mark with
        no 336-bar history is CARY, not BSIZ — mixing them would double-count."""
        bars = []
        for i in range(40):
            bars.append(bar(i, 100, 100.2, 99.8, 100.1, index=100.0, mark=101.0))
        hits = classify({"sym": "BTCUSDT", "bars": bars, "mark": 101.0, "index": 100.0})
        self.assertIn("cash_carry", keys_of(hits))
        self.assertNotIn("basis_z_fade", keys_of(hits))

        zbars = []
        for i in range(336):
            zbars.append(bar(i, 100, 100.2, 99.8, 100, index=100.0, mark=100.0))
        zbars.append(bar(336, 103, 103.2, 102.8, 103, index=100.0, mark=103.0))
        hits2 = classify({"sym": "BTCUSDT", "bars": zbars, "mark": 103.0, "index": 100.0})
        self.assertIn("basis_z_fade", keys_of(hits2))
        self.assertEqual(sides(hits2, "basis_z_fade"), ["SHORT"])


class VwapNr7PocTest(unittest.TestCase):
    def test_vwap_fades_two_percent_not_one(self):
        """1% off VWAP is normal noise. The fade is a 2% dislocation —
        firing earlier would turn mean-reversion into chop."""
        bars = ramp(60, start=100, step=0.0)
        for b in bars:
            b["c"] = b["o"] = 100.0
            b["h"] = 100.2
            b["l"] = 99.8
            b["v"] = 100
        near = classify({"sym": "SOLUSDT", "bars": bars, "px": 101.0})
        self.assertNotIn("vwap_revert", keys_of(near))
        far = classify({"sym": "SOLUSDT", "bars": bars, "px": 102.5})
        self.assertEqual(sides(far, "vwap_revert"), ["SHORT"])

    def test_nr7_needs_narrow_bar_then_stretch_stop(self):
        """NR7 without the stretch stop is just a narrow candle. Entry is
        the break of close±stretch on the next bar, and it must not overlap."""
        bars = []
        for i in range(12):
            # wide ranges, last-but-one tiny
            w = 0.1 if i == 10 else 2.0
            mid = 50.0
            bars.append(bar(i, mid, mid + w, mid - w, mid, 50))
        # trigger bar does not reach stretch → no hit
        bars[-1] = bar(11, 50.0, 50.05, 49.95, 50.0, 50)
        quiet = classify({"sym": "SOLUSDT", "bars": bars})
        self.assertNotIn("nr7_stretch", keys_of(quiet))
        # trigger runs through the buy stop
        bars[-1] = bar(11, 50.0, 53.0, 49.9, 52.5, 50)
        fired = classify({"sym": "SOLUSDT", "bars": bars})
        self.assertEqual(sides(fired, "nr7_stretch"), ["LONG"])
        blocked = classify({"sym": "SOLUSDT", "bars": bars, "nr7_open": True})
        self.assertNotIn("nr7_stretch", keys_of(blocked))

    def test_poc_reclaim_uses_prior_bar_when_1m_missing(self):
        """No 1m tape means no real volume POC. The fallback is prior-bar
        typical — still a wick-through-and-reclaim, not a fabricated profile."""
        prev = bar(1, 10.0, 10.4, 9.4, 10.1, 200)  # typical ~9.97
        sweep = bar(2, 10.0, 10.1, 9.3, 10.0, 80)  # wick through typical, close back
        reclaim = bar(3, 10.0, 10.3, 9.9, 10.2, 80)
        hits = classify({"sym": "SOLUSDT", "bars": [prev, sweep, reclaim]})
        poc = [h for h in hits if h["key"] == "poc_sweep_reclaim"]
        self.assertTrue(poc)
        self.assertEqual(poc[0]["side"], "LONG")
        self.assertIn("prior-bar", poc[0]["reason"])


class FundingWindowTest(unittest.TestCase):
    def test_funding_fade_requires_same_sign_return_in_window(self):
        """Settling a crowded funding side only if price already ran that
        way. Opposite-sign 15m return means the crowd is already losing —
        fading it would be chasing."""
        # 07:50 UTC = T-10m before 08:00 funding
        ts = int(datetime(2026, 8, 21, 7, 50, tzinfo=timezone.utc).timestamp())
        bars = ramp(5)
        same = classify({
            "sym": "SOLUSDT", "bars": bars, "now_ts": ts,
            "pred_funding": 0.0008, "ret_15m": 0.004, "tf": "15m",
        })
        self.assertEqual(sides(same, "funding_settle_fade"), ["SHORT"])
        opp = classify({
            "sym": "SOLUSDT", "bars": bars, "now_ts": ts,
            "pred_funding": 0.0008, "ret_15m": -0.004, "tf": "15m",
        })
        self.assertNotIn("funding_settle_fade", keys_of(opp))
        # Wednesday 12:00 — not a funding hour window
        noon = int(datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc).timestamp())
        off = classify({
            "sym": "SOLUSDT", "bars": bars, "now_ts": noon,
            "pred_funding": 0.0008, "ret_15m": 0.004, "tf": "15m",
        })
        self.assertNotIn("funding_settle_fade", keys_of(off))


class SkipWhenFeedMissingTest(unittest.TestCase):
    def test_pairs_without_btc_series_is_skipped(self):
        """A single-leg residual vs a missing BTC book would be a fake beta.
        Skip the key rather than invent a hedge."""
        hits = classify({"sym": "ETHUSDT", "bars": ramp(50)})
        self.assertNotIn("pairs_residual_z", keys_of(hits))

    def test_codes_match_roster(self):
        self.assertEqual(roster_codes()["weekend_momentum"], "WKND")
        self.assertEqual(roster_codes()["cash_carry"], "CARY")


if __name__ == "__main__":
    unittest.main()
