"""場外賽：股票代幣時鐘與 S3 隔離。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "量化與交易機器人"))
sys.path.insert(0, os.path.join(ROOT, "backtest", "engine"))

from arena_promote import HAS_PUSH, apply_promote_guard
from arena_report import (
    ROSTER as S3_ROSTER,
    S3_KICKOFF_TS,
    S3_ROOKIE_KEYS,
    apply_rookie_roster,
)
from altsignal_replay_labeler import apply_rookie_exits
from stock_arena_report import (
    ROSTER as STOCK_ROSTER,
    STOCK_KEYS,
    STOCK_KICKOFF_TS,
    UNIVERSE,
    is_stock_token,
    seed_board,
)
from stock_arena_classify import classify_stock
from stock_arena_exits import EXIT_PROFILE as STOCK_EXITS
from stock_arena_exits import apply_stock_exits
from stock_arena_hook import record_stock_row
from _altsignal_collector_once import classify as classify_s3
from _altsignal_collector_once import extend_hits


def bar(ts, o, h, l, c, v=10):
    return {"ts": int(ts), "o": o, "h": h, "l": l, "c": c, "v": v}


def keys_of(hits):
    return {h["key"] for h in hits}


def sides(hits, key):
    return [h["side"] for h in hits if h["key"] == key]


def ny_ts(hour, minute, day=21):
    dt = datetime(2026, 8, day, hour, minute, tzinfo=ZoneInfo("America/New_York"))
    return int(dt.timestamp())


def hk_ts(hour, minute, day=21):
    dt = datetime(2026, 8, day, hour, minute, tzinfo=ZoneInfo("Asia/Hong_Kong"))
    return int(dt.timestamp())


def minute_bars(start_ts, n, px0=100.0, step=0.0, spike_at=None, spike=0.0):
    bars = []
    px = px0
    for i in range(n):
        c = px + step
        h, l = max(px, c), min(px, c)
        if spike_at is not None and i == spike_at:
            c = px + spike
            h, l = max(px, c), min(px, c)
        bars.append(bar(start_ts + i * 60, px, h + 0.05, l - 0.05, c, 20))
        px = c
    return bars


class IsolationTest(unittest.TestCase):
    def test_stock_roster_is_not_merged_into_s3(self):
        """場外賽進 S3 ROSTER 會讓 SURF 跟 NYOR 比權益。合併函式必須拒絕。"""
        merged = apply_rookie_roster({"sector_heat": ("板塊衝浪 Surfer", "SURF", "題材派")})
        self.assertNotIn("ny_orb", merged)
        self.assertNotIn("ny_orb", S3_ROSTER)
        self.assertEqual(STOCK_KICKOFF_TS, 1787241600)
        self.assertNotEqual(STOCK_KICKOFF_TS, S3_KICKOFF_TS)
        self.assertEqual(S3_KICKOFF_TS, 1786982400)
        self.assertTrue(set(STOCK_ROSTER).isdisjoint(S3_ROSTER))
        self.assertTrue(set(STOCK_ROSTER).isdisjoint(S3_ROOKIE_KEYS))

    def test_s3_classify_skips_tesla_token(self):
        """S3 補選在 TSLAX 上開火 = 幣圈策略打股票。必須空命中。"""
        hits = classify_s3({
            "sym": "TSLAXUSDT",
            "mark": 101, "index": 100,
            "bars": [bar(i, 100, 101, 99, 100) for i in range(30)],
        })
        self.assertEqual(hits, [])

    def test_s3_classify_skips_mstr_alias(self):
        """S3 已誤開 MSTRUSDT。無 X 後綴的股票代幣也必須從幣圈 classify 踢出。"""
        hits = classify_s3({
            "sym": "MSTRUSDT",
            "mark": 101, "index": 100,
            "bars": [bar(i, 100, 101, 99, 100) for i in range(30)],
        })
        self.assertEqual(hits, [])
        self.assertTrue(is_stock_token("MSTRUSDT"))
        self.assertNotIn("MSTR", UNIVERSE)

    def test_stock_classify_skips_btc(self):
        hits = classify_stock({
            "sym": "BTCUSDT",
            "now_ts": ny_ts(10, 0),
            "bars": [bar(i, 100, 101, 99, 100) for i in range(30)],
        })
        self.assertEqual(hits, [])

    def test_extend_hits_does_not_inject_stock_into_s3(self):
        """live classify 的 extend_hits 若把 NYOR 寫進 S3 hits，兩個榜就混了。"""
        t0 = ny_ts(9, 30)
        later = minute_bars(t0, 20, px0=100, step=0.0)
        later[-1] = bar(t0 + 19 * 60, 100.2, 102.5, 100.1, 102.2, 30)
        row = {
            "sym": "NVDAXUSDT",
            "now_ts": t0 + 19 * 60,
            "bars_1m": later,
            "bars": later,
        }
        self.assertEqual(extend_hits([], row), [])
        leftover = [{"key": "radar", "side": "LONG"}]
        self.assertEqual(extend_hits(leftover, row), [])
        self.assertEqual(sides(classify_stock(row), "ny_orb"), ["LONG"])

    def test_push_still_only_lowcap(self):
        hp, tg = apply_promote_guard(set(STOCK_ROSTER) | HAS_PUSH, set(STOCK_ROSTER) | HAS_PUSH)
        self.assertEqual(hp, {"lowcap_momo_long"})
        self.assertNotIn("ny_orb", hp)
        self.assertNotIn("ny_orb", tg)

    def test_stock_exits_stay_off_s3_exit_map(self):
        """場外出場合進 S3 EXIT_PROFILE 會讓 replay 用股票時鐘平幣圈倉。"""
        merged = apply_rookie_exits({"squeeze_long": {"sl_atr": 1.5}})
        self.assertNotIn("ny_orb", merged)
        stock = apply_stock_exits({})
        self.assertIn("ny_orb", stock)
        self.assertEqual(set(stock), set(STOCK_EXITS))
        self.assertTrue(STOCK_EXITS["ny_orb"].get("eod_flat"))
        self.assertTrue(STOCK_EXITS["weekend_converge"].get("flatten_on_monday_rth"))


class SessionClockTest(unittest.TestCase):
    def test_nyor_does_not_fire_on_hynix(self):
        """美股 ORB 打韓股 = 用錯開盤時鐘。"""
        t0 = ny_ts(9, 30)
        intra = minute_bars(t0, 40, px0=100, step=0.0)
        intra[-1] = bar(t0 + 39 * 60, 100, 108, 99, 107, 20)
        hits = classify_stock({
            "sym": "SKHYNIXUSDT",
            "now_ts": t0 + 39 * 60,
            "bars_1m": intra,
            "bars": intra,
        })
        self.assertNotIn("ny_orb", keys_of(hits))

    def test_hkor_does_not_fire_on_nvdax(self):
        """港股 09:30 HKT ≠ 美股 09:30 ET。NVDAX 不得吃 HKOR。"""
        t0 = hk_ts(9, 30)
        later = minute_bars(t0, 20, px0=100, step=0.0)
        later[-1] = bar(t0 + 19 * 60, 100.2, 102.5, 100.1, 102.2, 30)
        nvda = classify_stock({
            "sym": "NVDAXUSDT",
            "now_ts": t0 + 19 * 60,
            "bars_1m": later,
            "bars": later,
        })
        self.assertNotIn("hk_orb", keys_of(nvda))
        tencent = classify_stock({
            "sym": "TENCENTUSDT",
            "now_ts": t0 + 19 * 60,
            "bars_1m": later,
            "bars": later,
        })
        self.assertEqual(sides(tencent, "hk_orb"), ["LONG"])

    def test_nyor_breakout_after_15m_not_during_range(self):
        """區間還在長的前 15 分鐘進場會把 OR 定義寫進成交。"""
        t0 = ny_ts(9, 30)  # Friday 2026-08-21
        intra = minute_bars(t0, 10, px0=100, step=0.2)
        during = classify_stock({
            "sym": "NVDAXUSDT",
            "now_ts": t0 + 8 * 60,
            "bars_1m": intra,
            "bars": intra,
        })
        self.assertNotIn("ny_orb", keys_of(during))

        later = minute_bars(t0, 20, px0=100, step=0.0)
        later[-1] = bar(t0 + 19 * 60, 100.2, 102.5, 100.1, 102.2, 30)
        after = classify_stock({
            "sym": "NVDAXUSDT",
            "now_ts": t0 + 19 * 60,
            "bars_1m": later,
            "bars": later,
        })
        self.assertEqual(sides(after, "ny_orb"), ["LONG"])

    def test_nyor_skips_hourly_only_bars(self):
        """只有 1h 棒卻假裝有 15m 開盤區間，會用錯時鐘粒度。"""
        t0 = ny_ts(10, 0)
        hourly = [bar(t0 - 3600 * (20 - i), 100, 101, 99, 100) for i in range(20)]
        hits = classify_stock({
            "sym": "NVDAXUSDT",
            "now_ts": t0,
            "bars": hourly,
        })
        self.assertNotIn("ny_orb", keys_of(hits))


class OvernightAndWeekendTest(unittest.TestCase):
    def test_overnight_drift_skips_rth(self):
        """RTH 內的隔夜漂移是把開盤當成夜盤。"""
        ts = ny_ts(11, 0)  # 11:00 NY Friday = RTH
        bars = minute_bars(ts - 3600, 10, px0=100, step=0.5)
        hits = classify_stock({
            "sym": "TSLAXUSDT", "now_ts": ts, "bars_1m": bars, "bars": bars,
        })
        self.assertNotIn("overnight_drift", keys_of(hits))

    def test_overnight_drift_anchors_to_session_close(self):
        """關盤後必須錨今日收盤，不能用昨日開盤前的價把整段 RTH 算成隔夜。"""
        now = ny_ts(20, 5)
        bars = [
            bar(ny_ts(9, 30, day=20), 90, 91, 89, 90),
            bar(ny_ts(16, 0) - 60, 100, 100.2, 99.8, 100),
            bar(now - 60, 100.5, 100.7, 100.4, 100.6),
        ]
        hits = classify_stock({
            "sym": "TSLAXUSDT", "now_ts": now, "bars_1m": bars, "bars": bars,
        })
        self.assertEqual(sides(hits, "overnight_drift"), ["LONG"])
        rec = [h for h in hits if h["key"] == "overnight_drift"][0]
        self.assertAlmostEqual(rec["overnight_ret"], 0.006, places=3)

    def test_ndrf_skips_parked_funding_and_rth(self):
        """Gate 股票永續常停在 0.01% 預設資費，那不是擁擠。"""
        sat = int(datetime(2026, 8, 22, 12, 0, tzinfo=ZoneInfo("America/New_York")).timestamp())
        bars = minute_bars(sat - 600, 5, px0=100)
        parked = classify_stock({
            "sym": "TSLAXUSDT", "now_ts": sat, "bars": bars,
            "funding": 0.0001,
        })
        self.assertNotIn("overnight_fund_fade", keys_of(parked))
        real = classify_stock({
            "sym": "TSLAXUSDT", "now_ts": sat, "bars": bars,
            "funding": 0.0005,
        })
        self.assertEqual(sides(real, "overnight_fund_fade"), ["SHORT"])

    def test_weekend_converge_fades_not_holds(self):
        """與 S3 WKND 相反：週末上漲要淡、不是順勢抱到週日。"""
        fri_close = ny_ts(16, 0, day=21)
        sat = int(datetime(2026, 8, 22, 12, 0, tzinfo=ZoneInfo("America/New_York")).timestamp())
        bars = [bar(fri_close - 60, 100, 100.2, 99.8, 100, 10)]
        bars += minute_bars(sat - 300, 6, px0=102, step=0.1)
        hits = classify_stock({
            "sym": "NVDAXUSDT", "now_ts": sat, "bars_1m": bars, "bars": bars,
        })
        self.assertEqual(sides(hits, "weekend_converge"), ["SHORT"])

    def test_gap_fill_fades_open_gap_in_first_30m(self):
        """開盤跳空要淡回前收，不是當突破追。30 分鐘後不再新開。"""
        t0 = ny_ts(9, 32)
        bars = [bar(ny_ts(16, 0, day=20) - 60, 100, 100.1, 99.9, 100)]
        bars += minute_bars(ny_ts(9, 30), 5, px0=99.0, step=0.0)
        hits = classify_stock({
            "sym": "TSLAXUSDT", "now_ts": t0, "bars_1m": bars, "bars": bars,
        })
        self.assertEqual(sides(hits, "gap_fill"), ["LONG"])
        late = classify_stock({
            "sym": "TSLAXUSDT", "now_ts": ny_ts(10, 5),
            "bars_1m": bars, "bars": bars,
        })
        self.assertNotIn("gap_fill", keys_of(late))


class VwapAndSeedTest(unittest.TestCase):
    def test_session_vwap_needs_1p2_not_24h(self):
        t0 = ny_ts(10, 0)
        bars = minute_bars(ny_ts(9, 30), 40, px0=100, step=0.0)
        near = classify_stock({
            "sym": "AAPLXUSDT", "now_ts": t0, "px": 101.0,
            "bars_1m": bars, "bars": bars,
        })
        self.assertNotIn("session_vwap", keys_of(near))
        far = classify_stock({
            "sym": "AAPLXUSDT", "now_ts": t0, "px": 102.0,
            "bars_1m": bars, "bars": bars,
        })
        self.assertEqual(sides(far, "session_vwap"), ["SHORT"])

    def test_seed_board_has_no_players_and_mstr_is_stock(self):
        """ROSTER-only 進 registered；MSTR 是股票代幣不是山寨。"""
        board = seed_board()
        self.assertEqual(board["players"], [])
        self.assertEqual(board["kickoff"], STOCK_KICKOFF_TS)
        self.assertNotEqual(board["kickoff"], S3_KICKOFF_TS)
        self.assertEqual(len(board["registered"]), 12)
        self.assertTrue(is_stock_token("MSTRXUSDT"))
        self.assertIn("MSTRX", UNIVERSE)
        self.assertNotIn("BTC", UNIVERSE)

    def test_controls_retag_off_s3_keys(self):
        """對照組若原樣吐 wavetrend_cross / turtle_soup，S3 與場外會搶同一 key。"""
        bars = [bar(1_700_000_000 + i * 3600, 100, 101, 99.5, 100) for i in range(20)]
        bars.append(bar(1_700_000_000 + 20 * 3600, 100, 100.2, 98, 99))
        bars.append(bar(1_700_000_000 + 21 * 3600, 99, 101, 99, 100.5))
        hits = classify_stock({
            "sym": "NVDAXUSDT",
            "now_ts": ny_ts(10, 0),
            "bars": bars,
        })
        self.assertTrue(keys_of(hits).issubset(set(STOCK_KEYS)))
        self.assertNotIn("turtle_soup", keys_of(hits))
        self.assertNotIn("wavetrend_cross", keys_of(hits))
        self.assertEqual(sides(hits, "eq_turtle_soup"), ["LONG"])


class RecordPathTest(unittest.TestCase):
    def test_record_is_noop_without_live_dir(self):
        """單元測試目錄不是 collector。寫檔會把 hits 當測試副作用。"""
        self.assertIsNone(os.environ.get("STOCK_ARENA_DIR"))
        self.assertEqual(record_stock_row({"sym": "NVDAXUSDT"}), [])

    def test_record_writes_only_stock_keys(self):
        t0 = ny_ts(9, 30)
        later = minute_bars(t0, 20, px0=100, step=0.0)
        later[-1] = bar(t0 + 19 * 60, 100.2, 102.5, 100.1, 102.2, 30)
        row = {
            "sym": "NVDAXUSDT",
            "now_ts": t0 + 19 * 60,
            "bars_1m": later,
            "bars": later,
        }
        prev = os.environ.get("STOCK_ARENA_DIR")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["STOCK_ARENA_DIR"] = tmp
            try:
                hits = record_stock_row(row)
                self.assertIn("ny_orb", keys_of(hits))
                self.assertTrue(keys_of(hits).issubset(set(STOCK_KEYS)))
                path = os.path.join(tmp, "stock_hits.jsonl")
                self.assertTrue(os.path.isfile(path))
                self.assertEqual(record_stock_row({"sym": "BTCUSDT", "bars": later}), [])
            finally:
                if prev is None:
                    os.environ.pop("STOCK_ARENA_DIR", None)
                else:
                    os.environ["STOCK_ARENA_DIR"] = prev


if __name__ == "__main__":
    unittest.main()
