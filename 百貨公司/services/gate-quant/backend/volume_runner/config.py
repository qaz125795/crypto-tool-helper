# -*- coding: utf-8 -*-
"""刷量 Runner 設定（環境變數）。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_bool(key: str, default: bool = False) -> bool:
    v = os.environ.get(key, "1" if default else "0").strip().lower()
    return v in ("1", "true", "yes", "on")


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(float(os.environ.get(key, str(default))))
    except (TypeError, ValueError):
        return default


@dataclass
class VolumeRunnerConfig:
    enabled: bool = False
    dry_run: bool = True
    allow_mainnet: bool = False
    strategy: str = "mm"  # mm | funding | grid | trendmaker（目前僅 mm 實作）
    symbols: list[str] = field(default_factory=lambda: ["BTC_USDT", "ETH_USDT"])
    margin_usdt: float = 100.0
    leverage: int = 10
    spread_bps: float = 0.35
    maker_fee_rate: float = 0.00005  # VIP10 maker
    rebate_pct: float = 0.70
    tick_interval_s: float = 45.0
    order_timeout_s: float = 120.0
    max_position_usdt: float = 500.0
    max_daily_loss_usdt: float = 50.0
    account_slot: int = 1
    state_dir: str = "/app/data/volume_runner"
    report_hour_utc: int = 14  # 台北 22:00 日報

    @classmethod
    def from_env(cls) -> "VolumeRunnerConfig":
        syms = os.environ.get("VOLUME_RUNNER_SYMBOLS", "BTC_USDT,ETH_USDT")
        symbol_list = [s.strip() for s in syms.split(",") if s.strip()]
        return cls(
            enabled=_env_bool("VOLUME_RUNNER_ENABLED", False),
            dry_run=_env_bool("VOLUME_RUNNER_DRY_RUN", True),
            allow_mainnet=_env_bool("VOLUME_RUNNER_ALLOW_MAINNET", False),
            strategy=os.environ.get("VOLUME_RUNNER_STRATEGY", "mm").strip().lower() or "mm",
            symbols=symbol_list or ["BTC_USDT", "ETH_USDT"],
            margin_usdt=_env_float("VOLUME_RUNNER_MARGIN_USDT", 100.0),
            leverage=_env_int("VOLUME_RUNNER_LEVERAGE", 10),
            spread_bps=_env_float("VOLUME_RUNNER_SPREAD_BPS", 0.35),
            maker_fee_rate=_env_float("VOLUME_RUNNER_MAKER_FEE", 0.00005),
            rebate_pct=_env_float("VOLUME_RUNNER_REBATE_PCT", 0.70),
            tick_interval_s=_env_float("VOLUME_RUNNER_TICK_INTERVAL_S", 45.0),
            order_timeout_s=_env_float("VOLUME_RUNNER_ORDER_TIMEOUT_S", 120.0),
            max_position_usdt=_env_float("VOLUME_RUNNER_MAX_POSITION_USDT", 500.0),
            max_daily_loss_usdt=_env_float("VOLUME_RUNNER_MAX_DAILY_LOSS_USDT", 50.0),
            account_slot=_env_int("VOLUME_RUNNER_ACCOUNT_SLOT", 1),
            state_dir=os.environ.get("VOLUME_RUNNER_STATE_DIR", "/app/data/volume_runner").strip(),
            report_hour_utc=_env_int("VOLUME_RUNNER_REPORT_HOUR_UTC", 14),
        )

    def should_block_url(self, base_url: str) -> bool:
        """未開主網許可時，非 testnet URL 一律阻擋。"""
        if self.allow_mainnet:
            return False
        return "testnet" not in (base_url or "").lower()

    def net_fee_rate(self) -> float:
        return self.maker_fee_rate * (1.0 - self.rebate_pct)
