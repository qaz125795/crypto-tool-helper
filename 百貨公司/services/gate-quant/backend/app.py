from __future__ import annotations

import os
import time
import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("quant_app")

from fastapi import Depends, FastAPI, Header, HTTPException, Query

from backend.core.models import Direction, GateAccountConfig, GateAPIConfig, HealthResponse, Intent, OrderRequest, OrderResponse, OrderType, PositionResponse, StrategyConfig, TGBotConfig, TGSignalPayload, TimeInForce, WebhookSignal
from backend.bot import OrderbookBot
from backend.exchanges.gate_perp import GatePerpAdapter
from backend.settings import settings
from backend.tg_bot_manager import TGBotManager
from backend.volume_runner.config import VolumeRunnerConfig
from backend.volume_runner.runner import VolumeRunner


app = FastAPI(title="Quant Admin API", version="0.1.0")

gate = GatePerpAdapter()
adapters = {"gate": gate}
_APP_DIR = Path(__file__).parent.parent
_STATE_FILE = _APP_DIR / "runtime_state.json"

RISK_BREAKER_ENABLED = os.getenv("RISK_BREAKER_ENABLED", "1") == "1"
DAILY_LOSS_CAP_USDT = float(os.getenv("DAILY_LOSS_CAP_USDT", "120"))
MAX_DRAWDOWN_PCT = float(os.getenv("MAX_DRAWDOWN_PCT", "35"))
MAX_CONCURRENT_POSITIONS = int(os.getenv("MAX_CONCURRENT_POSITIONS", "5"))

def _to_float(x) -> float | None:
    try:
        return float(x)
    except Exception:
        return None


def _default_accounts() -> list[GateAccountConfig]:
    return [GateAccountConfig(slot=i) for i in range(1, 11)]


def _accounts_from_state(data: list | None) -> list[GateAccountConfig]:
    if not isinstance(data, list):
        return _default_accounts()
    result = _default_accounts()
    for item in data:
        if isinstance(item, dict):
            try:
                acct = GateAccountConfig.model_validate(item)
                if 1 <= acct.slot <= 10:
                    result[acct.slot - 1] = acct
            except Exception:
                pass
    return result


