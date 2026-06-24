"""
signal-tracker - 訊號追蹤器服務

功能：
1. 接收新訊號（POST /signals）
2. 自動判斷：新訊號 / 重複訊號 / 反向訊號
3. 5 秒輪詢 Gate.io 價格對齊 TP/SL
4. 觸發 TP/SL 時推播帶訊號編號 (#142) 到原頻道
5. 提供查詢 API（GET /signals, /signals/{id}）
"""
from __future__ import annotations
import asyncio
import json
import logging
import tardis_filter
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional

import asyncpg
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from market_state import (
    market_state_loop,
    get_state as get_market_state,
    calc_symbol_rs,
    evaluate_signal,
    format_market_info,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
logger = logging.getLogger("signal-tracker")

# ── 設定 ────────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://platform:platform_pass@postgres:5432/platform"
)
GATE_QUANT_URL = os.environ.get("GATE_QUANT_URL", "http://gate-quant:8001")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "@Jacky87084")
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_NOTIFY_TOKEN = os.environ.get("TG_NOTIFY_TOKEN", "")
TG_NOTIFY_CHAT_ID = os.environ.get("TG_NOTIFY_CHAT_ID", "").strip()

# [v30] source → TG thread_id / DC channel_id 對應，讓追蹤訊號跟原訊號同話題
try:
    _TG_THREAD_MAP = json.loads(os.environ.get("TG_THREAD_IDS", "{}"))
except Exception:
    _TG_THREAD_MAP = {}
try:
    _DC_THREAD_MAP = json.loads(os.environ.get("DC_THREAD_IDS", "{}"))
except Exception:
    _DC_THREAD_MAP = {}
# _v30_extra_patches_applied
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")
POLL_INTERVAL = int(os.environ.get("TRACKER_POLL_INTERVAL", "5"))

# 自動下單到 Gate 量化（False 則只追蹤，不下單）
AUTO_TRADE = os.environ.get("TRACKER_AUTO_TRADE", "false").lower() == "true"

# 手動跟單用戶版面：預設不推「點位微調」「風控出場」（僅 DB + 量化同步）
PUSH_LEVEL_UPDATES = os.environ.get("TRACKER_PUSH_LEVEL_UPDATES", "false").lower() == "true"
PUSH_RISK_EXIT = os.environ.get("TRACKER_PUSH_RISK_EXIT", "false").lower() == "true"
# 跨 Bot reply 在 TG 會錯亂，預設關閉（訊息自帶 #編號即可對帳）
USE_REPLY_THREAD = os.environ.get("TRACKER_USE_REPLY", "false").lower() == "true"

# [v2 出場優化 06-08] 級聯早砍 + 僵屍單 + TP1 後移動停損（實盤）
LIVE_EXIT_ENABLED = os.environ.get("LIVE_EXIT_ENABLED", "true").lower() == "true"
EARLY_CUT_RULES = [(4, 1.5), (2, 2.5)]  # (持倉小時, 虧損R門檻)
ZOMBIE_HOURS = float(os.environ.get("EXIT_ZOMBIE_HOURS", "24"))
ZOMBIE_MIN_R = float(os.environ.get("EXIT_ZOMBIE_MIN_R", "0.3"))

# ── 全域物件 ────────────────────────────────────────────────────────────────
db_pool: Optional[asyncpg.Pool] = None
http: Optional[httpx.AsyncClient] = None


# ── 資料模型 ────────────────────────────────────────────────────────────────
class IncomingSignal(BaseModel):
    """從 JackBot signal_bridge 進來的訊號"""
    source: str = Field(..., description="crit_radar / position_change / gold_signal")
    symbol: str
    side: str  # long / short
    entry_price: float
    sl_price: Optional[float] = None
    tp1_price: Optional[float] = None
    tp2_price: Optional[float] = None
    tp3_price: Optional[float] = None
    tp4_price: Optional[float] = None
    leverage: int = 10
    tg_message_id: Optional[int] = None
    tg_chat_id: Optional[int] = None
    payload: Optional[dict] = None


class SignalResponse(BaseModel):
    id: int
    signal_uuid: str
    action: str  # new / updated / reversed
    note: str
    pnl_pct: Optional[float] = None


# ── 啟動/關閉 ────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool, http
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    http = httpx.AsyncClient(timeout=15.0)
    logger.info("Signal Tracker started, AUTO_TRADE=%s, POLL=%ss", AUTO_TRADE, POLL_INTERVAL)

    # 啟動價格追蹤循環 + 市場狀態循環
    tracker_task = asyncio.create_task(price_tracker_loop())
    market_task = asyncio.create_task(market_state_loop(http, interval=60))

    yield

    tracker_task.cancel()
    market_task.cancel()
    try:
        await asyncio.gather(tracker_task, market_task, return_exceptions=True)
    except asyncio.CancelledError:
        pass
    await http.aclose()
    await db_pool.close()


