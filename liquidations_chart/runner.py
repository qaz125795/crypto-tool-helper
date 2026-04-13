# SPDX-License-Identifier: MIT
"""產生清算圖 PNG（Binance 公開資料），供 Telegram sendPhoto。"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from liquidations_chart.data import get_new_data
from liquidations_chart.plot import liquidations_plot
from liquidations_chart.summary import summarize_liquidations

logger = logging.getLogger(__name__)

DEFAULT_COIN = "BTCUSDT"
DEFAULT_MARKET = "um"
DEFAULT_LOOKBACK_DAYS = 180
DEFAULT_MAX_SYNC_DAYS = 14


def generate_liquidation_chart_png(
    base_dir: Optional[Path] = None,
    coin: str = DEFAULT_COIN,
    market: str = DEFAULT_MARKET,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    max_sync_days: int = DEFAULT_MAX_SYNC_DAYS,
) -> Optional[Path]:
    """
    下載並彙總 Binance 清算快照，輸出 PNG。

    - base_dir: 資料與輸出根目錄（預設：專案下 liquidations_chart_data）
    - 僅同步「最近 max_sync_days 天」內缺檔，避免每次推播抓全歷史。
    回傳 PNG 路徑；失敗則 None。
    """
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent / "liquidations_chart_data"
    base_dir = Path(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    data_root = str(base_dir)

    try:
        new_dates = get_new_data(coin, market=market, base_extract_to=data_root, max_sync_days=max_sync_days)
        if new_dates:
            logger.info(f"[清算圖] 新下載 {len(new_dates)} 個交易日快照")

        summarize_liquidations(coin=coin, market=market, base_dir=data_root)

        summary_csv = base_dir / "summary" / coin / market / "liquidation_summary.csv"
        if not summary_csv.is_file():
            logger.warning(f"[清算圖] 找不到彙總檔: {summary_csv}")
            return None

        import pandas as pd

        df = pd.read_csv(summary_csv, index_col=0, parse_dates=True)
        if df.empty:
            logger.warning("[清算圖] 彙總 CSV 為空")
            return None

        tail = df[-lookback_days:] if len(df) > lookback_days else df

        out_dir = base_dir / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"liquidation_{coin}_{market}.png"

        ok = liquidations_plot(
            tail,
            output_path=str(out_path),
            title_suffix=f"({coin} last {len(tail)}d)",
        )
        if ok and out_path.is_file():
            return out_path
    except Exception as e:
        logger.warning(f"[清算圖] 產生失敗: {e}", exc_info=True)
    return None
