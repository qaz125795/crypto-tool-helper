import os
import time
from typing import List, Dict, Optional, Tuple

import requests
from PIL import Image, ImageDraw, ImageFont


def _safe_float(x, default=None):
    try:
        if x is None:
            return default
        v = float(x)
        return v
    except Exception:
        return default


def fetch_binance_ohlc_5m(symbol_base: str, limit: int = 60) -> Optional[List[Dict]]:
    """
    回傳: [{"t": unix_sec, "o": float, "h": float, "l": float, "c": float}, ...]
    """
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
            if not isinstance(raw, list) or len(raw) < 10:
                continue
            out = []
            for row in raw:
                # [ts, open, high, low, close, volume, close_ts, ...]
                t_ms = row[0]
                o = row[1]
                h = row[2]
                l = row[3]
                c = row[4]
                out.append(
                    {
                        "t": int(t_ms / 1000) if t_ms and t_ms > 1e12 else int(t_ms),
                        "o": float(o),
                        "h": float(h),
                        "l": float(l),
                        "c": float(c),
                    }
                )
            return out[-limit:]
        except Exception:
            continue
    return None


def fetch_coinglass_oi_5m(symbol_base: str, limit: int = 60) -> Optional[List[Dict]]:
    """
    回傳: [{"t": unix_sec, "v": float}, ...]
    """
    cg_api_key = os.getenv("CG_API_KEY")
    cg_api_base = "https://open-api-v4.coinglass.com"
    if not cg_api_key:
        return None

    base_symbol = symbol_base.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
    url = f"{cg_api_base}/api/futures/open-interest/aggregated-history"
    headers = {"CG-API-KEY": cg_api_key, "accept": "application/json"}
    try:
        r = requests.get(
            url,
            params={"symbol": base_symbol, "interval": "5m", "limit": limit},
            headers=headers,
            timeout=10,
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
        for row in raw:
            if not isinstance(row, dict):
                continue
            t = row.get("t") or row.get("time") or row.get("timestamp") or 0
            if t and t > 1e12:
                t = int(t / 1000)
            oi_val = (
                row.get("c")
                or row.get("close")
                or row.get("openInterest")
                or row.get("oi")
                or row.get("value")
            )
            oi_val_f = _safe_float(oi_val)
            if t and oi_val_f is not None:
                out.append({"t": int(t), "v": oi_val_f})
        if len(out) < 10:
            return None
        return out[-limit:]
    except Exception:
        return None


def render_kline_oi_card(
    symbol_base: str,
    ohlc_5m: List[Dict],
    oi_5m: Optional[List[Dict]],
    sl: float,
    tp1: float,
    tp2: float,
    entry: float,
    vwap: float,
    title_line: str = "",
    out_path: str = "kline_card_preview.png",
):
    width, height = 980, 520
    pad_left, pad_right = 70, 20
    pad_top, pad_bottom = 20, 30

    # 分區：上半 K線，下半 OI 柱狀
    top_h = 330
    bottom_h = height - pad_top - pad_bottom - top_h
    if bottom_h < 80:
        bottom_h = 110
        top_h = height - pad_top - pad_bottom - bottom_h

    bg = (14, 18, 33)
    grid = (44, 50, 80)
    up_col = (0, 214, 124)
    down_col = (255, 75, 87)
    line_sl = (255, 70, 70)
    line_tp1 = (0, 210, 110)
    line_tp2 = (0, 160, 90)
    line_entry = (255, 200, 0)
    line_vwap = (90, 200, 255)
    text_col = (220, 230, 255)

    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)

    plot_w = width - pad_left - pad_right
    plot_top_y0 = pad_top
    plot_top_y1 = pad_top + top_h
    plot_bot_y0 = plot_top_y1
    plot_bot_y1 = height - pad_bottom

    # 背景網格
    for i in range(6):
        y = plot_top_y0 + int(top_h * i / 5)
        draw.line([(pad_left, y), (width - pad_right, y)], fill=grid, width=1)
    for i in range(4):
        y = plot_bot_y0 + int(bottom_h * i / 3)
        draw.line([(pad_left, y), (width - pad_right, y)], fill=grid, width=1)

    # 價格縮放（包含 SL/TP）
    prices = []
    for k in ohlc_5m:
        prices.extend([k["h"], k["l"]])
    prices.extend([sl, tp1, tp2, entry, vwap])
    p_min = min(prices)
    p_max = max(prices)
    if p_max == p_min:
        p_max += 1
    margin = (p_max - p_min) * 0.05
    p_min -= margin
    p_max += margin

    def y_price(p: float) -> int:
        return plot_top_y1 - int((p - p_min) / (p_max - p_min) * top_h)

    # OI 縮放
    oi_vals = [x["v"] for x in oi_5m] if oi_5m else []
    if oi_vals:
        o_min = min(oi_vals)
        o_max = max(oi_vals)
        if o_max == o_min:
            o_max += 1
    else:
        o_min, o_max = 0.0, 1.0

    def y_oi(v: float) -> int:
        return plot_bot_y1 - int((v - o_min) / (o_max - o_min) * bottom_h)

    # 水平線 + 標籤
    def draw_hline(price: float, col: Tuple[int, int, int], label: str):
        y = y_price(price)
        draw.line([(pad_left, y), (width - pad_right, y)], fill=col, width=2)
        # 右側標籤框
        box_w = 110
        box_h = 18
        x0 = width - pad_right - box_w
        x1 = width - pad_right
        y0 = max(plot_top_y0 + 2, y - box_h // 2)
        y1 = y0 + box_h
        draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0), outline=col, width=2)
        txt = f"{label}:{price:.4f}"
        draw.text((x0 + 6, y0 + 2), txt[:18], fill=col)

    draw_hline(sl, line_sl, "止損")
    draw_hline(tp1, line_tp1, "TP1")
    draw_hline(tp2, line_tp2, "TP2")
    draw_hline(entry, line_entry, "進場")
    draw_hline(vwap, line_vwap, "均價")

    # title
    if title_line:
        draw.text((pad_left, 5), title_line[:60], fill=text_col)

    # 繪製燭台
    n = len(ohlc_5m)
    if n <= 1:
        n = 60
    candle_slot = plot_w / 60.0
    candle_w = max(2, int(candle_slot * 0.55))
    for i in range(min(60, len(ohlc_5m))):
        k = ohlc_5m[-60 + i] if len(ohlc_5m) > 60 else ohlc_5m[i]
        x_center = pad_left + int(i * candle_slot + candle_slot / 2)
        x0 = x_center - candle_w // 2
        x1 = x_center + candle_w // 2
        o = k["o"]
        c = k["c"]
        h = k["h"]
        l = k["l"]

        col = up_col if c >= o else down_col
        y_o = y_price(o)
        y_c = y_price(c)
        y_h = y_price(h)
        y_l = y_price(l)

        # wick
        draw.line([(x_center, y_h), (x_center, y_l)], fill=col, width=2)
        # body
        body_top = min(y_o, y_c)
        body_bot = max(y_o, y_c)
        if body_bot - body_top < 2:
            body_bot = body_top + 2
        draw.rectangle([x0, body_top, x1, body_bot], fill=col)

    # 繪製 OI 柱狀
    if oi_5m:
        oi_use = oi_5m[-60:] if len(oi_5m) >= 60 else oi_5m
        m = len(oi_use)
        for i, b in enumerate(oi_use):
            v = b["v"]
            x_center = pad_left + int(i * candle_slot + candle_slot / 2)
            bar_w = max(2, int(candle_slot * 0.35))
            x0 = x_center - bar_w // 2
            x1 = x_center + bar_w // 2
            yv = y_oi(v)
            # clamp，避免 yv > plot_bot_y1-1 導致 PIL error
            if yv < plot_bot_y0:
                yv = plot_bot_y0
            if yv > plot_bot_y1 - 1:
                yv = plot_bot_y1 - 1
            draw.rectangle([x0, yv, x1, plot_bot_y1 - 1], fill=(110, 190, 255))

    # y-axis labels（價格）
    # 固定三個刻度
    for frac, label in [(0, p_max), (0.5, (p_max + p_min) / 2), (1, p_min)]:
        y = y_price(label)
        txt = f"{label:.4f}"
        draw.text((5, y - 8), txt, fill=text_col)

    img.save(out_path)
    return out_path


