#!/usr/bin/env python3
"""Patch the *currently served* war-room app for 股票場外賽.

blackstockai.com proxies /war-room/apps/p-6e6dee8f to the partner-app
Node process (PORT=4196). That process serves
``releases/<id>/`` (also the ``repo`` symlink), NOT ``releases/current``.

Copying repo arena_web/index.html onto the live tree would wipe 第3季 /
潛力訊號組. This script only:

  * copies stock.html
  * inserts one nav button on the live index
  * surgically patches live app.js / strategies.js
  * writes data/stock_arena.json next to live arena.json

No WR_TOKEN / push-once. No overwrite of server.js or S3 pages.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_BOT = os.path.join(_REPO, "量化與交易機器人")
if os.path.isdir(_BOT) and _BOT not in sys.path:
    sys.path.insert(0, _BOT)
try:
    from stock_arena_report import seed_board
except ImportError:
    seed_board = None  # type: ignore

APP_ROOT = "/data/partner-apps/p-6e6dee8f"
STOCK_BIDIR = (
    "NYOR", "HKOR", "KROR", "GAPF", "ONDR", "WKCV", "SVWP", "NDRF",
    "EWTR", "ETSU", "ENR7", "ESTR",
)
STOCK_STRATEGIES_MARKER = "// --- STOCK_SIDE_STRATEGIES_BEGIN ---"
STOCK_STRATEGIES_END = "// --- STOCK_SIDE_STRATEGIES_END ---"
CRON_MARK_BEGIN = "# --- STOCK_ARENA_JSON_SYNC ---"
CRON_MARK_END = "# --- STOCK_ARENA_JSON_SYNC_END ---"


def looks_live_s3(html: str) -> bool:
    """True for the public 第三季 board (not the in-repo simplified index)."""
    return ("第3季" in html or "第三季" in html) and "潛力訊號組" in html


def detect_live_release() -> str | None:
    # Ground truth: the Node process war-room proxies to (PORT=4196).
    cwd = _docker_port_cwd("platform-war-room", "4196")
    if cwd and os.path.isfile(os.path.join(cwd, "index.html")):
        return cwd

    env = os.environ.get("WR_RELEASE")
    if env and os.path.isfile(os.path.join(env, "index.html")):
        html = open(os.path.join(env, "index.html"), encoding="utf-8").read()
        if looks_live_s3(html):
            return env

    repo = os.path.join(APP_ROOT, "repo")
    if os.path.islink(repo) or os.path.isdir(repo):
        target = os.path.realpath(repo)
        idx = os.path.join(target, "index.html")
        if os.path.isfile(idx) and looks_live_s3(open(idx, encoding="utf-8").read()):
            return target

    rel = os.path.join(APP_ROOT, "releases")
    if os.path.isdir(rel):
        scored = []
        for name in os.listdir(rel):
            path = os.path.join(rel, name)
            idx = os.path.join(path, "index.html")
            if not os.path.isfile(idx):
                continue
            html = open(idx, encoding="utf-8").read()
            if looks_live_s3(html):
                scored.append(path)
        if len(scored) == 1:
            return scored[0]
        if scored:
            scored.sort(key=lambda p: os.path.getmtime(os.path.join(p, "index.html")), reverse=True)
            return scored[0]
    return None


def _docker_port_cwd(container: str, port: str) -> str | None:
    script = (
        'for d in /proc/[0-9]*; do '
        '[ -r "$d/environ" ] || continue; '
        'if tr "\\0" "\\n" < "$d/environ" 2>/dev/null | grep -qx "PORT=%s"; then '
        'readlink -f "$d/cwd"; exit 0; fi; done; exit 1' % port
    )
    try:
        r = subprocess.run(
            ["docker", "exec", container, "sh", "-c", script],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    path = (r.stdout or "").strip().splitlines()
    return path[-1] if path else None


def patch_index(html: str) -> str:
    if re.search(r'href="stock\.html"', html):
        return html
    needle = '<a class="btn btn-vip" href="vip.html">'
    insert = (
        '<a class="btn btn-vip" href="stock.html">🏛️ 股票場外賽</a>\n'
        "        " + needle
    )
    if needle not in html:
        return html
    return html.replace(needle, insert, 1)


def _patch_bidir(js: str) -> str:
    m = re.search(r"var BIDIR = \{[^}]*\}", js)
    if not m:
        return js
    body = m.group(0)
    if "NYOR:" in body:
        return js
    inner = body[:-1].rstrip()
    if not inner.endswith(","):
        inner += ","
    extra = " " + ", ".join("%s: 1" % c for c in STOCK_BIDIR) + " }"
    return js[: m.start()] + inner + extra + js[m.end() :]


def _patch_cat_style(js: str) -> str:
    if 'c.indexOf("場外")' in js:
        return js
    needle = '    if (c.indexOf("補選") >= 0) return { emoji: "🆕", bg: "#6c8cff" };'
    insert = (
        needle + "\n"
        '    if (c.indexOf("場外對照") >= 0) return { emoji: "🪞", bg: "#7a8aa0" };\n'
        '    if (c.indexOf("場外") >= 0) return { emoji: "🏛️", bg: "#c4a35a" };'
    )
    if needle not in js:
        return js
    return js.replace(needle, insert, 1)


def _patch_load(js: str) -> str:
    if "ARENA_DATA_URL" in js and "ARENA_SHOW_REGISTERED" in js:
        return js
    old = (
        "  function load(file) {\n"
        "    if (file) state.dataFile = file;\n"
        '    return fetch("data/" + state.dataFile + "?t=" + Date.now())\n'
        "      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })\n"
        "      .then(function (d) {\n"
        "        state.players = (d.players || []).slice()\n"
        "          .map(inferTier)\n"
        "          .sort(function (a, b) { return b.equity_live - a.equity_live; });\n"
        "        state.registered = d.registered || [];\n"
    )
    new = (
        "  function load(file) {\n"
        "    if (file) state.dataFile = file;\n"
        "    var url = window.ARENA_DATA_URL || (\"data/\" + state.dataFile);\n"
        "    return fetch(url + (url.indexOf(\"?\") >= 0 ? \"&\" : \"?\") + \"t=\" + Date.now())\n"
        "      .then(function (r) {\n"
        "        if (!r.ok) {\n"
        "          if (window.ARENA_ALLOW_EMPTY && window.ARENA_EMPTY_BOARD) return window.ARENA_EMPTY_BOARD;\n"
        "          throw new Error(r.status);\n"
        "        }\n"
        "        return r.json();\n"
        "      })\n"
        "      .then(function (d) {\n"
        "        state.players = (d.players || []).slice()\n"
        "          .map(inferTier)\n"
        "          .sort(function (a, b) { return b.equity_live - a.equity_live; });\n"
        "        state.registered = d.registered || [];\n"
        "        if (!state.players.length && state.registered.length &&\n"
        "            (window.ARENA_SHOW_REGISTERED || /stock\\.html/i.test(location.pathname))) {\n"
        "          state.players = state.registered.map(function (r) {\n"
        "            return inferTier({\n"
        "              name: r.name, code: r.code, cat: r.cat, key: r.key,\n"
        "              equity_live: CAPITAL, roi: 0, n: 0, wr: 0, avg_R: 0, total_R: 0,\n"
        "              open_n: 0, pf: 0, mdd: 0, history: [], open_positions: []\n"
        "            });\n"
        "          });\n"
        "        }\n"
    )
    if old not in js:
        return js
    return js.replace(old, new, 1)


def _patch_boot(js: str) -> str:
    if "stock_arena.json" in js and "function boot" in js:
        if "stock\\.html" in js or "stock.html" in js:
            return js
    needle = "  function boot() {\n"
    insert = (
        "  function boot() {\n"
        "    if (/stock\\.html/i.test(location.pathname)) {\n"
        '      state.dataFile = "stock_arena.json";\n'
        "    }\n"
    )
    if needle not in js:
        return js
    return js.replace(needle, insert, 1)


def patch_app_js(js: str) -> str:
    js = _patch_bidir(js)
    js = _patch_cat_style(js)
    js = _patch_load(js)
    js = _patch_boot(js)
    return js


def stock_strategies_snippet(staging_arena: str) -> str:
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


def patch_strategies_js(js: str, snippet: str) -> str:
    if not snippet:
        return js
    if "NYOR:" in js:
        return js
    if STOCK_STRATEGIES_MARKER in js:
        return js
    anchor = "  // ── 衛冕對照"
    if anchor in js:
        return js.replace(anchor, snippet + "\n\n" + anchor, 1)
    return re.sub(r"\n};\s*$", "\n" + snippet + "\n};\n", js, count=1)


def _seed_board(staging: str):
    if seed_board is None:
        raise ImportError("stock_arena_report.seed_board unavailable (staging=%s)" % staging)
    return seed_board()


def _write_json(path: str, board) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(board, f, ensure_ascii=False)


def _backup(path: str, bak_dir: str) -> None:
    if not os.path.isfile(path):
        return
    os.makedirs(bak_dir, exist_ok=True)
    shutil.copy2(path, os.path.join(bak_dir, os.path.basename(path)))


def patch_arena_web_update(path: str = "/usr/local/bin/arena_web_update.sh") -> str:
    if not os.path.isfile(path):
        return "missing"
    text = open(path, encoding="utf-8").read()
    if CRON_MARK_BEGIN in text:
        return "already"
    snippet = """