app = FastAPI(title="Signal Tracker", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


# ── 健康檢查 ────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "signal-tracker", "auto_trade": AUTO_TRADE}


# ── 市場狀態端點 ────────────────────────────────────────────────────────────
@app.get("/market-state")
async def market_state_endpoint():
    return get_market_state()


# ── 接收訊號 ────────────────────────────────────────────────────────────────
@app.post("/signals", response_model=SignalResponse)
async def receive_signal(sig: IncomingSignal):
    """接收新訊號，判斷類型並處理"""
    logger.info(
        "Received signal: %s %s %s @%s",
        sig.source, sig.symbol, sig.side, sig.entry_price
    )

    # ── 訊號過濾（RS + 市場模式）──────────────────────────────────────────
    rs = await calc_symbol_rs(sig.symbol, http)
    eval_result = evaluate_signal(sig.symbol, sig.side, sig.source, rs)

    # 在 payload 中記錄市場狀態（即使被過濾也存）
    if not sig.payload:
        sig.payload = {}
    # [OOS] 保留來源(tracker_hook)算好的信心分級，不被本地 evaluate 覆蓋（單一事實來源）
    _incoming_me = sig.payload.get("market_eval") or {}
    for _k in ("oos_tier", "oos_conf", "oos_mult", "oos_pass", "oos_detail"):
        if _k in _incoming_me:
            eval_result[_k] = _incoming_me[_k]
    sig.payload["market_eval"] = eval_result

    # 所有訊號都追蹤（不再拒絕），逆勢的標記 quality='counter_trend'
    if not eval_result["pass"]:
        logger.info(
            "Signal COUNTER-TREND: %s %s %s - %s",
            sig.source, sig.symbol, sig.side, eval_result["reason"]
        )
        # 在 notes 標記為逆勢
        if not sig.payload:
            sig.payload = {}
        sig.payload["quality"] = "counter_trend"
        sig.payload["counter_trend_reason"] = eval_result["reason"]

    async with db_pool.acquire() as conn:
        # 1. 查找最近的同 symbol 活躍訊號
        existing = await conn.fetchrow(
            """
            SELECT * FROM signal_tracker
            WHERE symbol = $1 AND status IN ('pending', 'active', 'tp1', 'tp2', 'tp3')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            sig.symbol,
        )

        if not existing:
            # 全新訊號
            result = await create_new_signal(conn, sig)
            return SignalResponse(**result)

        if existing["side"] == sig.side:
            # 同向重複 → 更新點位
            result = await update_existing_signal(conn, existing, sig)
            return SignalResponse(**result)
        else:
            # 反向訊號 → 平倉並開新單
            result = await reverse_signal(conn, existing, sig)
            return SignalResponse(**result)


async def create_new_signal(conn, sig: IncomingSignal) -> dict:
    """建立新訊號"""
    signal_uuid = str(uuid.uuid4())
    row = await conn.fetchrow(
        """
        INSERT INTO signal_tracker (
            signal_uuid, source, symbol, side, entry_price,
            sl_price, tp1_price, tp2_price, tp3_price, tp4_price,
            leverage, status, tg_message_id, tg_chat_id, payload
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, 'active', $12, $13, $14)
        RETURNING id, signal_uuid
        """,
        signal_uuid, sig.source, sig.symbol, sig.side, sig.entry_price,
        sig.sl_price, sig.tp1_price, sig.tp2_price, sig.tp3_price, sig.tp4_price,
        sig.leverage, sig.tg_message_id, sig.tg_chat_id,
        json.dumps(sig.payload or {}),
    )
    signal_id = row["id"]

    if sig.tg_message_id:
        try:
            await conn.execute(
                """
                UPDATE signal_tracker
                SET payload = COALESCE(payload, '{}'::jsonb)
                    || jsonb_build_object('tg_root_message_id', $1::text)
                WHERE id = $2
                """,
                str(sig.tg_message_id), signal_id,
            )
        except Exception as _e_root:
            logger.warning("[root-msg] #%s err: %s", signal_id, _e_root)

    await conn.execute(
        """
        INSERT INTO signal_updates (signal_id, event_type, new_value, price_at_event, note)
        VALUES ($1, 'create', $2, $3, $4)
        """,
        signal_id,
        json.dumps({
            "side": sig.side,
            "entry": sig.entry_price,
            "sl": sig.sl_price,
            "tp1": sig.tp1_price,
        }),
        sig.entry_price,
        f"新訊號建立：{sig.source}",
    )

    market_eval = (sig.payload or {}).get("market_eval", {})
    _pl_pre = sig.payload or {}
    if sig.source == "gold_signal" and _pl_pre.get("push_text"):
        msg = str(_pl_pre["push_text"])
        if f"#{signal_id}" not in msg:
            msg += f"\n\n<code>#{signal_id}</code>"
    else:
        msg = _build_v2_push_message(sig, signal_id, market_eval)

    # Tardis / 大盤：只寫 DB；推播僅異常時一行警告
    tardis_eval = None
    try:
        tardis_eval = await tardis_filter.evaluate_signal(sig.symbol, sig.side)
        if tardis_eval.get("tardis_available"):
            sig.payload = sig.payload or {}
            sig.payload["_tardis_eval"] = tardis_eval
            logger.info("[tardis-tag] #%s sym=%s state=%s",
                        signal_id, sig.symbol, tardis_eval.get("tardis_state"))
    except Exception as _e_t:
        logger.warning("[tardis-tag] receive_signal err: %s", _e_t)

    try:
        from datetime import datetime, timezone
        _now = datetime.now(timezone.utc)
        _btc_env = await tardis_filter.fetch_oi_fr_window("BTCUSDT", _now, minutes=60)
        _eth_env = await tardis_filter.fetch_oi_fr_window("ETHUSDT", _now, minutes=60)
        if _btc_env or _eth_env:
            sig.payload = sig.payload or {}
            sig.payload["market_env_snapshot"] = {
                "btc_oi_1h_pct":  _btc_env["oi_change_pct"] if _btc_env else None,
                "btc_fr_avg":     _btc_env["fr_avg"] if _btc_env else None,
                "eth_oi_1h_pct":  _eth_env["oi_change_pct"] if _eth_env else None,
                "eth_fr_avg":     _eth_env["fr_avg"] if _eth_env else None,
                "captured_at":    _now.isoformat(),
            }
    except Exception as _e_env:
        logger.warning("[market-env] err: %s", _e_env)

    _warn = _compact_push_warn(market_eval, tardis_eval)
    if _warn:
        msg += "\n" + _warn

    # [v29 fix] 寫回 Tardis + market_env 到 DB payload（確保「每筆訂單記錄可循」）
    try:
        await conn.execute(
            "UPDATE signal_tracker SET payload = $1::jsonb WHERE id = $2",
            json.dumps(sig.payload or {}), signal_id
        )
        logger.info("[payload-writeback] #%s payload updated（tardis+envsnap）", signal_id)
    except Exception as _e_pw:
        logger.warning("[payload-writeback] err #%s: %s", signal_id, _e_pw)

    # [過濾一致性] OOS 輕倉(負期望)訊號不推播給用戶(與下單過濾一致)；DB 已記錄供追蹤統計
    # fail-open：無 oos_pass 欄位 → 照推
    # jackbot 已推播 → 僅補缺的那一邊（避免 TG 失敗+DC 成功時 TG 永遠缺訊）
    _pl = sig.payload or {}
    _need_tg = not bool(sig.tg_message_id)
    _need_dc = not bool(_pl.get("dc_message_id"))
    if _pl.get("jackbot_pushed"):
        _need_tg = _need_dc = False
    if market_eval.get("oos_pass") is False:
        logger.info("[alert-filter] #%s %s %s OOS輕倉(負期望)→不推播(僅DB追蹤)",
                    signal_id, sig.symbol, sig.side)
    elif not _need_tg and not _need_dc:
        logger.info("[alert-skip] #%s %s jackbot雙通道已推播→僅DB追蹤 tg_msg=%s dc_msg=%s",
                    signal_id, sig.symbol, sig.tg_message_id,
                    _pl.get("dc_message_id"))
    else:
        if _need_tg:
            await send_tg_message(
                sig.tg_chat_id, msg, signal_id=signal_id, source=sig.source,
                write_back_id=True,
            )
        if _need_dc:
            await send_dc_message(msg, source=sig.source, signal_id=signal_id)
        if _need_tg or _need_dc:
            logger.info("[alert-backfill] #%s %s 補推播 tg=%s dc=%s",
                        signal_id, sig.symbol, _need_tg, _need_dc)

    # 自動跟單與推播分離：分析師／jackbot 已推播時仍須下單
    if AUTO_TRADE and sig.source != "gold_signal":
        _me_oos = (sig.payload or {}).get("market_eval") or {}
        _is_quant_src = sig.source in ("crit_radar", "position_change")
        if _is_quant_src and _me_oos.get("oos_pass") is False:
            logger.info(
                "[auto-trade] SKIP #%s %s %s — OOS 輕倉(負期望)過濾，不下單 detail=%s",
                signal_id, sig.symbol, sig.side, _me_oos.get("oos_detail"),
            )
        else:
            try:
                _qt_resp = await forward_to_gate_quant(sig, signal_id)
                _gate_id = None
                for _r in (_qt_resp.get("results") or []):
                    _raw = _r.get("raw") or {}
                    _eid = (_raw.get("calc") or {}).get("entry_order_id")
                    if _eid:
                        _gate_id = str(_eid)
                        break
                    _eid2 = _raw.get("entry_order_id") or _raw.get("entry_resp", {}).get("id")
                    if _eid2:
                        _gate_id = str(_eid2)
                        break
                if _gate_id:
                    await conn.execute(
                        "UPDATE signal_tracker SET gate_order_id=$1 WHERE id=$2",
                        _gate_id, signal_id,
                    )
                    logger.info("[gate-writeback] #%s gate_order_id=%s", signal_id, _gate_id)
            except Exception as _e_qt:
                logger.warning("[auto-trade] forward #%s fail: %s", signal_id, _e_qt)

    return {
        "id": signal_id, "signal_uuid": signal_uuid,
        "action": "new", "note": f"新訊號 #{signal_id} 建立",
        "pnl_pct": None,
    }


async def update_existing_signal(conn, existing, sig: IncomingSignal) -> dict:
    """更新現有訊號點位"""
    signal_id = existing["id"]
    old_vals = {
        "entry": float(existing["entry_price"]),
        "sl": float(existing["sl_price"]) if existing["sl_price"] else None,
        "tp1": float(existing["tp1_price"]) if existing["tp1_price"] else None,
    }
    new_vals = {
        "entry": sig.entry_price,
        "sl": sig.sl_price,
        "tp1": sig.tp1_price,
    }

    await conn.execute(
        """
        UPDATE signal_tracker
        SET entry_price = $1, sl_price = $2,
            tp1_price = $3, tp2_price = $4, tp3_price = $5, tp4_price = $6,
            updated_at = NOW()
        WHERE id = $7
        """,
        sig.entry_price, sig.sl_price,
        sig.tp1_price, sig.tp2_price, sig.tp3_price, sig.tp4_price,
        signal_id,
    )

    await conn.execute(
        """
        INSERT INTO signal_updates (signal_id, event_type, old_value, new_value, price_at_event, note)
        VALUES ($1, 'update_levels', $2, $3, $4, $5)
        """,
        signal_id, json.dumps(old_vals), json.dumps(new_vals),
        sig.entry_price, "同向訊號更新點位",
    )

    # 點位微調：預設不推播（手動跟單看不懂 A→B；量化仍同步）
    if PUSH_LEVEL_UPDATES:
        msg = (
            f"📌 #{signal_id} <b>{sig.symbol}</b> 策略調整點位\n"
            f"進場 <code>{_fmt_px_short(sig.entry_price)}</code>｜"
            f"止損 <code>{_fmt_px_short(sig.sl_price)}</code>｜"
            f"TP1 <code>{_fmt_px_short(sig.tp1_price)}</code>\n"
            f"<i>系統依最新 K 線微調，手動跟單可維持原進場或依新價微調</i>"
        )
        await send_tg_message(
            sig.tg_chat_id, msg, signal_id=signal_id,
            reply_to=_root_tg_reply_id(existing), source=sig.source,
        )
        await send_dc_message(
            msg, source=sig.source, signal_id=signal_id,
            reply_to=_root_dc_reply_id(existing),
        )
    else:
        logger.info(
            "[level-update] #%s %s silent push (entry %.4g→%.4g)",
            signal_id, sig.symbol, old_vals["entry"], sig.entry_price,
        )

    if AUTO_TRADE and sig.source != "gold_signal":
        await update_gate_tpsl(existing, sig)

    return {
        "id": signal_id, "signal_uuid": existing["signal_uuid"],
        "action": "updated", "note": f"訊號 #{signal_id} 點位已更新",
        "pnl_pct": None,
    }


async def reverse_signal(conn, existing, sig: IncomingSignal) -> dict:
    """反向訊號：平倉舊單，開新反向單"""
    old_id = existing["id"]

    # 標記舊訊號為反向結束
    await conn.execute(
        """
        UPDATE signal_tracker
        SET status = 'reversed', closed_at = NOW(), updated_at = NOW()
        WHERE id = $1
        """,
        old_id,
    )

    await conn.execute(
        """
        INSERT INTO signal_updates (signal_id, event_type, price_at_event, note)
        VALUES ($1, 'reverse', $2, $3)
        """,
        old_id, sig.entry_price, f"反向訊號觸發，平倉並轉向 {sig.side}",
    )

    # 平倉 Gate 持倉（黃金只追蹤不下單）
    if AUTO_TRADE and sig.source != "gold_signal" and existing.get("gate_order_id"):
        await close_gate_position(existing["symbol"])

    # 建立新訊號
    new_result = await create_new_signal(conn, sig)
    new_id = new_result["id"]

    new_side = "做多 🟢" if sig.side == "long" else "做空 🔴"
    msg = (
        f"🔁 <b>{sig.symbol}</b> 方向翻轉\n"
        f"#{old_id} {existing['side']} 已平倉 → 新單 #{new_id} {new_side}\n"
        f"進場 <code>{_fmt_px_short(sig.entry_price)}</code>  "
        f"止損 <code>{_fmt_px_short(sig.sl_price)}</code>"
    )
    await send_tg_message(
        sig.tg_chat_id, msg, signal_id=new_id,
        reply_to=_root_tg_reply_id(existing), source=sig.source,
        write_back_id=True,
    )
    await send_dc_message(
        msg, source=sig.source, signal_id=new_id,
        reply_to=_root_dc_reply_id(existing),
    )

    return {
        "id": new_id, "signal_uuid": new_result["signal_uuid"],
        "action": "reversed", "note": f"#{old_id} 反向 → #{new_id}",
        "pnl_pct": None,
    }


# ── 5秒價格輪詢（核心追蹤迴圈）─────────────────────────────────────────────
async def price_tracker_loop():
    logger.info("Price tracker loop started, interval=%ss", POLL_INTERVAL)
    while True:
        try:
            await asyncio.sleep(POLL_INTERVAL)
            await check_active_signals()
        except asyncio.CancelledError:
            logger.info("Price tracker loop cancelled")
            break
        except Exception as exc:
            logger.error("Tracker loop error: %s", exc)


async def check_active_signals():
    """檢查所有活躍訊號，比對價格觸發 TP/SL"""
    async with db_pool.acquire() as conn:
        active = await conn.fetch(
            """
            SELECT * FROM signal_tracker
            WHERE status IN ('active', 'tp1', 'tp2', 'tp3')
            ORDER BY created_at ASC
            """
        )

        if not active:
            return

        # 取得所有 symbol 的目前價格
        symbols = list(set(row["symbol"] for row in active))
        prices = await fetch_gate_prices(symbols)

        for sig in active:
            cur_price = prices.get(sig["symbol"])
            if not cur_price:
                continue

            # 更新 last_price + pnl
            pnl_pct = calculate_pnl_pct(
                float(sig["entry_price"]),
                cur_price,
                sig["side"],
                int(sig["leverage"]),
            )
            await conn.execute(
                "UPDATE signal_tracker SET last_price=$1, pnl_pct=$2, updated_at=NOW() WHERE id=$3",
                cur_price, pnl_pct, sig["id"],
            )

            # [v2 實盤風控] 級聯早砍 / 僵屍單 / TP1+移動停損
            if await _live_exit_check(conn, sig, cur_price, pnl_pct):
                continue

            # 檢查 SL
            if sig["sl_price"]:
                sl = float(sig["sl_price"])
                if (sig["side"] == "long" and cur_price <= sl) or \
                   (sig["side"] == "short" and cur_price >= sl):
                    await trigger_sl(conn, sig, cur_price, pnl_pct)
                    continue

            # 檢查 TP1-4
            cur_status = sig["status"]
            tp_levels = [
                (1, sig["tp1_price"], "active"),
                (2, sig["tp2_price"], "tp1"),
                (3, sig["tp3_price"], "tp2"),
                (4, sig["tp4_price"], "tp3"),
            ]

            for tp_num, tp_price, required_status in tp_levels:
                if not tp_price or cur_status != required_status:
                    continue
                tp = float(tp_price)
                hit = (
                    (sig["side"] == "long" and cur_price >= tp) or
                    (sig["side"] == "short" and cur_price <= tp)
                )
                if hit:
                    await trigger_tp(conn, sig, tp_num, cur_price, pnl_pct)
                    break


async def trigger_tp(conn, sig, tp_num: int, price: float, pnl_pct: float):
    """觸發 TP"""
    new_status = f"tp{tp_num}"
    # 最後一個 TP 直接結束
    is_final = tp_num == 4 or (
        tp_num == 3 and not sig["tp4_price"]
    ) or (
        tp_num == 2 and not sig["tp3_price"]
    ) or (
        tp_num == 1 and not sig["tp2_price"]
    )

    if is_final:
        new_status = "tp_final"
        closed_at = "NOW()"
        await conn.execute(
            f"UPDATE signal_tracker SET status='{new_status}', closed_at=NOW(), updated_at=NOW() WHERE id=$1",
            sig["id"],
        )
    else:
        await conn.execute(
            f"UPDATE signal_tracker SET status='{new_status}', updated_at=NOW() WHERE id=$1",
            sig["id"],
        )

    await conn.execute(
        """
        INSERT INTO signal_updates (signal_id, event_type, price_at_event, note)
        VALUES ($1, $2, $3, $4)
        """,
        sig["id"], f"tp{tp_num}_hit", price,
        f"觸及 TP{tp_num}，PnL {pnl_pct:.2f}%",
    )

    if is_final:
        title = "🏆 全平獲利"
        foot = "本單結案，可忽略後續同幣訊號直至下一則進場"
    elif tp_num == 1:
        title = "✅ 停利1 達成（已平65%，餘倉保本）"
        foot = "餘倉止損已移至進場價附近，可繼續持有等停利2"
    else:
        title = f"✅ 停利{tp_num} 達成"
        foot = ""
    msg = _format_followup_msg(sig, title, pnl_pct, footnote=foot)
    await send_tg_message(
        sig["tg_chat_id"], msg, signal_id=sig["id"],
        reply_to=_root_tg_reply_id(sig), source=sig.get("source"),
    )
    await send_dc_message(
        msg, source=sig.get("source"), signal_id=sig["id"],
        reply_to=_root_dc_reply_id(sig), mention_everyone=is_final,
    )


async def trigger_sl(conn, sig, price: float, pnl_pct: float):
    """觸發 SL"""
    await conn.execute(
        "UPDATE signal_tracker SET status='sl', closed_at=NOW(), updated_at=NOW() WHERE id=$1",
        sig["id"],
    )
    await conn.execute(
        """
        INSERT INTO signal_updates (signal_id, event_type, price_at_event, note)
        VALUES ($1, 'sl_hit', $2, $3)
        """,
        sig["id"], price, f"觸及 SL，PnL {pnl_pct:.2f}%",
    )

    msg = _format_followup_msg(
        sig, "🛑 止損出場", pnl_pct,
        footnote="觸及止損，本單結案",
    )
    await send_tg_message(
        sig["tg_chat_id"], msg, signal_id=sig["id"],
        reply_to=_root_tg_reply_id(sig), source=sig.get("source"),
    )
    await send_dc_message(
        msg, source=sig.get("source"), signal_id=sig["id"],
        reply_to=_root_dc_reply_id(sig),
    )


# TP1 後移動停損參數（達 1.0R 武裝，峰值回撤 1.0R 出場）
SHADOW_TRAIL_R = float(os.environ.get("LIVE_TRAIL_R", "1.0"))
SHADOW_ARM_R = float(os.environ.get("LIVE_ARM_R", "1.0"))


# ── 工具函式 ────────────────────────────────────────────────────────────────
async def fetch_gate_prices(symbols: list) -> dict:
    """從 Gate.io 取得多個 symbol 的目前價格"""
    prices = {}
    for symbol in symbols:
        try:
            # Gate USDT 永續：BTCUSDT → BTC_USDT
            contract = _normalize_symbol(symbol)
            r = await http.get(
                f"https://fx-api.gateio.ws/api/v4/futures/usdt/tickers",
                params={"contract": contract},
                timeout=8.0,
            )
            data = r.json()
            if isinstance(data, list) and data:
                prices[symbol] = float(data[0].get("last", 0))
        except Exception as exc:
            logger.warning("Fetch price %s failed: %s", symbol, exc)
    return prices


def _normalize_symbol(symbol: str) -> str:
    """BTCUSDT → BTC_USDT"""
    s = symbol.upper().replace("/", "").replace("-", "").replace(" ", "")
    if s.endswith("USDT") and "_" not in s:
        return s[:-4] + "_USDT"
    return s


def calculate_pnl_pct(entry: float, current: float, side: str, leverage: int) -> float:
    """計算含槓桿 PnL%"""
    if entry <= 0:
        return 0.0
    if side == "long":
        raw = (current - entry) / entry * 100
    else:
        raw = (entry - current) / entry * 100
    return round(raw * leverage, 4)


def _source_name(source: str) -> str:
    names = {
        "crit_radar": "⚡ 爆擊雷達",
        "position_change": "🎯 持倉狙擊",
        "gold_signal": "🥇 黃金獵手",
    }
    return names.get(source, source)


def _payload_dict(row) -> dict:
    pl = row.get("payload") if isinstance(row, dict) else getattr(row, "payload", None)
    if isinstance(pl, str):
        try:
            pl = json.loads(pl)
        except Exception:
            pl = {}
    return pl if isinstance(pl, dict) else {}


def _root_tg_reply_id(row) -> Optional[int]:
    """僅在 TRACKER_USE_REPLY=true 時使用；預設不用 reply（跨 Bot 會錯亂）。"""
    if not USE_REPLY_THREAD:
        return None
    pl = _payload_dict(row)
    for key in ("tg_root_message_id",):
        try:
            v = pl.get(key) or (row.get("tg_message_id") if isinstance(row, dict) else None)
            if v:
                return int(v)
        except (TypeError, ValueError):
            pass
    return None


def _root_dc_reply_id(row) -> Optional[str]:
    if not USE_REPLY_THREAD:
        return None
    return _extract_dc_message_id(row)


def _fmt_px_short(v) -> str:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return "—"
    if x >= 100:
        return f"{x:.1f}".rstrip("0").rstrip(".")
    if x >= 1:
        return f"{x:.4f}".rstrip("0").rstrip(".")
    return f"{x:.6f}".rstrip("0").rstrip(".")


def _format_followup_msg(sig, title: str, pnl_pct: float, *, footnote: str = "") -> str:
    """TP/SL/結案：自包含格式，手動跟單一眼看懂，不依賴 reply 引用。"""
    sym = str(sig.get("symbol") or "").replace("USDT", "")
    side = "做多 🟢" if sig.get("side") == "long" else "做空 🔴"
    src = _source_name(sig.get("source"))
    entry = _fmt_px_short(sig.get("entry_price"))
    lines = [
        title,
        f"#{sig['id']} <b>{sym}</b> {side}｜{src}",
        f"進場 <code>{entry}</code>｜含槓桿損益 <b>{pnl_pct:+.1f}%</b>",
    ]
    if footnote:
        lines.append(footnote)
    return "\n".join(lines)


def _extract_dc_message_id(sig) -> Optional[str]:
    """從 signal_tracker 列或 payload 取出 Discord 原訊訊息 ID（供 reply 用）。"""
    if not sig:
        return None
    pl = sig.get("payload") if isinstance(sig, dict) else getattr(sig, "payload", None)
    if isinstance(pl, str):
        try:
            pl = json.loads(pl)
        except Exception:
            pl = {}
    if isinstance(pl, dict):
        dmid = pl.get("dc_message_id")
        if dmid:
            return str(dmid)
    return None


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


def _compact_push_warn(market_eval: dict | None, tardis_eval: dict | None = None) -> str:
    """僅在異常時加一行，平常不洗版。"""
    lines: list[str] = []
    if tardis_eval:
        st = tardis_eval.get("tardis_state") or ""
        if st == "LONG_CROWDED":
            lines.append("⚠️ 多頭擁擠，倉位建議再減半")
        elif st == "SHORT_CROWDED":
            lines.append("⚠️ 空頭擁擠，倉位建議再減半")
        elif st == "OI_SURGE":
            lines.append("⚠️ OI 暴增，方向未明，縮倉觀望")
    me = market_eval or {}
    if me.get("market_mode") == "defensive":
        lines.append("⚠️ 大盤防守模式，做多謹慎")
    for w in (me.get("warnings") or [])[:1]:
        lines.append(f"⚠️ {w}")
    return "\n".join(lines[:2])


def _risk_reward(entry, sl, tp, is_long) -> Optional[float]:
    """風險報酬比 R:R（低勝率系統的核心賣點）。"""
    try:
        e, s, t = float(entry), float(sl), float(tp)
        risk = (e - s) if is_long else (s - e)
        reward = (t - e) if is_long else (e - t)
        if risk <= 0 or reward <= 0:
            return None
        return round(reward / risk, 1)
    except (TypeError, ValueError):
        return None


def _build_v2_push_message(sig: IncomingSignal, signal_id: int, market_eval: dict) -> str:
    """精簡推播：清晰排版 + 風險報酬比（可推廣賣點）。"""
    tier = (market_eval or {}).get("oos_tier") or ""
    if "重倉" in tier:
        stars, pos = "★★★", "2~3%"
    elif "輕倉" in tier:
        stars, pos = "★", "≤1%"
    else:
        stars, pos = "★★", "1~2%"
    sym = str(sig.symbol or "").replace("USDT", "").replace("_", "")
    is_long = sig.side == "long"
    act_txt = "做多" if is_long else "做空"
    act_emo = "🟢" if is_long else "🔴"
    src_label = {
        "crit_radar": "⚡ 爆擊雷達",
        "position_change": "🎯 持倉狙擊",
        "gold_signal": "🥇 黃金獵手",
    }.get(sig.source, f"📢 {_source_name(sig.source)}")
    disp_sym = "XAUUSDT" if sig.source == "gold_signal" else sym

    entry = _fmt_px(sig.entry_price)
    sl = _fmt_px(sig.sl_price)
    tp1 = _fmt_px(sig.tp1_price)
    tp2 = _fmt_px(sig.tp2_price) if sig.tp2_price else ""
    rr = _risk_reward(sig.entry_price, sig.sl_price, sig.tp1_price, is_long)

    lines = [
        f"{src_label}  ·  {stars}",
        "",
        f"{act_emo} <b>{disp_sym}</b> {act_txt}",
        "",
        f"進場　<code>{entry}</code>",
        f"止損　<code>{sl}</code>",
    ]
    if tp1:
        tp_line = f"停利　<code>{tp1}</code>"
        if tp2:
            tp_line += f" → <code>{tp2}</code>"
        lines.append(tp_line)
    lines.append("")
    if rr:
        lines.append(f"⚖️ 風險報酬 <b>1 : {rr}</b>")
    lines += [
        f"💰 建議倉位 {pos}（觸損即出）",
        "🛡 系統自動追蹤停利停損",
        "",
        f"<code>#{signal_id}</code>",
    ]
    return "\n".join(lines)


def _hold_hours(sig) -> float:
    created = sig.get("created_at")
    if not created:
        return 0.0
    if getattr(created, "tzinfo", None) is None:
        created = created.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - created).total_seconds() / 3600.0


def _current_r(sig, cur_price: float) -> float | None:
    try:
        entry = float(sig["entry_price"] or 0)
        sl = float(sig["sl_price"] or 0)
    except (TypeError, ValueError):
        return None
    if not entry or not sl:
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    if sig["side"] == "long":
        return (cur_price - entry) / risk
    return (entry - cur_price) / risk


_RISK_EXIT_LABELS = {
    "early_cut_4h": "持倉逾 4 小時且虧損擴大，系統風控提前止損",
    "early_cut_2h": "持倉逾 2 小時且深度虧損，系統風控提前止損",
    "zombie_timeout": "持倉超過 24 小時無明顯進展，系統釋放資金",
    "trail_exit": "觸及移動停損，鎖定獲利",
}


async def trigger_risk_exit(conn, sig, price: float, pnl_pct: float, reason: str, cur_r: float):
    """智能風控出場：更新 DB + 推播 + 量化平倉。"""
    await conn.execute(
        "UPDATE signal_tracker SET status='risk_exit', closed_at=NOW(), updated_at=NOW() WHERE id=$1",
        sig["id"],
    )
    note = f"風控出場({reason}) R={cur_r:+.2f} PnL {pnl_pct:.2f}%"
    await conn.execute(
        """
        INSERT INTO signal_updates (signal_id, event_type, price_at_event, note)
        VALUES ($1, 'risk_exit', $2, $3)
        """,
        sig["id"], price, note,
    )
    label = _RISK_EXIT_LABELS.get(reason, "系統風控出場")
    if PUSH_RISK_EXIT:
        msg = _format_followup_msg(
            sig,
            f"🛡️ 系統風控平倉",
            pnl_pct,
            footnote=f"{label}（量化專用，手動跟單可忽略）",
        )
        await send_tg_message(
            sig["tg_chat_id"], msg, signal_id=sig["id"],
            reply_to=_root_tg_reply_id(sig), source=sig.get("source"),
        )
        await send_dc_message(
            msg, source=sig.get("source"), signal_id=sig["id"],
            reply_to=_root_dc_reply_id(sig),
        )
    else:
        logger.info(
            "[risk-exit] #%s %s silent push reason=%s pnl=%.1f%%",
            sig["id"], sig["symbol"], reason, pnl_pct,
        )
    if AUTO_TRADE and sig.get("source") != "gold_signal":
        await close_gate_position(sig["symbol"], sig["side"])
    logger.info("[risk-exit] #%s %s %s reason=%s cur_r=%.2f", sig["id"], sig["symbol"], sig["side"], reason, cur_r)


async def _live_exit_check(conn, sig, cur_price: float, pnl_pct: float) -> bool:
    """級聯早砍 / 僵屍單 / TP1+移動停損。True=已出場。"""
    if not LIVE_EXIT_ENABLED:
        return False
    status = sig.get("status")
    if status not in ("active", "tp1", "tp2", "tp3"):
        return False
    cur_r = _current_r(sig, cur_price)
    if cur_r is None:
        return False
    hours = _hold_hours(sig)

    # TP1 後：移動停損（峰值回撤 SHADOW_TRAIL_R）
    if status in ("tp1", "tp2", "tp3"):
        entry = float(sig["entry_price"] or 0)
        sl = float(sig["sl_price"] or 0)
        risk = abs(entry - sl)
        if risk <= 0:
            return False
        long = sig["side"] == "long"
        raw = sig["payload"]
        payload = (json.loads(raw) if isinstance(raw, str) else raw) or {}
        st = payload.get("live_trail") or {}
        peak = max(st.get("peak_r", 0.0), cur_r)
        armed = st.get("armed") or peak >= SHADOW_ARM_R
        trail_sl_r = max(0.0, peak - SHADOW_TRAIL_R) if armed else -1.0
        new_st = {"peak_r": round(peak, 3), "armed": armed, "trail_sl_r": round(trail_sl_r, 3)}
        if new_st != st:
            payload["live_trail"] = new_st
            await conn.execute(
                "UPDATE signal_tracker SET payload=$1::jsonb WHERE id=$2",
                json.dumps(payload), sig["id"],
            )
        if armed and cur_r <= trail_sl_r:
            await trigger_risk_exit(conn, sig, cur_price, pnl_pct, "trail_exit", cur_r)
            return True
        return False

    # active：級聯早砍
    for h_th, loss_r in EARLY_CUT_RULES:
        if hours >= h_th and cur_r <= -loss_r:
            await trigger_risk_exit(conn, sig, cur_price, pnl_pct, f"early_cut_{int(h_th)}h", cur_r)
            return True
    if hours >= ZOMBIE_HOURS and cur_r < ZOMBIE_MIN_R:
        await trigger_risk_exit(conn, sig, cur_price, pnl_pct, "zombie_timeout", cur_r)
        return True
    return False


async def send_tg_message(
    chat_id: Optional[int],
    text: str,
    signal_id: int = None,
    reply_to: int = None,
    source: Optional[str] = None,
    *,
    write_back_id: bool = False,
):
    """推播 TG 訊息（v28: dual-token routing + write_back message_id）
    
    - 群組 chat (cid<0): 優先 TG_TOKEN (jack_signal_center_bot)
    - 個人 chat (cid>0): 優先 TG_NOTIFY_TOKEN (autotraderrrrr_Bot)
    - 失敗自動 fallback 另一個 token
    - 成功時把 message_id 寫回 signal_tracker.tg_message_id
    """
    if not TG_TOKEN and not TG_NOTIFY_TOKEN:
        logger.info("[TG SKIP] no tokens at all, signal=#%s", signal_id)
        return

    targets: list[int] = []
    if chat_id:
        try:
            targets.append(int(chat_id))
        except (TypeError, ValueError):
            pass
    if TG_NOTIFY_CHAT_ID:
        try:
            admin_id = int(TG_NOTIFY_CHAT_ID)
            if admin_id not in targets:
                targets.append(admin_id)
        except ValueError:
            pass
    if not targets:
        logger.info("[TG SKIP] no chat_id, signal=#%s", signal_id)
        return

    params_base = {
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        # [2026-06-24] 降噪：只有新單根訊號(write_back_id=True)會響鈴；
        # 追蹤更新(TP/SL命中、點位更新)靜音推播，訊息照進頻道但手機不震
        "disable_notification": (not write_back_id),
    }
    # [v30] 群組訊息：依 source 對應到 thread_id（用戶要求：追蹤器跟原訊號同話題）
    _thread_id_main = None
    if source and source in _TG_THREAD_MAP:
        try:
            _thread_id_main = int(_TG_THREAD_MAP[source])
        except Exception:
            _thread_id_main = None
    if reply_to and chat_id:
        params_base["reply_to_message_id"] = reply_to

    primary_msg_id = None  # 對應 chat_id（主訊號頻道）的 message_id，寫回 DB

    for cid in targets:
        is_group = cid < 0
        primary_token = TG_TOKEN if is_group else (TG_NOTIFY_TOKEN or TG_TOKEN)
        fallback_token = TG_NOTIFY_TOKEN if is_group else TG_TOKEN

        params = {**params_base, "chat_id": cid}
        # [v30] 只對群組 chat 套 thread_id（個人 chat 不能帶 message_thread_id）
        if is_group and _thread_id_main:
            params["message_thread_id"] = _thread_id_main
        if cid != chat_id:
            params.pop("reply_to_message_id", None)

        success_msg_id = None
        attempts = [("primary", primary_token), ("fallback", fallback_token)]
        for tag, tok in attempts:
            if not tok or tok == "":
                continue
            try:
                r = await http.post(
                    f"https://api.telegram.org/bot{tok}/sendMessage",
                    json=params, timeout=10.0,
                )
                if r.status_code == 200:
                    try:
                        success_msg_id = r.json().get("result", {}).get("message_id")
                    except Exception:
                        success_msg_id = None
                    if tag == "fallback":
                        logger.info("[TG %s ok] chat=%s signal=#%s", tag, cid, signal_id)
                    break
                else:
                    logger.warning("[TG %s fail] chat=%s status=%s body=%s",
                                   tag, cid, r.status_code, r.text[:200])
            except Exception as exc:
                logger.error("[TG %s err] chat=%s: %s", tag, cid, exc)

        if success_msg_id is None:
            logger.error("[TG TOTAL FAIL] chat=%s signal=#%s — both tokens failed", cid, signal_id)
        elif cid == chat_id and primary_msg_id is None:
            primary_msg_id = success_msg_id

    # 僅新單根訊息寫回 ID；TP/SL/更新不覆蓋（避免 reply 錯亂）
    if primary_msg_id and signal_id and write_back_id:
        try:
            async with db_pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE signal_tracker
                    SET tg_message_id=$1,
                        payload = COALESCE(payload, '{}'::jsonb)
                            || jsonb_build_object('tg_root_message_id', $1::text)
                    WHERE id=$2
                    """,
                    primary_msg_id, signal_id,
                )
        except Exception as exc:
            logger.warning("[TG write_back] err: %s", exc)


async def send_dc_message(
    text: str,
    source: Optional[str] = None,
    signal_id: int = None,
    reply_to: Optional[str] = None,
    mention_everyone: bool = False,
) -> None:
    """[v30] 同步推到 Discord 對應頻道；支援 reply 原訊 + 停利 @everyone。"""
    if not DISCORD_BOT_TOKEN or not source:
        return
    channel_id = _DC_THREAD_MAP.get(source)
    if not channel_id:
        return
    try:
        # Discord 4000 字限制，超過截斷
        text_dc = text[:1900] + ("..." if len(text) > 1900 else "")
        # HTML 轉成 Discord 支援的 Markdown
        import re as _re
        text_dc = _re.sub(r"<b>(.*?)</b>", r"**\1**", text_dc)
        text_dc = _re.sub(r"<i>(.*?)</i>", r"*\1*", text_dc)
        text_dc = _re.sub(r"<code>(.*?)</code>", r"`\1`", text_dc)
        text_dc = _re.sub(r"<[^>]+>", "", text_dc)
        if mention_everyone:
            text_dc = "@everyone\n" + text_dc
        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}", "Content-Type": "application/json"}
        body: dict = {
            "content": text_dc,
            "allowed_mentions": {"parse": ["everyone"]},
        }
        if reply_to:
            try:
                body["message_reference"] = {
                    "message_id": int(reply_to),
                    "fail_if_not_exists": False,
                }
            except (TypeError, ValueError):
                pass
        r = await http.post(url, json=body, headers=headers, timeout=10.0)
        if r.status_code in (200, 201):
            logger.info(
                "[DC ok] source=%s channel=%s signal=#%s reply=%s everyone=%s",
                source, channel_id, signal_id, reply_to, mention_everyone,
            )
            # 僅「根訊息」寫回 dc_message_id（追蹤更新 reply 時不覆蓋）
            if signal_id and not reply_to:
                try:
                    dc_mid = r.json().get("id")
                    if dc_mid:
                        async with db_pool.acquire() as conn:
                            row = await conn.fetchrow(
                                "SELECT payload FROM signal_tracker WHERE id=$1", signal_id,
                            )
                            pl = row["payload"] if row else {}
                            if isinstance(pl, str):
                                pl = json.loads(pl)
                            if not isinstance(pl, dict):
                                pl = {}
                            if not pl.get("dc_message_id"):
                                pl["dc_message_id"] = str(dc_mid)
                                await conn.execute(
                                    "UPDATE signal_tracker SET payload=$1::jsonb WHERE id=$2",
                                    json.dumps(pl), signal_id,
                                )
                except Exception as wb_exc:
                    logger.warning("[DC write_back] err signal=#%s: %s", signal_id, wb_exc)
        else:
            logger.warning("[DC fail] source=%s channel=%s status=%s body=%s",
                           source, channel_id, r.status_code, r.text[:200])
    except Exception as exc:
        logger.error("[DC err] source=%s signal=#%s: %s", source, signal_id, exc)


