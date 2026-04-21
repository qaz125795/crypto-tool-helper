import logging
import os
import time
from typing import Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageDraw, ImageFont


logger = logging.getLogger(__name__)

_FONT_CACHE: Dict[int, ImageFont.ImageFont] = {}


def _load_cjk_font(font_size: int) -> ImageFont.ImageFont:
    """
    PIL 預設字型常不支援中文，會導致圖片內文字顯示「怪怪的」。
    這裡嘗試常見 CJK 字型；找不到就回退到 PIL 預設字型。
    """
    cached = _FONT_CACHE.get(font_size)
    if cached is not None:
        return cached

    candidates = [
        # Windows
        r"C:\Windows\Fonts\msyh.ttc",          # 微軟正黑體
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\mingliu.ttc",        # 明體
        r"C:\Windows\Fonts\simhei.ttf",        # 黑體(可能存在 ttf)
        # Linux (GitHub Actions / 多數容器)
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]

    for p in candidates:
        try:
            if os.path.exists(p):
                font = ImageFont.truetype(p, font_size)
                _FONT_CACHE[font_size] = font
                return font
        except Exception:
            continue

    font = ImageFont.load_default()
    _FONT_CACHE[font_size] = font
    return font


def _normalize_base_symbol(symbol_base: str) -> str:
    """
    把 1000/1000000 倍數前綴的 meme 幣統一轉回基礎代號，
    例如：1000000CHEEMS -> CHEEMS
    """
    clean = (symbol_base or "").replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
    # 常見合約倍數前綴（只在「字母在後面」的情境才剝離，避免誤傷）
    for prefix in ("1000000", "1000"):
        if clean.startswith(prefix) and len(clean) > len(prefix):
            tail = clean[len(prefix) :]
            if any(ch.isalpha() for ch in tail):
                clean = tail
                break
    return clean


def _safe_float(x, default=None):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _fetch_binance_ohlc_5m(symbol_base: str, limit: int = 60) -> Optional[List[Dict]]:
    clean = _normalize_base_symbol(symbol_base)
    candidates = [f"{clean}USDT", f"1000{clean}USDT", f"1000000{clean}USDT"]
    for sym_pair in candidates:
        try:
            r = requests.get(
                "https://fapi.binance.com/fapi/v1/klines",
                params={"symbol": sym_pair, "interval": "5m", "limit": limit},
                timeout=10,
            )
            if r.status_code != 200:
                continue
            raw = r.json()
            # 5m K 線用於畫圖；少於一定數量就直接當作失敗，避免畫面空白或 scaler 崩潰。
            # 原本門檻是 10，實務上不少幣會抓不到滿 10 根而導致整張卡片跳過。
            if not isinstance(raw, list) or len(raw) < 2:
                continue
            out = []
            for row in raw[-limit:]:
                if not isinstance(row, list) or len(row) < 6:
                    continue
                t_ms, o, h, l, c, v = row[0], row[1], row[2], row[3], row[4], row[5]
                t_sec = int(t_ms / 1000) if t_ms and t_ms > 1e12 else int(t_ms or 0)
                out.append(
                    {
                        "t": t_sec,
                        "o": float(o),
                        "h": float(h),
                        "l": float(l),
                        "c": float(c),
                        "v": float(v) if v is not None else 0.0,
                    }
                )
            if len(out) >= 2:
                return out
        except Exception:
            continue
    return None


