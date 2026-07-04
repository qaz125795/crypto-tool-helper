# -*- coding: utf-8 -*-
"""刷量 Runner 持久化狀態與指標。"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


@dataclass
class SymbolPhase:
    phase: str = "idle"  # idle | quoting_buy | quoting_sell
    order_id: str = ""
    order_placed_ts: float = 0.0
    entry_price: float = 0.0
    size: int = 0
    last_error: str = ""


@dataclass
class VolumeMetrics:
    day: str = ""
    round_trips: int = 0
    volume_usdt: float = 0.0
    gross_edge_usdt: float = 0.0
    fee_usdt: float = 0.0
    net_pnl_usdt: float = 0.0
    fills: int = 0
    cancels: int = 0
    errors: int = 0


@dataclass
class VolumeRunnerState:
    running: bool = False
    paused: bool = False
    pause_reason: str = ""
    symbols: Dict[str, SymbolPhase] = field(default_factory=dict)
    metrics: VolumeMetrics = field(default_factory=VolumeMetrics)
    logs: list[str] = field(default_factory=list)
    last_tick_ts: float = 0.0
    last_report_day: str = ""

    def log(self, msg: str) -> None:
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.logs.append(line)
        if len(self.logs) > 300:
            self.logs = self.logs[-300:]

    def ensure_day(self) -> None:
        today = _utc_day()
        if self.metrics.day != today:
            self.metrics = VolumeMetrics(day=today)

    def sym(self, symbol: str) -> SymbolPhase:
        if symbol not in self.symbols:
            self.symbols[symbol] = SymbolPhase()
        return self.symbols[symbol]


class StateStore:
    def __init__(self, state_dir: str) -> None:
        self.state_dir = state_dir
        os.makedirs(state_dir, exist_ok=True)
        self.path = os.path.join(state_dir, "runner_state.json")

    def load(self) -> VolumeRunnerState:
        if not os.path.isfile(self.path):
            st = VolumeRunnerState()
            st.metrics.day = _utc_day()
            return st
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            st = VolumeRunnerState()
            st.metrics.day = _utc_day()
            return st

        st = VolumeRunnerState(
            running=bool(raw.get("running")),
            paused=bool(raw.get("paused")),
            pause_reason=str(raw.get("pause_reason") or ""),
            last_tick_ts=float(raw.get("last_tick_ts") or 0),
            last_report_day=str(raw.get("last_report_day") or ""),
            logs=list(raw.get("logs") or [])[-300:],
        )
        m = raw.get("metrics") or {}
        st.metrics = VolumeMetrics(
            day=str(m.get("day") or _utc_day()),
            round_trips=int(m.get("round_trips") or 0),
            volume_usdt=float(m.get("volume_usdt") or 0),
            gross_edge_usdt=float(m.get("gross_edge_usdt") or 0),
            fee_usdt=float(m.get("fee_usdt") or 0),
            net_pnl_usdt=float(m.get("net_pnl_usdt") or 0),
            fills=int(m.get("fills") or 0),
            cancels=int(m.get("cancels") or 0),
            errors=int(m.get("errors") or 0),
        )
        sym_raw = raw.get("symbols") or {}
        for k, v in sym_raw.items():
            if isinstance(v, dict):
                st.symbols[k] = SymbolPhase(
                    phase=str(v.get("phase") or "idle"),
                    order_id=str(v.get("order_id") or ""),
                    order_placed_ts=float(v.get("order_placed_ts") or 0),
                    entry_price=float(v.get("entry_price") or 0),
                    size=int(v.get("size") or 0),
                    last_error=str(v.get("last_error") or ""),
                )
        st.ensure_day()
        return st

    def save(self, st: VolumeRunnerState) -> None:
        payload: Dict[str, Any] = {
            "running": st.running,
            "paused": st.paused,
            "pause_reason": st.pause_reason,
            "last_tick_ts": st.last_tick_ts,
            "last_report_day": st.last_report_day,
            "logs": st.logs[-300:],
            "metrics": asdict(st.metrics),
            "symbols": {k: asdict(v) for k, v in st.symbols.items()},
            "saved_at": time.time(),
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)
