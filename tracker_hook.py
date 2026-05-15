"""
tracker_hook.py — JackBot 訊號攔截 + 品質過濾 + 訊息加工 v2

核心設計哲學（來自實戰體感）：
────────────────────────────────────────────────────────
1. 市場轉折判斷：用「牛市燃料箱」分數，而非 BTC 漲跌
   · 燃料 < 40 → 跌勢已持續一段，空方筋疲力盡 → 追空二段 = 高風險
   · 燃料 >= 65 → 多方動能充足 → 空單逆風

2. 逆勢強幣多單 = 高勝率場景
   · 大盤暴跌時，某幣相對強度 RS > 0.5% → 主力護盤
   · 這種逆勢做多，歷史勝率顯著高於順勢追空

3. 爆擊雷達（樂透型）vs 持倉狙擊（高勝率型）
   · 爆擊：1-2% 小倉、寬鬆過濾、只擋最差場景（追空二段）
   · 狙擊：3-5% 標準倉、嚴格 RS 2.5%、每一單都要有明確優勢

4. 過濾規則（硬性拒絕，不推播）：
   · 爆擊雷達：燃料 < 40 時的空單 → 拒絕（第二段追空危險）
   · 持倉狙擊：燃料 < 45 時的空單 → 拒絕（同理）
   · 持倉狙擊：RS 未達 2.5% 的訊號 → 拒絕（優勢不夠明確）
────────────────────────────────────────────────────────
"""
import os
import re
import html as _html_mod
import logging
import threading
import requests

logger = logging.getLogger(__name__)

TRACKER_URL = os.environ.get("SIGNAL_TRACKER_URL", "http://signal-tracker:8004")

TRACK_THREAD_IDS = {
    int(os.environ.get("TG_THREAD_CRIT_RADAR", "11040")):       "crit_radar",
    int(os.environ.get("TG_THREAD_POSITION_CHANGE", "250")):    "position_change",
    int(os.environ.get("TG_THREAD_GOLD_SIGNAL", "254")):        "gold_signal",
}

SOURCE_DISPLAY = {
    "crit_radar":      "⚡ 爆擊雷達",
    "position_change": "🎯 持倉狙擊",
    "gold_signal":     "🥇 黃金訊號",
}

RE_SYMBOL = re.compile(r"\b([A-Z]{2,10})[/_]?(USDT|USD)\b", re.I)

# ══════════════════════════════════════════════════════════════
# 燃料箱分數閾值（核心轉折判斷）
# ══════════════════════════════════════════════════════════════
FUEL_BULL      = 65   # 以上 = 偏多，空單受阻
FUEL_NEUTRAL   = 48   # 以上 = 中性
FUEL_BEAR      = 40   # 以下 = 跌勢中，空單已過度（追空二段危險）
FUEL_BEAR_SNP  = 45   # 持倉狙擊用（更嚴格）


def _extract_number_after(text: str, keywords: list):
    for kw in keywords:
        idx = text.find(kw)
        if idx < 0:
            continue
        rest = text[idx + len(kw):]
        m = re.search(r"([\d,]+(?:\.\d+)?)", rest[:200])
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                pass
    return None


def parse_signal(text: str) -> dict:
    if not text or len(text) < 20:
        return None
    sym_m = RE_SYMBOL.search(text)
    if not sym_m:
        return None
    base   = sym_m.group(1).upper()
    suffix = sym_m.group(2).upper()
    symbol = base + ("USDT" if suffix == "USD" else suffix)

    side = None
    if "🔴" in text or "做空" in text or "Short" in text:
        side = "short"
    elif "🟢" in text or "做多" in text or "Long" in text:
        side = "long"
    if not side:
        return None

    entry = _extract_number_after(text, ["進場價", "進場", "入場", "Entry"])
    sl    = _extract_number_after(text, ["止損", "SL", "Stop Loss"])
    tp1   = _extract_number_after(text, ["近目標", "TP1", "停利1", "目標1"])
    tp2   = _extract_number_after(text, ["遠目標", "TP2", "停利2", "目標2"])
    tp3   = _extract_number_after(text, ["TP3", "停利3", "目標3"])
    tp4   = _extract_number_after(text, ["TP4", "停利4", "目標4"])
    lev_m = re.search(r"(?:槓桿|Lev|Leverage)[^\d]*?(\d+)", text)
    leverage = int(lev_m.group(1)) if lev_m else 10

    if not entry or not sl or not tp1:
        return None

    return {
        "symbol": symbol,
        "side": side,
        "entry_price": entry,
        "sl_price": sl,
        "tp1_price": tp1,
        "tp2_price": tp2,
        "tp3_price": tp3,
        "tp4_price": tp4,
        "leverage": leverage,
    }