def _to_gate_symbol(symbol: str) -> str:
    """BTCUSDT → BTC_USDT（Gate 格式）"""
    symbol = symbol.upper().strip()
    if "_" in symbol:
        return symbol
    for suffix in ("USDT", "USDC", "BTC", "ETH", "BUSD"):
        if symbol.endswith(suffix) and len(symbol) > len(suffix):
            return f"{symbol[:-len(suffix)]}_{suffix}"
    return symbol


def _conf_to_score(me: dict):
    """[方向二] OOS 信心 → 連續 signal_score（55-95，對應 gate-quant 0.5x-1.5x）。
    有 oos_conf 用連續映射；否則 fallback 舊 3 段（重倉90/正常70）。bounded 防爆倉。"""
    conf = me.get("oos_conf")
    if conf is not None:
        try:
            return int(max(55, min(95, round(50 + float(conf) * 50))))
        except (TypeError, ValueError):
            pass
    tier = me.get("oos_tier") or ""
    if "重倉" in tier:
        return 90
    if "正常" in tier:
        return 70
    return None


async def forward_to_gate_quant(sig: IncomingSignal, signal_id: int) -> dict:
    """轉發訊號到 Gate Quant 下單（TGSignalPayload 格式）。回傳 webhook 回應供上層寫 gate_order_id。"""
    try:
        # [v27/v28] 從 payload 取 quality metadata + Tardis 結果
        sp = sig.payload or {}
        me = sp.get("market_eval") or {}
        tardis_eval = sp.get("_tardis_eval") or await tardis_filter.evaluate_signal(
            sig.symbol, sig.side
        )
        _src_map = {
            "crit_radar":      "爆擊訊號",
            "position_change": "持倉狙擊訊號",
        }
        _side_map = {"long": "做多", "short": "做空"}

        payload = {
            "exchange":    "gate",
            "symbol":      _to_gate_symbol(sig.symbol),
            "action":      _side_map.get(sig.side, "做多"),
            "signal_type": _src_map.get(sig.source, "分析師訊號"),
            "entry_price": float(sig.entry_price or 0),
            "sl_price":    float(sig.sl_price or 0),
            "tp1_price":   float(sig.tp1_price) if sig.tp1_price else None,
            "tp2_price":   float(sig.tp2_price) if sig.tp2_price else None,
            "tp3_price":   float(sig.tp3_price) if sig.tp3_price else None,
            "tp4_price":   float(sig.tp4_price) if sig.tp4_price else None,
            "max_risk_usdt": 10.0,
            # [方向二 06-13] 連續信心倉位（取代 3 段式）：研究實證連續縮放可砍回撤 84-93%。
            #   signal_score = clip(50 + oos_conf*50, 55, 95) → gate-quant 映射 0.5x~1.5x
            #   conf 0.5→75(1.0x)｜0.7→85(1.2x)｜0.9→95(1.5x)。bounded 不會爆倉。
            #   無 conf → fallback 舊 3 段（重倉90/正常70）。
            "signal_score": _conf_to_score(me),
            # [v27/v28] 訊號品質 metadata + Tardis 過濾結果
            "quality":      sp.get("quality") or None,
            "market_mode":  me.get("market_mode") or None,
            "fuel_score":   me.get("fuel_score"),
            "signal_rs":    me.get("rs"),
            "tardis_multiplier":     tardis_eval.get("tardis_multiplier", 1.0),
            "tardis_state":          tardis_eval.get("tardis_state"),
            "tardis_oi_change_30m":  tardis_eval.get("tardis_oi_change_30m"),
            "tardis_fr_avg":         tardis_eval.get("tardis_fr_avg"),
        }
        r = await http.post(
            f"{GATE_QUANT_URL}/webhook/tg-signal",
            json=payload,
            headers={"x-webhook-token": os.environ.get("WEBHOOK_TOKEN", "")},
            timeout=10.0,
        )
        result = r.json() if r.status_code == 200 else {"error": r.text[:200]}
        if r.status_code == 200:
            logger.info("[auto-trade] Gate Quant 下單成功 #%s %s %s resp=%s",
                        signal_id, sig.symbol, sig.side, str(result)[:200])
        else:
            logger.warning("[auto-trade] Gate Quant 回傳 %s：%s",
                           r.status_code, r.text[:300])
        return result
    except Exception as exc:
        logger.error("Forward to gate quant fail: %s", exc)
        return {"error": str(exc)}