def main():
    # 你截圖那個例子：SHORT HUMA（數字僅用於視覺預覽）
    symbol_base = "HUMAUSDT"
    out_path = os.path.join(os.path.dirname(__file__), "assets", "kline_card_preview.png")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    ohlc = fetch_binance_ohlc_5m(symbol_base, limit=60)
    if not ohlc:
        raise RuntimeError("OHLC 5m 取得失敗，請確認該幣在 Binance futures 是否存在。")

    # OI 需要 CG_API_KEY；沒有就先用空的 OI（只看圖形）
    oi = fetch_coinglass_oi_5m(symbol_base, limit=60)
    # 若目前環境沒有 CG_API_KEY，無法抓到 OI：用視覺替代 OI（依每棒波幅生成）
    if not oi:
        oi = []
        for k in ohlc:
            # 以波幅做個 proxy，避免 OI 區塊完全空白
            amp = abs(k["c"] - k["o"]) / k["o"] if k.get("o") else 0.0
            oi.append({"t": k["t"], "v": max(0.0, amp * 1000.0)})

    # 預覽用：用「實際抓到的OHLC」自動生成 SL/TP/進場/VWAP（避免因數字落在不同價格尺度看不到燭台）
    last_close = ohlc[-1]["c"]
    last20 = [k["c"] for k in ohlc[-20:]]
    entry = last_close
    vwap = sum(last20) / len(last20)
    # 假設 short：止損在上方、TP 在下方（僅為視覺預覽）
    sl = entry * 1.03
    tp1 = entry * 0.985
    tp2 = entry * 0.97

    title = "HUMAUSDT | 5m K 線 + OI"
    render_kline_oi_card(
        symbol_base=symbol_base,
        ohlc_5m=ohlc,
        oi_5m=oi,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        entry=entry,
        vwap=vwap,
        title_line=title,
        out_path=out_path,
    )
    print("OK ->", out_path)


if __name__ == "__main__":
    main()

