# -*- coding: utf-8 -*-
"""
Telegram 訊號發送 - 法人級格式
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from strategy_orb import SignalResult

logger = logging.getLogger(__name__)

# 對價用 K 線（Yahoo GC=F），文字連結 + TG 按鈕共用
LINK_YAHOO_GC = "https://finance.yahoo.com/quote/GC=F"


def get_gold_chart_keyboard() -> Dict[str, Any]:
    """回傳 Telegram Inline Keyboard：一個「查看 Yahoo K線」按鈕。"""
    return {
        "inline_keyboard": [
            [{"text": "📈 查看 Yahoo K線 (GC=F)", "url": LINK_YAHOO_GC}]
        ]
    }


def format_signal_message(
    signal: SignalResult,
    data_cutoff_utc: Optional[datetime] = None,
) -> str:
    """專業訊號格式：多空、進場、止損、止盈、趨勢強度、時間、圖表連結。可帶入數據截止時間。"""
    side_emoji = "🟢" if signal.direction == "long" else "🔴"
    side_text = "做多" if signal.direction == "long" else "做空"
    time_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    data_line = ""
    if data_cutoff_utc is not None:
        cutoff_str = data_cutoff_utc.strftime("%Y-%m-%d %H:00 UTC") if hasattr(data_cutoff_utc, "strftime") else str(data_cutoff_utc)
        data_line = f"📅 依據 K 線至：{cutoff_str}\n"
    return (
        f"{side_emoji} XAUUSD {side_text}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 進場：{signal.entry:,.2f}\n"
        f"🛑 止損：{signal.sl:,.2f}  (ATR {signal.atr:.2f} × 1.5)\n"
        f"🎯 止盈：{signal.tp:,.2f}  (R:R 1:{signal.rr_ratio:.0f})\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 趨勢強度：{signal.trend_strength}\n"
        f"⏰ 訊號時間：{time_str}\n"
        f"{data_line}"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 對價用圖表：Yahoo GC=F {LINK_YAHOO_GC}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "#XAUUSD #黃金 #訊號"
    )


def send_telegram(
    bot_token: str,
    chat_id: str,
    text: str,
) -> bool:
    """發送純文字到 Telegram。"""
    if not bot_token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set, skip send")
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=10,
        )
        if r.status_code != 200:
            logger.error("Telegram send failed: %s %s", r.status_code, r.text)
            return False
        return True
    except Exception as e:
        logger.exception("Telegram send error: %s", e)
        return False


def send_signal(signal: SignalResult, bot_token: str, chat_id: str) -> bool:
    """組裝訊號文案並發送。"""
    msg = format_signal_message(signal)
    return send_telegram(bot_token, chat_id, msg)
