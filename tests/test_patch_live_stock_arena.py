"""Live 場外賽 patch must keep 第三季 UI and still load stock_arena.json."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from patch_live_stock_arena import (  # noqa: E402
    copy_fund_pool,
    looks_live_s3,
    patch_app_js,
    patch_index,
    patch_strategies_js,
    stock_strategies_snippet,
)

LIVE_INDEX = """<!DOCTYPE html>
<html lang="zh-Hant">
<body>
      <div class="topbar-actions">
        <button id="season-s3" class="btn btn-wood season-btn is-active" type="button" data-season="3">第3季</button>
        <button id="season-s2" class="btn btn-wood season-btn" type="button" data-season="2">第2季</button>
        <button id="season-s1" class="btn btn-wood season-btn" type="button" data-season="1">第1季</button>
        <a class="btn btn-vip" href="vip.html">🧪 VIP 實驗室</a>
        <a class="btn btn-vip" href="fund.html">🧭 實盤策略池</a>
        <a class="btn btn-vip" href="trader-performance.html">📊 潛力訊號組</a>
        <a class="btn btn-vip" href="volume.html">💼 代理小金庫</a>
        <button id="refresh" class="btn btn-wood" type="button">↻ 重新整理</button>
      </div>
</body>
</html>
"""

REPO_INDEX = """<!DOCTYPE html>
<html><body>
  <h1>影子交易擂台</h1>
  <a class="btn btn-vip" href="vip.html">VIP</a>
</body></html>
"""

LIVE_APP = """
  var state = { dataFile: "arena.json", seasonView: 3 };
  var BIDIR = { CTRN: 1, RADAR: 1, SNIPE: 1, NR7I: 1 };
  function catStyle(cat) {
    var c = cat || "";
    if (c.indexOf("補選") >= 0) return { emoji: "🆕", bg: "#6c8cff" };
    return { emoji: "🧗", bg: "#67ad3e" };
  }
  function load(file) {
    if (file) state.dataFile = file;
    return fetch("data/" + state.dataFile + "?t=" + Date.now())
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) {
        state.players = (d.players || []).slice()
          .map(inferTier)
          .sort(function (a, b) { return b.equity_live - a.equity_live; });
        state.registered = d.registered || [];
        state.asOf = d.as_of || 0;
      });
  }
  function setSeasonView(n) {
    state.seasonView = n;
    state.dataFile = n === 1 ? "arena_s1.json" : (n === 2 ? "arena_s2.json" : "arena.json");
    return doRefresh(true);
  }
  function boot() {
    document.getElementById("refresh").addEventListener("click", function () { doRefresh(true); });
  }
"""

LIVE_STRAT = """
  NR7I: { tag: "NR7伸展",
    logic: "x",
    exit: "y",
    reverse: "z。" },

  // ── 衛冕對照（不參與獎項）──
  RADAR: { tag: "爆擊雷達（衛冕對照）",
    logic: "現役一級雷達",
    reverse: "依雷達原生邏輯換手。" },
};
"""

STAGING_STRAT = """
// ── 股票代幣場外賽（SHADOW；獨立榜 data/stock_arena.json；無 TG）──
  NYOR: { tag: "美股開盤區間",
    logic: "只打美股永續。",
    exit: "16:45 ET 必平。",
    reverse: "當日反向突破區間視為換倉。" },
  ESTR: { tag: "三重ST對照",
    logic: "三重 Supertrend。",
    exit: "任一 ST 反轉。",
    reverse: "三組全翻向。" },
