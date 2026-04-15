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
LINK_GATE_GOLD = "https://www.gate.com/futures/USDT/XAU_USDT"
LINK_YAHOO_GC = "https://finance.yahoo.com/quote/GC=F"


def get_gold_chart_keyboard() -> Dict[str, Any]:
    """回傳 Telegram Inline Keyboard：Gate 走勢圖（主要）+ Yahoo 對照。"""
    return {
        "inline_keyboard": [
            [{"text": "📊 Gate 走勢圖（請自行對照）", "url": LINK_GATE_GOLD}],
            [{"text": "📈 Yahoo GC=F 參考", "url": LINK_YAHOO_GC}],
        ]
    }


def format_signal_message(
    signal: SignalResult,
    data_cutoff_utc: Optional[datetime] = None,
) -> str:
    """完整交易計畫格式：進場區 + SL + TP1 (1:1) + TP2 (1:2) + ATR 標示。"""
    is_long     = signal.direction == "long"
    side_emoji  = "🟢" if is_long else "🔴"
    side_text   = "做多" if is_long else "做空"
    arrow_sl    = "▼" if is_long else "▲"
    arrow_tp    = "▲" if is_long else "▼"
    sl_diff     = abs(signal.entry - signal.sl)
    tp1_diff    = abs(signal.tp1 - signal.entry)
    tp2_diff    = abs(signal.tp2 - signal.entry)
    time_str    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    data_line   = ""
    if data_cutoff_utc is not None:
        cutoff_str = (
            data_cutoff_utc.strftime("%Y-%m-%d %H:00 UTC")
            if hasattr(data_cutoff_utc, "strftime") else str(data_cutoff_utc)
        )
        data_line = f"📅 依據 K 線至：{cutoff_str}\n"

    return (
        f"{side_emoji} XAUUSD {side_text}  [{signal.source}]\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 參考進場區：{signal.entry:,.2f}\n"
        f"📐 波動參考 (ATR)：{signal.atr:.2f}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📋 交易計畫\n"
        f"🛑 防守點  (SL) ：{signal.sl:,.2f}  "
        f"{arrow_sl} {sl_diff:.2f}  (-1.5 ATR)\n"
        f"🥇 目標一 (TP1)：{signal.tp1:,.2f}  "
        f"{arrow_tp} +{tp1_diff:.2f}  (+1.5 ATR | R:R 1:1)\n"
        f"🏆 目標二 (TP2)：{signal.tp2:,.2f}  "
        f"{arrow_tp} +{tp2_diff:.2f}  (+3.0 ATR | R:R 1:2)\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {signal.trend_strength}\n"
        f"⏰ 訊號時間：{time_str}\n"
        f"{data_line}"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ 重要提醒\n"
        "以上為系統計算的參考價格，請務必開啟 Gate 走勢圖，\n"
        "對照實際K線結構確認防守點與目標位，\n"
        "Gate報價與參考價可能有價差，請以圖表為準。\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "#XAUUSD #黃金 #訊號"
    )


def format_tp_sl_hit_message(
    hit_type: str,
    direction: str,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
) -> str:
    """
    觸及目標/止損的推播文案。
    hit_type: 'tp1' | 'tp2' | 'sl'
    """
    side_emoji = "🟢" if direction == "long" else "🔴"
    side_text  = "多單" if direction == "long" else "空單"
    time_str   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if hit_type == "tp2":
        title   = "🏆 目標二達成 (TP2)"
        result  = "✅ 完整盈利出場"
        pnl_pts = abs(tp2 - entry)
        pnl_str = f"+{pnl_pts:.2f} pts (+3.0 ATR | R:R 1:2)"
    elif hit_type == "tp1":
        title   = "🥇 目標一達成 (TP1)"
        result  = "✅ 部分獲利，可考慮移動止損至成本"
        pnl_pts = abs(tp1 - entry)
        pnl_str = f"+{pnl_pts:.2f} pts (+1.5 ATR | R:R 1:1)"
    else:  # sl
        title   = "🛑 止損觸及 (SL)"
        result  = "❌ 本單出場"
        pnl_pts = abs(entry - sl)
        pnl_str = f"-{pnl_pts:.2f} pts (-1.5 ATR)"

    return (
        f"{side_emoji} XAUUSD {side_text}  {title}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 原參考進場：{entry:,.2f}\n"
        f"🛑 防守點 (SL) ：{sl:,.2f}\n"
        f"🥇 目標一 (TP1)：{tp1:,.2f}\n"
        f"🏆 目標二 (TP2)：{tp2:,.2f}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 結果：{result}\n"
        f"💰 損益：{pnl_str}\n"
        f"⏰ 時間：{time_str}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "提醒：請以 Gate 圖表實際點位為準。\n"
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
