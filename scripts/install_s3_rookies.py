#!/usr/bin/env python3
"""Merge S3 補選 modules into a live collector tree without overwriting veterans.

Copies sidecar files next to the live collector. If live arena_report.py /
_altsignal_collector_once.py / altsignal_replay_labeler.py exist, inserts a
marked hook. Never replaces those live files, never touches .env / risk /
gatekeeper / grade functions, never enables TG.
"""
from __future__ import annotations

import os
import shutil
import sys

MARKER_BEGIN = "# --- S3_ROOKIE_HOOK_BEGIN ---"
MARKER_END = "# --- S3_ROOKIE_HOOK_END ---"

ROSTER_HOOK = """
{begin}
try:
    from s3_rookie_hook import apply_rookie_roster
    ROSTER = apply_rookie_roster(ROSTER)
except Exception as _s3_rookie_exc:
    pass
{end}
""".format(begin=MARKER_BEGIN, end=MARKER_END)

CLASSIFY_HOOK = """
{begin}
try:
    from s3_rookie_hook import extend_hits as _s3_extend_hits
    _s3_row = locals().get("row") or locals().get("rec") or locals().get("item")
    if _s3_row is not None:
        if "hits" in locals():
            hits = _s3_extend_hits(hits, _s3_row)
        elif "out" in locals():
            out = _s3_extend_hits(out, _s3_row)
except Exception:
    pass
{end}
""".format(begin=MARKER_BEGIN, end=MARKER_END)

EXIT_HOOK = """
{begin}
try:
    from s3_rookie_hook import apply_rookie_exits
    EXIT_PROFILE = apply_rookie_exits(EXIT_PROFILE)
except Exception:
    pass
{end}
""".format(begin=MARKER_BEGIN, end=MARKER_END)

SEARCH_DIRS = [
    "/app/data/crit_collector",
    "/root/unified-platform/services/jackbot/data/crit_collector",
    "/root/量化與交易機器人",
    "/root/crypto-tool-helper/量化與交易機器人",
]


def _repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _src_files():
    root = _repo_root()
    return {
        "s3_rookie_roster.py": os.path.join(root, "量化與交易機器人", "arena_report.py"),
        "s3_rookie_classify.py": os.path.join(root, "量化與交易機器人", "_altsignal_collector_once.py"),
        "s3_rookie_exits.py": os.path.join(root, "backtest", "engine", "altsignal_replay_labeler.py"),
        "s3_rookie_promote.py": os.path.join(root, "量化與交易機器人", "arena_promote.py"),
        "s3_rookie_hook.py": os.path.join(root, "量化與交易機器人", "s3_rookie_hook.py"),
        "arena_report.py": os.path.join(root, "量化與交易機器人", "arena_report.py"),
        "_altsignal_collector_once.py": os.path.join(root, "量化與交易機器人", "_altsignal_collector_once.py"),
        "arena_promote.py": os.path.join(root, "量化與交易機器人", "arena_promote.py"),
    }


def _find_live_dir(explicit=None):
    cands = []
    if explicit:
        cands.append(explicit)
    cands.extend(SEARCH_DIRS)
    cands.append(os.path.join(_repo_root(), "量化與交易機器人"))
    for d in cands:
        if d and os.path.isdir(d):
            if os.path.isfile(os.path.join(d, "arena_report.py")) or os.path.isfile(
                os.path.join(d, "_altsignal_collector_once.py")
            ) or os.path.basename(d) == "量化與交易機器人":
                return d
    return None


def _copy_sidecars(dest):
    srcs = _src_files()
    copied = []
    # Sidecars only — never overwrite a live veteran file that is not a sidecar.
    sidecar_names = (
        "s3_rookie_roster.py",
        "s3_rookie_classify.py",
        "s3_rookie_exits.py",
        "s3_rookie_promote.py",
        "s3_rookie_hook.py",
    )
    os.makedirs(dest, exist_ok=True)
    for name in sidecar_names:
        src = srcs[name]
        if not os.path.isfile(src):
            continue
        dst = os.path.join(dest, name)
        shutil.copy2(src, dst)
        copied.append(dst)
    # Also drop classify/roster under expected names IF they do not already exist.
    for name in ("arena_report.py", "_altsignal_collector_once.py", "arena_promote.py"):
        dst = os.path.join(dest, name)
        if os.path.isfile(dst):
            continue
        src = srcs[name]
        if os.path.isfile(src):
            shutil.copy2(src, dst)
            copied.append(dst)
    engine_dest = os.path.join(os.path.dirname(dest), "backtest", "engine")
    # If dest is 量化與交易機器人, engine lives at repo backtest/
    return copied


def _patch(path, hook_block):
    if not path or not os.path.isfile(path):
        return "missing"
    text = open(path, encoding="utf-8").read()
    if MARKER_BEGIN in text:
        return "already"
    # Do not patch our own rookie-only file (it already IS classify).
    if "S3 補選 classify()" in text and "extend_hits" in text:
        return "self"
    with open(path, "a", encoding="utf-8") as f:
        if not text.endswith("\n"):
            f.write("\n")
        f.write(hook_block)
        if not hook_block.endswith("\n"):
            f.write("\n")
    return "patched"


def main(argv=None):
    argv = list(argv or sys.argv[1:])
    dest = None
    if argv:
        dest = argv[0]
    live = _find_live_dir(dest)
    if not live:
        print("NO_LIVE_DIR: rookies stay in-repo; collector classify() will run after deploy copy")
        return 0
    copied = _copy_sidecars(live)
    print("SIDECARS", live, copied)
    results = {
        "roster": _patch(os.path.join(live, "arena_report.py"), ROSTER_HOOK),
        "classify": _patch(os.path.join(live, "_altsignal_collector_once.py"), CLASSIFY_HOOK),
        "exits": _patch(
            os.path.join(live, "altsignal_replay_labeler.py")
            if os.path.isfile(os.path.join(live, "altsignal_replay_labeler.py"))
            else "",
            EXIT_HOOK,
        ),
    }
    # Search a sibling backtest path for EXIT_PROFILE
    for cand in (
        os.path.join(os.path.dirname(live), "backtest", "engine", "altsignal_replay_labeler.py"),
        os.path.join(os.path.dirname(os.path.dirname(live)), "backtest", "engine", "altsignal_replay_labeler.py"),
    ):
        if os.path.isfile(cand) and results["exits"] == "missing":
            results["exits"] = _patch(cand, EXIT_HOOK)
    print("PATCH", results)
    if results["classify"] in ("missing", "self") and results["roster"] in ("missing", "self", "already"):
        print("NOTE: live veteran collector not found next to dest; "
              "ROSTER-only rookies will sit in registered once live ROSTER merges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
