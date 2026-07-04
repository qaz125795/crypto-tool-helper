# -*- coding: utf-8 -*-
"""雙邊掛單造市（Maker 往返刷量）。"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

from backend.core.models import Direction, Intent, OrderRequest, OrderType, TimeInForce
from backend.exchanges.gate_perp import GatePerpAdapter
from backend.volume_runner.config import VolumeRunnerConfig
from backend.volume_runner.state import StateStore, VolumeRunnerState


def _is_filled(order: dict) -> bool:
    status = str(order.get("status") or "").lower()
    finish = str(order.get("finish_as") or "").lower()
    left = order.get("left")
    if status == "finished" and finish == "filled":
        return True
    if left in (0, "0", 0.0):
        return True
    return False


def _fill_price(order: dict) -> float:
    for key in ("fill_price", "price"):
        try:
            v = float(order.get(key) or 0)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return 0.0


def _position_size(positions: Any, symbol: str) -> int:
    contract = symbol.upper().replace("USDT", "_USDT")
    items = positions if isinstance(positions, list) else [positions]
    for p in items:
        if not isinstance(p, dict):
            continue
        c = str(p.get("contract") or "").upper()
        if c == contract or c == symbol.upper():
            try:
                return int(float(p.get("size") or 0))
            except (TypeError, ValueError):
                return 0
    return 0


class MarketMakerEngine:
    """限價 Maker 往返：買一掛買 → 成交後掛賣平倉（含價差）。"""

    def __init__(self, cfg: VolumeRunnerConfig, store: StateStore) -> None:
        self.cfg = cfg
        self.store = store

    async def _order_size(self, gate: GatePerpAdapter, symbol: str) -> int:
        return await gate.convert_to_size(
            symbol=symbol,
            unit_mode="USDT-成本",
            unit_value=float(self.cfg.margin_usdt),
            leverage=int(self.cfg.leverage),
        )

    async def _place_maker(
        self,
        gate: GatePerpAdapter,
        *,
        symbol: str,
        direction: Direction,
        intent: Intent,
        qty: int,
        price: Optional[float] = None,
        reduce_only: bool = False,
        dry_run: bool,
    ) -> Dict[str, Any]:
        req = OrderRequest(
            exchange="gate",
            symbol=symbol,
            intent=intent,
            direction=direction,
            order_type=OrderType.LIMIT,
            qty=str(qty),
            price=str(price) if price else None,
            time_in_force=TimeInForce.GTC,
            reduce_only=reduce_only,
            client_order_id=f"vol_{symbol}_{int(time.time())}",
        )
        if dry_run:
            payload = await gate.build_maker_limit_payload(req)
            return {"dry_run": True, "payload": payload}
        return await gate.place_order(req)

    async def tick_symbol(
        self,
        gate: GatePerpAdapter,
        st: VolumeRunnerState,
        symbol: str,
        *,
        dry_run: bool,
    ) -> None:
        sym = st.sym(symbol)
        now = time.time()
        net_fee = self.cfg.net_fee_rate()

        try:
            positions = await gate.get_positions(symbol=symbol)
            pos = _position_size(positions, symbol)
        except Exception as e:
            sym.last_error = f"查倉位失敗: {e}"
            st.metrics.errors += 1
            st.log(f"{symbol} 查倉位失敗: {e}")
            return

        # 有倉但狀態機 idle → 進入平倉流程
        if pos > 0 and sym.phase == "idle":
            sym.phase = "quoting_sell"
            sym.size = pos
            st.log(f"{symbol} 偵測到多倉 {pos} 張，轉入平倉掛單")

        if sym.phase == "idle":
            await self._tick_idle(gate, st, symbol, sym, dry_run=dry_run)
        elif sym.phase == "quoting_buy":
            await self._tick_quoting_buy(gate, st, symbol, sym, dry_run=dry_run, net_fee=net_fee)
        elif sym.phase == "quoting_sell":
            await self._tick_quoting_sell(gate, st, symbol, sym, pos, dry_run=dry_run, net_fee=net_fee)
        else:
            sym.phase = "idle"

        if sym.order_placed_ts and (now - sym.order_placed_ts) > self.cfg.order_timeout_s:
            if not dry_run and sym.order_id:
                try:
                    await gate.cancel_order(symbol=symbol, order_id=sym.order_id)
                    st.metrics.cancels += 1
                    st.log(f"{symbol} 掛單逾時，已撤單 {sym.order_id}")
                except Exception as e:
                    st.log(f"{symbol} 撤單失敗: {e}")
            sym.phase = "idle"
            sym.order_id = ""
            sym.order_placed_ts = 0.0

    async def _tick_idle(
        self,
        gate: GatePerpAdapter,
        st: VolumeRunnerState,
        symbol: str,
        sym,
        *,
        dry_run: bool,
    ) -> None:
        if not dry_run:
            try:
                await gate.cancel_all_limit_orders(contract=symbol)
            except Exception:
                pass

        try:
            await gate.update_position_leverage(symbol=symbol, leverage=int(self.cfg.leverage))
        except Exception as e:
            st.log(f"{symbol} 設槓桿失敗（繼續）: {e}")

        try:
            qty = await self._order_size(gate, symbol)
        except Exception as e:
            sym.last_error = str(e)
            st.metrics.errors += 1
            st.log(f"{symbol} 換算張數失敗: {e}")
            return

        try:
            resp = await self._place_maker(
                gate, symbol=symbol, direction=Direction.LONG, intent=Intent.OPEN,
                qty=qty, dry_run=dry_run,
            )
        except Exception as e:
            sym.last_error = str(e)
            st.metrics.errors += 1
            st.log(f"{symbol} 掛買單失敗: {e}")
            return

        sym.phase = "quoting_buy"
        sym.size = qty
        sym.order_id = str(resp.get("id") or "")
        sym.order_placed_ts = time.time()
        st.log(f"{symbol} 掛買 Maker {qty} 張" + (" (dry-run)" if dry_run else f" id={sym.order_id}"))

    async def _tick_quoting_buy(
        self,
        gate: GatePerpAdapter,
        st: VolumeRunnerState,
        symbol: str,
        sym,
        *,
        dry_run: bool,
        net_fee: float,
    ) -> None:
        if dry_run:
            # dry-run 模擬成交
            bid, _ = await gate._get_best_bid_ask(symbol)
            sym.entry_price = bid
            sym.phase = "quoting_sell"
            sym.order_id = ""
            sym.order_placed_ts = time.time()
            notional = bid * sym.size * await gate._get_contract_face_value(symbol)
            st.metrics.fills += 1
            st.metrics.volume_usdt += notional
            st.log(f"{symbol} dry-run 模擬買入 @{bid:.2f}")
            return

        if not sym.order_id:
            sym.phase = "idle"
            return

        try:
            order = await gate.get_order(symbol=symbol, order_id=sym.order_id)
        except Exception as e:
            sym.last_error = str(e)
            st.metrics.errors += 1
            return

        if not _is_filled(order):
            return

        entry = _fill_price(order) or sym.entry_price
        sym.entry_price = entry
        sym.phase = "quoting_sell"
        sym.order_id = ""
        sym.order_placed_ts = time.time()
        face = await gate._get_contract_face_value(symbol)
        notional = entry * sym.size * face
        st.metrics.fills += 1
        st.metrics.volume_usdt += notional
        st.log(f"{symbol} 買入成交 @{entry:.4f} 名目≈{notional:.0f}U")

    async def _tick_quoting_sell(
        self,
        gate: GatePerpAdapter,
        st: VolumeRunnerState,
        symbol: str,
        sym,
        pos: int,
        *,
        dry_run: bool,
        net_fee: float,
    ) -> None:
        qty = abs(pos) if pos != 0 else sym.size
        if qty <= 0:
            sym.phase = "idle"
            return

        spread = self.cfg.spread_bps / 10000.0
        entry = sym.entry_price
        if entry <= 0:
            bid, ask = await gate._get_best_bid_ask(symbol)
            entry = ask

        sell_px = entry * (1.0 + spread)
        tick = await gate.get_price_round(symbol)
        sell_str = gate.snap_price(sell_px, tick)

        if not sym.order_id:
            try:
                resp = await self._place_maker(
                    gate,
                    symbol=symbol,
                    direction=Direction.LONG,
                    intent=Intent.CLOSE,
                    qty=qty,
                    price=float(sell_str),
                    reduce_only=True,
                    dry_run=dry_run,
                )
            except Exception as e:
                sym.last_error = str(e)
                st.metrics.errors += 1
                st.log(f"{symbol} 掛賣單失敗: {e}")
                return
            sym.order_id = str(resp.get("id") or "dry")
            sym.order_placed_ts = time.time()
            st.log(f"{symbol} 掛賣平倉 @{sell_str}" + (" (dry-run)" if dry_run else ""))
            if dry_run:
                sym.phase = "idle"
                sym.order_id = ""
                face = await gate._get_contract_face_value(symbol)
                notional = float(sell_str) * qty * face
                edge = notional * spread
                fee = notional * net_fee * 2
                st.metrics.volume_usdt += notional
                st.metrics.gross_edge_usdt += edge
                st.metrics.fee_usdt += fee
                st.metrics.net_pnl_usdt += edge - fee
                st.metrics.round_trips += 1
                st.metrics.fills += 1
            return

        if dry_run:
            return

        try:
            order = await gate.get_order(symbol=symbol, order_id=sym.order_id)
        except Exception as e:
            sym.last_error = str(e)
            st.metrics.errors += 1
            return

        if not _is_filled(order):
            return

        exit_px = _fill_price(order) or float(sell_str)
        face = await gate._get_contract_face_value(symbol)
        buy_notional = entry * qty * face
        sell_notional = exit_px * qty * face
        edge = sell_notional - buy_notional
        fee = (buy_notional + sell_notional) * net_fee
        st.metrics.volume_usdt += sell_notional
        st.metrics.gross_edge_usdt += max(0.0, edge)
        st.metrics.fee_usdt += fee
        st.metrics.net_pnl_usdt += edge - fee
        st.metrics.round_trips += 1
        st.metrics.fills += 1
        st.log(
            f"{symbol} 賣出成交 @{exit_px:.4f} 往返淨利≈{edge - fee:+.2f}U "
            f"累計量≈{st.metrics.volume_usdt:.0f}U"
        )
        sym.phase = "idle"
        sym.order_id = ""
        sym.entry_price = 0.0
        sym.order_placed_ts = 0.0