async def update_gate_tpsl(existing, sig: IncomingSignal):
    """更新 Gate 的 TP/SL（自動偵測持倉方向，支援 SOXLUSDT/SOXL_USDT 兩種格式）"""
    try:
        # 自動偵測方向（sig.side 來自 jackbot 原始訊號，與持倉同向）
        direction = sig.side or ""
        r = await http.post(
            f"{GATE_QUANT_URL}/orders/patch-tpsl",
            params={"symbol": sig.symbol, "entry_price": sig.entry_price, "direction": direction},
            headers={"x-admin-token": ADMIN_TOKEN},
        )
        if r.status_code != 200:
            logger.warning("[patch-tpsl] #%s %s status=%s body=%s",
                           existing.get("id"), sig.symbol, r.status_code, r.text[:200])
        else:
            logger.info("[patch-tpsl] #%s %s dir=%s ok", existing.get("id"), sig.symbol, direction)
    except Exception as exc:
        logger.error("Update Gate TPSL fail: %s", exc)


async def close_gate_position(symbol: str, side: str | None = None):
    """平倉所有啟用量化帳號的指定合約（雙帳號）。"""
    try:
        contract = _normalize_symbol(symbol)
        params = {"contract": contract}
        if side in ("long", "short"):
            params["side"] = side
        r = await http.post(
            f"{GATE_QUANT_URL}/emergency/close-all",
            params=params,
            headers={"x-admin-token": ADMIN_TOKEN},
            timeout=30.0,
        )
        if r.status_code == 200:
            logger.info("[close-all] %s side=%s ok", contract, side or "all")
        else:
            logger.warning("[close-all] %s failed %s: %s", contract, r.status_code, r.text[:200])
    except Exception as exc:
        logger.error("Close Gate position fail: %s", exc)


