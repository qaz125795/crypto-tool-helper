#!/usr/bin/env python3
"""Deprecated entry: in-place patch of the live partner-app (no WR_TOKEN).

push-once with a partial tar would replace the whole S3 app. Use
patch_live_stock_arena.py which edits the PORT=4196 document root.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from patch_live_stock_arena import main as patch_main


def main(argv=None) -> int:
    argv = list(argv or sys.argv[1:])
    print("WR_STOCK_INPLACE (no push-once; live PORT=4196 cwd)")
    return patch_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
