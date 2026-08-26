"""策略上線前哨兵：樣本不足／實盤劣於宣傳數字的判斷邏輯。

背景（2026-08-26）：主買狂潮/BTC閘門動能/鯨魚雙吸上線時只有「本季」回測數字撐場，
沒有樣本門檻就開真倉，虧到才發現全敗。這裡驗證的是「為什�麼要示警」，
不是單純跑一次函式看回什麼。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

_SPEC = importlib.util.spec_from_file_location(
    "strategy_live_guard",
    os.path.join(ROOT, "百貨公司", "services", "jackbot", "strategy_live_guard.py"),
)
guard = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(guard)


REF = {"backtest_wr": 62.6, "backtest_avgR": 1.132}  # 鯨魚雙吸「本季」數字


class ClassifySeverityTests(unittest.TestCase):
    def test_disabled_strategy_never_alerts(self):
        """帳號已停用（暫停量化）就不該再對它示警，不然吵得沒意義。"""
        stats = {"n": 0, "wr": 0.0, "avg_r": -1.0}
        severity, reason = guard.classify_severity(stats, REF, enabled=False)
        self.assertEqual(severity, "ok")
        self.assertEqual(reason, "")

    def test_enabled_with_thin_sample_warns_before_losing_money(self):
        """這是本次事件的核心：TKUP/BTCR/WHAL 上線時樣本 1~5 筆就已滿倉，
        必須在虧錢之前就被標記為「樣本不足」，不能等結案數字算出來才知道。"""
        stats = {"n": 4, "wr": 100.0, "avg_r": 2.0}  # 就算目前看起來很猛也要示警
        severity, reason = guard.classify_severity(stats, REF, enabled=True)
        self.assertEqual(severity, "warn")
        self.assertIn("樣本不足", reason)

    def test_enabled_with_large_sample_and_zero_winrate_fails(self):
        """複現鯨魚雙吸的實際狀況：n=5 全敗，明顯劣於「本季 62.6%」的宣傳數字。"""
        stats = {"n": 5, "wr": 0.0, "avg_r": -1.0}
        severity, reason = guard.classify_severity(stats, REF, enabled=True)
        self.assertEqual(severity, "fail")
        self.assertIn("建議人工複核", reason)

    def test_enabled_with_healthy_large_sample_stays_ok(self):
        """資費反殺這種長期正期望策略，即使近期連虧也不該被這個哨兵誤殺。"""
        stats = {"n": 166, "wr": 40.4, "avg_r": 0.184}
        ref = {"backtest_wr": 52.6, "backtest_avgR": 0.32}
        severity, reason = guard.classify_severity(stats, ref, enabled=True)
        self.assertEqual(severity, "ok")

    def test_borderline_sample_not_yet_enough_for_judgement(self):
        """樣本剛好卡在「能不能判斷」門檻之間時，只該提示樣本不足，
        不該同時又跳出「劣於宣傳數字」的複核警告（避免同一件事重複兩條訊息）。"""
        stats = {"n": 4, "wr": 0.0, "avg_r": -1.0}
        severity, reason = guard.classify_severity(stats, REF, enabled=True)
        self.assertEqual(severity, "warn")
        self.assertIn("樣本不足", reason)
        self.assertNotIn("複核", reason)


if __name__ == "__main__":
    unittest.main()
