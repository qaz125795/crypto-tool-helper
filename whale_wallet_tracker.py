#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大佬錢包動向追蹤（Hyperliquid + Etherscan）
- 追蹤指定地址的 HL 合約開/平/加減倉（預設門檻偏寬鬆，易先有訊號）
- 追蹤鏈上現貨大額轉帳（預設約 ≥3 萬 USD 等值，見 WHALE_SPOT_MIN_USD）
- 產出 Markdown 訊息，由 jackbot 既有 TG/DC 發送流程送出
"""

from __future__ import annotations

import copy
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests

logger = logging.getLogger(__name__)

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
ETHERSCAN_API_URL = "https://api.etherscan.io/api"

WHALE_PROFILES: Dict[str, Dict[str, str]] = {
    "0x020ca66c30bec2c4fe3861a94e4db4a498a35872": {
        "name": "麻吉大哥 (Machi Big Brother)",
        "confidence": "高可信",
        "intro": "台灣幣圈傳奇，鏈上合約狂人，風格超激進。",
        "pros": "市場情緒指標很強，爆倉後原地重開常是關鍵觀察點。",
        "cons": "常態高槓桿，不愛止損，波動很大不適合無腦跟。",
    },
    "0xe8c19db00287e3536075c60ed78ec809cda52023": {
        "name": "Andrew Kang (Mechanism Capital)",
        "confidence": "高可信（關聯地址）",
        "intro": "頂級機構交易員，趨勢與反轉判斷很強。",
        "pros": "進出場策略性高，方向參考價值高。",
        "cons": "動作很快且可能在對沖，單筆容易誤判全局。",
    },
    "0x534a0076fb7c2b1f83fa21497429ad7ad3bd7587": {
        "name": "Arthur Hayes (BitMEX 創辦人)",
        "confidence": "高可信",
        "intro": "幣圈教父級人物，擅長宏觀敘事與趨勢佈局。",
        "pros": "中長線動作常是行情主題前哨。",
        "cons": "頻率較低，且可能帶節奏，需注意成本與滑點。",
    },
    "0xd8da6bf26964af9d7eed9e03e53415d37aa96045": {
        "name": "Vitalik Buterin (V 神)",
        "confidence": "官方高共識",
        "intro": "以太坊核心人物，平常少做高頻交易，但每次鏈上動作都很有市場影響力。",
        "pros": "大額轉帳常帶動市場情緒，屬於高敏感風向指標。",
        "cons": "未必是交易行為，可能是捐贈或內部調度，容易被過度解讀。",
    },
    "0x3ddfa8ec3052539b6c9549f12cea2c295cff5296": {
        "name": "Justin Sun (孫宇晨)",
        "confidence": "中高可信（公開關聯）",
        "intro": "高頻資金調度代表人物，常見巨額穩定幣與主流幣轉移。",
        "pros": "大額轉入轉出通常領先市場波動，具風險預警價值。",
        "cons": "錢包眾多且策略複雜，單一地址不代表完整持倉意圖。",
    },
}


def _normalize_addr(addr: str) -> str:
    s = str(addr or "").strip().lower()
    if s.startswith("0x") and len(s) == 42:
        return s
    return ""


def _load_runtime_whale_profiles() -> Dict[str, Dict[str, str]]:
    """
    允許透過環境變數擴充追蹤地址：
    - WHALE_EXTRA_WALLETS_JSON: {"0x...":{"name":"...","confidence":"..."}, ...}
    - WHALE_EXTRA_WALLETS: 0xabc...,0xdef...
    """
    profiles: Dict[str, Dict[str, str]] = copy.deepcopy(WHALE_PROFILES)

    raw_json = os.getenv("WHALE_EXTRA_WALLETS_JSON", "").strip()
    if raw_json:
        try:
            obj = json.loads(raw_json)
            if isinstance(obj, dict):
                for k, v in obj.items():
                    addr = _normalize_addr(str(k))
                    if not addr:
                        continue
                    base = {
                        "name": f"自定義巨鯨 {addr[:6]}...{addr[-4:]}",
                        "confidence": "自定義",
                        "intro": "使用者自定義加入的追蹤地址。",
                        "pros": "可快速擴充觀察名單。",
                        "cons": "需自行驗證地址歸屬與交易意圖。",
                    }
                    if isinstance(v, dict):
                        for field in ("name", "confidence", "intro", "pros", "cons"):
                            if str(v.get(field) or "").strip():
                                base[field] = str(v.get(field)).strip()
                    profiles[addr] = base
        except Exception as e:
            logger.warning("[WhaleTracker] WHALE_EXTRA_WALLETS_JSON 解析失敗: %s", e)

    raw_csv = os.getenv("WHALE_EXTRA_WALLETS", "").strip()
    if raw_csv:
        for token in raw_csv.split(","):
            addr = _normalize_addr(token)
            if not addr or addr in profiles:
                continue
            profiles[addr] = {
                "name": f"自定義巨鯨 {addr[:6]}...{addr[-4:]}",
                "confidence": "自定義",
                "intro": "使用者自定義加入的追蹤地址。",
                "pros": "可快速擴充觀察名單。",
                "cons": "需自行驗證地址歸屬與交易意圖。",
            }

    return profiles


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "")
    if raw is None:
        return default
    s = str(raw).strip()
    if not s:
        return default
    try:
        return float(s)
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    if raw is None:
        return default
    s = str(raw).strip()
    if not s:
        return default
    try:
        return int(float(s))
    except Exception:
        return default


def _load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"hl_positions": {}, "sent_event_ids": [], "sent_transfer_ids": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"hl_positions": {}, "sent_event_ids": [], "sent_transfer_ids": []}


def _save_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _fetch_hl_positions(address: str) -> Dict[str, Dict[str, float]]:
    payload = {"type": "clearinghouseState", "user": address}
    try:
        r = requests.post(HYPERLIQUID_INFO_URL, json=payload, timeout=12)
        if r.status_code != 200:
            logger.warning("[WhaleTracker] HL API error %s", r.status_code)
            return {}
        data = r.json() or {}
    except Exception as e:
        logger.warning("[WhaleTracker] HL fetch failed %s: %s", address, e)
        return {}

    out: Dict[str, Dict[str, float]] = {}
    for item in data.get("assetPositions", []) or []:
        pos = item.get("position") or {}
        coin = str(pos.get("coin") or "").upper()
        if not coin:
            continue
        try:
            size = float(pos.get("szi") or 0)
        except Exception:
            size = 0.0
        if abs(size) < 1e-12:
            continue
        try:
            entry = float(pos.get("entryPx") or 0)
        except Exception:
            entry = 0.0
        try:
            lev = float(pos.get("leverage", {}).get("value") or 0)
        except Exception:
            lev = 0.0
        liq_price = None
        for k in ("liquidationPx", "liquidation_price", "liqPx", "liq_price"):
            v = pos.get(k)
            if v is None:
                continue
            try:
                liq_price = float(v)
                break
            except Exception:
                continue
        notional = abs(size) * entry if entry > 0 else 0.0
        out[coin] = {
            "size": size,
            "entry": entry,
            "leverage": lev,
            "notional": notional,
            "liq_price": liq_price,
        }
    return out


def _detect_hl_events(
    address: str,
    profile: Dict[str, str],
    prev_positions: Dict[str, Dict[str, float]],
    now_positions: Dict[str, Dict[str, float]],
    sent_ids: set,
    min_delta_usd: float,
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    now_ts = _iso_now()

    for coin, cur in now_positions.items():
        if coin not in prev_positions:
            side = "多單" if cur["size"] > 0 else "空單"
            evt_id = f"hl_open:{address}:{coin}:{round(cur['size'], 6)}"
            if evt_id in sent_ids:
                continue
            events.append(
                {
                    "id": evt_id,
                    "type": "hl_open",
                    "time": now_ts,
                    "address": address,
                    "profile": profile,
                    "coin": coin,
                    "side": side,
                    "size": cur["size"],
                    "entry": cur["entry"],
                    "leverage": cur["leverage"],
                    "notional": cur["notional"],
                    "liq_price": cur.get("liq_price"),
                }
            )

    for coin, old in prev_positions.items():
        if coin not in now_positions:
            side = "多單" if old["size"] > 0 else "空單"
            evt_id = f"hl_close:{address}:{coin}:{round(old['size'], 6)}"
            if evt_id in sent_ids:
                continue
            events.append(
                {
                    "id": evt_id,
                    "type": "hl_close",
                    "time": now_ts,
                    "address": address,
                    "profile": profile,
                    "coin": coin,
                    "side": side,
                    "size": old["size"],
                    "entry": old["entry"],
                    "leverage": old.get("leverage", 0.0),
                    "notional": old.get("notional", 0.0),
                    "liq_price": old.get("liq_price"),
                }
            )

    # 既有持倉：加倉 / 減倉 / 反手
    for coin, cur in now_positions.items():
        if coin not in prev_positions:
            continue
        old = prev_positions.get(coin) or {}
        try:
            old_size = float(old.get("size") or 0.0)
            cur_size = float(cur.get("size") or 0.0)
            old_notional = float(old.get("notional") or 0.0)
            cur_notional = float(cur.get("notional") or 0.0)
        except Exception:
            continue
        if abs(old_size) < 1e-12 or abs(cur_size) < 1e-12:
            continue

        # 反手（多轉空 / 空轉多）
        if old_size * cur_size < 0:
            evt_id = f"hl_flip:{address}:{coin}:{round(old_size, 6)}:{round(cur_size, 6)}"
            if evt_id not in sent_ids:
                events.append(
                    {
                        "id": evt_id,
                        "type": "hl_flip",
                        "time": now_ts,
                        "address": address,
                        "profile": profile,
                        "coin": coin,
                        "old_side": "多單" if old_size > 0 else "空單",
                        "new_side": "多單" if cur_size > 0 else "空單",
                        "old_size": old_size,
                        "new_size": cur_size,
                        "old_notional": old_notional,
                        "new_notional": cur_notional,
                        "entry": cur.get("entry"),
                        "leverage": cur.get("leverage"),
                        "liq_price": cur.get("liq_price"),
                    }
                )
            continue

        # 同方向變化：加倉 / 減倉
        delta_size = abs(cur_size) - abs(old_size)
        delta_notional = abs(cur_notional - old_notional)
        if delta_notional < max(0.0, min_delta_usd):
            continue
        if abs(delta_size) < 1e-9:
            continue
        evt_type = "hl_add" if delta_size > 0 else "hl_reduce"
        evt_id = f"{evt_type}:{address}:{coin}:{round(old_size, 6)}:{round(cur_size, 6)}"
        if evt_id in sent_ids:
            continue
        events.append(
            {
                "id": evt_id,
                "type": evt_type,
                "time": now_ts,
                "address": address,
                "profile": profile,
                "coin": coin,
                "side": "多單" if cur_size > 0 else "空單",
                "old_size": old_size,
                "new_size": cur_size,
                "delta_size": delta_size,
                "old_notional": old_notional,
                "new_notional": cur_notional,
                "delta_notional": delta_notional,
                "entry": cur.get("entry"),
                "leverage": cur.get("leverage"),
                "liq_price": cur.get("liq_price"),
            }
        )
    return events


def _fetch_eth_price_usd() -> float:
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "ethereum", "vs_currencies": "usd"},
            timeout=10,
        )
        if r.status_code == 200:
            return float((r.json() or {}).get("ethereum", {}).get("usd") or 0)
    except Exception:
        pass
    return 0.0


def _fetch_token_prices_usd(contracts: List[str]) -> Dict[str, float]:
    if not contracts:
        return {}
    uniq = sorted({c.lower() for c in contracts if c})
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/token_price/ethereum",
            params={
                "contract_addresses": ",".join(uniq),
                "vs_currencies": "usd",
            },
            timeout=12,
        )
        if r.status_code != 200:
            return {}
        data = r.json() or {}
        return {k.lower(): float((v or {}).get("usd") or 0) for k, v in data.items()}
    except Exception:
        return {}


def _etherscan_get(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    key = os.getenv("ETHERSCAN_API_KEY", "").strip()
    if not key:
        return []
    q = dict(params)
    q["apikey"] = key
    try:
        r = requests.get(ETHERSCAN_API_URL, params=q, timeout=12)
        if r.status_code != 200:
            return []
        data = r.json() or {}
        if str(data.get("status")) not in ("1", "0"):
            return []
        result = data.get("result") or []
        return result if isinstance(result, list) else []
    except Exception:
        return []


def _detect_spot_transfers(
    address: str,
    profile: Dict[str, str],
    sent_ids: set,
    min_usd: float,
    lookback_sec: int,
) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    now_ts = int(datetime.now(timezone.utc).timestamp())
    lower_addr = address.lower()

    erc20 = _etherscan_get(
        {
            "module": "account",
            "action": "tokentx",
            "address": address,
            "sort": "desc",
            "page": 1,
            "offset": 30,
        }
    )
    contracts = [str(x.get("contractAddress") or "").lower() for x in erc20]
    token_prices = _fetch_token_prices_usd(contracts)

    for tx in erc20:
        try:
            ts = int(tx.get("timeStamp") or 0)
            if ts <= 0 or now_ts - ts > lookback_sec:
                continue
            decimals = int(tx.get("tokenDecimal") or 0)
            raw = float(tx.get("value") or 0)
            amount = raw / (10 ** decimals) if decimals >= 0 else 0.0
            contract = str(tx.get("contractAddress") or "").lower()
            px = token_prices.get(contract, 0.0)
            usd = amount * px
            if usd < min_usd:
                continue
            txid = f"erc20:{tx.get('hash')}:{tx.get('logIndex')}"
            if txid in sent_ids:
                continue
            to_addr = str(tx.get("to") or "").lower()
            direction = "轉出" if str(tx.get("from") or "").lower() == lower_addr else "轉入"
            symbol = str(tx.get("tokenSymbol") or "TOKEN")
            events.append(
                {
                    "id": txid,
                    "type": "spot_transfer",
                    "time": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                    "address": address,
                    "profile": profile,
                    "direction": direction,
                    "symbol": symbol,
                    "amount": amount,
                    "usd": usd,
                    "to": to_addr,
                    "hash": tx.get("hash"),
                }
            )
        except Exception:
            continue

    eth_price = _fetch_eth_price_usd()
    native = _etherscan_get(
        {
            "module": "account",
            "action": "txlist",
            "address": address,
            "sort": "desc",
            "page": 1,
            "offset": 20,
        }
    )
    for tx in native:
        try:
            if str(tx.get("isError") or "0") != "0":
                continue
            ts = int(tx.get("timeStamp") or 0)
            if ts <= 0 or now_ts - ts > lookback_sec:
                continue
            value_eth = float(tx.get("value") or 0) / (10 ** 18)
            usd = value_eth * eth_price
            if usd < min_usd:
                continue
            txid = f"eth:{tx.get('hash')}"
            if txid in sent_ids:
                continue
            to_addr = str(tx.get("to") or "").lower()
            direction = "轉出" if str(tx.get("from") or "").lower() == lower_addr else "轉入"
            events.append(
                {
                    "id": txid,
                    "type": "spot_transfer",
                    "time": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                    "address": address,
                    "profile": profile,
                    "direction": direction,
                    "symbol": "ETH",
                    "amount": value_eth,
                    "usd": usd,
                    "to": to_addr,
                    "hash": tx.get("hash"),
                }
            )
        except Exception:
            continue
    return events


def _fmt_usd(v: float) -> str:
    if v >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v/1_000:.2f}K"
    return f"${v:,.0f}"


def _format_hl_holdings_for_log(positions: Dict[str, Dict[str, float]]) -> str:
    """人類可读的 HL 持倉一行摘要（給 LOG 用）。"""
    if not positions:
        return "（無持倉）"
    parts: List[str] = []
    for coin in sorted(positions.keys()):
        d = positions.get(coin) or {}
        try:
            sz = float(d.get("size") or 0)
        except Exception:
            continue
        if abs(sz) < 1e-12:
            continue
        side = "多" if sz > 0 else "空"
        try:
            n = float(d.get("notional") or 0)
        except Exception:
            n = 0.0
        parts.append(f"{coin}{side}{_fmt_usd(n)}")
    return " | ".join(parts) if parts else "（無持倉）"


def _log_hl_near_miss_threshold(
    name: str,
    addr: str,
    prev_positions: Dict[str, Dict[str, float]],
    now_positions: Dict[str, Dict[str, float]],
    min_delta_usd: float,
) -> None:
    """
    與 _detect_hl_events 邏輯一致：同向加減倉但 delta 名目未達 WHALE_HL_MIN_DELTA_USD 時，印 INFO 說明為何無 hl_evt。
    """
    for coin, cur in now_positions.items():
        if coin not in prev_positions:
            continue
        old = prev_positions.get(coin) or {}
        try:
            old_size = float(old.get("size") or 0.0)
            cur_size = float(cur.get("size") or 0.0)
            old_notional = float(old.get("notional") or 0.0)
            cur_notional = float(cur.get("notional") or 0.0)
        except Exception:
            continue
        if abs(old_size) < 1e-12 or abs(cur_size) < 1e-12:
            continue
        if old_size * cur_size < 0:
            continue
        delta_notional = abs(cur_notional - old_notional)
        delta_size = abs(cur_size) - abs(old_size)
        if abs(delta_size) < 1e-9:
            continue
        if delta_notional >= max(0.0, min_delta_usd):
            continue
        logger.info(
            "[WhaleTracker][未達HL門檻] %s | %s…%s | %s 同向調整 名目變化≈%s < 門檻%s（故不發 hl_add/hl_reduce）",
            name,
            addr[:6],
            addr[-4:],
            coin,
            _fmt_usd(delta_notional),
            _fmt_usd(min_delta_usd),
        )


def _log_event_one_liner(event: Dict[str, Any]) -> str:
    """本輪單一事件一行文字（LOG）。"""
    t = event.get("type") or ""
    p = event.get("profile") or {}
    name = str(p.get("name") or "?")
    if t == "hl_open":
        return f"{name} | 合約開倉 {event.get('coin')} {event.get('side')} 名目~{_fmt_usd(float(event.get('notional') or 0))}"
    if t == "hl_close":
        return f"{name} | 合約平倉 {event.get('coin')} 原{event.get('side')} 名目~{_fmt_usd(float(event.get('notional') or 0))}"
    if t == "hl_add":
        return (
            f"{name} | 加倉 {event.get('coin')} Δ名目~{_fmt_usd(float(event.get('delta_notional') or 0))}"
        )
    if t == "hl_reduce":
        return (
            f"{name} | 減倉 {event.get('coin')} Δ名目~{_fmt_usd(float(event.get('delta_notional') or 0))}"
        )
    if t == "hl_flip":
        return f"{name} | 反手 {event.get('coin')} {event.get('old_side')}→{event.get('new_side')}"
    if t == "spot_transfer":
        return (
            f"{name} | 現貨{event.get('direction')} {event.get('symbol')} "
            f"~{_fmt_usd(float(event.get('usd') or 0))}"
        )
    return f"{name} | type={t}"


def _build_markdown_message(event: Dict[str, Any]) -> str:
    p = event["profile"]
    title = "🐋 *鏈上巨鯨動向*"
    if event["type"] == "hl_open":
        action_line = f"🚀 合約開倉：`{event['coin']}` {event['side']}"
        detail_line = (
            f"名目價值：{_fmt_usd(float(event.get('notional') or 0))} | "
            f"槓桿：{float(event.get('leverage') or 0):.1f}x | "
            f"均價：{float(event.get('entry') or 0):,.4f}"
        )
    elif event["type"] == "hl_close":
        action_line = f"🧹 合約平倉：`{event['coin']}` 原 {event['side']}"
        detail_line = (
            f"原名目價值：{_fmt_usd(float(event.get('notional') or 0))} | "
            f"參考均價：{float(event.get('entry') or 0):,.4f}"
        )
    elif event["type"] == "hl_add":
        action_line = f"➕ 合約加倉：`{event['coin']}` {event['side']}"
        detail_line = (
            f"增量：{_fmt_usd(float(event.get('delta_notional') or 0))} | "
            f"倉位：{_fmt_usd(float(event.get('old_notional') or 0))} → {_fmt_usd(float(event.get('new_notional') or 0))}"
        )
    elif event["type"] == "hl_reduce":
        action_line = f"➖ 合約減倉：`{event['coin']}` {event['side']}"
        detail_line = (
            f"減少：{_fmt_usd(float(event.get('delta_notional') or 0))} | "
            f"倉位：{_fmt_usd(float(event.get('old_notional') or 0))} → {_fmt_usd(float(event.get('new_notional') or 0))}"
        )
    elif event["type"] == "hl_flip":
        action_line = f"🔄 合約反手：`{event['coin']}` {event.get('old_side')} → {event.get('new_side')}"
        detail_line = (
            f"名目：{_fmt_usd(float(event.get('old_notional') or 0))} → {_fmt_usd(float(event.get('new_notional') or 0))} | "
            f"均價：{float(event.get('entry') or 0):,.4f}"
        )
    else:
        action_line = f"💸 大額現貨{event['direction']}：`{event['symbol']}`"
        detail_line = (
            f"數量：{float(event.get('amount') or 0):,.4f} | "
            f"估值：{_fmt_usd(float(event.get('usd') or 0))}"
        )

    lines = [
        title,
        f"👤 *{p['name']}*",
        f"🔎 地址可信度：*{p.get('confidence', '待確認')}*",
        f"🧾 介紹：{p['intro']}",
        f"✅ 優點：{p['pros']}",
        f"⚠️ 缺點：{p['cons']}",
        "",
        action_line,
        detail_line,
        f"地址：`{event['address'][:6]}...{event['address'][-4:]}`",
        f"🔗 倉位追蹤：https://hyperbot.network/trader/{event['address'].lower()}",
    ]
    liq = event.get("liq_price")
    try:
        liq_val = float(liq) if liq is not None and str(liq).strip() != "" else None
    except Exception:
        liq_val = None
    if liq_val is not None and liq_val > 0:
        lines.append(f"💥 暴倉價：`{liq_val:,.4f}`")
    if event.get("hash"):
        lines.append(f"Tx: https://etherscan.io/tx/{event['hash']}")
    lines.append("")
    lines.append("⚠️ 僅供風險觀察，非投資建議")
    return "\n".join(lines)


def run_whale_wallet_tracker_once(data_dir: Path) -> List[str]:
    """
    回傳本輪可推播的 Markdown 訊息列表。
    """
    state_path = data_dir / "whale_tracker_state.json"
    state = _load_state(state_path)
    sent_event_ids = set(state.get("sent_event_ids") or [])
    sent_transfer_ids = set(state.get("sent_transfer_ids") or [])
    old_hl = state.get("hl_positions") or {}

    # 預設偏寬鬆（先求「看得到訊號」）；要減少雜訊可用環境變數拉高門檻
    min_usd = _env_float("WHALE_SPOT_MIN_USD", 30000.0)
    lookback_sec = _env_int("WHALE_LOOKBACK_SECONDS", 7200)
    min_hl_delta_usd = _env_float("WHALE_HL_MIN_DELTA_USD", 12000.0)
    profiles = _load_runtime_whale_profiles()

    es_key_ok = bool(os.getenv("ETHERSCAN_API_KEY", "").strip())
    logger.info(
        "[WhaleTracker] 推播門檻: HL加減倉名目變化≥%s | 現貨單筆≥%s 且回溯≤%ss | Etherscan=%s",
        _fmt_usd(min_hl_delta_usd),
        _fmt_usd(min_usd),
        lookback_sec,
        "已設定" if es_key_ok else "未設定（spot 永遠 0）",
    )
    if not es_key_ok:
        logger.warning(
            "[WhaleTracker] 未設定 ETHERSCAN_API_KEY：無法掃以太坊鏈上 ERC20/ETH 大額轉帳，"
            "所有錢包 spot_evt 會是 0（非 bug）。"
        )

    events: List[Dict[str, Any]] = []
    new_hl_state: Dict[str, Dict[str, Dict[str, float]]] = {}
    summary_rows: List[str] = []

    for addr, profile in profiles.items():
        name = str(profile.get("name") or "N/A")
        now_pos = _fetch_hl_positions(addr)
        prev_pos = old_hl.get(addr, {}) if isinstance(old_hl, dict) else {}
        if not now_pos and addr:
            logger.info(
                "[WhaleTracker][主力快照] %s | %s…%s | HL API 回傳無持倉（或未取得資料）",
                name,
                addr[:6],
                addr[-4:],
            )
        else:
            logger.info(
                "[WhaleTracker][主力快照] %s | %s…%s | %s",
                name,
                addr[:6],
                addr[-4:],
                _format_hl_holdings_for_log(now_pos),
            )

        hl_events = _detect_hl_events(addr, profile, prev_pos, now_pos, sent_event_ids, min_delta_usd=min_hl_delta_usd)
        _log_hl_near_miss_threshold(name, addr, prev_pos, now_pos, min_hl_delta_usd)
        spot_events = _detect_spot_transfers(addr, profile, sent_transfer_ids, min_usd=min_usd, lookback_sec=lookback_sec)
        events.extend(hl_events)
        events.extend(spot_events)
        summary_rows.append(
            f"{profile.get('name','N/A')}: prev={len(prev_pos)} now={len(now_pos)} hl_evt={len(hl_events)} spot_evt={len(spot_events)}"
        )
        new_hl_state[addr] = now_pos

    for row in summary_rows:
        logger.info("[WhaleTracker][摘要] %s", row)

    if not events:
        logger.info(
            "[WhaleTracker] 本輪無新事件（常見原因：與上次快照相比無開平／反手；"
            "同向加減倉但名目變化未達門檻；或現貨無符合金額+時間窗／未設 Etherscan）"
        )
        state["hl_positions"] = new_hl_state
        _save_state(state_path, state)
        return []

    for e in events:
        logger.info("[WhaleTracker][推播事件] %s", _log_event_one_liner(e))

    messages = [_build_markdown_message(e) for e in events]
    for e in events:
        if e["type"] in ("hl_open", "hl_close", "hl_add", "hl_reduce", "hl_flip"):
            sent_event_ids.add(e["id"])
        else:
            sent_transfer_ids.add(e["id"])

    state["hl_positions"] = new_hl_state
    state["sent_event_ids"] = list(sent_event_ids)[-3000:]
    state["sent_transfer_ids"] = list(sent_transfer_ids)[-3000:]
    _save_state(state_path, state)
    logger.info("[WhaleTracker] 本輪事件=%s，待發送訊息=%s", len(events), len(messages))
    return messages
