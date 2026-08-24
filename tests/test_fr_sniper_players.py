"""新選手推播：大戶純空預設關閉、四支選手走 FRS 文案＋勝率。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import fr_sniper_push as frs  # noqa: E402
import frs_settle  # noqa: E402


NOW = 1_700_000_000


class WhaleShortOffTests(unittest.TestCase):
    def test_whale_short_is_off_by_default(self):
        """實盤全期負期望，不能再把大戶純空射進頻道。"""
        self.assertFalse(frs.WHALE_SHORT_ON)
        self.assertEqual(frs.whale_short_candidates(), [])


class PlayerClassifyTests(unittest.TestCase):
    def test_four_new_players_from_multilens_row(self):
        """只有四支指定選手、且必須做多，才會變成可推播訊號。"""
        row = {
            "strategy": "oi_taker_breakout_q",
            "side": "LONG",
            "sym": "SOLUSDT",
            "vol24_m": 80,
            "price": 150,
        }
        sig = frs.player_sig_from_row(row)
        self.assertEqual(sig["name"], "突破手·品質")
        self.assertTrue(sig["fresh"])
        self.assertEqual(sig["side"], "LONG")

    def test_rejects_short_and_unknown_and_thin_volume(self):
        """空單、未知策略、流動性不足都不能混進新選手推播。"""
        self.assertIsNone(frs.player_sig_from_row({
            "strategy": "oi_taker_breakout_q", "side": "SHORT",
            "sym": "SOLUSDT", "vol24_m": 80,
        }))
        self.assertIsNone(frs.player_sig_from_row({
            "strategy": "whale_pure_short_opt", "side": "SHORT",
            "sym": "SOLUSDT", "vol24_usd": 50_000_000,
        }))
        self.assertIsNone(frs.player_sig_from_row({
            "strategy": "taker_surge_long", "side": "LONG",
            "sym": "SOLUSDT", "vol24_m": 1,
        }))
        self.assertIsNone(frs.player_sig_from_row({
            "strategy": "lowcap_momo_long", "side": "LONG",
            "sym": "ACEUSDT", "vol24_m": 80,
        }))

    def test_snapshot_picks_all_four_and_skips_stale(self):
        """一份新鮮 snapshot 抽出四支；過期 snapshot 整包丟棄。"""
        rows = []
        for key, sym in (
            ("oi_taker_breakout_q", "SOLUSDT"),
            ("taker_surge_long", "DOGEUSDT"),
            ("btc_regime_momo_long", "SUIUSDT"),
            ("whale_accum_long", "LINKUSDT"),
        ):
            rows.append({
                "strategy": key, "side": "LONG", "sym": sym,
                "vol24_m": 40, "price": 10,
            })
        snap = {"ts": NOW, "rows": rows}
        got = frs.player_candidates_from_snap(snap, now=NOW, price_fn=lambda b, r: 10)
        names = {c[2]["name"] for c in got}
        self.assertEqual(names, {"突破手·品質", "主買狂潮", "BTC閘門動能", "鯨魚雙吸"})
        stale = dict(snap)
        stale["ts"] = NOW - 4000
        self.assertEqual(frs.player_candidates_from_snap(stale, now=NOW), [])


class MessageFormatTests(unittest.TestCase):
    def test_fresh_player_caption_matches_frs_spec(self):
        """用戶要的規格：🎯 新訊號、新選手備註、勝率、追蹤器。"""
        sig = {
            "side": "LONG", "name": "突破手·品質", "fresh": True, "exp": False,
            "reason": "品質濾網突破",
            "winrate": "擂台本季：勝率 60.3%（n=68）｜avgR +0.719｜MDD -10.8%",
        }
        text = frs.fmt("SOLUSDT", 150, sig, sl=145, tp1=157.5, tp2=162.5)
        self.assertIn("🎯 新訊號 ·「突破手·品質」", text)
        self.assertIn("🆕 新選手訊號", text)
        self.assertIn("🟢 做多", text)
        self.assertIn("`SOLUSDT`", text)
        self.assertIn("勝率 60.3%", text)
        self.assertIn("🛡 系統自動追蹤，到價會再提醒", text)
        self.assertNotIn("大戶純空", text)

    def test_frs_long_caption_has_no_newcomer_badge(self):
        """資費反殺維持原格式，不能被標成新選手。"""
        sig = {"side": "LONG", "name": "資費反殺", "exp": False,
               "reason": "空頭擁擠", "winrate": "實盤累積：勝率 52.0%｜結案 157 筆"}
        text = frs.fmt("ETHUSDT", 3500, sig, sl=3400, tp1=3650, tp2=3750)
        self.assertIn("🎯 新訊號 ·「資費反殺」", text)
        self.assertNotIn("🆕 新選手訊號", text)

    def test_arena_winrate_line_before_live_samples(self):
        """還沒有實盤結案時，必須帶擂台勝率，不能空白。"""
        line = frs.format_live_winrate("突破手·品質")
        self.assertIn("勝率 60.3%", line)
        self.assertIn("n=68", line)
        self.assertIn("+0.719", line)


class SettleNoticeTests(unittest.TestCase):
    def test_tp_notice_names_strategy(self):
        """結案提醒要讓半自動跟單知道哪一支出場。"""
        rec = {"strategy": "主買狂潮", "sym": "DOGEUSDT", "side": "LONG"}
        msg = frs_settle.settle_notice(rec, "win")
        self.assertIn("追蹤結案 ·「主買狂潮」", msg)
        self.assertIn("止盈", msg)
        self.assertIn("DOGEUSDT", msg)


class FundPoolTests(unittest.TestCase):
    def test_fund_pool_is_five_player_signals(self):
        """實盤策略池只掛五支選手；大戶純空必須消失。"""
        js = open(os.path.join(ROOT, "arena_web", "js", "fund.js"), encoding="utf-8").read()
        html = open(os.path.join(ROOT, "arena_web", "fund.html"), encoding="utf-8").read()
        for code in ("SMCP", "BRKq", "TKUP", "BTCR", "WHAL"):
            self.assertIn('code: "%s"' % code, js)
        self.assertLess(js.index('code: "SMCP"'), js.index('code: "BRKq"'))
        self.assertNotIn('name: "大戶純空"', js)
        self.assertNotIn('code: "WHS"', js)
        self.assertIn("已停推", html)
        self.assertIn("新選手", js)
        self.assertIn("小盤妖股", html)
        self.assertIn("大戶純空已停推", html)
        self.assertEqual(js.count("fresh: true"), 4)


if __name__ == "__main__":
    unittest.main()