def get_market_state() -> dict:
    try:
        r = requests.get(f"{TRACKER_URL}/market-state", timeout=5)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.warning("get_market_state error: %s", e)
    return {}


def calc_symbol_rs(symbol: str) -> float:
    """幣種相對 BTC 的 4H 相對強度（RS）。正 = 強於大盤，負 = 弱於大盤。"""
    try:
        sym = symbol.replace("_", "").upper()
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": sym, "interval": "1h", "limit": 5},
            timeout=5,
        )
        klines = r.json()
        if not isinstance(klines, list) or len(klines) < 5:
            return 0.0
        close_now    = float(klines[-1][4])
        close_4h_ago = float(klines[-4][4])
        sym_change   = (close_now - close_4h_ago) / close_4h_ago * 100

        ms         = get_market_state()
        btc_change = float(ms.get("btc_change_4h", 0))
        return round(sym_change - btc_change, 3)
    except Exception:
        return 0.0


def _fuel_zone(fuel: float) -> str:
    """燃料分數映射到市場階段。"""
    if fuel >= FUEL_BULL:    return "bull"
    if fuel >= FUEL_NEUTRAL: return "neutral"
    if fuel >= FUEL_BEAR:    return "bear"
    return "deep_bear"


# ══════════════════════════════════════════════════════════════
# 核心評估邏輯（v2）
# ══════════════════════════════════════════════════════════════
def evaluate_signal(signal: dict, source: str) -> dict:
    """
    回傳 dict 包含：
      pass (bool)        → 是否推播
      quality (str)      → 品質星等
      scenario (str)     → 場景標籤
      reason (str)       → 原因說明
      rs, fuel_score, market_mode, fuel_label, warnings
    """
    ms         = get_market_state()
    rs         = calc_symbol_rs(signal["symbol"])
    fuel       = float(ms.get("fuel_score", 50))
    mode       = ms.get("market_mode", "neutral")
    fuel_label = ms.get("fuel_label", "中性")
    warnings   = ms.get("warnings", [])
    zone       = _fuel_zone(fuel)

    is_long = signal["side"] == "long"
    base = dict(rs=rs, fuel_score=fuel, fuel_label=fuel_label,
                market_mode=mode, warnings=warnings)

    # ── 高勝率場景識別 ────────────────────────────────────────
    # 跌勢中（燃料 < neutral）RS > 0.5% → 主力護盤，逆勢多單高勝率
    is_counter_long = (
        is_long
        and zone in ("bear", "deep_bear")
        and rs > 0.5
    )
    # 跌勢中的空單 = 追空二段，反彈危險
    is_exhausted_short = (not is_long) and zone in ("bear", "deep_bear")

    # ──────────────────────────────────────────────────────────
    # 爆擊雷達（樂透型）：寬鬆過濾，只擋最差場景
    # ──────────────────────────────────────────────────────────
    if source == "crit_radar":

        # ❌ 硬拒：跌勢中追空二段（低勝率，反彈會被殺）
        if is_exhausted_short:
            return {**base, "pass": False, "quality": "❌ 已過濾",
                    "scenario": "exhausted_short",
                    "reason": (f"牛市燃料 {fuel:.0f}（市場跌勢持續），"
                               f"此時空單為追空第二段，反彈風險極高，本輪不推")}

        # ❌ 硬拒：RS 過弱的多單（幣種比大盤跌更多，沒有理由做多）
        if is_long and rs < -2.0:
            return {**base, "pass": False, "quality": "❌ 已過濾",
                    "scenario": "weak_rs_long",
                    "reason": (f"相對強度 RS {rs:+.2f}%（幣種弱於大盤），"
                               f"多單無相對優勢，跳過")}

        # ⭐⭐⭐⭐⭐ 逆勢強幣多單：高勝率場景
        if is_counter_long:
            quality = "⭐⭐⭐⭐⭐ 逆勢強幣"
            return {**base, "pass": True, "quality": quality,
                    "scenario": "counter_trend_long",
                    "reason": (f"跌勢中 RS +{rs:.2f}%（本幣抗跌/獨立走強）"
                               f"→ 逆勢強幣多單，歷史高勝率場景")}

        # 多頭市場出現多單 → 正常順勢
        if is_long and zone == "bull":
            quality = "⭐⭐⭐⭐⭐ 極優" if rs >= 2.5 else "⭐⭐⭐⭐ 優質" if rs >= 1.0 else "⭐⭐⭐ 良好"
            return {**base, "pass": True, "quality": quality,
                    "scenario": "bull_long", "reason": "多頭市場順勢多單"}

        # 中性市場 or 牛市出現空單 → 附加品質標籤
        quality = "⭐⭐⭐⭐ 優質" if abs(rs) >= 2.0 else "⭐⭐⭐ 良好" if abs(rs) >= 0.8 else "⭐⭐ 弱訊號"
        dir_note = "空頭市場" if not is_long and zone == "bull" else "中性市場"
        return {**base, "pass": True, "quality": quality,
                "scenario": "normal", "reason": f"{dir_note}，RS {rs:+.2f}%"}

    # ──────────────────────────────────────────────────────────
    # 持倉狙擊（高勝率型）：嚴格過濾，只推最有把握的
    # ──────────────────────────────────────────────────────────
    elif source == "position_change":
        SNP_RS = 2.5   # 持倉狙擊 RS 嚴格門檻

        # ❌ 硬拒：跌勢中追空二段（持倉狙擊比爆擊雷達更嚴，燃料 < 45 就拒）
        fuel_bear_snp = float(os.environ.get("SNIPER_FUEL_BEAR_THRESHOLD", FUEL_BEAR_SNP))
        if not is_long and fuel < fuel_bear_snp:
            return {**base, "pass": False, "quality": "❌ 已過濾",
                    "scenario": "exhausted_short",
                    "reason": (f"牛市燃料 {fuel:.0f}（跌勢進行中），"
                               f"空單追二段勝率低，狙擊不等這種機會，本輪跳過")}

        # ❌ 硬拒：RS 不達門檻（沒有明確相對優勢）
        if is_long and rs < SNP_RS:
            return {**base, "pass": False, "quality": "❌ 已過濾",
                    "scenario": "weak_rs_long",
                    "reason": (f"RS {rs:+.2f}%（持倉狙擊需 > +{SNP_RS}%），"
                               f"相對強度不夠明確，等更好機會")}
        if not is_long and rs > -SNP_RS:
            return {**base, "pass": False, "quality": "❌ 已過濾",
                    "scenario": "weak_rs_short",
                    "reason": (f"RS {rs:+.2f}%（持倉狙擊需 < -{SNP_RS}%），"
                               f"相對弱勢不夠明確，等更好機會")}

        # ⭐⭐⭐⭐⭐ 逆勢強幣多單：最佳狙擊場景（跌勢 + 幣種抗跌強勢）
        if is_counter_long and rs >= SNP_RS:
            quality = "⭐⭐⭐⭐⭐ 逆勢強幣"
            return {**base, "pass": True, "quality": quality,
                    "scenario": "counter_trend_long",
                    "reason": (f"跌勢中 RS +{rs:.2f}%（抗跌且達狙擊門檻）"
                               f"→ 最佳高勝率場景")}

        # 通過所有過濾的正常訊號
        quality = "⭐⭐⭐⭐⭐ 極優" if abs(rs) >= 4.0 else "⭐⭐⭐⭐ 優質" if abs(rs) >= 2.5 else "⭐⭐⭐ 良好"
        return {**base, "pass": True, "quality": quality,
                "scenario": "normal",
                "reason": f"RS {rs:+.2f}%，通過持倉狙擊過濾"}

    # ── 黃金訊號（不過濾，只加資訊）────────────────────────────
    quality = "⭐⭐⭐⭐ 優質" if abs(rs) >= 1.5 else "⭐⭐⭐ 良好"
    return {**base, "pass": True, "quality": quality,
            "scenario": "other", "reason": "黃金 ORB 訊號"}