# ── 查詢 API ────────────────────────────────────────────────────────────────
@app.get("/signals")
async def list_signals(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    symbol: Optional[str] = None,
):
    """列出訊號"""
    async with db_pool.acquire() as conn:
        query = "SELECT * FROM signal_tracker WHERE 1=1"
        params = []
        if status:
            params.append(status)
            query += f" AND status = ${len(params)}"
        if symbol:
            params.append(symbol)
            query += f" AND symbol = ${len(params)}"
        params.append(limit)
        params.append(offset)
        query += f" ORDER BY created_at DESC LIMIT ${len(params)-1} OFFSET ${len(params)}"

        rows = await conn.fetch(query, *params)
        return [dict(r) for r in rows]


@app.get("/signals/active")
async def list_active():
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM signal_tracker
            WHERE status IN ('active','tp1','tp2','tp3')
            ORDER BY created_at DESC
            """
        )
        return [dict(r) for r in rows]


@app.get("/signals/{signal_id}")
async def get_signal(signal_id: int):
    async with db_pool.acquire() as conn:
        sig = await conn.fetchrow("SELECT * FROM signal_tracker WHERE id=$1", signal_id)
        if not sig:
            raise HTTPException(404, "Signal not found")
        updates = await conn.fetch(
            "SELECT * FROM signal_updates WHERE signal_id=$1 ORDER BY created_at DESC",
            signal_id,
        )
        return {"signal": dict(sig), "updates": [dict(u) for u in updates]}


COOLDOWN_HOURS = float(os.environ.get("SYMBOL_SL_COOLDOWN_HOURS", "24"))
COOLDOWN_MIN_LOSS_PCT = float(os.environ.get("SYMBOL_SL_COOLDOWN_LOSS_PCT", "25"))


@app.get("/internal/symbol-cooldown")
async def symbol_cooldown(symbol: str, side: str):
    """同幣同向近期大虧止損 → 冷卻（供 tracker_hook 查詢）。"""
    side = (side or "").lower()
    if side not in ("long", "short"):
        raise HTTPException(400, "side must be long or short")
    sym = symbol.upper().replace("_", "")
    cutoff = datetime.now(timezone.utc) - timedelta(hours=COOLDOWN_HOURS)
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, pnl_pct, closed_at
            FROM signal_tracker
            WHERE symbol = $1 AND side = $2
              AND status IN ('sl', 'reversed', 'risk_exit')
              AND closed_at > $3
              AND pnl_pct IS NOT NULL
              AND pnl_pct::float8 < $4::float8
            ORDER BY closed_at DESC
            LIMIT 1
            """,
            sym,
            side,
            cutoff,
            -abs(COOLDOWN_MIN_LOSS_PCT),
        )
    if not row:
        return {"blocked": False}
    sym = symbol.replace("USDT", "")
    return {
        "blocked": True,
        "signal_id": row["id"],
        "pnl_pct": float(row["pnl_pct"]),
        "reason": (
            f"{sym} {side} 近 {int(COOLDOWN_HOURS)}h 內止損 "
            f"（#{row['id']} {float(row['pnl_pct']):.1f}%），同向冷卻中"
        ),
    }