%s
if [ -f $CC/stock_arena.json ]; then
  cp -f $CC/stock_arena.json $APP/repo/data/stock_arena.json
  mkdir -p $APP/data $APP/releases/current/data
  cp -f $CC/stock_arena.json $APP/data/stock_arena.json
  cp -f $CC/stock_arena.json $APP/releases/current/data/stock_arena.json
fi
%s
""" % (CRON_MARK_BEGIN, CRON_MARK_END)
    with open(path, "a", encoding="utf-8") as f:
        if not text.endswith("\n"):
            f.write("\n")
        f.write(snippet)
    return "patched"


def extra_data_targets(live: str) -> list[str]:
    out = [
        os.path.join(live, "data", "stock_arena.json"),
        os.path.join(APP_ROOT, "data", "stock_arena.json"),
        os.path.join(APP_ROOT, "releases", "current", "data", "stock_arena.json"),
        os.path.join(APP_ROOT, "repo", "data", "stock_arena.json"),
        "/root/unified-platform/services/jackbot/data/crit_collector/stock_arena.json",
        "/app/data/crit_collector/stock_arena.json",
    ]
    seen = set()
    uniq = []
    for p in out:
        rp = os.path.realpath(p) if os.path.isdir(os.path.dirname(p) or ".") else p
        if rp in seen:
            continue
        seen.add(rp)
        uniq.append(p)
    return uniq


def apply_to_live(live: str, staging: str) -> dict:
    staging_arena = os.path.join(staging, "arena_web")
    bak = os.path.join(live, ".bak_stock_side")
    report = {"live": live, "copied": [], "patched": [], "json": [], "notes": []}

    idx = os.path.join(live, "index.html")
    if not os.path.isfile(idx):
        raise SystemExit("LIVE_INDEX_MISSING " + idx)
    html = open(idx, encoding="utf-8").read()
    if not looks_live_s3(html) and "stock.html" not in html:
        raise SystemExit("REFUSE_OVERWRITE not a 第三季 live index: " + idx)

    stock_src = os.path.join(staging_arena, "stock.html")
    if os.path.isfile(stock_src):
        shutil.copy2(stock_src, os.path.join(live, "stock.html"))
        report["copied"].append("stock.html")
    else:
        report["notes"].append("missing staging stock.html")

    _backup(idx, bak)
    new_html = patch_index(html)
    if new_html != html:
        open(idx, "w", encoding="utf-8").write(new_html)
        report["patched"].append("index.html")
    else:
        report["notes"].append("index already had stock.html or vip needle missing")

    app_path = os.path.join(live, "js", "app.js")
    if os.path.isfile(app_path):
        _backup(app_path, bak)
        old = open(app_path, encoding="utf-8").read()
        new = patch_app_js(old)
        if new != old:
            open(app_path, "w", encoding="utf-8").write(new)
            report["patched"].append("js/app.js")
            if "ARENA_DATA_URL" not in new:
                report["notes"].append("app.js load() pattern mismatch — stock page may still hit arena.json")
        else:
            report["notes"].append("app.js unchanged (already patched or pattern mismatch)")
    else:
        report["notes"].append("missing js/app.js")

    snippet = stock_strategies_snippet(staging_arena)
    strat_path = os.path.join(live, "js", "strategies.js")
    if os.path.isfile(strat_path) and snippet:
        _backup(strat_path, bak)
        old = open(strat_path, encoding="utf-8").read()
        new = patch_strategies_js(old, snippet)
        if new != old:
            open(strat_path, "w", encoding="utf-8").write(new)
            report["patched"].append("js/strategies.js")
    elif not snippet:
        report["notes"].append("no stock strategies snippet")

    try:
        board = _seed_board(staging)
    except Exception as exc:
        report["notes"].append("seed_board failed: %s" % exc)
        board = None
    if board is not None:
        n = len(board.get("registered") or [])
        if n != 12:
            report["notes"].append("registered count %s (want 12)" % n)
        for path in extra_data_targets(live):
            parent = os.path.dirname(path)
            if parent and not os.path.isdir(parent):
                # Never mkdir container paths on the host (would create /app/...).
                if parent.startswith("/app/"):
                    continue
                try:
                    os.makedirs(parent, exist_ok=True)
                except OSError:
                    continue
            try:
                _write_json(path, board)
                report["json"].append(path)
            except OSError as exc:
                report["notes"].append("json skip %s %s" % (path, exc))

    report["cron"] = patch_arena_web_update()
    return report


def main(argv=None) -> int:
    argv = list(argv or sys.argv[1:])
    staging = argv[0] if argv else os.environ.get("EXTRA_SRC", "/root/deploy-staging")
    live = detect_live_release()
    if not live:
        print("LIVE_RELEASE_NOT_FOUND")
        return 1
    report = apply_to_live(live, staging)
    print("LIVE_STOCK_OK", json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
