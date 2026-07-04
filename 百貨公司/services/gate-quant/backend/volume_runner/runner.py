# -*- coding: utf-8 -*-
"""刷量 Runner 主迴圈（asyncio background task）。"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from backend.exchanges.gate_perp import GatePerpAdapter
from backend.volume_runner.config import VolumeRunnerConfig
from backend.volume_runner.mm_engine import MarketMakerEngine
from backend.volume_runner.state import StateStore, VolumeRunnerState


class VolumeRunner:
    def __init__(
        self,
        cfg: VolumeRunnerConfig,
        *,
        gate_factory: Callable[[], GatePerpAdapter],
        base_url: str,
        report_callback: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self.cfg = cfg
        self.store = StateStore(cfg.state_dir)
        self.state = self.store.load()
        self._gate_factory = gate_factory
        self.base_url = base_url
        self._report_callback = report_callback
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._engine = MarketMakerEngine(cfg, self.store)

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.is_running():
            self.state.log("VolumeRunner 已在執行")
            return
        self.state.running = True
        self.state.paused = False
        self.state.pause_reason = ""
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="volume-runner")
        self.state.log("VolumeRunner 啟動")
        self.store.save(self.state)

    async def stop(self) -> None:
        if not self.is_running():
            self.state.running = False
            self.store.save(self.state)
            return
        self.state.log("VolumeRunner 停止中…")
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=8)
        except Exception:
            self._task.cancel()
        self.state.running = False
        self.state.log("VolumeRunner 已停止")
        self.store.save(self.state)

    async def tick_once(self) -> Dict[str, Any]:
        """手動單次 tick（cron / admin API）。"""
        await self._run_tick()
        self.store.save(self.state)
        return self.snapshot()

    def snapshot(self) -> Dict[str, Any]:
        m = self.state.metrics
        return {
            "running": self.is_running() or self.state.running,
            "paused": self.state.paused,
            "pause_reason": self.state.pause_reason,
            "strategy": self.cfg.strategy,
            "symbols": self.cfg.symbols,
            "dry_run": self.cfg.dry_run,
            "base_url": self.base_url,
            "last_tick_ts": self.state.last_tick_ts,
            "metrics": {
                "day": m.day,
                "round_trips": m.round_trips,
                "volume_usdt": round(m.volume_usdt, 2),
                "gross_edge_usdt": round(m.gross_edge_usdt, 4),
                "fee_usdt": round(m.fee_usdt, 4),
                "net_pnl_usdt": round(m.net_pnl_usdt, 4),
                "fills": m.fills,
                "cancels": m.cancels,
                "errors": m.errors,
            },
            "symbol_phases": {
                k: {"phase": v.phase, "order_id": v.order_id, "entry_price": v.entry_price}
                for k, v in self.state.symbols.items()
            },
            "logs": self.state.logs[-40:],
        }

    async def _loop(self) -> None:
        backoff = 1
        while not self._stop.is_set():
            try:
                await self._run_tick()
                self.store.save(self.state)
                backoff = 1
            except Exception as e:
                self.state.metrics.errors += 1
                self.state.log(f"tick 異常: {e}")
                self.store.save(self.state)
                backoff = min(backoff * 2, 60)
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=max(5.0, float(self.cfg.tick_interval_s)),
                )
                break
            except asyncio.TimeoutError:
                pass
            if backoff > 1:
                await asyncio.sleep(backoff)

    async def _run_tick(self) -> None:
        self.state.ensure_day()
        self.state.last_tick_ts = time.time()

        if self.state.paused:
            return

        if self.cfg.should_block_url(self.base_url):
            self.state.log("阻擋：未允許主網且 Base URL 非 Testnet")
            self.state.paused = True
            self.state.pause_reason = "需使用 Testnet 或設 VOLUME_RUNNER_ALLOW_MAINNET=1"
            return

        if self.state.metrics.net_pnl_usdt <= -abs(self.cfg.max_daily_loss_usdt):
            self.state.paused = True
            self.state.pause_reason = f"日虧損達上限 {self.cfg.max_daily_loss_usdt}U"
            self.state.log(self.state.pause_reason)
            return

        gate = self._gate_factory()
        try:
            for symbol in self.cfg.symbols:
                if self.cfg.strategy == "mm":
                    await self._engine.tick_symbol(
                        gate, self.state, symbol, dry_run=self.cfg.dry_run,
                    )
                else:
                    self.state.log(f"策略 {self.cfg.strategy} 尚未實作，跳過")
        finally:
            await gate.aclose()

        await self._maybe_daily_report()

    async def _maybe_daily_report(self) -> None:
        if not self._report_callback:
            return
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        hour = datetime.now(timezone.utc).hour
        if hour < self.cfg.report_hour_utc:
            return
        if self.state.last_report_day == today:
            return
        m = self.state.metrics
        msg = (
            f"📊 <b>刷量 Runner 日報</b> ({today})\n"
            f"策略：雙邊掛單造市 · {'dry-run' if self.cfg.dry_run else '實盤'}\n"
            f"往返：{m.round_trips} 次 · 成交量≈{m.volume_usdt:,.0f} USDT\n"
            f"毛邊際：{m.gross_edge_usdt:+.2f}U · 手續費：-{m.fee_usdt:.2f}U\n"
            f"淨利：{m.net_pnl_usdt:+.2f}U · 成交{m.fills} · 錯誤{m.errors}"
        )
        try:
            await self._report_callback(msg)
            self.state.last_report_day = today
            self.state.log("已發送日報")
        except Exception as e:
            self.state.log(f"日報發送失敗: {e}")