# ══════════════════════════════════════════════════════════════
# 訊息加工
# ══════════════════════════════════════════════════════════════
def build_market_block(eval_result: dict, signal_side: str = "long") -> str:
    fuel     = eval_result.get("fuel_score", 50)
    label    = eval_result.get("fuel_label", "中性")
    mode     = eval_result.get("market_mode", "neutral")
    rs       = eval_result.get("rs", 0)
    warnings = eval_result.get("warnings", [])
    quality  = eval_result.get("quality", "⭐⭐⭐ 良好")
    scenario = eval_result.get("scenario", "normal")

    mode_text = {
        "aggressive": "🟢 進攻（多單為主）",
        "defensive":  "🔴 防守（空單為主）",
        "neutral":    "⚪ 中性（雙向操作）",
    }.get(mode, mode)

    # 燃料視覺化（每 10 分一個格子）
    filled = int(fuel / 10)
    fuel_bar = "█" * filled + "░" * (10 - filled)

    lines = [
        "━━━━━━━━━━━━━━━━",
        f"📊 <b>訊號品質：{quality}</b>",
        f"🛢️ 牛市燃料：<b>{fuel:.0f}/100</b> [{fuel_bar}]（{label}）",
        f"🎯 操作模式：{mode_text}",
        f"💪 相對強度：<b>{rs:+.2f}%</b>（vs BTC 4h）",
    ]

    # 場景說明
    if scenario == "counter_trend_long":
        lines.append("🔥 <b>逆勢強幣多單</b>：市場下跌本幣抗跌 → 主力護盤，歷史高勝率場景")
        lines.append("💡 幣種在大跌中相對強勢 = 機構選擇性買入，反彈力道通常較強")
    elif scenario == "bull_long":
        lines.append("💡 多頭趨勢中的順勢做多，方向與燃料一致")
    elif scenario == "exhausted_short" or scenario == "weak_rs_long" or scenario == "weak_rs_short":
        # 這些已被過濾，不應出現在推播中；萬一出現則顯示原因
        reason = eval_result.get("reason", "")
        lines.append(f"⚠️ <b>注意：{reason}</b>")

    for w in (warnings or [])[:2]:
        lines.append(f"⚠️ {w}")

    return "\n".join(lines)


