"""
狙擊訊號推播（取代舊持倉狙擊）— 圖文版（K 線卡片 + caption），推到 TG（區塊鏈船長 thread 250）+ DC。

兩支已驗證正期望的策略（多空雙向）：
  多｜資費反殺      sniper_snapshots：side=LONG & confirmed & aligned>=4 & 資費<=-0.04%
                    回測 +0.32R / 勝率52.6% / n=344
  空｜大戶純空      multilens_snapshots：strategy=whale_pure_short_opt & side=SHORT
                    回測 +0.169R / 勝率58.2% / n=146（用 _opt 版；基礎版是虧的）

停利停損：15m ATR×1.8 動態 SL（2.2%~5.5% 夾限）+ TP1=1.5R / TP2=2.5R（非固定 4%/6%）。

風控（對應交易心法）：同幣同向 6h 冷卻、每日上限防洗版、低流動/股票代幣過濾。
DRY_RUN=1 只印不發。
"""
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import httpx

DIR = os.environ.get("ARENA_DATA_DIR", "/app/data/crit_collector")
if not os.path.isdir(DIR):
    DIR = os.path.dirname(os.path.abspath(__file__))
SNIPER_SNAP = os.path.join(DIR, "sniper_snapshots.jsonl")
MULTILENS_SNAP = os.path.join(DIR, "multilens_snapshots.jsonl")
STATE = os.path.join(DIR, "fr_sniper_push_state.json")
CJK_FONT = os.path.join(DIR, "fonts", "cjk.ttc")

TG_TOKEN = os.environ.get("TG_TOKEN", "")
DC_TOKEN = os.environ.get("DC_TOKEN", "")
TG_CHAT = os.environ.get("FRS_TG_CHAT", "-1003611242392")
TG_THREAD = int(os.environ.get("FRS_TG_THREAD", "250"))
DC_CHANNEL = os.environ.get("FRS_DC_CHANNEL", "1493134120186941470")

COOLDOWN_H = float(os.environ.get("FRS_COOLDOWN_H", "6"))
DAILY_CAP = int(os.environ.get("FRS_DAILY_CAP", "10"))   # 每日推播上限，防洗版
SNAP_MAX_AGE_S = 1800
DRY = os.environ.get("DRY_RUN", "0") == "1"

# 資費反殺（多）
LONG_FR_FLOOR = -0.015
# 資費過熱（空）：預設關閉（無邊際），改用大戶純空
SHORT_FR = 0.003
SHORT_FR_CAP = 0.01
SL_PCT_MIN = 0.022   # 保底 2.2%（對齊 jackbot MIN_SL）
SL_PCT_MAX = 0.055   # 上限 5.5%
ATR_SL_MULT = 1.8
TP1_R = 1.5
TP2_R = 2.5
MIN_VOL_M = 25.0     # 恢復流動性門檻（15M 太鬆導致劣質標的）
WHALE_MIN_VOL_USD = 15_000_000.0
LONG_FR = -0.0004    # -0.04% 資費才夠「極度偏空」
LONG_ALIGNED_MIN = 4 # 恢復共振嚴格度
WHALE_MIN_TOP_LONG = 75.0

SIGNALS_LOG = os.path.join(DIR, "frs_signals.jsonl")
BREAKER = os.path.join(DIR, "frs_breaker.json")

EXCLUDE_BASES = {
    "SPX500", "US500", "NDX", "NAS100", "DJI", "US30",
    "TSLA", "NVDA", "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "AMD",
    "COIN", "MSTR", "NFLX", "BABA", "SKHX", "SKHYNIX", "DRAM", "HOOD", "PLTR",
    "XAU", "XAG", "XAUT", "PAXG", "XTI", "XBR", "NG", "GOLD", "SILVER", "OIL", "WTI",
    "EUR", "GBP", "JPY", "USD", "USDJPY", "EURUSD",
}


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _last_line_json(path):
    last = None
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    last = ln
    except FileNotFoundError:
        return None
    if not last:
        return None
    try:
        return json.loads(last)
    except json.JSONDecodeError:
        return None


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(s):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f)


def base_of(sym):
    return (sym or "").upper().replace("USDT", "").replace("_", "")


