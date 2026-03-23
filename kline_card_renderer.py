import os
import time
from typing import Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageDraw


def _safe_float(x, default=None):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _fetch_binance_ohlc_5m(symbol_base: str, limit: int = 60) -> Optional[List[Dict]]:
    clean = symbol_base.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
    candidates = [f"{clean}USDT", f"1000{clean}USDT"]
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
            if not isinstance(raw, list) or len(raw) < 5:
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
            if len(out) >= 5:
                return out
        except Exception:
            continue
    return None


def _fetch_bybit_ohlc_5m(symbol_base: str, limit: int = 60) -> Optional[List[Dict]]:
    clean = symbol_base.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
    interval_map = {"5m": "5"}
    bybit_interval = interval_map.get("5m", "5")
    for sym_pair in [f"{clean}USDT", f"1000{clean}USDT"]:
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
            if not isinstance(raw, list) or len(raw) < 5:
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
            if len(out) >= 5:
                return out
        except Exception:
            continue
    return None


def _fetch_bingx_spot_ohlc_5m(symbol_base: str, limit: int = 60) -> Optional[List[Dict]]:
    clean = symbol_base.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
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
        if not isinstance(raw, list) or len(raw) < 5:
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
        if len(out) >= 5:
            return out
    except Exception:
        return None
    return None


def fetch_ohlc_5m(symbol_base: str, limit: int = 60) -> Optional[List[Dict]]:
    # 優先 Binance（含 volume），其次 Bybit，最後 BingX
    out = _fetch_binance_ohlc_5m(symbol_base, limit=limit)
    if out:
        return out
    out = _fetch_bybit_ohlc_5m(symbol_base, limit=limit)
    if out:
        return out
    out = _fetch_bingx_spot_ohlc_5m(symbol_base, limit=limit)
    if out:
        return out
    return None


def fetch_coinglass_oi_5m(symbol_base: str, limit: int = 60) -> Optional[List[Dict]]:
    """
    回傳: [{"t": unix_sec, "v": float}, ...]
    """
    cg_api_key = os.getenv("CG_API_KEY")
    if not cg_api_key:
        return None
    cg_api_base = "https://open-api-v4.coinglass.com"
    base_symbol = symbol_base.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
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
    sl: float,
    tp1: float,
    tp2: float,
    entry: float,
    vwap: Optional[float],
    out_path: str,
    title_line: str = "",
) -> str:
    """
    用 PIL 畫：上半 K線、下半 OI 柱狀；並疊加水平線（SL/TP1/TP2/進場/VWAP）。
    """
    width, height = 980, 520
    pad_left, pad_right = 70, 20
    pad_top, pad_bottom = 18, 28
    top_h = 320
    bot_h = height - pad_top - pad_bottom - top_h
    if bot_h < 110:
        bot_h = 120
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
    oi_col = (110, 190, 255)
    text_col = (220, 230, 255)

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

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

    # scale
    prices = []
    for k in ohlc_5m:
        prices.extend([k["h"], k["l"]])
    prices.extend([sl, tp1, tp2, entry])
    if vwap is not None:
        prices.append(float(vwap))
    p_min, p_max = min(prices), max(prices)
    if p_max == p_min:
        p_max += 1e-9
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

    def draw_hline(price: float, col: Tuple[int, int, int], label: str):
        y = y_price(price)
        y = max(plot_top_y0, min(plot_top_y1 - 1, y))
        draw.line([(pad_left, y), (width - pad_right, y)], fill=col, width=2)

        box_w, box_h = 120, 18
        x0 = width - pad_right - box_w
        x1 = width - pad_right
        y0 = max(plot_top_y0 + 2, y - box_h // 2)
        y1 = min(plot_top_y1 - 2, y0 + box_h)
        draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0), outline=col, width=2)
        txt = f"{label}:{price:.4f}"
        draw.text((x0 + 6, y0 + 2), txt[:20], fill=col)

    if title_line:
        draw.text((pad_left, 4), title_line[:80], fill=text_col)

    draw_hline(sl, sl_col, "止損")
    draw_hline(tp1, tp1_col, "TP1")
    draw_hline(tp2, tp2_col, "TP2")
    draw_hline(entry, entry_col, "進場")
    if vwap is not None:
        draw_hline(float(vwap), vwap_col, "均價")

    # candles
    # 60 根：只取最後 60
    ohlc_use = ohlc_5m[-60:]
    candle_slot = plot_w / 60.0
    candle_w = max(2, int(candle_slot * 0.55))

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

    # OI bars
    if oi_5m:
        oi_use = oi_5m[-60:]
        oi_vals_use = [b.get("v") for b in oi_use]
    else:
        oi_use = []
        oi_vals_use = []
        for k in ohlc_use:
            o = float(k.get("o") or 0) or 0
            c = float(k.get("c") or 0) or 0
            amp = abs(c - o) / o if o else 0.0
            oi_vals_use.append(max(0.0, amp * 1000.0))

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
        draw.rectangle([x0, yv, x1, plot_bot_y1 - 1], fill=oi_col)

    img.save(out_path)
    return out_path