def build_strategy_block(source: str, signal: dict, signal_id: int = None,
                         scenario: str = "normal") -> str:
    if source == "crit_radar":
        # 逆勢強幣場景：強調抗跌特性，加碼策略
        if scenario == "counter_trend_long":
            return (
                "━━━━━━━━━━━━━━━━\n"
                "🔥 <b>逆勢強幣爆擊策略</b>\n"
                "🎲 倉位：本金 2-3%（比普通爆擊略重，因場景優）\n"
                "📈 TP1 達成 → 加碼 30%、SL 移到進場價（保本）\n"
                "📈 TP2 達成 → 再加碼 30%、SL 移到 TP1\n"
                "💡 市場反轉時這類幣種往往漲最快，讓利潤奔跑\n"
                "🛑 SL 嚴守，行情若繼續下跌就認輸，等下一次"
            )
        # 一般爆擊策略
        return (
            "━━━━━━━━━━━━━━━━\n"
            "💎 <b>金字塔策略（爆擊·樂透小倉）</b>\n"
            "🎲 倉位：本金 1-2%（小倉拼爆擊）\n"
            "📈 近目標達成 → 加碼 30%、SL 移到進場價（保本）\n"
            "📈 遠目標達成 → 再加碼 30%、SL 移到近目標\n"
            "💰 理念：賠就賠小錢，賺就賺大錢\n"
            "⚠️ 嚴守 SL，不凹單，連續失敗總有一次大爆擊"
        )
    elif source == "position_change":
        if scenario == "counter_trend_long":
            return (
                "━━━━━━━━━━━━━━━━\n"
                "🔥 <b>逆勢強幣狙擊策略（高勝率）</b>\n"
                "💼 倉位：本金 4-5%（場景優，可略重）\n"
                "🥇 TP1 達成 → 平 60% 鎖利、SL 移到進場價\n"
                "🏆 TP2 達成 → 剩餘全平，收工\n"
                "🛑 SL 嚴格執行，這種場景失敗就是失敗，不凹\n"
                "📊 理念：跌勢強幣 = 最高確定性，用倉位換報酬"
            )
        return (
            "━━━━━━━━━━━━━━━━\n"
            "🎯 <b>穩健跟單策略（狙擊·高勝率）</b>\n"
            "💼 倉位：本金 3-5%（標準倉位）\n"
            "🥇 TP1 達成 → 平 60% 鎖利、SL 移到進場價\n"
            "🏆 TP2 達成 → 剩餘全平\n"
            "🛑 SL 嚴格執行，絕不凹單\n"
            "📊 理念：高勝率穩定複利，不求單筆暴利"
        )
    elif source == "gold_signal":
        return (
            "━━━━━━━━━━━━━━━━\n"
            "🥇 <b>黃金 ORB 策略</b>\n"
            "💼 倉位：本金 1-3%\n"
            "🎯 TP1（1R）平半倉、SL 移保本\n"
            "🏆 TP2（2R）全平\n"
            "⏰ 黃金不貪心，到點就走"
        )
    return ""


