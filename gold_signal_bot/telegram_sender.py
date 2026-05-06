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
        f"{side_emoji} *XAUUSD 黃金 {side_text}*  [{signal.source}]\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 進場價：`{signal.entry:,.2f}`\n"
        f"🛑 止損：`{signal.sl:,.2f}`（{arrow_sl} {sl_diff:.2f}）\n"
        f"🥇 停利1：`{signal.tp1:,.2f}`（{arrow_tp} +{tp1_diff:.2f}，1R）\n"
        f"🏆 停利2：`{signal.tp2:,.2f}`（{arrow_tp} +{tp2_diff:.2f}，2R）\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📋 *新手怎麼跟（4 步）*\n"
        "1) 倉位：本金的 1~3%（黃金波動雖小但槓桿放大會傷）\n"
        "2) 進場：在進場價附近掛限價，超過 0.3% 就放棄這支\n"
        "3) 停利1：到 TP1 先平一半，剩下移動止損到進場價（保本）\n"
        "4) 停利2：到 TP2 全平；中間任何時候止損觸及就出場，不要凹\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {signal.trend_strength}\n"
        f"⏰ {time_str}\n"
        f"{data_line}"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ 為避免價差，請對照 Gate 實際走勢確認點位。\n"
        "本訊息僅供研究參考，非投資建議。\n"
        "#XAUUSD #黃金"
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
        title   = "🏆 停利 2 達成"
        result  = "✅ 全平倉，本單收工"
        pnl_pts = abs(tp2 - entry)
        pnl_str = f"+{pnl_pts:.2f} 點（2R）"
    elif hit_type == "tp1":
        title   = "🥇 停利 1 達成"
        result  = "✅ 平一半倉位，剩下移動止損到進場價（保本）"
        pnl_pts = abs(tp1 - entry)
        pnl_str = f"+{pnl_pts:.2f} 點（1R）"
    else:  # sl
        title   = "🛑 止損出場"
        result  = "❌ 本單虧損出場（這次先學經驗，下次再來）"
        pnl_pts = abs(entry - sl)
        pnl_str = f"-{pnl_pts:.2f} 點（-1R）"

    return (
        f"{side_emoji} *XAUUSD 黃金 {side_text}*  {title}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 進場：`{entry:,.2f}`\n"
        f"🛑 止損：`{sl:,.2f}`\n"
        f"🥇 停利1：`{tp1:,.2f}`\n"
        f"🏆 停利2：`{tp2:,.2f}`\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 結果：{result}\n"
        f"💰 損益：{pnl_str}\n"
        f"⏰ {time_str}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "提醒：以 Gate 實際點位為準。\n"
        "#XAUUSD #黃金"
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