def _load_runtime_state() -> dict:
    try:
        if _STATE_FILE.exists():
            data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save_runtime_state(app_obj: FastAPI) -> None:
    try:
        accts = getattr(app_obj.state, "accounts", _default_accounts())
        notify_chat_id = ""
        nb = getattr(app_obj.state, "notify_bot", None)
        if nb:
            notify_chat_id = nb.admin_chat_id
        # 記錄 TG bot 運行狀態，下次重啟時可自動恢復
        tg_bot = getattr(app_obj.state, "tg_bot", None)
        tg_bot_running = bool(tg_bot and getattr(tg_bot, "running", False))
        state_data = {
            "strategy_config": app_obj.state.strategy_config.model_dump(),
            "tg_bot_config": app_obj.state.tg_bot_config.model_dump(),
            "gate_config": {
                "gate_base_url": settings.gate_base_url,
                "gate_key": settings.gate_key,
                "gate_secret": settings.gate_secret,
                "gate_futures_settle": settings.gate_futures_settle,
            },
            "accounts": [a.model_dump() for a in accts],
            "notify_chat_id": notify_chat_id,
            "tg_bot_running": tg_bot_running,
        }
        _STATE_FILE.write_text(
            json.dumps(state_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


@app.on_event("startup")
async def _startup() -> None:
    runtime_state = _load_runtime_state()

    # 通知機器人（最先初始化，之後 callbacks 需要引用）
    from backend.tg_notify_bot import TGNotifyBot  # noqa: PLC0415
    _saved_chat_id = runtime_state.get("notify_chat_id", "") if isinstance(runtime_state, dict) else ""
    _notify_chat_id = str(_saved_chat_id or settings.tg_notify_chat_id).strip()
    notify_bot = TGNotifyBot(token=settings.tg_notify_token, admin_chat_id=_notify_chat_id)
    app.state.notify_bot = notify_bot

    saved_gate = runtime_state.get("gate_config") if isinstance(runtime_state, dict) else {}
    if isinstance(saved_gate, dict):
        settings.gate_base_url = str(saved_gate.get("gate_base_url") or settings.gate_base_url).strip().rstrip("/")
        settings.gate_key = str(saved_gate.get("gate_key") or settings.gate_key).strip()
        settings.gate_secret = str(saved_gate.get("gate_secret") or settings.gate_secret).strip()
        settings.gate_futures_settle = str(saved_gate.get("gate_futures_settle") or settings.gate_futures_settle).strip().lower()

    global gate
    gate = GatePerpAdapter()
    adapters["gate"] = gate

    # 先用 app.state 儲存（簡單可用），之後可換資料庫
    # 熱重載/版本演進時，可能殘留舊欄位；這裡做一次升級/補齊預設。
    existing = getattr(app.state, "strategy_config", None)
    saved_strategy = runtime_state.get("strategy_config") if isinstance(runtime_state, dict) else None
    if existing is None:
        if isinstance(saved_strategy, dict):
            try:
                app.state.strategy_config = StrategyConfig.model_validate(saved_strategy)
            except Exception:
                app.state.strategy_config = StrategyConfig()
        else:
            app.state.strategy_config = StrategyConfig()
    else:
        try:
            if hasattr(existing, "model_dump"):
                data = existing.model_dump()
            elif isinstance(existing, dict):
                data = existing
            else:
                data = {}
            app.state.strategy_config = StrategyConfig.model_validate(data)
        except Exception:
            app.state.strategy_config = StrategyConfig()

    # Orderbook bot manager
    if not hasattr(app.state, "bot"):
        bot = OrderbookBot()
        bot.strategy_getter = lambda: app.state.strategy_config
        app.state.bot = bot

    # TG signal bot manager
    if not hasattr(app.state, "tg_bot"):
        app.state.tg_bot = TGBotManager()

    # 注入進程內直呼回調，避免 TG bot HTTP 打自己造成 connection refused
    async def _tg_order_callback(payload: dict) -> dict:
        from backend.core.models import TGSignalPayload  # noqa: PLC0415
        sig = TGSignalPayload(**payload)
        # 分發到所有啟用帳號並行下單，回傳 dispatch 彙總結果
        return await _dispatch_signal_to_accounts(sig)

    app.state.tg_bot.order_callback = _tg_order_callback

    async def _tg_update_tpsl_callback(payload: dict) -> dict:
        from backend.core.models import TGSignalPayload  # noqa: PLC0415
        sig = TGSignalPayload(**payload)
        return await _dispatch_update_tpsl_to_accounts(sig)

    app.state.tg_bot.update_tpsl_callback = _tg_update_tpsl_callback

    async def _cancel_duplicate_entries_callback(symbol: str, direction: str) -> dict:
        """成交後撤銷同合約同方向的其他進場掛單（防雙重成交）。"""
        from backend.exchanges.gate_perp import GatePerpAdapter as _Adp  # noqa: PLC0415
        accts = [a for a in app.state.accounts if a.enabled and a.has_credentials()]
        # 做多成交 → 撤銷其他 bid（買）方向未成交限價單
        # 做空成交 → 撤銷其他 ask（賣）方向未成交限價單
        side = "bid" if direction in ("做多", "LONG") else "ask"
        results = []
        for acct in accts:
            _g = _Adp(api_key=acct.api_key, api_secret=acct.api_secret,
                      base_url=settings.gate_base_url, settle=settings.gate_futures_settle)
            try:
                # 只撤該合約 + 方向的未成交限價單
                r = await _g.cancel_all_limit_orders(contract=symbol, side=side)
                results.append({"slot": acct.slot, "ok": True, "result": r})
            except Exception as e:
                results.append({"slot": acct.slot, "ok": False, "error": str(e)})
            finally:
                await _g.aclose()
        return {"symbol": symbol, "direction": direction, "results": results}

    app.state.tg_bot.cancel_duplicate_entries_callback = _cancel_duplicate_entries_callback

    # ── 通知機器人指令回調（使用已設定的 local notify_bot 變數）──
    nb = notify_bot  # 此時 notify_bot 一定已由 _startup 前段建立

    async def _nb_status() -> str:
        tg = app.state.tg_bot
        accts = app.state.accounts
        enabled = sum(1 for a in accts if a.enabled and a.has_credentials())
        return (
            f"📊 <b>系統狀態</b>\n"
            f"TG 訊號機器人：{'🟢 執行中' if tg.running else '⚪ 已停止'}\n"
            f"TG 連線：{'✅ 已連線' if tg.connected else '❌ 未連線'}\n"
            f"啟用帳號：{enabled} 組\n"
            f"時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    async def _nb_positions() -> str:
        """每帳號一行摘要（持倉數 + 浮盈虧），再附上各倉精簡列表。"""
        from backend.exchanges.gate_perp import GatePerpAdapter as _Adp  # noqa: PLC0415
        accts = [a for a in app.state.accounts if a.enabled and a.has_credentials()]
        if not accts:
            return "⚠️ 無啟用帳號"
        now_str = datetime.now().strftime("%H:%M:%S")
        lines = [f"📊 <b>各帳號持倉</b>（{now_str}）"]
        total_upnl = 0.0
        for acct in accts:
            _name = acct.name or f"帳號{acct.slot}"
            _g = _Adp(api_key=acct.api_key, api_secret=acct.api_secret,
                      base_url=settings.gate_base_url, settle=settings.gate_futures_settle)
            try:
                pos_raw = await _g.get_positions()
                plist = pos_raw if isinstance(pos_raw, list) else [pos_raw]
                upnl = 0.0
                pos_items = []
                for p in plist:
                    try:
                        sz = float(p.get("size", 0))
                    except Exception:
                        sz = 0.0
                    if sz != 0:
                        u = float(p.get("unrealised_pnl", 0) or 0)
                        upnl += u
                        d = "多" if sz > 0 else "空"
                        pos_items.append(f"{p.get('contract','?')}({d}) {u:+.2f}U")
                total_upnl += upnl
                sign = "🟢" if upnl >= 0 else "🔴"
                if not pos_items:
                    lines.append(f"📁 <b>{_name}</b>：無持倉")
                else:
                    lines.append(f"{sign} <b>{_name}</b>（{len(pos_items)}倉 {upnl:+.2f}U）")
                    # 每行 4 個，最多顯示 20 個（再多就省略）
                    for i in range(0, min(len(pos_items), 20), 4):
                        lines.append("  " + "  ".join(pos_items[i:i+4]))
                    if len(pos_items) > 20:
                        lines.append(f"  … 省略 {len(pos_items)-20} 個倉位")
            except Exception as e:
                lines.append(f"❌ <b>{_name}</b>：查詢失敗 {e}")
            finally:
                await _g.aclose()
        sign_t = "🟢" if total_upnl >= 0 else "🔴"
        lines.append(f"\n{sign_t} <b>合計浮盈虧：{total_upnl:+.2f} USDT</b>")
        return "\n".join(lines)

    async def _nb_pnl() -> str:
        """損益摘要：各帳號浮盈虧 + 訊號記錄勝率。"""
        from backend.exchanges.gate_perp import GatePerpAdapter as _Adp  # noqa: PLC0415
        accts = [a for a in app.state.accounts if a.enabled and a.has_credentials()]
        now_str = datetime.now().strftime("%H:%M:%S")
        lines = [f"💰 <b>損益摘要</b>（{now_str}）"]

        # 1. 各帳號浮盈虧
        total_upnl = 0.0
        for acct in accts:
            _name = acct.name or f"帳號{acct.slot}"
            _g = _Adp(api_key=acct.api_key, api_secret=acct.api_secret,
                      base_url=settings.gate_base_url, settle=settings.gate_futures_settle)
            try:
                pos_raw = await _g.get_positions()
                plist = pos_raw if isinstance(pos_raw, list) else [pos_raw]
                upnl = 0.0
                cnt = 0
                for p in plist:
                    try:
                        sz = float(p.get("size", 0))
                    except Exception:
                        sz = 0.0
                    if sz != 0:
                        cnt += 1
                        upnl += float(p.get("unrealised_pnl", 0) or 0)
                total_upnl += upnl
                sign = "🟢" if upnl >= 0 else "🔴"
                lines.append(f"  {sign} <b>{_name}</b>（{cnt}倉）{upnl:+.2f}U")
            except Exception as e:
                lines.append(f"  ❌ <b>{_name}</b>：{e}")
            finally:
                await _g.aclose()
        sign_t = "🟢" if total_upnl >= 0 else "🔴"
        lines.append(f"  {sign_t} <b>浮盈虧合計：{total_upnl:+.2f} USDT</b>")

        # 2. 勝率統計（從訊號記錄）
        try:
            tg_mgr = app.state.tg_bot
            all_persisted = tg_mgr._persisted or []
            by_status: dict[str, int] = {}
            for d in all_persisted:
                if isinstance(d, dict):
                    s = d.get("status", "?")
                    by_status[s] = by_status.get(s, 0) + 1
            total_logs = len(all_persisted)
            closed_cnt = by_status.get("已平倉", 0)
            active_cnt = by_status.get("已成交", 0) + by_status.get("掛單中", 0)
            lines.append(f"\n📈 <b>訊號記錄</b>（共 {total_logs} 筆）")
            lines.append(f"  ✅ 已平倉：{closed_cnt} 筆　🔄 持倉中：{active_cnt} 筆")
            lines.append(f"  ⏭️ 跳過：{by_status.get('skipped',0)} 筆　"
                         f"❌ 失敗：{by_status.get('error',0)} 筆")
            lines.append(f"\n💡 詳細勝率請至前端 UI 查看績效曲線圖")
        except Exception as e:
            lines.append(f"\n⚠️ 統計失敗：{e}")

        return "\n".join(lines)

    async def _nb_winrate() -> str:
        """查詢各帳號最近 100 筆平倉，計算勝率與平均損益。"""
        from backend.exchanges.gate_perp import GatePerpAdapter as _Adp  # noqa: PLC0415
        accts = [a for a in app.state.accounts if a.enabled and a.has_credentials()]
        if not accts:
            return "⚠️ 無啟用帳號"
        now_str = datetime.now().strftime("%H:%M:%S")
        lines = [f"📊 <b>勝率統計</b>（最近 100 筆平倉，{now_str}）\n"]
        grand_wins = 0
        grand_losses = 0
        grand_pnl = 0.0

        for acct in accts:
            _name = acct.name or f"帳號{acct.slot}"
            _g = _Adp(api_key=acct.api_key, api_secret=acct.api_secret,
                      base_url=settings.gate_base_url, settle=settings.gate_futures_settle)
            try:
                closes = await _g.get_position_closes(limit=100)
                wins = 0
                losses = 0
                total_pnl = 0.0
                for c in closes:
                    pnl_val = None
                    for key in ("realised_pnl", "pnl"):
                        try:
                            v = c.get(key)
                            if v is not None and v != "":
                                pnl_val = float(v)
                                break
                        except Exception:
                            pass
                    if pnl_val is None:
                        continue
                    total_pnl += pnl_val
                    if pnl_val > 0:
                        wins += 1
                    elif pnl_val < 0:
                        losses += 1
                total = wins + losses
                wr = (wins / total * 100) if total > 0 else 0.0
                sign = "🟢" if total_pnl >= 0 else "🔴"
                grand_wins += wins
                grand_losses += losses
                grand_pnl += total_pnl
                lines.append(
                    f"{sign} <b>{_name}</b>  {total} 筆\n"
                    f"  ✅ 獲利 {wins} 筆  ❌ 虧損 {losses} 筆  勝率 <b>{wr:.1f}%</b>\n"
                    f"  累計已實現：{total_pnl:+.2f} USDT"
                )
            except Exception as e:
                lines.append(f"❌ <b>{_name}</b>：查詢失敗 {e}")
            finally:
                await _g.aclose()

        if accts:
            gt = grand_wins + grand_losses
            gwr = (grand_wins / gt * 100) if gt > 0 else 0.0
            sign_g = "🟢" if grand_pnl >= 0 else "🔴"
            lines.append(
                f"\n{sign_g} <b>三帳號合計</b>\n"
                f"  勝率 <b>{gwr:.1f}%</b>（{grand_wins}✅ / {grand_losses}❌）\n"
                f"  已實現損益：<b>{grand_pnl:+.2f} USDT</b>"
            )
        return "\n".join(lines)

    async def _nb_logs() -> str:
        tg = app.state.tg_bot
        logs = list(tg.text_logs)[:20]
        return "📋 <b>最近操作日誌</b>\n" + ("\n".join(logs) if logs else "（無日誌）")

    async def _nb_calibrate() -> str:
        r = await tg_bot_calibrate()
        checked = r.get("checked", 0)
        updated = r.get("updated", 0)
        return f"🔍 校準完成：{checked} 筆訊號，{updated} 筆已更新 SL/TP"

    async def _nb_audit_tpsl() -> str:
        r = await audit_positions_tpsl(force_close=True)
        s = r.get("summary", {})
        lines = [
            f"🛡️ <b>SL/TP 稽核完成</b>",
            f"檢查 {s.get('checked',0)} 倉  正常 {s.get('ok',0)}",
            f"補掛 {s.get('patched',0)}  強平 {s.get('force_closed',0)}",
            f"錯誤 {s.get('errors',0)}",
        ]
        # 細節（若有動作）
        for acct_r in r.get("results", []):
            for a in acct_r.get("actions", []):
                if a.get("action") in ("patched", "force_closed", "error", "patch_failed"):
                    lines.append(
                        f"  • [{acct_r.get('name')}] {a.get('contract')} {a.get('direction')}：{a.get('status')}"
                    )
        return "\n".join(lines)

    async def _nb_start_bot() -> str:
        tg = app.state.tg_bot
        cfg = app.state.tg_bot_config
        if tg.running:
            return "⚠️ TG 訊號機器人已在執行中"
        cfg_dict = cfg.model_dump()
        cfg_dict["chat_ids"] = cfg.chat_ids_as_list()
        await tg.start(cfg_dict)
        return "✅ TG 訊號機器人已啟動"

    async def _nb_stop_bot() -> str:
        tg = app.state.tg_bot
        await tg.stop()
        return "⏹️ TG 訊號機器人已停止"

    async def _nb_restart_bot() -> str:
        tg = app.state.tg_bot
        cfg = app.state.tg_bot_config
        try:
            if tg.running:
                await tg.stop()
                await asyncio.sleep(1)
            cfg_dict = cfg.model_dump()
            cfg_dict["chat_ids"] = cfg.chat_ids_as_list()
            await tg.start(cfg_dict)
            _save_runtime_state(app)
            return "🔄 TG 訊號機器人已成功重啟，監聽中"
        except Exception as e:
            return f"❌ 重啟失敗：{e}"

    async def _nb_restart_server() -> None:
        """延遲 2 秒後重啟 uvicorn 進程。"""
        import os, sys  # noqa: E401
        try:
            await nb.send("🔁 後端伺服器重啟中，約 10 秒後恢復…")
            await asyncio.sleep(2)
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            await nb.send(f"❌ 伺服器重啟失敗：{e}")

    async def _nb_stop_long() -> str:
        r = await emergency_close_all(side="long")
        return f"🔴 一鍵平多完成：{r.get('closed', 0)} 個帳號"

    async def _nb_stop_short() -> str:
        r = await emergency_close_all(side="short")
        return f"🔵 一鍵平空完成：{r.get('closed', 0)} 個帳號"

    async def _nb_cancel_sl() -> str:
        r = await emergency_cancel_price_orders()
        return f"🧹 已撤所有條件單：{r}"

    async def _nb_stop_all() -> str:
        await emergency_close_all(side="all")
        await emergency_cancel_limit_orders(side="all")
        await emergency_cancel_price_orders()
        return "☢️ 緊急全平 + 全撤單 已執行"

    nb.cmd_status_cb = _nb_status
    nb.cmd_positions_cb = _nb_positions
    nb.cmd_pnl_cb = _nb_pnl
    nb.cmd_winrate_cb = _nb_winrate
    nb.cmd_logs_cb = _nb_logs
    nb.cmd_calibrate_cb = _nb_calibrate
    nb.cmd_audit_tpsl_cb = _nb_audit_tpsl
    async def _nb_riskinfo() -> str:
        """回傳所有啟用帳號的固定虧損設定。"""
        accts = app.state.accounts
        lines = ["💰 <b>各帳號固定虧損設定</b>\n"]
        for a in accts:
            if not a.has_credentials():
                continue
            status = "✅" if a.enabled else "🔴 停用"
            lines.append(
                f"{status} <b>Slot{a.slot} {a.name or f'帳號{a.slot}'}</b>\n"
                f"  爆擊：<b>{a.risk_blast} U</b>　"
                f"狙擊：<b>{a.risk_holding} U</b>　"
                f"分析師：<b>{a.risk_analyst} U</b>"
            )
        lines.append("\n用 /setrisk 修改設定")
        return "\n".join(lines)

    async def _nb_setrisk(acct_arg: str, risk_type: str, amount: float) -> str:
        """依帳號 slot 或名稱更新固定虧損。"""
        accts: list = app.state.accounts
        # 找目標帳號：支援 slot 數字或名稱（模糊比對）
        target = None
        if acct_arg.isdigit():
            slot = int(acct_arg)
            if 1 <= slot <= len(accts):
                target = accts[slot - 1]
        else:
            for a in accts:
                if acct_arg.lower() in (a.name or "").lower():
                    target = a
                    break
        if target is None:
            return f"❌ 找不到帳號「{acct_arg}」\n請輸入 Slot 編號（1-10）或帳號名稱"
        if not target.has_credentials():
            return f"❌ Slot{target.slot} 未設定 API Key，無法操作"

        # 更新對應欄位
        body: dict = {}
        if risk_type == "blast":
            body["risk_blast"] = amount
        elif risk_type == "holding":
            body["risk_holding"] = amount
        elif risk_type == "analyst":
            body["risk_analyst"] = amount
        elif risk_type == "all":
            body["risk_blast"] = amount
            body["risk_holding"] = amount
            body["risk_analyst"] = amount

        try:
            await update_account(target.slot, body)
        except Exception as e:
            return f"❌ 更新失敗：{e}"

        # 重新讀取確認
        updated = app.state.accounts[target.slot - 1]
        type_label = {"blast": "爆擊", "holding": "狙擊", "analyst": "分析師", "all": "全部"}.get(risk_type, risk_type)
        return (
            f"✅ <b>固定虧損已更新</b>\n\n"
            f"帳號：<b>Slot{updated.slot} {updated.name or f'帳號{updated.slot}'}</b>\n"
            f"修改類型：<b>{type_label}</b>\n"
            f"新設定：<b>{amount} U</b>\n\n"
            f"目前設定：\n"
            f"  爆擊 {updated.risk_blast} U　狙擊 {updated.risk_holding} U　分析師 {updated.risk_analyst} U"
        )

    nb.cmd_start_bot_cb = _nb_start_bot
    nb.cmd_stop_bot_cb = _nb_stop_bot
    nb.cmd_restart_bot_cb = _nb_restart_bot
    nb.cmd_restart_server_cb = _nb_restart_server
    nb.cmd_stop_long_cb = _nb_stop_long
    nb.cmd_stop_short_cb = _nb_stop_short
    nb.cmd_cancel_sl_cb = _nb_cancel_sl
    nb.cmd_stop_all_cb = _nb_stop_all
    nb.cmd_riskinfo_cb = _nb_riskinfo
    nb.cmd_setrisk_cb = _nb_setrisk

    async def _nb_health() -> None:
        """手動觸發健康檢查（與每日自動相同邏輯）。"""
        try:
            await _run_daily_health_check()
        except Exception as e:
            await app.state.notify_bot.send(f"❌ 健康檢查失敗：{e}")

    nb.cmd_health_cb = _nb_health

    # 注入通知 callback 到 TG 訊號機器人
    async def _tg_notify_callback(event: str, **kwargs) -> None:
        try:
            if event == "signal_received":
                await nb.notify_signal_received(**kwargs)
            elif event == "dispatch_result":
                await nb.notify_dispatch_result(**kwargs)
            elif event == "order_filled":
                await nb.notify_order_filled(**kwargs)
            elif event == "order_error":
                await nb.notify_order_error(**kwargs)
            elif event == "tpsl_updated":
                await nb.notify_tpsl_updated(**kwargs)
            elif event == "safety_closed":
                # 安全閥強制平倉通知（大額帳號特別重要）
                acct_name = kwargs.get("account_name", "?")
                contract = kwargs.get("contract", "?")
                upnl = kwargs.get("upnl", 0)
                threshold = kwargs.get("threshold", 0)
                await nb.send(
                    f"🚨 <b>安全閥強制平倉</b>\n"
                    f"帳號：<b>{acct_name}</b>\n"
                    f"合約：<b>{contract}</b>\n"
                    f"浮虧：<b>{upnl:+.2f} U</b> &gt; 閾值 {threshold:.1f} U\n"
                    f"已強制市價平倉並撤銷條件單"
                )
            elif event == "audit_missing_sl":
                acct_name = kwargs.get("account_name", "?")
                contract = kwargs.get("contract", "?")
                await nb.send(
                    f"⚠️ <b>稽核發現缺少 SL</b>\n"
                    f"帳號：{acct_name}｜合約：{contract}\n已自動補掛"
                )
        except Exception as _notify_err:
            logger.error(f"[notify] TG 推播失敗（event={event}）：{_notify_err}")

    app.state.tg_bot.notify_callback = _tg_notify_callback

    # 倉位監控 callback（供通知機器人偵測 TP/SL 觸發）
    async def _fetch_positions_for_monitor() -> dict:
        """回傳 {account_name: set("SYMBOL_方向")} 方便比對倉位變化。"""
        from backend.exchanges.gate_perp import GatePerpAdapter as _Adp  # noqa: PLC0415
        result: dict = {}
        for acct in app.state.accounts:
            if not acct.enabled or not acct.has_credentials():
                continue
            _g = _Adp(api_key=acct.api_key, api_secret=acct.api_secret,
                      base_url=settings.gate_base_url, settle=settings.gate_futures_settle)
            try:
                pos_raw = await _g.get_positions()
                pos_set: set = set()
                for p in (pos_raw if isinstance(pos_raw, list) else [pos_raw]):
                    try:
                        sz = float(p.get("size", 0))
                    except Exception:
                        sz = 0.0
                    if sz != 0:
                        contract = str(p.get("contract", ""))
                        direction = "做多" if sz > 0 else "做空"
                        pos_set.add(f"{contract}_{direction}")
                result[acct.name or f"帳號{acct.slot}"] = pos_set
            except Exception:
                pass
            finally:
                await _g.aclose()
        return result

    nb._fetch_positions_cb = _fetch_positions_for_monitor

    # 訊號記錄 callback（供非授權開倉核對）
    async def _fetch_signal_logs_for_auth() -> list:
        tg: TGBotManager = app.state.tg_bot
        logs: list[dict] = []
        for entry in list(tg.signal_logs):
            logs.append(entry.to_dict())
        for d in tg._persisted:
            logs.append(d)
        return logs

    nb._fetch_signal_logs_cb = _fetch_signal_logs_for_auth

    # 倉位平倉時查最近 round-trip PnL（用於推播實際損益）
    # 直接使用 Gate 的 position_close API，取得準確的已實現損益（含手續費）
    # 舊版 FIFO 手動配對在帳號有多筆歷史交易時會算錯（如 小熊 ETH 案例）
    async def _fetch_recent_pnl(account_name: str, symbol: str) -> "float | None":
        from backend.exchanges.gate_perp import GatePerpAdapter as _Adp  # noqa: PLC0415
        import time as _time  # noqa: PLC0415
        target = None
        for a in app.state.accounts:
            if (a.name == account_name or f"帳號{a.slot}" == account_name) and a.has_credentials():
                target = a
                break
        if not target:
            return None

        _g = _Adp(api_key=target.api_key, api_secret=target.api_secret,
                  base_url=settings.gate_base_url, settle=settings.gate_futures_settle)
        try:
            # 用 get_position_closes 直接取 Gate 計算好的 realised pnl
            closes = await _g.get_position_closes(contract=symbol, limit=5)
            if not closes:
                return None
            # 按 close_time 降序（最新的在前），取最新一筆
            closes_sorted = sorted(
                closes,
                key=lambda c: float(c.get("time") or c.get("close_time") or 0),
                reverse=True,
            )
            latest = closes_sorted[0]
            # Gate position_close 回傳 pnl = pnl_pnl + pnl_fee + pnl_fund（已含全部費用）
            pnl_val = latest.get("pnl")
            if pnl_val is None:
                pnl_val = latest.get("realised_pnl")
            return float(pnl_val) if pnl_val is not None else None
        except Exception:
            return None
        finally:
            await _g.aclose()

    nb._fetch_pnl_cb = _fetch_recent_pnl

    # 自動綁定 chat_id 後持久化
    def _on_chat_bound() -> None:
        try:
            _save_runtime_state(app)
        except Exception:
            pass

    nb._on_chat_bound_cb = _on_chat_bound

    # ── TG 控制機器人（@autotraderrrrr_Bot）指令輪詢 + 按鈕選單 ──
    from backend.tg_ctrl_bot import TGCtrlBot  # noqa: PLC0415
    from backend.exchanges.gate_perp import GatePerpAdapter as _AdpCtrl  # noqa: PLC0415

    ctrl = TGCtrlBot()

    async def _ctrl_positions() -> list:
        all_p: list = []
        for _a in [a for a in app.state.accounts if a.enabled and a.has_credentials()]:
            _g = _AdpCtrl(
                api_key=_a.api_key, api_secret=_a.api_secret,
                base_url=settings.gate_base_url, settle=settings.gate_futures_settle,
            )
            try:
                pos_raw = await _g.get_positions()
                for _p in (pos_raw if isinstance(pos_raw, list) else [pos_raw]):
                    try:
                        _sz = int(float(_p.get("size", 0) or 0))
                    except Exception:
                        _sz = 0
                    if _sz != 0:
                        _p["_account"] = _a.name or f"Slot{_a.slot}"
                        all_p.append(_p)
            except Exception:
                pass
            finally:
                await _g.aclose()
        return all_p

    async def _ctrl_close(sym: str) -> str:
        res: list[str] = []
        for _a in [a for a in app.state.accounts if a.enabled and a.has_credentials()]:
            _g = _AdpCtrl(
                api_key=_a.api_key, api_secret=_a.api_secret,
                base_url=settings.gate_base_url, settle=settings.gate_futures_settle,
            )
            try:
                pos_raw = await _g.get_positions()
                for _p in (pos_raw if isinstance(pos_raw, list) else [pos_raw]):
                    try:
                        _sz = int(float(_p.get("size", 0) or 0))
                    except Exception:
                        _sz = 0
                    if _sz != 0 and str(_p.get("contract", "")) == sym:
                        await _g.close_position_market(contract=sym, position_size=_sz)
                        res.append(f"{_a.name}: OK")
            except Exception as _e:
                res.append(f"{_a.name}: {_e}")
            finally:
                await _g.aclose()
        return "\n".join(res) if res else "未找到持倉"

    async def _ctrl_balance() -> str:
        import httpx as _httpx_bal  # noqa: PLC0415
        lines = ["💰 <b>各帳號餘額</b>\n"]
        for _a in [a for a in app.state.accounts if a.enabled and a.has_credentials()]:
            _g = _AdpCtrl(
                api_key=_a.api_key, api_secret=_a.api_secret,
                base_url=settings.gate_base_url, settle=settings.gate_futures_settle,
            )
            try:
                path = f"/futures/{settings.gate_futures_settle}/accounts"
                headers = _g._signed_headers("GET", f"/api/v4{path}", "", "")
                async with _httpx_bal.AsyncClient(base_url=settings.gate_base_url, timeout=10) as c:
                    r = await c.get(path, headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    total = data.get("total", "?")
                    avail = data.get("available", "?")
                    upnl = data.get("unrealised_pnl", data.get("unrealized_pnl", "?"))
                    lines.append(
                        f"📁 <b>{_a.name}</b>  總權益 {total}U  可用 {avail}U  浮盈虧 {upnl}U"
                    )
                else:
                    lines.append(f"❌ {_a.name} HTTP {r.status_code}")
            except Exception as _e:
                lines.append(f"❌ {_a.name} {_e}")
            finally:
                await _g.aclose()
        return "\n".join(lines)

    ctrl.get_positions_cb = _ctrl_positions
    ctrl.close_position_cb = _ctrl_close
    ctrl.get_status_cb = _nb_status
    ctrl.get_balance_cb = _ctrl_balance
    ctrl.audit_tpsl_cb = _nb_audit_tpsl
    ctrl.calibrate_cb = _nb_calibrate
    ctrl.pnl_cb = _nb_pnl
    ctrl.start_tgbot_cb = _nb_start_bot
    ctrl.stop_tgbot_cb = _nb_stop_bot
    app.state.tg_ctrl_bot = ctrl
    ctrl.start()

    # 通知機器人：推播 + 倉位監控（指令輪詢由 TGCtrl 接管，避免同 token 雙重 polling）
    await nb.start(enable_polling=not ctrl.running)
    asyncio.create_task(nb.notify_server_start())

    # ── 啟動核對：掃描現有倉位，標出非訊號系統持有的倉位 ────────────
    async def _startup_position_reconcile() -> None:
        """
        伺服器啟動後 20 秒（等訊號記錄載入完畢），
        掃描 Gate 所有帳號現有倉位，與訊號記錄交叉比對。
        發現沒有對應訊號的倉位 → 推播警告給 Telegram。
        """
        import os as _os_check
        if _os_check.environ.get("STARTUP_RECONCILE_DISABLED", "").lower() in ("true", "1", "yes"):
            return
        await asyncio.sleep(20)
        try:
            from backend.exchanges.gate_perp import GatePerpAdapter as _Adp  # noqa: PLC0415
            tg_mgr: TGBotManager = app.state.tg_bot

            # 建立訊號記錄 lookup：symbol_direction → True
            # 只要 TG 訊號記錄中曾出現過此合約方向（無論狀態），即視為已授權
            # 排除明確「從未開倉」的狀態：skipped / error / dry_run
            _excluded_status = {"skipped", "error", "dry_run"}
            authorized_set: set[str] = set()
            for entry in list(tg_mgr.signal_logs):
                d = entry.to_dict()
                if d.get("status") not in _excluded_status:
                    authorized_set.add(f"{d.get('symbol')}_{d.get('direction')}")
            for d in tg_mgr._persisted:
                if d.get("status") not in _excluded_status:
                    authorized_set.add(f"{d.get('symbol')}_{d.get('direction')}")

            unauthorized_found: list[str] = []
            for acct in app.state.accounts:
                if not acct.enabled or not acct.has_credentials():
                    continue
                _g = _Adp(api_key=acct.api_key, api_secret=acct.api_secret,
                          base_url=settings.gate_base_url, settle=settings.gate_futures_settle)
                try:
                    pos_raw = await _g.get_positions()
                    for p in (pos_raw if isinstance(pos_raw, list) else [pos_raw]):
                        try:
                            sz = float(p.get("size", 0))
                        except Exception:
                            sz = 0.0
                        if sz == 0:
                            continue
                        contract = str(p.get("contract", ""))
                        direction = "做多" if sz > 0 else "做空"
                        key = f"{contract}_{direction}"
                        if key not in authorized_set:
                            unauthorized_found.append(
                                f"  • [{acct.name}] {contract} {direction}（{int(sz)}張）"
                            )
                except Exception:
                    pass
                finally:
                    await _g.aclose()

            if unauthorized_found:
                msg = (
                    f"⚠️ <b>伺服器啟動核對：發現非訊號系統持有的倉位</b>\n"
                    f"以下倉位在訊號記錄中找不到對應記錄，\n"
                    f"可能是手動開倉、舊系統遺留或外部操作：\n\n"
                    + "\n".join(unauthorized_found)
                    + "\n\n請確認後決定是否手動平倉。"
                    + "\n如需全部平倉請發送 /stop_all"
                )
                await app.state.notify_bot.send(msg)
                logger.warning(f"[startup-reconcile] 發現 {len(unauthorized_found)} 個非授權倉位")
            else:
                logger.info("[startup-reconcile] ✅ 所有現有倉位均有對應訊號記錄")
        except Exception as e:
            logger.error(f"[startup-reconcile] 核對失敗：{e}")

    asyncio.create_task(_startup_position_reconcile())

    # 每 1 小時自動 SL/TP 稽核
    async def _periodic_audit_loop() -> None:
        await asyncio.sleep(60)  # 啟動後 1 分鐘才開始
        while True:
            try:
                await audit_positions_tpsl(force_close=True)
            except Exception as e:
                logger.error(f"[audit] 1小時稽核失敗：{e}")
            await asyncio.sleep(60 * 60)  # 每 1 小時

    asyncio.create_task(_periodic_audit_loop())

    # ── 每日健康檢查（每 24 小時，偏移 1 分鐘讓系統先穩定）──────────────
    async def _daily_health_check_loop() -> None:
        await asyncio.sleep(60)  # 啟動後 1 分鐘才開始計時
        while True:
            await asyncio.sleep(60 * 60 * 24)  # 每 24 小時
            try:
                await _run_daily_health_check()
            except Exception as e:
                logger.error(f"[health] 每日健康檢查失敗：{e}", exc_info=True)

    async def _run_daily_health_check() -> None:
        """檢查所有機器人和帳號 API 連線狀態，推播每日報告到 TG。"""
        from backend.exchanges.gate_perp import GatePerpAdapter as _HAdp  # noqa: PLC0415
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines: list[str] = [f"📋 <b>每日健康檢查報告</b>  {now_str}\n"]

        # 1. TG 訊號機器人
        tg_inst: TGBotManager = app.state.tg_bot
        tg_ok = tg_inst.running and tg_inst.connected
        lines.append(f"{'✅' if tg_ok else '❌'} TG 訊號機器人：{'運行中' if tg_ok else '已停止/斷線'}")

        # 2. 通知機器人
        nb_inst = app.state.notify_bot
        nb_ok = bool(nb_inst and nb_inst.token)
        lines.append(f"{'✅' if nb_ok else '❌'} TG 通知機器人：{'已設定' if nb_ok else '未設定 token'}")

        # 3. 各帳號 Gate API 連線
        lines.append("\n<b>帳號 Gate API 狀態：</b>")
        accts = [a for a in app.state.accounts if a.enabled and a.has_credentials()]
        all_gate_ok = True
        for acct in accts:
            _g = _HAdp(
                api_key=acct.api_key, api_secret=acct.api_secret,
                base_url=settings.gate_base_url, settle=settings.gate_futures_settle,
            )
            try:
                pos = await _g.get_positions()
                n_pos = sum(1 for p in (pos if isinstance(pos, list) else [pos])
                            if int(float(p.get("size", 0) or 0)) != 0)
                lines.append(f"  ✅ Slot{acct.slot} <b>{acct.name or f'帳號{acct.slot}'}</b>：連線正常，{n_pos} 個持倉")
            except Exception as e:
                lines.append(f"  ❌ Slot{acct.slot} <b>{acct.name or f'帳號{acct.slot}'}</b>：API 失敗 {str(e)[:60]}")
                all_gate_ok = False
            finally:
                try:
                    await _g.aclose()
                except Exception:
                    pass

        # 4. 整體狀態
        overall_ok = tg_ok and nb_ok and all_gate_ok
        lines.append(f"\n{'🟢 系統整體正常' if overall_ok else '🔴 部分服務異常，請確認'}")

        report = "\n".join(lines)
        logger.info(f"[health] 每日健康檢查完成，推播報告")
        try:
            await app.state.notify_bot.send(report)
        except Exception as e:
            logger.error(f"[health] 推播健康報告失敗：{e}")

    asyncio.create_task(_daily_health_check_loop())

    # ── 每日訊號 PnL 報表（台灣時間 23:55，推送各訊號類型勝率/盈虧）─────
    async def _daily_pnl_report_loop() -> None:
        """每天台灣 23:55 推送一份「今日各訊號類型 PnL 報表」到 notify_bot。"""
        from datetime import timezone as _tz, timedelta as _td  # noqa: PLC0415
        tw_tz = _tz(_td(hours=8))
        while True:
            try:
                # 計算到下一個 23:55 (UTC+8) 的秒數
                now_tw = datetime.now(tw_tz)
                target = now_tw.replace(hour=23, minute=55, second=0, microsecond=0)
                if now_tw >= target:
                    target = target + _td(days=1)
                wait_sec = (target - now_tw).total_seconds()
                await asyncio.sleep(max(60, wait_sec))
                # 觸發報表
                try:
                    data = await analytics_today_winrate()
                    if isinstance(data, dict) and data.get("accounts"):
                        lines = [f"📊 <b>每日訊號 PnL 報表</b>  {data.get('date', '')}"]
                        for acct_r in data.get("accounts", []):
                            n = acct_r.get("name", "?")
                            tot = acct_r.get("total", 0) or 0
                            wins = acct_r.get("wins", 0) or 0
                            losses = acct_r.get("losses", 0) or 0
                            wr = acct_r.get("win_rate")
                            pnl = acct_r.get("pnl", 0) or 0
                            wr_str = f"{wr}%" if wr is not None else "—"
                            lines.append(
                                f"\n<b>[{n}]</b> 平倉 {tot} 筆  勝 {wins} / 敗 {losses}  "
                                f"勝率 {wr_str}  PnL {pnl:+.2f}U"
                            )
                            for bt in acct_r.get("by_type", []):
                                if (bt.get("total") or 0) == 0:
                                    continue
                                t_wr = bt.get("win_rate")
                                t_wr_s = f"{t_wr}%" if t_wr is not None else "—"
                                lines.append(
                                    f"  · {bt.get('signal_type', '?')}：{bt.get('wins', 0)}勝/{bt.get('losses', 0)}敗  "
                                    f"勝率 {t_wr_s}  PnL {bt.get('pnl', 0):+.2f}U"
                                )
                        report = "\n".join(lines)
                        nb_inst2 = getattr(app.state, "notify_bot", None)
                        if nb_inst2:
                            await nb_inst2.send(report)
                        logger.info("[daily-pnl] 每日 PnL 報表已推送")
                    else:
                        logger.warning(f"[daily-pnl] 報表資料異常: {data}")
                except Exception as e:
                    logger.error(f"[daily-pnl] 產生報表失敗：{e}", exc_info=True)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[daily-pnl] loop 錯誤：{e}", exc_info=True)
                await asyncio.sleep(300)  # 出錯時等 5 分鐘再試

    asyncio.create_task(_daily_pnl_report_loop())

    # ── TG Bot Watchdog（每 5 分鐘檢查 running/connected，斷線自動重連並推警告）─
    async def _tg_bot_watchdog_loop() -> None:
        """每 5 分鐘檢查 tg-bot 是否仍在運行 & 已連線；若斷線：
           - 嘗試自動重啟一次
           - 同時推 TG 警告通知（避免靜默斷線害分析師訊號全漏接）
        """
        await asyncio.sleep(45)  # 啟動後 45 秒開始第一次檢查
        consecutive_fails = 0
        while True:
            try:
                tg = getattr(app.state, "tg_bot", None)
                cfg_watch: TGBotConfig = app.state.tg_bot_config
                should_listen = bool(cfg_watch.chat_ids_as_list())
                if should_listen and tg:
                    is_running = bool(getattr(tg, "running", False))
                    is_connected = bool(getattr(tg, "connected", False))
                    if not (is_running and is_connected):
                        consecutive_fails += 1
                        logger.warning(f"[tg-watchdog] TG Bot 異常 running={is_running} connected={is_connected} 連續 {consecutive_fails} 次")
                        try:
                            cfg: TGBotConfig = app.state.tg_bot_config
                            cfg_dict = cfg.model_dump()
                            cfg_dict["chat_ids"] = cfg.chat_ids_as_list()
                            await tg.start(cfg_dict)
                            logger.info("[tg-watchdog] TG Bot 已自動重啟")
                            try:
                                nb = getattr(app.state, "notify_bot", None)
                                if nb:
                                    await nb.send(
                                        f"⚠️ <b>TG 訊號機器人斷線自動重連</b>\n"
                                        f"running={is_running} connected={is_connected} 連續 {consecutive_fails} 次失敗\n"
                                        f"已嘗試自動重啟，請至後台確認狀態。"
                                    )
                            except Exception:
                                pass
                            consecutive_fails = 0
                            _save_runtime_state(app)
                        except Exception as re:
                            logger.error(f"[tg-watchdog] 自動重啟失敗：{re}")
                    else:
                        if consecutive_fails > 0:
                            logger.info("[tg-watchdog] TG Bot 已恢復")
                        consecutive_fails = 0
                        _save_runtime_state(app)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[tg-watchdog] loop 錯誤：{e}", exc_info=True)
            await asyncio.sleep(60)  # 每 1 分鐘

    asyncio.create_task(_tg_bot_watchdog_loop())

    async def _check_order_callback(symbol: str, order_id: str) -> dict:
        return await gate.get_order(symbol=symbol, order_id=order_id)

    app.state.tg_bot.check_order_callback = _check_order_callback

    # TG bot config（從 .env 預填預設值）
    if not hasattr(app.state, "tg_bot_config"):
        default_cfg = TGBotConfig(
            api_id=settings.tg_api_id,
            api_hash=settings.tg_api_hash,
            phone="",
            session_name="tg_signal_session",
            chat_ids="",
            default_leverage=20,
            webhook_token=settings.webhook_token,
            backend_url=f"http://127.0.0.1:{settings.local_api_port}",
        )
        saved_tg = runtime_state.get("tg_bot_config") if isinstance(runtime_state, dict) else None
        if isinstance(saved_tg, dict):
            try:
                app.state.tg_bot_config = TGBotConfig.model_validate(saved_tg)
            except Exception:
                app.state.tg_bot_config = default_cfg
        else:
            app.state.tg_bot_config = default_cfg

    # 多帳號設定
    if not hasattr(app.state, "accounts"):
        saved_accts = runtime_state.get("accounts") if isinstance(runtime_state, dict) else None
        app.state.accounts = _accounts_from_state(saved_accts)

    # 若帳號 1（使用者A）尚未設定 API Key，且 .env 有 GATE_KEY，則自動預填
    _slot1 = app.state.accounts[0]
    if not _slot1.api_key.strip() and settings.gate_key.strip():
        app.state.accounts[0] = GateAccountConfig(
            slot=1,
            name="使用者A",
            api_key=settings.gate_key,
            api_secret=settings.gate_secret,
            enabled=True,
            enable_blast=True,
            enable_holding=True,
            enable_analyst=True,
            risk_blast=10.0,
            risk_holding=10.0,
            risk_analyst=10.0,
        )
        print("[startup] 已將 .env Gate API 預填為帳號 1（使用者A）")

    _save_runtime_state(app)

    # ── 刷量 Runner（Maker 往返，Testnet 驗證 → 一週後帶單）────────────────
    vol_cfg = VolumeRunnerConfig.from_env()
    app.state.volume_runner_config = vol_cfg

    def _volume_gate_factory() -> GatePerpAdapter:
        slot = max(1, min(10, int(vol_cfg.account_slot)))
        accts: list = app.state.accounts
        acct = accts[slot - 1] if slot <= len(accts) else accts[0]
        key = (acct.api_key or settings.gate_key or "").strip()
        secret = (acct.api_secret or settings.gate_secret or "").strip()
        return GatePerpAdapter(
            api_key=key,
            api_secret=secret,
            base_url=settings.gate_base_url,
            settle=settings.gate_futures_settle,
        )

    async def _volume_report(msg: str) -> None:
        nb = getattr(app.state, "notify_bot", None)
        if nb and getattr(nb, "admin_chat_id", ""):
            await nb.send(msg)

    app.state.volume_runner = VolumeRunner(
        vol_cfg,
        gate_factory=_volume_gate_factory,
        base_url=settings.gate_base_url,
        report_callback=_volume_report,
    )

    if vol_cfg.enabled:
        async def _auto_start_volume_runner() -> None:
            await asyncio.sleep(5)
            try:
                vr: VolumeRunner = app.state.volume_runner
                await vr.start()
                logger.info("[startup] ✅ VolumeRunner 自動啟動 (dry_run=%s)", vol_cfg.dry_run)
                if app.state.notify_bot and settings.tg_notify_chat_id:
                    await app.state.notify_bot.send(
                        "📈 <b>刷量 Runner 已啟動</b>\n"
                        f"策略：雙邊掛單造市 · {'🧪 dry-run' if vol_cfg.dry_run else '🔴 實盤'}\n"
                        f"標的：{', '.join(vol_cfg.symbols)}\n"
                        f"API：{settings.gate_base_url}"
                    )
            except Exception as e:
                logger.error("[startup] VolumeRunner 啟動失敗: %s", e)

        asyncio.create_task(_auto_start_volume_runner())

    # ── 有設定監聽頻道即自動啟動（不依賴上次 running 旗標，避免重啟後漏接訊號）──
    _tg_cfg_boot: TGBotConfig = app.state.tg_bot_config
    if _tg_cfg_boot.chat_ids_as_list():
        async def _auto_start_tg_bot() -> None:
            await asyncio.sleep(3)  # 等其他 startup 任務完成
            try:
                tg: TGBotManager = app.state.tg_bot
                cfg: TGBotConfig = app.state.tg_bot_config
                cfg_dict = cfg.model_dump()
                cfg_dict["chat_ids"] = cfg.chat_ids_as_list()
                await tg.start(cfg_dict)
                await asyncio.sleep(1)  # 等 _run_loop 設定 running=True
                _save_runtime_state(app)
                logger.info("[startup] ✅ TG Bot 自動啟動（已設定監聽頻道）")
                await app.state.notify_bot.send(
                    "🤖 <b>TG 訊號機器人自動啟動</b>\n"
                    "伺服器啟動後已自動連線監聽分析師／量化訊號頻道。"
                )
            except Exception as _e:
                logger.error(f"[startup] TG Bot 自動啟動失敗：{_e}")
                try:
                    await app.state.notify_bot.send(
                        f"🚨 <b>TG 訊號機器人啟動失敗</b>\n<code>{_e}</code>\n請至後台手動啟動。"
                    )
                except Exception:
                    pass
        asyncio.create_task(_auto_start_tg_bot())


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if settings.admin_token and x_admin_token != settings.admin_token:
        raise HTTPException(status_code=401, detail="unauthorized")


def require_webhook(x_webhook_token: str | None = Header(default=None)) -> None:
    if settings.webhook_token and x_webhook_token != settings.webhook_token:
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "ok": True, "adapters": sorted(adapters.keys())}


@app.get("/strategy/config", response_model=StrategyConfig, dependencies=[Depends(require_admin)])
async def get_strategy_config() -> StrategyConfig:
    return app.state.strategy_config


@app.put("/strategy/config", response_model=StrategyConfig, dependencies=[Depends(require_admin)])
async def set_strategy_config(cfg: StrategyConfig) -> StrategyConfig:
    app.state.strategy_config = cfg
    _save_runtime_state(app)
    return app.state.strategy_config


@app.get("/analytics/today-winrate", dependencies=[Depends(require_admin)])
async def analytics_today_winrate() -> dict:
    """
    查詢當日（台灣時間）各帳號平倉記錄，
    並按訊號類型（爆擊/狙擊/分析師）分類統計勝率。
    """
    from backend.exchanges.gate_perp import GatePerpAdapter as _Adp  # noqa: PLC0415
    import time as _time

    accts = [a for a in app.state.accounts if a.enabled and a.has_credentials()]
    if not accts:
        return {"error": "無啟用帳號"}

    # 建立 symbol → signal_type 對照表（從 signal_log，取最新一筆）
    tg = getattr(app.state, "tg_bot", None)
    sig_map: dict[str, str] = {}  # symbol_upper → signal_type
    if tg:
        # 合併 runtime signal_logs + persisted，統一轉 dict
        all_entries: list[dict] = []
        for entry in list(tg.signal_logs):
            d = entry.to_dict() if hasattr(entry, "to_dict") else (entry if isinstance(entry, dict) else {})
            all_entries.append(d)
        for d in getattr(tg, "_persisted", []):
            all_entries.append(d if isinstance(d, dict) else {})
        # 依時間排序，讓最新的覆蓋舊的
        for d in sorted(all_entries, key=lambda x: str(x.get("ts", ""))):
            sym = str(d.get("symbol") or "").upper().replace("-", "_")
            stype = str(d.get("signal_type") or "分析師訊號")
            if sym and stype:
                sig_map[sym] = stype

    # 今日 UTC+8 開始時間戳
    from datetime import timezone, timedelta as _td  # noqa: PLC0415
    _tz8 = timezone(_td(hours=8))
    _now_tz8 = datetime.now(_tz8)
    _today_start = _now_tz8.replace(hour=0, minute=0, second=0, microsecond=0)
    _today_start_ts = _today_start.timestamp()

    SIG_TYPES = ["爆擊訊號", "持倉狙擊訊號", "分析師訊號"]

    account_results: list[dict] = []

    for acct in accts:
        _g = _Adp(api_key=acct.api_key, api_secret=acct.api_secret,
                  base_url=settings.gate_base_url, settle=settings.gate_futures_settle)
        try:
            closes = await _g.get_position_closes(limit=200)
        except Exception as e:
            account_results.append({"name": acct.name or f"帳號{acct.slot}", "error": str(e)})
            await _g.aclose()
            continue
        finally:
            await _g.aclose()

        # 分類桶
        buckets: dict[str, dict] = {
            t: {"wins": 0, "losses": 0, "pnl": 0.0} for t in SIG_TYPES
        }
        total_wins = total_losses = 0
        total_pnl = 0.0

        for c in closes:
            close_ts = float(c.get("time") or c.get("close_time") or 0)
            if close_ts < _today_start_ts:
                continue  # 跳過非今日

            pnl_val = None
            for k in ("realised_pnl", "pnl"):
                v = c.get(k)
                if v is not None and v != "":
                    try:
                        pnl_val = float(v)
                        break
                    except Exception:
                        pass
            if pnl_val is None:
                continue

            sym = str(c.get("contract") or "").upper().replace("-", "_")
            stype = sig_map.get(sym, "分析師訊號")

            total_pnl += pnl_val
            if pnl_val > 0:
                total_wins += 1
                buckets[stype]["wins"] += 1
            elif pnl_val < 0:
                total_losses += 1
                buckets[stype]["losses"] += 1
            buckets[stype]["pnl"] += pnl_val

        total = total_wins + total_losses
        per_type: list[dict] = []
        for stype in SIG_TYPES:
            b = buckets[stype]
            n = b["wins"] + b["losses"]
            per_type.append({
                "signal_type": stype,
                "wins": b["wins"],
                "losses": b["losses"],
                "total": n,
                "win_rate": round(b["wins"] / n * 100, 1) if n else None,
                "pnl": round(b["pnl"], 4),
            })

        account_results.append({
            "name": acct.name or f"帳號{acct.slot}",
            "total": total,
            "wins": total_wins,
            "losses": total_losses,
            "win_rate": round(total_wins / total * 100, 1) if total else None,
            "pnl": round(total_pnl, 4),
            "by_type": per_type,
        })

    # 合計（主帳號代表）
    return {
        "date": _now_tz8.strftime("%Y-%m-%d"),
        "accounts": account_results,
    }


@app.get("/analytics/performance", dependencies=[Depends(require_admin)])
async def analytics_performance(
    contract: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    """
    從 Gate 個人成交紀錄（my_trades）做簡易績效統計。

    注意：
    - MVP 先用 FIFO 將多空成交配對成 round-trip
    - 手續費若非 USDT 幣種，先不換算（避免缺匯率）
    """
    # testnet 限制已移除，支援主網成交紀錄

    try:
        trades = await gate.get_my_trades(contract=contract, limit=limit, offset=offset)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"取得 my_trades 失敗: {e}")

    # 依 create_time 升冪處理（FIFO 配對）
    def _t(ts) -> float:
        v = _to_float(ts)
        return v if v is not None else 0.0

    trades_sorted = sorted(trades, key=lambda t: _t(t.get("create_time") or t.get("time") or t.get("timestamp")))

    # lots: 每個 contract 各自 FIFO
    long_lots: dict[str, list[dict]] = {}
    short_lots: dict[str, list[dict]] = {}
    closed: list[dict] = []

    # face value（quanto_multiplier）用來把 contracts -> BTC qty，進而算 USDT PnL
    face_cache: dict[str, float] = {}

    async def _face(sym: str) -> float:
        if sym in face_cache:
            return face_cache[sym]
        f = await gate._get_contract_face_value(sym)  # type: ignore[attr-defined]
        face_cache[sym] = float(f)
        return float(f)

    for tr in trades_sorted:
        sym = str(tr.get("contract") or tr.get("symbol") or "").strip()
        if not sym:
            continue

        # Gate futures my_trades 常見欄位：size, price, fee, fee_currency, create_time, order_id, text, role
        size = _to_float(tr.get("size"))
        if size is None:
            size = _to_float(tr.get("settlement_size")) or _to_float(tr.get("close_size"))
        price = _to_float(tr.get("price"))
        if size is None or price is None:
            continue
        if size == 0:
            continue

        fee = _to_float(tr.get("fee")) or 0.0
        fee_ccy = str(tr.get("fee_currency") or "").upper()
        fee_usdt = fee if fee_ccy in ("USDT", "") else 0.0  # MVP：非 USDT 先忽略

        ts = tr.get("create_time") or tr.get("time") or tr.get("timestamp")

        if size > 0:
            # buy: 優先回補 short，再加 long
            remain = size
            lots = short_lots.setdefault(sym, [])
            while remain > 0 and lots:
                lot = lots[0]
                m = min(remain, float(lot["size"]))
                remain -= m
                lot["size"] = float(lot["size"]) - m

                f = await _face(sym)
                qty_btc = m * f
                # short 平倉：entry=sell, exit=buy
                pnl = (float(lot["price"]) - price) * qty_btc
                orig = float(lot.get("orig_size") or (float(lot.get("size", 0.0)) + m) or m)
                ratio = m / orig if orig > 0 else 1.0
                fee_part = float(lot.get("fee_usdt", 0.0)) * ratio
                closed.append(
                    {
                        "contract": sym,
                        "side": "SHORT",
                        "qty_contracts": m,
                        "qty_btc": qty_btc,
                        "entry_price": float(lot["price"]),
                        "exit_price": price,
                        "entry_time": lot.get("time"),
                        "exit_time": ts,
                        "gross_pnl_usdt": pnl,
                        "fee_usdt": fee_part + fee_usdt,
                    }
                )
                if float(lot["size"]) <= 1e-12:
                    lots.pop(0)

            if remain > 0:
                long_lots.setdefault(sym, []).append(
                    {"size": remain, "price": price, "time": ts, "fee_usdt": fee_usdt, "orig_size": remain}
                )

        else:
            # sell: 優先回補 long，再加 short
            remain = abs(size)
            lots = long_lots.setdefault(sym, [])
            while remain > 0 and lots:
                lot = lots[0]
                m = min(remain, float(lot["size"]))
                remain -= m
                lot["size"] = float(lot["size"]) - m

                f = await _face(sym)
                qty_btc = m * f
                # long 平倉：entry=buy, exit=sell
                pnl = (price - float(lot["price"])) * qty_btc
                orig = float(lot.get("orig_size") or (float(lot.get("size", 0.0)) + m) or m)
                ratio = m / orig if orig > 0 else 1.0
                fee_part = float(lot.get("fee_usdt", 0.0)) * ratio
                closed.append(
                    {
                        "contract": sym,
                        "side": "LONG",
                        "qty_contracts": m,
                        "qty_btc": qty_btc,
                        "entry_price": float(lot["price"]),
                        "exit_price": price,
                        "entry_time": lot.get("time"),
                        "exit_time": ts,
                        "gross_pnl_usdt": pnl,
                        "fee_usdt": fee_part + fee_usdt,
                    }
                )
                if float(lot["size"]) <= 1e-12:
                    lots.pop(0)

            if remain > 0:
                short_lots.setdefault(sym, []).append(
                    {"size": remain, "price": price, "time": ts, "fee_usdt": fee_usdt, "orig_size": remain}
                )

    # 彙總
    gross = sum(float(x.get("gross_pnl_usdt", 0.0)) for x in closed)
    fee_total = sum(float(x.get("fee_usdt", 0.0)) for x in closed)
    net = gross - fee_total
    wins = sum(1 for x in closed if (float(x.get("gross_pnl_usdt", 0.0)) - float(x.get("fee_usdt", 0.0))) > 0)
    n = len(closed)

    return {
        "contract": contract,
        "trades_source": {"endpoint": "GET /futures/{settle}/my_trades", "limit": limit, "offset": offset, "count": len(trades_sorted)},
        "round_trips": n,
        "win_rate": (wins / n) if n else None,
        "gross_pnl_usdt": gross,
        "fee_usdt": fee_total,
        "net_pnl_usdt": net,
        "avg_net_pnl_usdt": (net / n) if n else None,
        "closed_trades": closed[-200:],  # 避免一次回傳太大
        "open_lots": {
            "long": {k: [{"size": v2["size"], "price": v2["price"], "time": v2["time"]} for v2 in vs] for k, vs in long_lots.items() if vs},
            "short": {k: [{"size": v2["size"], "price": v2["price"], "time": v2["time"]} for v2 in vs] for k, vs in short_lots.items() if vs},
        },
        "notes": [
            "MVP：以成交 size/price 做 FIFO 配對。",
            "MVP：若 fee_currency 非 USDT 會先忽略換算。",
        ],
    }


@app.get("/bot/status", dependencies=[Depends(require_admin)])
async def bot_status() -> dict:
    bot: OrderbookBot = app.state.bot
    cfg: StrategyConfig = app.state.strategy_config
    return bot.state.snapshot(cfg)


@app.post("/bot/start", dependencies=[Depends(require_admin)])
async def bot_start(symbol: str = "BTC_USDT") -> dict:
    bot: OrderbookBot = app.state.bot
    await bot.start(symbol=symbol)
    cfg: StrategyConfig = app.state.strategy_config
    return bot.state.snapshot(cfg)


@app.post("/bot/stop", dependencies=[Depends(require_admin)])
async def bot_stop() -> dict:
    bot: OrderbookBot = app.state.bot
    await bot.stop()
    cfg: StrategyConfig = app.state.strategy_config
    return bot.state.snapshot(cfg)


@app.get("/volume/status", dependencies=[Depends(require_admin)])
async def volume_status() -> dict:
    vr: VolumeRunner = app.state.volume_runner
    snap = vr.snapshot()
    snap["config"] = {
        "enabled": vr.cfg.enabled,
        "dry_run": vr.cfg.dry_run,
        "allow_mainnet": vr.cfg.allow_mainnet,
        "margin_usdt": vr.cfg.margin_usdt,
        "leverage": vr.cfg.leverage,
        "spread_bps": vr.cfg.spread_bps,
        "account_slot": vr.cfg.account_slot,
    }
    return snap


@app.post("/volume/start", dependencies=[Depends(require_admin)])
async def volume_start() -> dict:
    vr: VolumeRunner = app.state.volume_runner
    await vr.start()
    return vr.snapshot()


@app.post("/volume/stop", dependencies=[Depends(require_admin)])
async def volume_stop() -> dict:
    vr: VolumeRunner = app.state.volume_runner
    await vr.stop()
    return vr.snapshot()


@app.post("/volume/tick", dependencies=[Depends(require_admin)])
async def volume_tick() -> dict:
    """手動單次 tick（cron 備援）。"""
    vr: VolumeRunner = app.state.volume_runner
    return await vr.tick_once()


@app.post("/volume/unpause", dependencies=[Depends(require_admin)])
async def volume_unpause() -> dict:
    vr: VolumeRunner = app.state.volume_runner
    vr.state.paused = False
    vr.state.pause_reason = ""
    vr.state.log("管理員解除暫停")
    vr.store.save(vr.state)
    return vr.snapshot()


@app.post("/orders", response_model=OrderResponse, dependencies=[Depends(require_admin)])
async def place_order(req: OrderRequest, dry_run: bool = Query(default=None)) -> OrderResponse:
    adapter = adapters.get(req.exchange)
    if not adapter:
        raise HTTPException(status_code=400, detail=f"unknown exchange: {req.exchange}")
    try:
        use_dry_run = settings.dry_run_default if dry_run is None else dry_run
        if use_dry_run:
            if req.exchange == "gate":
                raw = {"dry_run": True, "payload_preview": gate.build_order_preview(req)}
            else:
                raw = {"dry_run": True, "note": "payload preview not implemented for this exchange yet"}
        else:
            raw = await adapter.place_order(req)
        return OrderResponse(exchange=req.exchange, raw=raw)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/orders/{exchange}", dependencies=[Depends(require_admin)])
async def get_open_orders(exchange: str, symbol: str | None = None) -> dict:
    """查詢未成交限價委託單 + 未觸發條件委託單（SL）。"""
    adapter = adapters.get(exchange)
    if not adapter:
        raise HTTPException(400, f"unknown exchange: {exchange}")
    try:
        limit_orders = await gate.get_open_orders(symbol=symbol)
        price_orders = await gate.get_open_price_orders(symbol=symbol)
        return {"limit_orders": limit_orders, "price_orders": price_orders}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.delete("/price-orders/{exchange}/{order_id}", response_model=OrderResponse, dependencies=[Depends(require_admin)])
async def cancel_price_order(exchange: str, order_id: str) -> OrderResponse:
    """撤銷條件委託單（SL 觸發單）。"""
    try:
        raw = await gate.cancel_price_order(order_id=order_id)
        return OrderResponse(exchange=exchange, raw=raw)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.delete("/orders/{exchange}/{symbol}/{order_id}", response_model=OrderResponse, dependencies=[Depends(require_admin)])
async def cancel_order(exchange: str, symbol: str, order_id: str) -> OrderResponse:
    adapter = adapters.get(exchange)
    if not adapter:
        raise HTTPException(status_code=400, detail=f"unknown exchange: {exchange}")
    try:
        raw = await adapter.cancel_order(symbol=symbol, order_id=order_id)
        return OrderResponse(exchange=exchange, raw=raw)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/positions/{exchange}", response_model=PositionResponse, dependencies=[Depends(require_admin)])
async def positions(exchange: str, symbol: str | None = None) -> PositionResponse:
    adapter = adapters.get(exchange)
    if not adapter:
        raise HTTPException(status_code=400, detail=f"unknown exchange: {exchange}")
    try:
        raw = await adapter.get_positions(symbol=symbol)
        return PositionResponse(exchange=exchange, raw=raw)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/webhook/signal", response_model=OrderResponse, dependencies=[Depends(require_webhook)])
async def webhook_signal(signal: WebhookSignal) -> OrderResponse:
    """
    自動化訊號端點：
    - 驗證 header `X-Webhook-Token`
    - 將訊號轉成 OrderRequest
    - 依 dry-run 規則呼叫既有下單流程（預設沿用 settings.dry_run_default）
    """
    try:
        req = signal.to_order_request()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    adapter = adapters.get(req.exchange)
    if not adapter:
        raise HTTPException(status_code=400, detail=f"unknown exchange: {req.exchange}")

    use_dry_run = settings.dry_run_default if signal.dry_run is None else signal.dry_run

    # 豆腐策略：Gate 開多完整流程
    # - 支援 action=開多（UI/中文）
    # - 也支援 intent=OPEN + direction=LONG（避免某些環境 JSON/編碼造成中文亂碼）
    is_open_long = (req.intent == Intent.OPEN and req.direction == Direction.LONG)
    if req.exchange == "gate" and (signal.action == "開多" or is_open_long):
        # Testnet 防呆：避免誤下真倉
        if not use_dry_run and "testnet" not in settings.gate_base_url:
            raise HTTPException(status_code=400, detail="目前非 Testnet Base URL，為避免誤下真倉，請先切換到 Gate Testnet")

        cfg: StrategyConfig = app.state.strategy_config
        symbol = req.symbol
        unit_mode = cfg.order_unit_mode
        unit_value = float(cfg.order_unit_value)
        tp_pct = float(cfg.take_profit_pct)
        sl_pct = float(cfg.stop_loss_pct)

        print(
            f"[tofu] config: leverage={cfg.leverage}x tp={tp_pct:.3f}% sl={sl_pct:.3f}% unit={unit_mode} value={unit_value} trigger_mult={cfg.big_order_trigger_mult}"
        )

        # 先把輸入換算成實際 size(張)
        try:
            size_contracts = await gate.convert_to_size(
                symbol=symbol, unit_mode=unit_mode, unit_value=unit_value, leverage=int(cfg.leverage)
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"下單單位換算失敗: {e}")

        qty = str(size_contracts)

        print(f"[tofu] plan: symbol={symbol} size={qty} (from {unit_mode}={unit_value})")
        print("[tofu] step1: Maker 進場（LIMIT + Post-Only，掛買一）")
        print(f"[tofu] step2: 成交後依策略算 tp_price（含 maker fee_rate=0.0002）並掛 Post-Only 平倉單")
        print(f"[tofu] step3: （可選）止損 price_orders 觸發單（目前仍為市價平倉）")

        # dry-run：不打交易所（避免需要 API key / 權限），只回傳預覽
        if use_dry_run:
            entry_req = OrderRequest(
                exchange="gate",
                symbol=symbol,
                intent=Intent.OPEN,
                direction=Direction.LONG,
                order_type="LIMIT",  # type: ignore[arg-type]
                qty=qty,
                price=None,
                time_in_force="GTC",  # type: ignore[arg-type]
                reduce_only=False,
                client_order_id=req.client_order_id,
            )
            return OrderResponse(
                exchange=req.exchange,
                raw={
                    "dry_run": True,
                    "entry_preview": await gate.build_maker_limit_payload(entry_req),
                    "note": "dry-run 不查倉位/不下真單；此預覽為 Maker 刷量模式（LIMIT+POC，掛買一/賣一）。止盈/止損價格會在真下單取得成交價後計算",
                },
            )

        # 0) 防呆：若已有倉位（size > 0），不重複進場
        try:
            positions_raw = await gate.get_positions(symbol=symbol)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"查倉位失敗: {e}")

        existing_long = False
        try:
            for p in positions_raw if isinstance(positions_raw, list) else [positions_raw]:
                if str(p.get("contract", "")).upper() == str(symbol).upper():
                    s = p.get("size", 0)
                    try:
                        if float(s) > 0:
                            existing_long = True
                            break
                    except Exception:
                        if str(s) not in ("0", "", "None"):
                            # 無法解析時保守：視為已有倉位
                            existing_long = True
                            break
        except Exception:
            pass

        if existing_long:
            print(f"[tofu] skip: already have long position: {symbol}")
            return OrderResponse(exchange=req.exchange, raw={"skipped": True, "reason": "已有倉位，避免重複進場"})

        # 0.5) 套用槓桿（依 UI 設定）
        try:
            lev_resp = await gate.update_position_leverage(symbol=symbol, leverage=int(cfg.leverage))
            print(f"[tofu] leverage set: {cfg.leverage}x")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"設定槓桿失敗: {e}")

        # 1) Maker 進場：LIMIT + Post-Only（實際 payload 由 adapter 強制 tif=poc）
        entry_req = OrderRequest(
            exchange="gate",
            symbol=symbol,
            intent=Intent.OPEN,
            direction=Direction.LONG,
            order_type="LIMIT",  # type: ignore[arg-type]
            qty=qty,
            price=None,  # 交由 adapter 掛買一
            time_in_force="GTC",  # type: ignore[arg-type]
            reduce_only=False,
            client_order_id=req.client_order_id,
        )

        def _is_filled(o: dict) -> bool:
            s = str(o.get("status") or "").lower()
            fa = str(o.get("finish_as") or "").lower()
            # Gate futures: status finished + finish_as filled
            return (s == "finished" and fa == "filled") or (o.get("left") in (0, "0"))

        def _is_cancelled(o: dict) -> bool:
            s = str(o.get("status") or "").lower()
            fa = str(o.get("finish_as") or "").lower()
            return (s == "finished" and fa in ("cancelled", "canceled")) or fa in ("cancelled", "canceled")

        # Post-Only 可能因避免吃單而被交易所直接取消，所以這裡做「重掛」直到成交或超時
        entry_resp: dict | None = None
        order_id = ""
        max_attempts = 15
        for attempt in range(1, max_attempts + 1):
            try:
                entry_resp = await gate.place_order(entry_req)
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"進場下單失敗: {e}")

            order_id = str(entry_resp.get("id") or entry_resp.get("order_id") or "")
            if not order_id:
                raise HTTPException(status_code=400, detail=f"進場下單回傳缺少 id: {entry_resp}")

            # 快速判斷是否立刻成交/被取消
            if _is_filled(entry_resp):
                break
            if _is_cancelled(entry_resp):
                print(f"[tofu] entry post-only cancelled immediately (attempt {attempt}/{max_attempts}), re-place…")
                await asyncio.sleep(0.3)
                continue

            # 輪詢最多 ~6 秒，看是否成交；若仍未成交就撤單重掛在最新買一
            filled = False
            for _ in range(12):
                try:
                    od = await gate.get_order(symbol=symbol, order_id=order_id)
                    if _is_filled(od):
                        entry_resp = od
                        filled = True
                        break
                    if _is_cancelled(od):
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.5)

            if filled:
                break

            # 未成交：撤單重掛（避免長時間掛單卡住策略）
            try:
                await gate.cancel_order(symbol=symbol, order_id=order_id)
            except Exception:
                pass
            await asyncio.sleep(0.3)

        if not entry_resp or not _is_filled(entry_resp):
            raise HTTPException(status_code=400, detail=f"進場 Post-Only 限價單超時未成交（{max_attempts} 次嘗試），最後回應={entry_resp}")

        # 2) 取得成交價 fill_price（必要時再補一次查單）
        fill_price = entry_resp.get("fill_price")

        def _try_parse_price(x) -> float | None:
            try:
                v = float(x)
                return v if v > 0 else None
            except Exception:
                return None

        fp = _try_parse_price(fill_price)
        if fp is None:
            for _ in range(6):
                try:
                    od = await gate.get_order(symbol=symbol, order_id=order_id)
                    fp = _try_parse_price(od.get("fill_price"))
                    if fp is not None:
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.5)

        if fp is None:
            raise HTTPException(status_code=400, detail=f"無法取得成交價（fill_price），entry_resp={entry_resp}")

        # Maker 刷量模式：止盈需把雙邊 Maker 手續費算進去（預設 0.02%）
        fee_rate = 0.0002
        tp_raw = gate.calculate_volume_strategy(entry_price=float(fp), side="LONG", take_profit_pct=tp_pct, fee_rate=fee_rate)
        sl_raw = fp * (1 - sl_pct / 100.0)

        # 依合約 tick 對齊價格（Gate 不接受精度超出規格的價格）
        try:
            tick = await gate.get_price_round(symbol)
        except Exception:
            tick = 0.1
        tp_price_str = gate.snap_price(tp_raw, tick)
        sl_price_str = gate.snap_price(sl_raw, tick)

        print(f"[tofu] filled: entry={fp} tp={tp_price_str} sl={sl_price_str} tick={tick} fee_rate={fee_rate}")

        # 3) 止盈：LIMIT SELL（reduce_only + Post-Only）
        tp_req = OrderRequest(
            exchange="gate",
            symbol=symbol,
            intent=Intent.OPEN,  # reduce_only will ensure not open new
            direction="SHORT",  # type: ignore[arg-type]
            order_type="LIMIT",  # type: ignore[arg-type]
            qty=qty,
            price=tp_price_str,
            time_in_force="GTC",  # type: ignore[arg-type]
            reduce_only=True,
            client_order_id=(req.client_order_id + "-tp") if req.client_order_id else None,
        )

        # 4) 止損：STOP MARKET（price_orders），觸發價 <= sl
        # 對於 LONG，平倉賣出 size 應為負數
        try:
            q_int = int(float(qty))
        except Exception:
            q_int = 1

        sl_size = -abs(q_int)

        print(f"[tofu] place TP limit sell @ {tp_price_str} (raw={tp_raw:.4f} tick={tick})")
        print(f"[tofu] place SL stop market trigger @ {sl_price_str} (raw={sl_raw:.4f})")

        try:
            tp_resp = await gate.place_order(tp_req)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"止盈掛單失敗: {e}")

        try:
            sl_resp = await gate.create_price_triggered_order(
                symbol=symbol,
                size=sl_size,
                trigger_price=sl_price_str,
                rule=2,
                price_type=0,  # 0=last price（統一與 TG 路徑一致）
                expiration=86400 * 7,
                reduce_only=True,
            )
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"止損觸發單失敗: {e}")

        return OrderResponse(
            exchange=req.exchange,
            raw={
                "entry": entry_resp,
                "take_profit": tp_resp,
                "stop_loss": sl_resp,
                "calc": {"entry_price": fp, "tp": tp_price_str, "sl": sl_price_str, "tick": tick},
            },
        )

    # 預設：沿用原本單筆下單行為
    try:
        if use_dry_run:
            if req.exchange == "gate":
                raw = {"dry_run": True, "payload_preview": gate.build_order_preview(req)}
            else:
                raw = {"dry_run": True, "note": "payload preview not implemented for this exchange yet"}
        else:
            raw = await adapter.place_order(req)
        return OrderResponse(exchange=req.exchange, raw=raw)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/orders/patch-tpsl", dependencies=[Depends(require_admin)])