def enhance_signal_message(original: str, signal: dict, source: str,
                            eval_result: dict, signal_id: int = None) -> str:
    """加工後輸出 HTML（parse_mode=HTML）"""
    escaped  = _html_mod.escape(original.rstrip(), quote=False)
    enhanced = escaped
    scenario = eval_result.get("scenario", "normal")

    if signal_id:
        enhanced += f"\n\n🆔 訊號編號：<code>#{signal_id}</code>（追蹤點位用）"

    enhanced += "\n\n" + build_market_block(eval_result, signal.get("side", "long"))

    strategy = build_strategy_block(source, signal, signal_id, scenario)
    if strategy:
        enhanced += "\n\n" + strategy

    return enhanced


def send_to_tracker(signal: dict, source: str, chat_id: int,
                    msg_id: int = None, eval_result: dict = None) -> dict:
    try:
        payload = {
            **signal,
            "source": source,
            "tg_chat_id": chat_id,
            "tg_message_id": msg_id,
            "payload": {"market_eval": eval_result} if eval_result else None,
        }
        r = requests.post(f"{TRACKER_URL}/signals", json=payload, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.error("send_to_tracker error: %s", e)
    return None


# ══════════════════════════════════════════════════════════════
# Monkey Patch 安裝
# ══════════════════════════════════════════════════════════════
def install_hook(jackbot_module):
    original_send = jackbot_module.send_telegram_message

    def patched_send(message, thread_id=None, *args, **kwargs):
        # 不是訊號頻道 → 照常推
        if thread_id not in TRACK_THREAD_IDS:
            return original_send(message, thread_id, *args, **kwargs)

        source = TRACK_THREAD_IDS[thread_id]

        # 解析訊號
        signal = parse_signal(message)
        if not signal:
            # 非訊號訊息（統計、提醒等）→ 照常推
            return original_send(message, thread_id, *args, **kwargs)

        chat_id = int(os.environ.get("CHAT_ID", "0") or 0)

        # ── 核心評估 ──────────────────────────────────────────
        try:
            eval_result = evaluate_signal(signal, source)
        except Exception as e:
            logger.error("[tracker-hook] evaluate error: %s", e)
            eval_result = {"pass": True, "quality": "⭐⭐⭐ 良好",
                           "scenario": "other", "reason": "評估失敗，預設通過",
                           "rs": 0, "fuel_score": 50, "fuel_label": "未知",
                           "market_mode": "neutral", "warnings": []}

        # ── 硬過濾：不通過的訊號不推播 ─────────────────────────
        if not eval_result.get("pass", True):
            logger.info(
                "[tracker-hook] FILTERED %s %s %s | %s",
                source, signal["symbol"], signal["side"],
                eval_result.get("reason", "")
            )
            # 送 tracker 記錄（統計用，不推播）
            threading.Thread(
                target=send_to_tracker,
                args=(signal, source, chat_id, None, eval_result),
                daemon=True,
            ).start()
            return True  # 回傳 True 避免 jackbot 誤判為發送失敗

        # ── 通過：加工並推播 ────────────────────────────────────
        tracker_resp = send_to_tracker(signal, source, chat_id, None, eval_result)
        signal_id    = tracker_resp.get("id") if tracker_resp else None

        try:
            enhanced    = enhance_signal_message(message, signal, source, eval_result, signal_id)
            kwargs_html = {**kwargs, "parse_mode": "HTML"}
            result      = original_send(enhanced, thread_id, *args, **kwargs_html)
            logger.info(
                "[tracker-hook] PASS #%s %s %s %s | RS=%.2f%% fuel=%.0f quality=%s scenario=%s",
                signal_id, source, signal["symbol"], signal["side"],
                eval_result.get("rs", 0), eval_result.get("fuel_score", 50),
                eval_result.get("quality", "?"), eval_result.get("scenario", "?"),
            )
            return result
        except Exception as e:
            logger.error("[tracker-hook] enhance error: %s", e)
            return original_send(message, thread_id, *args, **kwargs)

    jackbot_module.send_telegram_message = patched_send
    logger.info(
        "[tracker-hook] v2 安裝完成 | 邏輯：燃料箱轉折判斷 + 逆勢強幣高勝率 + 硬性過濾 | thread_ids=%s",
        list(TRACK_THREAD_IDS.keys()),
    )