def _fetch_bybit_ohlc_5m(symbol_base: str, limit: int = 60) -> Optional[List[Dict]]:
    clean = _normalize_base_symbol(symbol_base)
    interval_map = {"5m": "5"}
    bybit_interval = interval_map.get("5m", "5")
    for sym_pair in [f"{clean}USDT", f"1000{clean}USDT", f"1000000{clean}USDT"]:
        try:
            r = requests.get(
                "https://api.bybit.com/v5/market/kline",
                params={
                    "category": "linear",
                    "symbol": sym_pair,
                    "interval": bybit_interval,
                    "limit": limit,
                },
                timeout=10,
            )
            if r.status_code != 200:
                continue
            j = r.json()
            if j.get("retCode") != 0:
                continue
            raw = j.get("result", {}).get("list", [])
            if not isinstance(raw, list) or len(raw) < 2:
                continue
            raw = list(reversed(raw))[-limit:]
            out = []
            for bar in raw:
                # [timestamp, open, high, low, close, volume, turnover]
                if not isinstance(bar, list) or len(bar) < 6:
                    continue
                ts = bar[0]
                t_sec = int(ts / 1000) if ts and ts > 1e12 else int(ts or 0)
                out.append(
                    {
                        "t": t_sec,
                        "o": float(bar[1]),
                        "h": float(bar[2]),
                        "l": float(bar[3]),
                        "c": float(bar[4]),
                        "v": float(bar[5]) if bar[5] is not None else 0.0,
                    }
                )
            if len(out) >= 2:
                return out
        except Exception:
            continue
    return None


