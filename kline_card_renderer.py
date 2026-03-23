import os
import time
from typing import Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageDraw, ImageFont


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
    ema20: Optional[float] = None,
    ema20_touch_low: Optional[float] = None,
    ema20_touch_high: Optional[float] = None,
    ema20_4h: Optional[float] = None,
    title_line: str = "",
) -> str:
    """
    用 PIL 畫：上半 K線、下半 OI 柱狀；並疊加水平線（SL/TP1/TP2/進場/VWAP/EMA20）。
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
        # 中文字被截斷會顯示異常；太長就簡單省略
        txt_show = txt if len(txt) <= 18 else (txt[:16] + "..")
        draw.text((x0 + 6, y0 + 2), txt_show, fill=col, font=font_label)

    def draw_label_box(y: int, col: Tuple[int, int, int], label: str, value: float):
        """
        只畫右側標籤框（不畫橫向水平線）。
        用於 EMA20 曲線的最後一點標示，避免你說的「變水平線」問題。
        """
        box_w, box_h = 120, 18
        x0 = width - pad_right - box_w
        x1 = width - pad_right
        y = max(plot_top_y0 + 2, min(plot_top_y1 - 2, y))
        y0 = max(plot_top_y0 + 2, y - box_h // 2)
        y1 = min(plot_top_y1 - 2, y0 + box_h)
        draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0), outline=col, width=2)
        txt = f"{label}:{value:.4f}"
        txt_show = txt if len(txt) <= 18 else (txt[:16] + "..")
        draw.text((x0 + 6, y0 + 2), txt_show, fill=col, font=font_label)

    if title_line:
        draw.text((pad_left, 4), title_line[:80], fill=text_col, font=font_title)

    draw_hline(sl, sl_col, "SL")
    draw_hline(tp1, tp1_col, "TP1")
    draw_hline(tp2, tp2_col, "TP2")
    draw_hline(entry, entry_col, "Entry")
    if vwap is not None:
        draw_hline(float(vwap), vwap_col, "VWAP")

    # EMA20 曲線：依 5m closes 即時計算「真正的 EMA 線」
    # 注意：不要再用 draw_hline 這種水平線，否則會跟你說的不符。

    if direction_is_long:
        if ema20_touch_low is not None and isinstance(ema20_touch_low, (int, float)) and float(ema20_touch_low) > 0:
            draw_hline(float(ema20_touch_low), ema20_touch_col, "EMA_touch_low")
    else:
        if ema20_touch_high is not None and isinstance(ema20_touch_high, (int, float)) and float(ema20_touch_high) > 0:
            draw_hline(float(ema20_touch_high), ema20_touch_col, "EMA_touch_high")

    if ema20_4h is not None and isinstance(ema20_4h, (int, float)) and float(ema20_4h) > 0:
        draw_hline(float(ema20_4h), ema20_4h_col, "4H_EMA20")

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

        # 標籤：只標最後一點（不再畫水平線）
        if ema_vals:
            last_y = y_price(float(ema_vals[-1]))
            last_y = max(plot_top_y0, min(plot_top_y1 - 1, last_y))
            draw_label_box(last_y, ema20_col, "EMA20", float(ema_vals[-1]))

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
        bar_col = oi_col if v_f >= prev_v else (255, 150, 90)
        draw.rectangle([x0, yv, x1, plot_bot_y1 - 1], fill=bar_col)

    img.save(out_path)
    return out_path

