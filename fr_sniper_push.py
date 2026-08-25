"""
狙擊訊號推播 — 圖文版（K 線卡片 + caption），推到 TG（區塊鏈船長 thread 250）+ DC。

已推播：
  多｜資費反殺      sniper_snapshots：side=LONG & confirmed & aligned>=4 & 資費<=-0.04%
  多｜四支新選手     altsignal_snapshots：BRKq / TKUP / BTCR / WHAL（備註「新選手訊號」）
小盤妖股 SMCP 仍走 collector HAS_PUSH，不在本腳本。

大戶純空（whale_pure_short_opt）預設停推（實盤全期負期望）。設 FRS_WHALE_SHORT=1 才恢復。

停利停損：15m ATR×1.8 動態 SL（2.2%~5.5% 夾限）+ TP1=1.5R / TP2=2.5R。
風控：同幣同策略 6h 冷卻、每日上限、低流動/股票代幣過濾。
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
ALTSIGNAL_SNAP = os.path.join(DIR, "altsignal_snapshots.jsonl")
STATE = os.path.join(DIR, "fr_sniper_push_state.json")
CJK_FONT = os.path.join(DIR, "fonts", "cjk.ttc")

TG_TOKEN = os.environ.get("TG_TOKEN", "")
DC_TOKEN = os.environ.get("DC_TOKEN", "")
TG_CHAT = os.environ.get("FRS_TG_CHAT", "-1003611242392")
TG_THREAD = int(os.environ.get("FRS_TG_THREAD", "250"))
DC_CHANNEL = os.environ.get("FRS_DC_CHANNEL", "1493134120186941470")
TRACKER_URL = os.environ.get("SIGNAL_TRACKER_URL", "http://signal-tracker:8004").strip().rstrip("/")
_GATE_BASES_CACHE = {"ts": 0.0, "bases": None, "url": ""}
GATE_CONTRACTS_CACHE_TTL = float(os.environ.get("GATE_CONTRACTS_CACHE_TTL", "3600"))

COOLDOWN_H = float(os.environ.get("FRS_COOLDOWN_H", "6"))
DAILY_CAP = int(os.environ.get("FRS_DAILY_CAP", "18"))   # 資費反殺 + 四支新選手
PLAYER_DAILY_CAP = int(os.environ.get("FRS_PLAYER_DAILY_CAP", "2"))
SNAP_MAX_AGE_S = 1800
DRY = os.environ.get("DRY_RUN", "0") == "1"
WHALE_SHORT_ON = os.environ.get("FRS_WHALE_SHORT", "0") == "1"
PLAYERS_ON = os.environ.get("FRS_PLAYERS_ENABLED", "1") != "0"

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

# 擂台第3季影子（2026-08-24）；推播文案用，實盤 jsonl 有結案後改走 format_live_winrate。
PLAYER_STRATS = {
    "oi_taker_breakout_q": {
        "quality": "trend_follow",
        "name": "突破手·品質", "code": "BRKq", "fresh": True,
        "reason": "OI＋價＋主買突破，且日線已偏多（品質濾網）→ 順勢做多",
        "wr": 60.3, "n": 68, "avg_R": 0.719, "mdd": -10.8,
    },
    "taker_surge_long": {
        "quality": "trend_follow",
        "name": "主買狂潮", "code": "TKUP", "fresh": True,
        "reason": "主買佔比暴衝（≥65%）→ 順勢做多",
        "wr": 64.5, "n": 110, "avg_R": 0.884, "mdd": -10.8,
    },
    "btc_regime_momo_long": {
        "quality": "trend_follow",
        "name": "BTC閘門動能", "code": "BTCR", "fresh": True,
        "reason": "BTC 偏多 regime 才放行山寨動能做多（排除 BTC/ETH）",
        "wr": 65.5, "n": 165, "avg_R": 1.189, "mdd": -17.3,
    },
    "whale_accum_long": {
        "quality": "trend_follow",
        "name": "鯨魚雙吸", "code": "WHAL", "fresh": True,
        "reason": "現貨與永續同步淨流入＝大戶雙吸 → 順勢做多",
        "wr": 62.6, "n": 147, "avg_R": 1.132, "mdd": -11.3,
    },
}

BACKTEST_REF = {
    "資費反殺": {"wr": 52.6, "n": 344},
    "大戶純空": {"wr": 58.2, "n": 146},
}
for _meta in PLAYER_STRATS.values():
    BACKTEST_REF[_meta["name"]] = {
        "wr": _meta["wr"], "n": _meta["n"],
        "avg_R": _meta["avg_R"], "mdd": _meta["mdd"],
    }
STATS_SINCE_TS = int(os.environ.get("FRS_STATS_SINCE_TS", "0"))  # 0=全部實盤 log


def _load_signal_records():
    recs = []
    if not os.path.exists(SIGNALS_LOG):
        return recs
    try:
        with open(SIGNALS_LOG, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    recs.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return recs


def strategy_live_stats(strategy_name):
    """從 frs_signals.jsonl 累積實盤結案樣本（每 15 分 frs_settle 更新 status）。"""
    out = {"win": 0, "loss": 0, "timeout": 0, "open": 0, "total": 0}
    for rec in _load_signal_records():
        if rec.get("strategy") != strategy_name:
            continue
        ts = int(fnum(rec.get("ts")) or 0)
        if STATS_SINCE_TS and ts < STATS_SINCE_TS:
            continue
        st = (rec.get("status") or "open").lower()
        if st in out:
            out[st] += 1
        else:
            out[st] = out.get(st, 0) + 1
        out["total"] += 1
    return out


def format_live_winrate(strategy_name, exp=False, log_path=None, ref_note=None):
    """推播用勝率/樣本行（實盤累積；新選手附擂台本季勝率）。"""
    if exp:
        return "實驗中（小倉觀察）"
    # SMCP 可指定獨立 log；預設仍讀 FRS SIGNALS_LOG
    if log_path:
        recs = []
        try:
            with open(log_path, encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        recs.append(json.loads(ln))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            recs = []
        out = {"win": 0, "loss": 0, "timeout": 0, "open": 0, "total": 0}
        for rec in recs:
            if rec.get("strategy") != strategy_name:
                continue
            st = (rec.get("status") or "open").lower()
            if st in out:
                out[st] += 1
            else:
                out[st] = out.get(st, 0) + 1
            out["total"] += 1
        st = out
    else:
        st = strategy_live_stats(strategy_name)
    settled = st["win"] + st["loss"]
    open_n = st["open"]
    ref = BACKTEST_REF.get(strategy_name, {})
    ref_wr = ref.get("wr")
    ref_n = ref.get("n")
    ref_tail = ""
    if ref_note:
        ref_tail = "；" + ref_note
    elif ref_wr and ref_n:
        if ref.get("avg_R") is not None:
            extra = "｜avgR %+.3f｜MDD %.1f%%" % (ref["avg_R"], ref.get("mdd") or 0)
            ref_tail = "；擂台本季勝率 %.1f%%（n=%d%s）" % (ref_wr, ref_n, extra)
        else:
            ref_tail = "；回測參考 %.1f%%（n=%d）" % (ref_wr, ref_n)
    if settled >= 1:
        wr = st["win"] / settled * 100.0
        return "實盤累積：勝率 %.1f%%｜結案 %d 筆（勝 %d 敗 %d%s）｜進行中 %d%s" % (
            wr, settled, st["win"], st["loss"],
            " 逾時 %d" % st["timeout"] if st["timeout"] else "",
            open_n, ref_tail,
        )
    if ref_wr and ref_n and ref.get("avg_R") is not None and not ref_note:
        return "擂台本季：勝率 %.1f%%（n=%d）｜avgR %+.3f｜MDD %.1f%%" % (
            ref_wr, ref_n, ref["avg_R"], ref.get("mdd") or 0,
        )
    return "實盤累積：樣本收集中（進行中 %d%s）" % (open_n, ref_tail)


def attach_live_winrate(sig, log_path=None, ref_note=None):
    sig["winrate"] = format_live_winrate(
        sig.get("name", ""), sig.get("exp", False), log_path=log_path, ref_note=ref_note
    )
    return sig



EXCLUDE_BASES = {
    "SPX500", "US500", "NDX", "NAS100", "DJI", "US30",
    "TSLA", "NVDA", "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "AMD",
    "COIN", "MSTR", "NFLX", "BABA", "SKHX", "SKHYNIX", "DRAM", "HOOD", "PLTR",
    "XAU", "XAG", "XAUT", "PAXG", "XTI", "XBR", "NG", "GOLD", "SILVER", "OIL", "WTI",
    "EUR", "GBP", "JPY", "USD", "USDJPY", "EURUSD",
    "SP500", "USOIL", "BRENTOIL", "XYZ100",
    "SAMSUNG", "SAMSUNGEM", "SKHY", "SKHYNIX", "SOXL", "SNDK", "SNXX",
    "NBIS", "MRVL", "KORU",
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


def _gate_contracts_url():
    """推播用合約白名單 URL。

    2026-07-20：預設 **mainnet**（~800+）。
    勿再吃 GATE_BASE_URL／GATE_TESTNET——那是 gate-quant 交易端設定，
    容器若跑 testnet 會把頻道推播鎖死在 ~63 幣（INJ/BZ/小盤全 skip）。
    覆寫：GATE_CONTRACTS_URL=... 或 FRS_GATE_CONTRACTS=testnet
    """
    explicit = (os.environ.get("GATE_CONTRACTS_URL") or "").strip()
    if explicit:
        return explicit
    mode = (os.environ.get("FRS_GATE_CONTRACTS") or "mainnet").strip().lower()
    if mode in ("testnet", "tn", "sim"):
        return "https://api-testnet.gateapi.io/api/v4/futures/usdt/contracts"
    return "https://api.gateio.ws/api/v4/futures/usdt/contracts"



def fetch_gate_tradable_bases(force=False):
    """回傳 Gate USDT 永續可交易 base 集合。失敗回空集合（呼叫端 fail-open）。"""
    now = time.time()
    url = _gate_contracts_url()
    c = _GATE_BASES_CACHE
    if (
        not force
        and c["bases"] is not None
        and c.get("url") == url
        and (now - float(c["ts"] or 0)) < GATE_CONTRACTS_CACHE_TTL
    ):
        return c["bases"]
    bases = set()
    try:
        r = httpx.get(url, timeout=15)
        if r.status_code != 200:
            print("[frs] Gate contracts HTTP %s url=%s" % (r.status_code, url[:60]))
            return bases
        rows = r.json()
        if not isinstance(rows, list):
            return bases
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "")
            status = str(row.get("status") or "").lower()
            if not name.endswith("_USDT"):
                continue
            if status and status not in ("trading", "tradable", "online", ""):
                continue
            base = name[:-5].upper()
            if base.startswith("1000") and len(base) > 4:
                bases.add(base[4:])  # 1000PEPE → 也認 PEPE
            if base:
                bases.add(base)
        print("[frs] Gate tradable bases=%d url=%s" % (len(bases), "testnet" if "testnet" in url else "main"))
    except Exception as e:
        print("[frs] Gate contracts err: %s" % str(e)[:80])
    c["ts"], c["bases"], c["url"] = now, bases, url
    return bases



def is_gate_tradable(sym, bases=None):
    """bases 空＝API 失敗 → fail-open（不擋推播，交給下游）。"""
    if bases is None:
        bases = fetch_gate_tradable_bases()
    if not bases:
        return True
    b = base_of(sym)
    return b in bases or ("1000" + b) in bases



def resolve_live_entry(sym, price):
    """SMCP 用：以 Gate 即時價校正進場；失敗則用 snapshot 價。"""
    base = base_of(sym)
    live = gate_price(base) if base else None
    if live and live > 0:
        return live, None
    if price and float(price) > 0:
        return float(price), None
    return None, "no_live_price"

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
                "exp": False, "quality": "counter_trend"}
    short_on = os.environ.get("FRS_SHORT_ENABLED", "0") == "1"
    if short_on and side == "SHORT" and conf and aligned >= 4 and SHORT_FR <= fr <= SHORT_FR_CAP:
        return {"side": "SHORT", "name": "資費過熱",
                "reason": "多頭過熱（資費極正 %.3f%%）→ 回吐做空" % (fr * 100),
                "exp": True}
    return None


def _row_vol_ok(row):
    vol_m = fnum(row.get("vol24_m"))
    if vol_m is None:
        usd = fnum(row.get("vol24_usd"))
        vol_m = (usd / 1_000_000.0) if usd else 0
    return (vol_m or 0) >= MIN_VOL_M


def _row_price(row, base):
    for k in ("price", "mark", "last"):
        p = fnum(row.get(k))
        if p and p > 0:
            return p
    return gate_price(base)


def player_sig_from_row(row):
    """altsignal row → 新選手訊號 dict；不合格回 None。"""
    key = row.get("strategy")
    meta = PLAYER_STRATS.get(key)
    if not meta:
        return None
    if (row.get("side") or "").upper() != "LONG":
        return None
    base = base_of(row.get("sym"))
    if not base or base in EXCLUDE_BASES:
        return None
    if not _row_vol_ok(row):
        return None
    return {
        "side": "LONG",
        "name": meta["name"],
        "code": meta["code"],
        "reason": meta["reason"],
        "exp": False,
        "fresh": True,
        "key": key,
        "quality": meta.get("quality"),
    }


def player_candidates_from_snap(snap, now=None, price_fn=None):
    """Pure helper：從一份 altsignal snapshot 抽出新選手候選。"""
    out = []
    if not snap:
        return out
    now = time.time() if now is None else now
    age = now - (fnum(snap.get("ts")) or 0)
    if age > SNAP_MAX_AGE_S:
        return out
    get_px = price_fn or (lambda base, row: _row_price(row, base))
    for row in snap.get("rows") or []:
        sig = player_sig_from_row(row)
        if not sig:
            continue
        base = base_of(row.get("sym"))
        price = get_px(base, row)
        if not price:
            continue
        out.append((row.get("sym"), price, sig))
    return out


def _iter_recent_jsonl(path, max_lines=60):
    """由檔尾往回讀最近幾行 JSON（altsignal 常寫空 rows 尾行，不能只看最後一行）。"""
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()[-max_lines:]
    except OSError:
        return []
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def player_candidates():
    """altsignal_snapshots 新鮮窗內的四支新選手（去重）。"""
    if not PLAYERS_ON:
        return []
    now = time.time()
    seen = set()
    out = []
    # 由新到舊掃，同幣同策略只留最新一筆
    for snap in reversed(_iter_recent_jsonl(ALTSIGNAL_SNAP)):
        for item in player_candidates_from_snap(snap, now=now):
            sym, _price, sig = item
            key = "%s|%s" % (sym, sig.get("key") or sig.get("name"))
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


def whale_short_candidates():
    """multilens snapshot → 大戶純空。預設關閉（實盤全期負期望）。"""
    out = []
    if not WHALE_SHORT_ON:
        return out
    snap = _last_line_json(MULTILENS_SNAP)
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
               "exp": False}
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
    title = "🎯 新訊號 ·「%s」%s" % (sig["name"], exp_tag)
    lines = [title]
    if sig.get("fresh"):
        lines.append("🆕 新選手訊號")
    # 標的與點位用反引號＝等寬可複製模板（TG/DC 點一下即複製）
    lines += [
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
        "📈 %s" % (sig.get("winrate") or format_live_winrate(sig.get("name", ""), sig.get("exp"))),
        "🛡 系統自動追蹤，到價會再提醒",
        "",
        "⚠️ 合約有風險，請控制倉位，盈虧自負。",
    ]
    return "\n".join(lines)


# ── 發送 ────────────────────────────────────────────────────
def _tg_msg_id(resp_json):
    if not isinstance(resp_json, dict) or not resp_json.get("ok"):
        return None
    try:
        return int(resp_json.get("result", {}).get("message_id"))
    except (TypeError, ValueError):
        return None




def send_tg(text):
    if not TG_TOKEN:
        return False, None
    try:
        r = httpx.post("https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN,
                       json={"chat_id": TG_CHAT, "message_thread_id": TG_THREAD,
                             "text": text, "parse_mode": "Markdown",
                             "disable_web_page_preview": True}, timeout=15)
        j = r.json()
        return bool(j.get("ok")), _tg_msg_id(j)
    except Exception as e:
        print("[frs] TG err", str(e)[:80]); return False, None


def send_tg_photo(path, caption):
    if not TG_TOKEN:
        return False, None
    try:
        with open(path, "rb") as f:
            r = httpx.post("https://api.telegram.org/bot%s/sendPhoto" % TG_TOKEN,
                           data={"chat_id": str(TG_CHAT), "message_thread_id": str(TG_THREAD),
                                 "caption": caption[:1020], "parse_mode": "Markdown"},
                           files={"photo": ("card.png", f, "image/png")}, timeout=30)
        j = r.json()
        return bool(j.get("ok")), _tg_msg_id(j)
    except Exception as e:
        print("[frs] TG photo err", str(e)[:80]); return False, None


def send_dc(text):
    # 2026-08-04 用戶拍板：訊號只留 TG，停 Discord（設 DISCORD_MIRROR=1 可臨時重開）
    if os.environ.get("DISCORD_MIRROR", "0").strip().lower() not in ("1", "true", "yes", "on"):
        return False, None
    if not DC_TOKEN:
        return False, None
    try:
        r = httpx.post("https://discord.com/api/v10/channels/%s/messages" % DC_CHANNEL,
                       headers={"Authorization": "Bot %s" % DC_TOKEN},
                       json={"content": text}, timeout=15)
        if r.status_code not in (200, 201):
            return False, None
        try:
            return True, str(r.json().get("id"))
        except Exception:
            return True, None
    except Exception as e:
        print("[frs] DC err", str(e)[:80]); return False, None


def send_dc_photo(path, caption):
    # 2026-08-04 用戶拍板：訊號只留 TG，停 Discord（設 DISCORD_MIRROR=1 可臨時重開）
    if os.environ.get("DISCORD_MIRROR", "0").strip().lower() not in ("1", "true", "yes", "on"):
        return False, None
    if not DC_TOKEN:
        return False, None
    try:
        with open(path, "rb") as f:
            r = httpx.post("https://discord.com/api/v10/channels/%s/messages" % DC_CHANNEL,
                           headers={"Authorization": "Bot %s" % DC_TOKEN},
                           data={"payload_json": json.dumps({"content": caption})},
                           files={"files[0]": ("card.png", f, "image/png")}, timeout=30)
        if r.status_code not in (200, 201):
            return False, None
        try:
            return True, str(r.json().get("id"))
        except Exception:
            return True, None
    except Exception as e:
        print("[frs] DC photo err", str(e)[:80]); return False, None



def _norm_symbol(sym):
    s = (sym or "").upper().replace("_", "")
    if not s.endswith("USDT"):
        s += "USDT"
    return s



def register_with_tracker(sym, sig, entry, sl, tp1, tp2, tg_msg_id=None, dc_msg_id=None):
    """推播成功後註冊 signal-tracker，啟用停損/停利追蹤推播。"""
    if not TRACKER_URL:
        return None
    side = "long" if sig.get("side") == "LONG" else "short"
    pl = {"strategy": sig.get("name"), "jackbot_pushed": True}
    if sig.get("quality"):
        pl["quality"] = sig["quality"]
    if dc_msg_id:
        pl["dc_message_id"] = str(dc_msg_id)
    payload = {
        "source": "position_change",
        "symbol": _norm_symbol(sym),
        "side": side,
        "entry_price": float(entry),
        "sl_price": float(sl),
        "tp1_price": float(tp1),
        "tp2_price": float(tp2) if tp2 else None,
        "leverage": 10,
        "tg_chat_id": int(TG_CHAT),
        "tg_message_id": tg_msg_id,
        "payload": pl,
    }
    for attempt in range(3):
        try:
            r = httpx.post("%s/signals" % TRACKER_URL, json=payload, timeout=12)
            if r.status_code == 200:
                data = r.json()
                tid = data.get("id")
                print("[frs] tracker #%s %s %s" % (tid, data.get("action"), _norm_symbol(sym)))
                return tid
            print("[frs] tracker HTTP %s: %s" % (r.status_code, r.text[:120]))
        except Exception as e:
            print("[frs] tracker err (try %d): %s" % (attempt + 1, str(e)[:80]))
        if attempt < 2:
            time.sleep(1.0 * (2 ** attempt))
    return None



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

    # 來源 2：altsignal → 四支新選手（BRKq / TKUP / BTCR / WHAL）
    candidates += player_candidates()
    # 來源 3：大戶純空 — 預設關閉
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
        key = "%s_%s_%s" % (sym, sig["side"], sig["name"])
        if now - state.get(key, 0) < COOLDOWN_H * 3600:
            continue
        if sig.get("fresh"):
            pday = int(state.get("_pday_%s_%s" % (day, sig["name"]), 0))
            if pday >= PLAYER_DAILY_CAP:
                print("[frs] %s 今日已達每策略上限 %d，跳過 %s" % (
                    sig["name"], PLAYER_DAILY_CAP, sym))
                continue
        attach_live_winrate(sig)
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
            ok_tg, tg_mid = send_tg_photo(card, text); ok_dc, dc_mid = send_dc_photo(card, text)
        else:
            ok_tg, tg_mid = send_tg(text); ok_dc, dc_mid = send_dc(text)
        if ok_tg or ok_dc:
            state[key] = now; pushed += 1
            if sig.get("fresh"):
                pk = "_pday_%s_%s" % (day, sig["name"])
                state[pk] = int(state.get(pk, 0)) + 1
            tid = register_with_tracker(sym, sig, entry, sl, tp1, tp2, tg_mid, dc_mid)
            log_signal(sym, sig, entry, sl, tp1, tp2)
            print("[frs] pushed %s %s img=%s tg=%s dc=%s tracker=%s" % (
                sym, sig["side"], bool(card), ok_tg, ok_dc, tid))

    if pushed and not DRY:
        state["_day_" + day] = day_cnt + pushed
    if not DRY:
        save_state(state)
    print("[frs] done pushed=%d (dry=%s)" % (pushed, DRY))


if __name__ == "__main__":
    main()