def _fetch_bingx_spot_ohlc_5m(symbol_base: str, limit: int = 60) -> Optional[List[Dict]]:
    clean = _normalize_base_symbol(symbol_base)
    sym_pair = f"{clean}-USDT"
    try:
        r = requests.get(
            "https://open-api.bingx.com/openApi/spot/v2/market/kline",
            params={"symbol": sym_pair, "interval": "5m", "limit": limit},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        j = r.json()
        raw = j.get("data") if isinstance(j, dict) else j
        if not isinstance(raw, list) or len(raw) < 2:
            return None
        out = []
        for row in raw[-limit:]:
            # [ts, open, high, low, close, volume, ...]
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            t_ms = row[0]
            t_sec = int(t_ms / 1000) if t_ms and t_ms > 1e12 else int(t_ms or 0)
            out.append(
                {
                    "t": t_sec,
                    "o": float(row[1]),
                    "h": float(row[2]),
                    "l": float(row[3]),
                    "c": float(row[4]),
                    "v": float(row[5]) if row[5] is not None else 0.0,
                }
            )
        if len(out) >= 2:
            return out
    except Exception:
        return None
    return None


def _ts_to_unix_sec(t_raw) -> int:
    try:
        if t_raw is None:
            return 0
        t = float(t_raw)
        ti = int(t)
        if ti > 1e12:
            ti = int(ti / 1000)
        return ti
    except (TypeError, ValueError):
        return 0


def _fetch_gate_futures_ohlc_5m(symbol_base: str, limit: int = 60) -> Optional[List[Dict]]:
    """Gate.io USDT 永續 5m（免 Key），覆蓋僅在某所上線的山寨；格式對齊 render_kline_oi_card。"""
    clean = _normalize_base_symbol(symbol_base)
    for contract in (f"{clean}_USDT", f"1000{clean}_USDT"):
        try:
            r = requests.get(
                "https://api.gateio.ws/api/v4/futures/usdt/candlesticks",
                params={"contract": contract, "interval": "5m", "limit": limit},
                timeout=12,
            )
            if r.status_code != 200:
                continue
            raw = r.json()
            if not isinstance(raw, list) or len(raw) < 2:
                continue
            out: List[Dict] = []
            for bar in raw[-limit:]:
                if not isinstance(bar, dict):
                    continue
                t_sec = _ts_to_unix_sec(bar.get("t") or bar.get("time"))
                if not t_sec:
                    continue
                try:
                    o = float(bar.get("o") or 0)
                    h = float(bar.get("h") or o)
                    l = float(bar.get("l") or o)
                    c = float(bar.get("c") or o)
                    v = float(bar.get("v") or 0)
                except (TypeError, ValueError):
                    continue
                out.append({"t": t_sec, "o": o, "h": h, "l": l, "c": c, "v": v})
            if len(out) >= 2:
                logger.info(f"[K線卡片OHLC] {clean}: Gate 永續 5m 備援 {len(out)} 根 ({contract})")
                return out
        except Exception as e:
            logger.debug(f"[K線卡片OHLC] Gate {contract}: {e}")
            continue
    return None


def _fetch_coinglass_ohlc_5m(symbol_base: str, limit: int = 60) -> Optional[List[Dict]]:
    """CoinGlass 聚合期貨 K 線（需 CG_API_KEY），與 jackbot 指標管線一致作最終備援。"""
    cg_key = (os.getenv("CG_API_KEY") or "").strip()
    if not cg_key:
        return None
    clean = _normalize_base_symbol(symbol_base)
    base = "https://open-api-v4.coinglass.com"
    headers = {"CG-API-KEY": cg_key, "accept": "application/json"}
    try_pairs = [f"{clean}USDT", f"1000{clean}USDT"]
    exchanges = ["Bybit", "OKX", "Binance", "Gate", "Bitget"]
    for exchange in exchanges:
        for sym_pair in try_pairs:
            try:
                r = requests.get(
                    f"{base}/api/futures/price/history",
                    headers=headers,
                    params={
                        "exchange": exchange,
                        "symbol": sym_pair,
                        "interval": "5m",
                        "limit": limit,
                    },
                    timeout=12,
                )
                if r.status_code == 429:
                    time.sleep(1.2)
                    continue
                if r.status_code != 200:
                    continue
                j = r.json()
                if j.get("code") not in (0, "0", 200, "200", None):
                    continue
                raw = j.get("data") or j.get("list") or []
                if not isinstance(raw, list) or len(raw) < 2:
                    continue
                out: List[Dict] = []
                for row in raw[-limit:]:
                    if isinstance(row, dict):
                        t_sec = _ts_to_unix_sec(
                            row.get("time") or row.get("timestamp") or row.get("t")
                        )
                        o = row.get("open") or row.get("o") or row.get("openPrice")
                        h = row.get("high") or row.get("h") or row.get("highPrice")
                        l = row.get("low") or row.get("l") or row.get("lowPrice")
                        c = row.get("close") or row.get("c") or row.get("closePrice")
                        v = row.get("volume") or row.get("v") or row.get("vol") or 0
                    elif isinstance(row, (list, tuple)) and len(row) >= 6:
                        t_sec = _ts_to_unix_sec(row[0])
                        o, h, l, c, v = row[1], row[2], row[3], row[4], row[5]
                    else:
                        continue
                    if not t_sec or o is None or c is None:
                        continue
                    try:
                        fo, fh, fl, fc = float(o), float(h or c), float(l or c), float(c)
                        fv = float(v) if v is not None else 0.0
                    except (TypeError, ValueError):
                        continue
                    out.append({"t": t_sec, "o": fo, "h": fh, "l": fl, "c": fc, "v": fv})
                if len(out) >= 2:
                    logger.info(
                        f"[K線卡片OHLC] {clean}: CoinGlass 5m 備援 {len(out)} 根 ({exchange}/{sym_pair})"
                    )
                    return out
            except Exception as e:
                logger.debug(f"[K線卡片OHLC] CG {exchange}/{sym_pair}: {e}")
                continue
    return None


def fetch_ohlc_5m(symbol_base: str, limit: int = 60) -> Optional[List[Dict]]:
    # 對齊 jackbot 技術指標降級順序：Binance → Bybit → Gate 永續 → BingX 現貨 → CoinGlass
    out = _fetch_binance_ohlc_5m(symbol_base, limit=limit)
    if out:
        return out
    out = _fetch_bybit_ohlc_5m(symbol_base, limit=limit)
    if out:
        return out
    out = _fetch_gate_futures_ohlc_5m(symbol_base, limit=limit)
    if out:
        return out
    out = _fetch_bingx_spot_ohlc_5m(symbol_base, limit=limit)
    if out:
        return out
    out = _fetch_coinglass_ohlc_5m(symbol_base, limit=limit)
    if out:
        return out
    logger.warning(
        f"[K線卡片OHLC] {_normalize_base_symbol(symbol_base)}: "
        f"Binance／Bybit／Gate／BingX／CoinGlass 皆未取得足夠 5m K 線"
    )
    return None


def fetch_coinglass_oi_5m(symbol_base: str, limit: int = 60) -> Optional[List[Dict]]:
    """
    回傳: [{"t": unix_sec, "v": float}, ...]
    """
    cg_api_key = os.getenv("CG_API_KEY")
    if not cg_api_key:
        return None
    cg_api_base = "https://open-api-v4.coinglass.com"
    base_symbol = _normalize_base_symbol(symbol_base)
    url = f"{cg_api_base}/api/futures/open-interest/aggregated-history"
    headers = {"CG-API-KEY": cg_api_key, "accept": "application/json"}
    try:
        r = requests.get(
            url,
            params={"symbol": base_symbol, "interval": "5m", "limit": limit},
            headers=headers,
            timeout=15,
        )
        if r.status_code != 200:
            return None
        j = r.json()
        if j.get("code") not in (0, "0", 200, "200", None):
            return None
        raw = j.get("data") or j.get("list") or []
        if not isinstance(raw, list) or len(raw) < 10:
            return None
        out = []
        for row in raw[-limit:]:
            if not isinstance(row, dict):
                continue
            t = row.get("t") or row.get("time") or row.get("timestamp") or 0
            if t and t > 1e12:
                t = int(t / 1000)
            t_sec = int(t or 0)
            if not t_sec:
                continue
            v = (
                row.get("c")
                or row.get("close")
                or row.get("v")
                or row.get("value")
                or row.get("openInterest")
                or row.get("oi")
                or row.get("value")
            )
            v_f = _safe_float(v, None)
            if v_f is None:
                continue
            out.append({"t": t_sec, "v": v_f})
        return out[-limit:] if len(out) >= 10 else None
    except Exception:
        return None


def render_kline_oi_card(
    symbol_base: str,
    direction_is_long: bool,
    ohlc_5m: List[Dict],
    oi_5m: Optional[List[Dict]],
    sl: Optional[float],
    tp1: Optional[float],
    tp2: Optional[float],
    entry: Optional[float],
    vwap: Optional[float],
    out_path: str,
    vwap_anchor: Optional[float] = None,
    anchor_fit_stars: int = 0,
    anchor_hint: str = "",
    ema20: Optional[float] = None,
    ema20_touch_low: Optional[float] = None,
    ema20_touch_high: Optional[float] = None,
    ema20_4h: Optional[float] = None,
    atr: Optional[float] = None,
    tp1_r: Optional[float] = None,
    tp2_r: Optional[float] = None,
    macro_badge: Optional[str] = None,
    signal_version: str = "",
    triggered_from_pending: bool = False,
    title_line: str = "",
) -> str:
    """
    用 PIL 畫：上半 K線、下半 OI 柱狀；並疊加水平線（SL/TP1/TP2/進場/VWAP/EMA20）。
    """
    width, height = 1000, 580
    pad_left, pad_right = 68, 18
    pad_top, pad_bottom = 16, 22
    # 放大 K 線區塊；OI 區維持可讀下限
    top_h = 475
    bot_h = height - pad_top - pad_bottom - top_h
    if bot_h < 52:
        bot_h = 52
        top_h = height - pad_top - pad_bottom - bot_h

    bg = (14, 18, 33)
    grid = (44, 50, 80)
    up_col = (0, 214, 124)
    down_col = (255, 75, 87)
    sl_col = (255, 70, 70)
    tp1_col = (0, 210, 110)
    tp2_col = (0, 160, 90)
    entry_col = (255, 200, 0)
    vwap_col = (90, 200, 255)
    vwap_anchor_col = (255, 195, 85)  # 金：錨定發動 VWAP（與 2h 主力 VWAP 區隔）
    oi_col = (110, 190, 255)
    ema20_col = (185, 105, 255)         # 紫：1H EMA20
    ema20_4h_col = (255, 195, 90)      # 金黃：4H EMA20
    ema20_touch_col = (0, 200, 255)    # 青：EMA20 回踩錨點
    text_col = (220, 230, 255)

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    font_title = _load_cjk_font(18)
    font_label = _load_cjk_font(14)

    plot_w = width - pad_left - pad_right
    plot_top_y0 = pad_top
    plot_top_y1 = pad_top + top_h
    plot_bot_y0 = plot_top_y1
    plot_bot_y1 = height - pad_bottom

    # grid
    for i in range(6):
        y = plot_top_y0 + int(top_h * i / 5)
        draw.line([(pad_left, y), (width - pad_right, y)], fill=grid, width=1)
    for i in range(4):
        y = plot_bot_y0 + int(bot_h * i / 3)
        draw.line([(pad_left, y), (width - pad_right, y)], fill=grid, width=1)

    ohlc_use = ohlc_5m[-60:] if ohlc_5m else []
    candle_slot = plot_w / 60.0
    candle_w = max(2, int(candle_slot * 0.70))

    def _winsor_lo_hi(vals: List[float], trim_ratio: float = 0.03) -> Tuple[float, float]:
        s = sorted(v for v in vals if v == v and v > 0)
        if not s:
            return 1.0, 1.0001
        if len(s) < 5:
            return s[0], s[-1]
        k = max(1, int(len(s) * trim_ratio))
        return float(s[k]), float(s[len(s) - 1 - k])

    # 價格縮放：先以 K 線高低 winsor 去極端影線，再「有條件」納入 SL/TP/VWAP（避免離譜價位壓扁蠟燭）
    hl_flat: List[float] = []
    for k in ohlc_use:
        try:
            hl_flat.extend([float(k["h"]), float(k["l"])])
        except (TypeError, ValueError, KeyError):
            continue
    if len(hl_flat) < 2:
        hl_flat = [1.0, 1.0001]
    p_lo_w, p_hi_w = _winsor_lo_hi(hl_flat)
    mid_c = (p_lo_w + p_hi_w) / 2.0
    span_c = max(p_hi_w - p_lo_w, mid_c * 1e-9)

    p_min, p_max = p_lo_w, p_hi_w
    for p in (sl, tp1, tp2, entry, vwap, vwap_anchor):
        pf = _safe_float(p, None)
        if pf is not None and pf > 0 and abs(pf - mid_c) <= span_c * 4.5:
            p_min = min(p_min, pf)
            p_max = max(p_max, pf)
    if p_max <= p_min:
        p_max = p_min + 1e-9
    margin = (p_max - p_min) * 0.05
    p_min -= margin
    p_max += margin

    def y_price(p: float) -> int:
        return plot_top_y1 - int((p - p_min) / (p_max - p_min) * top_h)

    # OI scale
    if oi_5m:
        oi_vals = [b["v"] for b in oi_5m if _safe_float(b.get("v"), None) is not None]
    else:
        oi_vals = []
    if not oi_vals:
        # proxy: use candle amp so chart isn't empty
        oi_vals = []
        for k in ohlc_5m:
            o, c = k.get("o"), k.get("c")
            if not o:
                continue
            amp = abs(c - o) / o
            oi_vals.append(max(0.0, amp * 1000.0))

    o_min, o_max = min(oi_vals), max(oi_vals)
    if o_max == o_min:
        o_max += 1.0

    def y_oi(v: float) -> int:
        # bottom bar: y from bot_y0..bot_y1
        return plot_bot_y1 - int((v - o_min) / (o_max - o_min) * bot_h)

    def draw_hline(price: Optional[float], col: Tuple[int, int, int], label: str):
        if price is None:
            return
        try:
            price = float(price)
        except Exception:
            return
        if price <= 0:
            return
        y = y_price(price)
        y = max(plot_top_y0, min(plot_top_y1 - 1, y))
        draw.line([(pad_left, y), (width - pad_right, y)], fill=col, width=2)

        # 只對你指定需要的水平線標示價格：SL + VWAP + 錨VW
        if label in ("SL", "VWAP", "錨VW"):
            txt = f"{label}:{price:.4f}"
            txt_show = txt if len(txt) <= 22 else (txt[:20] + "..")
            # 右側小字標籤（不畫整塊框），避免你說的怪字/怪框
            text_x = width - pad_right - 130
            text_y = max(plot_top_y0 + 2, min(plot_top_y1 - 18, y - 7))
            draw.text((text_x, text_y), txt_show, fill=col, font=font_label)

    # draw_hline 會只在 SL + VWAP 顯示數值

    top_text_y = 4
    if title_line:
        draw.text((pad_left, top_text_y), title_line[:80], fill=text_col, font=font_title)
        top_text_y += 22

    badge_chunks: List[str] = []
    if triggered_from_pending:
        badge_chunks.append("✅ 完美回踩觸發")
    if signal_version:
        if signal_version == "confirmed":
            badge_chunks.append("🚀 確定籌碼")
        elif signal_version == "pullback":
            badge_chunks.append("🎯 回踩進場")
        elif signal_version == "exhaustion_reversal":
            badge_chunks.append("🔥 衰竭反轉")
        elif signal_version == "tier2":
            badge_chunks.append("⚠️ 觀察名單")
    if badge_chunks:
        draw.text((pad_left, top_text_y), " | ".join(badge_chunks)[:88], fill=(255, 220, 120), font=font_label)
        top_text_y += 18

    if macro_badge:
        macro_line = str(macro_badge).replace("🛡️ ", "")
        draw.text((pad_left, top_text_y), f"Macro: {macro_line}"[:96], fill=(130, 220, 255), font=font_label)

    # 右上角風控摘要（快速可讀）
    rr_t1 = _safe_float(tp1_r, None)
    rr_t2 = _safe_float(tp2_r, None)
    atr_f = _safe_float(atr, None)
    summary_parts: List[str] = []
    if rr_t1 is not None and rr_t2 is not None:
        summary_parts.append(f"TP1 {rr_t1:.1f}R / TP2 {rr_t2:.1f}R")
    if atr_f is not None and atr_f > 0:
        summary_parts.append(f"ATR {atr_f:.4f}")
    summary_parts.append("Exit 50/50")
    summary_txt = " | ".join(summary_parts)
    draw.text((width - 380, 4), summary_txt[:52], fill=(220, 235, 255), font=font_label)

    # 主力 2h VWAP（青）；錨定線改在 K 線之後疊加，避免被蠟燭蓋住
    if vwap is not None:
        draw_hline(float(vwap), vwap_col, "VWAP")

    # candles
    for i, k in enumerate(ohlc_use):
        x_center = pad_left + int(i * candle_slot + candle_slot / 2)
        x0 = x_center - candle_w // 2
        x1 = x_center + candle_w // 2
        o = float(k["o"])
        c = float(k["c"])
        h = float(k["h"])
        l = float(k["l"])

        col = up_col if c >= o else down_col
        y_o = y_price(o)
        y_c = y_price(c)
        y_h = y_price(h)
        y_l = y_price(l)

        y_h = max(plot_top_y0, min(plot_top_y1 - 1, y_h))
        y_l = max(plot_top_y0, min(plot_top_y1 - 1, y_l))
        y_o = max(plot_top_y0, min(plot_top_y1 - 1, y_o))
        y_c = max(plot_top_y0, min(plot_top_y1 - 1, y_c))

        # wick
        draw.line([(x_center, y_h), (x_center, y_l)], fill=col, width=2)
        # body
        body_top = min(y_o, y_c)
        body_bot = max(y_o, y_c)
        if body_bot - body_top < 2:
            body_bot = body_top + 2
        draw.rectangle([x0, body_top, x1, body_bot], fill=col)

    # 收盤價走勢線（細線）：蠟燭異常時仍可讀趨勢
    close_line_col = (200, 215, 245)
    _px, _py = None, None
    for i, k in enumerate(ohlc_use):
        try:
            cf = float(k["c"])
        except (TypeError, ValueError, KeyError):
            continue
        x_center = pad_left + int(i * candle_slot + candle_slot / 2)
        y_c = max(plot_top_y0, min(plot_top_y1 - 1, y_price(cf)))
        if _px is not None and _py is not None:
            draw.line([(_px, _py), (x_center, y_c)], fill=close_line_col, width=1)
        _px, _py = x_center, y_c

    # EMA20 曲線（沿著 5m K 線走）
    closes_5m = [float(k["c"]) for k in ohlc_use if k.get("c") is not None]
    if len(closes_5m) >= 2:
        period = 20
        alpha = 2.0 / (period + 1.0)
        ema_vals: List[float] = []
        ema_prev: Optional[float] = None
        for c in closes_5m:
            if ema_prev is None:
                ema_prev = c
            else:
                ema_prev = (c - ema_prev) * alpha + ema_prev
            ema_vals.append(float(ema_prev))

        # 用線條疊在蠟燭圖上
        prev_x = None
        prev_y = None
        for i, ev in enumerate(ema_vals[-60:]):
            x_center = pad_left + int(i * candle_slot + candle_slot / 2)
            y_e = y_price(float(ev))
            y_e = max(plot_top_y0, min(plot_top_y1 - 1, y_e))
            if prev_x is not None and prev_y is not None:
                draw.line([(prev_x, prev_y), (x_center, y_e)], fill=ema20_col, width=2)
            prev_x, prev_y = x_center, y_e

        # 不在右側標籤框顯示 EMA 價位（由電報文字提供）

    # 錨定發動 VWAP（金線，疊在 K 線／EMA 之上）
    _va = _safe_float(vwap_anchor, None)
    if _va is not None and _va > 0:
        draw_hline(float(_va), vwap_anchor_col, "錨VW")

    # OI bars（改用顏色區分漲/跌，較好讀）
    if oi_5m:
        oi_use = oi_5m[-60:]
        oi_vals_use = [_safe_float(b.get("v"), None) for b in oi_use]
    else:
        oi_use = []
        oi_vals_use = []
        for k in ohlc_use:
            o = float(k.get("o") or 0) or 0
            c = float(k.get("c") or 0) or 0
            amp = abs(c - o) / o if o else 0.0
            oi_vals_use.append(max(0.0, amp * 1000.0))

    # 需要篩掉 None，避免 float/scale 崩潰
    oi_vals_use = [v for v in oi_vals_use if v is not None]

    for i, v in enumerate(oi_vals_use[-60:]):
        try:
            v_f = float(v)
        except Exception:
            v_f = 0.0
        x_center = pad_left + int(i * candle_slot + candle_slot / 2)
        bar_w = max(2, int(candle_slot * 0.35))
        x0 = x_center - bar_w // 2
        x1 = x_center + bar_w // 2
        yv = y_oi(v_f)
        yv = max(plot_bot_y0, min(plot_bot_y1 - 1, yv))
        # 與前一根相比：上升/下降上色
        prev_v = float(oi_vals_use[i - 1]) if i - 1 >= 0 else v_f
        # 讓柱狀圖更乾淨：上升藍、下降橘
        bar_col = oi_col if v_f >= prev_v else (255, 150, 90)
        draw.rectangle([x0, yv, x1, plot_bot_y1 - 1], fill=bar_col)

    _stars_i = max(0, min(3, int(anchor_fit_stars or 0)))
    if _va is not None and _va > 0:
        _star_txt = ("⭐" * _stars_i) if _stars_i > 0 else "—"
        _hint_short = (anchor_hint or "")[:40] + ("…" if len(anchor_hint or "") > 40 else "")
        draw.text(
            (pad_left, height - 22),
            f"錨 {_star_txt}  金線=發動VWAP  {_hint_short}",
            fill=vwap_anchor_col,
            font=font_label,
        )

    img.save(out_path)
    return out_path

