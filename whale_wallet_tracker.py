#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大佬錢包動向追蹤（Hyperliquid + Etherscan）
- 追蹤指定地址的 HL 合約開/平倉
- 追蹤鏈上現貨大額轉帳（> 100k USD）
- 產出 Markdown 訊息，由 jackbot 既有 TG/DC 發送流程送出
"""

from __future__ import annotations

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

    min_usd = _env_float("WHALE_SPOT_MIN_USD", 100000.0)
    lookback_sec = _env_int("WHALE_LOOKBACK_SECONDS", 1800)

    events: List[Dict[str, Any]] = []
    new_hl_state: Dict[str, Dict[str, Dict[str, float]]] = {}

    for addr, profile in WHALE_PROFILES.items():
        now_pos = _fetch_hl_positions(addr)
        prev_pos = old_hl.get(addr, {}) if isinstance(old_hl, dict) else {}
        events.extend(_detect_hl_events(addr, profile, prev_pos, now_pos, sent_event_ids))
        events.extend(_detect_spot_transfers(addr, profile, sent_transfer_ids, min_usd=min_usd, lookback_sec=lookback_sec))
        new_hl_state[addr] = now_pos

    if not events:
        logger.info("[WhaleTracker] 本輪無新事件")
        state["hl_positions"] = new_hl_state
        _save_state(state_path, state)
        return []

    messages = [_build_markdown_message(e) for e in events]
    for e in events:
        if e["type"] in ("hl_open", "hl_close"):
            sent_event_ids.add(e["id"])
        else:
            sent_transfer_ids.add(e["id"])

    state["hl_positions"] = new_hl_state
    state["sent_event_ids"] = list(sent_event_ids)[-3000:]
    state["sent_transfer_ids"] = list(sent_transfer_ids)[-3000:]
    _save_state(state_path, state)
    logger.info("[WhaleTracker] 本輪事件=%s，待發送訊息=%s", len(events), len(messages))
    return messages