async def patch_tpsl(
    symbol: str = "BTC_USDT",
    entry_price: float = 0.0,
    direction: str = "",
) -> dict:
    """
    幫現有持倉補掛 TP/SL 單（適用於 TP/SL 之前失敗的情況）。
    - symbol: 支援 SOXLUSDT / SOXL_USDT 兩種格式（自動正規化）
    - entry_price: 開倉均價（手動填入，或從 Gate 倉位 API 自動取）
    - direction: 空=自動偵測，SHORT=平多倉，LONG=平空倉
    """
    cfg: StrategyConfig = app.state.strategy_config
    tp_pct = float(cfg.take_profit_pct)
    sl_pct = float(cfg.stop_loss_pct)

    # 正規化 symbol（支援 SOXLUSDT → SOXL_USDT）
    norm_sym = gate._normalize_symbol(symbol)

    # 取倉位資訊（get_positions 內部已做 normalize）
    try:
        positions_raw = await gate.get_positions(symbol=norm_sym)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"查倉位失敗: {e}")

    # 找出持倉的 size 與均價（同時支援 LONG>0 與 SHORT<0）
    pos_size = 0
    pos_entry = entry_price
    pos_side = ""
    pos_list = positions_raw if isinstance(positions_raw, list) else [positions_raw]
    for p in pos_list:
        # 比較時兩邊都要 normalize
        p_contract = gate._normalize_symbol(str(p.get("contract", "")))
        if p_contract.upper() == norm_sym.upper():
            try:
                sz = float(p.get("size", 0))
                if sz != 0:
                    pos_size = int(sz)
                    pos_side = "long" if pos_size > 0 else "short"
                    if pos_entry <= 0:
                        for k in ("entry_price", "avg_price", "value"):
                            ep = p.get(k)
                            if ep is not None:
                                try:
                                    pos_entry = float(ep)
                                    break
                                except (TypeError, ValueError):
                                    pass
                    break
            except Exception:
                pass

    if pos_size == 0:
        raise HTTPException(status_code=400, detail=f"找不到 {norm_sym} 的有效持倉（size=0）")
    if pos_entry <= 0:
        raise HTTPException(status_code=400, detail=f"無法取得開倉均價，請傳入 entry_price")

    # 自動偵測 direction
    if not direction:
        direction = "long" if pos_size > 0 else "short"
    is_long_pos = direction.lower() in ("long", "做多")
    # 若方向與持倉反向，視為反向加倉而非覆寫 TP/SL
    if (is_long_pos and pos_size < 0) or (not is_long_pos and pos_size > 0):
        raise HTTPException(status_code=400,
            detail=f"direction={direction} 與持倉方向不符（LONG size={pos_size}），請確認方向或留空自動偵測")

    # TP/SL 方向：持有多倉→平倉用空單（size<0），持有空倉→平倉用多單（size>0）
    close_size = -abs(pos_size)  # 與持倉反向

    try:
        tick = await gate.get_price_round(norm_sym)
    except Exception:
        tick = 0.1

    tp_price = pos_entry * (1 + tp_pct / 100.0) if is_long_pos else pos_entry * (1 - tp_pct / 100.0)
    sl_price = pos_entry * (1 - sl_pct / 100.0) if is_long_pos else pos_entry * (1 + sl_pct / 100.0)
    tp_price_str = gate.snap_price(tp_price, tick)
    sl_price_str = gate.snap_price(sl_price, tick)

    print(f"[patch-tpsl] {norm_sym} dir={direction} entry={pos_entry} size={pos_size} "
          f"close_size={close_size} tp={tp_price_str} sl={sl_price_str} tick={tick}")

    # 止盈：LIMIT 平倉單
    tp_req = OrderRequest(
        exchange="gate", symbol=norm_sym,
        intent=Intent.CLOSE, direction=Direction.LONG if is_long_pos else Direction.SHORT,
        order_type="LIMIT",
        qty=str(abs(pos_size)), price=tp_price_str,
        time_in_force="GTC",
        reduce_only=True,
    )
    try:
        tp_resp = await gate.place_order(tp_req)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"止盈單失敗: {e}")

    # 止損：STOP MARKET（市價觸發）
    try:
        sl_resp = await gate.create_price_triggered_order(
            symbol=norm_sym, size=close_size,
            trigger_price=sl_price_str, rule=2 if is_long_pos else 1,
            price_type=0, expiration=86400 * 7, reduce_only=True,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"止損單失敗: {e}")

    return {
        "ok": True,
        "direction": direction,
        "calc": {"entry": pos_entry, "size": pos_size, "close_size": close_size,
                 "tp": tp_price_str, "sl": sl_price_str, "tick": tick},
        "take_profit": tp_resp,
        "stop_loss": sl_resp,
    }


