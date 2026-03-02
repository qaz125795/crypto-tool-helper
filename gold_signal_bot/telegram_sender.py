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

# 圖表連結
LINK_BINGX_GOLD = "https://bingx.com/zh-tc/perpetual/GOLD(XAU)-USDT/"
LINK_YAHOO_GC = "https://finance.yahoo.com/quote/GC=F"


def get_gold_chart_keyboard() -> Dict[str, Any]:
    """回傳 Telegram Inline Keyboard：BingX 走勢圖（主要）+ Yahoo 對照。"""
    return {
        "inline_keyboard": [
            [{"text": "📊 BingX 走勢圖（請自行對照）", "url": LINK_BINGX_GOLD}],
            [{"text": "📈 Yahoo GC=F 參考", "url": LINK_YAHOO_GC}],
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
        f"📌 參考進場區：{signal.entry:,.2f}\n"
        f"🛑 參考防守點：{signal.sl:,.2f}  (ATR {signal.atr:.2f} × 1.5)\n"
        f"🎯 參考停利點：{signal.tp:,.2f}  (R:R 1:{signal.rr_ratio:.0f})\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 趨勢強度：{signal.trend_strength}\n"
        f"⏰ 訊號時間：{time_str}\n"
        f"{data_line}"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ 重要提醒\n"
        "以上價格僅供參考，請務必自行開啟 BingX 走勢圖，\n"
        "對照實際K線結構來確認你的防守點與停利點。\n"
        "切勿直接照單全收輸入上方數字，\n"
        "BingX報價與參考價可能存在價差，實際點位以圖表為準。\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "#XAUUSD #黃金 #訊號"
    )


def format_tp_sl_hit_message(
    hit_type: str,
    direction: str,
    entry: float,
    sl: float,
    tp: float,
) -> str:
    """觸及止盈或止損時的推播文案。hit_type 為 'tp' 或 'sl'。"""
    side_emoji = "🟢" if direction == "long" else "🔴"
    side_text = "多單" if direction == "long" else "空單"
    if hit_type == "tp":
        title = "🎯 觸及止盈"
    else:
        title = "🛑 觸及止損"
    time_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"{side_emoji} XAUUSD {side_text} {title}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 原參考進場：{entry:,.2f}\n"
        f"🛑 參考防守點：{sl:,.2f}\n"
        f"🎯 參考停利點：{tp:,.2f}\n"
        f"⏰ 時間：{time_str}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "提醒：請以 BingX 圖表實際點位為準。\n"
        "#XAUUSD #黃金 #平倉"
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