"""


class LiveStockPatchTests(unittest.TestCase):
    def test_looks_live_rejects_repo_index(self):
        """Repo 簡化 index 沒有第3季按鈕，不能當成線上頁去覆寫。"""
        self.assertTrue(looks_live_s3(LIVE_INDEX))
        self.assertFalse(looks_live_s3(REPO_INDEX))

    def test_patch_index_keeps_season_and_potential_group(self):
        """場外賽連結加在 VIP 前面；第3季／潛力訊號組必須還在。"""
        out = patch_index(LIVE_INDEX)
        self.assertIn('href="stock.html">🏛️ 股票場外賽', out)
        self.assertIn("第3季", out)
        self.assertIn("第2季", out)
        self.assertIn("第1季", out)
        self.assertIn("潛力訊號組", out)
        self.assertEqual(out.count('href="vip.html"'), 1)
        self.assertEqual(patch_index(out), out)

    def test_patch_app_js_stock_file_does_not_break_season_switch(self):
        """stock.html 改讀 stock_arena.json；S3 的 setSeasonView 仍切 arena_s1/s2。"""
        out = patch_app_js(LIVE_APP)
        self.assertIn("NYOR: 1", out)
        self.assertIn("場外對照", out)
        self.assertIn("ARENA_DATA_URL", out)
        self.assertIn('state.dataFile = "stock_arena.json"', out)
        self.assertIn("setSeasonView", out)
        self.assertIn('n === 1 ? "arena_s1.json"', out)
        self.assertIn("ARENA_SHOW_REGISTERED", out)
        self.assertEqual(patch_app_js(out), out)

    def test_patch_strategies_inserts_before_defending_champs(self):
        """股票說明插在衛冕對照前面，不刪 RADAR。"""
        with tempfile.TemporaryDirectory() as tmp:
            arena = os.path.join(tmp, "js")
            os.makedirs(arena)
            with open(os.path.join(arena, "strategies.js"), "w", encoding="utf-8") as f:
                f.write(STAGING_STRAT)
            snippet = stock_strategies_snippet(tmp)
        self.assertIn("NYOR:", snippet)
        self.assertIn("ESTR:", snippet)
        out = patch_strategies_js(LIVE_STRAT, snippet)
        self.assertLess(out.index("NYOR:"), out.index("衛冕對照"))
        self.assertIn("RADAR:", out)
        self.assertEqual(patch_strategies_js(out, snippet), out)

    def test_real_live_index_fixture_if_present(self):
        """若本機有從 Vultr 拉下的 live index，同樣不能丟掉第3季。"""
        path = os.environ.get("LIVE_INDEX_FIXTURE", "/tmp/live_index.html")
        if not os.path.isfile(path):
            self.skipTest("no live index fixture")
        html = open(path, encoding="utf-8").read()
        self.assertTrue(looks_live_s3(html))
        out = patch_index(html)
        self.assertIn("stock.html", out)
        self.assertIn("第3季", out)
        self.assertIn("潛力訊號組", out)
        self.assertIn("第三季品質濾網", out)

    def test_copy_fund_pool_overwrites_only_fund_pages(self):
        """實盤策略池可以整頁覆蓋；不能順便動 index（會洗掉第3季）。"""
        with tempfile.TemporaryDirectory() as tmp:
            staging = os.path.join(tmp, "staging")
            live = os.path.join(tmp, "live")
            os.makedirs(os.path.join(staging, "js"))
            os.makedirs(os.path.join(live, "js"))
            open(os.path.join(staging, "fund.html"), "w", encoding="utf-8").write("NEW FUND")
            open(os.path.join(staging, "js", "fund.js"), "w", encoding="utf-8").write("SMCP BRKq")
            open(os.path.join(live, "index.html"), "w", encoding="utf-8").write("KEEP INDEX")
            open(os.path.join(live, "fund.html"), "w", encoding="utf-8").write("OLD")
            copied = copy_fund_pool(live, staging)
            self.assertEqual(sorted(copied), ["fund.html", "js/fund.js"])
            self.assertEqual(open(os.path.join(live, "fund.html"), encoding="utf-8").read(), "NEW FUND")
            self.assertEqual(open(os.path.join(live, "index.html"), encoding="utf-8").read(), "KEEP INDEX")


if __name__ == "__main__":
    unittest.main()