@app.get("/stats")
async def stats():
    async with db_pool.acquire() as conn:
        total = await conn.fetchval("SELECT COUNT(*) FROM signal_tracker")
        active = await conn.fetchval("SELECT COUNT(*) FROM signal_tracker WHERE status IN ('active','tp1','tp2','tp3')")
        wins = await conn.fetchval("SELECT COUNT(*) FROM signal_tracker WHERE status IN ('tp_final')")
        partial_wins = await conn.fetchval("SELECT COUNT(*) FROM signal_tracker WHERE status IN ('tp1','tp2','tp3')")
        losses = await conn.fetchval("SELECT COUNT(*) FROM signal_tracker WHERE status='sl'")
        reversed_count = await conn.fetchval("SELECT COUNT(*) FROM signal_tracker WHERE status='reversed'")
        rejected = await conn.fetchval("SELECT COUNT(*) FROM signal_tracker WHERE status='rejected'")

        win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

        # 各訊號類型勝率
        per_source = await conn.fetch(
            """
            SELECT source,
                   COUNT(*) FILTER (WHERE status='tp_final')                  AS wins,
                   COUNT(*) FILTER (WHERE status='sl')                        AS losses,
                   COUNT(*) FILTER (WHERE status='rejected')                  AS rejected,
                   COUNT(*) AS total
            FROM signal_tracker
            GROUP BY source
            """
        )
        sources = []
        for r in per_source:
            w, l = r["wins"], r["losses"]
            sources.append({
                "source": r["source"],
                "total": r["total"],
                "wins": w,
                "losses": l,
                "rejected": r["rejected"],
                "win_rate": round(w / (w + l) * 100, 2) if (w + l) > 0 else 0,
            })

        return {
            "total": total,
            "active": active,
            "wins": wins,
            "partial_wins": partial_wins,
            "losses": losses,
            "reversed": reversed_count,
            "rejected": rejected,
            "win_rate": round(win_rate, 2),
            "filter_rate": round(rejected / total * 100, 2) if total > 0 else 0,
            "by_source": sources,
            "market_state": get_market_state(),
        }