def _max_contracts_for_notional_cap(
    face_value: float, entry_price: float, cap_usdt: float, *, safety: float = 0.98
) -> int | None:
    """在名目上限內最多可下幾張；cap_usdt<=0 表示不啟用，回傳 None。"""
    if cap_usdt <= 0:
        return None
    denom = float(face_value) * float(entry_price)
    if denom <= 0:
        return None
    return max(0, int(cap_usdt * safety / denom))


def _collect_exception_text(exc: BaseException) -> str:
    """串起例外與 __cause__ 鏈，避免 Gate 回應只在內層而漏判。"""
    parts: list[str] = []
    cur: BaseException | None = exc
    for _ in range(8):
        if cur is None:
            break
        parts.append(str(cur))
        cur = cur.__cause__
    return "\n".join(parts)


def _gate_api_label_from_text(msg: str) -> str | None:
    m = re.search(r'"label"\s*:\s*"([^"]+)"', msg, re.IGNORECASE)
    return m.group(1) if m else None


def _is_exchange_margin_or_size_reject(haystack: str) -> bool:
    """判斷是否為可透過縮小張數重試的拒單（保證金／持倉／風險額度等）。"""
    u = haystack.upper()
    lbl = _gate_api_label_from_text(haystack)
    if lbl:
        lu = lbl.upper()
        # Gate 常見：RISK_LIMIT_CHECK、INSUFFICIENT_BALANCE…
        if lu.startswith(
            ("RISK", "INSUFF", "BALANCE", "MARGIN", "TOO_BIG", "TOO_MUCH", "POSITION", "EXCEED", "NOTIONAL")
        ):
            return True
        if any(x in lu for x in ("LIMIT_CHECK", "RISK_LIMIT", "MARGIN", "INSUFFICIENT", "EXCEED", "NOTIONAL")):
            return True
        # 明確不可靠「縮量」解決的參數類錯誤
        if any(
            x in lu
            for x in (
                "PRECISION",
                "INVALID_PRICE",
                "PRICE_TOO",
                "CONTRACT_NOT_FOUND",
                "INVALID_CURRENCY",
                "INVALID_ARGUMENT",
            )
        ):
            return False

    keys = (
        "INSUFF",
        "BALANCE",
        "MARGIN",
        "RISK_",
        "RISK_LIMIT",
        "RISK LIMIT",
        "POSITION",
        "EXCEED",
        "TOO_MUCH",
        "POC_",
        "LEVERAGE",
        "NOTIONAL",
        "INSUFFICIENT",
    )
    if any(k in u for k in keys):
        return True
    # 回應 JSON 裡有 label 且風險相關（避免字串被截斷時漏判 RISK_*）
    if '"LABEL"' in u and ("RISK" in u or "MARGIN" in u or "INSUFF" in u or "NOTIONAL" in u):
        return True
    # 下單 HTTP 400：多為風控／餘額／張數，預設允許縮量重試（非白名單 label 時）
    if "GATE API HTTP 400" in u and re.search(r"回應[:：]", haystack):
        m = re.search(r"回應[:：]\s*", haystack)
        tail = haystack[m.end() :] if m else haystack
        tu = tail.upper()
        if any(x in tu for x in ("RISK", "MARGIN", "BALANCE", "INSUFF", "NOTIONAL", "EXCEED", "SIZE", "LIMIT")):
            return True
    return False


async def _process_tg_signal(
    signal: TGSignalPayload,
    gate_adapter: "GatePerpAdapter | None" = None,
    account: GateAccountConfig | None = None,
) -> OrderResponse:
    """核心邏輯（無驗證），供進程內直呼與 HTTP webhook 共用。
    gate_adapter 若傳入則使用該帳號的 adapter，否則使用全域 gate。
    account 若傳入則套用該帳號的 max_notional_usdt 名目上限與縮量重試。
    """
    _g = gate_adapter or gate

    direction_str = signal.action.strip()
    if direction_str in ("做多", "LONG", "long"):
        direction = Direction.LONG
    elif direction_str in ("做空", "SHORT", "short"):
        direction = Direction.SHORT
    else:
        raise HTTPException(status_code=400, detail=f"未知方向: {signal.action}，請傳 做多/做空")

    # 多帳號模式：強制全倉。dry_run 只在訊號明確帶 true 時生效
    # （signal-tracker 不帶此欄位 → 實盤行為不變；避免測試打成真單）
    use_dry_run = bool(signal.dry_run)
    cross_margin = True
    leverage = 0  # cross margin

    use_market_entry = bool(getattr(signal, "market_entry", False))
    effective_entry = float(signal.entry_price)
    if use_market_entry:
        try:
            effective_entry = await _g._get_mark_price(signal.symbol)
            logger.info(f"[market-entry] {signal.symbol} 市價追單 mark={effective_entry}")
        except Exception as _mpe:
            logger.warning(f"[market-entry] 取標記價失敗，沿用 entry_price：{_mpe}")

    # ── 1. 計算固定風險倉位 ──────────────────────────────────────
    try:
        contracts, face_value = await _g.calculate_fixed_risk_size(
            symbol=signal.symbol,
            entry_price=effective_entry,
            sl_price=signal.sl_price,
            max_risk_usdt=signal.max_risk_usdt,
            allow_exceed_ratio=1.0,
        )
    except Exception as e:
        err_str = str(e)
        if "CONTRACT_NOT_FOUND" in err_str or "404" in err_str or "not found" in err_str.lower():
            return OrderResponse(
                exchange=signal.exchange,
                raw={
                    "skipped": True,
                    "reason": (
                        f"合約不存在於 Gate.io（符號解析後仍無法下單）：{signal.symbol}。"
                        f"請確認 Gate 有上架此永續合約。原始錯誤：{err_str[:200]}"
                    ),
                },
            )
        raise HTTPException(status_code=400, detail=f"倉位計算失敗: {e}")

    # ── 安全檢查：最小一張已超過 max_risk，跳過避免超額風險 ──
    _is_analyst_proc = (signal.signal_type or "").strip() == "分析師訊號"
    if contracts == 0:
        risk_1 = abs(effective_entry - signal.sl_price) * face_value
        if _is_analyst_proc:
            contracts = 1
            logger.warning(
                "[analyst-force] %s 1張 SL風險 %.2fU > 上限 %.1fU，分析師跟單仍下 1 張",
                signal.symbol, risk_1, signal.max_risk_usdt,
            )
        else:
            return OrderResponse(
                exchange=signal.exchange,
                raw={
                    "skipped": True,
                    "reason": (
                        f"合約面值過大，最小 1 張的停損風險 {risk_1:.2f} USDT "
                        f"已超過固定虧損上限 {signal.max_risk_usdt:.1f} USDT，"
                        f"跳過此交易以保護帳號風控"
                    ),
                    "risk_per_1_contract_usdt": round(risk_1, 4),
                    "max_risk_usdt": signal.max_risk_usdt,
                    "face_value": face_value,
                },
            )

    # ── 1.25 名目上限：避免固定風險推論張數過大，交易所拒單（持倉價值／保證金）──
    cap_usdt = float(account.max_notional_usdt) if account else 0.0
    max_by_cap = _max_contracts_for_notional_cap(face_value, effective_entry, cap_usdt)
    if max_by_cap is not None:
        if max_by_cap == 0:
            per_1 = float(face_value) * float(effective_entry)
            return OrderResponse(
                exchange=signal.exchange,
                raw={
                    "skipped": True,
                    "reason": (
                        f"單張名目約 {per_1:.0f} USDT 已超過帳號設定的名目上限 {cap_usdt:.0f} USDT，無法下單"
                    ),
                    "max_notional_usdt": cap_usdt,
                    "per_contract_notional_usdt": round(per_1, 2),
                },
            )
        if contracts > max_by_cap:
            logger.info(
                f"[notional-cap] {signal.symbol} 依固定風險張數={contracts} → 名目上限壓至 {max_by_cap} 張"
                f"（上限 {cap_usdt} USDT，面值×進場≈{float(face_value) * float(signal.entry_price):.2f}）"
            )
            contracts = max_by_cap

    price_diff = abs(effective_entry - signal.sl_price)
    notional_value_usdt = float(face_value) * float(contracts) * float(effective_entry)
    _est_sl_loss = price_diff * face_value * contracts
    if _est_sl_loss > signal.max_risk_usdt * 1.01:
        _safe_contracts = max(0, int(signal.max_risk_usdt / (price_diff * face_value)))
        if _safe_contracts == 0:
            return OrderResponse(
                exchange=signal.exchange,
                raw={
                    "skipped": True,
                    "reason": (
                        f"停損風險 {_est_sl_loss:.2f} USDT 超過上限 {signal.max_risk_usdt:.1f} USDT，"
                        f"無法在風控內下單"
                    ),
                    "estimated_max_loss_usdt": round(_est_sl_loss, 4),
                    "max_risk_usdt": signal.max_risk_usdt,
                },
            )
        logger.warning(
            "[risk-cap] %s 張數 %s→%s（SL風險 %.2f→上限 %.1fU）",
            signal.symbol, contracts, _safe_contracts, _est_sl_loss, signal.max_risk_usdt,
        )
        contracts = _safe_contracts
    calc_preview = {
        "symbol": signal.symbol,
        "direction": direction.value,
        "entry_price": effective_entry,
        "sl_price": signal.sl_price,
        "tp1_price": signal.tp1_price,
        "price_diff": round(price_diff, 8),
        "face_value": face_value,
        "contracts": contracts,
        "estimated_max_loss_usdt": round(price_diff * face_value * contracts, 4),
        "max_risk_usdt": signal.max_risk_usdt,
        "notional_value_usdt": round(notional_value_usdt, 4),
    }

    if use_dry_run:
        return OrderResponse(
            exchange=signal.exchange,
            raw={"dry_run": True, "calc": calc_preview},
        )

    # 流動性／滑價過濾已停用：收到訊號即走標準進場（符號解析與風控張數仍生效）

    # ── 2. 先確認無同向持倉（不可在有倉位時動槓桿設定，避免爆倉）───
    try:
        positions_raw = await _g.get_positions(symbol=signal.symbol)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"查倉位失敗: {e}")

    for p in (positions_raw if isinstance(positions_raw, list) else [positions_raw]):
        if str(p.get("contract", "")).upper() != _g._normalize_symbol(signal.symbol).upper():
            continue
        try:
            sz = float(p.get("size", 0))
        except Exception:
            sz = 0.0
        if direction == Direction.LONG and sz > 0:
            return OrderResponse(exchange=signal.exchange, raw={"skipped": True, "reason": "已有多倉，跳過"})
        if direction == Direction.SHORT and sz < 0:
            return OrderResponse(exchange=signal.exchange, raw={"skipped": True, "reason": "已有空倉，跳過"})

    # ── 3. 無持倉才設定全倉保證金模式（有倉位時禁止，防止重算爆倉）─
    # ⚠️ Gate 在有倉位時呼叫 update_position_leverage 會重新計算保證金，
    #    在波動時可能瞬間觸發強平。只有零倉時才設定。
    actual_leverage: str | int = "0(全倉)"
    try:
        await _g.update_position_leverage(
            symbol=signal.symbol,
            leverage=0,
            cross_margin=True,
        )
        logger.info(f"[margin] {signal.symbol} 設定為全倉(cross)，無倉位確認安全")
    except Exception as e:
        err_str = str(e)
        # Gate 在已是全倉時回傳 POSITION_IN_CROSS_MARGIN，屬正常；其他失敗記 warning
        if "CROSS" in err_str.upper() or "cross" in err_str:
            logger.debug(f"[margin] {signal.symbol} 已是全倉模式（Gate 正常回應）")
        else:
            logger.warning(f"[margin] {signal.symbol} 設定全倉失敗，繼續嘗試下單（請確認帳號保證金模式）：{e}")

    # ── 4. 進場（限價 GTC 或市價 IOC）──────────────────────────────
    entry_price_str = _g.snap_price(effective_entry, await _g.get_price_round(signal.symbol))
    current_qty = contracts
    entry_resp: dict | None = None
    last_entry_err: Exception | None = None
    cap_contracts = (
        _max_contracts_for_notional_cap(face_value, effective_entry, cap_usdt, safety=0.98)
        if cap_usdt > 0
        else None
    )

    for attempt in range(24):
        if current_qty < 1:
            break
        try:
            if use_market_entry:
                entry_req = OrderRequest(
                    exchange=signal.exchange,
                    symbol=signal.symbol,
                    intent=Intent.OPEN,
                    direction=direction,
                    order_type=OrderType.MARKET,
                    qty=str(current_qty),
                    price="0",
                    time_in_force=TimeInForce.IOC,
                    reduce_only=False,
                )
                entry_resp = await _g.place_market_order_raw(entry_req)
            else:
                entry_req = OrderRequest(
                    exchange=signal.exchange,
                    symbol=signal.symbol,
                    intent=Intent.OPEN,
                    direction=direction,
                    order_type=OrderType.LIMIT,
                    qty=str(current_qty),
                    price=entry_price_str,
                    time_in_force=TimeInForce.GTC,
                    reduce_only=False,
                )
                entry_resp = await _g.place_limit_order_raw(entry_req)
            if attempt > 0:
                logger.warning(
                    f"[entry-retry] {signal.symbol} 第 {attempt + 1} 次下單成功，張數={current_qty}"
                )
            contracts = current_qty
            notional_value_usdt = float(face_value) * float(contracts) * float(effective_entry)
            calc_preview["contracts"] = contracts
            calc_preview["notional_value_usdt"] = round(notional_value_usdt, 4)
            calc_preview["estimated_max_loss_usdt"] = round(price_diff * face_value * contracts, 4)
            calc_preview["entry_retry_attempts"] = attempt
            calc_preview["market_entry"] = use_market_entry
            break
        except Exception as e:
            last_entry_err = e
            hay = _collect_exception_text(e)
            if not _is_exchange_margin_or_size_reject(hay):
                _label = "市價進場" if use_market_entry else "限價進場"
                raise HTTPException(status_code=400, detail=f"{_label}失敗: {e}")
            next_qty = max(1, int(current_qty * (0.55 if current_qty > 2000 else 0.72)))
            if cap_contracts is not None:
                next_qty = min(next_qty, cap_contracts)
            if next_qty >= current_qty:
                next_qty = current_qty - 1
            logger.warning(
                f"[entry-retry] {signal.symbol} 拒單縮量 {current_qty} → {next_qty}，原因：{hay[:200]!r}"
            )
            current_qty = next_qty

    if entry_resp is None:
        _label = "市價進場" if use_market_entry else "限價進場"
        raise HTTPException(
            status_code=400,
            detail=f"{_label}失敗（已多次縮量重試）: {last_entry_err}",
        )

    order_id = str(entry_resp.get("id") or entry_resp.get("order_id") or "")

    def _try_float(x) -> float | None:
        try:
            v = float(x)
            return v if v > 0 else None
        except Exception:
            return None

    fp = _try_float(entry_resp.get("fill_price")) or effective_entry

    # ── 5. 對齊 tick ─────────────────────────────────────────────
    try:
        tick = await _g.get_price_round(signal.symbol)
    except Exception:
        tick = 0.1

    sl_price_str = _g.snap_price(signal.sl_price, tick)

    # ── 6. TP 條件單 ─────────────────────────────────────────────
    raw_tps = [
        (signal.tp1_price, signal.tp1_close_pct),
        (signal.tp2_price, signal.tp2_close_pct),
        (signal.tp3_price, signal.tp3_close_pct),
        (signal.tp4_price, signal.tp4_close_pct),
    ]
    active_tps = [(p, c) for p, c in raw_tps if p]
    tp_resps = []
    tp_placed = []
    allocated = 0
    tp_rule = 1 if direction == Direction.LONG else 2
    tp_size_sign = -1 if direction == Direction.LONG else 1

    for i, (tp_price, close_pct) in enumerate(active_tps):
        _is_last = (i == len(active_tps) - 1)
        if _is_last:
            qty_tp = contracts - allocated   # 不 max(1)，剩多少掛多少
            if qty_tp <= 0:
                print(f"[tp] TP{i+1}: 剩餘張數=0（倉位已被前面 TP 分配完），跳過")
                break
        else:
            qty_tp = max(1, int(contracts * close_pct / 100))
        allocated += qty_tp
        tp_price_str = _g.snap_price(tp_price, tick)
        try:
            resp = await _g.create_price_triggered_order(
                symbol=signal.symbol,
                size=tp_size_sign * qty_tp,
                trigger_price=tp_price_str,
                rule=tp_rule,
                price_type=1,
                expiration=86400 * 7,
                reduce_only=True,
            )
            tp_resps.append(resp)
            tp_placed.append({"price": tp_price_str, "qty": qty_tp, "close_pct": close_pct})
            print(f"[tp] TP{i+1}: {tp_price_str} x {qty_tp} 張")
        except Exception as e:
            tp_resps.append({"error": str(e), "price": tp_price_str, "qty": qty_tp})

    # SL 條件單方向（Gate rule：1=price≥trigger，2=price≤trigger）
    # LONG：SL 在進場下方，價格跌到 SL 觸發 → price≤trigger → rule=2
    # SHORT：SL 在進場上方，價格漲到 SL 觸發 → price≥trigger → rule=1
    # ⚠️ 全專案統一 `2 if LONG else 1`，勿再改動（曾誤改導致 SL 全失效）
    sl_rule = 2 if direction == Direction.LONG else 1
    sl_size = -abs(contracts) if direction == Direction.LONG else abs(contracts)
    try:
        sl_resp = await _g.create_price_triggered_order(
            symbol=signal.symbol,
            size=sl_size,
            trigger_price=sl_price_str,
            rule=sl_rule,
            price_type=0,   # 0=last price（最新成交價觸發，較快）
            expiration=86400 * 7,
            reduce_only=True,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"停損觸發單失敗: {e}")

    return OrderResponse(
        exchange=signal.exchange,
        raw={
            "entry": entry_resp,
            "take_profits": tp_resps,
            "stop_loss": sl_resp,
            "calc": {
                **calc_preview,
                "fill_price": fp,
                "entry_order_id": (entry_resp or {}).get("id") if isinstance(entry_resp, dict) else None,
                "tp_orders": tp_placed,
                "sl_price_placed": sl_price_str,
                "leverage": "0(全倉)",
                "actual_leverage": actual_leverage,
                "notional_value_usdt": round(face_value * contracts * float(fp or signal.entry_price), 4),
                "tick": tick,
                "tp_count": len(tp_placed),
            },
        },
    )


