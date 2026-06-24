"""
tracker_hook.py — JackBot 訊號攔截 + 品質過濾 + 訊息加工 v3

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
import time
import requests
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser

# 限制背景 tracker thread 數量，防訊號海嘯時 thread 爆炸（最多 8 個並發）
# 關閉由 app.py 的 _coordinated_shutdown 統一協調（確保 task_executor 先完成）
_tracker_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tracker-bg")

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

RE_SYMBOL_PAIR = re.compile(r"\b([A-Z]{2,10})[/_]?(USDT|USD)\b", re.I)
# 持倉狙擊格式：「💎 ONDO · 逆勢摸底」不帶 USDT 後綴
# 持倉狙擊新手版：`🟢 💎 `ALLO` · 追突破`（幣名有反引號）
RE_SYMBOL_BARE = re.compile(
    r"(?:💎|[🔴🟢])\s*`?([A-Z]{2,10})`?\s*[·\|]",
    re.I,
)


def _parse_symbol(text: str) -> str | None:
    sym_m = RE_SYMBOL_PAIR.search(text)
    if sym_m:
        base = sym_m.group(1).upper()
        suffix = sym_m.group(2).upper()
        return base + ("USDT" if suffix == "USD" else suffix)
    sym_m = RE_SYMBOL_BARE.search(text)
    if sym_m:
        return sym_m.group(1).upper() + "USDT"
    return None

# ── RS 快取 + Single-Flight（防止快取 miss 時多 thread 同時打 Binance API）──
_rs_cache: dict[str, tuple[float, float]] = {}   # symbol → (rs_value, expire_ts)
_rs_cache_lock  = threading.Lock()
_rs_inflight: dict[str, threading.Event] = {}    # symbol → Event（正在請求中）
_RS_TTL_SEC = 90.0

# ── get_market_state TTL 快取（30 秒，防訊號海嘯時灌爆 signal-tracker）─────
_ms_cache: tuple[dict, float] = ({}, 0.0)   # (data, expire_ts)
_ms_cache_lock  = threading.Lock()
_MS_TTL_SEC = 30.0

# ══════════════════════════════════════════════════════════════
# 燃料箱分數閾值（核心轉折判斷）
# ══════════════════════════════════════════════════════════════
FUEL_BULL      = 65   # 以上 = 偏多，空單受阻
FUEL_NEUTRAL   = 45   # 以上 = 中性（放寬：原 48）
FUEL_BEAR      = 30   # 以下 = 跌勢中，空單已過度（放寬：原 40）
FUEL_BEAR_SNP  = 35   # 持倉狙擊用（放寬：原 45）


def _extract_number_after(text: str, keywords: list):
    for kw in keywords:
        idx = text.find(kw)
        if idx < 0:
            continue
        rest = text[idx + len(kw):].replace("`", "").replace("*", "")
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
    symbol = _parse_symbol(text)
    if not symbol:
        return None

    side = None
    if "🔴" in text or "做空" in text or "Short" in text:
        side = "short"
    elif "🟢" in text or "做多" in text or "Long" in text:
        side = "long"
    if not side:
        return None

    # 注意："🎯 進場" 和 "進場 "（加空格）必須放在 "進場價" 前面
    # 否則 "移 SL 至進場價" 這行會被先命中，導致抓到 TP2 而非真正進場價
    entry = _extract_number_after(text, ["🎯 進場", "進場 ", "入場", "Entry", "進場價"])
    sl    = _extract_number_after(text, ["止損", "SL", "Stop Loss"])
    tp1   = _extract_number_after(text, ["🥇 停利1", "近目標", "TP1", "停利1", "目標1"])
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
    """帶 30 秒 TTL 快取的市場狀態查詢，防止訊號海嘯時灌爆 signal-tracker。
    若 API 失敗，使用「最後已知良好狀態 (Last Known Good)」而非空字典，
    避免 fuel_score 預設 50 讓高風險訊號意外放行。
    """
    global _ms_cache
    now = time.monotonic()
    with _ms_cache_lock:
        data, expire_ts = _ms_cache
        if now < expire_ts:
            return data
        stale_data = data  # 快取已過期，但保留舊值供 fallback
    try:
        r = requests.get(f"{TRACKER_URL}/market-state", timeout=5)
        if r.status_code == 200:
            fresh = r.json()
            with _ms_cache_lock:
                _ms_cache = (fresh, now + _MS_TTL_SEC)
            return fresh
    except Exception as e:
        logger.warning("get_market_state error: %s", e)

    # Fallback：用舊快取（保守）而非空字典（危險）
    if stale_data:
        logger.warning("get_market_state: signal-tracker 不可用，使用過期快取 fuel=%.0f",
                       stale_data.get("fuel_score", 50))
        return stale_data
    # 完全沒有任何快取時：回傳中性值 48（恰好在 FUEL_NEUTRAL 邊界，不觸發逆勢強幣邏輯）
    # fuel=0 會讓任何 RS>0.5% 的多單被誤判為「逆勢強幣⭐⭐⭐⭐⭐」→ 應避免
    logger.error("get_market_state: 無任何快取，回傳中性保守預設 fuel=48")
    return {"fuel_score": 48, "market_mode": "neutral", "fuel_label": "中性(API不可用)"}



def calc_symbol_rs(symbol: str) -> float:
    """幣種相對 BTC 的 4H 相對強度（RS）。
    - TTL=90s 快取：極端行情多訊號並發時不重複打 Binance API
    - Single-flight（is_leader 模式）：快取 miss 時只讓一個 thread 發請求，
      其餘等待；try/finally 確保 Event 在任何情況下都會被 set，不會永遠卡住
    """
    sym = symbol.replace("_", "").upper()
    now = time.monotonic()

    # ── 原子性地判斷「是否快取命中」與「自己是否 leader」──────────────────
    is_leader = False
    wait_event: threading.Event | None = None

    with _rs_cache_lock:
        cached = _rs_cache.get(sym)
        if cached and now < cached[1]:
            return cached[0]                      # 快取命中，直接返回

        if sym in _rs_inflight:
            wait_event = _rs_inflight[sym]        # 有人在飛，拿到 Event 等待
        else:
            evt = threading.Event()
            _rs_inflight[sym] = evt
            is_leader = True                      # 本 thread 負責發請求

    # ── Waiter：等 leader 完成後從快取讀取 ──────────────────────────────
    if not is_leader:
        wait_event.wait(timeout=8)
        with _rs_cache_lock:
            cached = _rs_cache.get(sym)
            return cached[0] if cached else 0.0

    # ── Leader：發 API 請求，try/finally 確保無論如何都通知等待者 ────────
    rs = 0.0
    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": sym, "interval": "1h", "limit": 5},
            timeout=5,
        )
        klines = r.json()
        if isinstance(klines, list) and len(klines) >= 5:
            close_now    = float(klines[-1][4])
            close_4h_ago = float(klines[-4][4])
            sym_change   = (close_now - close_4h_ago) / close_4h_ago * 100
            ms           = get_market_state()
            btc_change   = float(ms.get("btc_change_4h", 0))
            rs           = round(sym_change - btc_change, 3)
    except Exception:
        rs = 0.0
    finally:
        # 無論成功或失敗，都寫快取並通知等待的 thread
        with _rs_cache_lock:
            _rs_cache[sym] = (rs, time.monotonic() + _RS_TTL_SEC)
            leader_evt = _rs_inflight.pop(sym, None)
        if leader_evt:
            leader_evt.set()

    return rs


def _fuel_zone(fuel: float) -> str:
    """燃料分數映射到市場階段。"""
    if fuel >= FUEL_BULL:    return "bull"
    if fuel >= FUEL_NEUTRAL: return "neutral"
    if fuel >= FUEL_BEAR:    return "bear"
    return "deep_bear"


# ══════════════════════════════════════════════════════════════
# 市場狀態增強過濾（研究實證：regime filtering 擋掉 60-70% 假訊號）
# ══════════════════════════════════════════════════════════════
def _detect_chop(ms: dict) -> bool:
    """方向一：CHOP（盤整）偵測。BTC 多時框無方向 + 中性燃料 → 盤整。
    研究：盤整盤是最大虧損來源，『不交易』本身就是最賺的決定。"""
    try:
        b4 = abs(float(ms.get("btc_change_4h", 0) or 0))
        b24 = abs(float(ms.get("btc_change_24h", 0) or 0))
        fuel = float(ms.get("fuel_score", 50))
    except (TypeError, ValueError):
        return False
    return b4 < 1.0 and b24 < 2.0 and 42 <= fuel <= 62


def _detect_squeeze(ms: dict) -> bool:
    """方向三：軋空風險偵測（BTC 級代理）。BTC 急跌 → 反彈軋空風險，不宜追空。
    研究：funding 持續負（空頭擁擠）時追空易被軋；此處用 BTC 24h 急跌 + 軋空警告代理。"""
    warns = ms.get("warnings", []) or []
    if any("軋空" in str(w) for w in warns):
        return True
    try:
        return float(ms.get("btc_change_24h", 0) or 0) < -5.0
    except (TypeError, ValueError):
        return False


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

    # ── 方向一：CHOP（盤整）閘門 — 盤整盤雙向停手（除非 RS 夠強）──
    # ── 方向三：軋空風險閘門 — 空頭擠壓時不追空（極弱幣 rs≤-3% 例外）──
    if source in ("crit_radar", "position_change"):
        if _detect_chop(ms) and abs(rs) < 2.0:
            return {**base, "pass": False, "quality": "❌ 已過濾",
                    "scenario": "chop_skip",
                    "reason": (f"盤整盤（BTC 4h/24h 無方向、燃料 {fuel:.0f}）"
                               f"且 RS {rs:+.2f}% 不夠強 → 雙向停手，等趨勢明朗")}
        if (not is_long) and _detect_squeeze(ms) and rs > -3.0:
            return {**base, "pass": False, "quality": "❌ 已過濾",
                    "scenario": "squeeze_risk",
                    "reason": (f"偵測軋空風險（BTC 急跌/空頭擁擠），"
                               f"RS {rs:+.2f}% 未達極弱（≤-3%），不追空避免被軋")}

    # ── 高勝率場景識別 ────────────────────────────────────────
    # 跌勢中（燃料 < neutral）RS > 0.5% → 主力護盤，逆勢多單高勝率
    is_counter_long = (
        is_long
        and zone in ("bear", "deep_bear")
        and rs > 1.0
    )
    # 跌勢中的空單 = 追空二段，反彈危險
    is_exhausted_short = (not is_long) and zone in ("bear", "deep_bear")

    # ── 摸頭摸底場景（明確識別）────────────────────────────────────────────
    # 摸底（bottom fishing）：深度熊市 + RS > 1.5%（強勢抗跌幣）→ 最佳低接點
    is_bottom_fishing = is_long and zone == "deep_bear" and rs > 1.5
    # 摸頭（top shorting）：牛市高位 + RS 已轉負（幣種相對轉弱）→ 頂部做空機會
    is_top_shorting = (not is_long) and zone == "bull" and rs < -1.0

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

        # ❌ 硬拒：RS 過弱的多單（放寬：原 -2.0）
        if is_long and rs < -3.0:
            return {**base, "pass": False, "quality": "❌ 已過濾",
                    "scenario": "weak_rs_long",
                    "reason": (f"相對強度 RS {rs:+.2f}%（幣種弱於大盤），"
                               f"多單無相對優勢，跳過")}

        # ⭐⭐⭐⭐⭐ 摸底場景（deep_bear + 強RS）：最優先
        if is_bottom_fishing:
            return {**base, "pass": True, "quality": "⭐⭐⭐⭐⭐ 摸底強幣",
                    "scenario": "bottom_fishing",
                    "reason": f"深度熊市低接，RS +{rs:.2f}%（主力護盤 = 反彈領漲標的）"}

        # ⭐⭐⭐⭐⭐ 逆勢強幣多單：高勝率場景
        if is_counter_long:
            quality = "⭐⭐⭐⭐⭐ 逆勢強幣"
            return {**base, "pass": True, "quality": quality,
                    "scenario": "counter_trend_long",
                    "reason": (f"跌勢中 RS +{rs:.2f}%（本幣抗跌/獨立走強）"
                               f"→ 逆勢強幣多單，歷史高勝率場景")}

        # ⭐⭐⭐⭐⭐ 摸頭場景（bull + RS 轉弱 + 空單）：頂部做空
        if is_top_shorting:
            return {**base, "pass": True, "quality": "⭐⭐⭐⭐⭐ 摸頭做空",
                    "scenario": "top_shorting",
                    "reason": f"牛市高位 RS {rs:+.2f}%（幣種相對轉弱 = 頂部分配訊號）"}

        # 多頭市場出現多單 → 正常順勢
        if is_long and zone == "bull":
            quality = "⭐⭐⭐⭐⭐ 極優" if rs >= 2.5 else "⭐⭐⭐⭐ 優質" if rs >= 1.0 else "⭐⭐⭐ 良好"
            return {**base, "pass": True, "quality": quality,
                    "scenario": "bull_long", "reason": "多頭市場順勢多單"}

        # ❌ 硬拒：非牛市追多（6/10 加嚴：摸底/逆勢強幣已在上方放行，其餘不追）
        if is_long and zone != "bull":
            need = 1.5 if zone in ("bear", "deep_bear") else 1.0
            return {**base, "pass": False, "quality": "❌ 已過濾",
                    "scenario": "non_bull_long",
                    "reason": (f"燃料 {fuel:.0f}（{fuel_label}），"
                               f"非牛市不宜追多（需 RS≥+{need}% 或摸底/逆勢強幣）")}

        # ❌ 硬拒：牛市/進攻模式追空（6/11 加嚴：摸頭已在上方放行，其餘不追）
        if not is_long and zone == "bull":
            return {**base, "pass": False, "quality": "❌ 已過濾",
                    "scenario": "non_bear_short",
                    "reason": (f"燃料 {fuel:.0f}（{fuel_label}），"
                               f"牛市/進攻模式不宜追空（需摸頭訊號：RS≤-1%）")}

        # ❌ 硬拒：中性盤追空需幣種走弱（6/11：中性追空 3/3 全滅）
        if not is_long and zone == "neutral" and rs > -1.0:
            return {**base, "pass": False, "quality": "❌ 已過濾",
                    "scenario": "neutral_short_weak",
                    "reason": (f"燃料 {fuel:.0f}（{fuel_label}），"
                               f"中性盤追空需 RS≤-1%（本幣需相對走弱）")}

        # 中性市場 or 其他 → 附加品質標籤
        quality = "⭐⭐⭐⭐ 優質" if abs(rs) >= 2.0 else "⭐⭐⭐ 良好" if abs(rs) >= 0.8 else "⭐⭐ 弱訊號"
        dir_note = "空頭市場" if not is_long and zone == "bull" else "中性市場"
        return {**base, "pass": True, "quality": quality,
                "scenario": "normal", "reason": f"{dir_note}，RS {rs:+.2f}%"}

    # ──────────────────────────────────────────────────────────
    # 持倉狙擊（高勝率型）：嚴格過濾，只推最有把握的
    # ──────────────────────────────────────────────────────────
    elif source == "position_change":
        # [修復 06-04] 回測實證：持倉狙擊本是「高勝率型」，但實盤勝率僅29%。根因：
        #   ① SHORT 全盤皆虧（回測36%/期望-0.09R，連最嚴SHORT都37%/虧）→ 全過濾
        #   ② LONG 門檻被放寬(2.5%→1.5%)拉低品質 → 恢復 rs>=2.0
        # 修復後回測：LONG+rs>=2.0 → 勝率50%/期望+0.24R（回到高勝率設計初衷，寧缺勿濫）
        if not is_long:
            return {**base, "pass": False, "quality": "❌ 已過濾",
                    "scenario": "sniper_short_filtered",
                    "reason": ("持倉狙擊回測 SHORT 勝率僅 36%（期望 −0.09R，全盤虧損），"
                               "狙擊定位高勝率、只做 LONG，本筆空單過濾不推")}
        SNP_RS = 2.0   # 持倉狙擊 RS 門檻（回測：LONG+rs>=2.0→勝率50%/+0.24R；原放寬1.5拉低勝率）

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

        # ⭐⭐⭐⭐⭐ 摸底：deep_bear + 強RS → 狙擊最優先
        if is_bottom_fishing and rs >= SNP_RS:
            return {**base, "pass": True, "quality": "⭐⭐⭐⭐⭐ 摸底狙擊",
                    "scenario": "bottom_fishing",
                    "reason": f"深度熊市底部狙擊，RS +{rs:.2f}%"}

        # ⭐⭐⭐⭐⭐ 摸頭：bull高位 + RS 轉弱 + 空單
        if is_top_shorting and abs(rs) >= SNP_RS:
            return {**base, "pass": True, "quality": "⭐⭐⭐⭐⭐ 摸頭狙擊",
                    "scenario": "top_shorting",
                    "reason": f"牛市頂部狙擊，RS {rs:+.2f}%（相對轉弱）"}

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
# ══════════════════════════════════════════════════════════════
# OOS 信心分級引擎接入（shadow_grader_live）
# OOS 實證：重倉組 +0.11R / 輕倉組 −0.21R（差 11%，可回寫✅）
# 規則：輕倉（conf<0.4，負期望）→ pass=False（不推播 + 不下單）
# 安全護欄：
#   · 算不出/引擎異常/逾時 → fail-open（不改原 pass，照推照下單）
#   · OOS_FILTER_ENABLED=false → 緊急停用過濾（仍標註分級，不擋單）
#   · 過濾比例異常（>50%）→ TG 自檢告警，防誤殺
# ══════════════════════════════════════════════════════════════
import sys as _sys
import concurrent.futures as _cf

_GRADER = None
_GRADER_TRIED = False
_OOS_FILTER_ENABLED = os.environ.get("OOS_FILTER_ENABLED", "true").lower() == "true"
_OOS_GRADER_PATH = os.environ.get("OOS_GRADER_PATH", "/app/data/crit_collector")
_OOS_GRADE_TIMEOUT = float(os.environ.get("OOS_GRADE_TIMEOUT", "10"))
_oos_stats = {"total": 0, "filtered": 0}
_oos_stats_lock = threading.Lock()
_OOS_ALERT_WINDOW = 20
_OOS_ALERT_RATIO = 0.5
_original_send_ref = None


def _get_grader():
    global _GRADER, _GRADER_TRIED
    if _GRADER_TRIED:
        return _GRADER
    _GRADER_TRIED = True
    try:
        if _OOS_GRADER_PATH not in _sys.path:
            _sys.path.insert(0, _OOS_GRADER_PATH)
        import shadow_grader_live as _sgl
        _GRADER = _sgl
        logger.info("[oos] shadow_grader_live 載入成功（信心分級啟用，過濾=%s）", _OOS_FILTER_ENABLED)
    except Exception as e:
        logger.warning("[oos] grader 載入失敗 → fail-open 全部放行: %s", e)
        _GRADER = None
    return _GRADER


def _oos_alert(ratio, total, filtered):
    logger.error("[oos] HIGH-FILTER-ALERT %.0f%% (%d/%d)", ratio * 100, filtered, total)
    if not _original_send_ref:
        return
    try:
        _crit_thread = int(os.environ.get("TG_THREAD_CRIT_RADAR", "11040"))
        _original_send_ref(
            (f"⚠️ <b>OOS 分級自檢告警</b>\n"
             f"最近 {total} 筆訊號過濾掉 {filtered} 筆（{ratio:.0%}），比例異常偏高。\n"
             f"請確認分級引擎是否誤殺；緊急停用過濾：設 <code>OOS_FILTER_ENABLED=false</code> 後重啟。"),
            _crit_thread, parse_mode="HTML",
        )
    except Exception as e:
        logger.warning("[oos] alert send fail: %s", e)


def _record_oos_stat(filtered: bool):
    global _oos_stats
    do_alert = None
    with _oos_stats_lock:
        _oos_stats["total"] += 1
        if filtered:
            _oos_stats["filtered"] += 1
        if _oos_stats["total"] >= _OOS_ALERT_WINDOW:
            t, f = _oos_stats["total"], _oos_stats["filtered"]
            ratio = (f / t) if t else 0.0
            _oos_stats = {"total": 0, "filtered": 0}
            if ratio > _OOS_ALERT_RATIO:
                do_alert = (ratio, t, f)
    if do_alert:
        _oos_alert(*do_alert)


def _oos_grade_call(grader, symbol, side):
    import httpx
    with httpx.Client(timeout=6) as _cli:
        return grader.live_grade(_cli, symbol, side)


def _apply_oos_grade(eval_result: dict, signal: dict, source: str) -> None:
    """疊加 OOS 信心分級。輕倉（負期望）→ pass=False（不推 + 不下單）。
    任何不確定情況一律 fail-open（不改原 pass，照推照下單）。"""
    grader = _get_grader()
    if grader is None:
        return
    _ex = _cf.ThreadPoolExecutor(max_workers=1)
    try:
        fut = _ex.submit(_oos_grade_call, grader, signal["symbol"], signal["side"])
        tier, mult, conf, detail = fut.result(timeout=_OOS_GRADE_TIMEOUT)
    except Exception as e:
        logger.warning("[oos] grade 逾時/錯誤 → fail-open %s: %s", signal.get("symbol"), e)
        return
    finally:
        _ex.shutdown(wait=False)

    eval_result["oos_tier"] = tier
    eval_result["oos_conf"] = round(float(conf), 2)
    eval_result["oos_mult"] = mult
    eval_result["oos_detail"] = detail
    is_light = (mult is not None and mult <= 0.3)   # 輕倉檔（conf<0.4，OOS 負期望）
    if is_light and _OOS_FILTER_ENABLED:
        eval_result["oos_pass"] = False             # signal-tracker 據此不下單
        if eval_result.get("pass", True):
            eval_result["pass"] = False             # tracker_hook 據此不推播
            eval_result["scenario"] = "oos_negative"
            eval_result["reason"] = (
                f"OOS 數據信心＝輕倉★（信心 {conf:.0%}），"
                f"此類訊號歷史回測為負期望（−0.21R），本輪過濾不推送"
            )
    else:
        eval_result["oos_pass"] = True
    # [v2.1 06-09] 順勢過濾：保留高勝率特例 + 做空加嚴（RS>0 不追空）
    _apply_trend_filter(eval_result, signal, source, detail)
    _record_oos_stat(is_light)


# 高勝率場景：evaluate_signal 已放行，不被 EMA 順勢規則覆蓋（例：DRAM 逆勢強幣）
_TREND_LONG_EXEMPT = frozenset({"counter_trend_long", "bottom_fishing"})
_TREND_SHORT_EXEMPT = frozenset({"top_shorting"})


def _apply_trend_filter(eval_result: dict, signal: dict, source: str, detail: dict | None) -> None:
    """疊加 EMA/ADX/RS 順勢過濾。fail-open：無因子資料時不額外阻擋。"""
    if source not in ("crit_radar", "position_change") or not _OOS_FILTER_ENABLED:
        return
    scenario = eval_result.get("scenario") or ""
    _rs = float(eval_result.get("rs") or 0)
    _ema = detail.get("ema_align") if isinstance(detail, dict) else None
    _adx = detail.get("adx") if isinstance(detail, dict) else None
    _fr = detail.get("fr") if isinstance(detail, dict) else None
    is_long = signal.get("side") == "long"

    if is_long:
        if scenario in _TREND_LONG_EXEMPT:
            return
        if _ema is None and _rs <= 0:
            return
        _block = (_ema is not None and _ema <= 0) or _rs <= 0
        _why = "做多需順勢（EMA 多排）且不弱於大盤（RS>0）"
    else:
        if scenario in _TREND_SHORT_EXEMPT:
            return
        reasons: list[str] = []
        if _ema is not None and _ema >= 0:
            reasons.append("EMA 非空排")
        if _adx is not None and _adx < 30:
            reasons.append("ADX<30 趨勢不足")
        if _rs > 0:
            reasons.append(f"RS +{_rs:.2f}% 相對強勢不宜追空")
        try:
            if _adx is not None and _fr is not None and float(_adx) > 50 and float(_fr) > 0.003:
                reasons.append("ADX 過熱且正 FR（空頭擁擠）")
        except (TypeError, ValueError):
            pass
        _block = bool(reasons)
        _why = "做空需強空頭且幣種弱於大盤"
        if reasons:
            _why += "（" + "、".join(reasons) + "）"

    if _block:
        eval_result["pass"] = False
        eval_result["oos_pass"] = False
        eval_result["scenario"] = "trend_weak"
        eval_result["reason"] = _why + "；本輪過濾不推"


_COOLDOWN_HOURS = float(os.environ.get("SYMBOL_SL_COOLDOWN_HOURS", "24"))
_COOLDOWN_MIN_LOSS_PCT = float(os.environ.get("SYMBOL_SL_COOLDOWN_LOSS_PCT", "25"))


def _check_sl_cooldown(signal: dict) -> str | None:
    """同幣同向 24h 內大虧止損 → 冷卻不推。查詢失敗 fail-open。"""
    try:
        r = requests.get(
            f"{TRACKER_URL}/internal/symbol-cooldown",
            params={"symbol": signal["symbol"], "side": signal["side"]},
            timeout=5,
        )
        if r.status_code == 200 and r.json().get("blocked"):
            return r.json().get("reason") or "同幣近期止損冷卻中"
    except Exception as e:
        # fail-open 可見化：逾時漏擋曾造成同幣 10 分鐘內重複進場再 SL（6/11 XMR）
        logger.warning("[cooldown] 查詢失敗 fail-open %s %s: %s", signal.get("symbol"), signal.get("side"), e)
    return None


def build_market_block(eval_result: dict, signal_side: str = "long") -> str:
    fuel     = eval_result.get("fuel_score", 50)
    # 所有動態字串套用 html.escape()，防止 > < & 等字元破壞 HTML → Telegram 400
    _e = _html_mod.escape  # 別名，簡化呼叫
    label    = _e(str(eval_result.get("fuel_label", "中性")))
    mode     = eval_result.get("market_mode", "neutral")
    rs       = eval_result.get("rs", 0)
    warnings = eval_result.get("warnings", [])
    quality  = _e(str(eval_result.get("quality", "⭐⭐⭐ 良好")))
    scenario = eval_result.get("scenario", "normal")

    mode_text = {
        "aggressive": "🟢 進攻（多單為主）",
        "defensive":  "🔴 防守（空單為主）",
        "neutral":    "⚪ 中性（雙向操作）",
    }.get(mode, _e(str(mode)))

    filled   = int(fuel / 10)
    fuel_bar = "█" * filled + "░" * (10 - filled)

    lines = ["━━━━━━━━━━━━━━━━"]
    # OOS 數據信心分級（歷史回測勝率驗證）為主；無分級時(fail-open)回退舊品質星等
    _oos_tier = eval_result.get("oos_tier")
    if _oos_tier:
        _oos_conf = eval_result.get("oos_conf", 0) or 0
        lines.append(
            f"🎯 <b>跟單信心：{_e(str(_oos_tier))}</b>"
            f"（OOS 回測勝率分級・信心 {_oos_conf:.0%}）"
        )
        lines.append(f"📊 場景參考：{quality}")
    else:
        lines.append(f"📊 <b>訊號品質：{quality}</b>")
    lines += [
        f"🛢️ 牛市燃料：<b>{fuel:.0f}/100</b> [{fuel_bar}]（{label}）",
        f"🎯 操作模式：{mode_text}",
        f"💪 相對強度：<b>{rs:+.2f}%</b>（vs BTC 4h）",
    ]

    if scenario == "counter_trend_long":
        lines.append("🔥 <b>逆勢強幣多單</b>：市場下跌本幣抗跌 → 主力護盤，歷史高勝率場景")
        lines.append("💡 幣種在大跌中相對強勢 = 機構選擇性買入，反彈力道通常較強")
    elif scenario == "bull_long":
        lines.append("💡 多頭趨勢中的順勢做多，方向與燃料一致")
    elif scenario == "trend_weak":
        pass  # 已被過濾，不應出現
    elif eval_result.get("pass") and eval_result.get("scenario") not in ("trend_weak",):
        lines.append("✅ <b>順勢確認通過</b>：符合 v2.0 趨勢過濾標準")
    elif scenario in ("exhausted_short", "weak_rs_long", "weak_rs_short"):
        reason = _e(str(eval_result.get("reason", "")))  # ← 關鍵修復：>< 轉義
        lines.append(f"⚠️ <b>注意：{reason}</b>")

    for w in (warnings or [])[:2]:
        lines.append(f"⚠️ {_e(str(w))}")

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


def _fmt_px(v) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    if x >= 100:
        return f"{x:.1f}".rstrip("0").rstrip(".")
    if x >= 1:
        return f"{x:.2f}".rstrip("0").rstrip(".")
    return f"{x:.5f}".rstrip("0").rstrip(".")


def _hook_compact_warn(eval_result: dict) -> str:
    lines: list[str] = []
    if eval_result.get("market_mode") == "defensive":
        lines.append("⚠️ 大盤防守模式，做多謹慎")
    for w in (eval_result.get("warnings") or [])[:1]:
        lines.append(f"⚠️ {_html_mod.escape(str(w))}")
    return "\n".join(lines[:1])


def _rr_ratio(entry, sl, tp, is_long):
    """風險報酬比 R:R。"""
    try:
        e, s, t = float(entry), float(sl), float(tp)
        risk = (e - s) if is_long else (s - e)
        reward = (t - e) if is_long else (e - t)
        if risk <= 0 or reward <= 0:
            return None
        return round(reward / risk, 1)
    except (TypeError, ValueError):
        return None


def build_action_summary(signal: dict, source: str, eval_result: dict) -> str:
    """精簡操作建議：清晰排版 + 風險報酬比（可推廣賣點）。"""
    _e = _html_mod.escape
    is_long = signal.get("side", "long") == "long"
    sym = _e(str(signal.get("symbol", "")).replace("USDT", "").replace("_", ""))
    tier = eval_result.get("oos_tier", "") or ""
    if "重倉" in tier:
        stars, pos, follow = "★★★", "2~3%", "可跟"
    elif "輕倉" in tier:
        stars, pos, follow = "★", "≤1%", "僅觀望"
    else:
        stars, pos, follow = "★★", "1~2%", "小倉試單"
    act_txt = "做多" if is_long else "做空"
    act_emo = "🟢" if is_long else "🔴"
    src_label = {
        "crit_radar": "⚡ 爆擊雷達",
        "position_change": "🎯 持倉狙擊",
    }.get(source, _e(SOURCE_DISPLAY.get(source, source)))

    entry = _fmt_px(signal.get("entry_price"))
    sl = _fmt_px(signal.get("sl_price"))
    tp1 = _fmt_px(signal.get("tp1_price"))
    tp2 = _fmt_px(signal.get("tp2_price")) if signal.get("tp2_price") else ""
    rr = _rr_ratio(signal.get("entry_price"), signal.get("sl_price"),
                   signal.get("tp1_price"), is_long)

    # 一眼看完：方向／點位／怎麼跟全部壓在 4~5 行內
    lines = [
        f"{src_label}｜{act_emo} {sym} {act_txt}｜{stars} {follow}",
        f"進場 <code>{entry}</code>　止損 <code>{sl}</code>（碰到就走）",
    ]
    if tp1:
        tp_line = f"停利 <code>{tp1}</code> 平65%"
        if tp2:
            tp_line += f" → <code>{tp2}</code> 抱單"
        lines.append(tp_line)
    pos_line = f"倉位 {pos}"
    if rr:
        pos_line += f"・風報 1:{rr}"
    pos_line += "・系統自動追蹤 TP/SL"
    lines.append(pos_line)
    warn = _hook_compact_warn(eval_result)
    if warn:
        lines.append(warn)
    lines.append("<i>研究參考，非投資建議、非任何形式帶單</i>")
    return "\n".join(lines)


def enhance_signal_message(original: str, signal: dict, source: str,
                            eval_result: dict, signal_id: int = None) -> str:
    """加工後輸出 HTML（parse_mode=HTML）"""
    escaped  = _html_mod.escape(original.rstrip(), quote=False)
    scenario = eval_result.get("scenario", "normal")

    # [白話化] 無腦跟單指示放最頂，一眼看懂怎麼操作
    try:
        action = build_action_summary(signal, source, eval_result)
    except Exception as e:
        logger.warning("[white] action summary err: %s", e)
        action = ""
    if action:
        enhanced = action
        if signal_id:
            enhanced += f"\n\n<code>#{signal_id}</code>"
    else:
        enhanced = escaped
    return enhanced


# ══════════════════════════════════════════════════════════════
# HTML 安全工具：驗證 + 自動降級
# ══════════════════════════════════════════════════════════════
class _TagBalanceChecker(HTMLParser):
    """計算開閉標籤是否平衡（Telegram 支援的 HTML 子集）。"""
    TG_TAGS = {'b', 'i', 'u', 's', 'code', 'pre', 'a', 'em', 'strong'}

    def __init__(self):
        super().__init__()
        self._stack: list[str] = []
        self._ok = True

    def handle_starttag(self, tag, attrs):
        if tag in self.TG_TAGS:
            self._stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.TG_TAGS:
            if self._stack and self._stack[-1] == tag:
                self._stack.pop()
            else:
                self._ok = False   # 閉合順序錯誤

    def is_balanced(self) -> bool:
        return self._ok and len(self._stack) == 0

    def unclosed(self) -> list[str]:
        return list(self._stack)


def _validate_html(text: str) -> tuple[bool, list[str]]:
    """回傳 (is_valid, unclosed_tags)。"""
    checker = _TagBalanceChecker()
    try:
        checker.feed(text)
    except Exception:
        return False, []
    return checker.is_balanced(), checker.unclosed()


def _strip_html_tags(text: str) -> str:
    """移除所有 HTML 標籤，保留純文字（含 unescape entity）。"""
    clean = re.sub(r'<[^>]+>', '', text)
    return _html_mod.unescape(clean)


def _safe_html(text: str) -> tuple[str, str | None]:
    """
    驗證 HTML：
    - 有效 → 回傳 (text, 'HTML')
    - 無效（標籤未閉合等）→ 移除所有標籤，回傳 (clean_text, None)
    """
    valid, unclosed = _validate_html(text)
    if valid:
        return text, "HTML"
    logger.warning(
        "[tracker-hook] HTML 驗證失敗，未閉合標籤: %s，降級為純文字", unclosed
    )
    return _strip_html_tags(text), None


_DEAD_LETTER_FILE = "/app/data/dead_letter.log"

def _write_dead_letter(signal: dict, source: str, kind: str):
    """Executor 已關閉時，將丟失的訊號寫入死信紀錄，方便事後排查。"""
    try:
        sym  = signal.get("symbol", "?")
        side = signal.get("side", "?")
        with open(_DEAD_LETTER_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.time():.0f} | {kind} | {source} | {sym} | {side}\n")
    except Exception as e:
        logger.debug("dead letter write failed: %s", e)


# TG sendPhoto caption 上限 1024（用 1000 留 emoji/UTF-16 餘裕）
_CAP_SAFE = 1000


def _build_caption_enhanced(caption: str, signal: dict, source: str,
                            eval_result: dict):
    """K 線卡片 caption 加料：頂部「怎麼跟（無腦版）＋跟單信心」(HTML)。
    受 1024 上限保護：超長則截斷原始訊號尾段、優先保留無腦版指示。
    回傳 (caption_text, parse_mode)；無法加料時回傳 None → 呼叫端維持原行為。"""
    try:
        action = build_action_summary(signal, source, eval_result)
    except Exception as e:
        logger.warning("[tracker-hook] photo caption 加料失敗(action): %s", e)
        return None
    if not action:
        return None
    # K 線圖已含點位：caption 只用精簡操作建議，不重複貼原文
    safe_text, mode = _safe_html(action)
    if mode and len(safe_text) <= _CAP_SAFE:
        return safe_text, mode
    # HTML 無效或仍超長 → 純文字版(去標籤)並硬截斷
    plain = _strip_html_tags(action)[: _CAP_SAFE - 1].rstrip()
    return plain, None


def send_to_tracker(signal: dict, source: str, chat_id: int,
                    msg_id: int = None, eval_result: dict = None,
                    dc_msg_id: str | int | None = None,
                    push_text: str | None = None) -> dict:
    # timeout=4：縮短內部微服務 timeout，避免 Gunicorn shutdown 時 bg thread 拖慢關閉
    try:
        pl = {}
        if eval_result:
            pl["market_eval"] = eval_result
        if push_text:
            pl["push_text"] = push_text
        if dc_msg_id:
            pl["dc_message_id"] = str(dc_msg_id)
        if msg_id and dc_msg_id:
            pl["jackbot_pushed"] = True
        payload = {
            **signal,
            "source": source,
            "tg_chat_id": chat_id,
            "tg_message_id": msg_id,
            "payload": pl or None,
        }
        r = requests.post(f"{TRACKER_URL}/signals", json=payload, timeout=4)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.error("send_to_tracker error: %s", e)
    return None


# ══════════════════════════════════════════════════════════════
# Monkey Patch 安裝
# ══════════════════════════════════════════════════════════════
def install_hook(jackbot_module):
    global _original_send_ref
    original_send = jackbot_module.send_telegram_message
    _original_send_ref = original_send   # 供 OOS 過濾比例自檢告警用
    _get_grader()                        # 預載 OOS 分級引擎（載入失敗自動 fail-open）

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

        # ── OOS 信心分級（輕倉負期望 → 過濾不推不下單；fail-open）──
        _apply_oos_grade(eval_result, signal, source)

        # ── 同幣止損冷卻（BEAT/ALLO 連虧防洗版）──
        if eval_result.get("pass", True):
            _cd = _check_sl_cooldown(signal)
            if _cd:
                eval_result["pass"] = False
                eval_result["oos_pass"] = False
                eval_result["scenario"] = "sl_cooldown"
                eval_result["reason"] = _cd

        # ── 硬過濾：不通過的訊號不推播 ─────────────────────────
        if not eval_result.get("pass", True):
            logger.info(
                "[tracker-hook] FILTERED %s %s %s | %s",
                source, signal["symbol"], signal["side"],
                eval_result.get("reason", "")
            )
            # 送 tracker 記錄（統計用，不推播）；用 executor 限制 thread 數
            try:
                _tracker_executor.submit(send_to_tracker, signal, source, chat_id, None, eval_result)
            except RuntimeError:
                logger.warning("[tracker-hook] executor 已關閉，FILTERED tracker 記錄丟失（shutdown 中）")
                _write_dead_letter(signal, source, "FILTERED")
            return True  # 回傳 True 避免 jackbot 誤判為發送失敗

        # ── 通過：TG 先推，tracker 非同步背景記錄 ───────────────────
        if source == "gold_signal":
            # 黃金獵手已是精簡單則（6/10 格式），不再疊市場/策略區塊（避免洗版）
            kwargs_gold = {**kwargs, "parse_mode": kwargs.get("parse_mode") or "HTML"}
            result = original_send(message, thread_id, *args, **kwargs_gold)
        else:
            try:
                enhanced = enhance_signal_message(message, signal, source, eval_result, signal_id=None)

                # 驗證 HTML 並自動降級
                safe_text, safe_parse_mode = _safe_html(enhanced)

                # 生產環境：只記錄長度與模式，不記錄訊號文本（避免日誌含使用者內容）
                logger.debug(
                    "[tracker-hook] 即將送出 HTML（長度=%d，parse_mode=%s）",
                    len(safe_text), safe_parse_mode,
                )
                if not safe_parse_mode:
                    # 降級到純文字（原始訊號，不含 HTML 加工）
                    logger.warning(
                        "[tracker-hook] HTML 格式異常，降級為原始訊號純文字 sym=%s",
                        signal.get("symbol", "?")
                    )
                    safe_text = message

                kwargs_final = {**kwargs, "parse_mode": safe_parse_mode} if safe_parse_mode else {
                    k: v for k, v in kwargs.items() if k != "parse_mode"
                }
                result = original_send(safe_text, thread_id, *args, **kwargs_final)
            except Exception as e:
                logger.error("[tracker-hook] enhance error: %s", e)
                result = original_send(message, thread_id, *args, **kwargs)

        # 背景送 tracker（帶 TG/DC message_id，避免 signal-tracker 重複推播）
        def _bg_tracker():
            tg_id = getattr(jackbot_module, "_LAST_PUSH_TG_MSG_ID", None)
            dc_id = getattr(jackbot_module, "_LAST_PUSH_DC_MSG_ID", None)
            _push_txt = message if source == "gold_signal" else None
            resp = send_to_tracker(
                signal, source, chat_id, tg_id, eval_result,
                dc_msg_id=dc_id, push_text=_push_txt,
            )
            sid = resp.get("id") if resp else None
            logger.info(
                "[tracker-hook] PASS #%s %s %s %s | tg_msg=%s dc_msg=%s | RS=%.2f%% fuel=%.0f",
                sid, source, signal["symbol"], signal["side"], tg_id, dc_id,
                eval_result.get("rs", 0), eval_result.get("fuel_score", 50),
            )

        try:
            _tracker_executor.submit(_bg_tracker)
        except RuntimeError:
            logger.warning("[tracker-hook] executor 已關閉，PASS tracker 記錄丟失（shutdown 中）")
            _write_dead_letter(signal, source, "PASS")
        return result

    def _tracker_from_caption(caption: str, thread_id: int, *, submit: bool = True):
        """解析 caption 並（可選）送 tracker。photo hook 需先推播再 submit 以帶 message_id。"""
        if thread_id not in TRACK_THREAD_IDS:
            return None
        source = TRACK_THREAD_IDS[thread_id]
        signal = parse_signal(caption)
        if not signal:
            return None
        chat_id = int(os.environ.get("CHAT_ID", "0") or 0)
        try:
            eval_result = evaluate_signal(signal, source)
        except Exception as e:
            logger.error("[tracker-hook] evaluate error: %s", e)
            eval_result = {"pass": True, "quality": "⭐⭐⭐ 良好",
                           "scenario": "other", "reason": "評估失敗，預設通過",
                           "rs": 0, "fuel_score": 50, "fuel_label": "未知",
                           "market_mode": "neutral", "warnings": []}
        _apply_oos_grade(eval_result, signal, source)
        if eval_result.get("pass", True):
            _cd = _check_sl_cooldown(signal)
            if _cd:
                eval_result["pass"] = False
                eval_result["oos_pass"] = False
                eval_result["scenario"] = "sl_cooldown"
                eval_result["reason"] = _cd
        if not eval_result.get("pass", True):
            logger.info(
                "[tracker-hook] FILTERED %s %s %s | %s",
                source, signal["symbol"], signal["side"],
                eval_result.get("reason", ""),
            )
            if submit:
                try:
                    _tracker_executor.submit(send_to_tracker, signal, source, chat_id, None, eval_result)
                except RuntimeError:
                    _write_dead_letter(signal, source, "FILTERED")
            return {"filtered": True, "signal": signal, "eval": eval_result, "source": source, "chat_id": chat_id}
        if submit:
            def _bg():
                resp = send_to_tracker(signal, source, chat_id, None, eval_result)
                sid = resp.get("id") if resp else None
                logger.info(
                    "[tracker-hook] PASS #%s %s %s %s | RS=%.2f%% fuel=%.0f quality=%s",
                    sid, source, signal["symbol"], signal["side"],
                    eval_result.get("rs", 0), eval_result.get("fuel_score", 50),
                    eval_result.get("quality", "?"),
                )
            try:
                _tracker_executor.submit(_bg)
            except RuntimeError:
                _write_dead_letter(signal, source, "PASS")
        return {"filtered": False, "signal": signal, "eval": eval_result, "source": source, "chat_id": chat_id}

    def _submit_tracker_with_push_ids(r: dict):
        """photo 推播完成後，帶 TG/DC message_id 送 tracker。"""
        tg_id = getattr(jackbot_module, "_LAST_PUSH_TG_MSG_ID", None)
        dc_id = getattr(jackbot_module, "_LAST_PUSH_DC_MSG_ID", None)
        signal = r["signal"]
        source = r["source"]
        chat_id = r["chat_id"]
        eval_result = r.get("eval")

        def _bg():
            resp = send_to_tracker(
                signal, source, chat_id, tg_id, eval_result, dc_msg_id=dc_id,
            )
            sid = resp.get("id") if resp else None
            logger.info(
                "[tracker-hook] PASS #%s %s %s %s | tg_msg=%s dc_msg=%s",
                sid, source, signal["symbol"], signal["side"], tg_id, dc_id,
            )

        try:
            _tracker_executor.submit(_bg)
        except RuntimeError:
            _write_dead_letter(signal, source, "PASS")

    original_photo = jackbot_module.send_telegram_photo

    def patched_photo(photo_path, caption, thread_id, *args, **kwargs):
        """持倉狙擊 K 線卡片走 sendPhoto：先品質/OOS 過濾，通過則把 caption
        加料(怎麼跟+跟單信心)再送圖；加料失敗自動退回原 caption，不打斷推播。"""
        if thread_id in TRACK_THREAD_IDS:
            r = _tracker_from_caption(caption or "", thread_id, submit=False)
            if r and r.get("filtered"):
                try:
                    _tracker_executor.submit(
                        send_to_tracker, r["signal"], r["source"], r["chat_id"], None, r.get("eval"),
                    )
                except RuntimeError:
                    _write_dead_letter(r["signal"], r["source"], "FILTERED")
                return True
            if r and r.get("signal"):
                source = TRACK_THREAD_IDS[thread_id]
                enh = _build_caption_enhanced(caption or "", r["signal"], source, r.get("eval") or {})
                kw = {k: v for k, v in kwargs.items() if k != "parse_mode"}
                if enh is not None:
                    new_cap, mode = enh
                    if mode:
                        kw["parse_mode"] = mode
                    result = original_photo(photo_path, new_cap, thread_id, *args, **kw)
                else:
                    result = original_photo(photo_path, caption, thread_id, *args, **kwargs)
                _submit_tracker_with_push_ids(r)
                return result
        return original_photo(photo_path, caption, thread_id, *args, **kwargs)

    jackbot_module.send_telegram_message = patched_send
    jackbot_module.send_telegram_photo = patched_photo
    logger.info(
        "[tracker-hook] v8 安裝完成 | 防重複推播(tg/dc id)+精簡K線caption | "
        "v2.1順勢過濾+止損冷卻 | thread_ids=%s",
        list(TRACK_THREAD_IDS.keys()),
    )


# ══════════════════════════════════════════════════════════════
# Module-Level Helper（容錯 monkey-patch 失效時的後備）
# ══════════════════════════════════════════════════════════════
# 不依賴 install_hook 的閉包：jackbot.send_telegram_photo 直接呼叫此函數
# 確保 K 線卡片訊號（持倉狙擊）也能進 signal-tracker

_STATE_TRACKER_HELPER = {"executor": None, "patched_thread_ids": None}


def tracker_for_caption_module_level(caption: str, thread_id: int) -> dict | None:
    """模組級 helper：把 caption + thread_id 送進 signal-tracker。

    回傳 {"filtered": bool, "signal": dict, "eval": dict} 或 None。

    安全性：
    - 任何異常都吞掉並 log，不影響呼叫端
    - 用 module-level _tracker_executor（install_hook 安裝後賦值）
    - 若 _tracker_executor 未準備好，建立一個臨時 sync 跑（次次都是新 thread 在小流量場景可接受）
    """
    try:
        if thread_id not in TRACK_THREAD_IDS:
            return None
        source = TRACK_THREAD_IDS[thread_id]
        signal = parse_signal(caption)
        if not signal:
            return None
        chat_id = int(os.environ.get("CHAT_ID", "0") or 0)
        try:
            eval_result = evaluate_signal(signal, source)
        except Exception as e:
            logger.error("[tracker-helper] evaluate error: %s", e)
            eval_result = {"pass": True, "quality": "⭐⭐⭐ 良好",
                           "scenario": "other", "reason": "評估失敗，預設通過",
                           "rs": 0, "fuel_score": 50, "fuel_label": "未知",
                           "market_mode": "neutral", "warnings": []}
        _apply_oos_grade(eval_result, signal, source)
        if eval_result.get("pass", True):
            _cd = _check_sl_cooldown(signal)
            if _cd:
                eval_result["pass"] = False
                eval_result["oos_pass"] = False
                eval_result["scenario"] = "sl_cooldown"
                eval_result["reason"] = _cd
        if not eval_result.get("pass", True):
            logger.info(
                "[tracker-helper] FILTERED %s %s %s | %s",
                source, signal["symbol"], signal["side"],
                eval_result.get("reason", ""),
            )
            try:
                _tracker_executor.submit(send_to_tracker, signal, source, chat_id, None, eval_result)
            except (RuntimeError, NameError):
                _write_dead_letter(signal, source, "FILTERED")
            return {"filtered": True, "signal": signal, "eval": eval_result}

        def _bg():
            try:
                resp = send_to_tracker(signal, source, chat_id, None, eval_result)
                sid = resp.get("id") if resp else None
                logger.info(
                    "[tracker-helper] PASS #%s %s %s %s | RS=%.2f%% fuel=%.0f quality=%s",
                    sid, source, signal["symbol"], signal["side"],
                    eval_result.get("rs", 0), eval_result.get("fuel_score", 50),
                    eval_result.get("quality", "?"),
                )
            except Exception as e:
                logger.error("[tracker-helper] _bg send_to_tracker error: %s", e)

        try:
            _tracker_executor.submit(_bg)
        except (RuntimeError, NameError):
            _write_dead_letter(signal, source, "PASS")
        return {"filtered": False, "signal": signal, "eval": eval_result}
    except Exception as e:
        logger.error("[tracker-helper] tracker_for_caption_module_level error: %s", e)
        return None

