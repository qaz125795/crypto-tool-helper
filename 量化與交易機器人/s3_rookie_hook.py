"""Single import surface for merging S3 rookies into the live collector.

Prefers sidecar module names so a live arena_report.py can import this
file without a circular import.

Live files should add, once:

    from s3_rookie_hook import apply_rookie_roster, apply_rookie_exits
    from s3_rookie_hook import extend_hits, max_open_for
    ROSTER = apply_rookie_roster(ROSTER)
    EXIT_PROFILE = apply_rookie_exits(EXIT_PROFILE)
    # inside classify():
    hits = extend_hits(hits, row)

Never import this from jackbot.py scoring / gatekeeper paths.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE = os.path.join(os.path.dirname(_HERE), "backtest", "engine")
for _p in (_HERE, _ENGINE):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from s3_rookie_roster import (
        ROSTER,
        ROSTER_META,
        S3_ROOKIE_INTAKE_TS,
        S3_ROOKIE_KEYS,
        apply_rookie_roster,
        intake_ts_for,
        max_open_for,
    )
except ImportError:
    from arena_report import (
        ROSTER,
        ROSTER_META,
        S3_ROOKIE_INTAKE_TS,
        S3_ROOKIE_KEYS,
        apply_rookie_roster,
        intake_ts_for,
        max_open_for,
    )

try:
    from s3_rookie_promote import ALREADY_TG, HAS_PUSH, apply_promote_guard
except ImportError:
    from arena_promote import ALREADY_TG, HAS_PUSH, apply_promote_guard

try:
    from s3_rookie_exits import apply_rookie_exits, exit_for
except ImportError:
    from altsignal_replay_labeler import apply_rookie_exits, exit_for

try:
    from s3_rookie_classify import classify, classify_rookies, extend_hits
except ImportError:
    from _altsignal_collector_once import classify, classify_rookies, extend_hits