def gate_price(base):
    for contract in ("%s_USDT" % base, "1000%s_USDT" % base):
        try:
            r = httpx.get(
                "https://api.gateio.ws/api/v4/futures/usdt/tickers",
                params={"contract": contract}, timeout=10,
            )
            j = r.json()
            if isinstance(j, list) and j:
                p = fnum(j[0].get("last"))
                if p and p > 0:
                    return p
        except Exception:
            continue
    return None


# ── 訊號判定 ────────────────────────────────────────────────
def classify_long(row):
    """sniper snapshot row → 資費反殺（多）"""
    side = (row.get("side") or "").upper()
    fr = fnum(row.get("fr_raw"))
    aligned = fnum(row.get("aligned")) or 0
    conf = bool(row.get("confirmed"))
    vol = fnum(row.get("vol24_m")) or 0
    base = base_of(row.get("sym"))
    if fr is None or vol < MIN_VOL_M or base in EXCLUDE_BASES:
        return None
    if side == "LONG" and conf and aligned >= LONG_ALIGNED_MIN and LONG_FR_FLOOR <= fr <= LONG_FR:
        return {"side": "LONG", "name": "資費反殺",
                "reason": "空頭擁擠（資費極負 %.3f%%）→ 軋空做多" % (fr * 100),
                "winrate": "歷史勝率 52.6%（樣本 344）", "exp": False}
    short_on = os.environ.get("FRS_SHORT_ENABLED", "0") == "1"
    if short_on and side == "SHORT" and conf and aligned >= 4 and SHORT_FR <= fr <= SHORT_FR_CAP:
        return {"side": "SHORT", "name": "資費過熱",
                "reason": "多頭過熱（資費極正 %.3f%%）→ 回吐做空" % (fr * 100),
                "winrate": "實驗中（小倉觀察）", "exp": True}
    return None


def whale_short_candidates():
    """multilens snapshot → 大戶純空（空，whale_pure_short_opt）"""
    snap = _last_line_json(MULTILENS_SNAP)
    out = []
    if not snap:
        return out
    age = time.time() - (fnum(snap.get("ts")) or 0)
    if age > SNAP_MAX_AGE_S:
        return out
    for row in snap.get("rows", []):
        if row.get("strategy") != "whale_pure_short_opt":
            continue
        if (row.get("side") or "").upper() != "SHORT":
            continue
        base = base_of(row.get("sym"))
        if base in EXCLUDE_BASES:
            continue
        vol = fnum(row.get("vol24_usd")) or 0
        top_long = fnum(row.get("top_long_pct")) or 0
        if vol < WHALE_MIN_VOL_USD or top_long < WHALE_MIN_TOP_LONG:
            continue
        price = gate_price(base)
        if not price:
            continue
        sig = {"side": "SHORT", "name": "大戶純空",
               "reason": "大戶多 %.0f%% 高位派發 → 順勢做空" % top_long,
               "winrate": "歷史勝率 58.2%（樣本 146）", "exp": False}
        out.append((row.get("sym"), price, sig))
    return out


# ── 出場價 / 圖卡 / 文字 ────────────────────────────────────
def fetch_klines_15m(base, limit=48):
    for contract in ("%s_USDT" % base, "1000%s_USDT" % base):
        try:
            r = httpx.get(
                "https://api.gateio.ws/api/v4/futures/usdt/candlesticks",
                params={"contract": contract, "interval": "15m", "limit": limit},
                timeout=12,
            )
            j = r.json()
            if isinstance(j, list) and len(j) >= 20:
                rows = []
                for b in j:
                    if isinstance(b, dict):
                        h, l, c = fnum(b.get("h")), fnum(b.get("l")), fnum(b.get("c"))
                        if h and l and c:
                            rows.append((h, l, c))
                if rows:
                    return rows
        except Exception:
            continue
    return None


def atr14_from_klines(rows):
    if not rows or len(rows) < 15:
        return None
    trs = []
    prev_c = rows[0][2]
    for h, l, c in rows[1:]:
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
        prev_c = c
    if len(trs) < 14:
        return None
    return sum(trs[-14:]) / 14.0


