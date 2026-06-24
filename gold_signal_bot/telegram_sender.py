# -*- coding: utf-8 -*-
"""
Telegram 訊號發送 - 精簡推播格式
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

from strategy_orb import SignalResult

logger = logging.getLogger(__name__)

LINK_GATE_GOLD = "https://www.gate.com/futures/USDT/XAU_USDT"
LINK_YAHOO_GC = "https://finance.yahoo.com/quote/GC=F"


def get_gold_chart_keyboard() -> Dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": "📊 Gate XAU 走勢", "url": LINK_GATE_GOLD}],
            [{"text": "📈 Yahoo 參考", "url": LINK_YAHOO_GC}],
        ]
    }


def _setup_label(source: str) -> str:
    s = (source or "").replace("獵手v2·", "").replace("ORB+MA", "ORB").strip()
    return s or "趨勢"


def _quality_stars(trend: str, setup: str) -> str:
    t = f"{trend} {setup}"
    if any(k in t for k in ("順勢", "突破", "回踩", "倫敦", "紐約")):
        return "★★★"
    return "★★"


def format_signal_message(
    signal: SignalResult,
    data_cutoff_utc: Optional[datetime] = None,
) -> str:
    """黃金獵手精簡單則（HTML；TG/DC 同源，DC 由 jackbot 轉 Markdown）。"""
    import html as _html

    is_long = signal.direction == "long"
    emoji = "🟢" if is_long else "🔴"
    side = "做多" if is_long else "做空"
    setup_raw = _setup_label(signal.source)
    setup = _html.escape(setup_raw)
    stars = _quality_stars(signal.trend_strength, setup_raw)
    trend = _html.escape((signal.trend_strength or "").strip())

    # 含關鍵字（非投資建議＋帶單）可讓 jackbot 跳過重複法規聲明
    return (
        f"🥇 <b>黃金獵手</b>｜XAUUSDT {side} {emoji} · {stars}\n"
        f"進場 <code>{signal.entry:,.1f}</code>　止損 <code>{signal.sl:,.1f}</code>（碰到就走）\n"
        f"停利 <code>{signal.tp1:,.1f}</code> 平60% → <code>{signal.tp2:,.1f}</code> 抱單\n"
        f"倉位 2~3%・系統自動追蹤 TP/SL\n"
        f"<i>以 Gate XAU_USDT 為準・研究參考，非投資建議、非任何形式帶單</i>"
    )


def format_tp_sl_hit_message(
    hit_type: str,
    direction: str,
    entry: float,
    sl: float,
    tp1: float,
    tp2: float,
) -> str:
    side_emoji = "🟢" if direction == "long" else "🔴"
    side_text = "多單" if direction == "long" else "空單"

    if hit_type == "tp2":
        title = "🏆 停利2 達成"
        result = "全平，本單結案"
        pnl_str = f"+{abs(tp2 - entry):.1f} 點"
    elif hit_type == "tp1":
        title = "🥇 停利1 達成"
        result = "平60%，餘倉 SL 移保本"
        pnl_str = f"+{abs(tp1 - entry):.1f} 點"
    else:
        title = "🛑 止損出場"
        result = "觸及止損，本單結案"
        pnl_str = f"-{abs(entry - sl):.1f} 點"

    return (
        f"{side_emoji} *黃金獵手*｜XAUUSDT {side_text} {title}\n"
        f"進場 `{entry:,.1f}` → 結果 {pnl_str}\n"
        f"{result}\n"
        f"_研究參考，非投資建議、非任何形式帶單_"
    )


def send_telegram(bot_token: str, chat_id: str, text: str) -> bool:
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
    msg = format_signal_message(signal)
    return send_telegram(bot_token, chat_id, msg)