# ══════════════════════════════════════════════════════════════
# Gate API 設定（可從 UI 熱更新，無需重啟）
# ══════════════════════════════════════════════════════════════

@app.get("/gate/config", response_model=GateAPIConfig, dependencies=[Depends(require_admin)])
async def gate_get_config() -> GateAPIConfig:
    return GateAPIConfig(
        gate_base_url=settings.gate_base_url,
        gate_key=settings.gate_key,
        gate_secret=settings.gate_secret,
        gate_futures_settle=settings.gate_futures_settle,
    )


@app.put("/gate/config", response_model=GateAPIConfig, dependencies=[Depends(require_admin)])
async def gate_set_config(cfg: GateAPIConfig) -> GateAPIConfig:
    """
    熱更新 Gate API 金鑰與環境，無需重啟後端。
    - 更新 settings 全域物件
    - 重新初始化 GatePerpAdapter（換 base_url / settle）
    - 注意：僅影響本次執行期，重啟後仍從 .env 讀取
    """
    settings.gate_base_url = cfg.gate_base_url.strip().rstrip("/")
    settings.gate_key = cfg.gate_key.strip()
    settings.gate_secret = cfg.gate_secret.strip()
    settings.gate_futures_settle = cfg.gate_futures_settle.strip().lower()

    # 重新初始化 adapter（換 base_url / settle 時需要重建 httpx client）
    global gate
    try:
        await gate.aclose()
    except Exception:
        pass
    from backend.exchanges.gate_perp import GatePerpAdapter  # noqa: PLC0415
    gate = GatePerpAdapter()
    adapters["gate"] = gate
    _save_runtime_state(app)

    return cfg