def levels(price, side, base=None):
    """ATR 動態 SL + R 倍 TP（對齊回測結構，非固定 4%/6%）。"""
    long = side == "LONG"
    atr = None
    if base:
        kl = fetch_klines_15m(base)
        atr = atr14_from_klines(kl) if kl else None
    if atr and atr > 0:
        sl_dist = max(ATR_SL_MULT * atr, price * SL_PCT_MIN)
        sl_dist = min(sl_dist, price * SL_PCT_MAX)
    else:
        sl_dist = price * 0.035
    if long:
        sl = price - sl_dist
        tp1 = price + sl_dist * TP1_R
        tp2 = price + sl_dist * TP2_R
    else:
        sl = price + sl_dist
        tp1 = price - sl_dist * TP1_R
        tp2 = price - sl_dist * TP2_R
    return price, sl, tp1, tp2, sl_dist / price if price else 0


def make_card(sym, sig, entry, sl, tp1, tp2):
    try:
        if "/app" not in sys.path:
            sys.path.insert(0, "/app")
        import kline_card_renderer as K
        if os.path.exists(CJK_FONT):
            from PIL import ImageFont
            for sz in (14, 18):
                if sz not in K._FONT_CACHE:
                    try:
                        K._FONT_CACHE[sz] = ImageFont.truetype(CJK_FONT, sz)
                    except Exception:
                        pass
        base = base_of(sym)
        ohlc = K.fetch_ohlc_5m(base, limit=60)
        if not ohlc:
            return None
        oi = None
        try:
            oi = K.fetch_coinglass_oi_5m(base, limit=60)
        except Exception:
            oi = None
        cards_dir = os.path.join(DIR, "kline_cards")
        os.makedirs(cards_dir, exist_ok=True)
        out = os.path.join(cards_dir, "frs_%s_%s_%d.png" % (base, sig["side"], int(time.time())))
        long = sig["side"] == "LONG"
        title = "%s（%s） · %s/USDT" % (sig["name"], "做多" if long else "做空", base)
        K.render_kline_oi_card(base, long, ohlc, oi, sl, tp1, tp2, entry, None, out,
                               title_line=title, signal_version="")
        return out
    except Exception as e:
        print("[frs] card err", str(e)[:120])
        return None


def fmt(sym, price, sig, sl=None, tp1=None, tp2=None):
    long = sig["side"] == "LONG"
    arrow = "🟢 做多" if long else "🔴 做空"
    if sl is None or tp1 is None or tp2 is None:
        _, sl, tp1, tp2, _ = levels(price, sig["side"], base=base_of(sym))
    sl_pct = abs(sl - price) / price * 100 if price else 0

    def p(v):
        return ("%.6g" % v)

    base = base_of(sym)
    exp_tag = " ⚗️實驗" if sig["exp"] else ""
    # 標的與點位用反引號＝等寬可複製模板（TG/DC 點一下即複製）
    return "\n".join([
        "🎯 新訊號 ·「%s」%s" % (sig["name"], exp_tag),
        "%s  `%sUSDT`" % (arrow, base),
        "━━━━━━━━━━━━",
        "進場　`%s`" % p(price),
        "停損　`%s`　（約 -%.1f%%）" % (p(sl), sl_pct),
        "停利　`%s`  /  `%s`" % (p(tp1), p(tp2)),
        "━━━━━━━━━━━━",
        "💡 怎麼跟（新手照做）",
        "　• 投入：本金的 2~5%",
        "　• 槓桿：5x 以內就好",
        "　• 不會抓價位就照上面數字（點價格可複製）",
        "📊 依據：%s" % sig["reason"],
        "📈 %s" % sig["winrate"],
        "🛡 系統自動追蹤，到價會再提醒",
        "",
        "⚠️ 合約有風險，請控制倉位，盈虧自負。",
    ])


# ── 發送 ────────────────────────────────────────────────────
def send_tg(text):
    if not TG_TOKEN:
        return False
    try:
        r = httpx.post("https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN,
                       json={"chat_id": TG_CHAT, "message_thread_id": TG_THREAD,
                             "text": text, "parse_mode": "Markdown",
                             "disable_web_page_preview": True}, timeout=15)
        return r.json().get("ok", False)
    except Exception as e:
        print("[frs] TG err", str(e)[:80]); return False


def send_tg_photo(path, caption):
    if not TG_TOKEN:
        return False
    try:
        with open(path, "rb") as f:
            r = httpx.post("https://api.telegram.org/bot%s/sendPhoto" % TG_TOKEN,
                           data={"chat_id": str(TG_CHAT), "message_thread_id": str(TG_THREAD),
                                 "caption": caption[:1020], "parse_mode": "Markdown"},
                           files={"photo": ("card.png", f, "image/png")}, timeout=30)
        return r.json().get("ok", False)
    except Exception as e:
        print("[frs] TG photo err", str(e)[:80]); return False


