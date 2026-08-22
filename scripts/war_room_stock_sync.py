#!/usr/bin/env python3
"""Patch live war-room (blackstockai.com) for 股票場外賽 and push via WR API.

Direct cp to releases/current does NOT update what Next/nginx serves.
This mirrors the LIVE bundle, adds stock.html + data/stock_arena.json,
patches index/app/strategies without removing 第三季 UI, then push-once.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

PUBLIC_BASE = os.environ.get(
    "WR_PUBLIC_BASE",
    "https://blackstockai.com/war-room/apps/p-6e6dee8f",
).rstrip("/")

MIRROR_PATHS = (
    "index.html",
    "stock.html",
    "vip.html",
    "fund.html",
    "volume.html",
    "trader-performance.html",
    "css/style.css",
    "js/access_guard.js",
    "js/app.js",
    "js/strategies.js",
    "js/fund.js",
    "js/vip.js",
    "js/volume_live.js",
)

STOCK_BIDIR = (
    "NYOR", "HKOR", "KROR", "GAPF", "ONDR", "WKCV", "SVWP", "NDRF",
    "EWTR", "ETSU", "ENR7", "ESTR",
)

STOCK_STRATEGIES_MARKER = "// --- STOCK_SIDE_STRATEGIES_BEGIN ---"
STOCK_STRATEGIES_END = "// --- STOCK_SIDE_STRATEGIES_END ---"


def _fetch(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "jackbot-deploy/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                return None
            return resp.read()
    except Exception as exc:
        print("FETCH_FAIL", url, exc)
        return None


def _mirror_live(root: str) -> list[str]:
    got = []
    for rel in MIRROR_PATHS:
        url = f"{PUBLIC_BASE}/{rel}"
        data = _fetch(url)
        if not data:
            continue
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path) or root, exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        got.append(rel)
    return got


def _stock_strategies_snippet(staging_arena: str) -> str:
    src = os.path.join(staging_arena, "js", "strategies.js")
    if not os.path.isfile(src):
        return ""
    text = open(src, encoding="utf-8").read()
    m = re.search(
        r"// ── 股票代幣場外賽.*?(\n  NYOR:.*?ESTR:.*?\n    reverse:.*?。\"\s*\},)",
        text,
        re.S,
    )
    if not m:
        return ""
    return STOCK_STRATEGIES_MARKER + "\n" + m.group(1) + "\n" + STOCK_STRATEGIES_END


def _patch_index(html: str) -> str:
    if "stock.html" in html:
        return html
    needle = '<a class="btn btn-vip" href="vip.html">'
    insert = (
        '<a class="btn btn-vip" href="stock.html">🏛️ 股票場外賽</a>\n        '
        + needle
    )
    if needle not in html:
        return html
    return html.replace(needle, insert, 1)


def _patch_app_js(js: str) -> str:
    if "stock_arena.json" in js and "場外對照" in js:
        return js
    m = re.search(r"var BIDIR = \{([^}]+)\}", js)
    if m:
        body = m.group(1)
        for code in STOCK_BIDIR:
            if f"{code}:" not in body:
                body = body.rstrip().rstrip(",") + f", {code}: 1"
        js = js[: m.start(1)] + body + js[m.end(1) :]
    if "場外對照" not in js:
        js = js.replace(
            'if (c.indexOf("補選") >= 0) return { emoji: "🆕", bg: "#6c8cff" };',
            'if (c.indexOf("補選") >= 0) return { emoji: "🆕", bg: "#6c8cff" };\n'
            '    if (c.indexOf("場外對照") >= 0) return { emoji: "🪞", bg: "#7a8aa0" };\n'
            '    if (c.indexOf("場外") >= 0) return { emoji: "🏛️", bg: "#c4a35a" };',
            1,
        )
    if "stock_arena.json" not in js:
        js = js.replace(
            "function boot() {",
            'function boot() {\n'
            '    if (/stock\\.html/i.test(location.pathname)) state.dataFile = "stock_arena.json";',
            1,
        )
        js = js.replace(
            "state.registered = d.registered || [];",
            "state.registered = d.registered || [];\n"
            '        if (!state.players.length && state.registered.length && '
            '/stock\\.html/i.test(location.pathname)) {\n'
            "          state.players = state.registered.map(function (r) {\n"
            "            return inferTier({\n"
            "              name: r.name, code: r.code, cat: r.cat, key: r.key,\n"
            "              equity_live: CAPITAL, roi: 0, n: 0, wr: 0, avg_R: 0, total_R: 0,\n"
            "              open_n: 0, pf: 0, mdd: 0, history: [], open_positions: []\n"
            "            });\n"
            "          });\n"
            "        }",
            1,
        )
    return js


def _patch_strategies_js(js: str, snippet: str) -> str:
    if not snippet or "NYOR:" in js:
        return js
    if STOCK_STRATEGIES_MARKER in js:
        return js
    # Insert before 衛冕對照 block if present, else before closing };
    anchor = "  // ── 衛冕對照"
    if anchor in js:
        return js.replace(anchor, snippet + "\n\n" + anchor, 1)
    return re.sub(r"\n};\s*$", "\n" + snippet + "\n};\n", js, count=1)


def _seed_stock_json(dest: str, bot_dir: str) -> None:
    if bot_dir not in sys.path:
        sys.path.insert(0, bot_dir)
    from stock_arena_report import seed_board

    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(seed_board(), f, ensure_ascii=False)


def _read_env(path: str) -> dict[str, str]:
    out = {}
    if not os.path.isfile(path):
        return out
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main(argv=None) -> int:
    argv = list(argv or sys.argv[1:])
    staging = argv[0] if argv else os.environ.get("EXTRA_SRC", "/root/deploy-staging")
    staging_arena = os.path.join(staging, "arena_web")
    env_path = os.environ.get("UNIFIED_ENV", "/root/unified-platform/.env")
    env = _read_env(env_path)
    wr_token = os.environ.get("WR_TOKEN") or env.get("WR_TOKEN", "")
    wr_url = (os.environ.get("WR_URL") or env.get("WR_URL") or "https://blackstockai.com/war-room").rstrip("/")
    wr_slug = os.environ.get("WR_SLUG") or env.get("WR_SLUG") or "p-6e6dee8f"

    if not wr_token:
        print("WR_STOCK_SKIP no WR_TOKEN in env — blackstockai.com 需 war-room API 才會更新")
        return 0

    work = tempfile.mkdtemp(prefix="wr-stock-")
    try:
        mirrored = _mirror_live(work)
        print("WR_MIRROR", len(mirrored), mirrored[:8], "...")

        staging_stock = os.path.join(staging_arena, "stock.html")
        if os.path.isfile(staging_stock):
            shutil.copy2(staging_stock, os.path.join(work, "stock.html"))
        push_once_src = os.path.join(staging_arena, "push-once.cjs")
        if os.path.isfile(push_once_src):
            shutil.copy2(push_once_src, os.path.join(work, "push-once.cjs"))

        snippet = _stock_strategies_snippet(staging_arena)
        idx_path = os.path.join(work, "index.html")
        app_path = os.path.join(work, "js", "app.js")
        strat_path = os.path.join(work, "js", "strategies.js")
        if os.path.isfile(idx_path):
            open(idx_path, "w", encoding="utf-8").write(
                _patch_index(open(idx_path, encoding="utf-8").read())
            )
        if os.path.isfile(app_path):
            open(app_path, "w", encoding="utf-8").write(
                _patch_app_js(open(app_path, encoding="utf-8").read())
            )
        if os.path.isfile(strat_path) and snippet:
            open(strat_path, "w", encoding="utf-8").write(
                _patch_strategies_js(open(strat_path, encoding="utf-8").read(), snippet)
            )

        bot_dir = os.path.join(staging, "量化與交易機器人")
        if not os.path.isdir(bot_dir):
            bot_dir = os.path.join(os.path.dirname(staging), "量化與交易機器人")
        _seed_stock_json(os.path.join(work, "data", "stock_arena.json"), bot_dir)

        env_push = dict(os.environ)
        env_push.update({
            "WR_URL": wr_url,
            "WR_SLUG": wr_slug,
            "WR_TOKEN": wr_token,
            "WR_INSECURE": env_push.get("WR_INSECURE", "1"),
        })
        r = subprocess.run(
            ["node", "push-once.cjs"],
            cwd=work,
            env=env_push,
            capture_output=True,
            text=True,
        )
        print(r.stdout)
        if r.stderr:
            print(r.stderr, file=sys.stderr)
        if r.returncode != 0:
            print("WR_STOCK_FAIL exit", r.returncode)
            return r.returncode
        print("WR_STOCK_OK", wr_url + "/apps/" + wr_slug)
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