@app.get("/gate/test", dependencies=[Depends(require_admin)])
async def gate_test_connection() -> dict:
    """測試 Gate API 金鑰是否有效（查詢帳戶餘額）。"""
    if not settings.gate_key or not settings.gate_secret:
        return {"ok": False, "error": "尚未設定 API Key / Secret"}
    try:
        settle = settings.gate_futures_settle
        path = f"/futures/{settle}/accounts"
        headers_signed = gate._signed_headers("GET", f"/api/v4{path}", "", "")
        import httpx as _httpx  # noqa: PLC0415
        async with _httpx.AsyncClient(base_url=settings.gate_base_url, timeout=10) as c:
            r = await c.get(path, headers=headers_signed)
        if r.status_code == 200:
            data = r.json()
            total = data.get("total") or data.get("available") or "—"
            return {"ok": True, "env": settings.gate_base_url, "balance_usdt": total}
        else:
            return {"ok": False, "status": r.status_code, "error": r.text[:300]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ══════════════════════════════════════════════════════════════
# TG 訊號機器人管理
# ══════════════════════════════════════════════════════════════

@app.get("/tg-bot/config", response_model=TGBotConfig, dependencies=[Depends(require_admin)])
async def tg_bot_get_config() -> TGBotConfig:
    return app.state.tg_bot_config


@app.put("/tg-bot/config", response_model=TGBotConfig, dependencies=[Depends(require_admin)])
async def tg_bot_set_config(cfg: TGBotConfig) -> TGBotConfig:
    app.state.tg_bot_config = cfg
    _save_runtime_state(app)
    return cfg


@app.get("/tg-bot/status", dependencies=[Depends(require_admin)])
async def tg_bot_status() -> dict:
    tg: TGBotManager = app.state.tg_bot
    return tg.snapshot()


@app.post("/tg-bot/start", dependencies=[Depends(require_admin)])
async def tg_bot_start() -> dict:
    tg: TGBotManager = app.state.tg_bot
    cfg: TGBotConfig = app.state.tg_bot_config
    cfg_dict = cfg.model_dump()
    cfg_dict["chat_ids"] = cfg.chat_ids_as_list()
    await tg.start(cfg_dict)
    await asyncio.sleep(0.5)  # 等 _run_loop 設定 running=True
    _save_runtime_state(app)  # 持久化「bot 已啟動」狀態
    return tg.snapshot()


@app.post("/tg-bot/stop", dependencies=[Depends(require_admin)])
async def tg_bot_stop() -> dict:
    tg: TGBotManager = app.state.tg_bot
    await tg.stop()
    _save_runtime_state(app)  # 持久化「bot 已停止」狀態
    return tg.snapshot()


# ══════════════════════════════════════════════════════════════
# 多帳號管理 API
# ══════════════════════════════════════════════════════════════

@app.get("/accounts", dependencies=[Depends(require_admin)])
async def list_accounts() -> list[dict]:
    """列出全部 10 個帳號設定（API Secret 不回傳，只回傳是否有填）。"""
    accts: list[GateAccountConfig] = app.state.accounts
    result = []
    for a in accts:
        d = a.model_dump()
        d["has_secret"] = bool(a.api_secret.strip())
        d["api_secret"] = ""  # 不回傳 secret
        result.append(d)
    return result


@app.put("/accounts/{slot}", dependencies=[Depends(require_admin)])
async def update_account(slot: int, body: dict) -> dict:
    """更新單一帳號設定。slot=1~10。Patch 語意：只更新傳入的欄位，其餘保留舊值。"""
    if not 1 <= slot <= 10:
        raise HTTPException(400, "slot 必須在 1-10 之間")
    accts: list[GateAccountConfig] = app.state.accounts
    old = accts[slot - 1]
    # 以舊值為底，只覆蓋有傳入的欄位
    merged = old.model_dump()
    for k, v in body.items():
        if k == "slot":
            continue  # slot 不允許修改
        if k == "api_secret" and not str(v or "").strip():
            continue  # 空 secret 保留舊值
        if k == "api_key" and not str(v or "").strip():
            continue  # 空 api_key 保留舊值
        if k == "name" and v is None:
            continue  # name 為 None 保留舊值
        merged[k] = v
    merged["slot"] = slot
    try:
        accts[slot - 1] = GateAccountConfig.model_validate(merged)
    except Exception as e:
        raise HTTPException(400, str(e))
    _save_runtime_state(app)
    result = accts[slot - 1].model_dump()
    result["api_secret"] = ""
    return result


@app.put("/accounts", dependencies=[Depends(require_admin)])
async def update_all_accounts(body: list[dict]) -> list[dict]:
    """批次更新全部帳號。"""
    for item in body:
        slot = int(item.get("slot", 0))
        if 1 <= slot <= 10:
            await update_account(slot, item)
    return await list_accounts()


async def _update_tpsl_only_for_account(
    acct: "GateAccountConfig",
    _g: "GatePerpAdapter",
    signal: "TGSignalPayload",
    pos_size: int,
) -> dict:
    """
    已知該帳號有同向持倉時，撤舊條件單並掛新 SL/TP。
    ─ 安全規則 ─
    1. 新 SL 不可比舊 SL 更寬鬆（若更寬則保留舊 SL，以免放大現有持倉的損失）。
    2. cancel 失敗 → 跳過 TP 掛單（防止重複），僅在新 SL 更緊時補掛新 SL。
    3. TP 掛單前過濾已被突破的價格（TP1 已觸的情況）。
    """
    direction_str = signal.action.strip()
    direction = Direction.LONG if direction_str in ("做多", "LONG", "long") else Direction.SHORT
    is_long = direction == Direction.LONG
    abs_size = abs(int(pos_size))

    # ── 步驟 1：先讀取目前條件單，找出現有 SL 價格 ──────────────
    existing_sl_price: float | None = None
    existing_orders_list: list = []
    sl_rule_expected = 2 if is_long else 1
    try:
        existing_orders_list = await _g.get_open_price_orders(symbol=signal.symbol) or []
        for o in existing_orders_list:
            if o.get("trigger", {}).get("rule") == sl_rule_expected:
                p = o.get("trigger", {}).get("price")
                if p:
                    existing_sl_price = float(p)
                    break
    except Exception:
        pass

    # ── 步驟 2：決定最終 SL（永不放大現有持倉的風險）────────────
    new_sl_price = float(signal.sl_price)
    if existing_sl_price is not None:
        if is_long:
            # 多單：SL 越高越緊，選較高的
            better_sl_price = max(existing_sl_price, new_sl_price)
            if better_sl_price > new_sl_price:
                logger.info(
                    f"[update_tpsl] {signal.symbol} LONG 保留較緊舊SL {existing_sl_price} "
                    f"（新訊號SL={new_sl_price} 更寬，忽略）"
                )
        else:
            # 空單：SL 越低越緊，選較低的
            better_sl_price = min(existing_sl_price, new_sl_price)
            if better_sl_price < new_sl_price:
                logger.info(
                    f"[update_tpsl] {signal.symbol} SHORT 保留較緊舊SL {existing_sl_price} "
                    f"（新訊號SL={new_sl_price} 更寬，忽略）"
                )
    else:
        better_sl_price = new_sl_price

    sl_used_new_signal = (better_sl_price == new_sl_price)  # 是否真的用了新訊號的 SL

    # ── 步驟 3：嘗試取消舊 SL/TP（強健版：批量→逐一）────────────
    cancel_ok = False
    try:
        await _g.cancel_all_price_orders(contract=signal.symbol)
        cancel_ok = True
    except Exception as ce:
        logger.warning(f"[update_tpsl] cancel_all_price_orders 失敗: {ce}，嘗試逐一取消")
        cancelled_any = False
        for o in existing_orders_list:
            oid = o.get("id")
            if oid:
                try:
                    _path = f"/futures/{_g.settle}/price_orders/{oid}"
                    _hdrs = _g._signed_headers("DELETE", f"/api/v4{_path}", "", "")
                    await _g._client.delete(_path, headers=_hdrs)
                    cancelled_any = True
                except Exception:
                    pass
        cancel_ok = cancelled_any

    if not cancel_ok:
        logger.error(
            f"[update_tpsl] {signal.symbol} 所有取消操作均失敗，舊 TP/SL 仍存在。"
            f"跳過 TP 掛單以防重複，僅在新 SL 更緊時補掛。"
        )

    try:
        tick = await _g.get_price_round(signal.symbol)
    except Exception:
        tick = 0.1

    # ── 步驟 4：取 mark price（用於跳過已突破的 TP）─────────────
    mark_price = 0.0
    try:
        _sym_norm = _g._normalize_symbol(signal.symbol)
        _mr = await _g._client.get(
            f"/futures/{_g.settle}/contracts/{_sym_norm}",
            headers=_g._signed_headers("GET", f"/api/v4/futures/{_g.settle}/contracts/{_sym_norm}", "", ""),
        )
        mark_price = float(_mr.json().get("mark_price", 0) or 0)
    except Exception:
        pass

    # ── 步驟 5：掛新 TP（cancel 失敗時跳過，防重複）──────────────
    tp_placed: list[str] = []
    if cancel_ok:
        tp_rule = 1 if is_long else 2
        tp_size_sign = -1 if is_long else 1
        raw_tps = [
            (signal.tp1_price, signal.tp1_close_pct),
            (signal.tp2_price, signal.tp2_close_pct),
            (signal.tp3_price, signal.tp3_close_pct),
            (signal.tp4_price, signal.tp4_close_pct),
        ]
        active_tps = []
        for tp_p, tp_c in raw_tps:
            if not tp_p:
                continue
            tp_val = float(tp_p)
            if mark_price > 0:
                if is_long and mark_price >= tp_val:
                    logger.info(f"[update_tpsl] LONG TP {tp_val} 已突破(mark={mark_price:.6f})，跳過")
                    continue
                if not is_long and mark_price <= tp_val:
                    logger.info(f"[update_tpsl] SHORT TP {tp_val} 已突破(mark={mark_price:.6f})，跳過")
                    continue
            active_tps.append((tp_val, float(tp_c)))

        allocated = 0
        for i, (tp_price, close_pct) in enumerate(active_tps):
            is_last_tp = (i == len(active_tps) - 1)
            if is_last_tp:
                # 最後一個 TP：只平剩餘張數（不強制 min=1，避免超過倉位導致 reduce_only 被拒）
                qty_tp = abs_size - allocated
                if qty_tp <= 0:
                    logger.info(f"[update_tpsl] TP{i+1} 剩餘張數={qty_tp}，倉位已被前面 TP 分配完，跳過")
                    break
            else:
                qty_tp = max(1, int(abs_size * close_pct / 100))
            allocated += qty_tp
            tp_str = _g.snap_price(tp_price, tick)
            try:
                await _g.create_price_triggered_order(
                    symbol=signal.symbol, size=tp_size_sign * qty_tp,
                    trigger_price=tp_str, rule=tp_rule, price_type=1,
                    expiration=86400 * 7, reduce_only=True,
                )
                tp_placed.append(f"TP{i+1}@{tp_str}x{qty_tp}")
                logger.info(f"[update_tpsl] 掛 TP{i+1}: {tp_str} x {qty_tp} 張")
            except Exception as e:
                tp_placed.append(f"TP{i+1}失敗:{e}")
                logger.warning(f"[update_tpsl] TP{i+1} 掛單失敗: {e}")
    else:
        tp_placed.append("⚠️ cancel失敗，跳過TP掛單防重複")
        logger.warning(f"[update_tpsl] {signal.symbol} cancel失敗，跳過TP掛單")

    # ── 步驟 6：掛 SL（使用 better_sl_price，cancel 失敗時只在新 SL 更緊時補掛）──
    sl_rule = 2 if is_long else 1
    sl_size = -abs_size if is_long else abs_size
    sl_str = _g.snap_price(better_sl_price, tick)
    sl_err: str | None = None

    # cancel 失敗時：若新 SL 沒更緊，就不補掛（舊 SL 還在，不需重複）
    should_place_sl = cancel_ok or sl_used_new_signal  # sl_used_new_signal 此時 = True 代表新 SL 更緊
    if should_place_sl:
        try:
            await _g.create_price_triggered_order(
                symbol=signal.symbol, size=sl_size,
                trigger_price=sl_str, rule=sl_rule, price_type=0,
                expiration=86400 * 7, reduce_only=True,
            )
            sl_label = "新訊號SL" if sl_used_new_signal else f"保留舊SL({existing_sl_price})"
            logger.info(f"[update_tpsl] 掛 SL: {sl_str} x {abs_size} 張（{sl_label}）")
        except Exception as e:
            sl_err = str(e)
            logger.error(f"[update_tpsl] SL 掛單失敗: {e}")
    else:
        logger.info(
            f"[update_tpsl] {signal.symbol} cancel失敗且新SL較寬，"
            f"保留既有SL({existing_sl_price})，不補掛"
        )

    return {
        "mode": "update_tpsl",
        "pos_size": int(pos_size),
        "sl_used": sl_str,
        "sl_was_tightened": not sl_used_new_signal,
        "new_sl": sl_str if not sl_err else f"FAIL:{sl_err}",
        "new_tps": tp_placed,
        "ok_sl": sl_err is None,
        "cancel_ok": cancel_ok,
    }


async def _query_account_state(_g: "GatePerpAdapter", symbol: str) -> tuple[int, list[dict]]:
    """
    查單帳號在某合約上的狀態：
    - pos_size：> 0 = 多倉、< 0 = 空倉、0 = 無倉
    - pending_orders：未成交限價單列表
    """
    pos_size = 0
    try:
        pos_raw = await _g.get_positions(symbol=symbol)
        target_norm = _g._normalize_symbol(symbol).upper()
        for p in (pos_raw if isinstance(pos_raw, list) else [pos_raw]):
            if _g._normalize_symbol(str(p.get("contract", ""))).upper() == target_norm:
                try:
                    pos_size = int(float(p.get("size", 0)))
                except Exception:
                    pos_size = 0
                break
    except Exception:
        pos_size = 0

    pending_orders: list[dict] = []
    try:
        pending_orders = await _g.get_open_orders(symbol=symbol) or []
    except Exception:
        pending_orders = []
    return pos_size, pending_orders


# ══════════════════════════════════════════════════════════════
# 合約黑名單 + 時段過濾 → 倉位縮小（2026-05-27 新增）
# ══════════════════════════════════════════════════════════════
# 來源：signal_tracker 30 天回測 crit_radar 256 筆，avg pnl_pct=-6.44%
# 設計原則：不阻擋下單，只縮小倉位（保留分析師訊號的判斷自由度）
# 只對量化訊號（爆擊/持倉狙擊）生效，分析師訊號不受影響
#
# 嚴重虧損合約（× 0.3）：n≥4 + 累計 pnl_pct ≤ -200%
_BLACKLIST_CONTRACTS: set[str] = {
    "RIVERUSDT", "VVVUSDT", "LITUSDT", "RENDERUSDT",
    "SNDKUSDT", "PENDLEUSDT", "INJUSDT", "HYPEUSDT",
}
# 觀察名單（× 0.5）：累計 pnl_pct ≤ -100% 但樣本較小
_WATCHLIST_CONTRACTS: set[str] = {
    "INTCUSDT", "TRUMPUSDT", "STRKUSDT", "TONUSDT",
    "APTUSDT", "ATOMUSDT", "MUUSDT",
}
# 最壞時段（× 0.3）：台北時間 avg pnl_pct ≤ -30%
_VERY_BAD_HOURS_TAIPEI: set[int] = {9, 10, 14, 15}
# 次壞時段（× 0.5）：avg pnl_pct ≤ -15%
_BAD_HOURS_TAIPEI: set[int] = {8, 11, 13, 17, 19, 21, 22, 23}
# 受過濾的訊號類型（量化訊號）
_QUANT_SIGNAL_TYPES: set[str] = {"爆擊訊號", "持倉狙擊訊號"}


def _norm_symbol(s: str) -> str:
    """規範化合約符號：去底線/連字號、大寫。
    RIVER_USDT → RIVERUSDT；river-usdt → RIVERUSDT
    （signal_tracker DB 內 symbol 是無底線格式）
    """
    return (s or "").replace("_", "").replace("-", "").upper()


def _calc_filter_multiplier(signal) -> tuple[float, dict]:
    """合約黑名單 + 時段過濾倉位調整。
    
    只對量化訊號生效。回傳 (multiplier, breakdown)。
    分析師訊號回傳 1.0，不縮倉。
    """
    from datetime import datetime, timezone, timedelta  # noqa: PLC0415

    sig_type = (getattr(signal, "signal_type", "") or "").strip()
    is_quant = sig_type in _QUANT_SIGNAL_TYPES
    breakdown: dict = {"signal_type": sig_type, "is_quant": is_quant}

    if not is_quant:
        breakdown["multiplier"] = 1.0
        return 1.0, breakdown

    sym_norm = _norm_symbol(getattr(signal, "symbol", ""))
    if sym_norm in _BLACKLIST_CONTRACTS:
        bl_m = 0.3
        bl_tag = "blacklist"
    elif sym_norm in _WATCHLIST_CONTRACTS:
        bl_m = 0.5
        bl_tag = "watchlist"
    else:
        bl_m = 1.0
        bl_tag = "ok"

    tw_hour = datetime.now(timezone(timedelta(hours=8))).hour
    if tw_hour in _VERY_BAD_HOURS_TAIPEI:
        sess_m = 0.3
        sess_tag = "very_bad"
    elif tw_hour in _BAD_HOURS_TAIPEI:
        sess_m = 0.5
        sess_tag = "bad"
    else:
        sess_m = 1.0
        sess_tag = "ok"

    total = bl_m * sess_m
    breakdown.update({
        "symbol_norm": sym_norm,
        "blacklist_mult": bl_m,
        "blacklist_tag": bl_tag,
        "hour_taipei": tw_hour,
        "session_mult": sess_m,
        "session_tag": sess_tag,
        "multiplier": total,
    })
    return total, breakdown


# ══════════════════════════════════════════════════════════════
# 同步分析師訊號到 signal_tracker DB（讓統一查詢與績效分析可行）
# ══════════════════════════════════════════════════════════════
async def _record_to_signal_tracker(signal) -> None:
    """將分析師訊號同步寫入 signal_tracker DB。
    量化訊號（爆擊/持倉狙擊）已由 jackbot tracker_hook 同步，這裡只處理分析師訊號避免重複。
    
    失敗不影響下單流程（容錯設計，wrapped in try）。
    """
    sig_type = (getattr(signal, "signal_type", "") or "").strip()
    if sig_type != "分析師訊號":
        return

    try:
        import asyncpg  # noqa: PLC0415
        import json as _json  # noqa: PLC0415
        import os as _os  # noqa: PLC0415

        dburl = _os.environ.get("DATABASE_URL") or _os.environ.get("POSTGRES_URL")
        if not dburl:
            logger.warning("[sync-st] DATABASE_URL 未設定，分析師訊號未同步")
            return

        side = "long" if signal.action.strip() in ("做多", "LONG", "long") else "short"
        symbol_norm = _norm_symbol(signal.symbol)
        payload = {
            "analyst": getattr(signal, "analyst_name", None),
            "order_no": getattr(signal, "order_no", None),
            "env_snapshot": getattr(signal, "env_snapshot", None),
            "signal_score": getattr(signal, "signal_score", None),
            "signal_grade": getattr(signal, "signal_grade", None),
            "source_chat_id": getattr(signal, "source_chat_id", None),
            "source_topic_id": getattr(signal, "source_topic_id", None),
        }
        conn = await asyncpg.connect(dburl)
        try:
            await conn.execute(
                """
                INSERT INTO signal_tracker
                    (signal_uuid, source, symbol, side, entry_price, sl_price,
                     tp1_price, tp2_price, tp3_price, tp4_price, leverage,
                     status, payload, tg_chat_id, created_at)
                VALUES
                    (gen_random_uuid()::text, $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'active', $11, $12, NOW())
                """,
                f"analyst_{side}",
                symbol_norm,
                side,
                float(signal.entry_price or 0) or None,
                float(signal.sl_price) if signal.sl_price else None,
                float(signal.tp1_price) if signal.tp1_price else None,
                float(signal.tp2_price) if signal.tp2_price else None,
                float(signal.tp3_price) if signal.tp3_price else None,
                float(signal.tp4_price) if signal.tp4_price else None,
                int(signal.leverage or 10),
                _json.dumps(payload, default=str),
                int(getattr(signal, "source_chat_id", None) or 0) or None,
            )
            logger.info(
                "[sync-st] 分析師訊號已同步 → signal_tracker: %s %s order=%s analyst=%s",
                symbol_norm, side, payload["order_no"], payload["analyst"],
            )
        finally:
            await conn.close()
    except Exception as e:
        logger.warning("[sync-st] 同步分析師訊號失敗（不影響下單）：%s", e)


# ══════════════════════════════════════════════════════════════
# 訊號品質→倉位調整（2026-05-28 新增，基於 30 天回測 199 筆）
# ══════════════════════════════════════════════════════════════
# 回測數據（backtest_p2b_results.json）：
#   counter_trend + aggressive: 68 筆 avg -41% total -2813%（最大虧損源）
#   counter_trend + fuel≥70:    avg -45%（高燃料分逆勢致命）
#   abs(RS) >= 1%:              avg -38%
#   quality 未分類:              avg -45%
# 分析師訊號不套用（保留分析師判斷自由度）
def _calc_quality_multiplier(signal) -> tuple[float, dict]:
    """根據訊號自身品質（quality/market_mode/fuel/RS）縮倉。
    
    回傳 (multiplier, breakdown)。
    """
    sig_type = (getattr(signal, "signal_type", "") or "").strip()
    if sig_type == "分析師訊號":
        return 1.0, {"applied": False, "reason": "analyst_passthrough"}
    
    quality = (getattr(signal, "quality", "") or "").strip().lower()
    market_mode = (getattr(signal, "market_mode", "") or "").strip().lower()
    fuel_score = getattr(signal, "fuel_score", None)
    rs = getattr(signal, "signal_rs", None)
    
    mult = 1.0
    tags = []
    
    # D: quality 未分類 → ×0.5
    if not quality or quality in ("?", "unknown", "none"):
        mult *= 0.5
        tags.append("unclassified")
    elif quality == "counter_trend":
        # A: counter_trend + aggressive → ×0.1（幾乎不下）
        if market_mode == "aggressive":
            mult *= 0.1
            tags.append("ct_aggressive")
        else:
            # B: counter_trend + fuel ≥ 70 → ×0.5（neutral 模式下高燃料分仍危險）
            try:
                if fuel_score is not None and float(fuel_score) >= 70:
                    mult *= 0.5
                    tags.append("ct_high_fuel")
            except (TypeError, ValueError):
                pass
    
    # C: abs(RS) ≥ 1% → ×0.7（極端 RS 訊號）
    if rs is not None:
        try:
            if abs(float(rs)) >= 1.0:
                mult *= 0.7
                tags.append("extreme_rs")
        except (TypeError, ValueError):
            pass
    
    return mult, {
        "applied": True, "multiplier": mult, "tags": tags,
        "quality": quality, "market_mode": market_mode,
        "fuel_score": fuel_score, "rs": rs,
    }


# ══════════════════════════════════════════════════════════════
# 評分→倉位動態調整（2026-05-27 新增）
# ══════════════════════════════════════════════════════════════
def _calc_score_multiplier(signal) -> float:
    """根據訊號評分/grade 計算倉位乘數，乘到 max_risk_usdt。

    優先順序：signal_score（數值）> signal_grade（等級）。
    Score 規則（保守，最高 1.5x，最低 0.5x）：
        score >= 90  → 1.50
        score >= 80  → 1.20
        score >= 70  → 1.00
        score >= 60  → 0.70
        score >  0   → 0.50
    Grade 規則（持倉狙擊 S/A/R 等級）：
        S → 1.20、A → 1.00、R/B → 0.70、C/D → 0.50
    無評分 → 1.00（維持原 risk）。
    """
    try:
        s = int(getattr(signal, "signal_score", None) or 0)
        if s >= 90:
            return 1.5
        if s >= 80:
            return 1.2
        if s >= 70:
            return 1.0
        if s >= 60:
            return 0.7
        if s > 0:
            return 0.5
    except (TypeError, ValueError):
        pass
    g = str(getattr(signal, "signal_grade", None) or "").upper().strip()
    if g == "S":
        return 1.2
    if g == "A":
        return 1.0
    if g in ("R", "B"):
        return 0.7
    if g in ("C", "D"):
        return 0.5
    if g:
        return 0.8
    return 1.0


async def _resolve_signal_symbol(signal: TGSignalPayload) -> TGSignalPayload:
    """千倍幣／髒符號 → Gate 合約；分析師顯示價 → Gate 實價。"""
    from backend.exchanges.gate_perp import GatePerpAdapter as _Adapter  # noqa: PLC0415

    _g = _Adapter(
        api_key="",
        api_secret="",
        base_url=settings.gate_base_url,
        settle=settings.gate_futures_settle,
    )
    try:
        contract, mult = await _g.resolve_symbol(signal.symbol)
    finally:
        await _g.aclose()

    if contract == signal.symbol and mult == 1.0:
        return signal

    inv = 1.0 / mult if mult != 1.0 else 1.0

    def _sc(v: float | None) -> float | None:
        return (v * inv) if v is not None else None

    updates: dict = {"symbol": contract}
    if mult != 1.0:
        updates.update({
            "entry_price": signal.entry_price * inv,
            "sl_price": signal.sl_price * inv,
            "tp1_price": _sc(signal.tp1_price),
            "tp2_price": _sc(signal.tp2_price),
            "tp3_price": _sc(signal.tp3_price),
            "tp4_price": _sc(signal.tp4_price),
        })
        logger.info(
            "[symbol-resolve] %s → %s price÷%g (entry %.8g→%.8g)",
            signal.symbol, contract, mult,
            signal.entry_price, updates["entry_price"],
        )
    else:
        logger.info("[symbol-resolve] %s → %s", signal.symbol, contract)
    return signal.model_copy(update=updates)


async def _dispatch_signal_to_accounts(signal: TGSignalPayload) -> dict:
    """
    將訊號分發到所有符合條件的帳號，**每個帳號獨立判斷**：
      - 該帳號已有同向持倉 → 走更新 SL/TP（不重複進場）
      - 該帳號有同向掛單但未成交 → 撤舊掛單，按新訊號重新進場 + 掛新 SL/TP
      - 該帳號無持倉、無掛單 → 走標準進場流程

    這樣可徹底避免「A 帳號搶先成交鎖定全域旗標 → B 帳號被誤判而不開倉」的問題。
    各帳號使用各自的 risk，強制全倉（cross_margin=True）。
    """
    from backend.exchanges.gate_perp import GatePerpAdapter as _Adapter  # noqa: PLC0415

    signal = await _resolve_signal_symbol(signal)

    accts: list[GateAccountConfig] = app.state.accounts
    active = [
        a for a in accts
        if a.enabled and a.has_credentials() and a.is_enabled_for(signal.signal_type)
        and a.accepts_strategy(getattr(signal, "strategy_name", None))
    ]

    if not active:
        return {"dispatched": 0, "results": [], "note": "無啟用的帳號符合此訊號類型"}

    _brk = await _risk_breaker_check(active)
    if _brk["blocked"]:
        logger.warning(f"[breaker] 擋下 {signal.symbol} 進場：{_brk['reason']}")
        return {"dispatched": 0, "results": [], "note": f"風控熔斷：{_brk['reason']}"}

    direction_str = signal.action.strip()
    is_long = direction_str in ("做多", "LONG", "long")

    # [2026-05-27] 分析師訊號同步寫入 signal_tracker（量化訊號由 jackbot 路徑寫入）
    # 一次性寫入：每個帳號 dispatch 是同筆訊號，只記一次。失敗不影響下單。
    # 用 await 確保完成（內部 try 包好不會 raise，DB INSERT ~10ms 不影響延遲）
    try:
        await _record_to_signal_tracker(signal)
    except Exception:
        pass  # 雙層保險，不阻斷下單

    async def _run_one(acct: GateAccountConfig) -> dict:
        _adapter = _Adapter(
            api_key=acct.api_key,
            api_secret=acct.api_secret,
            base_url=settings.gate_base_url,
            settle=settings.gate_futures_settle,
        )
        try:
            pos_size, pending_orders = await _query_account_state(_adapter, signal.symbol)

            same_dir_held = (pos_size > 0 and is_long) or (pos_size < 0 and not is_long)
            opposite_held = (pos_size > 0 and not is_long) or (pos_size < 0 and is_long)

            if same_dir_held:
                upd = await _update_tpsl_only_for_account(acct, _adapter, signal, pos_size)
                return {
                    "slot": acct.slot, "name": acct.name, "ok": True,
                    "raw": {"mode": "update_tpsl", **upd},
                }

            if opposite_held:
                # 允許雙向持倉（同時做多做空）：直接走開新倉流程
                logger.info(f"[dispatch] {acct.name} {signal.symbol} 已有反向倉位 {pos_size} 張，允許雙向持倉繼續開新倉")

            # ── 閘門 P1：同方向倉位數量上限（防過度同向曝險）──────────────────
            # 分析師訊號不受此限（用戶要求嚴格跟單）
            _is_analyst_gate = (signal.signal_type or "").strip() == "分析師訊號"
            if not _is_analyst_gate:
                _max_same_dir = int(os.environ.get("GATE_MAX_SAME_DIRECTION", "15"))
                try:
                    _all_pos = await _adapter.get_positions()
                    _all_pos_list = _all_pos if isinstance(_all_pos, list) else [_all_pos]
                    _same_dir_cnt = 0
                    for _p in _all_pos_list:
                        try:
                            _psz = int(float(_p.get("size", 0) or 0))
                        except Exception:
                            _psz = 0
                        if _psz == 0:
                            continue
                        if (is_long and _psz > 0) or (not is_long and _psz < 0):
                            _same_dir_cnt += 1
                    if _same_dir_cnt >= _max_same_dir:
                        msg = (
                            f"[閘門P1] {acct.name} {signal.symbol} {signal.action} "
                            f"被攔截：同方向已持 {_same_dir_cnt} 倉 ≥ 上限 {_max_same_dir}"
                        )
                        logger.warning(msg)
                        return {
                            "slot": acct.slot, "name": acct.name, "ok": True,
                            "raw": {"mode": "skipped_p1_same_direction",
                                    "skipped": True, "reason": msg,
                                    "same_dir_count": _same_dir_cnt, "limit": _max_same_dir},
                        }
                except Exception as e:
                    logger.warning(f"[閘門P1] {acct.name} 檢查失敗: {e}")

            # ── 閘門 P2：總 SL 風險占本金比例上限 ──────────────────────────
            # 分析師訊號不受此限
            if not _is_analyst_gate:
                _max_total_sl_pct = float(os.environ.get("GATE_MAX_TOTAL_SL_PCT", "0.30"))
                try:
                    # 取餘額
                    _bal_path = f"/futures/{settings.gate_futures_settle}/accounts"
                    _bal_hdrs = _adapter._signed_headers("GET", f"/api/v4{_bal_path}", "", "")
                    _bal_r = await _adapter._client.get(_bal_path, headers=_bal_hdrs)
                    _bal_data = _bal_r.json() if _bal_r.status_code == 200 else {}
                    _equity = float(_bal_data.get("total") or 0)
                    if _equity > 0:
                        # 統計目前所有持倉的「理論 SL 風險」加總
                        _cur_total_risk = 0.0
                        try:
                            _all_pos2 = await _adapter.get_positions()
                            _all_pos2_list = _all_pos2 if isinstance(_all_pos2, list) else [_all_pos2]
                            for _p in _all_pos2_list:
                                try:
                                    _psz = int(float(_p.get("size", 0) or 0))
                                except Exception:
                                    _psz = 0
                                if _psz == 0:
                                    continue
                                _pc = str(_p.get("contract", ""))
                                _pe = float(_p.get("entry_price") or 0)
                                if _pe <= 0:
                                    continue
                                _pis_long = _psz > 0
                                _psl_rule = 2 if _pis_long else 1
                                try:
                                    _ppo = await _adapter.get_open_price_orders(symbol=_pc)
                                except Exception:
                                    _ppo = []
                                _ppo_sl = [o for o in (_ppo or []) if o.get("trigger", {}).get("rule") == _psl_rule]
                                if not _ppo_sl:
                                    # 無 SL → 用 entry*8% 估算
                                    _est = abs(_pe * 0.08)
                                    try:
                                        _fv = await _adapter._get_contract_face_value(_pc)
                                        _cur_total_risk += _est * _fv * abs(_psz)
                                    except Exception:
                                        pass
                                    continue
                                _sl_px = float(_ppo_sl[0].get("trigger", {}).get("price", 0) or 0)
                                if _sl_px <= 0:
                                    continue
                                try:
                                    _fv = await _adapter._get_contract_face_value(_pc)
                                    _cur_total_risk += abs(_pe - _sl_px) * _fv * abs(_psz)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        # 新單預估風險（用該訊號類型的固定 risk）
                        _new_risk = float(acct.risk_for_type(signal.signal_type))
                        _projected = _cur_total_risk + _new_risk
                        _max_allowed = _equity * _max_total_sl_pct
                        if _projected > _max_allowed:
                            msg = (
                                f"[閘門P2] {acct.name} {signal.symbol} {signal.action} "
                                f"被攔截：總 SL 風險 {_cur_total_risk:.1f}U + 新單 {_new_risk:.1f}U "
                                f"= {_projected:.1f}U > 本金 {_equity:.1f}U × {_max_total_sl_pct*100:.0f}% "
                                f"= {_max_allowed:.1f}U"
                            )
                            logger.warning(msg)
                            return {
                                "slot": acct.slot, "name": acct.name, "ok": True,
                                "raw": {"mode": "skipped_p2_total_exposure",
                                        "skipped": True, "reason": msg,
                                        "current_risk": round(_cur_total_risk, 2),
                                        "new_risk": _new_risk,
                                        "equity": round(_equity, 2),
                                        "limit_pct": _max_total_sl_pct},
                            }
                except Exception as e:
                    logger.warning(f"[閘門P2] {acct.name} 檢查失敗: {e}")

            # 未持倉：先撤掉同方向所有未成交限價單（避免舊掛單佔用倉位額度 / 重複進場）
            try:
                pending_same_side = [
                    o for o in pending_orders
                    if (is_long and float(o.get("size", 0)) > 0) or (not is_long and float(o.get("size", 0)) < 0)
                ]
                if pending_same_side:
                    _side = "bid" if is_long else "ask"
                    try:
                        await _adapter.cancel_all_limit_orders(contract=signal.symbol, side=_side)
                    except Exception:
                        pass
                    # 同時撤舊條件單（避免之前的孤兒 SL/TP 殘留）
                    try:
                        await _adapter.cancel_all_price_orders(contract=signal.symbol)
                    except Exception:
                        pass
            except Exception:
                pass

            # 動態倉位：爆擊/狙擊走 OOS+品質濾網；分析師固定倉位（不縮放）
            _base_risk = float(acct.risk_for_type(signal.signal_type))
            _is_analyst_sig = (signal.signal_type or "").strip() == "分析師訊號"
            if _is_analyst_sig:
                _adj_risk = _base_risk
            else:
                _score_mult = _calc_score_multiplier(signal)
                _filter_mult, _filter_bd = _calc_filter_multiplier(signal)
                _quality_mult, _quality_bd = _calc_quality_multiplier(signal)
                _raw_total = _score_mult * _filter_mult * _quality_mult
                # bound [0.1, 1.0]：動態只縮不放大，硬上限 = 該類型 base risk
                _total_mult = max(0.1, min(1.0, _raw_total))
                _adj_risk = min(round(_base_risk * _total_mult, 2), _base_risk)
                if abs(_total_mult - 1.0) > 1e-6:
                    logger.info(
                        "[dispatch-mult] %s %s %s score×%.2f filter×%.2f quality×%.2f "
                        "raw=%.3f bounded=%.2f risk %.1f→%.2fU bl=%s sess=%s(hr%s) qtags=%s",
                        acct.name, signal.signal_type, signal.symbol,
                        _score_mult, _filter_mult, _quality_mult,
                        _raw_total, _total_mult, _base_risk, _adj_risk,
                        _filter_bd.get("blacklist_tag", "?"),
                        _filter_bd.get("session_tag", "?"),
                        _filter_bd.get("hour_taipei", "?"),
                        _quality_bd.get("tags", []),
                    )
            sig_copy = signal.model_copy(update={"max_risk_usdt": _adj_risk})
            resp = await _process_tg_signal(sig_copy, gate_adapter=_adapter, account=acct)
            return {"slot": acct.slot, "name": acct.name, "ok": True, "raw": resp.raw}

        except Exception as exc:
            return {"slot": acct.slot, "name": acct.name, "ok": False, "error": str(exc)}
        finally:
            await _adapter.aclose()

    results = list(await asyncio.gather(*[_run_one(a) for a in active], return_exceptions=False))
    return {
        "dispatched": len(active),
        "success": sum(1 for r in results if r.get("ok")),
        "results": results,
    }


# ══════════════════════════════════════════════════════════════
def _tpe_day_key() -> str:
    from datetime import timedelta, timezone  # noqa: PLC0415

    return datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")


def _risk_breaker_state() -> dict:
    st = getattr(app.state, "risk_breaker", None)
    if st is None:
        st = {
            "day": "",
            "day_start_equity": 0.0,
            "peak_equity": 0.0,
            "halted": False,
            "halted_reason": "",
            "halted_day": "",
        }
        app.state.risk_breaker = st
    return st


async def _risk_breaker_check(active: list[GateAccountConfig]) -> dict:
    """
    進場前的全域熔斷：日虧上限、淨值回撤、同時持倉數。

    以交易所即時淨值為準，不依賴本地 PnL 記帳——記帳漏一筆就是假安全。
    淨值查不到時擋單（fail-closed）：查不到就無法證明還沒觸及虧損上限。
    """
    if not RISK_BREAKER_ENABLED:
        return {"blocked": False, "reason": ""}

    from backend.exchanges.gate_perp import GatePerpAdapter as _Adapter  # noqa: PLC0415

    st = _risk_breaker_state()
    today = _tpe_day_key()

    total_equity = 0.0
    open_positions = 0
    for acct in active:
        _g = _Adapter(
            api_key=acct.api_key,
            api_secret=acct.api_secret,
            base_url=settings.gate_base_url,
            settle=settings.gate_futures_settle,
        )
        try:
            total_equity += await _g.get_equity()
            for p in (await _g.get_positions()) or []:
                try:
                    if int(float(p.get("size", 0) or 0)) != 0:
                        open_positions += 1
                except (TypeError, ValueError):
                    continue
        except Exception as e:
            logger.error(f"[breaker] slot{acct.slot} 淨值/持倉查詢失敗，擋單：{e}")
            return {"blocked": True, "reason": f"淨值查詢失敗，保守擋單：{e}"}
        finally:
            try:
                await _g.aclose()
            except Exception:
                pass

    # 跨日重置：新的一天重算當日基準，並解除「僅當日」的熔斷
    if st["day"] != today:
        st["day"] = today
        st["day_start_equity"] = total_equity
        if st["halted"] and st["halted_day"] == "daily":
            st["halted"] = False
            st["halted_reason"] = ""
            st["halted_day"] = ""

    st["peak_equity"] = max(float(st.get("peak_equity") or 0.0), total_equity)

    if st["halted"]:
        return {"blocked": True, "reason": st["halted_reason"]}

    peak = float(st["peak_equity"])
    if peak > 0 and total_equity <= peak * (1.0 - MAX_DRAWDOWN_PCT / 100.0):
        st["halted"] = True
        st["halted_day"] = "permanent"
        st["halted_reason"] = (
            f"淨值自高點 {peak:.2f}U 回撤至 {total_equity:.2f}U，"
            f"超過 {MAX_DRAWDOWN_PCT:.0f}% 上限 → 全面停止，需人工解除"
        )
        await _breaker_alert(st["halted_reason"])
        return {"blocked": True, "reason": st["halted_reason"]}

    day_start = float(st["day_start_equity"] or 0.0)
    if day_start > 0 and total_equity <= day_start - DAILY_LOSS_CAP_USDT:
        st["halted"] = True
        st["halted_day"] = "daily"
        st["halted_reason"] = (
            f"當日自 {day_start:.2f}U 虧至 {total_equity:.2f}U，"
            f"達日虧上限 {DAILY_LOSS_CAP_USDT:.0f}U → 今日停止進場"
        )
        await _breaker_alert(st["halted_reason"])
        return {"blocked": True, "reason": st["halted_reason"]}

    if open_positions >= MAX_CONCURRENT_POSITIONS:
        return {
            "blocked": True,
            "reason": f"同時持倉 {open_positions} 已達上限 {MAX_CONCURRENT_POSITIONS}",
        }

    return {"blocked": False, "reason": ""}


async def _breaker_alert(reason: str) -> None:
    logger.critical(f"[breaker] {reason}")
    try:
        nb = getattr(app.state, "notify_bot", None)
        if nb:
            await nb.send(f"⛔ <b>風控熔斷</b>\n{reason}")
    except Exception:
        pass


@app.get("/risk/breaker", dependencies=[Depends(require_admin)])
async def risk_breaker_status() -> dict:
    return {
        "enabled": RISK_BREAKER_ENABLED,
        "daily_loss_cap_usdt": DAILY_LOSS_CAP_USDT,
        "max_drawdown_pct": MAX_DRAWDOWN_PCT,
        "max_concurrent_positions": MAX_CONCURRENT_POSITIONS,
        "state": _risk_breaker_state(),
    }


@app.post("/risk/breaker/reset", dependencies=[Depends(require_admin)])
async def risk_breaker_reset() -> dict:
    """人工解除熔斷（含永久級）。解除後當日基準重新以下次查詢的淨值為準。"""
    st = _risk_breaker_state()
    st.update({"halted": False, "halted_reason": "", "halted_day": "", "day": ""})
    return {"ok": True, "state": st}


@app.post("/health/trade-checkup", dependencies=[Depends(require_admin)])
async def health_trade_checkup(
    send_tg: bool = True,
    do_rebuild: bool = True,
) -> dict:
    """
    每 4 小時級交易健診：SL/TP 覆蓋、條件單張數、固定虧損 R、名目上限、槓桿模式。
    缺單時只依本地訊號帳重掛（不猜 ±N% 價）。報告推到 notify_bot。
    """
    from backend.exchanges.gate_perp import GatePerpAdapter as _Adp  # noqa: PLC0415
    from backend.trade_checkup import build_latest_signal_map, run_trade_checkup  # noqa: PLC0415

    tg: TGBotManager = app.state.tg_bot
    latest = build_latest_signal_map(tg)
    nb = getattr(app.state, "notify_bot", None)

    async def _send(text: str) -> None:
        if nb:
            await nb.send(text)

    result = await run_trade_checkup(
        accounts=list(app.state.accounts),
        adapter_factory=_Adp,
        settings=settings,
        latest_signals=latest,
        breaker_snapshot={
            "enabled": True,
            "state": _risk_breaker_state(),
        },
        notify_send=_send if send_tg else None,
        send_tg=send_tg,
        do_rebuild=do_rebuild,
    )
    # 精簡對外回傳（避免超大）
    return {
        "ok": result.get("ok"),
        "overall": result.get("overall"),
        "checked_positions": result.get("checked_positions"),
        "ok_positions": result.get("ok_positions"),
        "issues": result.get("issues"),
        "actions": result.get("actions"),
        "tg_sent": result.get("tg_sent"),
        "ts": result.get("ts"),
        "accounts": [
            {
                "slot": a.get("slot"),
                "name": a.get("name"),
                "equity": a.get("equity"),
                "error": a.get("error"),
                "positions": a.get("positions"),
            }
            for a in (result.get("accounts") or [])
        ],
    }


# 緊急操作 API（一鍵批量平倉 / 批量撤單）
# ══════════════════════════════════════════════════════════════


async def _dispatch_update_tpsl_to_accounts(signal: TGSignalPayload) -> dict:
    """
    重複訊號／校準訊號處理：每個帳號獨立判斷：
      - 已有同向持倉 → 撤舊條件單，依新訊號掛 SL/TP
      - 無持倉但有掛單 → 撤舊掛單後重新進場（追單到最新訊號）
      - 完全無持倉、無掛單 → 走補開倉流程
    使用與一般訊號相同的 _dispatch_signal_to_accounts 路徑，確保行為一致。
    """
    direction_str = signal.action.strip()
    result = await _dispatch_signal_to_accounts(signal)

    updated = 0
    opened = 0
    for r in result.get("results", []):
        raw = r.get("raw") or {}
        if r.get("ok"):
            if raw.get("mode") == "update_tpsl":
                updated += 1
            elif not raw.get("skipped"):
                opened += 1

    return {
        "action": "update_tpsl",
        "symbol": signal.symbol,
        "direction": direction_str,
        "updated": updated,
        "opened": opened,
        "results": result.get("results", []),
        "dispatched": result.get("dispatched", 0),
    }


# ══════════════════════════════════════════════════════════════
# TP/SL 安全稽核（檢查所有持倉是否有條件單，沒有就補；補不上就平倉）
# ══════════════════════════════════════════════════════════════

@app.post("/positions/audit-tpsl", dependencies=[Depends(require_admin)])
async def audit_positions_tpsl(force_close: bool = True) -> dict:
    """
    對所有啟用帳號掃描持倉，檢查每個倉位是否有 SL 條件單保護。
    - 沒有 SL → 嘗試以最近訊號的 SL 點位補掛
    - 補不上（找不到匹配訊號或 API 失敗）且 force_close=True → 直接市價平倉
    """
    from backend.exchanges.gate_perp import GatePerpAdapter as _Adp  # noqa: PLC0415

    accts = [a for a in app.state.accounts if a.enabled and a.has_credentials()]
    if not accts:
        return {"ok": True, "checked": 0, "results": [], "note": "無啟用帳號"}

    # 取最新訊號 map：symbol+direction → 最新訊號 dict
    tg: TGBotManager = app.state.tg_bot
    latest_sig: dict[str, dict] = {}
    for entry in list(tg.signal_logs):
        d = entry.to_dict()
        gk = f"{d.get('symbol')}_{d.get('direction')}"
        prev = latest_sig.get(gk)
        if prev is None or str(d.get("ts", "")) > str(prev.get("ts", "")):
            latest_sig[gk] = d
    for d in tg._persisted:
        gk = f"{d.get('symbol')}_{d.get('direction')}"
        prev = latest_sig.get(gk)
        if prev is None or str(d.get("ts", "")) > str(prev.get("ts", "")):
            latest_sig[gk] = d

    async def _audit_one(acct) -> dict:
        _g = _Adp(api_key=acct.api_key, api_secret=acct.api_secret,
                  base_url=settings.gate_base_url, settle=settings.gate_futures_settle)
        # safety_threshold 在後面依持倉對應的訊號類型動態計算（見下方迴圈）
        try:
            pos_raw = await _g.get_positions()
            plist = pos_raw if isinstance(pos_raw, list) else [pos_raw]
            account_actions: list[dict] = []
            for p in plist:
                try:
                    sz = int(float(p.get("size", 0)))
                except Exception:
                    sz = 0
                if sz == 0:
                    continue
                contract = str(p.get("contract", ""))
                direction = "做多" if sz > 0 else "做空"
                is_long = sz > 0
                mark_price = float(p.get("mark_price", 0) or 0)
                upnl = float(p.get("unrealised_pnl", 0) or 0)

                # 檢查該合約的 SL / TP 各自是否存在
                try:
                    pos_orders = await _g.get_open_price_orders(symbol=contract)
                except Exception:
                    pos_orders = []

                sl_rule_expected = 2 if is_long else 1
                tp_rule_expected = 1 if is_long else 2
                sl_orders = [o for o in (pos_orders or []) if o.get("trigger", {}).get("rule") == sl_rule_expected]
                tp_orders = [o for o in (pos_orders or []) if o.get("trigger", {}).get("rule") == tp_rule_expected]
                has_sl = len(sl_orders) > 0
                has_tp = len(tp_orders) > 0

                # 提前取出 sig / abs_size（後續各分支均需使用）
                abs_size = abs(sz)
                gk = f"{contract}_{direction}"
                sig = latest_sig.get(gk)

                # ── 安全閥閾值：依該持倉對應的訊號類型動態決定，避免大額帳號誤用小風控 ──
                _sig_type = (sig.get("signal_type") or "分析師訊號") if sig else "分析師訊號"
                _acct_risk = acct.risk_for_type(_sig_type) if hasattr(acct, "risk_for_type") else getattr(acct, "risk_analyst", 10.0)
                safety_threshold = _acct_risk * 1.3

                # ── 倉位超限：SL 理論最大虧損 > 1.1× 固定風險 → 減倉至上限 ──
                if has_sl and sl_orders and abs_size > 0:
                    try:
                        sl_px_r = float(sl_orders[0].get("trigger", {}).get("price", 0))
                        entry_px_r = float(p.get("entry_price", 0) or 0)
                        if sl_px_r > 0 and entry_px_r > 0:
                            fv_r = await _g._get_contract_face_value(contract)
                            pd_r = abs(entry_px_r - sl_px_r)
                            if pd_r > 0 and fv_r > 0:
                                risk_at_sl = pd_r * fv_r * abs_size
                                if risk_at_sl > _acct_risk * 1.1:
                                    target_sz = max(1, int(_acct_risk / (pd_r * fv_r)))
                                    if abs_size > target_sz:
                                        cut = abs_size - target_sz
                                        _trim_dir = Direction.SHORT if is_long else Direction.LONG
                                        _trim_req = OrderRequest(
                                            exchange="gate", symbol=contract, intent=Intent.CLOSE,
                                            direction=_trim_dir, order_type=OrderType.MARKET,
                                            qty=str(cut), time_in_force=TimeInForce.IOC, reduce_only=True,
                                        )
                                        await _g.place_market_order_raw(_trim_req)
                                        account_actions.append({
                                            "contract": contract, "direction": direction, "size": sz,
                                            "status": (
                                                f"✂️ 倉位超限：SL風險{risk_at_sl:.1f}U > {_acct_risk:.1f}U，"
                                                f"減{cut}張（{abs_size}→{target_sz}）"
                                            ),
                                            "action": "risk_trimmed",
                                        })
                                        logger.warning(
                                            f"[audit-trim] {acct.name} {contract} 減{cut}張 "
                                            f"{abs_size}→{target_sz} SL風險{risk_at_sl:.2f}U > {_acct_risk:.1f}U"
                                        )
                                        continue
                    except Exception as e_trim:
                        logger.warning(f"[audit-trim] {acct.name} {contract} 減倉失敗: {e_trim}")

                # ── 安全閥：浮虧 > 1.3x 固定風險 → 無論有無 SL 都強制平倉 ──
                if upnl < -safety_threshold:
                    try:
                        await _g.cancel_all_price_orders(contract=contract)
                    except Exception:
                        pass
                    try:
                        await _g.close_position_market(contract=contract, position_size=sz)
                        account_actions.append({
                            "contract": contract, "direction": direction, "size": sz,
                            "status": f"🚨 安全閥：浮虧{upnl:+.2f}U > {safety_threshold:.1f}U → 強制平倉",
                            "action": "safety_closed",
                            "upnl": upnl,
                        })
                        logger.warning(f"[audit-safety] {acct.name} {contract}({_sig_type}) 浮虧{upnl:.2f}U 超過安全閥{safety_threshold:.1f}U(1.3×{_acct_risk}U) → 強制平倉")
                        # 推播安全閥通知
                        tg_inst = getattr(app.state, "tg_bot", None)
                        if tg_inst and getattr(tg_inst, "notify_callback", None):
                            try:
                                await tg_inst.notify_callback(
                                    "safety_closed",
                                    account_name=acct.name or f"帳號{acct.slot}",
                                    contract=contract, upnl=upnl, threshold=safety_threshold,
                                )
                            except Exception:
                                pass
                    except Exception as e:
                        account_actions.append({
                            "contract": contract, "direction": direction, "size": sz,
                            "status": f"🚨 安全閥觸發但平倉失敗：{e}",
                            "action": "safety_failed",
                        })
                    continue

                if has_sl and has_tp:
                    # ── 檢查 TP1 是否已觸發 → 移益保本 ──
                    # 判斷方式：訊號設定的 TP 數量 > 目前剩餘 TP 條件單數量
                    # （不用倉位合約數比較，避免多帳號合約數不同導致誤判）
                    moved_breakeven = False
                    if sig:
                        expected_tp_count = sum(1 for k in ("tp1_price", "tp2_price", "tp3_price", "tp4_price")
                                                if sig.get(k))
                        current_tp_count = len(tp_orders)
                        pos_entry_px = float(p.get("entry_price", 0) or 0)
                        # 單一 TP：expected=1，tp_orders=0 代表唯一 TP 已觸發，也啟用保本
                        # 多 TP：expected>=2 且現存 TP 數量減少，代表至少 TP1 已觸發
                        if expected_tp_count == 1:
                            tp1_was_hit = (current_tp_count == 0 and pos_entry_px > 0)
                        else:
                            tp1_was_hit = (expected_tp_count >= 2 and current_tp_count < expected_tp_count
                                           and pos_entry_px > 0)
                        if tp1_was_hit:
                            # 確認市價仍在進場價有利方向（否則 Gate 會拒絕觸發價設定）
                            _price_in_profit = (
                                (is_long and mark_price > pos_entry_px) or
                                (not is_long and mark_price < pos_entry_px)
                            )
                            if _price_in_profit:
                                current_sl_trig = float(sl_orders[0].get("trigger", {}).get("price", 0))
                                needs_move = (
                                    (is_long and 0 < current_sl_trig < pos_entry_px) or
                                    (not is_long and current_sl_trig > pos_entry_px > 0)
                                )
                                if needs_move:
                                    try:
                                        tick_be = await _g.get_price_round(contract)
                                    except Exception:
                                        tick_be = 0.1
                                    be_sl_str = _g.snap_price(pos_entry_px, tick_be)
                                    # LONG: rule=2（跌破觸發）；SHORT: rule=1（漲破觸發）
                                    sl_rule_be = 2 if is_long else 1
                                    sl_sign_be = -1 if is_long else 1
                                    oid_sl = sl_orders[0].get("id")
                                    if oid_sl:
                                        try:
                                            _path = f"/futures/{_g.settle}/price_orders/{oid_sl}"
                                            _hdrs = _g._signed_headers("DELETE", f"/api/v4{_path}", "", "")
                                            await _g._client.delete(_path, headers=_hdrs)
                                            await _g.create_price_triggered_order(
                                                symbol=contract, size=sl_sign_be * abs_size,
                                                trigger_price=be_sl_str, rule=sl_rule_be,
                                                price_type=0, expiration=86400 * 7, reduce_only=True,
                                            )
                                            account_actions.append({
                                                "contract": contract, "direction": direction, "size": sz,
                                                "status": f"✅ TP1已觸（TP單從{expected_tp_count}→{current_tp_count}），SL移至進場價 {be_sl_str}",
                                                "action": "sl_breakeven",
                                            })
                                            logger.info(f"[audit-breakeven] {acct.name} {contract} TP1已觸（TP單{expected_tp_count}→{current_tp_count}），SL移至進場價 {be_sl_str}")
                                            moved_breakeven = True
                                        except Exception as e_be:
                                            logger.warning(f"[audit-breakeven] {acct.name} {contract} 移益保本失敗: {e_be}")
                    if not moved_breakeven:
                        # ── 即將到期主動更新（剩餘效期 < 24h → 取消重掛，避免保護單自動失效）──
                        import time as _time  # noqa: PLC0415
                        _now_ts = _time.time()
                        _RENEW_WIN = 24 * 3600
                        _near_exp = []
                        for _o in (sl_orders + tp_orders):
                            _ct = float(_o.get("create_time", 0) or 0)
                            _ex = int(_o.get("trigger", {}).get("expiration", 0) or 0)
                            if _ex > 0 and _ct > 0 and (_ct + _ex - _now_ts) < _RENEW_WIN:
                                _near_exp.append(_o)

                        if _near_exp:
                            try:
                                _tick_r = await _g.get_price_round(contract)
                            except Exception:
                                _tick_r = 0.1
                            _renew_notes: list[str] = []
                            for _o in _near_exp:
                                _oid_r = _o.get("id")
                                _rule_r = _o.get("trigger", {}).get("rule")
                                _tp_r = _o.get("trigger", {}).get("price")
                                _pt_r = int(_o.get("trigger", {}).get("price_type", 0) or 0)
                                _sz_r = int(float(_o.get("order", {}).get("size", 0) or 0))
                                if not (_oid_r and _rule_r and _tp_r and _sz_r):
                                    continue
                                try:
                                    _dp = f"/futures/{_g.settle}/price_orders/{_oid_r}"
                                    _dh = _g._signed_headers("DELETE", f"/api/v4{_dp}", "", "")
                                    await _g._client.delete(_dp, headers=_dh)
                                except Exception:
                                    pass
                                try:
                                    await _g.create_price_triggered_order(
                                        symbol=contract, size=_sz_r,
                                        trigger_price=str(_tp_r), rule=int(_rule_r),
                                        price_type=_pt_r, expiration=86400 * 7, reduce_only=True,
                                    )
                                    _renew_notes.append(f"更新效期@{_tp_r}")
                                except Exception as _er:
                                    _renew_notes.append(f"效期更新失敗:{_er}")
                                    logger.warning(f"[audit-renew] {acct.name} {contract} 效期更新失敗: {_er}")
                            account_actions.append({
                                "contract": contract, "direction": direction, "size": sz,
                                "status": f"🔄 條件單即將到期→已更新 {', '.join(_renew_notes)}",
                                "action": "renewed",
                            })
                            logger.info(f"[audit-renew] {acct.name} {contract} 效期更新: {_renew_notes}")
                        else:
                            account_actions.append({
                                "contract": contract, "direction": direction, "size": sz,
                                "status": "✅ SL+TP 完整", "action": "none",
                            })
                    continue

                # ── 補掛缺失的 SL / TP ──
                try:
                    tick = await _g.get_price_round(contract)
                except Exception:
                    tick = 0.1
                sl_rule = 2 if is_long else 1
                tp_rule = 1 if is_long else 2
                sl_size_sign = -1 if is_long else 1
                tp_size_sign = -1 if is_long else 1
                patch_notes: list[str] = []

                # 補 SL
                if not has_sl:
                    sl_price = float(sig["sl_price"]) if sig and sig.get("sl_price") else None
                    if sl_price is None:
                        # 應急 SL：entry ± 8%
                        entry_px = float(p.get("entry_price", 0) or 0)
                        sl_price = entry_px * (0.92 if is_long else 1.08) if entry_px else None
                    if sl_price:
                        # ── 保護：SL 已被突破（現價已越過 SL 方向）→ 調整至現價 ±2% ──
                        # Gate 規則：多單 SL 觸發價必須 < 現價；空單 SL 觸發價必須 > 現價
                        sl_breached = (is_long and sl_price >= mark_price) or \
                                      (not is_long and sl_price <= mark_price)
                        if sl_breached:
                            old_sl_price = sl_price
                            sl_price = mark_price * (0.98 if is_long else 1.02)
                            logger.warning(
                                f"[audit-sl] {acct.name} {contract} 備用SL {old_sl_price:.4f} "
                                f"已被現價 {mark_price:.4f} 突破，調整至 {sl_price:.4f}（±2%緩衝）"
                            )
                            patch_notes.append(f"⚠️SL已突破，升至現價{'−2%' if is_long else '+2%'}={sl_price:.4f}")
                        try:
                            sl_str = _g.snap_price(sl_price, tick)
                            await _g.create_price_triggered_order(
                                symbol=contract, size=sl_size_sign * abs_size,
                                trigger_price=sl_str, rule=sl_rule, price_type=0,
                                expiration=86400 * 7, reduce_only=True,
                            )
                            patch_notes.append(f"補SL@{sl_str}")
                        except Exception as e:
                            patch_notes.append(f"SL補掛失敗:{e}")
                    else:
                        patch_notes.append("無法確定SL價格")

                # 補 TP（只補不存在的，跳過已被突破的價格）
                if not has_tp and sig:
                    tp_prices_pcts = [
                        (sig.get("tp1_price"), float(sig.get("tp1_close_pct") or 55)),
                        (sig.get("tp2_price"), float(sig.get("tp2_close_pct") or 25)),
                        (sig.get("tp3_price"), float(sig.get("tp3_close_pct") or 20)),
                        (sig.get("tp4_price"), float(sig.get("tp4_close_pct") or 10)),
                    ]
                    valid_tps = []
                    for tp_raw, pct in tp_prices_pcts:
                        if not tp_raw:
                            continue
                        tp_val = float(tp_raw)
                        # 跳過已被突破的 TP
                        if is_long and mark_price >= tp_val:
                            continue
                        if not is_long and mark_price <= tp_val:
                            continue
                        valid_tps.append((tp_val, pct))

                    if valid_tps:
                        alloc = 0
                        for i, (tp_p, pct) in enumerate(valid_tps):
                            _is_last_a = (i == len(valid_tps) - 1)
                            if _is_last_a:
                                qty = abs_size - alloc
                                if qty <= 0:
                                    break
                            else:
                                qty = max(1, int(abs_size * pct / 100))
                            alloc += qty
                            tp_str = _g.snap_price(tp_p, tick)
                            try:
                                await _g.create_price_triggered_order(
                                    symbol=contract, size=tp_size_sign * qty,
                                    trigger_price=tp_str, rule=tp_rule,
                                    price_type=1, expiration=86400 * 7, reduce_only=True,
                                )
                                patch_notes.append(f"補TP@{tp_str}x{qty}")
                            except Exception:
                                pass

                if patch_notes:
                    account_actions.append({
                        "contract": contract, "direction": direction, "size": sz,
                        "status": f"⚠️→✅ {', '.join(patch_notes)}",
                        "action": "patched",
                    })
                    continue

                # 完全無法補掛且沒 SL → 強制平倉
                if not has_sl and force_close:
                    try:
                        await _g.close_position_market(contract=contract, position_size=sz)
                        account_actions.append({
                            "contract": contract, "direction": direction, "size": sz,
                            "status": "🚨 無SL且補掛失敗 → 強制平倉",
                            "action": "force_closed",
                        })
                    except Exception as ex:
                        account_actions.append({
                            "contract": contract, "direction": direction, "size": sz,
                            "status": f"❌ 平倉也失敗：{ex}", "action": "error",
                        })
                elif not has_sl:
                    account_actions.append({
                        "contract": contract, "direction": direction, "size": sz,
                        "status": "🚨 無SL且補掛失敗（force_close=False，未動）",
                        "action": "naked",
                    })

            # ── 孤立條件單清除：持倉已歸零但條件單殘留 ──────────────────
            # 取所有目前有條件單的合約
            try:
                all_price_orders = await _g.get_open_price_orders() or []
            except Exception:
                all_price_orders = []

            # 建立有持倉合約集合（size != 0）
            active_contracts: set[str] = set()
            for p in plist:
                try:
                    _sz = int(float(p.get("size", 0)))
                except Exception:
                    _sz = 0
                if _sz != 0:
                    active_contracts.add(str(p.get("contract", "")))

            # 找出條件單所屬合約已無持倉的（孤立條件單）
            orphan_contracts: set[str] = set()
            for o in all_price_orders:
                _c = str(o.get("contract") or o.get("initial", {}).get("contract") or "")
                if _c and _c not in active_contracts:
                    orphan_contracts.add(_c)

            for orphan_c in orphan_contracts:
                try:
                    await _g.cancel_all_price_orders(contract=orphan_c)
                    account_actions.append({
                        "contract": orphan_c, "direction": "-", "size": 0,
                        "status": "🧹 孤立條件單已清除（持倉已歸零）",
                        "action": "orphan_cleaned",
                    })
                    logger.info(f"[audit-orphan] {acct.name} {orphan_c} 持倉已平，清除殘留條件單")
                except Exception as oe:
                    logger.warning(f"[audit-orphan] {acct.name} {orphan_c} 清除孤立條件單失敗：{oe}")

            return {
                "slot": acct.slot, "name": acct.name or f"帳號{acct.slot}",
                "actions": account_actions,
            }
        except Exception as exc:
            return {"slot": acct.slot, "name": acct.name or f"帳號{acct.slot}",
                    "error": str(exc), "actions": []}
        finally:
            await _g.aclose()

    results = list(await asyncio.gather(*[_audit_one(a) for a in accts], return_exceptions=False))

    summary = {"checked": 0, "patched": 0, "breakeven": 0, "renewed": 0, "force_closed": 0, "safety_closed": 0, "ok": 0, "errors": 0}
    for r in results:
        for a in r.get("actions", []):
            summary["checked"] += 1
            act = a.get("action", "")
            if act == "patched":
                summary["patched"] += 1
            elif act == "sl_breakeven":
                summary["breakeven"] += 1
            elif act == "renewed":
                summary["renewed"] += 1
            elif act == "force_closed":
                summary["force_closed"] += 1
            elif act == "safety_closed":
                summary["safety_closed"] += 1
                summary["force_closed"] += 1
            elif act == "none":
                summary["ok"] += 1
            elif act in ("error", "patch_failed", "safety_failed"):
                summary["errors"] += 1

    # 推播稽核結果（若有任何補掛、強平、移益保本或效期更新）
    if summary["patched"] > 0 or summary["breakeven"] > 0 or summary["renewed"] > 0 or summary["force_closed"] > 0 or summary["errors"] > 0:
        nb = getattr(app.state, "notify_bot", None)
        if nb:
            detail_lines = []
            for r in results:
                aname = r.get("name", "?")
                for a in r.get("actions", []):
                    act = a.get("action", "")
                    if act in ("patched", "sl_breakeven", "renewed", "force_closed", "safety_closed", "error", "patch_failed", "safety_failed", "naked"):
                        detail_lines.append(f"  [{aname}] {a['contract']} {a['direction']}: {a['status']}")
            detail_text = "\n".join(detail_lines[:20])  # 最多20條
            asyncio.create_task(nb.send(
                f"🛡️ <b>SL/TP 1小時稽核完成</b>\n"
                f"檢查 {summary['checked']} 倉  正常 {summary['ok']}\n"
                f"補掛 {summary['patched']}  移益保本 {summary['breakeven']}  效期更新 {summary['renewed']}  安全閥強平 {summary['safety_closed']}  強平 {summary['force_closed'] - summary['safety_closed']}\n"
                f"錯誤 {summary['errors']}\n"
                + (f"\n<b>明細：</b>\n{detail_text}" if detail_lines else "")
            ))

    return {"ok": True, "summary": summary, "results": results}


# 緊急操作 API（一鍵批量平倉 / 批量撤單）
# ══════════════════════════════════════════════════════════════

@app.post("/emergency/close-all", dependencies=[Depends(require_admin)])
async def emergency_close_all(side: str = "all", contract: str = "") -> dict:
    """
    緊急平倉所有帳號的持倉（市價 reduce_only IOC）。
    side: 'long' / 'short' / 'all'
    contract: 指定合約（如 BCH_USDT / BCHUSDT）→ 只平該合約；留空 = 全部合約。
    ⚠️ contract 為空時會平掉符合 side 的『所有』持倉，呼叫端務必明確。
    """
    from backend.exchanges.gate_perp import GatePerpAdapter as _Adp  # noqa: PLC0415

    accts = [a for a in app.state.accounts if a.enabled and a.has_credentials()]
    if not accts:
        raise HTTPException(400, "無啟用帳號")

    # 正規化目標合約（支援 BCHUSDT / BCH_USDT 兩種輸入）
    target_contract = ""
    if contract:
        _tmp = _Adp(base_url=settings.gate_base_url, settle=settings.gate_futures_settle)
        target_contract = _tmp._normalize_symbol(contract).upper()

    async def _close_one(acct) -> dict:
        _g = _Adp(api_key=acct.api_key, api_secret=acct.api_secret,
                  base_url=settings.gate_base_url, settle=settings.gate_futures_settle)
        acct_results = []
        try:
            positions_raw = await _g.get_positions()
            positions = positions_raw if isinstance(positions_raw, list) else [positions_raw]
            for p in positions:
                try:
                    sz = int(float(p.get("size", 0)))
                except Exception:
                    continue
                if sz == 0:
                    continue
                is_long = sz > 0
                if side == "long" and not is_long:
                    continue
                if side == "short" and is_long:
                    continue
                contract = p.get("contract", "")
                if not contract:
                    continue
                # 指定合約過濾：只平目標合約（避免誤平整個帳戶）
                if target_contract and _g._normalize_symbol(str(contract)).upper() != target_contract:
                    continue
                # 平倉前先撤該合約所有 SL/TP 條件單 + 限價掛單，避免殘留孤立訂單
                try:
                    await _g.cancel_all_price_orders(contract=contract)
                except Exception:
                    pass
                try:
                    # 撤銷該合約未成交的限價掛單（避免平倉後重新建倉）
                    open_orders = await _g.get_open_orders(symbol=contract) or []
                    for oo in open_orders:
                        oid = oo.get("id")
                        if oid:
                            try:
                                await _g.cancel_order(symbol=contract, order_id=str(oid))
                            except Exception:
                                pass
                except Exception:
                    pass
                try:
                    resp = await _g.close_position_market(contract=contract, position_size=sz)
                    acct_results.append({"contract": contract, "size": sz, "result": "ok", "resp": resp})
                except Exception as e:
                    acct_results.append({"contract": contract, "size": sz, "result": "error", "error": str(e)})
        except Exception as e:
            acct_results.append({"result": "error", "error": f"查倉位失敗: {e}"})
        finally:
            await _g.aclose()
        return {"slot": acct.slot, "name": acct.name or f"帳號{acct.slot}", "details": acct_results}

    all_results = list(await asyncio.gather(*[_close_one(a) for a in accts]))
    total_ok = sum(1 for r in all_results for d in r.get("details", []) if d.get("result") == "ok")
    total_err = sum(1 for r in all_results for d in r.get("details", []) if d.get("result") == "error")
    return {"ok": True, "side": side, "accounts": len(accts),
            "closed": total_ok, "errors": total_err, "results": all_results}


@app.post("/emergency/cancel-limit-orders", dependencies=[Depends(require_admin)])
async def emergency_cancel_limit_orders(side: str = "all") -> dict:
    """
    批量撤銷所有帳號的未成交限價單。
    side: 'long'(bid) / 'short'(ask) / 'all'
    """
    from backend.exchanges.gate_perp import GatePerpAdapter as _Adp  # noqa: PLC0415

    accts = [a for a in app.state.accounts if a.enabled and a.has_credentials()]
    if not accts:
        raise HTTPException(400, "無啟用帳號")

    async def _cancel_one(acct) -> dict:
        _g = _Adp(api_key=acct.api_key, api_secret=acct.api_secret,
                  base_url=settings.gate_base_url, settle=settings.gate_futures_settle)
        try:
            if side == "long":
                result = await _g.cancel_all_limit_orders(side="bid")
            elif side == "short":
                result = await _g.cancel_all_limit_orders(side="ask")
            else:
                result = await _g.cancel_all_limit_orders()
            return {"slot": acct.slot, "name": acct.name or f"帳號{acct.slot}", "ok": True, "result": result}
        except Exception as e:
            return {"slot": acct.slot, "name": acct.name or f"帳號{acct.slot}", "ok": False, "error": str(e)}
        finally:
            await _g.aclose()

    all_results = list(await asyncio.gather(*[_cancel_one(a) for a in accts]))
    return {"ok": True, "side": side, "accounts": len(accts), "results": all_results}


@app.post("/emergency/cancel-price-orders", dependencies=[Depends(require_admin)])
async def emergency_cancel_price_orders(contract: str | None = None) -> dict:
    """批量撤銷所有帳號的條件單（SL / TP price_orders）。指定 contract 則只撤該合約。"""
    from backend.exchanges.gate_perp import GatePerpAdapter as _Adp  # noqa: PLC0415

    accts = [a for a in app.state.accounts if a.enabled and a.has_credentials()]
    if not accts:
        raise HTTPException(400, "無啟用帳號")

    async def _cancel_price_one(acct) -> dict:
        _g = _Adp(api_key=acct.api_key, api_secret=acct.api_secret,
                  base_url=settings.gate_base_url, settle=settings.gate_futures_settle)
        try:
            result = await _g.cancel_all_price_orders(contract=contract if contract else None)
            return {"slot": acct.slot, "name": acct.name or f"帳號{acct.slot}", "ok": True, "result": result}
        except Exception as e:
            return {"slot": acct.slot, "name": acct.name or f"帳號{acct.slot}", "ok": False, "error": str(e)}
        finally:
            await _g.aclose()

    all_results = list(await asyncio.gather(*[_cancel_price_one(a) for a in accts]))
    return {"ok": True, "contract": contract or "all", "accounts": len(accts), "results": all_results}


async def _make_adapter_for_slot(slot: int) -> "GatePerpAdapter":
    """根據帳號 slot 建立獨立 adapter（使用該帳號的 API 金鑰）。"""
    from backend.exchanges.gate_perp import GatePerpAdapter as _Adapter  # noqa: PLC0415
    accts: list[GateAccountConfig] = app.state.accounts
    if not 1 <= slot <= 10:
        raise HTTPException(400, "slot 需在 1-10 之間")
    acct = accts[slot - 1]
    if not acct.has_credentials():
        raise HTTPException(400, f"帳號 {slot} 尚未填入 API Key / Secret")
    return _Adapter(
        api_key=acct.api_key,
        api_secret=acct.api_secret,
        base_url=settings.gate_base_url,
        settle=settings.gate_futures_settle,
    )


@app.get("/accounts/{slot}/positions", dependencies=[Depends(require_admin)])
async def account_positions(slot: int, symbol: str | None = None) -> dict:
    """查詢指定帳號的倉位（PositionResponse 相容格式）。"""
    adapter = await _make_adapter_for_slot(slot)
    try:
        raw = await adapter.get_positions(symbol=symbol)
        return {"exchange": "gate", "raw": raw}
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        await adapter.aclose()
        settings.gate_key = (app.state.accounts[0].api_key if app.state.accounts[0].has_credentials() else settings.gate_key)


@app.get("/accounts/{slot}/orders", dependencies=[Depends(require_admin)])
async def account_orders(slot: int, symbol: str | None = None) -> dict:
    """查詢指定帳號的掛單。"""
    adapter = await _make_adapter_for_slot(slot)
    try:
        limit_orders = await adapter.get_open_orders(symbol=symbol)
        price_orders = await adapter.get_open_price_orders(symbol=symbol)
        return {"limit_orders": limit_orders, "price_orders": price_orders}
    except Exception as e:
        raise HTTPException(400, str(e))
    finally:
        await adapter.aclose()


@app.post("/positions/gate/{contract}/close-market", dependencies=[Depends(require_admin)])
async def close_position_market(contract: str, qty: int | None = None) -> dict:
    """
    市價平倉指定合約。qty 不傳則全倉平；傳入正整數則只平該張數（自動判斷方向）。
    """
    try:
        positions_raw = await gate.get_positions(symbol=contract)
    except Exception as e:
        raise HTTPException(400, f"查倉位失敗: {e}")

    pos_size = 0
    positions = positions_raw if isinstance(positions_raw, list) else [positions_raw]
    for p in positions:
        if gate._normalize_symbol(str(p.get("contract", ""))).upper() == gate._normalize_symbol(contract).upper():
            try:
                pos_size = int(float(p.get("size", 0)))
            except Exception:
                pass
            break

    if pos_size == 0:
        raise HTTPException(400, f"找不到 {contract} 的有效持倉")

    close_size = pos_size if qty is None else (abs(qty) * (1 if pos_size > 0 else -1))
    try:
        resp = await gate.close_position_market(contract=contract, position_size=close_size)
        return {"ok": True, "contract": contract, "closed_size": close_size, "resp": resp}
    except Exception as e:
        raise HTTPException(400, f"平倉失敗: {e}")


@app.post("/positions/gate/{contract}/adjust", dependencies=[Depends(require_admin)])
async def adjust_position(contract: str, qty: int, direction: str, dry_run: bool = True) -> dict:
    """
    加減碼。
    - direction: 'long' 或 'short'
    - qty: 正整數，加碼為正 qty，減碼請用 /close-market
    """
    from backend.core.models import Direction, Intent, OrderRequest, OrderType, TimeInForce  # noqa: PLC0415
    dir_enum = Direction.LONG if direction.lower() in ("long", "做多") else Direction.SHORT
    req = OrderRequest(
        exchange="gate",
        symbol=contract,
        intent=Intent.OPEN,
        direction=dir_enum,
        order_type=OrderType.LIMIT,
        qty=str(abs(qty)),
        time_in_force=TimeInForce.GTC,
        reduce_only=False,
    )
    if dry_run:
        preview = gate.build_order_preview(req)
        return {"ok": True, "dry_run": True, "preview": preview}
    try:
        resp = await gate.place_limit_order_raw(req)
        return {"ok": True, "dry_run": False, "resp": resp}
    except Exception as e:
        raise HTTPException(400, f"加碼失敗: {e}")


@app.post("/tg-bot/calibrate", dependencies=[Depends(require_admin)])
async def tg_bot_calibrate() -> dict:
    """
    校準訊號記錄（並行版）：
    1. 以同合約+方向的最新訊號 SL/TP 為基準
    2. 對每個帳號：查倉位 + 查條件單（全部並行，秒完成）
    3. 若有倉位但 SL/TP 與最新訊號不符 → 自動撤舊掛新
    4. 無倉位 → 標記為已平倉
    """
    from backend.exchanges.gate_perp import GatePerpAdapter as _Adp  # noqa: PLC0415
    from backend.tg_bot_manager import _save_persisted_logs  # noqa: PLC0415

    tg: TGBotManager = app.state.tg_bot
    accts_enabled = [a for a in app.state.accounts if a.enabled and a.has_credentials()]

    # ── 蒐集活躍訊號，按 symbol+direction 分組，取最新一筆 ────────
    all_logs: list[dict] = []
    seen: set[str] = set()
    for entry in list(tg.signal_logs):
        d = entry.to_dict()
        key = f"{d.get('ts')}_{d.get('symbol')}_{d.get('direction')}"
        if key not in seen:
            seen.add(key)
            all_logs.append(d)
    for d in tg._persisted:
        key = f"{d.get('ts')}_{d.get('symbol')}_{d.get('direction')}"
        if key not in seen:
            seen.add(key)
            all_logs.append(d)

    # 按 symbol+direction 分組，取最新 ts 的訊號作為基準
    latest_map: dict[str, dict] = {}
    for d in all_logs:
        sym = str(d.get("symbol", ""))
        direction = str(d.get("direction", ""))
        if not sym or not direction:
            continue
        gk = f"{sym}_{direction}"
        existing = latest_map.get(gk)
        if existing is None or str(d.get("ts", "")) > str(existing.get("ts", "")):
            latest_map[gk] = d

    # 只校準狀態是「活躍」的最新訊號
    active_latest = [v for v in latest_map.values()
                     if v.get("status") in ("掛單中", "已成交", "pending", "sent")]

    if not active_latest:
        return {"checked": 0, "report": [], "note": "無活躍訊號需要校準"}

    # ── 針對每個帳號的單一查詢任務（並行）────────────────────────
    async def _check_one(log: dict, acct: "GateAccountConfig") -> dict:  # type: ignore
        """
        智能補掛版：不再暴力全撤全掛。
        有 SL+TP → 不動；缺 SL 或缺 TP → 僅補掛缺失部分。
        TP 補掛時自動跳過已被突破的價格（TP1 已觸發的情況）。
        """
        sym = log["symbol"]
        direction = log["direction"]
        new_sl = log.get("sl_price")
        new_tp1 = log.get("tp1_price")
        new_tp2 = log.get("tp2_price")
        new_tp3 = log.get("tp3_price")
        new_tp4 = log.get("tp4_price")

        _g = _Adp(api_key=acct.api_key, api_secret=acct.api_secret,
                  base_url=settings.gate_base_url, settle=settings.gate_futures_settle)
        try:
            # 並行查倉位 + 條件單
            pos_task = _g.get_positions(symbol=sym)
            po_task = _g.get_open_price_orders(symbol=sym)
            pos_raw, price_orders = await asyncio.gather(pos_task, po_task, return_exceptions=True)

            if isinstance(pos_raw, Exception):
                return {"slot": acct.slot, "name": acct.name, "status": f"⚠️ 查倉位失敗：{pos_raw}"}
            if isinstance(price_orders, Exception):
                price_orders = []

            pos_size = 0
            for p in (pos_raw if isinstance(pos_raw, list) else [pos_raw]):
                if _g._normalize_symbol(str(p.get("contract", ""))).upper() == _g._normalize_symbol(sym).upper():
                    try:
                        sz = int(float(p.get("size", 0)))
                        if direction in ("做多", "LONG") and sz > 0:
                            pos_size = sz
                        elif direction in ("做空", "SHORT") and sz < 0:
                            pos_size = sz
                    except Exception:
                        pass
                    break

            po_count = len([o for o in (price_orders or []) if o])

            if pos_size == 0:
                return {"slot": acct.slot, "name": acct.name or f"帳號{acct.slot}",
                        "pos_size": 0, "price_orders": po_count,
                        "status": "❌ 無持倉（已平倉/SL）", "action": "marked_closed"}

            is_long = pos_size > 0
            abs_size = abs(pos_size)
            sl_rule_exp = 2 if is_long else 1
            tp_rule_exp = 1 if is_long else 2
            sl_orders_c = [o for o in (price_orders or []) if o.get("trigger", {}).get("rule") == sl_rule_exp]
            tp_orders_c = [o for o in (price_orders or []) if o.get("trigger", {}).get("rule") == tp_rule_exp]
            has_sl = len(sl_orders_c) > 0
            has_tp = len(tp_orders_c) > 0

            # 有完整保護 → 不操作
            if has_sl and has_tp:
                return {"slot": acct.slot, "name": acct.name or f"帳號{acct.slot}",
                        "pos_size": pos_size, "price_orders": po_count,
                        "status": f"✅ 有倉位（{pos_size} 張），{po_count} 筆條件單完整，無需更新",
                        "action": "no_change"}

            # 缺 SL 或缺 TP → 智能補掛
            if not new_sl and not new_tp1:
                return {"slot": acct.slot, "name": acct.name or f"帳號{acct.slot}",
                        "pos_size": pos_size, "price_orders": po_count,
                        "status": f"⚠️ 有倉位（{pos_size} 張）但無條件單且訊號缺 SL/TP 點位",
                        "action": "no_change"}

            try:
                tick = await _g.get_price_round(sym)
            except Exception:
                tick = 0.1

            # 取得目前 mark price（用於跳過已突破的 TP）
            mark_price_c = 0.0
            try:
                _sym_norm = _g._normalize_symbol(sym)
                _mark_resp = await _g._client.get(
                    f"/futures/{_g.settle}/contracts/{_sym_norm}",
                    headers=_g._signed_headers("GET", f"/api/v4/futures/{_g.settle}/contracts/{_sym_norm}", "", ""),
                )
                mark_price_c = float(_mark_resp.json().get("mark_price", 0) or 0)
            except Exception:
                pass

            patch_notes: list[str] = []

            # ── 補 SL（僅在缺失時，不取消已有的 TP）──
            if not has_sl and new_sl:
                sl_rule_c = 2 if is_long else 1
                sl_sign_c = -1 if is_long else 1
                sl_str_c = _g.snap_price(float(new_sl), tick)
                try:
                    await _g.create_price_triggered_order(
                        symbol=sym, size=sl_sign_c * abs_size,
                        trigger_price=sl_str_c, rule=sl_rule_c, price_type=0,
                        expiration=86400 * 7, reduce_only=True)
                    patch_notes.append(f"補SL@{sl_str_c}")
                except Exception as e_sl:
                    patch_notes.append(f"SL補掛失敗:{e_sl}")

            # ── 補 TP（僅在缺失時，跳過已突破的 TP 價格）──
            if not has_tp:
                pct1_c = float(log.get("tp1_close_pct") or 40)
                pct2_c = float(log.get("tp2_close_pct") or 30)
                pct3_c = float(log.get("tp3_close_pct") or 20)
                pct4_c = float(log.get("tp4_close_pct") or 10)
                tps_raw_c = [
                    (new_tp1, pct1_c), (new_tp2, pct2_c),
                    (new_tp3, pct3_c), (new_tp4, pct4_c),
                ]
                valid_tps_c = []
                for tp_p_c, tp_c_c in tps_raw_c:
                    if not tp_p_c:
                        continue
                    tp_val_c = float(tp_p_c)
                    if mark_price_c > 0:
                        if is_long and mark_price_c >= tp_val_c:
                            logger.info(f"[calibrate] {sym} LONG TP {tp_val_c} 已突破(mark={mark_price_c})，跳過")
                            continue
                        if not is_long and mark_price_c <= tp_val_c:
                            logger.info(f"[calibrate] {sym} SHORT TP {tp_val_c} 已突破(mark={mark_price_c})，跳過")
                            continue
                    valid_tps_c.append((tp_val_c, tp_c_c))

                tp_rule_c = 1 if is_long else 2
                tp_sign_c = -1 if is_long else 1
                alloc_c = 0
                for i_c, (tp_p_c, pct_c) in enumerate(valid_tps_c):
                    _is_last_c = (i_c == len(valid_tps_c) - 1)
                    if _is_last_c:
                        qty_c = abs_size - alloc_c
                        if qty_c <= 0:
                            break
                    else:
                        qty_c = max(1, int(abs_size * pct_c / 100))
                    alloc_c += qty_c
                    tp_str_c = _g.snap_price(tp_p_c, tick)
                    try:
                        await _g.create_price_triggered_order(
                            symbol=sym, size=tp_sign_c * qty_c,
                            trigger_price=tp_str_c, rule=tp_rule_c, price_type=1,
                            expiration=86400 * 7, reduce_only=True)
                        patch_notes.append(f"補TP@{tp_str_c}x{qty_c}")
                    except Exception as e_tp:
                        patch_notes.append(f"TP補掛失敗:{e_tp}")

            if patch_notes:
                action_taken = "patched"
                status_hint = f"⚠️→✅ 有倉位（{pos_size} 張），{', '.join(patch_notes)}"
            else:
                action_taken = "no_change"
                status_hint = f"✅ 有倉位（{pos_size} 張），{po_count} 筆條件單活躍，無需更新"

            return {"slot": acct.slot, "name": acct.name or f"帳號{acct.slot}",
                    "pos_size": pos_size, "price_orders": po_count,
                    "status": status_hint, "action": action_taken}
        except Exception as exc:
            return {"slot": acct.slot, "name": acct.name or f"帳號{acct.slot}",
                    "status": f"⚠️ 錯誤：{exc}", "action": "error"}
        finally:
            await _g.aclose()

    # ── 全部並行執行 ────────────────────────────────────────────
    all_tasks = [(log, acct) for log in active_latest for acct in accts_enabled]
    results_flat = await asyncio.gather(*[_check_one(lg, ac) for lg, ac in all_tasks], return_exceptions=False)

    # 重新組合報告
    report: list[dict] = []
    idx = 0
    for log in active_latest:
        n = len(accts_enabled)
        acct_results = list(results_flat[idx:idx + n])
        idx += n

        # 自動更新訊號記錄：所有帳號都無倉位 → 標記已平倉
        all_closed = all(r.get("pos_size", -1) == 0 for r in acct_results if "error" not in r.get("action", ""))
        if all_closed:
            for entry in tg.signal_logs:
                if (entry.symbol == log["symbol"] and
                        entry.direction == log["direction"] and
                        entry.status in ("掛單中", "已成交")):
                    entry.status = "已平倉"
            for p in tg._persisted:
                if (p.get("symbol") == log["symbol"] and
                        p.get("direction") == log["direction"] and
                        p.get("status") in ("掛單中", "已成交")):
                    p["status"] = "已平倉"

        report.append({
            "signal_ts": log.get("ts", ""),
            "symbol": log["symbol"],
            "direction": log["direction"],
            "signal_type": log.get("signal_type", ""),
            "sl": log.get("sl_price"),
            "tp1": log.get("tp1_price"),
            "signal_status": log.get("status"),
            "accounts": acct_results,
        })

    _save_persisted_logs(tg._persisted)

    updated_count = sum(
        1 for item in report
        for a in item.get("accounts", [])
        if "updated_tpsl" in str(a.get("action", ""))
    )

    return {
        "checked": len(report),
        "updated": updated_count,
        "report": report,
    }


@app.post("/tg-bot/clear-logs", dependencies=[Depends(require_admin)])
async def tg_bot_clear_logs() -> dict:
    """清除所有訊號記錄（歸零重新統計）。"""
    from backend.tg_bot_manager import _LOG_FILE, _save_persisted_logs  # noqa: PLC0415
    tg: TGBotManager = app.state.tg_bot
    tg.signal_logs.clear()
    tg._persisted.clear()
    _save_persisted_logs([])
    tg._log("🗑️ 訊號記錄已清除，重新計算績效")
    return {"ok": True, "msg": "訊號記錄已全部清除"}


@app.post("/tg-bot/unlock/{symbol}", dependencies=[Depends(require_admin)])
async def tg_bot_unlock(symbol: str, direction: str = "") -> dict:
    """手動解除方向鎖定。direction=做多/做空，留空解除兩個方向。"""
    tg: TGBotManager = app.state.tg_bot
    ok = tg.unlock_symbol(symbol.upper(), direction)
    return {"ok": ok, "msg": f"{'已解鎖 ' + symbol + ' ' + direction if ok else symbol + ' 未鎖定'}"}


@app.get("/tg-bot/check-session", dependencies=[Depends(require_admin)])
async def tg_bot_check_session() -> dict:
    """
    快速檢查目前 session 檔案是否有效，不需要重新驗證碼。
    回傳 authorized=true 時可直接啟動機器人。
    """
    tg: TGBotManager = app.state.tg_bot
    cfg: TGBotConfig = app.state.tg_bot_config
    if not cfg.api_id or not cfg.api_hash:
        return {"authorized": False, "msg": "尚未設定 API ID / API Hash"}
    try:
        ok = await tg.check_session(
            api_id=int(cfg.api_id),
            api_hash=cfg.api_hash,
            session_name=cfg.session_name,
        )
        return {
            "authorized": ok,
            "msg": "✅ Session 有效，可直接啟動機器人" if ok else "⚠️ Session 不存在或已過期，請重新驗證",
        }
    except Exception as e:
        return {"authorized": False, "msg": str(e)}


@app.post("/tg-bot/send-code", dependencies=[Depends(require_admin)])
async def tg_bot_send_code() -> dict:
    """向 Telegram 請求手機驗證碼（UI 登入流程第一步）。"""
    tg: TGBotManager = app.state.tg_bot
    cfg: TGBotConfig = app.state.tg_bot_config
    if not cfg.api_id or not cfg.api_hash or not cfg.phone:
        raise HTTPException(400, "請先填入 api_id / api_hash / phone 並儲存設定")
    try:
        await tg.send_code(
            api_id=int(cfg.api_id),
            api_hash=cfg.api_hash,
            phone=cfg.phone,
            session_name=cfg.session_name,
        )
        return {"ok": True, "msg": f"驗證碼已發送至 {cfg.phone}"}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/tg-bot/verify-code", dependencies=[Depends(require_admin)])
async def tg_bot_verify_code(code: str) -> dict:
    """提交驗證碼（UI 登入流程第二步）。若帳號有 2FA 會回傳 needs_2fa=true。"""
    tg: TGBotManager = app.state.tg_bot
    cfg: TGBotConfig = app.state.tg_bot_config
    try:
        result = await tg.verify_code(phone=cfg.phone, code=code)
        if result == "2FA_REQUIRED":
            return {"ok": True, "needs_2fa": True, "msg": "此帳號啟用了兩步驟驗證，請輸入 Telegram 登入密碼"}
        return {"ok": True, "needs_2fa": False, "msg": f"登入成功：{result}"}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/tg-bot/verify-2fa", dependencies=[Depends(require_admin)])
async def tg_bot_verify_2fa(password: str) -> dict:
    """提交兩步驟驗證密碼（UI 登入流程第三步，僅帳號有 2FA 時需要）。"""
    tg: TGBotManager = app.state.tg_bot
    try:
        name = await tg.verify_2fa(password=password)
        return {"ok": True, "msg": f"2FA 驗證成功，登入帳號：{name}"}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.post("/webhook/tg-signal", dependencies=[Depends(require_webhook)])
async def tg_signal_webhook(signal: TGSignalPayload) -> dict:
    """HTTP 入口：分發給所有已啟用帳號並行下單，回傳彙總結果。"""
    result = await _dispatch_signal_to_accounts(signal)
    return result


@app.get("/debug/build")
async def debug_build() -> dict:
    """確認是否已載入新版（PositionResponse.raw 支援 list | dict）。"""
    return {"positions_endpoint": "PositionResponse_raw_Any", "revision": 7, "market_type_fix": True, "analytics_performance": True}


@app.get("/debug/time")
async def debug_time() -> dict:
    """
    Gate 文件要求 Timestamp 與伺服器差距 <= 60 秒。
    這個端點用來快速看本機時間是否合理（排查 REQUEST_EXPIRED）。
    """
    return {"unix_seconds": int(time.time())}


@app.on_event("shutdown")
async def _shutdown() -> None:
    # 推播伺服器關閉
    ctrl = getattr(app.state, "tg_ctrl_bot", None)
    if ctrl:
        try:
            await ctrl.stop()
        except Exception:
            pass
    nb = getattr(app.state, "notify_bot", None)
    if nb:
        try:
            await nb.notify_server_shutdown()
            await nb.stop()
        except Exception:
            pass
    bot: OrderbookBot = getattr(app.state, "bot", None)
    if bot:
        try:
            await bot.stop()
        except Exception:
            pass
    tg: TGBotManager = getattr(app.state, "tg_bot", None)
    if tg:
        try:
            await tg.stop()
        except Exception:
            pass
    await gate.aclose()


# ── 通知機器人管理 API ────────────────────────────────────────────

@app.get("/notify-bot/status", dependencies=[Depends(require_admin)])
async def notify_bot_status() -> dict:
    nb = app.state.notify_bot
    return {
        "running": nb.running,
        "has_token": bool(nb.token),
        "admin_chat_id": nb.admin_chat_id,
    }


@app.post("/notify-bot/set-chat-id", dependencies=[Depends(require_admin)])
async def notify_bot_set_chat_id(chat_id: str) -> dict:
    nb = app.state.notify_bot
    nb.admin_chat_id = str(chat_id).strip()
    _save_runtime_state(app)
    await nb.send(f"✅ Chat ID 已設定！你可以使用 /help 查看所有指令。")
    return {"ok": True, "admin_chat_id": nb.admin_chat_id}


@app.post("/notify-bot/test", dependencies=[Depends(require_admin)])
async def notify_bot_test() -> dict:
    nb = app.state.notify_bot
    ok = await nb.send(
        "🧪 <b>測試推播成功！</b>\n"
        f"時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        "EGO 量化交易系統通知機器人運作正常。輸入 /help 查看指令。"
    )
    return {"ok": ok}


@app.post("/notify-bot/send", dependencies=[Depends(require_admin)])
async def notify_bot_send(body: dict) -> dict:
    """發送自訂訊息到 TG 通知頻道。"""
    msg = body.get("message", "")
    if not msg:
        raise HTTPException(400, "message 不可為空")
    nb = app.state.notify_bot
    ok = await nb.send(msg)
    return {"ok": ok}