def send_dc(text):
    if not DC_TOKEN:
        return False
    try:
        r = httpx.post("https://discord.com/api/v10/channels/%s/messages" % DC_CHANNEL,
                       headers={"Authorization": "Bot %s" % DC_TOKEN},
                       json={"content": text}, timeout=15)
        return r.status_code in (200, 201)
    except Exception as e:
        print("[frs] DC err", str(e)[:80]); return False


def send_dc_photo(path, caption):
    if not DC_TOKEN:
        return False
    try:
        with open(path, "rb") as f:
            r = httpx.post("https://discord.com/api/v10/channels/%s/messages" % DC_CHANNEL,
                           headers={"Authorization": "Bot %s" % DC_TOKEN},
                           data={"payload_json": json.dumps({"content": caption})},
                           files={"files[0]": ("card.png", f, "image/png")}, timeout=30)
        return r.status_code in (200, 201)
    except Exception as e:
        print("[frs] DC photo err", str(e)[:80]); return False


def _today_key():
    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d")


def load_breaker():
    try:
        with open(BREAKER, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def is_paused(breaker, name):
    info = breaker.get(name) or {}
    return time.time() < (info.get("paused_until") or 0)


def log_signal(sym, sig, entry, sl, tp1, tp2):
    rec = {"ts": int(time.time()), "sym": sym, "side": sig["side"],
           "strategy": sig["name"], "entry": entry, "sl": sl, "tp1": tp1,
           "tp2": tp2, "status": "open"}
    try:
        with open(SIGNALS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print("[frs] log err", str(e)[:60])


def main():
    candidates = []  # (sym, price, sig)

    # 來源 1：sniper snapshot → 資費反殺（多）
    snap = _last_line_json(SNIPER_SNAP)
    if snap and (time.time() - (fnum(snap.get("ts")) or 0)) <= SNAP_MAX_AGE_S:
        for row in snap.get("rows", []):
            sym = row.get("sym"); price = fnum(row.get("price"))
            if not sym or not price:
                continue
            sig = classify_long(row)
            if sig:
                candidates.append((sym, price, sig))

    # 來源 2：multilens snapshot → 大戶純空（空）
    candidates += whale_short_candidates()

    state = load_state()
    breaker = load_breaker()
    now = time.time()
    day = _today_key()
    day_cnt = int(state.get("_day_" + day, 0))
    pushed = 0

    for sym, price, sig in candidates:
        if day_cnt + pushed >= DAILY_CAP:
            print("[frs] 已達每日上限 %d，跳過其餘" % DAILY_CAP)
            break
        if is_paused(breaker, sig["name"]):
            print("[frs] %s 連虧熔斷暫停中，跳過 %s" % (sig["name"], sym))
            continue
        key = "%s_%s" % (sym, sig["side"])
        if now - state.get(key, 0) < COOLDOWN_H * 3600:
            continue
        entry, sl, tp1, tp2, _ = levels(price, sig["side"], base=base_of(sym))
        text = fmt(sym, price, sig, sl, tp1, tp2)
        if DRY:
            print("----- WOULD PUSH -----\n" + text)
            card = make_card(sym, sig, entry, sl, tp1, tp2)
            print("[card]", card or "(文字模式)")
            state[key] = now; pushed += 1
            continue
        card = make_card(sym, sig, entry, sl, tp1, tp2)
        if card:
            ok_tg = send_tg_photo(card, text); ok_dc = send_dc_photo(card, text)
        else:
            ok_tg = send_tg(text); ok_dc = send_dc(text)
        if ok_tg or ok_dc:
            state[key] = now; pushed += 1
            log_signal(sym, sig, entry, sl, tp1, tp2)
            print("[frs] pushed %s %s img=%s tg=%s dc=%s" % (sym, sig["side"], bool(card), ok_tg, ok_dc))

    if pushed and not DRY:
        state["_day_" + day] = day_cnt + pushed
    if not DRY:
        save_state(state)
    print("[frs] done pushed=%d (dry=%s)" % (pushed, DRY))


if __name__ == "__main__":
    main()
