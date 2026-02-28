#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
區塊鏈船長—傑克：自動化推播系統
整合所有功能模塊
"""

import requests
import json
import time
import logging
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any, Tuple, Set, Union
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import random
import contextlib
import pandas as pd
import numpy as np

# 台灣台北時區（UTC+8）
TAIPEI_TZ = timezone(timedelta(hours=8))

# 配置日誌：執行時終端顯示 + 寫入 log 檔，方便排查
_log_fmt = '%(asctime)s - %(levelname)s - %(message)s'
logging.basicConfig(
    level=logging.INFO,
    format=_log_fmt,
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True
)
logger = logging.getLogger(__name__)

# ==================== 配置設定 ====================
# 一律從環境變量讀取，避免在程式碼中硬編 API 金鑰等敏感資訊

# CoinGecko API
CG_GECKO_API_KEY = os.getenv('CG_GECKO_API_KEY')

# CoinGlass API
CG_API_KEY = os.getenv('CG_API_KEY')
CG_API_BASE = "https://open-api-v4.coinglass.com"

# ══════════════════════════════════════════════════════════════════════════════
# CoinGlass API 端點完整清單（v4 標準版）
# 格式：「功能鍵」: "路徑"  # 說明 [分類]
# ※ 備援路徑以 _v2 / _alt 後綴區分；停用端點標注 ⛔
# ══════════════════════════════════════════════════════════════════════════════
CG_EP = {
    # ════════════════ 交易市場 Market ════════════════
    "supported_coins":       "/api/futures/supported-coins",                         # 支持合約幣種列表
    "supported_pairs":       "/api/futures/supported-exchange-pairs",                # 支持的交易對
    "pairs_markets":         "/api/futures/pairs-markets",                           # 合約交易對詳情
    "coins_markets":         "/api/futures/coins-markets",                           # 合約幣種市場行情（主要掃描源）
    "price_change_list":     "/futures/price-change-list",                           # 幣種價格變化列表
    "price_ohlc_history":    "/api/price/ohlc-history",                             # 交易對價格K線歷史
    # ── 現有路徑備援別名 ──
    "price_history_futures": "/api/futures/price/history",                           # 合約價格K線（舊路徑）
    "price_history_spot":    "/api/spot/price/history",                              # 現貨價格K線
    "delisted_pairs":        "/api/futures/delisted-exchange-pairs",                 # 已下架交易對

    # ════════════════ 持倉 Open Interest ════════════════
    "oi_history":            "/api/futures/openInterest/ohlc-history",              # 合約持倉K線
    "oi_agg_history":        "/api/futures/openInterest/ohlc-aggregated-history",   # 聚合持倉K線（主力）
    "oi_agg_stable":         "/api/futures/openInterest/ohlc-aggregated-stablecoin",# 穩定幣保證金持倉
    "oi_agg_coin":           "/api/futures/openInterest/ohlc-aggregated-coin-margin-history", # 幣本位持倉
    "oi_exchange_list":      "/api/futures/open-interest/exchange-list",              # 各所持倉列表（文檔確認 kebab-case）
    "oi_exchange_history":   "/api/futures/open-interest/exchange-history-chart",    # 各所持倉歷史圖表（文檔確認 kebab-case）
    # ── 舊路徑備援（部分 API 可能只支援新路徑）──
    "oi_history_old":        "/api/futures/open-interest/history",
    "oi_agg_history_old":    "/api/futures/open-interest/aggregated-history",

    # ════════════════ 資金費率 Funding Rate ════════════════
    "fr_history":            "/api/futures/fundingRate/ohlc-history",               # 費率K線
    "fr_oi_weight":          "/api/futures/fundingRate/oi-weight-ohlc-history",     # OI加權費率（最精準）
    "fr_vol_weight":         "/api/futures/fundingRate/vol-weight-ohlc-history",    # 成交量加權費率
    "fr_exchange_list":      "/api/futures/fundingRate/exchange-list",              # 各所費率列表
    "fr_accum_exchange":     "/api/futures/fundingRate/accumulated-exchange-list",  # 累積費率（過熱偵測）
    "fr_arbitrage":          "/api/futures/fundingRate/arbitrage",                  # 費率套利機會 🆕
    # ── 舊路徑備援 ──
    "fr_history_old":        "/api/futures/funding-rate/history",
    "fr_oi_weight_old":      "/api/futures/funding-rate/oi-weight-history",
    "fr_vol_weight_old":     "/api/futures/funding-rate/vol-weight-history",
    "fr_exchange_list_old":  "/api/futures/funding-rate/exchange-list",
    "fr_accum_exchange_old": "/api/futures/funding-rate/accumulated-exchange-list",

    # ════════════════ 多空比 Long/Short Ratio ════════════════
    "ls_global_history":     "/api/futures/global-long-short-account-ratio/history",# 全網帳戶多空比
    "ls_top_account":        "/api/futures/top-long-short-account-ratio/history",   # 大戶帳戶多空比
    "ls_top_position":       "/api/futures/top-long-short-position-ratio/history",  # 大戶持倉多空比

    # ════════════════ 淨持倉 Net Position ════════════════
    "net_pos_v2":            "/api/futures/v2/net-position/history",                  # 淨多/空持倉歷史 v2（欄位更完整）
    "net_pos_v1":            "/api/futures/net-position/history",                     # 淨多/空持倉歷史 v1（備援）

    # ════════════════ 爆倉 Liquidation ════════════════
    "liq_history":           "/api/futures/liquidation/history",                    # 交易對爆倉歷史
    "liq_agg_history":       "/api/futures/liquidation/aggregated-history",         # 幣種聚合爆倉歷史（主力）
    "liq_coin_list":         "/api/futures/liquidation/coin-list",                  # 幣種爆倉列表
    "liq_exchange_list":     "/api/futures/liquidation/exchange-list",              # 交易所爆倉列表
    "liq_order":             "/api/futures/liquidation/order",                      # 即時爆倉訂單
    "liq_heatmap_m1":        "/api/futures/liquidation/heatmap/model1",             # 爆倉熱力圖 Model1 🆕
    "liq_heatmap_m2":        "/api/futures/liquidation/heatmap/model2",             # 爆倉熱力圖 Model2 🆕
    "liq_heatmap_m3":        "/api/futures/liquidation/heatmap/model3",             # 爆倉熱力圖 Model3 🆕
    "liq_agg_heatmap_m1":    "/api/futures/liquidation/aggregated-heatmap/model1",  # 幣種聚合熱力圖 M1 🆕
    "liq_agg_heatmap_m2":    "/api/futures/liquidation/aggregated-heatmap/model2",  # 幣種聚合熱力圖 M2 🆕
    "liq_agg_heatmap_m3":    "/api/futures/liquidation/aggregated-heatmap/model3",  # 幣種聚合熱力圖 M3 🆕
    "liq_map":               "/api/futures/liquidation/map",                        # 爆倉地圖 🆕
    "liq_agg_map":           "/api/futures/liquidation/aggregated-map",             # 幣種爆倉地圖 🆕

    # ════════════════ 訂單簿 Orderbook（合約） ════════════════
    "ob_ask_bids_history":   "/api/futures/orderbook/ask-bids-history",             # 交易對掛單深度歷史
    "ob_agg_ask_bids":       "/api/futures/orderbook/aggregated-ask-bids-history",  # 幣種聚合深度歷史
    "ob_heatmap":            "/api/futures/orderbook/history",                      # 訂單簿熱力圖
    "ob_large_order":        "/api/futures/orderbook/large-limit-order",            # 大額掛單
    "ob_large_order_hist":   "/api/futures/orderbook/large-limit-order-history",    # 大額掛單歷史

    # ════════════════ 主動買賣（合約） ════════════════
    "taker_exchange_list":   "/api/futures/taker-buy-sell-volume/exchange-list",    # 各所主動買賣比（參數用 range=h1）
    "taker_pair_history":    "/api/futures/v2/taker-buy-sell-volume/history",       # 交易對主動買賣歷史 v2（文檔確認）
    "taker_agg_history":     "/api/futures/aggregated-taker-buy-sell-volume/history",# 幣種聚合主動買賣（主力）

    # ════════════════ 訂單簿（現貨） ════════════════
    "spot_ob_ask_bids":      "/api/spot/orderbook/ask-bids-history",                # 現貨交易對深度 🆕
    "spot_ob_agg_ask_bids":  "/api/spot/orderbook/aggregated-ask-bids-history",     # 現貨幣種聚合深度 🆕
    "spot_ob_heatmap":       "/api/spot/orderbook/history",                         # 現貨訂單簿熱力圖 🆕
    "spot_ob_large_order":   "/api/spot/orderbook/large-limit-order",               # 現貨大額掛單 🆕
    "spot_ob_large_order_h": "/api/spot/orderbook/large-limit-order-history",       # 現貨大額掛單歷史 🆕

    # ════════════════ 主動買賣（現貨） ════════════════
    "spot_taker_history":    "/api/spot/taker-buy-sell-volume/history",             # 現貨交易對主動買賣 🆕
    "spot_taker_agg":        "/api/spot/aggregated-taker-buy-sell-volume/history",  # 現貨幣種聚合 🆕

    # ════════════════ 現貨市場 ════════════════
    "spot_supported_coins":  "/api/spot/supported-coins",                           # 支持的現貨幣種 🆕
    "spot_supported_pairs":  "/api/spot/supported-exchange-pairs",                  # 支持的現貨交易對 🆕
    "spot_coins_markets":    "/api/spot/coins-markets",                             # 現貨幣種市場 🆕
    "spot_pairs_markets":    "/api/spot/pairs-markets",                             # 現貨交易對市場 🆕

    # ════════════════ 期權 Options ════════════════
    "opt_max_pain":          "/api/option/max-pain",                                # 最大痛點價 🆕
    "opt_info":              "/api/option/info",                                    # 期權信息 🆕
    "opt_exchange_oi":       "/api/option/exchange-oi-history",                     # 各所期權持倉歷史 🆕
    "opt_exchange_vol":      "/api/option/exchange-vol-history",                    # 各所期權成交量歷史 🆕

    # ════════════════ 鏈上 On-Chain ════════════════
    "exchange_assets":       "/api/exchange/assets",                                # 交易所資產透明度 🆕
    "exchange_balance_list": "/api/exchange/balance/list",                          # 交易所餘額列表 🆕
    "exchange_balance_chart":"/api/exchange/balance/chart",                         # 交易所餘額圖表 🆕
    "exchange_chain_tx":     "/api/exchange/chain/tx/list",                         # 鏈上轉帳記錄 🆕

    # ════════════════ ETF（比特幣 & 以太坊） ════════════════
    "btc_etf_list":          "/api/etf/bitcoin/list",                               # 比特幣ETF列表 🆕
    "btc_etf_flow":          "/api/etf/bitcoin/flow-history",                       # 比特幣ETF資金流 🆕
    "btc_etf_net_assets":    "/api/etf/bitcoin/net-assets/history",                 # 比特幣ETF淨資產 🆕
    "btc_etf_premium":       "/api/etf/bitcoin/premium-discount/history",           # 比特幣ETF溢價/折價 🆕
    "btc_etf_history":       "/api/etf/bitcoin/history",                            # 比特幣ETF歷史 🆕
    "btc_etf_price":         "/api/etf/bitcoin/price/history",                      # 比特幣ETF價格 🆕
    "btc_etf_detail":        "/api/etf/bitcoin/detail",                             # 比特幣ETF詳情 🆕
    "hk_btc_etf_flow":       "/api/hk-etf/bitcoin/flow-history",                   # 香港BTC ETF流向 🆕
    "eth_etf_net_assets":    "/api/etf/ethereum/net-assets-history",                # 以太坊ETF淨資產 🆕
    "eth_etf_list":          "/api/etf/ethereum/list",                              # 以太坊ETF列表 🆕
    "eth_etf_flow":          "/api/etf/ethereum/flow-history",                      # 以太坊ETF資金流 🆕
    "grayscale_holdings":    "/api/grayscale/holdings-list",                        # 灰度持倉列表 🆕
    "grayscale_premium":     "/api/grayscale/premium-history",                      # 灰度溢價歷史 🆕

    # ════════════════ 市場指標 Indicators ════════════════
    "rsi_list":              "/api/futures/rsi/list",                               # RSI列表
    "contract_basis":        "/api/futures/basis/history",                          # 合約基差歷史 🆕
    "borrow_rate":           "/api/borrow-interest-rate/history",                   # 借貸利率歷史 🆕
    "coinbase_premium":      "/api/coinbase-premium-index",                         # Coinbase溢價指數 🆕
    "bitfinex_margin_ls":    "/api/bitfinex-margin-long-short",                     # Bitfinex保證金多空 🆕
    "fear_greed":            "/api/index/fear-greed-history",                       # 恐懼貪婪指數 🆕
    "stablecoin_mcap":       "/api/index/stableCoin-marketCap-history",             # 穩定幣市值歷史
    "bull_market_peak":      "/api/bull-market-peak-indicator",                     # 牛市頂部指標 🆕
    "ahr999":                "/api/index/ahr999",                                   # AHR999指標 🆕
    "puell_multiple":        "/api/index/puell-multiple",                           # Puell多重指標 🆕
    "stock_flow":            "/api/index/stock-flow",                               # Stock-to-Flow模型 🆕
    "pi_cycle":              "/api/index/pi-cycle-indicator",                       # Pi Cycle頂部指標 🆕
    "golden_ratio":          "/api/index/golden-ratio-multiplier",                  # 黃金比例乘數 🆕
    "btc_profitable_days":   "/api/index/bitcoin/profitable-days",                  # BTC盈利天數 🆕
    "btc_rainbow":           "/api/index/bitcoin/rainbow-chart",                    # BTC彩虹圖 🆕
    "btc_bubble_index":      "/api/index/bitcoin/bubble-index",                     # BTC泡沫指數 🆕
    "ma_2yr_multiplier":     "/api/index/2-year-ma-multiplier",                     # 2年均線乘數 🆕
    "ma_200wk_heatmap":      "/api/index/200-week-moving-average-heatmap",          # 200週均線熱力圖 🆕

    # ════════════════ Hyperliquid ════════════════
    "hl_whale_alert":        "/api/hyperliquid/whale-alert",                        # HL鯨魚預警
    "hl_whale_position":     "/api/hyperliquid/whale-position",                     # HL鯨魚持倉
    "hl_position":           "/api/hyperliquid/position",                           # HL幣種持倉
    "hl_wallet_pos_dist":    "/api/hyperliquid/wallet/position-distribution",       # HL錢包持倉分布
    "hl_wallet_pnl_dist":    "/api/hyperliquid/wallet/pnl-distribution",            # HL錢包盈虧分布

    # ════════════════ CVD（舊路徑保留） ════════════════
    "cvd_history":           "/api/futures/cvd/history",
    "cvd_agg_history":       "/api/futures/aggregated-cvd/history",

    # ════════════════ 腳步圖（需更高授權 ⛔ 暫停）════════════════
    "footprint":             "/api/futures/volume/footprint-history",               # ⛔ 需升級帳號
}

# Tree of Alpha API
TREE_API_KEY = os.getenv('TREE_API_KEY')

# Telegram 配置
TG_TOKEN = os.getenv('TG_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

# Telegram Thread IDs (從環境變量讀取 JSON，或使用預設值)
thread_ids_str = os.environ.get('TG_THREAD_IDS', '')
if thread_ids_str:
    try:
        TG_THREAD_IDS = json.loads(thread_ids_str)
    except:
        TG_THREAD_IDS = {
            'sector_ranking': 5,
            'buying_power_monitor': 246,  # 原 whale_position，已替換為購買力監控
            'position_change': 250,
            'economic_data': 13,
            'news': 7,
            'funding_rate': 244,
            'long_term_index': 248,
            'liquidity_radar': 3,
            'altseason_radar': 11044,
            'hyperliquid': 252,
            'gold_signal': 254,  # 黃金 XAUUSD 訊號（可改為專用 topic 的 thread_id）
        }
else:
    TG_THREAD_IDS = {
        'sector_ranking': int(os.environ.get('TG_THREAD_SECTOR_RANKING', 5)),
        'buying_power_monitor': int(os.environ.get('TG_THREAD_WHALE_POSITION', 246)),  # 使用原 whale_position 的 thread ID
        'position_change': int(os.environ.get('TG_THREAD_POSITION_CHANGE', 250)),
        'economic_data': int(os.environ.get('TG_THREAD_ECONOMIC_DATA', 13)),
        'news': int(os.environ.get('TG_THREAD_NEWS', 7)),
        'funding_rate': int(os.environ.get('TG_THREAD_FUNDING_RATE', 244)),
        'long_term_index': int(os.environ.get('TG_THREAD_LONG_TERM_INDEX', 248)),
        'liquidity_radar': int(os.environ.get('TG_THREAD_LIQUIDITY_RADAR', 3)),
        'altseason_radar': int(os.environ.get('TG_THREAD_ALTSEASON_RADAR', 11044)),
        'hyperliquid': int(os.environ.get('TG_THREAD_HYPERLIQUID', 252)),
        'gold_signal': int(os.environ.get('TG_THREAD_GOLD_SIGNAL') or 254),
    }

# 其他配置
EXCHANGE = "Binance"
TIME_TYPE = "h1"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
# 持倉變化篩選：改為只偵測合約幣種（使用 API 獲取）
MAX_SYMBOLS = 904  # 將由 API 返回的合約幣種數量決定

# 數據存儲目錄（使用腳本所在目錄，確保 cron/Zeabur 等不同 cwd 下路徑一致）
DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(exist_ok=True)
# 同時寫入 log 檔，方便事後排查
_log_file = DATA_DIR / "jackbot.log"
_fh = logging.FileHandler(_log_file, encoding="utf-8")
_fh.setLevel(logging.INFO)
_fh.setFormatter(logging.Formatter(_log_fmt))
logging.getLogger().addHandler(_fh)

# CoinGlass OI 呼叫限速（初創版 80 次/分鐘，global 必須在函數內「最先」宣告再賦值）
_coinglass_oi_rate_limiter = None

# CoinGlass API 全域呼叫計數（標準版 300/min，保留 50 緩衝，設 250）
_coinglass_api_counter: Dict[str, Any] = {"window_start": 0.0, "count": 0}
_coinglass_api_counter_lock = threading.Lock()
_COINGLASS_MAX_CALLS_PER_MINUTE = 250

# BingX 技術指標失敗次數（每輪用於判斷是否啟用 CoinGlass Plan B）
# 加鎖確保 ThreadPoolExecutor 並發環境下計數正確
_bingx_tech_fail_count: int = 0
_bingx_tech_fail_lock = threading.Lock()

# OI API 最後一次呼叫的 HTTP 狀態碼與錯誤訊息（供 process_single_symbol 診斷回報）
_oi_last_status: Dict[str, int] = {}
_oi_last_error: Dict[str, str] = {}

# 動態 OI 門檻統計（每輪由 fetch_position_change 根據當前樣本分佈更新）
# 宣告於頂部，確保 _classify_signal_and_tier 與 fetch_position_change 均能正確存取
_dynamic_oi_mean_30m: Optional[float] = None
_dynamic_oi_std_30m: Optional[float] = None
_dynamic_oi_4star: Optional[float] = None
_dynamic_oi_5star: Optional[float] = None
_dynamic_oi_sample_size: int = 0

# 緊急備援：GitHub Action timeout (SIGTERM) 前確保 sniper_cooldown.json 能寫回磁碟
# fetch_position_change 執行時會持續更新此 dict，atexit/SIGTERM handler 讀取後寫入
_emergency_sniper_state: Dict[str, Any] = {}
_emergency_sniper_path: Optional[str] = None

# ── API 熔斷器 (Circuit Breaker) ──────────────────────────────────────────────
# 若連續出現 5 次 429，自動將 MAX_WORKERS 降為 1 並將 wait_time 加倍，持續 5 分鐘
_circuit_breaker: Dict[str, Any] = {
    "consecutive_429": 0,
    "warned": False,       # 3+ 次 429：進入「警戒」模式，MAX_WORKERS→2
    "tripped": False,      # 5+ 次 429：進入「熔斷」模式，MAX_WORKERS→1
    "trip_time": 0.0,
    "trip_duration": 300.0,  # 5 分鐘保護期（tripped 狀態）
    "warn_duration": 120.0,  # 2 分鐘警戒期（warned 狀態）
    "warn_time": 0.0,
}
_circuit_breaker_lock = threading.Lock()


def _cb_record_429() -> None:
    """記錄一次 429 錯誤。
    ≥3 次：進入警戒（MAX_WORKERS→2，輕量保護）。
    ≥5 次：進入熔斷（MAX_WORKERS→1，完全保護）。
    """
    with _circuit_breaker_lock:
        _circuit_breaker["consecutive_429"] += 1
        cnt = _circuit_breaker["consecutive_429"]
        now = time.time()
        # 警戒階段（3-4 次）
        if cnt >= 3 and not _circuit_breaker["warned"] and not _circuit_breaker["tripped"]:
            _circuit_breaker["warned"] = True
            _circuit_breaker["warn_time"] = now
            logger.warning(
                f"[熔斷器警戒⚠️] 連續 {cnt} 次 429，"
                f"MAX_WORKERS 降至 2，持續 {_circuit_breaker['warn_duration']:.0f} 秒保護"
            )
        # 熔斷階段（5+ 次）
        if cnt >= 5 and not _circuit_breaker["tripped"]:
            _circuit_breaker["tripped"] = True
            _circuit_breaker["trip_time"] = now
            logger.warning(
                f"[熔斷器啟動🚨] 連續 {cnt} 次 429，"
                f"MAX_WORKERS 降至 1，持續 {_circuit_breaker['trip_duration']/60:.0f} 分鐘完全保護"
            )


def _cb_record_success() -> None:
    """記錄一次成功請求，重置連續 429 計數與警戒狀態。"""
    with _circuit_breaker_lock:
        if _circuit_breaker["consecutive_429"] > 0:
            _circuit_breaker["consecutive_429"] = 0
        _circuit_breaker["warned"] = False


def _cb_is_tripped() -> bool:
    """判斷熔斷器是否仍在「完全熔斷」保護期；到期自動恢復。"""
    with _circuit_breaker_lock:
        if not _circuit_breaker["tripped"]:
            return False
        elapsed = time.time() - _circuit_breaker["trip_time"]
        if elapsed >= _circuit_breaker["trip_duration"]:
            _circuit_breaker["tripped"] = False
            _circuit_breaker["warned"] = False
            _circuit_breaker["consecutive_429"] = 0
            logger.info("[熔斷器恢復✅] 5 分鐘保護期結束，恢復正常並行數與等待時間")
            return False
        return True


def _cb_is_warned() -> bool:
    """判斷熔斷器是否仍在「警戒」狀態（3-4 次 429）；到期自動解除。"""
    with _circuit_breaker_lock:
        if not _circuit_breaker["warned"] or _circuit_breaker["tripped"]:
            return False
        elapsed = time.time() - _circuit_breaker["warn_time"]
        if elapsed >= _circuit_breaker["warn_duration"]:
            _circuit_breaker["warned"] = False
            _circuit_breaker["consecutive_429"] = max(0, _circuit_breaker["consecutive_429"] - 2)
            logger.info("[熔斷器警戒解除🟡] 警戒期結束，MAX_WORKERS 回升至預設值")
            return False
        return True


def _cb_get_max_workers(default: int = 15) -> int:
    """根據熔斷器狀態返回建議最大執行緒數。
    正常 → default(12)；警戒(3次429) → 2；完全熔斷(5次429) → 1
    """
    if _cb_is_tripped():
        return 1
    if _cb_is_warned():
        return 2
    return default


def _cb_get_wait_multiplier() -> float:
    """根據熔斷器狀態返回 wait_time 倍率。
    正常 → 1×；警戒 → 1.5×；完全熔斷 → 2×
    """
    if _cb_is_tripped():
        return 2.0
    if _cb_is_warned():
        return 1.5
    return 1.0


def _emergency_save_sniper_state() -> None:
    """緊急備援寫入：atexit 與 SIGTERM handler 共用。
    確保 GitHub Action timeout 或意外終止前，sniper_cooldown.json 能落磁碟。
    """
    global _emergency_sniper_state, _emergency_sniper_path
    if not _emergency_sniper_path or not _emergency_sniper_state:
        return
    try:
        path = Path(_emergency_sniper_path)
        tmp = path.with_suffix(path.suffix + ".emergency_tmp")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_emergency_sniper_state, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
        logging.getLogger(__name__).info(
            f"[緊急備援] sniper_cooldown.json 已安全寫回 ({path})"
        )
    except Exception as ex:
        logging.getLogger(__name__).warning(f"[緊急備援] 緊急寫入失敗: {ex}")


import atexit as _atexit
import signal as _signal

_atexit.register(_emergency_save_sniper_state)

def _sigterm_handler(signum, frame):  # type: ignore[type-arg]
    _emergency_save_sniper_state()
    raise SystemExit(0)

try:
    _signal.signal(_signal.SIGTERM, _sigterm_handler)
except (OSError, ValueError):
    # 某些環境（Windows/非主執行緒）不支援 SIGTERM，忽略
    pass


def _respect_coinglass_rate_limit() -> None:
    """簡單的全域速率限制：確保 CoinGlass API 約 <70 次/分鐘。"""
    now = time.time()
    with _coinglass_api_counter_lock:
        window_start = _coinglass_api_counter.get("window_start", 0.0)
        count = _coinglass_api_counter.get("count", 0)
        if now - window_start >= 60.0:
            window_start = now
            count = 0
        if count >= _COINGLASS_MAX_CALLS_PER_MINUTE:
            sleep_for = 60.0 - (now - window_start)
            if sleep_for > 0:
                logger.info(f"[CoinGlass 限流保護] 本分鐘 API 已達 {count} 次，休息 {sleep_for:.1f} 秒再繼續")
                time.sleep(sleep_for)
            window_start = time.time()
            count = 0
        _coinglass_api_counter["window_start"] = window_start
        _coinglass_api_counter["count"] = count + 1

# ==================== 工具函數 ====================

def send_telegram_message(text: str, thread_id: int, parse_mode: str = "Markdown", reply_markup: Optional[Dict] = None) -> bool:
    """發送訊息到 Telegram（支援 Inline Keyboard 按鈕）"""
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "message_thread_id": thread_id,
        "text": text,
        "disable_web_page_preview": True
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            result = response.json()
            if result.get("ok"):
                logger.info("Telegram 訊息發送成功")
                return True
            else:
                logger.error(f"Telegram API 錯誤: {result}")
                return False
        else:
            logger.error(f"Telegram HTTP 錯誤: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"發送 Telegram 訊息失敗: {str(e)}")
        return False


def load_json_file(filepath: Path, default: Any = None) -> Any:
    """從文件加載 JSON 數據；若主文件損毀或為空，自動嘗試從備份恢復。"""
    def _try_load(path: Path) -> Any:
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding='utf-8').strip()
            if not text:
                return None
            return json.loads(text)
        except Exception:
            return None

    result = _try_load(filepath)
    if result is not None:
        return result

    # 主文件損毀或不存在，嘗試備份
    backup_path = DATA_DIR / "backup_state.json"
    if backup_path.exists() and backup_path != filepath:
        try:
            backup_all: Dict[str, Any] = json.loads(backup_path.read_text(encoding='utf-8'))
            key = str(filepath.name)
            backed = backup_all.get(key)
            if backed is not None:
                logger.warning(f"[備份還原] 主文件 {filepath.name} 損毀/為空，已從 backup_state.json 恢復")
                return backed
        except Exception as be:
            logger.debug(f"[備份還原] 讀取備份失敗: {be}")

    if filepath.exists():
        logger.error(f"讀取文件失敗（內容無效）{filepath}")
    return default if default is not None else []


def save_json_file_safe(filepath: Path, data: Any) -> bool:
    """原子安全寫入 JSON 檔案。
    實作流程（GitHub Actions 超時中斷保護）：
      1. 寫入 <filepath>.tmp 暫存檔（含 fsync）
      2. os.replace() 原子改名 → 確保目標檔案永不處於半寫入狀態
      3. 若為關鍵狀態檔（sniper_cooldown.json / last_summary_date.json），
         同步更新 data/backup_state.json 多重保護

    建議所有持久狀態 JSON 都改用此函數替代 open()/json.dump()。
    """
    try:
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        # sniper_cooldown.json 使用固定名稱 temp_sniper.json 避免與其他 .tmp 檔案衝突
        if filepath.name == "sniper_cooldown.json":
            tmp_path = filepath.parent / "temp_sniper.json"
        else:
            tmp_path = filepath.with_suffix(filepath.suffix + ".tmp")

        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass  # Windows/某些 FS 不支援 fsync，忽略
        os.replace(tmp_path, filepath)  # 原子改名，絕不產生中間損毀態

        # 關鍵狀態檔：同步寫入 backup_state.json（多重保護）
        _critical_files = {"sniper_cooldown.json", "last_summary_date.json", "performance_history.json"}
        if filepath.name in _critical_files:
            backup_path = DATA_DIR / "backup_state.json"
            try:
                if backup_path.exists():
                    try:
                        backup_all: Dict[str, Any] = json.loads(backup_path.read_text(encoding='utf-8'))
                    except Exception:
                        backup_all = {}
                else:
                    backup_all = {}
                backup_all[filepath.name] = data
                bak_tmp = backup_path.with_suffix(".tmp")
                with open(bak_tmp, 'w', encoding='utf-8') as bf:
                    json.dump(backup_all, bf, ensure_ascii=False, indent=2)
                    bf.flush()
                    try:
                        os.fsync(bf.fileno())
                    except OSError:
                        pass
                os.replace(bak_tmp, backup_path)
            except Exception as be:
                logger.warning(f"[備份寫入] backup_state.json 更新失敗（不影響主流程）: {be}")
        return True
    except Exception as e:
        logger.error(f"[safe寫入失敗] {filepath}: {e}")
        return False


def save_json_file(filepath: Path, data: Any) -> bool:
    """向後相容包裝器，實際委派給 save_json_file_safe。"""
    return save_json_file_safe(filepath, data)


def translate_text(text: str, target_lang: str = 'zh-tw') -> str:
    """翻譯文本（使用 googletrans，如果可用）"""
    try:
        from googletrans import Translator
        translator = Translator()
        result = translator.translate(text, dest=target_lang)
        return result.text
    except ImportError:
        logger.warning("googletrans 未安裝，跳過翻譯")
        return text
    except Exception as e:
        logger.warning(f"翻譯失敗: {str(e)}，使用原文")
        return text


def get_taipei_time(dt: Optional[datetime] = None) -> datetime:
    """獲取台灣台北時間（UTC+8）"""
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        # 如果沒有時區資訊，假設是 UTC
        dt = dt.replace(tzinfo=timezone.utc)
    # 轉換為台灣時間
    return dt.astimezone(TAIPEI_TZ)


def format_datetime(dt: datetime) -> str:
    """格式化日期時間（自動轉換為台灣時間）"""
    # 轉換為台灣時間
    dt_taipei = get_taipei_time(dt)
    weekdays = ['一', '二', '三', '四', '五', '六', '日']
    weekday = weekdays[dt_taipei.weekday()]
    return dt_taipei.strftime(f"%Y-%m-%d (週{weekday}) %H:%M")


# ==================== 1. 主流板塊排行榜推播 ====================

MAIN_SECTORS = {
    "Artificial Intelligence (AI)": "AI 機器人幫手",
    "Meme": "Meme 迷因 (年輕人愛玩)",
    "Smart Contract Platform": "智慧合約 (基礎建設)",
    "Decentralized Finance (DeFi)": "DeFi (虛擬銀行)",
    "Exchange-based Tokens": "交易所代幣 (券商幣)",
    "Real World Assets (RWA)": "RWA (房產/黃金上鏈)",
    "Gaming (GameFi)": "GameFi (打電動賺錢)",
    "Stablecoins": "穩定幣 (美金)"
}


def fetch_sector_ranking():
    """抓取主流板塊排行榜"""
    url = f"https://api.coingecko.com/api/v3/coins/categories?x_cg_demo_api_key={CG_GECKO_API_KEY}"
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            logger.error(f"CoinGecko API 錯誤: {response.status_code}")
            return
        
        categories = response.json()
        
        # 過濾並中文化
        filtered_sectors = []
        for category in categories:
            if category.get('name') in MAIN_SECTORS:
                filtered_sectors.append({
                    'displayName': MAIN_SECTORS[category['name']],
                    'change': category.get('market_cap_change_24h', 0)
                })
        
        # 排序
        filtered_sectors.sort(key=lambda x: x['change'], reverse=True)
        
        send_ranking_to_tg(filtered_sectors)
        
    except Exception as e:
        logger.error(f"數據抓取失敗: {str(e)}")


def send_ranking_to_tg(ranking: List[Dict]):
    """發送排行榜到 Telegram（阿嬤友善版 + 熱力圖按鈕）"""
    message = "📊 *【全球主流加密板塊排行榜】(4H)*\n\n"
    message += "🔥 *主流板塊強弱一覽：*\n"

    for index, sector in enumerate(ranking):
        medal = "🥇" if index == 0 else "🥈" if index == 1 else "🥉" if index == 2 else "🔹"
        ch = sector.get("change", 0) or 0
        change_str = f"{ch:.2f}"
        sign = "+" if ch >= 0 else ""
        # 視覺指標：>5% 火爆 / <-5% 冷卻 / -1%~1% 盤整 / 其餘 📈📉
        if ch > 5:
            prefix = "🔥"
        elif ch < -5:
            prefix = "❄️"
        elif -1 <= ch <= 1:
            prefix = "😴"
        else:
            prefix = "📈" if ch > 0 else "📉"
        message += f"{medal} {prefix} *{sector['displayName']}* `{sign}{change_str}%`\n"

    message += "\n💡 _由傑克 AI 每四小時自動監控資金流向_"

    keyboard = {
        "inline_keyboard": [
            [{"text": "🔥 查看族群熱力圖 (點我)", "url": "https://www.coingecko.com/zh-tw/categories#key-stats"}]
        ]
    }
    send_telegram_message(message, TG_THREAD_IDS["sector_ranking"], reply_markup=keyboard)


# ==================== 2. 巨鯨與大戶持倉動向 ====================

def fetch_global_account_ratio(symbol: str, time_type: str) -> Optional[Dict]:
    """獲取全局帳戶比（散戶情緒）"""
    url = f"{CG_API_BASE}/api/futures/global-long-short-account-ratio/history"
    params = {
        "exchange": EXCHANGE,
        "symbol": symbol,
        "interval": time_type
    }
    headers = {
        "CG-API-KEY": CG_API_KEY,
        "Accept": "application/json"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.debug(f"全局帳戶比 API 請求失敗 - {symbol}: HTTP {response.status_code}（可能不在指定交易所）")
            return None
        
        data = response.json()
        if data.get('code') not in ['0', 0, 200, '200']:
            _code = data.get('code')
            if str(_code) == '400':
                logger.debug(f"全局帳戶比 API 返回 400 - {symbol}: 此幣種可能不在 Binance 上（預期行為）")
            else:
                logger.warning(f"全局帳戶比 API 返回錯誤 - {symbol}: code={_code}")
            return None
        
        return data
    except Exception as e:
        logger.error(f"獲取全局帳戶比時發生錯誤 - {symbol}: {str(e)}")
        return None


def fetch_top_account_ratio(symbol: str, time_type: str) -> Optional[Dict]:
    """獲取大戶帳戶比（大戶帳戶數）"""
    url = f"{CG_API_BASE}/api/futures/top-long-short-account-ratio/history"
    params = {
        "exchange": EXCHANGE,
        "symbol": symbol,
        "interval": time_type
    }
    headers = {
        "CG-API-KEY": CG_API_KEY,
        "Accept": "application/json"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            return None
        
        data = response.json()
        if data.get('code') not in ['0', 0, 200, '200']:
            return None
        
        return data
    except Exception as e:
        logger.error(f"獲取大戶帳戶比時發生錯誤 - {symbol}: {str(e)}")
        return None


def fetch_top_position_ratio(symbol: str, time_type: str) -> Optional[Dict]:
    """獲取大戶持倉比（巨鯨部位）"""
    url = f"{CG_API_BASE}/api/futures/top-long-short-position-ratio/history"
    params = {
        "exchange": EXCHANGE,
        "symbol": symbol,
        "interval": time_type
    }
    headers = {
        "CG-API-KEY": CG_API_KEY,
        "Accept": "application/json"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            return None
        
        data = response.json()
        if data.get('code') not in ['0', 0, 200, '200']:
            return None
        
        return data
    except Exception as e:
        logger.error(f"獲取大戶持倉比時發生錯誤 - {symbol}: {str(e)}")
        return None


def get_latest_data_point(data: Dict) -> Optional[Dict]:
    """從 API 響應中提取最新的數據點"""
    if not data or 'data' not in data:
        return None
    
    data_list = data['data']
    if isinstance(data_list, list) and len(data_list) > 0:
        return data_list[-1]
    
    return data_list if isinstance(data_list, dict) else None


def analyze_data(all_data: Dict) -> Optional[Dict]:
    """分析數據並判斷市場狀況（改進版：更合理的閾值和白話描述）"""
    global_point = get_latest_data_point(all_data.get('global'))
    global_ratio = global_point.get('global_account_long_short_ratio') if global_point else None
    
    top_account_point = get_latest_data_point(all_data.get('topAccount'))
    top_account_ratio = top_account_point.get('top_account_long_short_ratio') if top_account_point else None
    
    top_position_point = get_latest_data_point(all_data.get('topPosition'))
    top_position_ratio = top_position_point.get('top_position_long_short_ratio') if top_position_point else None
    
    if global_ratio is None and top_position_ratio is None:
        logger.warning("無法提取必要的數據指標")
        return None
    
    # 改進的診斷邏輯：使用更合理的閾值，並提供更白話的描述
    diagnosis = ""
    diagnosis_detail = ""
    risk_level = "中等"
    
    # 計算散戶和巨鯨的傾向
    retail_bullish = global_ratio > 1.2 if global_ratio else False
    retail_bearish = global_ratio < 0.9 if global_ratio else False
    whale_bullish = top_position_ratio > 1.15 if top_position_ratio else False
    whale_bearish = top_position_ratio < 0.9 if top_position_ratio else False
    
    # 判斷市場狀況
    if global_ratio is not None and top_position_ratio is not None:
        # 情況1：散戶極度看多，巨鯨看空（危險信號）
        if global_ratio > 1.5 and top_position_ratio < 0.95:
            diagnosis = "⚠️ 散戶狂熱，巨鯨撤退"
            diagnosis_detail = f"散戶多空比 {global_ratio:.2f} 極度偏多（做多），但巨鯨持倉比 {top_position_ratio:.2f} 偏空（做空）。這是典型的「散戶接盤，巨鯨出貨」信號，價格可能面臨大幅回調。"
            risk_level = "高"
        # 情況2：散戶恐慌，巨鯨抄底（機會信號）
        elif global_ratio < 0.85 and top_position_ratio > 1.2:
            diagnosis = "✅ 散戶恐慌，巨鯨抄底"
            diagnosis_detail = f"散戶多空比 {global_ratio:.2f} 極度偏空（做空），但巨鯨持倉比 {top_position_ratio:.2f} 強勢偏多（做多）。這是「散戶割肉，巨鯨掃貨」的底部信號，可能是抄底機會。"
            risk_level = "低"
        # 情況3：散戶看多，巨鯨也看多（健康上漲）
        elif global_ratio > 1.1 and top_position_ratio > 1.1:
            diagnosis = "📈 散戶與巨鯨同步看多"
            diagnosis_detail = f"散戶多空比 {global_ratio:.2f} 和巨鯨持倉比 {top_position_ratio:.2f} 都偏多（做多方向）。市場情緒一致看漲，上漲動能較強，但需注意過熱風險。"
            risk_level = "中低"
        # 情況4：散戶看空，巨鯨也看空（下跌趨勢）
        elif global_ratio < 0.95 and top_position_ratio < 0.95:
            diagnosis = "📉 散戶與巨鯨同步看空"
            diagnosis_detail = f"散戶多空比 {global_ratio:.2f} 和巨鯨持倉比 {top_position_ratio:.2f} 都偏空（做空方向）。市場情緒一致看跌，下跌壓力較大，建議謹慎操作。"
            risk_level = "高"
        # 情況5：散戶看多，巨鯨中性（需觀察）
        elif global_ratio > 1.15 and 0.95 <= top_position_ratio <= 1.15:
            diagnosis = "🔍 散戶看多，巨鯨觀望"
            diagnosis_detail = f"散戶多空比 {global_ratio:.2f} 偏多（做多方向），但巨鯨持倉比 {top_position_ratio:.2f} 保持中性。巨鯨可能在等待更好的進場時機，需密切觀察。"
            risk_level = "中"
        # 情況6：散戶看空，巨鯨中性（需觀察）
        elif global_ratio < 0.9 and 0.95 <= top_position_ratio <= 1.15:
            diagnosis = "🔍 散戶看空，巨鯨觀望"
            diagnosis_detail = f"散戶多空比 {global_ratio:.2f} 偏空（做空方向），但巨鯨持倉比 {top_position_ratio:.2f} 保持中性。巨鯨可能在等待更好的進場時機，需密切觀察。"
            risk_level = "中"
        # 情況7：散戶中性，巨鯨看多（機會信號）
        elif 0.95 <= global_ratio <= 1.15 and top_position_ratio > 1.15:
            diagnosis = "💎 散戶中性，巨鯨看多"
            diagnosis_detail = f"散戶多空比 {global_ratio:.2f} 保持中性，但巨鯨持倉比 {top_position_ratio:.2f} 偏多（做多方向）。巨鯨可能提前布局，這是較好的跟隨信號。"
            risk_level = "中低"
        # 情況8：散戶中性，巨鯨看空（警告信號）
        elif 0.95 <= global_ratio <= 1.15 and top_position_ratio < 0.9:
            diagnosis = "⚠️ 散戶中性，巨鯨看空"
            diagnosis_detail = f"散戶多空比 {global_ratio:.2f} 保持中性，但巨鯨持倉比 {top_position_ratio:.2f} 偏空（做空方向）。巨鯨可能提前減倉，需警惕下跌風險。"
            risk_level = "中高"
        # 情況9：雙方都接近中性（平衡狀態）
        else:
            diagnosis = "⚖️ 市場平衡"
            diagnosis_detail = f"散戶多空比 {global_ratio:.2f} 和巨鯨持倉比 {top_position_ratio:.2f} 都接近中性。市場處於平衡狀態，等待明確方向。"
            risk_level = "中等"
    elif global_ratio is not None:
        # 只有散戶數據
        if global_ratio > 1.3:
            diagnosis = "👤 散戶極度看多"
            diagnosis_detail = f"散戶多空比 {global_ratio:.2f} 極度偏多（做多方向），市場情緒過熱，需警惕回調風險。"
            risk_level = "中高"
        elif global_ratio > 1.1:
            diagnosis = "👤 散戶看多"
            diagnosis_detail = f"散戶多空比 {global_ratio:.2f} 偏多（做多方向），市場情緒偏樂觀。"
            risk_level = "中"
        elif global_ratio < 0.8:
            diagnosis = "👤 散戶極度看空"
            diagnosis_detail = f"散戶多空比 {global_ratio:.2f} 極度偏空（做空方向），市場情緒恐慌，可能是底部信號。"
            risk_level = "中"
        elif global_ratio < 0.95:
            diagnosis = "👤 散戶看空"
            diagnosis_detail = f"散戶多空比 {global_ratio:.2f} 偏空（做空方向），市場情緒偏悲觀。"
            risk_level = "中"
        else:
            diagnosis = "👤 散戶中性"
            diagnosis_detail = f"散戶多空比 {global_ratio:.2f} 接近中性，市場情緒平衡。"
            risk_level = "中等"
    elif top_position_ratio is not None:
        # 只有巨鯨數據
        if top_position_ratio > 1.3:
            diagnosis = "🐳 巨鯨強勢看多"
            diagnosis_detail = f"巨鯨持倉比 {top_position_ratio:.2f} 強勢偏多（做多方向），大戶積極建倉，可能是上漲信號。"
            risk_level = "低"
        elif top_position_ratio > 1.1:
            diagnosis = "🐳 巨鯨看多"
            diagnosis_detail = f"巨鯨持倉比 {top_position_ratio:.2f} 偏多（做多方向），大戶傾向做多（看漲）。"
            risk_level = "中低"
        elif top_position_ratio < 0.8:
            diagnosis = "🐳 巨鯨強勢看空"
            diagnosis_detail = f"巨鯨持倉比 {top_position_ratio:.2f} 偏空（做空方向），大戶積極減倉，需警惕下跌風險。"
            risk_level = "高"
        elif top_position_ratio < 0.95:
            diagnosis = "🐳 巨鯨看空"
            diagnosis_detail = f"巨鯨持倉比 {top_position_ratio:.2f} 偏空（做空方向），大戶傾向做空（看跌）。"
            risk_level = "中高"
        else:
            diagnosis = "🐳 巨鯨中性"
            diagnosis_detail = f"巨鯨持倉比 {top_position_ratio:.2f} 接近中性，大戶保持觀望。"
            risk_level = "中等"
    else:
        diagnosis = "❓ 數據不足"
        diagnosis_detail = "無法獲取足夠的數據進行分析。"
        risk_level = "未知"
    
    return {
        'globalRatio': global_ratio,
        'topAccountRatio': top_account_ratio,
        'topPositionRatio': top_position_ratio,
        'diagnosis': diagnosis,
        'diagnosisDetail': diagnosis_detail,
        'riskLevel': risk_level
    }


def format_symbol_message(symbol: str, analysis: Dict) -> str:
    """格式化單個幣種的訊息片段（改進版：更白話、更直觀）"""
    coin_symbol = symbol.replace("USDT", "")
    message = f"\n🐋 【{coin_symbol}】\n"
    message += "━━━━━━━━━━━━━━━━━━━\n"
    
    # 顯示數據指標（簡化顯示）
    if analysis.get('globalRatio') is not None:
        gr = analysis['globalRatio']
        # 用更直觀的方式顯示
        if gr > 1.2:
            emoji = "🔥"
            status = "極度看多（偏做多／看漲）"
        elif gr > 1.05:
            emoji = "📈"
            status = "看多（做多方向）"
        elif gr < 0.85:
            emoji = "❄️"
            status = "極度看空（偏做空／看跌）"
        elif gr < 0.95:
            emoji = "📉"
            status = "看空（做空方向）"
        else:
            emoji = "➡️"
            status = "中性"
        message += f"👤 散戶情緒：{emoji} {status} (多空比 {gr:.2f})\n"
    
    if analysis.get('topAccountRatio') is not None:
        tar = analysis['topAccountRatio']
        message += f"📊 大戶帳戶比：{tar:.2f}\n"
    
    if analysis.get('topPositionRatio') is not None:
        tpr = analysis['topPositionRatio']
        # 用更直觀的方式顯示
        if tpr > 1.2:
            emoji = "🟢"
            status = "強勢看多（做多方向）"
        elif tpr > 1.05:
            emoji = "🟡"
            status = "看多（做多方向）"
        elif tpr < 0.85:
            emoji = "🔴"
            status = "強勢看空（做空方向）"
        elif tpr < 0.95:
            emoji = "🟠"
            status = "看空（做空方向）"
        else:
            emoji = "⚪"
            status = "中性"
        message += f"🐳 巨鯨部位：{emoji} {status} (持倉比 {tpr:.2f})\n"
    
    # 顯示診斷結果（更突出）
    message += f"\n🚩 市場診斷：\n"
    message += f"   {analysis.get('diagnosis', '無法判斷')}\n"
    
    if analysis.get('diagnosisDetail'):
        message += f"\n💡 解讀：\n"
        message += f"   {analysis['diagnosisDetail']}\n"
    
    # 顯示風險等級
    risk_level = analysis.get('riskLevel', '未知')
    risk_emoji = {
        '低': '🟢',
        '中低': '🟡',
        '中等': '🟠',
        '中高': '🟠',
        '高': '🔴',
        '未知': '⚪'
    }
    message += f"\n⚠️ 風險等級：{risk_emoji.get(risk_level, '⚪')} {risk_level}\n"
    
    return message


def fetch_stablecoin_marketcap_history() -> Optional[List[Dict]]:
    """獲取穩定幣市值歷史數據"""
    url = "https://open-api-v4.coinglass.com/api/index/stableCoin-marketCap-history"
    headers = {
        "CG-API-KEY": CG_API_KEY,
        "accept": "application/json"
    }
    
    try:
        logger.info(f"正在調用穩定幣市值 API: {url}")
        response = requests.get(url, headers=headers, timeout=10)
        logger.info(f"穩定幣市值 API 響應狀態碼: {response.status_code}")
        
        if response.status_code != 200:
            logger.error(f"穩定幣市值 API 返回狀態碼: {response.status_code}")
            logger.error(f"響應內容: {response.text[:500]}")
            return None
        
        data = response.json()
        logger.info(f"穩定幣市值 API 返回數據結構: code={data.get('code')}, msg={data.get('msg')}")
        # 輸出完整的數據結構以便調試
        logger.info(f"完整響應結構（前2000字符）: {json.dumps(data, ensure_ascii=False, indent=2)[:2000]}")
        
        # 檢查返回碼
        if data.get('code') not in ['0', 0, 200, '200', None]:
            error_msg = data.get('msg') or data.get('message') or '未知錯誤'
            logger.error(f"穩定幣市值 API 返回錯誤: {error_msg} (code: {data.get('code')})")
            return None
        
        # 返回數據列表（根據實際 API 響應結構）
        # API 返回結構: { "code": "0", "data": { "data_list": [...] } }
        data_content = data.get('data')
        
        if isinstance(data_content, dict):
            # 檢查 data_list 字段
            data_list = data_content.get('data_list')
            if isinstance(data_list, list) and len(data_list) > 0:
                logger.info(f"成功獲取穩定幣市值數據: {len(data_list)} 條記錄")
                # 轉換數據格式：將每個 { "USDT": value } 轉換為標準格式
                formatted_list = []
                for idx, item in enumerate(data_list):
                    if isinstance(item, dict):
                        # 計算總市值（加總所有穩定幣）
                        total_mcap = sum(float(v) for v in item.values() if isinstance(v, (int, float)))
                        # 或者只取 USDT（根據需求）
                        usdt_mcap = item.get('USDT') or item.get('usdt') or 0
                        
                        # 使用總市值或 USDT 市值（優先使用總市值）
                        mcap_value = total_mcap if total_mcap > 0 else float(usdt_mcap)
                        
                        # 構建標準格式的數據點
                        # 注意：API 可能沒有時間戳，使用索引作為時間順序（最新的在最後）
                        formatted_item = {
                            'marketCap': mcap_value,
                            'market_cap': mcap_value,
                            'value': mcap_value,
                            'time': None,  # 如果 API 沒有提供時間戳
                            'timestamp': None,
                            'index': idx  # 用於排序
                        }
                        formatted_list.append(formatted_item)
                
                logger.info(f"格式化後的數據: {len(formatted_list)} 條記錄")
                return formatted_list
        
        # 如果 data 是列表，直接返回（但需要格式化）
        if isinstance(data_content, list) and len(data_content) > 0:
            logger.info(f"data 是列表，直接返回: {len(data_content)} 條記錄")
            return data_content
        
        # 嘗試其他可能的字段
        for key in ['data_list', 'list', 'items', 'history', 'marketCap', 'market_cap', 'values', 'records']:
            if key in data:
                value = data[key]
                if isinstance(value, list) and len(value) > 0:
                    logger.info(f"從 {key} 字段獲取數據: {len(value)} 條記錄")
                    return value
        
        # 如果還是找不到，記錄完整的數據結構以便調試
        logger.warning(f"穩定幣市值 API 返回的數據格式不符合預期")
        logger.info(f"數據類型: {type(data_content)}")
        if isinstance(data_content, dict):
            logger.info(f"data 字典的鍵: {list(data_content.keys())}")
        logger.info(f"數據結構（前1000字符）: {json.dumps(data, ensure_ascii=False, indent=2)[:1000]}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"穩定幣市值 API 請求失敗: {str(e)}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"穩定幣市值 API 響應 JSON 解析失敗: {str(e)}")
        logger.error(f"響應內容: {response.text[:500] if 'response' in locals() else 'N/A'}")
        return None
    except Exception as e:
        logger.error(f"獲取穩定幣市值歷史失敗: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return None


def fetch_aggregated_stablecoin_oi_history(symbol: str = "BTC", interval: str = "1h") -> Optional[List[Dict]]:
    """獲取聚合穩定幣保證金持倉歷史數據"""
    url = "https://open-api-v4.coinglass.com/api/futures/open-interest/aggregated-stablecoin-history"
    params = {
        "exchange_list": "Binance",
        "symbol": symbol,
        "interval": interval
    }
    headers = {
        "CG-API-KEY": CG_API_KEY,
        "accept": "application/json"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.error(f"穩定幣 OI API 返回狀態碼: {response.status_code}")
            return None
        
        data = response.json()
        if data.get('code') not in ['0', 0, 200, '200']:
            logger.error(f"穩定幣 OI API 返回錯誤: {data.get('msg')}")
            return None
        
        # 返回數據列表
        data_list = data.get('data', [])
        if isinstance(data_list, list):
            return data_list
        return None
    except Exception as e:
        logger.error(f"獲取穩定幣 OI 歷史失敗: {str(e)}")
        return None


def calculate_marketcap_change(data_list: List[Dict]) -> Optional[Dict]:
    """計算穩定幣市值變化率（1小時和24小時）"""
    if not data_list or len(data_list) < 2:
        return None
    
    # 按時間戳或索引排序（最新的在最後）
    def get_sort_key(item):
        time_val = item.get('time') or item.get('timestamp')
        if time_val is not None:
            return time_val
        # 如果沒有時間戳，使用索引
        index_val = item.get('index')
        if index_val is not None:
            return index_val
        # 如果都沒有，返回 0（保持原順序）
        return 0
    
    sorted_data = sorted(data_list, key=get_sort_key)
    
    # 獲取最新值
    latest = sorted_data[-1]
    latest_mcap = latest.get('marketCap') or latest.get('market_cap') or latest.get('value')
    
    if latest_mcap is None:
        return None
    
    # 計算1小時和24小時變化
    # 如果數據沒有時間戳，使用數據點索引來估算
    # 假設數據是每小時一個點（或根據實際情況調整）
    one_hour_data = None
    twenty_four_hours_data = None
    
    if len(sorted_data) >= 2:
        # 如果數據有時間戳，使用時間戳
        if sorted_data[0].get('time') or sorted_data[0].get('timestamp'):
            now = get_taipei_time()
            one_hour_ago = now - timedelta(hours=1)
            one_hour_ago_ts = int(one_hour_ago.timestamp() * 1000)
            
            twenty_four_hours_ago = now - timedelta(hours=24)
            twenty_four_hours_ago_ts = int(twenty_four_hours_ago.timestamp() * 1000)
            
            for item in sorted_data:
                item_time = item.get('time') or item.get('timestamp', 0)
                if item_time <= one_hour_ago_ts:
                    one_hour_data = item
                if item_time <= twenty_four_hours_ago_ts:
                    twenty_four_hours_data = item
                else:
                    break
        else:
            # 如果沒有時間戳，使用索引來估算（假設數據是每小時一個點）
            # 1小時前 = 倒數第2個點（如果有的話）
            if len(sorted_data) >= 2:
                one_hour_data = sorted_data[-2]
            # 24小時前 = 倒數第25個點（如果有的話）
            if len(sorted_data) >= 25:
                twenty_four_hours_data = sorted_data[-25]
            elif len(sorted_data) >= 2:
                # 如果數據點不足24個，使用最早的數據點
                twenty_four_hours_data = sorted_data[0]
    
    result = {
        'latest_mcap': float(latest_mcap),
        'change_1h': None,
        'change_24h': None
    }
    
    # 計算1小時變化率
    if one_hour_data:
        one_hour_mcap = one_hour_data.get('marketCap') or one_hour_data.get('market_cap') or one_hour_data.get('value')
        if one_hour_mcap and one_hour_mcap > 0:
            result['change_1h'] = ((latest_mcap - one_hour_mcap) / one_hour_mcap) * 100
    
    # 計算24小時變化率
    if twenty_four_hours_data:
        twenty_four_hours_mcap = twenty_four_hours_data.get('marketCap') or twenty_four_hours_data.get('market_cap') or twenty_four_hours_data.get('value')
        if twenty_four_hours_mcap and twenty_four_hours_mcap > 0:
            result['change_24h'] = ((latest_mcap - twenty_four_hours_mcap) / twenty_four_hours_mcap) * 100
    
    return result


def calculate_oi_change(data_list: List[Dict]) -> Optional[Dict]:
    """計算穩定幣 OI 變化率（1小時和24小時）"""
    if not data_list or len(data_list) < 2:
        return None
    
    # 按時間戳排序
    sorted_data = sorted(data_list, key=lambda x: x.get('time', 0) or x.get('timestamp', 0))
    
    # 獲取最新值（使用 close 或 value）
    latest = sorted_data[-1]
    latest_oi = latest.get('close') or latest.get('value') or latest.get('openInterest')
    
    if latest_oi is None:
        return None
    
    # 計算1小時變化
    now = get_taipei_time()
    one_hour_ago = now - timedelta(hours=1)
    one_hour_ago_ts = int(one_hour_ago.timestamp() * 1000)
    
    one_hour_data = None
    for item in sorted_data:
        item_time = item.get('time') or item.get('timestamp', 0)
        if item_time <= one_hour_ago_ts:
            one_hour_data = item
        else:
            break
    
    # 計算24小時變化
    twenty_four_hours_ago = now - timedelta(hours=24)
    twenty_four_hours_ago_ts = int(twenty_four_hours_ago.timestamp() * 1000)
    
    twenty_four_hours_data = None
    for item in sorted_data:
        item_time = item.get('time') or item.get('timestamp', 0)
        if item_time <= twenty_four_hours_ago_ts:
            twenty_four_hours_data = item
        else:
            break
    
    result = {
        'latest_oi': float(latest_oi),
        'change_1h': None,
        'change_24h': None
    }
    
    # 計算1小時變化率
    if one_hour_data:
        one_hour_oi = one_hour_data.get('close') or one_hour_data.get('value') or one_hour_data.get('openInterest')
        if one_hour_oi and one_hour_oi > 0:
            result['change_1h'] = ((latest_oi - one_hour_oi) / one_hour_oi) * 100
    
    # 計算24小時變化率
    if twenty_four_hours_data:
        twenty_four_hours_oi = twenty_four_hours_data.get('close') or twenty_four_hours_data.get('value') or twenty_four_hours_data.get('openInterest')
        if twenty_four_hours_oi and twenty_four_hours_oi > 0:
            result['change_24h'] = ((latest_oi - twenty_four_hours_oi) / twenty_four_hours_oi) * 100
    
    return result


def _fetch_usdt_premium() -> Optional[float]:
    """查詢 USDT/USD 溢價率（正值=溢價=真實買盤，負值=折價=搬磚套利）。
    使用 Binance 公開 API 抓取 USDCUSDT 匯率，USDC 理論上 = 1 USD，
    故 premium = (1.0 / USDCUSDT - 1.0) * 100，
    USDCUSDT < 1.0 代表 1 USDC 買不到 1 USDT → USDT 溢價（需求旺盛）
    USDCUSDT > 1.0 代表 1 USDC > 1 USDT → USDT 折價（搬磚套利為主）
    """
    try:
        url = "https://api.binance.com/api/v3/ticker/price"
        resp = requests.get(url, params={"symbol": "USDCUSDT"}, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            usdc_usdt = float(data.get("price", 1.0))
            if usdc_usdt > 0:
                premium_pct = (1.0 / usdc_usdt - 1.0) * 100.0
                logger.info(f"[USDT溢價] USDCUSDT={usdc_usdt:.6f} → 溢價率={premium_pct:+.4f}%")
                return round(premium_pct, 4)
    except Exception as e:
        logger.warning(f"[USDT溢價] 查詢失敗: {e}")
    return None


def _make_fuel_bar(score: int, max_score: int = 5) -> str:
    """生成燃料進度條 ▓▓▓░░（滿分 5 格）"""
    filled = max(0, min(score, max_score))
    empty = max_score - filled
    return "▓" * filled + "░" * empty


def _fetch_smart_money_oi_split(symbol: str = "BTC") -> Dict[str, Any]:
    """聰明錢 OI 拆分：穩定幣保證金（專業資金）vs 幣本位保證金（散戶槓桿）。
    方案A：aggregated-stablecoin-history + aggregated-coin-margin-history（最精準）
    方案B：aggregated-history 總量（備援，無法拆分但至少有數據）
    回傳 {"stable_chg": float, "coin_chg": float, "smart_money": bool/None}
    """
    empty = {"stable_chg": None, "coin_chg": None, "smart_money": None, "data_source": "none"}
    base = symbol.upper().replace("USDT", "")
    params = {"symbol": base, "interval": "15m", "limit": 4}

    logger.debug(f"[聰明錢OI] 嘗試抓取穩定幣/幣本位OI分拆 symbol={base}")

    stable_bars, coin_bars = None, None
    try:
        j_s = _cg_get(CG_EP["oi_agg_stable"], params)
        rows_s = j_s.get("data") or j_s.get("list") or [] if j_s else []
        stable_bars = _parse_oi_bars_from_rows(rows_s) if rows_s else None
        logger.debug(f"[聰明錢OI] 穩定幣OI: {len(stable_bars) if stable_bars else 0}棒")
    except Exception as e_s:
        logger.debug(f"[聰明錢OI] 穩定幣OI異常: {e_s}")
    try:
        j_c = _cg_get(CG_EP["oi_agg_coin"], params)
        rows_c = j_c.get("data") or j_c.get("list") or [] if j_c else []
        coin_bars = _parse_oi_bars_from_rows(rows_c) if rows_c else None
        logger.debug(f"[聰明錢OI] 幣本位OI: {len(coin_bars) if coin_bars else 0}棒")
    except Exception as e_c:
        logger.debug(f"[聰明錢OI] 幣本位OI異常: {e_c}")

    stable_chg = coin_chg = None
    if stable_bars and len(stable_bars) >= 2 and stable_bars[-2] != 0:
        stable_chg = round((stable_bars[-1] - stable_bars[-2]) / stable_bars[-2] * 100, 3)
    if coin_bars and len(coin_bars) >= 2 and coin_bars[-2] != 0:
        coin_chg = round((coin_bars[-1] - coin_bars[-2]) / coin_bars[-2] * 100, 3)

    # 聰明錢判斷：穩定幣OI增加 且 幣本位OI不增加（或也增但穩定幣增更多）
    # → 專業機構在建倉，不是散戶借幣槓桿
    smart_money = None
    if stable_chg is not None and coin_chg is not None:
        if stable_chg > 0.2 and coin_chg <= 0.1:
            smart_money = True   # 聰明錢主導建倉
        elif coin_chg > 0.5 and stable_chg <= 0.1:
            smart_money = False  # 散戶槓桿主導
        else:
            smart_money = None   # 混合，無法判斷
    elif stable_chg is not None:
        smart_money = stable_chg > 0.2

    if smart_money is True:
        logger.info(f"[聰明錢OI✅] {base}: 穩定幣OI+{stable_chg:.3f}% 幣本位{coin_chg if coin_chg is not None else 'N/A'} → 專業資金建倉")
    elif smart_money is False:
        logger.info(f"[聰明錢OI⚠️] {base}: 幣本位OI+{coin_chg:.3f}% 穩定幣{stable_chg if stable_chg is not None else 'N/A'} → 散戶槓桿主導")

    return {"stable_chg": stable_chg, "coin_chg": coin_chg, "smart_money": smart_money, "data_source": "split"}


def _calc_fuel_score(mcap_15m: float, mcap_1h: float, oi_15m: float, oi_1h: float,
                     usdt_premium: Optional[float],
                     smart_money: Optional[bool] = None) -> int:
    """計算燃料積分（0-7），新增聰明錢維度：
    穩定幣 15m 流入 (+1)、穩定幣 1h 流入 (+1)、
    OI 15m 擴張 (+1)、OI 1h 擴張 (+1)、USDT 溢價 > 0.05% (+1)
    聰明錢OI主導（穩定幣>幣本位）(+1)、聰明錢強力確認(+1)
    """
    score = 0
    if mcap_15m > 0.01:
        score += 1
    if mcap_1h > 0.03:
        score += 1
    if oi_15m > 0.3:
        score += 1
    if oi_1h > 0.8:
        score += 1
    if usdt_premium is not None and usdt_premium > 0.05:
        score += 1
    if smart_money is True:
        score += 1
    if smart_money is True and oi_1h > 0.8:  # 聰明錢+持倉擴張雙確認
        score += 1
    return score


def buying_power_monitor():
    """【牛市燃料監控】資金進場=發車，判斷大盤動能（15m 高頻版 + 聰明錢指標）"""
    logger.info("開始執行牛市燃料監控（15m 高頻版 + 聰明錢拆分版）...")
    marketcap_data = fetch_stablecoin_marketcap_history()
    mcap_change = calculate_marketcap_change(marketcap_data) if marketcap_data else {}

    # 升級：同時抓取 15m 與 1h OI
    oi_data_15m = fetch_aggregated_stablecoin_oi_history("BTC", "15m")
    oi_data_1h = fetch_aggregated_stablecoin_oi_history("BTC", "1h")
    oi_change_15m = calculate_oi_change(oi_data_15m) if oi_data_15m else {}
    oi_change_1h = calculate_oi_change(oi_data_1h) if oi_data_1h else {}

    # 聰明錢拆分：穩定幣OI vs 幣本位OI
    smart_money_data = _fetch_smart_money_oi_split("BTC")
    stable_chg = smart_money_data.get("stable_chg")
    coin_chg = smart_money_data.get("coin_chg")
    smart_money = smart_money_data.get("smart_money")
    logger.info(f"[牛市燃料] 聰明錢OI拆分：穩定幣={stable_chg} 幣本位={coin_chg} 聰明錢={smart_money}")

    # 新增：恐懼貪婪 + BTC ETF流 + Coinbase溢價
    fg_data = fetch_fear_greed_index()
    etf_data = fetch_btc_etf_flow()
    cb_data = fetch_coinbase_premium()
    logger.info(f"[牛市燃料] 恐懼貪婪={fg_data.get('value')} ETF流={etf_data.get('direction')} CB溢價={cb_data.get('premium')}")

    if not mcap_change:
        logger.warning("牛市燃料監控：無法取得市值數據，跳過推播")
        return

    # 抓取 USDT 溢價率（正值=真實買盤，負值=搬磚套利）
    usdt_premium = _fetch_usdt_premium()

    mcap_1h = mcap_change.get("change_1h") or 0
    oi_15m_chg = (oi_change_15m.get("change_1h") or 0)
    oi_1h_chg = (oi_change_1h.get("change_1h") or 0)

    # 「USDT 溢價>0.05%」才視為真實買盤
    premium_boost = (usdt_premium is not None and usdt_premium > 0.05)
    if premium_boost:
        logger.info(f"[牛市燃料] USDT 溢價 {usdt_premium:+.4f}% > 0.05%，加權燃料等級")

    # 積分（升級至 7 分滿，引入聰明錢維度）
    fuel_score = _calc_fuel_score(mcap_1h, mcap_1h, oi_15m_chg, oi_1h_chg, usdt_premium, smart_money)
    # 附加維度：恐懼貪婪 + ETF流 + Coinbase溢價（各+1分，最高可達 10 分）
    fg_val = fg_data.get("value")
    if fg_val is not None:
        if fg_val >= 60:   # 貪婪偏多
            fuel_score += 1
        elif fg_val <= 25: # 極度恐懼=底部機會
            fuel_score += 1  # 極度恐懼也是加分（抄底機會）
    if etf_data.get("direction") == "inflow":
        fuel_score += 1    # ETF機構流入=強力買盤
    if cb_data.get("signal") == "bullish":
        fuel_score += 1    # Coinbase溢價=美國機構買入
    fuel_bar = _make_fuel_bar(fuel_score)

    # 根據積分決定主標籤（7 分制）
    if fuel_score >= 6:
        headline = "🔥 強力做多環境"
        advice = "聰明錢+資金+槓桿三重確認！全市場資金同步入場，主升段往往在此起爆。"
        bar_label = "燃料滿載"
    elif fuel_score >= 5:
        headline = "🚀 火力全開（聰明錢主導）" if smart_money else "🚀 火力全開 (雙重利好)"
        advice = "專業資金主導建倉（穩定幣OI擴張），跟隨機構方向偏多。" if smart_money else "資金 + 槓桿雙噴，回調就是買點！"
        bar_label = "高燃料"
    elif fuel_score >= 4:
        headline = "💰 資金進場 (現貨買盤)"
        advice = "場外資金流入，底部墊高，偏多操作。"
        bar_label = "中燃料"
    elif fuel_score >= 2:
        headline = "➡️ 震盪蓄力"
        advice = "多看少動，等待方向確認再出手。"
        bar_label = "低燃料"
    elif oi_1h_chg > 1.5 and smart_money is False:
        headline = "⚠️ 散戶槓桿堆疊 (高波動預警)"
        advice = "散戶幣本位OI激增，小心插針清洗。"
        bar_label = "危險燃料"
    elif oi_1h_chg > 1.5:
        headline = "⚠️ 槓桿過熱 (高波動預警)"
        advice = "只有槓桿在堆，小心插針畫門。"
        bar_label = "危險燃料"
    elif mcap_1h < -0.05:
        headline = "❄️ 資金抽離警報"
        advice = "資金正在撤退！反彈請謹慎，空頭考慮加碼。"
        bar_label = "無燃料"
    else:
        headline = "➡️ 震盪蓄力"
        advice = "多看少動，等待方向確認再出手。"
        bar_label = "低燃料"

    lines = []
    lines.append("⛽ *【牛市燃料儀表板】*")
    lines.append(f"🕐 {datetime.now(TAIPEI_TZ).strftime('%H:%M')} (台灣) | ⚡ 15M 高頻監控")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(f"*{headline}*")
    lines.append(f"燃料計：`{fuel_bar}` {fuel_score}/7 ({bar_label})")
    lines.append("")

    # USDT 溢價標籤
    if premium_boost:
        lines.append(f"🔥 *USDT 真實買盤確認* (`+{usdt_premium:.3f}%`溢價)")
    elif usdt_premium is not None and usdt_premium < -0.05:
        lines.append(f"⚠️ USDT 折價 `{usdt_premium:+.3f}%`：疑似搬磚套利，非真實買盤")
    elif usdt_premium is not None:
        lines.append(f"💱 USDT 溢價：`{usdt_premium:+.3f}%`（中性）")

    lines.append("")
    mcap_val = (mcap_change.get("latest_mcap") or 0) / 1_000_000_000
    mcap_emoji = "📈" if mcap_1h > 0 else "📉"
    lines.append("💵 *穩定幣（場外資金）*")
    lines.append(f"• 總量：`${mcap_val:.2f}B`")
    lines.append(f"• 1H 變動：{mcap_emoji} `{mcap_1h:+.3f}%`")

    # 聰明錢 OI 拆分區塊
    lines.append("")
    lines.append("🧠 *聰明錢 OI 分析*")
    if stable_chg is not None:
        _s_emoji = "🟢" if stable_chg > 0.1 else ("🔴" if stable_chg < -0.1 else "🟡")
        lines.append(f"• 穩定幣保證金(機構)：{_s_emoji} `{stable_chg:+.3f}%`")
    else:
        lines.append("• 穩定幣保證金：`數據不可用`")
    if coin_chg is not None:
        _c_emoji = "🟢" if coin_chg > 0.1 else ("🔴" if coin_chg < -0.1 else "🟡")
        lines.append(f"• 幣本位保證金(散戶)：{_c_emoji} `{coin_chg:+.3f}%`")
    else:
        lines.append("• 幣本位保證金：`數據不可用`")
    if smart_money is True:
        lines.append("• 🎯 *聰明錢主導*：機構/職業交易者正在建倉（穩定幣>幣本位）")
    elif smart_money is False:
        lines.append("• ⚠️ *散戶槓桿主導*：幣本位OI擴張，投機氣氛濃厚，注意清洗")
    else:
        lines.append("• ❓ 多空資金混合：無明顯方向")

    lines.append("")
    oi_val_1h = (oi_change_1h.get("latest_oi") or 0) / 1_000_000_000
    oi_val_15m = (oi_change_15m.get("latest_oi") or 0) / 1_000_000_000 if oi_change_15m else 0
    oi_emoji_15m = "🔥" if oi_15m_chg > 0 else "❄️"
    oi_emoji_1h = "🔥" if oi_1h_chg > 0 else "❄️"
    lines.append("🎰 *合約持倉（場內槓桿）*")
    if oi_val_15m > 0:
        lines.append(f"• 15m 快照：`${oi_val_15m:.2f}B` {oi_emoji_15m} `{oi_15m_chg:+.2f}%`")
    lines.append(f"• 1H 趨勢：`${oi_val_1h:.2f}B` {oi_emoji_1h} `{oi_1h_chg:+.2f}%`")

    # ── 機構資金區塊（Fear&Greed + BTC ETF + Coinbase溢價）──────────
    lines.append("")
    lines.append("🏦 *機構資金 & 市場情緒*")
    if fg_val is not None:
        lines.append(f"• 恐懼貪婪：{fg_data.get('emoji','❓')} `{fg_val}` {fg_data.get('label','')}")
    if etf_data.get("label"):
        lines.append(f"• BTC ETF：{etf_data['label']}")
    if etf_data.get("total_assets_usd"):
        lines.append(f"• ETF總資產：`${etf_data['total_assets_usd']/1e9:.1f}B`")
    if cb_data.get("label"):
        lines.append(f"• {cb_data['label']}")
    if not any([fg_val, etf_data.get("label"), cb_data.get("label")]):
        lines.append("• 機構指標暫無資料")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(f"💡 *船長指令*：{advice}")

    msg = "\n".join(lines)
    keyboard = {"inline_keyboard": [[{"text": "💰 查看資金流向圖表", "url": "https://www.coinglass.com/zh-TW/pro/futures/OpenInterest"}]]}
    send_telegram_message(msg, TG_THREAD_IDS.get("buying_power_monitor", 246), parse_mode="Markdown", reply_markup=keyboard)
    logger.info(f"牛市燃料監控推播完成（燃料積分={fuel_score}/5）")


# 保留舊函數名稱以向後兼容
def fetch_whale_position():
    """已廢棄：請使用 buying_power_monitor()"""
    logger.warning("fetch_whale_position() 已廢棄，請使用 buying_power_monitor()")
    buying_power_monitor()


def fetch_whale_position_old():
    """主執行函數：巨鯨持倉監控（舊版本，保留作為備份）"""
    logger.info("開始執行巨鯨持倉監控...")
    
    all_analyses = []
    
    for symbol in SYMBOLS:
        try:
            logger.info(f"正在處理 {symbol}...")
            
            global_data = fetch_global_account_ratio(symbol, TIME_TYPE)
            top_account_data = fetch_top_account_ratio(symbol, TIME_TYPE)
            top_position_data = fetch_top_position_ratio(symbol, TIME_TYPE)
            
            all_data = {
                'global': global_data,
                'topAccount': top_account_data,
                'topPosition': top_position_data
            }
            
            analysis = analyze_data(all_data)
            all_analyses.append(analysis)
            
            time.sleep(2)  # 避免請求過於頻繁
            
        except Exception as e:
            logger.error(f"處理 {symbol} 時發生錯誤: {str(e)}")
            all_analyses.append(None)
    
    # 過濾掉失敗的分析結果
    valid_analyses = []
    valid_symbols = []
    
    for i, analysis in enumerate(all_analyses):
        if analysis is not None:
            valid_analyses.append(analysis)
            valid_symbols.append(SYMBOLS[i])
    
    if len(valid_analyses) == 0:
        logger.error("所有幣種數據獲取失敗，無法發送訊息")
        return
    
    # 格式化合併訊息（改進版：更白話、更實用）
    now = get_taipei_time()
    time_str = format_datetime(now)
    
    message = "🐋 *【巨鯨持倉動向】*\n"
    message += "━━━━━━━━━━━━━━━━━━━\n"
    message += "\n"
    
    for i, symbol in enumerate(SYMBOLS):
        if all_analyses[i] is not None:
            analysis = all_analyses[i]
            coin_symbol = symbol.replace("USDT", "")
            
            # 簡化顯示（白話簡短）
            message += f"*【{coin_symbol}】*\n"
            
            # 散戶情緒（簡化）
            if analysis.get('globalRatio') is not None:
                gr = analysis['globalRatio']
                if gr > 1.2:
                    retail_status = "🔥 極度看多（偏做多）"
                elif gr > 1.05:
                    retail_status = "📈 看多（做多方向）"
                elif gr < 0.85:
                    retail_status = "❄️ 極度看空（偏做空）"
                elif gr < 0.95:
                    retail_status = "📉 看空（做空方向）"
                else:
                    retail_status = "➡️ 中性"
                message += f"散戶：{retail_status}\n"
            
            # 巨鯨部位（簡化）
            if analysis.get('topPositionRatio') is not None:
                tpr = analysis['topPositionRatio']
                if tpr > 1.2:
                    whale_status = "🟢 強勢看多（做多方向）"
                elif tpr > 1.05:
                    whale_status = "🟡 看多（做多方向）"
                elif tpr < 0.85:
                    whale_status = "🔴 強勢看空（做空方向）"
                elif tpr < 0.95:
                    whale_status = "🟠 看空（做空方向）"
                else:
                    whale_status = "⚪ 中性"
                message += f"巨鯨：{whale_status}\n"
            
            # 市場診斷（簡化）
            diagnosis = analysis.get('diagnosis', '無法判斷')
            message += f"診斷：{diagnosis}\n"
            message += "\n"
    
    # 簡化的操作建議（白話，做多=看漲、做空=看跌）
    message += "━━━━━━━━━━━━━━━━━━━\n"
    message += "💡 *操作建議*：\n"
    message += "• 散戶狂熱(做多)+巨鯨撤退 = 危險⚠️\n"
    message += "• 散戶恐慌(做空)+巨鯨抄底(做多) = 機會✅\n"
    message += "• 散戶與巨鯨同步做多／同步做空 = 趨勢延續📈\n"
    message += "━━━━━━━━━━━━━━━━━━━\n"
    message += f"⏰ 更新時間：{time_str}"
    
    send_telegram_message(message, TG_THREAD_IDS['whale_position'], parse_mode="Markdown")


# ==================== 3. 持倉變化篩選器 ====================

def fetch_bingx_contracts() -> Tuple[Set[str], Dict[str, str], List[str]]:
    """
    開頭拉 BingX 有支援的合約交易對：GET /openApi/swap/v2/quote/contracts。
    回傳 (allowed_base_set, base_to_symbol, bases_for_price)。
    - allowed_base_set: 用於過濾的 base 集合。
    - base_to_symbol: base_upper -> 正確的 BingX symbol（如 1000PEPE-USDT），供 K 線/費率 API 使用。
    - bases_for_price: 每個合約一個 asset（用於向 BingX 取 15m 價格），不重複。
    """
    allowed: Set[str] = set()
    base_to_symbol: Dict[str, str] = {}
    bases_for_price: List[str] = []
    url = "https://open-api.bingx.com/openApi/swap/v2/quote/contracts"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return allowed, base_to_symbol, bases_for_price
        j = r.json()
        if j.get("code") != 0:
            return allowed, base_to_symbol, bases_for_price
        data = j.get("data", [])
        if not isinstance(data, list):
            return allowed, base_to_symbol, bases_for_price
        for item in data:
            if not isinstance(item, dict):
                continue
            sym = item.get("symbol") or item.get("symbolName")
            asset = item.get("asset") or ""
            if not sym or not asset:
                continue
            status = item.get("status", 1)
            if status != 1:
                continue
            asset_upper = asset.strip().upper()
            allowed.add(asset_upper)
            base_to_symbol[asset_upper] = sym.strip()
            bases_for_price.append(asset_upper)
            if asset_upper.startswith("1000") and len(asset_upper) > 4:
                short_base = asset_upper[4:]
                allowed.add(short_base)
                base_to_symbol[short_base] = sym.strip()
        return allowed, base_to_symbol, bases_for_price
    except Exception as e:
        logger.warning(f"BingX contracts API 失敗: {e}")
        return allowed, base_to_symbol, bases_for_price


def fetch_supported_futures_coins() -> List[str]:
    """獲取 BingX 交易所支援的合約幣種列表（應該有 600+ 個）"""
    url = "https://open-api-v4.coinglass.com/api/futures/supported-exchange-pairs"
    headers = {
        "CG-API-KEY": CG_API_KEY,
        "accept": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.error(f"supported-exchange-pairs API error: {response.status_code}")
            return []
        
        result = response.json()
        data = result.get('data', result)
        
        # API 返回的是字典結構：{"BingX": [{"instrument_id": "BTCUSDT", "base_asset": "BTC", ...}, ...]}
        if not isinstance(data, dict):
            logger.error(f"API 返回數據格式錯誤，預期字典但得到: {type(data)}")
            return []
        
        # 調試：記錄可用的交易所
        exchanges = list(data.keys())
        logger.info(f"API 返回的交易所: {exchanges[:10]}... (共 {len(exchanges)} 個)")
        
        # 查找 BingX（嘗試多種可能的鍵名）
        bingx_data = None
        for key in data.keys():
            if 'bingx' in str(key).lower() or 'bing' in str(key).lower():
                bingx_data = data[key]
                logger.info(f"找到 BingX 數據，鍵名: {key}")
                break
        
        if not bingx_data:
            logger.error(f"未找到 BingX 數據，可用交易所: {exchanges}")
            return []
        
        if not isinstance(bingx_data, list):
            logger.error(f"BingX 數據格式錯誤，預期列表但得到: {type(bingx_data)}")
            return []
        
        # 提取幣種符號
        symbols = []
        for item in bingx_data:
            if not isinstance(item, dict):
                continue
            
            # 優先使用 base_asset（例如 "BTC"）
            symbol = item.get('base_asset') or item.get('baseAsset') or item.get('base')
            
            # 如果沒有 base_asset，從 instrument_id 提取（例如 "BTCUSDT" 或 "BTC-USDT" -> "BTC"）
            if not symbol:
                instrument_id = item.get('instrument_id') or item.get('instrumentId') or item.get('symbol') or item.get('pair') or ''
                if instrument_id:
                    # 處理多種格式：BTCUSDT, BTC-USDT, BTC_USDT 等
                    symbol = instrument_id.replace('USDT', '').replace('USDT-PERP', '').replace('-PERP', '').replace('_USDT', '').replace('-USDT', '').replace('_', '').upper()
            
            if symbol and symbol not in symbols:
                # 過濾無效符號（如 NCCONATURALGAS2USD 等導致 OI 無數據）
                sym_upper = symbol.upper()
                if len(symbol) <= 12 and "2USD" not in sym_upper:
                    symbols.append(symbol)
        
        logger.info(f"從 BingX API 獲取到 {len(symbols)} 個合約幣種")
        return symbols
    except Exception as e:
        logger.error(f"獲取 BingX 合約幣種列表失敗: {str(e)}")
        import traceback
        logger.error(f"錯誤詳情: {traceback.format_exc()}")
        return []


# ── CoinGlass exchange-pairs BingX 支援快取 ────────────────────────────────
_cg_bingx_bases_cache: Dict[str, Any] = {"ts": 0.0, "bases": set()}
_CG_BINGX_BASES_TTL = 3600  # 1 小時快取，交易所上幣不頻繁

# ── 幣種→交易所反向對照表（由 fetch_cg_bingx_supported_bases 順帶建立）──────
# 格式：{"BTC": {"Binance", "OKX", "Bybit", "BingX", ...}, "ULTIMA": {"BingX"}, ...}
_cg_full_exchange_map: Dict[str, Set[str]] = {}


def fetch_cg_bingx_supported_bases() -> Set[str]:
    """從 CoinGlass /api/futures/supported-exchange-pairs 取得 BingX 支援的幣種 base set。
    帶 1 小時 TTL 快取，避免每輪重複呼叫。
    回傳格式：{"BTC", "ETH", "SOL", ...}（大寫 base，已去掉 USDT/- 等後綴）
    """
    global _cg_bingx_bases_cache
    now = time.time()
    if now - _cg_bingx_bases_cache["ts"] < _CG_BINGX_BASES_TTL and _cg_bingx_bases_cache["bases"]:
        return _cg_bingx_bases_cache["bases"]

    if not CG_API_KEY:
        return set()

    global _cg_full_exchange_map  # global 必須在函數最頂部，任何賦值之前
    bingx_bases: Set[str] = set()
    try:
        _respect_coinglass_rate_limit()
        r = requests.get(
            f"{CG_API_BASE}/api/futures/supported-exchange-pairs",
            headers={"CG-API-KEY": CG_API_KEY, "accept": "application/json"},
            timeout=15,
        )
        if r.status_code != 200:
            logger.warning(f"[CG支援查詢] supported-exchange-pairs HTTP {r.status_code}")
            return bingx_bases
        j = r.json()
        data = j.get("data", j)

        # 回應格式 A：dict keyed by exchange name
        # {"BingX": [{"instrument_id": "BTCUSDT", "base_asset": "BTC"}, ...], ...}
        if isinstance(data, dict):
            # 同時建立全局 coin→exchanges 反向對照表（只建立一次，供 ABC fallback 快速跳過）
            _tmp_map: Dict[str, Set[str]] = {}
            for ex_name, ex_pairs in data.items():
                if not isinstance(ex_pairs, list):
                    continue
                for item in ex_pairs:
                    _b = (item.get("base_asset") or item.get("baseAsset") or item.get("base") or "")
                    if not _b:
                        _inst = (item.get("instrument_id") or item.get("instrumentId")
                                 or item.get("symbol") or item.get("pair") or "")
                        _b = (_inst.replace("USDT", "").replace("USDT-PERP", "")
                              .replace("-PERP", "").replace("_USDT", "")
                              .replace("-USDT", "").replace("-", "").replace("_", "").upper())
                    _b = _b.strip().upper()
                    if _b and len(_b) <= 12:
                        _tmp_map.setdefault(_b, set()).add(ex_name)
            if _tmp_map:
                _cg_full_exchange_map = _tmp_map
                _ex_names = sorted(data.keys())
                logger.info(
                    f"[CG支援查詢] 反向對照表建立完成：{len(_tmp_map)} 個幣種跨 {len(data)} 個交易所"
                    f" | 交易所名稱樣本: {_ex_names[:10]}"
                )

            bingx_key = next((k for k in data if "bingx" in k.lower() or "bing" in k.lower()), None)
            if bingx_key:
                for item in (data[bingx_key] or []):
                    base = (
                        item.get("base_asset") or item.get("baseAsset") or item.get("base") or ""
                    )
                    if not base:
                        inst = (item.get("instrument_id") or item.get("instrumentId")
                                or item.get("symbol") or item.get("pair") or "")
                        base = (inst.replace("USDT", "").replace("USDT-PERP", "")
                                .replace("-PERP", "").replace("_USDT", "")
                                .replace("-USDT", "").replace("-", "").replace("_", "").upper())
                    base = base.strip().upper()
                    if base and len(base) <= 12:
                        bingx_bases.add(base)
                logger.info(
                    f"[CG支援查詢] BingX ({bingx_key}) 支援 {len(bingx_bases)} 個幣種"
                    f"（來源：CoinGlass supported-exchange-pairs）"
                )
            else:
                logger.warning(f"[CG支援查詢] 回應中未找到 BingX key，可用 key: {list(data.keys())[:10]}")

        # 回應格式 B：list of {"symbol": "BTC", "exchanges": [...]}
        elif isinstance(data, list):
            _tmp_map_b: Dict[str, Set[str]] = {}
            for item in data:
                exch_list = item.get("exchanges") or item.get("exchangeList") or []
                sym = (item.get("symbol") or item.get("coin") or "").strip().upper()
                if sym and len(sym) <= 12:
                    for _e in exch_list:
                        _tmp_map_b.setdefault(sym, set()).add(str(_e))
                    if any("bingx" in str(e).lower() for e in exch_list):
                        bingx_bases.add(sym)
            if _tmp_map_b:
                _cg_full_exchange_map = _tmp_map_b
            logger.info(f"[CG支援查詢] BingX 支援 {len(bingx_bases)} 個幣種（格式B）")

    except Exception as e:
        logger.warning(f"[CG支援查詢] fetch_cg_bingx_supported_bases 異常: {e}")

    if bingx_bases:
        _cg_bingx_bases_cache = {"ts": now, "bases": bingx_bases}
    return bingx_bases


def get_major_exchanges_for_coin(base: str, pool: Optional[List[str]] = None) -> List[str]:
    """
    從 _cg_full_exchange_map 快取查詢 pool 內哪些大所支援該幣種。

    判斷邏輯：
    1. 快取未建立 → 回傳完整 pool（保守，不誤封鎖）
    2. 幣不在 map 裡 → 回傳完整 pool（保守，可能是新幣或 map 有缺口）
    3. 幣在 map 裡但不在 pool 的任何交易所 → 回傳 []（已確認不支援，跳過）
    4. 幣在 map 裡且部分匹配 → 只回傳有支援的交易所

    範例：
        BTC    → ["Binance", "OKX", "Bybit"]  (三所都支援，全回)
        KABUTO → []                             (只在 BingX，Binance/OKX/Bybit 皆無，跳過)
    """
    if pool is None:
        pool = ["Binance", "OKX", "Bybit"]
    if not _cg_full_exchange_map:                     # 快取未建立，保守不縮減
        return pool
    base_upper = base.upper()
    if base_upper not in _cg_full_exchange_map:       # 幣不在 map，可能是新幣，保守回傳
        return pool
    supported = _cg_full_exchange_map[base_upper]
    filtered = [ex for ex in pool if ex in supported]
    # 若 pool 含 BingX 且 BingX 支援該幣，確保保留
    if "BingX" in pool and any("bingx" in s.lower() for s in supported):
        if "BingX" not in filtered:
            filtered.append("BingX")
    # filtered 可能為空（幣種確認不在這些交易所），直接回傳空 → for loop 0 次，立即跳過
    return filtered


# Fallback 時僅記錄第一次 Binance 失敗原因，避免刷屏
_binance_fallback_first_failure_logged = False


def fetch_price_change_15m_binance(symbol: str) -> Optional[float]:
    """用 Binance 合約 API 取得 15 分鐘漲跌幅%（含詳細錯誤代碼診斷）"""
    global _binance_fallback_first_failure_logged
    # 移除特殊符號並轉大寫，確保格式正確
    clean_symbol = symbol.replace("-", "").replace("_", "").upper()
    sym = f"{clean_symbol}USDT" if not clean_symbol.endswith("USDT") else clean_symbol
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {"symbol": sym, "interval": "15m", "limit": 2}
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if not isinstance(data, list) or len(data) < 2:
                if not _binance_fallback_first_failure_logged:
                    _binance_fallback_first_failure_logged = True
                    logger.warning(f"Binance API 回傳格式異常 {sym}: 非列表或長度<2 resp={str(data)[:150]}")
                return None
            prev_close = float(data[0][4])
            last_close = float(data[1][4])
            if not prev_close:
                return None
            return ((last_close - prev_close) / prev_close) * 100

        # --- 錯誤診斷：印出 Status Code 與回應內容 ---
        if not _binance_fallback_first_failure_logged:
            _binance_fallback_first_failure_logged = True
            logger.error(f"Binance API Error for {sym}: Status {r.status_code} - {r.text[:300]}")
        if r.status_code == 429:
            logger.warning("⚠️ RATE LIMIT TRIGGERED (429) - 幣安 API 頻率限制")
            time.sleep(1)
        elif r.status_code == 418:
            logger.warning(f"⚠️ IP 被幣安暫時封鎖 (418) - {sym}")
        elif r.status_code in (403, 451):
            logger.error("🚫 GEOBLOCK/REGION RESTRICTION (403/451) - 目前 IP 無法訪問幣安 API")
        return None
    except Exception as e:
        if not _binance_fallback_first_failure_logged:
            _binance_fallback_first_failure_logged = True
            logger.warning(f"Binance 連線異常 {sym}: {type(e).__name__}: {e}")
        return None


def fetch_coins_price_change() -> List[Dict]:
    """獲取幣種漲跌幅列表（改為只返回合約幣種）。初創版若 coins-price-change 為空則用 Binance 公開 API fallback。"""
    supported_coins = fetch_supported_futures_coins()
    if not supported_coins:
        logger.warning("無法獲取合約幣種列表，使用備用方法")
        url = f"{CG_API_BASE}/api/futures/coins-price-change"
        headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code != 200:
                return []
            result = response.json()
            return result.get('data', result if isinstance(result, list) else [])
        except Exception:
            return []

    url = f"{CG_API_BASE}/api/futures/coins-price-change"
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.error(f"coins-price-change HTTP {response.status_code}")
            return _fetch_coins_price_change_fallback(supported_coins)
        result = response.json()
        all_data = result.get('data', result if isinstance(result, list) else [])
        if not all_data:
            code = result.get('code', '')
            msg = result.get('msg', result.get('message', ''))
            logger.warning(f"CoinGlass 幣種漲跌幅返回空數據 code={code} msg={msg}（初創版可能不包含此接口，改用 BingX 官方 API）")
            return _fetch_coins_price_change_fallback(supported_coins)
        filtered_data = []
        for item in all_data:
            symbol = item.get('symbol') or item.get('coin') or ''
            symbol_clean = symbol.replace('USDT', '').replace('USDT-PERP', '').upper()
            if symbol_clean in supported_coins:
                filtered_data.append(item)
        logger.info(f"過濾後剩餘 {len(filtered_data)} 個合約幣種（原始 {len(all_data)} 個）")
        return filtered_data
    except Exception as e:
        logger.error(f"獲取幣種價格變化失敗: {str(e)}")
        return _fetch_coins_price_change_fallback(supported_coins)


def _fetch_coinglass_24h_map() -> Dict[str, float]:
    """
    CoinGlass 幣種漲跌幅：GET /api/futures/coins-price-change，回傳 {clean_symbol: pct}。
    創業版可能無此接口或回傳空，會顯示 0 個幣種，主流程改以 price/history 逐筆或 BingX 計算 24h。
    """
    if not CG_API_KEY:
        return {}
    try:
        r = requests.get(
            f"{CG_API_BASE}/api/futures/coins-price-change",
            headers={"CG-API-KEY": CG_API_KEY, "accept": "application/json"},
            timeout=10
        )
        if r.status_code != 200:
            logger.info("CoinGlass 24h 漲跌幅: HTTP 非 200，將用 BingX 計算")
            return {}
        j = r.json()
        if j.get("code") not in (0, "0", 200, "200", None):
            logger.info(f"CoinGlass 24h 漲跌幅: API code={j.get('code')} msg={j.get('msg', j.get('message', ''))}，將用 BingX 計算")
            return {}
        data = j.get("data", j if isinstance(j, list) else [])
        if not isinstance(data, list):
            data = [data] if isinstance(data, dict) and data else []
        out = {}
        for item in (data or []):
            if not isinstance(item, dict):
                continue
            sym = (
                item.get("symbol") or item.get("coin") or item.get("base") or
                item.get("coinSymbol") or item.get("symbol_name") or ""
            )
            if not sym:
                continue
            clean = str(sym).replace("USDT", "").replace("USDT-PERP", "").replace("-", "").replace("_", "").strip().upper()
            if not clean:
                continue
            pct = (
                item.get("price_change_percent_24h") or item.get("priceChangePercent24h") or
                item.get("change_24h") or item.get("priceChange24h") or item.get("price_change_24h")
            )
            if isinstance(pct, (int, float)) and pct == pct:
                out[clean] = float(pct)
        logger.info(f"CoinGlass 24h 漲跌幅已取得 {len(out)} 個幣種")
        return out
    except Exception as e:
        logger.warning(f"CoinGlass 24h 漲跌幅取得失敗，將用 BingX 計算: {e}")
        return {}


def fetch_price_change_24h_coinglass_klines(symbol: str, preferred_symbol: Optional[str] = None) -> Optional[float]:
    """
    CoinGlass 交易对K线历史 24h 漲跌幅。
    文件: https://docs.coinglass.com/v4.0-zh/reference/price-ohlc-history
    GET /api/futures/price/history，取 1h×25 根，首根 open、末根 close 計算 24h%。
    創業版可用此接口；coins-price-change 可能不可用，此處作逐筆 fallback。
    """
    if not CG_API_KEY:
        return None
    clean = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    for sym in ([preferred_symbol] if preferred_symbol else []) + [f"{clean}USDT", f"1000{clean}USDT"]:
        if not sym:
            continue
        try:
            r = requests.get(
                f"{CG_API_BASE}/api/futures/price/history",
                params={"exchange": "Binance", "symbol": sym, "interval": "1h", "limit": 25},
                headers={"CG-API-KEY": CG_API_KEY, "accept": "application/json"},
                timeout=10
            )
            if r.status_code != 200:
                continue
            j = r.json()
            if j.get("code") not in (0, "0", 200, "200", None):
                continue
            data = j.get("data", j.get("list", []))
            if not isinstance(data, list) or len(data) < 2:
                continue
            first = data[0]
            last = data[-1]
            first_open = float(
                first.get("open") or first.get("o") or first.get("openPrice") or 0
            )
            last_close = float(
                last.get("close") or last.get("c") or last.get("closePrice") or last.get("close_price") or 0
            )
            if first_open == 0:
                continue
            return ((last_close - first_open) / first_open) * 100
        except Exception:
            continue
    return None


def fetch_price_change_24h_bingx(symbol: str, preferred_symbol: Optional[str] = None) -> Optional[float]:
    """BingX 24h 漲跌幅：用 1h K 線取 24h 前開盤與最新收盤計算（CoinGlass 無資料時 fallback）。"""
    clean = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    for sym_fmt in ([preferred_symbol] if preferred_symbol else []) + [f"{clean}-USDT", f"1000{clean}-USDT"]:
        if not sym_fmt:
            continue
        try:
            r = requests.get(
                "https://open-api.bingx.com/openApi/swap/v3/quote/klines",
                params={"symbol": sym_fmt, "interval": "1h", "limit": 25},
                timeout=5
            )
            time.sleep(0.08)
            if r.status_code != 200:
                continue
            j = r.json()
            if j.get("code") != 0:
                continue
            data = j.get("data", [])
            if not isinstance(data, list) or len(data) < 2:
                continue
            first_open = float(data[0].get("open") or 0)
            last_close = float(data[-1].get("close") or 0)
            if first_open == 0:
                continue
            return ((last_close - first_open) / first_open) * 100
        except Exception:
            continue
    return None


def fetch_price_change_30m_bingx(symbol: str) -> Optional[float]:
    """
    【BingX 官方數據源】15 分鐘 K 線漲跌幅（已升級標準版 15m 高頻模式）。
    """
    clean_symbol = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    sym_formatted = f"{clean_symbol}-USDT"
    url = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"
    params = {"symbol": sym_formatted, "interval": "15m", "limit": 3}
    try:
        response = requests.get(url, params=params, timeout=5)
        if response.status_code != 200:
            return None
        res_json = response.json()
        if res_json.get("code") != 0:
            return None
        data = res_json.get("data", [])
        if not isinstance(data, list) or len(data) < 2:
            return None
        latest_k = data[-1]
        # 本根 15m K 線：open=15 分鐘前價格，close=當前價格 → 漲跌幅
        current_price = float(latest_k.get("close") or 0)
        open_price_15m_ago = float(latest_k.get("open") or 0)
        if open_price_15m_ago == 0:
            return None
        change_percent = ((current_price - open_price_15m_ago) / open_price_15m_ago) * 100
        return change_percent
    except Exception:
        return None


def _fetch_coins_price_change_fallback(supported_coins: List[str], max_symbols: int = 99999) -> List[Dict]:
    """Fallback：使用 BingX 官方 API 獲取 15m 漲跌幅（高頻版）。"""
    symbols = list(supported_coins)[:max_symbols]
    out = []
    logger.info(f"正在使用 BingX 官方 API 獲取 {len(symbols)} 個幣種的 15m 價格數據 (高頻模式)...")

    def one(sym):
        time.sleep(0.03)
        ch = fetch_price_change_30m_bingx(sym)
        return {"symbol": sym, "coin": sym, "price_change_percent_30m": ch} if ch is not None else None

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(one, s) for s in symbols]
        for i, res in enumerate(as_completed(futures)):
            r = res.result()
            if r is not None:
                out.append(r)
            if (i + 1) % 50 == 0:
                logger.info(f"BingX 價格獲取進度: {i + 1}/{len(symbols)} (成功 {len(out)} 個)")
    logger.info(f"成功從 BingX 獲取 {len(out)} 個幣種的價格數據")
    return out


# OI 首次失敗僅記錄一次，避免洗版
_coinglass_oi_first_failure_logged = False
# 線程鎖，防止多線程同時穿透限速
_oi_rate_limit_lock = threading.Lock()


def _parse_oi_change_from_data_list(data_list: list) -> Optional[float]:
    """從 CoinGlass OI K 線列表解析 15m 變化%（通用版：支援 v, c, close, oi）"""
    if not isinstance(data_list, list) or len(data_list) < 2:
        return None
    try:
        data_list = sorted(
            data_list,
            key=lambda x: x.get("t") or x.get("time") or x.get("timestamp") or 0
        )
    except Exception:
        pass
    last = data_list[-1]
    prev = data_list[-2]
    # 聚合接口常返回: t, o, h, l, c（短鍵名）
    keys_to_check = ["v", "value", "openInterest", "oi", "close", "c", "open", "o"]
    last_oi = None
    prev_oi = None
    for k in keys_to_check:
        if last.get(k) is not None:
            last_oi = last.get(k)
            break
    for k in keys_to_check:
        if prev.get(k) is not None:
            prev_oi = prev.get(k)
            break
    try:
        last_oi = float(last_oi) if last_oi is not None else None
        prev_oi = float(prev_oi) if prev_oi is not None else None
    except (ValueError, TypeError):
        return None
    if not last_oi or not prev_oi or prev_oi == 0:
        return None
    return ((last_oi - prev_oi) / prev_oi) * 100


def _parse_oi_bars_from_rows(rows: list) -> list:
    """通用 OI K線數值解析（支援多種欄位名稱）。"""
    keys = ["c", "close", "v", "value", "openInterest", "oi"]
    sorted_rows = sorted(rows, key=lambda x: x.get("t") or x.get("time") or 0)
    oi_bars = []
    for row in sorted_rows:
        if not isinstance(row, dict):
            continue
        for k in keys:
            val = row.get(k)
            if val is not None:
                try:
                    oi_bars.append(float(val))
                    break
                except (TypeError, ValueError):
                    pass
    return oi_bars


def _calc_oi_trend_from_bars(oi_bars: list) -> Dict[str, Any]:
    """從 OI 數值列表計算趨勢指標（可複用）。"""
    change_pct = ((oi_bars[-1] - oi_bars[-2]) / oi_bars[-2] * 100) if oi_bars[-2] != 0 else None
    recent = oi_bars[-4:] if len(oi_bars) >= 4 else oi_bars
    ups = sum(1 for i in range(1, len(recent)) if recent[i] > recent[i-1])
    downs = sum(1 for i in range(1, len(recent)) if recent[i] < recent[i-1])
    total_ch = ((recent[-1] - recent[0]) / recent[0] * 100) if recent[0] != 0 else 0
    if ups >= 3 and total_ch > 0.5:
        trend = "building"
    elif downs >= 3 and total_ch < -0.5:
        trend = "declining"
    elif abs(total_ch) < 0.2:
        trend = "flat"
    else:
        trend = "reversing"
    acceleration = False
    if len(oi_bars) >= 5:
        recent_ch = abs(oi_bars[-1] - oi_bars[-2]) / max(abs(oi_bars[-2]), 1)
        prev_ch = abs(oi_bars[-3] - oi_bars[-4]) / max(abs(oi_bars[-4]), 1)
        acceleration = recent_ch > prev_ch * 1.5
    # bars 僅保留最近 6 根，後續依 interval 推導 1h/4h 變化
    return {"change_pct": change_pct, "trend": trend, "acceleration": acceleration, "bars": oi_bars[-6:]}


def _augment_oi_multi_tf_changes(result: Dict[str, Any], interval: str) -> Dict[str, Any]:
    """
    基於 OI bars 推導多時間框架變化：
    - change_1h_pct：近 1 小時 OI 變化百分比
    - change_4h_pct：近 4 小時 OI 變化百分比（若資料不足則為 None）
    interval 目前實務上多為 h1 或 15m。
    """
    bars = result.get("bars") or []
    change_1h_pct: Optional[float] = None
    change_4h_pct: Optional[float] = None
    if not isinstance(bars, list) or len(bars) < 2:
        result["change_1h_pct"] = None
        result["change_4h_pct"] = None
        return result

    iv = (interval or "").lower()
    try:
        if iv in ("h1", "1h"):
            # 一根 = 1h
            if len(bars) >= 2 and bars[-2] != 0:
                change_1h_pct = (bars[-1] - bars[-2]) / bars[-2] * 100
            if len(bars) >= 4 and bars[-4] != 0:
                change_4h_pct = (bars[-1] - bars[-4]) / bars[-4] * 100
        elif iv in ("15m", "m15", "15min"):
            # 4 根 ≈ 1h；4h 需 16 根，通常資料不足 → 僅計 1h
            if len(bars) >= 4 and bars[-4] != 0:
                change_1h_pct = (bars[-1] - bars[-4]) / bars[-4] * 100
            change_4h_pct = None
        else:
            # 其他 interval 暫不特別推導
            change_1h_pct = result.get("change_pct")
            change_4h_pct = None
    except Exception:
        change_1h_pct = result.get("change_pct")
        change_4h_pct = None

    result["change_1h_pct"] = change_1h_pct
    result["change_4h_pct"] = change_4h_pct
    return result


def fetch_oi_trend_analysis(symbol: str, interval: str = "15m", limit: int = 8) -> Dict[str, Any]:
    """多棒 OI 趨勢分析（聚合歷史K線）。
    方案A：/api/futures/open-interest/aggregated-history（跨所聚合，最精準）
    方案B：/api/futures/open-interest/history（單所 Binance，備援）
    方案C：僅用現有 oiChange 單點估算趨勢（數據最少但保證有值）
    回傳 trend/acceleration/change_pct/bars/data_source
    快取 90 秒。
    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    cache_key = f"oi_trend:{base}:{interval}"
    now = time.time()
    empty: Dict[str, Any] = {"change_pct": None, "trend": "flat", "acceleration": False, "bars": [], "data_source": "none"}
    if cache_key in _flow_cache:
        cached, ts = _flow_cache[cache_key]
        if now - ts < _FLOW_TTL:
            logger.debug(f"[OI趨勢] {base} 命中快取，跳過API請求")
            return cached if cached else empty

    # OI 趨勢端點同樣 m15 對山寨幣幾乎全空，優先試 h1
    _oi_try_ivs = ["h1", interval] if interval not in ("1h", "h1") else ["h1"]
    _oi_try_ivs = list(dict.fromkeys(_oi_try_ivs))

    # ── 方案A：聚合歷史（最精準）──────────────────────────────────
    oi_bars: list = []
    for _oi_iv in _oi_try_ivs:
        logger.debug(f"[OI趨勢-A] {base} 聚合OI歷史 interval={_oi_iv} limit={limit}")
        j = _cg_get(CG_EP["oi_agg_history"], {"symbol": base, "interval": _oi_iv, "limit": limit})
        rows_a = j.get("data") or j.get("list") or [] if j else []
        oi_bars = _parse_oi_bars_from_rows(rows_a) if isinstance(rows_a, list) else []
        if len(oi_bars) >= 3:
            result = _calc_oi_trend_from_bars(oi_bars)
            result["data_source"] = f"agg_history_{_oi_iv}"
            result = _augment_oi_multi_tf_changes(result, _oi_iv)
            logger.info(f"[OI趨勢-A✅] {base}: 方案A成功({_oi_iv})，{len(oi_bars)}棒，trend={result['trend']}")
            _flow_cache[cache_key] = (result, now)
            return result
    logger.warning(f"[OI趨勢-A❌] {base}: 方案A無效數據（取得{len(oi_bars)}棒，需≥3，已試 {_oi_try_ivs}），改用方案B")

    # ── 方案B：單所輪詢 OI 歷史（備援）────────────────────────
    # 只嘗試實際支援該幣的大所，跳過 BingX-only 小幣的無效呼叫
    _oi_b_exchanges = get_major_exchanges_for_coin(base, ["Binance", "OKX", "Bybit"])
    if not _oi_b_exchanges:
        logger.debug(f"[OI趨勢-B] {base}: 無大所支援，跳過方案B")
    else:
        for _oi_ex in _oi_b_exchanges:
            for _oi_iv in _oi_try_ivs:
                logger.debug(f"[OI趨勢-B] {base} 單所OI歷史 exchange={_oi_ex} interval={_oi_iv}")
                j2 = _cg_get(CG_EP["oi_history"], {"symbol": base + "USDT", "exchange": _oi_ex, "interval": _oi_iv, "limit": limit})
                rows_b = j2.get("data") or j2.get("list") or [] if j2 else []
                oi_bars_b = _parse_oi_bars_from_rows(rows_b) if isinstance(rows_b, list) else []
                if len(oi_bars_b) >= 3:
                    result = _calc_oi_trend_from_bars(oi_bars_b)
                    result["data_source"] = f"{_oi_ex}_{_oi_iv}"
                    result = _augment_oi_multi_tf_changes(result, _oi_iv)
                    logger.info(f"[OI趨勢-B✅] {base}: 方案B成功（{_oi_ex}/{_oi_iv}），{len(oi_bars_b)}棒")
                    _flow_cache[cache_key] = (result, now)
                    return result
        logger.warning(f"[OI趨勢-B❌] {base}: 方案B也無效，退回 empty 結果")

    _flow_cache[cache_key] = (empty, now)
    return empty


def fetch_top_account_ls_ratio(symbol: str, interval: str = "1h", limit: int = 3) -> Optional[float]:
    """大戶帳戶多空比（Top Account Long/Short Ratio）。
    方案A：/api/futures/top-long-short-account-ratio/history（大戶帳戶數比）
    方案B：/api/futures/top-long-short-position-ratio/history（大戶持倉量比，備援）
    方案C：/api/futures/global-long-short-account-ratio/history（全市場散戶備援）
    > 1.0 = 偏多；< 1.0 = 偏空
    快取 90 秒。
    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    cache_key = f"top_ls:{base}:{interval}"
    now = time.time()
    if cache_key in _flow_cache:
        cached, ts = _flow_cache[cache_key]
        if now - ts < _FLOW_TTL:
            logger.debug(f"[大戶L/S] {base} 命中快取")
            return cached

    def _parse_ls_ratio(j: Optional[Dict]) -> Optional[float]:
        if not j:
            return None
        rows = j.get("data") or j.get("list") or []
        if not isinstance(rows, list) or not rows:
            return None
        sorted_rows = sorted(rows, key=lambda x: x.get("time") or x.get("t") or 0)
        last = sorted_rows[-1]
        for key in ["longShortRatio", "long_short_ratio", "ratio", "topLongShortRatio", "longShortAccountRatio"]:
            val = last.get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
        return None

    sym_param = base + "USDT"
    # L/S 比只支援較粗粒度，固定用 h1（m15 山寨幣幾乎全部空陣列）
    _ls_iv = "h1"
    # 只嘗試實際支援該幣的大所，BingX-only 小幣直接跳過（無資料）
    _ls_exchanges = get_major_exchanges_for_coin(base, ["Binance", "OKX", "Bybit"])

    # 若無大所支援，直接跳過 A/B/C 三方案（BingX-only 小幣無此數據）
    if not _ls_exchanges:
        logger.debug(f"[大戶L/S] {base}: 無大所支援，跳過 A/B/C 三方案")
        _flow_cache[cache_key] = (None, now)
        return None

    # ── 方案A：大戶帳戶數多空比──────────────────────────────────────
    for _ls_ex in _ls_exchanges:
        logger.debug(f"[大戶L/S-A] {base} exchange={_ls_ex} sym={sym_param} interval={_ls_iv}")
        j_a = _cg_get(CG_EP["ls_top_account"], {"symbol": sym_param, "exchange": _ls_ex,
                                                  "interval": _ls_iv, "limit": limit})
        result = _parse_ls_ratio(j_a)
        if result is not None:
            logger.info(f"[大戶L/S-A✅] {base}: {_ls_ex} 帳戶多空比={result:.3f}")
            _flow_cache[cache_key] = (result, now)
            return result
    logger.warning(f"[大戶L/S-A❌] {base}: 方案A（帳戶數比）無效，改用方案B（大戶持倉比）")

    # ── 方案B：大戶持倉多空比─────────────────────────────────────────
    for _ls_ex in _ls_exchanges:
        j_b = _cg_get(CG_EP["ls_top_position"], {"symbol": sym_param, "exchange": _ls_ex,
                                                   "interval": _ls_iv, "limit": limit})
        result = _parse_ls_ratio(j_b)
        if result is not None:
            logger.info(f"[大戶L/S-B✅] {base}: {_ls_ex} 大戶持倉多空比={result:.3f}")
            _flow_cache[cache_key] = (result, now)
            return result
    logger.warning(f"[大戶L/S-B❌] {base}: 方案B（持倉量比）無效，改用方案C（全市場散戶L/S）")

    # ── 方案C：全市場帳戶多空比（散戶視角，一般有值）──────────────────
    for _ls_ex in _ls_exchanges:
        j_c = _cg_get(CG_EP["ls_global_history"], {"symbol": sym_param, "exchange": _ls_ex,
                                                     "interval": _ls_iv, "limit": limit})
        result = _parse_ls_ratio(j_c)
        if result is not None:
            logger.info(f"[大戶L/S-C✅] {base}: {_ls_ex} 全市場多空比={result:.3f}（散戶視角）")
            _flow_cache[cache_key] = (result, now)
            return result

    logger.warning(f"[大戶L/S-全失敗] {base}: A/B/C 三個方案均無法獲取多空比，返回 None")
    _flow_cache[cache_key] = (None, now)
    return None


def fetch_oi_change_30m(symbol: str) -> Optional[float]:
    """
    計算單一 symbol 15 分鐘 OI 變化%（已升級標準版高頻模式）
    """
    global _coinglass_oi_rate_limiter, _coinglass_oi_first_failure_logged

    with _oi_rate_limit_lock:
        if _coinglass_oi_rate_limiter is None:
            _coinglass_oi_rate_limiter = {"last_call": 0.0}
        now = time.time()
        elapsed = now - _coinglass_oi_rate_limiter.get("last_call", 0.0)
        # 雲端環境 Anti-429：熔斷器啟動時 wait_time 自動加倍
        # 標準版高頻模式：隨機延遲縮短為 0.1~0.2s（熔斷時加倍）
        wait_time = random.uniform(0.1, 0.2) * _cb_get_wait_multiplier()
        if elapsed < wait_time:
            time.sleep(wait_time - elapsed)
        _coinglass_oi_rate_limiter["last_call"] = time.time()

    base_symbol = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    url = f"{CG_API_BASE}/api/futures/open-interest/aggregated-history"
    params = {"symbol": base_symbol, "interval": "15m", "limit": 5}
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}

    backoff = 2.0
    for attempt in range(4):
        try:
            _respect_coinglass_rate_limit()
            response = requests.get(url, params=params, headers=headers, timeout=10)
            if response.status_code == 200:
                result = response.json()
                if result.get("code") in ("0", 0, 200, "200"):
                    data_list = result.get("data", result.get("list", []))
                    change = _parse_oi_change_from_data_list(data_list)
                    if change is not None:
                        _cb_record_success()
                        return change
                msg = result.get("msg", "")
                if "Too Many Requests" in msg or result.get("code") in ("400", "429"):
                    _cb_record_429()
                    sleep_for = backoff + random.uniform(0, 1.0)
                    logger.warning(f"[GITHUB_IP_THROTTLED] CoinGlass 限流 ({base_symbol})，休息 {sleep_for:.1f} 秒（第 {attempt+1} 次重試）...")
                    time.sleep(sleep_for)
                    backoff *= 2.0
                    continue
            elif response.status_code == 429:
                _cb_record_429()
                sleep_for = backoff + random.uniform(0, 1.0)
                logger.warning(f"[GITHUB_IP_THROTTLED] CoinGlass HTTP 429 限流 ({base_symbol})，休息 {sleep_for:.1f} 秒（第 {attempt+1} 次重試）...")
                time.sleep(sleep_for)
                backoff *= 2.0
                continue
        except Exception as e:
            logger.debug(f"OI 請求異常 {base_symbol}: {e}")
            time.sleep(backoff)
            backoff *= 2.0
    return None


# ── 標準版特有：5M 動能共振驗證 ───────────────────────────────────────────────
_resonance_cache: Dict[str, Tuple[Optional[bool], float]] = {}
_RESONANCE_CACHE_TTL = 30.0  # 30 秒快取（比共識更短，動量瞬息萬變）


def fetch_oi_resonance_5m(symbol: str, category: str) -> Optional[bool]:
    """【標準版專屬】5M 動能共振驗證。
    在 15M OI 爆發的同時，拉取最新 5M OI 方向，確認動能具有「連續性」。

    category: 'long_open'/'short_open'  → 期待 OI 上升（建倉）
              'long_close'/'short_close' → 期待 OI 下降（平倉）

    Returns:
        True  = 5M 方向與 15M 一致（動能連續，訊號可信）
        False = 5M 方向相反（一秒插針式假訊號，動能已竭）
        None  = API 失敗（無法判斷，保持中立）
    """
    base_symbol = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    cache_key = f"{base_symbol}:{category}"
    now = time.time()

    if cache_key in _resonance_cache:
        cached_val, cached_ts = _resonance_cache[cache_key]
        if now - cached_ts < _RESONANCE_CACHE_TTL:
            return cached_val

    url = f"{CG_API_BASE}/api/futures/open-interest/aggregated-history"
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}
    params = {"symbol": base_symbol, "interval": "5m", "limit": 3}

    try:
        _respect_coinglass_rate_limit()
        response = requests.get(url, params=params, headers=headers, timeout=8)
        if response.status_code != 200:
            _resonance_cache[cache_key] = (None, now)
            return None

        result = response.json()
        if result.get("code") not in ("0", 0, 200, "200"):
            _resonance_cache[cache_key] = (None, now)
            return None

        data_list = result.get("data", result.get("list", []))
        if not isinstance(data_list, list) or len(data_list) < 2:
            _resonance_cache[cache_key] = (None, now)
            return None

        # 取最後兩根 5M K 線計算 OI 變化
        prev_oi = None
        curr_oi = None
        for bar in data_list[-2:]:
            if isinstance(bar, dict):
                oi_val = bar.get("openInterest") or bar.get("o") or bar.get("c")
            elif isinstance(bar, (list, tuple)) and len(bar) >= 5:
                oi_val = bar[4]  # 通常 close OI
            else:
                continue
            try:
                f_val = float(oi_val) if oi_val is not None else None
                if f_val and f_val > 0:
                    if prev_oi is None:
                        prev_oi = f_val
                    else:
                        curr_oi = f_val
            except (TypeError, ValueError):
                pass

        if prev_oi is None or curr_oi is None or prev_oi == 0:
            _resonance_cache[cache_key] = (None, now)
            return None

        oi_change_5m = (curr_oi - prev_oi) / prev_oi * 100

        # 判斷方向一致性
        oi_rising = oi_change_5m > 0.05   # 5M OI 上升 (>0.05% 才算有意義)
        oi_falling = oi_change_5m < -0.05  # 5M OI 下降

        # long_open / short_open 期待 OI 上升（主力仍在建倉）
        # long_close / short_close 期待 OI 下降（主力仍在平倉）
        expects_rising = category in ("long_open", "short_open")

        if expects_rising:
            resonance = oi_rising     # 5M 也在漲 → 共振
        else:
            resonance = oi_falling    # 5M 也在跌 → 共振

        logger.info(
            f"[5M共振] {base_symbol} ({category}): 5M OI 變化={oi_change_5m:+.3f}% → "
            f"{'🔥 共振確認' if resonance else '⚠️ 動能已竭'}"
        )
        _resonance_cache[cache_key] = (resonance, now)
        return resonance

    except Exception as e:
        logger.debug(f"[5M共振] {base_symbol} 查詢異常: {e}")

    # ── 方案B：穩定幣保證金 OI 5m 歷史（次級驗證）──────────────────
    # 當聚合 OI 無法判斷時，用穩定幣OI作為代理（更能反映真實資金流向）
    logger.debug(f"[5M共振-B] {base_symbol} 嘗試穩定幣OI 5m 次級驗證")
    try:
        j_s = _cg_get(CG_EP["oi_agg_stable"], {"symbol": base_symbol, "interval": "5m", "limit": 3})
        if j_s:
            rows_s = j_s.get("data") or j_s.get("list") or []
            bars_s = _parse_oi_bars_from_rows(rows_s) if rows_s else []
            if len(bars_s) >= 2 and bars_s[-2] != 0:
                chg_s = (bars_s[-1] - bars_s[-2]) / bars_s[-2] * 100
                oi_rising_s = chg_s > 0.03
                oi_falling_s = chg_s < -0.03
                expects_rising_b = category in ("long_open", "short_open")
                resonance_b: Optional[bool] = oi_rising_s if expects_rising_b else oi_falling_s
                logger.info(
                    f"[5M共振-B] {base_symbol} 穩定幣OI 5m={chg_s:+.3f}% → "
                    f"{'🔥 次級共振' if resonance_b else '⚠️ 次級無共振'}"
                )
                _resonance_cache[cache_key] = (resonance_b, now)
                return resonance_b
    except Exception as e_b:
        logger.debug(f"[5M共振-B] {base_symbol} 穩定幣OI備援異常: {e_b}")

    _resonance_cache[cache_key] = (None, now)
    return None


def fetch_rsi_5m(symbol: str) -> Optional[float]:
    """抓取 5m RSI(14)，供「RSI超賣鑽石升等」邏輯使用。
    優先使用 CoinGlass /api/futures/indicators/rsi（interval=5m）；
    失敗時備援 BingX 5m K 線本地計算。
    快取 30 秒避免重複 API 呼叫。
    Returns: RSI float (0-100) 或 None (無法取得)
    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    cache_key = f"rsi5m:{base}"
    now = time.time()
    if cache_key in _resonance_cache:
        cached_val, cached_ts = _resonance_cache[cache_key]
        if now - cached_ts < 30.0:
            return cached_val  # type: ignore[return-value]

    # ── 優先：CoinGlass indicators/rsi ────────────────────────────────────
    try:
        _respect_coinglass_rate_limit()
        r_cg = requests.get(
            f"{CG_API_BASE}/api/futures/indicators/rsi",
            headers={"CG-API-KEY": CG_API_KEY, "accept": "application/json"},
            params={"symbol": base, "interval": "5m"},
            timeout=8,
        )
        if r_cg.status_code == 200:
            j_cg = r_cg.json()
            if j_cg.get("code") in (0, "0", 200, "200", None):
                data_cg = j_cg.get("data") or {}
                # 支援多種回應格式
                rsi_raw = (
                    data_cg.get("rsi") or data_cg.get("rsi14") or data_cg.get("value")
                    if isinstance(data_cg, dict) else None
                )
                # 若 data 是列表，取最後一筆
                if rsi_raw is None and isinstance(data_cg, list) and data_cg:
                    last = data_cg[-1]
                    rsi_raw = (last.get("rsi") or last.get("value") or last.get("rsi14")
                               if isinstance(last, dict) else None)
                if rsi_raw is not None:
                    rsi = float(rsi_raw)
                    logger.info(f"[5m RSI-CG] {base}: RSI={rsi:.1f}")
                    _resonance_cache[cache_key] = (rsi, now)
                    return rsi
    except Exception as e:
        logger.debug(f"[5m RSI-CG] {base} CoinGlass 失敗，備援 BingX K線: {e}")

    # ── 備援：CoinGlass price/history 5m K 線本地計算 ──────────────────────
    try:
        for ex, sym_pair in [("Binance", f"{base}USDT"), ("OKX", f"{base}USDT")]:
            _respect_coinglass_rate_limit()
            r2 = requests.get(
                f"{CG_API_BASE}/api/futures/price/history",
                headers={"CG-API-KEY": CG_API_KEY, "accept": "application/json"},
                params={"exchange": ex, "symbol": sym_pair, "interval": "5m", "limit": 20},
                timeout=8,
            )
            if r2.status_code != 200:
                continue
            j2 = r2.json()
            if j2.get("code") not in (0, "0", 200, "200", None):
                continue
            raw2 = j2.get("data") or j2.get("list") or []
            if not isinstance(raw2, list) or len(raw2) < 15:
                continue
            _, _, _, closes2, _ = _parse_kline_rows(raw2)
            if len(closes2) < 15:
                continue
            deltas = [closes2[i] - closes2[i - 1] for i in range(1, len(closes2))]
            gains = [max(d, 0.0) for d in deltas]
            losses = [max(-d, 0.0) for d in deltas]
            avg_gain = sum(gains[-14:]) / 14.0
            avg_loss = sum(losses[-14:]) / 14.0
            rsi = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
            rsi_rounded = round(rsi, 1)
            _resonance_cache[cache_key] = (rsi_rounded, now)
            logger.info(f"[5m RSI-CG K線] {base} ({ex}): RSI={rsi_rounded}")
            return rsi_rounded
    except Exception as e:
        logger.debug(f"[5m RSI-CG K線] {base} 異常: {e}")
    _resonance_cache[cache_key] = (None, now)
    return None


# ── 標準版特有：多所共識檢查 ─────────────────────────────────────────────────
_CONSENSUS_MAJOR_EXCHANGES = ["Binance", "OKX", "Bybit", "BingX", "Bitget"]
_consensus_cache: Dict[str, Tuple[bool, float]] = {}  # {symbol: (is_consensus, ts)}
_CONSENSUS_CACHE_TTL = 60.0  # 60 秒快取，避免高頻重複呼叫


def fetch_exchange_oi_consensus(symbol: str) -> bool:
    """【標準版專屬】多所共識：檢查前五大交易所的 OI 在 15m 內是否同向變動。
    若 3 家以上同向（同增或同減），回傳 True（代表有全網資金共識）。
    結果快取 60 秒以避免頻繁 API 呼叫。
    """
    base_symbol = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    cache_key = base_symbol
    now = time.time()

    # 快取命中
    if cache_key in _consensus_cache:
        cached_val, cached_ts = _consensus_cache[cache_key]
        if now - cached_ts < _CONSENSUS_CACHE_TTL:
            return cached_val

    # 使用 CG_EP 定義的路徑（自動處理新舊路徑差異），同時準備舊路徑備援
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}
    params = {"symbol": base_symbol}

    # 嘗試新路徑 (camelCase) → 舊路徑 (kebab-case)
    _consensus_ep_paths = [
        CG_EP.get("oi_exchange_list", "/api/futures/openInterest/exchange-list"),
        "/api/futures/open-interest/exchange-list",   # 舊路徑備援
    ]
    data_list = []
    for _ep_path in _consensus_ep_paths:
        try:
            _respect_coinglass_rate_limit()
            response = requests.get(f"{CG_API_BASE}{_ep_path}",
                                    params=params, headers=headers, timeout=8)
            if response.status_code == 404:
                logger.debug(f"[多所共識] {_ep_path} 404，嘗試備援路徑")
                continue
            if response.status_code != 200:
                logger.debug(f"[多所共識] {_ep_path} HTTP={response.status_code}")
                break
            result = response.json()
            if result.get("code") not in ("0", 0, 200, "200", None):
                logger.debug(f"[多所共識] {_ep_path} code={result.get('code')}")
                break
            candidate = result.get("data", [])
            if isinstance(candidate, list) and candidate:
                data_list = candidate
                logger.debug(f"[多所共識] 使用路徑 {_ep_path}，取得 {len(data_list)} 筆")
                break
        except Exception as e_ep:
            logger.debug(f"[多所共識] {_ep_path} 異常: {e_ep}")

    try:
        if not data_list:
            _consensus_cache[cache_key] = (False, now)
            return False

        # 統計各大所的 OI 15m 變化方向
        positive_count = 0  # OI 增加
        negative_count = 0  # OI 減少
        found_exchanges = 0
        # 記錄第一筆欄位名，方便 debug
        _sample_keys = list(data_list[0].keys()) if data_list and isinstance(data_list[0], dict) else []
        logger.info(f"[多所共識] API 回應欄位樣本（第一筆）: {_sample_keys}")

        for entry in data_list:
            exch = (entry.get("exchange") or entry.get("exchangeName") or "").strip()
            if exch not in _CONSENSUS_MAJOR_EXCHANGES:
                continue
            found_exchanges += 1
            # 擴充 OI 變化欄位匹配（覆蓋新舊 API 格式）
            # 文檔實際欄位（已從 log 確認）：open_interest_change_percent_15m 等
            oi_chg = (
                entry.get("open_interest_change_percent_15m")   # ✅ 確認欄位
                or entry.get("open_interest_change_percent_5m")
                or entry.get("open_interest_change_percent_1h")
                or entry.get("open_interest_change_percent_4h")
                or entry.get("open_interest_change_percent_24h")
                or entry.get("open_interest_change_percent_30m")
                # 舊式欄位名（兼容備援）
                or entry.get("openInterestChangePercent15m")
                or entry.get("oiChange15m")
                or entry.get("openInterestChange")
                or entry.get("oiChangePercent")
                or entry.get("h4Change")
                or entry.get("h1Change")
            )
            if oi_chg is None:
                # 最後手段：用 openInterest - openInterestPrev 計算方向
                oi_now  = entry.get("openInterest") or entry.get("oi") or entry.get("currentOI") or 0
                oi_prev = (entry.get("openInterestPrev") or entry.get("oiPrev") or
                           entry.get("prevOI") or entry.get("previousOI") or 0)
                try:
                    diff = float(oi_now) - float(oi_prev)
                    oi_chg = diff if diff != 0 else None
                except (TypeError, ValueError):
                    pass
            if oi_chg is None:
                # 升級為 INFO，讓使用者看到實際欄位名稱以便除錯
                logger.info(f"[多所共識⚠️] {exch}: 找不到 OI 變化欄位 | 實際 keys={list(entry.keys())}")
                continue
            try:
                chg_val = float(oi_chg)
                if chg_val > 0:
                    positive_count += 1
                elif chg_val < 0:
                    negative_count += 1
            except (TypeError, ValueError):
                pass

        # 3 家以上同向 → 方案A 共識確立
        consensus_a = (positive_count >= 3) or (negative_count >= 3)
        logger.info(
            f"[多所共識-A] {base_symbol}: 查到 {found_exchanges} 家大所 | "
            f"OI增加 {positive_count} 家 | OI減少 {negative_count} 家 | "
            f"{'✅ 共識確立' if consensus_a else '❌ 無共識'}"
        )
        if consensus_a:
            _consensus_cache[cache_key] = (True, now)
            return True

        # ── 方案B：exchange-history-chart 多棒趨勢確認（文檔確認：用 range= 而非 interval=）
        # 文檔：/open-interest/exchange-history-chart?symbol=BTC&range=12h
        # symbol 用 BTC（base only），range 用 4h/12h 表示歷史視窗
        logger.debug(f"[多所共識-B] {base_symbol} 快照無共識，嘗試 exchange-history-chart 多棒分析")
        try:
            hist_positive = 0
            hist_negative = 0
            checked_hist = 0
            # range 降級：先用 4h（最近），無數據再試 12h
            for _b_range in ["4h", "12h"]:
                hist_positive = hist_negative = checked_hist = 0
                for exch in _CONSENSUS_MAJOR_EXCHANGES[:5]:
                    j_h = _cg_get(CG_EP["oi_exchange_history"],
                                   {"symbol": base_symbol, "range": _b_range})
                    if not j_h:
                        continue
                    rows_h = j_h.get("data") or j_h.get("list") or []
                    bars = _parse_oi_bars_from_rows(rows_h)
                    if len(bars) < 2:
                        continue
                    checked_hist += 1
                    recent_trend = bars[-1] - bars[-2]
                    if recent_trend > 0:
                        hist_positive += 1
                    elif recent_trend < 0:
                        hist_negative += 1
                if checked_hist >= 2:
                    logger.debug(f"[多所共識-B] range={_b_range} checked={checked_hist}")
                    break  # 有數據就用這個視窗
            consensus_b = checked_hist >= 3 and ((hist_positive >= 3) or (hist_negative >= 3))
            logger.info(
                f"[多所共識-B] {base_symbol}: 歷史K線分析 checked={checked_hist}"
                f" OI增 {hist_positive} OI減 {hist_negative}"
                f" {'✅ 歷史趨勢共識' if consensus_b else '❌ 歷史無共識'}"
            )
            _consensus_cache[cache_key] = (consensus_b, now)
            return consensus_b
        except Exception as e_b:
            logger.debug(f"[多所共識-B] {base_symbol} 歷史K線分析異常: {e_b}")

        _consensus_cache[cache_key] = (False, now)
        return False

    except Exception as e:
        logger.debug(f"[多所共識] {base_symbol} 查詢異常: {e}")
        _consensus_cache[cache_key] = (False, now)
        return False


# ── 爆倉熱力圖預警 ─────────────────────────────────────────────────────────────

_liq_heatmap_cache: Dict[str, Tuple[Optional[Dict], float]] = {}
_LIQ_HEATMAP_TTL = 120.0  # 2 分鐘快取（爆倉位置不會快速移動）


def fetch_liq_heatmap_nearby(
    symbol: str,
    current_price: float,
    is_long: bool,
    proximity_pct: float = 3.0,
) -> Optional[Dict[str, Any]]:
    """【爆倉熱力圖】查詢進場點附近的爆倉密集區。

    對做多訊號：尋找「下方」的爆倉池（支撐層）
    對做空訊號：尋找「上方」的爆倉池（阻力層）

    回傳：
        {"pct": float, "side": "多單爆倉"|"空單爆倉",
         "label": str, "usd": float}
        或 None（API 失敗 / 附近無明顯爆倉池）
    """
    if not CG_API_KEY or not current_price or current_price <= 0:
        return None

    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    cache_key = f"{base}:{'long' if is_long else 'short'}"
    now = time.time()

    if cache_key in _liq_heatmap_cache:
        cached, ts = _liq_heatmap_cache[cache_key]
        if now - ts < _LIQ_HEATMAP_TTL:
            return cached

    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}

    def _parse_result(data_list: list) -> Optional[Dict[str, Any]]:
        """從 API 回傳列表中找出最近的大型爆倉聚集位置。"""
        best: Optional[Dict] = None
        best_pct = proximity_pct + 1

        for entry in (data_list or []):
            if not isinstance(entry, dict):
                continue
            # 嘗試多種欄位名：price / liqPrice / liquidationPrice / level / priceLevel
            p_raw = (
                entry.get("price") or entry.get("liqPrice") or
                entry.get("liquidationPrice") or entry.get("level") or
                entry.get("priceLevel")
            )
            # 爆倉量：longLiqUsd / shortLiqUsd / value / amount
            long_usd = float(entry.get("longLiqUsd") or entry.get("longAmount") or
                             entry.get("long") or 0)
            short_usd = float(entry.get("shortLiqUsd") or entry.get("shortAmount") or
                              entry.get("short") or 0)
            try:
                liq_price = float(p_raw) if p_raw is not None else None
            except (TypeError, ValueError):
                liq_price = None
            if liq_price is None or liq_price <= 0:
                continue

            pct_dist = abs(liq_price - current_price) / current_price * 100.0
            if pct_dist > proximity_pct:
                continue

            # 做多：關注「下方」空單爆倉（會推升價格）和多單爆倉（支撐被擊穿）
            # 做空：關注「上方」多單爆倉（會下壓價格）和空單爆倉（阻力被突破）
            total_usd = long_usd + short_usd
            if total_usd < 500_000:  # 低於 50 萬 USD 忽略
                continue

            if pct_dist < best_pct:
                best_pct = pct_dist
                # 判斷哪方向的爆倉更多
                if long_usd >= short_usd:
                    side_label = "多單爆倉"
                    dominant_usd = long_usd
                else:
                    side_label = "空單爆倉"
                    dominant_usd = short_usd
                # 對做多信號：下方空單爆倉池 = 支撐（嘎空）；下方多單爆倉 = 危險
                if is_long:
                    if liq_price < current_price and side_label == "空單爆倉":
                        interp = "🔥 下方爆倉支撐池（嘎空動力）"
                    elif liq_price < current_price:
                        interp = "⚠️ 下方多單爆倉池（止損群聚）"
                    else:
                        interp = "🧱 上方爆倉阻力"
                else:
                    if liq_price > current_price and side_label == "多單爆倉":
                        interp = "🔥 上方爆倉支撐池（嘎多動力）"
                    elif liq_price > current_price:
                        interp = "⚠️ 上方空單爆倉池（止損群聚）"
                    else:
                        interp = "🧱 下方爆倉阻力"
                best = {
                    "pct": round(pct_dist, 2),
                    "price": round(liq_price, 6),
                    "side": side_label,
                    "usd": dominant_usd,
                    "total_usd": total_usd,
                    "label": interp,
                }
        return best

    result: Optional[Dict] = None

    # ── 方案A：幣種聚合爆倉歷史（最精準，真實爆倉數據）──────────────
    # aggregated-history 回傳最近 N 根 K 線的多空爆倉總量
    logger.debug(f"[爆倉-A] {base} endpoint={CG_EP['liq_agg_history']}")
    try:
        j_a = _cg_get(CG_EP["liq_agg_history"],
                       {"symbol": base, "interval": "15m", "limit": 8})
        if j_a:
            rows_a = j_a.get("data") or j_a.get("list") or []
            if isinstance(rows_a, list) and rows_a:
                # 找最近幾根K線中爆倉最密集的價格區間
                # 每棒通常含 longLiqUsd, shortLiqUsd, closePrice/price
                combined_a = []
                for bar in rows_a:
                    if not isinstance(bar, dict):
                        continue
                    try:
                        p_a = float(bar.get("closePrice") or bar.get("price") or
                                    bar.get("close") or bar.get("c") or 0)
                        long_usd_a = float(bar.get("longLiqUsd") or bar.get("buyLiqUsd") or
                                           bar.get("longAmount") or 0)
                        short_usd_a = float(bar.get("shortLiqUsd") or bar.get("sellLiqUsd") or
                                            bar.get("shortAmount") or 0)
                        if p_a > 0 and (long_usd_a + short_usd_a) > 0:
                            combined_a.append({
                                "price": p_a,
                                "longLiqUsd": long_usd_a,
                                "shortLiqUsd": short_usd_a,
                            })
                    except (TypeError, ValueError):
                        continue
                if combined_a:
                    result = _parse_result(combined_a)
                    if result:
                        result["data_source"] = "liq_agg_history"
                        logger.info(
                            f"[爆倉-A✅] {base}: 聚合歷史 {result['label']} "
                            f"距 {result['pct']:.2f}% ${result['total_usd']/1e6:.2f}M"
                        )
    except Exception as e_a:
        logger.debug(f"[爆倉-A] {base} 聚合歷史異常: {e_a}")

    # ── 方案B：即時爆倉訂單（最新的實際爆倉單，精準到個別訂單）──────
    if not result:
        logger.debug(f"[爆倉-B] {base} endpoint={CG_EP['liq_order']}")
        try:
            j_b = _cg_get(CG_EP["liq_order"],
                           {"symbol": base, "limit": 20})
            if j_b:
                rows_b = j_b.get("data") or j_b.get("list") or []
                if isinstance(rows_b, list) and rows_b:
                    combined_b = []
                    for order in rows_b:
                        if not isinstance(order, dict):
                            continue
                        try:
                            p_b = float(order.get("price") or order.get("liqPrice") or 0)
                            usd_b = float(order.get("usd") or order.get("amount_usd") or
                                          order.get("value") or 0)
                            side_b = str(order.get("side") or order.get("direction") or "").lower()
                            if p_b <= 0 or usd_b <= 0:
                                continue
                            combined_b.append({
                                "price": p_b,
                                "longLiqUsd": usd_b if side_b in ("long", "buy", "1") else 0,
                                "shortLiqUsd": usd_b if side_b in ("short", "sell", "2") else 0,
                            })
                        except (TypeError, ValueError):
                            continue
                    if combined_b:
                        result = _parse_result(combined_b)
                        if result:
                            result["data_source"] = "liq_order"
                            logger.info(f"[爆倉-B✅] {base}: 即時訂單 {result['label']} 距 {result['pct']:.2f}%")
        except Exception as e_b:
            logger.debug(f"[爆倉-B] {base} 即時訂單異常: {e_b}")

    # ── 方案C：新爆倉熱力圖 model1/2/3 + 聚合版（最精準的未平倉爆倉位分布）──
    # 這是 CoinGlass 最強的爆倉預測模型，model1=保守估算 model2=中性 model3=激進
    if not result:
        logger.debug(f"[爆倉-C] {base} 嘗試爆倉熱力圖 aggregated-heatmap model1~3")
        # 只用聚合版（交易對熱力圖無權限，只保留 aggregated 版本）
        heatmap_eps = [
            (CG_EP["liq_agg_heatmap_m2"], "agg_m2"),  # 聚合 M2（中性最準）✅ 有權限
            (CG_EP["liq_agg_heatmap_m1"], "agg_m1"),  # 聚合 M1（保守）✅ 有權限
            (CG_EP["liq_agg_heatmap_m3"], "agg_m3"),  # 聚合 M3（激進）✅ 有權限
            # liq_heatmap_m1/m2/m3 = 交易對版，⛔ 無權限，已移除
        ]
        for ep_hm, src_label in heatmap_eps:
            try:
                j_hm = _cg_get(ep_hm, {"symbol": base, "exchange": "Binance",
                                         "interval": "8h"})
                if not j_hm:
                    continue
                raw_hm = j_hm.get("data") or j_hm.get("list") or []
                # 熱力圖通常回傳 price levels with 預估爆倉量
                if isinstance(raw_hm, list) and raw_hm:
                    combined_hm = []
                    for item_hm in raw_hm:
                        if not isinstance(item_hm, dict):
                            continue
                        p_hm = float(item_hm.get("price") or item_hm.get("priceLevel") or
                                     item_hm.get("level") or 0)
                        long_hm = float(item_hm.get("longLiqUsd") or item_hm.get("buy") or
                                        item_hm.get("buyUsd") or 0)
                        short_hm = float(item_hm.get("shortLiqUsd") or item_hm.get("sell") or
                                         item_hm.get("sellUsd") or 0)
                        if p_hm > 0 and (long_hm + short_hm) > 0:
                            combined_hm.append({"price": p_hm, "longLiqUsd": long_hm,
                                                 "shortLiqUsd": short_hm})
                    if combined_hm:
                        result = _parse_result(combined_hm)
                        if result:
                            result["data_source"] = f"liq_heatmap_{src_label}"
                            logger.info(f"[爆倉-C✅] {base}: heatmap {src_label} "
                                        f"{result['label']} 距 {result['pct']:.2f}%")
                            break
                elif isinstance(raw_hm, dict):
                    # 部分 model 回傳 {longs: [...], shorts: [...]}
                    longs_hm = raw_hm.get("longs") or raw_hm.get("long") or []
                    shorts_hm = raw_hm.get("shorts") or raw_hm.get("short") or []
                    combined_hm2 = []
                    for it in (longs_hm or []):
                        if isinstance(it, dict):
                            it["_side"] = "long"
                            combined_hm2.append(it)
                    for it in (shorts_hm or []):
                        if isinstance(it, dict):
                            it["_side"] = "short"
                            combined_hm2.append(it)
                    if combined_hm2:
                        result = _parse_result(combined_hm2)
                        if result:
                            result["data_source"] = f"liq_heatmap_{src_label}"
                            logger.info(f"[爆倉-C✅] {base}: heatmap(dict) {src_label} 距 {result['pct']:.2f}%")
                            break
            except Exception as e_hm:
                logger.debug(f"[爆倉-C] {base} heatmap {src_label} 異常: {e_hm}")
                continue

    # ── 方案D：舊版估算端點（最後備援）──────────────────────────────
    if not result:
        logger.debug(f"[爆倉-D] {base} 嘗試舊版 estimated-levels 端點")
        for endpoint_d in [
            "/api/futures/liquidation/estimated-levels",
            "/api/futures/liquidation/level",
        ]:
            try:
                _respect_coinglass_rate_limit()
                r_d = requests.get(f"{CG_API_BASE}{endpoint_d}",
                                    params={"symbol": base, "exchange": "Binance"},
                                    headers=headers, timeout=8)
                if r_d.status_code in (404, 403):
                    continue
                if r_d.status_code != 200:
                    break
                j_d = r_d.json()
                if j_d.get("code") not in (0, "0", 200, "200", None):
                    continue
                raw_d = j_d.get("data", j_d.get("list", []))
                if isinstance(raw_d, list) and raw_d:
                    result = _parse_result(raw_d)
                    if result:
                        result["data_source"] = "liq_estimated"
                        break
            except Exception as e_d:
                logger.debug(f"[爆倉-D] {base} {endpoint_d} 異常: {e_d}")
                continue

    if result:
        logger.info(
            f"[爆倉熱力圖-A✅] {base}: {result['label']} 距離 {result['pct']:.2f}% "
            f"規模 ${result['total_usd']/1e6:.2f}M"
        )
        _liq_heatmap_cache[cache_key] = (result, now)
        return result

    # ── 方案B：訂單簿熱力圖（orderbook history = 各價位掛單密度，代理爆倉位）──
    # 原理：大量掛單通常就是爆倉單的聚集位，可作為爆倉熱力圖的代理指標
    logger.debug(f"[爆倉熱力圖-B] {base} 嘗試 orderbook heatmap endpoint={CG_EP['ob_heatmap']}")
    try:
        j_ob = _cg_get(CG_EP["ob_heatmap"], {"symbol": base, "interval": "15m", "limit": 2})
        if j_ob:
            raw_ob = j_ob.get("data") or j_ob.get("list") or []
            ob_result: Optional[Dict] = None
            ob_best_pct = proximity_pct + 1
            for bar_ob in (raw_ob if isinstance(raw_ob, list) else []):
                rows_b = bar_ob.get("bids") or bar_ob.get("asks") or []
                if isinstance(bar_ob, dict):
                    rows_b = (bar_ob.get("asks") or []) + (bar_ob.get("bids") or [])
                for row_b in (rows_b if isinstance(rows_b, list) else []):
                    try:
                        if isinstance(row_b, list):
                            p_b, vol_b = float(row_b[0]), float(row_b[1])
                        elif isinstance(row_b, dict):
                            p_b = float(row_b.get("price") or row_b.get("p") or 0)
                            vol_b = float(row_b.get("volume") or row_b.get("v") or row_b.get("qty") or 0)
                        else:
                            continue
                        usd_b = vol_b * p_b
                        if p_b <= 0 or usd_b < 500_000:
                            continue
                        pct_b = abs(p_b - current_price) / current_price * 100
                        if pct_b > proximity_pct:
                            continue
                        if pct_b < ob_best_pct:
                            ob_best_pct = pct_b
                            ob_side = "上方" if p_b > current_price else "下方"
                            ob_result = {
                                "pct": round(pct_b, 2),
                                "price": round(p_b, 6),
                                "side": "多單爆倉" if p_b < current_price else "空單爆倉",
                                "usd": usd_b,
                                "total_usd": usd_b,
                                "label": f"🔶 {ob_side}掛單密集區（代理爆倉位）",
                                "source": "orderbook_proxy",
                            }
                    except (TypeError, ValueError, IndexError):
                        continue
            if ob_result:
                logger.info(
                    f"[爆倉熱力圖-B✅] {base}: 訂單簿代理 {ob_result['label']} "
                    f"距離 {ob_result['pct']:.2f}% 掛單 ${ob_result['total_usd']/1e6:.2f}M"
                )
                _liq_heatmap_cache[cache_key] = (ob_result, now)
                return ob_result
    except Exception as e_b:
        logger.debug(f"[爆倉熱力圖-B] {base} orderbook heatmap 異常: {e_b}")

    logger.debug(f"[爆倉熱力圖-全失敗] {base}: 所有端點均無附近爆倉/掛單聚集")
    _liq_heatmap_cache[cache_key] = (None, now)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# 訂單流分析 (Order Flow Analysis) — 主動買賣、淨多倉、腳步圖關鍵位
# 這三個數據是 TP/SL 精準化的核心，全部使用 CoinGlass 標準版 API
# ══════════════════════════════════════════════════════════════════════════════

_flow_cache: Dict[str, Tuple[Any, float]] = {}   # {cache_key: (data, ts)}
_FLOW_TTL = 90.0   # 90 秒快取（訂單流數據更新頻率高）
_FOOTPRINT_TTL = 120.0  # 腳步圖快取 2 分鐘


def _cg_interval(interval: str) -> str:
    """將標準時間格式轉換為 CoinGlass API 要求的格式。
    文檔確認：taker/net-pos/L-S ratio 等端點使用 h1/m15 格式，非 1h/15m。
    OI history 等端點仍接受 15m，此 helper 統一處理兩邊格式。
    """
    _map = {
        "1m": "m1",  "3m": "m3",  "5m": "m5",  "15m": "m15", "30m": "m30",
        "1h": "h1",  "2h": "h2",  "4h": "h4",  "6h": "h6",
        "8h": "h8",  "12h": "h12","1d": "d1",  "1w": "w1",
        # 已經是正確格式的，原樣返回
        "m1": "m1",  "m3": "m3",  "m5": "m5",  "m15": "m15", "m30": "m30",
        "h1": "h1",  "h2": "h2",  "h4": "h4",  "h6": "h6",
        "h8": "h8",  "h12": "h12","d1": "d1",  "w1": "w1",
    }
    return _map.get(interval, interval)


def _cg_get(path: str, params: Dict) -> Optional[Dict]:
    """輕量 CoinGlass GET wrapper，帶速率限制與統一錯誤處理。"""
    if not CG_API_KEY:
        return None
    try:
        _respect_coinglass_rate_limit()
        r = requests.get(
            f"{CG_API_BASE}{path}",
            headers={"CG-API-KEY": CG_API_KEY, "accept": "application/json"},
            params=params, timeout=10,
        )
        if r.status_code == 429:
            _cb_record_429()
            return None
        if r.status_code != 200:
            return None
        j = r.json()
        if j.get("code") not in (0, "0", 200, "200", None):
            return None
        return j
    except Exception:
        return None


def _parse_taker_ratio_from_rows(rows: list) -> Optional[float]:
    """從各格式的主動買賣歷史列表解析買盤佔比%（通用）。"""
    if not isinstance(rows, list) or not rows:
        return None
    total_buy = total_sell = 0.0
    for row in rows:
        if not isinstance(row, dict):
            continue
        buy = float(row.get("buyVolume") or row.get("buy_volume") or
                    row.get("takerBuyVolume") or row.get("buy") or
                    row.get("buyVol") or 0)
        sell = float(row.get("sellVolume") or row.get("sell_volume") or
                     row.get("takerSellVolume") or row.get("sell") or
                     row.get("sellVol") or 0)
        total_buy += buy
        total_sell += sell
    total = total_buy + total_sell
    if total <= 0:
        return None
    return round(total_buy / total * 100, 1)


def fetch_taker_bvs_ratio(symbol: str, interval: str = "15m", limit: int = 4) -> Optional[float]:
    """主動買賣比（Taker Buy/Sell Volume Ratio）。
    方案A：/api/futures/aggregated-taker-buy-sell-volume/history（幣種聚合，跨所，最準）
    方案B：/api/futures/v2/taker-buy-sell-volume/history（單交易對，Binance 備援）
    方案C：/api/futures/taker-buy-sell-volume/exchange-list（當前各所快照，最後手段）
    回傳買盤佔比 0~100%，附帶 data_source 說明來源
    快取 90 秒。
    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    cache_key = f"taker:{base}:{interval}"
    now = time.time()
    if cache_key in _flow_cache:
        cached, ts = _flow_cache[cache_key]
        if now - ts < _FLOW_TTL:
            logger.debug(f"[主動買賣-快取] {base} ratio={cached}")
            return cached

    # CoinGlass Taker 端點：m15 在山寨幣幾乎全部回傳空陣列
    # 策略：先試 h1（支援最廣），再試 m15，方案C快照固定用 h1
    _try_intervals = ["h1", _cg_interval(interval)] if interval not in ("1h", "h1") else ["h1"]
    _try_intervals = list(dict.fromkeys(_try_intervals))  # 去重保順序

    # ── 方案A：聚合歷史（最精準）──────────────────────────────────
    for _iv_a in _try_intervals:
        logger.debug(f"[主動買賣-A] {base} endpoint={CG_EP['taker_agg_history']} interval={_iv_a} limit={limit}")
        j_a = _cg_get(CG_EP["taker_agg_history"], {"symbol": base, "interval": _iv_a, "limit": limit})
        rows_a = j_a.get("data") or j_a.get("list") or [] if j_a else []
        result = _parse_taker_ratio_from_rows(rows_a)
        if result is not None:
            logger.info(f"[主動買賣-A✅] {base}: 方案A成功({_iv_a})，買盤佔比={result:.1f}% 共{len(rows_a)}棒")
            _flow_cache[cache_key] = (result, now)
            return result
    _a_raw_keys = list(rows_a[0].keys()) if rows_a and isinstance(rows_a[0], dict) else "空陣列"
    logger.warning(f"[主動買賣-A❌] {base}: 方案A無效（已試 {_try_intervals}，rows={len(rows_a) if rows_a else 0}）"
                   f" | 首筆欄位={_a_raw_keys}，改用方案B")

    # ── 方案B：單交易對歷史 v2（多交易所 × h1 輪詢）─────────────────
    sym_param = base + "USDT"
    # 只嘗試實際支援該幣的交易所，跳過 BingX-only 小幣對 Binance/OKX/Bybit 的無效呼叫
    _b_exchanges = get_major_exchanges_for_coin(base, ["Binance", "OKX", "Bybit", "BingX", "Bitget"])
    if not _b_exchanges:
        logger.debug(f"[主動買賣-B] {base}: 無支援大所，跳過方案B，直接方案C")
    else:
        for _b_ex in _b_exchanges:
            for _iv_b in _try_intervals:
                logger.debug(f"[主動買賣-B] {base} exchange={_b_ex} sym={sym_param} interval={_iv_b}")
                j_b = _cg_get(CG_EP["taker_pair_history"], {"symbol": sym_param, "exchange": _b_ex,
                                                             "interval": _iv_b, "limit": limit})
                rows_b = j_b.get("data") or j_b.get("list") or [] if j_b else []
                result = _parse_taker_ratio_from_rows(rows_b)
                if result is not None:
                    logger.info(f"[主動買賣-B✅] {base}: {_b_ex}({_iv_b}) 成功，買盤佔比={result:.1f}%")
                    _flow_cache[cache_key] = (result, now)
                    return result
            logger.debug(f"[主動買賣-B] {base} {_b_ex} 無有效數據")
        logger.warning(f"[主動買賣-B❌] {base}: 所有交易所均無有效數據，改用方案C（各所快照）")

    # ── 方案C：各所當前快照（固定用 h1，覆蓋最廣）────────
    j_c = _cg_get(CG_EP["taker_exchange_list"], {"symbol": base, "range": "h1"})
    rows_c = j_c.get("data") or j_c.get("list") or [] if j_c else []
    result = _parse_taker_ratio_from_rows(rows_c)
    if result is not None:
        logger.info(f"[主動買賣-C✅] {base}: 方案C成功（各所快照 h1），買盤佔比={result:.1f}%")
        _flow_cache[cache_key] = (result, now)
        return result

    logger.warning(f"[主動買賣-全失敗] {base}: A/B/C 三方案均無法獲取，訂單流評分將降級")
    _flow_cache[cache_key] = (None, now)
    return None


def _parse_net_position_from_rows(rows: list) -> Optional[float]:
    """從淨多倉位歷史列表解析正規化方向值（通用）。"""
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    sorted_rows = sorted(rows, key=lambda x: int(x.get("time") or x.get("t") or 0))
    last2 = sorted_rows[-2:]
    vals = []
    for row in last2:
        if not isinstance(row, dict):
            continue
        net_long = float(row.get("netLong") or row.get("net_long") or
                         row.get("longPosition") or row.get("buyPosition") or 0)
        net_short = float(row.get("netShort") or row.get("net_short") or
                          row.get("shortPosition") or row.get("sellPosition") or 0)
        vals.append(net_long - net_short)
    if len(vals) < 2:
        return None
    delta = vals[1] - vals[0]
    base_val = abs(vals[0]) if vals[0] != 0 else 1.0
    norm = delta / base_val if base_val > 0 else 0.0
    return max(-1.0, min(1.0, norm))


def fetch_net_position_delta(symbol: str, interval: str = "15m", limit: int = 3) -> Optional[float]:
    """淨多倉位變化（Net Long Position Delta）。
    方案A：/api/futures/v2/net-position/history（v2版，欄位更完整）
    方案B：/api/futures/net-position/history（v1版，備援）
    回傳 -1~+1 正規化方向（+1=強力加多倉，-1=強力加空倉）
    快取 90 秒。
    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    cache_key = f"netpos:{base}:{interval}"
    now = time.time()
    if cache_key in _flow_cache:
        cached, ts = _flow_cache[cache_key]
        if now - ts < _FLOW_TTL:
            logger.debug(f"[淨多倉-快取] {base} delta={cached}")
            return cached

    sym_param = base + "USDT"
    # 同 Taker 端點：m15 山寨幣幾乎全部空陣列，優先試 h1
    _np_try_ivs = ["h1", _cg_interval(interval)] if interval not in ("1h", "h1") else ["h1"]
    _np_try_ivs = list(dict.fromkeys(_np_try_ivs))
    # 只嘗試實際支援該幣的大所，BingX-only 小幣不浪費 API 呼叫
    _np_exchanges = get_major_exchanges_for_coin(base, ["Binance", "OKX", "Bybit"])

    # 無大所支援直接跳過（BingX-only 小幣無此數據）
    if not _np_exchanges:
        logger.debug(f"[淨多倉] {base}: 無大所支援，跳過 A/B 兩方案")
        _flow_cache[cache_key] = (None, now)
        return None

    # ── 方案A：v2 版（欄位更豐富，文檔確認必填 exchange=）─────────────────
    rows_a: list = []
    for _np_ex in _np_exchanges:
        for _np_iv in _np_try_ivs:
            logger.debug(f"[淨多倉-A] {base} exchange={_np_ex} sym={sym_param} interval={_np_iv}")
            j_a = _cg_get(CG_EP["net_pos_v2"], {"symbol": sym_param, "exchange": _np_ex,
                                                  "interval": _np_iv, "limit": limit})
            rows_a = j_a.get("data") or j_a.get("list") or [] if j_a else []
            result = _parse_net_position_from_rows(rows_a)
            if result is not None:
                logger.info(f"[淨多倉-A✅] {base}: {_np_ex}({_np_iv}) 方案A成功，淨多倉方向={result:+.3f}")
                _flow_cache[cache_key] = (result, now)
                return result
    _np_a_keys = list(rows_a[0].keys()) if rows_a and isinstance(rows_a[0], dict) else "空陣列"
    logger.warning(f"[淨多倉-A❌] {base}: v2 所有交易所無效（已試 {_np_try_ivs}）"
                   f" | 首筆欄位={_np_a_keys}，改用 v1 方案B")

    # ── 方案B：v1 版（備援，同樣帶 exchange 輪詢）────────────────
    for _np_ex in _np_exchanges:
        for _np_iv in _np_try_ivs:
            logger.debug(f"[淨多倉-B] {base} exchange={_np_ex} interval={_np_iv}")
            j_b = _cg_get(CG_EP["net_pos_v1"], {"symbol": sym_param, "exchange": _np_ex,
                                                  "interval": _np_iv, "limit": limit})
            rows_b = j_b.get("data") or j_b.get("list") or [] if j_b else []
            result = _parse_net_position_from_rows(rows_b)
            if result is not None:
                logger.info(f"[淨多倉-B✅] {base}: {_np_ex}({_np_iv}) 方案B成功（v1），淨多倉方向={result:+.3f}")
                _flow_cache[cache_key] = (result, now)
                return result

    logger.warning(f"[淨多倉-全失敗] {base}: A/B 均無效，返回 None（不影響其他指標評分）")
    _flow_cache[cache_key] = (None, now)
    return None


def _extract_footprint_levels(
    rows: list, current_price: float, range_pct: float
) -> Dict[str, Any]:
    """從腳步圖 rows 提取支撐/阻力位（通用解析器）。"""
    empty: Dict[str, Any] = {
        "support_levels": [], "resistance_levels": [],
        "nearest_support": None, "nearest_resistance": None,
        "poc": None, "data_source": "none", "level_count": 0,
    }
    if not rows or current_price <= 0:
        return empty

    price_lo = current_price * (1 - range_pct / 100)
    price_hi = current_price * (1 + range_pct / 100)
    level_buy: Dict[float, float] = {}
    level_sell: Dict[float, float] = {}
    level_total: Dict[float, float] = {}
    tick = current_price * 0.001

    parsed_bars = 0
    parsed_levels = 0
    for bar in rows:
        if not isinstance(bar, dict):
            continue
        parsed_bars += 1
        levels_data = (bar.get("levels") or bar.get("priceLevel") or
                       bar.get("data") or bar.get("footprint") or [])
        if not isinstance(levels_data, list):
            continue
        for lvl in levels_data:
            if not isinstance(lvl, dict):
                continue
            try:
                p = float(lvl.get("price") or lvl.get("p") or 0)
                bv = float(lvl.get("buyVolume") or lvl.get("buy") or lvl.get("bv") or 0)
                sv = float(lvl.get("sellVolume") or lvl.get("sell") or lvl.get("sv") or 0)
            except (TypeError, ValueError):
                continue
            if p <= 0 or not (price_lo <= p <= price_hi):
                continue
            aligned_p = round(p / tick) * tick
            level_buy[aligned_p] = level_buy.get(aligned_p, 0.0) + bv
            level_sell[aligned_p] = level_sell.get(aligned_p, 0.0) + sv
            level_total[aligned_p] = level_total.get(aligned_p, 0.0) + bv + sv
            parsed_levels += 1

    if not level_total:
        return {**empty, "data_source": f"parsed_{parsed_bars}bars_no_levels"}

    poc_price = max(level_total, key=lambda p: level_total[p])
    support_levels = []
    resistance_levels = []
    for p in sorted(level_total, key=lambda x: level_total[x], reverse=True)[:30]:
        bv = level_buy.get(p, 0)
        sv = level_sell.get(p, 0)
        total = bv + sv
        if total <= 0:
            continue
        buy_ratio = bv / total
        if buy_ratio > 0.6 and p < current_price:
            support_levels.append((p, level_total[p], buy_ratio))
        elif buy_ratio < 0.4 and p > current_price:
            resistance_levels.append((p, level_total[p], buy_ratio))

    support_levels.sort(key=lambda x: current_price - x[0])
    resistance_levels.sort(key=lambda x: x[0] - current_price)

    return {
        "support_levels": [x[0] for x in support_levels[:5]],
        "resistance_levels": [x[0] for x in resistance_levels[:5]],
        "nearest_support": support_levels[0][0] if support_levels else None,
        "nearest_resistance": resistance_levels[0][0] if resistance_levels else None,
        "poc": poc_price,
        "data_source": "footprint",
        "level_count": parsed_levels,
        "bars_parsed": parsed_bars,
    }


def _fallback_ob_depth_levels(
    symbol: str, current_price: float, range_pct: float
) -> Dict[str, Any]:
    """腳步圖方案B：用訂單簿聚合深度歷史估算支撐/阻力。
    endpoint: /api/futures/orderbook/aggregated-ask-bids-history
    ask(賣掛) 密集 = 阻力；bid(買掛) 密集 = 支撐
    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    empty: Dict[str, Any] = {
        "support_levels": [], "resistance_levels": [],
        "nearest_support": None, "nearest_resistance": None,
        "poc": None, "data_source": "ob_depth",
    }
    logger.debug(f"[腳步圖-B] {base} 嘗試訂單簿深度歷史 endpoint={CG_EP['ob_agg_ask_bids']}")
    j = _cg_get(CG_EP["ob_agg_ask_bids"], {"symbol": base, "interval": "15m", "limit": 5, "range": str(int(range_pct))})
    if not j:
        return empty
    rows = j.get("data") or j.get("list") or []
    if not isinstance(rows, list) or not rows:
        return empty

    # 解析掛單密集價格：bids 集中 → 支撐，asks 集中 → 阻力
    # 每行通常包含 price/priceLevel 陣列及對應 volume
    bid_levels: Dict[float, float] = {}
    ask_levels: Dict[float, float] = {}
    tick = current_price * 0.001
    price_lo = current_price * (1 - range_pct / 100)
    price_hi = current_price * (1 + range_pct / 100)

    for bar in rows:
        if not isinstance(bar, dict):
            continue
        for side_key, store in [("bids", bid_levels), ("asks", ask_levels)]:
            side_data = bar.get(side_key) or []
            if not isinstance(side_data, list):
                continue
            for item in side_data:
                if not isinstance(item, (list, dict)):
                    continue
                try:
                    if isinstance(item, list):
                        p, vol = float(item[0]), float(item[1])
                    else:
                        p = float(item.get("price") or item.get("p") or 0)
                        vol = float(item.get("volume") or item.get("v") or item.get("amount") or 0)
                except (TypeError, ValueError, IndexError):
                    continue
                if p <= 0 or not (price_lo <= p <= price_hi):
                    continue
                ap = round(p / tick) * tick
                store[ap] = store.get(ap, 0.0) + vol

    support_levels = sorted(
        [(p, v) for p, v in bid_levels.items() if p < current_price],
        key=lambda x: x[1], reverse=True
    )[:5]
    resistance_levels = sorted(
        [(p, v) for p, v in ask_levels.items() if p > current_price],
        key=lambda x: x[1], reverse=True
    )[:5]

    sup_nearest = min(support_levels, key=lambda x: current_price - x[0])[0] if support_levels else None
    res_nearest = min(resistance_levels, key=lambda x: x[0] - current_price)[0] if resistance_levels else None

    return {
        "support_levels": [x[0] for x in support_levels],
        "resistance_levels": [x[0] for x in resistance_levels],
        "nearest_support": sup_nearest,
        "nearest_resistance": res_nearest,
        "poc": None,
        "data_source": "ob_depth",
    }


def fetch_footprint_key_levels(
    symbol: str,
    current_price: float,
    direction_is_long: bool,
    interval: str = "15m",
    limit: int = 20,
    range_pct: float = 8.0,
) -> Dict[str, Any]:
    """關鍵支撐/阻力位分析（替代腳步圖，採用可用 API）。

    ⚠️  /api/futures/volume/footprint-history 需更高授權等級，目前跳過。
        若未來升級帳號可將 _FOOTPRINT_API_ENABLED 改為 True。

    方案A：/api/futures/orderbook/aggregated-ask-bids-history
           掛單密集位 —— bid 聚集 = 支撐，ask 聚集 = 阻力
    方案B：/api/futures/orderbook/ask-bids-history
           單交易所訂單簿歷史（Binance，備援）
    方案C：taker 主動買賣集中價位（aggregated-taker-buy-sell-volume/history）
           主動買入最多 ≈ 強力支撐；主動賣出最多 ≈ 強力阻力
    方案D：空結構（data_source='unavailable'），TP/SL 回退 ATR/結構

    快取 2 分鐘。
    """
    _FOOTPRINT_API_ENABLED = False  # 升級帳號後設為 True 即可啟用

    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    cache_key = f"footprint:{base}:{interval}"
    now = time.time()
    empty_result: Dict[str, Any] = {
        "support_levels": [], "resistance_levels": [],
        "nearest_support": None, "nearest_resistance": None,
        "poc": None, "data_source": "unavailable",
    }
    if cache_key in _flow_cache:
        cached, ts = _flow_cache[cache_key]
        if now - ts < _FOOTPRINT_TTL:
            logger.debug(f"[關鍵位-快取] {base} sup={cached.get('nearest_support')} res={cached.get('nearest_resistance')}")
            return cached if cached else empty_result

    if not current_price or current_price <= 0:
        return empty_result

    # ── 若未來升級可解鎖：腳步圖歷史 API ──────────────────────────
    if _FOOTPRINT_API_ENABLED:
        logger.debug(f"[關鍵位-FP] {base} 嘗試腳步圖API endpoint={CG_EP['footprint']}")
        j_fp = _cg_get(CG_EP["footprint"], {"symbol": base, "interval": interval, "limit": limit})
        rows_fp = j_fp.get("data") or j_fp.get("list") or [] if j_fp else []
        if isinstance(rows_fp, list) and rows_fp:
            fp_result = _extract_footprint_levels(rows_fp, current_price, range_pct)
            if fp_result.get("nearest_support") is not None or fp_result.get("nearest_resistance") is not None:
                fp_result["data_source"] = "footprint_history"
                logger.info(f"[關鍵位-FP✅] {base}: 腳步圖成功 POC={fp_result.get('poc','N/A')}")
                _flow_cache[cache_key] = (fp_result, now)
                return fp_result

    # ── 方案A：聚合訂單簿深度歷史（掛單密集位）─────────────────────
    logger.debug(f"[關鍵位-A] {base} 聚合訂單簿深度 endpoint={CG_EP['ob_agg_ask_bids']}")
    ob_result = _fallback_ob_depth_levels(symbol, current_price, range_pct)
    if ob_result.get("nearest_support") is not None or ob_result.get("nearest_resistance") is not None:
        ob_result["data_source"] = "ob_depth_agg"
        sup_s = f"{ob_result['nearest_support']:.6g}" if ob_result.get('nearest_support') else "N/A"
        res_s = f"{ob_result['nearest_resistance']:.6g}" if ob_result.get('nearest_resistance') else "N/A"
        logger.info(f"[關鍵位-A✅] {base}: 聚合訂單簿 支撐={sup_s} 阻力={res_s}")
        _flow_cache[cache_key] = (ob_result, now)
        return ob_result
    logger.debug(f"[關鍵位-A❌] {base}: 聚合訂單簿無有效位，嘗試方案B")

    # ── 方案B：單交易所訂單簿歷史（Binance）──────────────────────
    logger.debug(f"[關鍵位-B] {base} 單所訂單簿 endpoint={CG_EP['ob_ask_bids_history']}")
    try:
        j_b = _cg_get(CG_EP["ob_ask_bids_history"],
                      {"symbol": base, "exchange": "Binance", "interval": interval, "limit": 5})
        if j_b:
            rows_b = j_b.get("data") or j_b.get("list") or []
            price_lo = current_price * (1 - range_pct / 100)
            price_hi = current_price * (1 + range_pct / 100)
            tick = current_price * 0.001
            bid_lv_b: Dict[float, float] = {}
            ask_lv_b: Dict[float, float] = {}
            for bar_b in (rows_b if isinstance(rows_b, list) else []):
                if not isinstance(bar_b, dict):
                    continue
                for sk, store_b in [("bids", bid_lv_b), ("asks", ask_lv_b)]:
                    for it_b in (bar_b.get(sk) or []):
                        try:
                            if isinstance(it_b, list):
                                pb, vb = float(it_b[0]), float(it_b[1])
                            elif isinstance(it_b, dict):
                                pb = float(it_b.get("price") or 0)
                                vb = float(it_b.get("volume") or it_b.get("amount") or 0)
                            else:
                                continue
                            if pb <= 0 or not (price_lo <= pb <= price_hi):
                                continue
                            ap_b = round(pb / tick) * tick
                            store_b[ap_b] = store_b.get(ap_b, 0.0) + vb
                        except (TypeError, ValueError, IndexError):
                            continue
            sup_b = sorted([(p, v) for p, v in bid_lv_b.items() if p < current_price],
                           key=lambda x: x[1], reverse=True)[:5]
            res_b = sorted([(p, v) for p, v in ask_lv_b.items() if p > current_price],
                           key=lambda x: x[1], reverse=True)[:5]
            sup_near_b = min(sup_b, key=lambda x: current_price - x[0])[0] if sup_b else None
            res_near_b = min(res_b, key=lambda x: x[0] - current_price)[0] if res_b else None
            if sup_near_b is not None or res_near_b is not None:
                r_b = {"support_levels": [x[0] for x in sup_b],
                       "resistance_levels": [x[0] for x in res_b],
                       "nearest_support": sup_near_b,
                       "nearest_resistance": res_near_b,
                       "poc": None, "data_source": "ob_depth_binance"}
                logger.info(f"[關鍵位-B✅] {base}: Binance訂單簿 支撐={sup_near_b or 'N/A'} 阻力={res_near_b or 'N/A'}")
                _flow_cache[cache_key] = (r_b, now)
                return r_b
    except Exception as e_b:
        logger.debug(f"[關鍵位-B] {base} Binance訂單簿異常: {e_b}")
    logger.debug(f"[關鍵位-B❌] {base}: Binance訂單簿無有效位，嘗試方案C（taker濃度）")

    # ── 方案C：taker 主動買賣集中價位（聚合歷史）────────────────────
    # taker 大量主動買入的區間 ≈ 市場認可的強支撐；大量主動賣出 ≈ 強阻力
    logger.debug(f"[關鍵位-C] {base} taker集中位 endpoint={CG_EP['taker_agg_history']}")
    try:
        j_t = _cg_get(CG_EP["taker_agg_history"],
                      {"symbol": base, "interval": interval, "limit": 8})
        if j_t:
            rows_t = j_t.get("data") or j_t.get("list") or []
            # 尋找 taker 主動買賣最集中的 K 線的收盤價 = 核心成交位
            best_buy_price: Optional[float] = None
            best_sell_price: Optional[float] = None
            max_buy_vol = max_sell_vol = 0.0
            for bar_t in (rows_t if isinstance(rows_t, list) else []):
                if not isinstance(bar_t, dict):
                    continue
                try:
                    buy_v = float(bar_t.get("buyVolume") or bar_t.get("buy") or
                                  bar_t.get("takerBuy") or bar_t.get("takerBuyVolume") or 0)
                    sell_v = float(bar_t.get("sellVolume") or bar_t.get("sell") or
                                   bar_t.get("takerSell") or bar_t.get("takerSellVolume") or 0)
                    p_c = float(bar_t.get("closePrice") or bar_t.get("price") or
                                bar_t.get("close") or bar_t.get("c") or 0)
                    ts_t = int(bar_t.get("t") or bar_t.get("timestamp") or bar_t.get("ts") or 0)
                except (TypeError, ValueError):
                    continue
                if p_c <= 0:
                    continue
                price_lo_t = current_price * (1 - range_pct / 100)
                price_hi_t = current_price * (1 + range_pct / 100)
                if not (price_lo_t <= p_c <= price_hi_t):
                    continue
                if buy_v > max_buy_vol:
                    max_buy_vol, best_buy_price = buy_v, p_c
                if sell_v > max_sell_vol:
                    max_sell_vol, best_sell_price = sell_v, p_c
            sup_c = best_buy_price if (best_buy_price and best_buy_price < current_price) else None
            res_c = best_sell_price if (best_sell_price and best_sell_price > current_price) else None
            if sup_c is not None or res_c is not None:
                r_c = {"support_levels": [sup_c] if sup_c else [],
                       "resistance_levels": [res_c] if res_c else [],
                       "nearest_support": sup_c, "nearest_resistance": res_c,
                       "poc": best_buy_price, "data_source": "taker_concentration"}
                logger.info(f"[關鍵位-C✅] {base}: taker集中位 支撐={sup_c or 'N/A'} 阻力={res_c or 'N/A'}")
                _flow_cache[cache_key] = (r_c, now)
                return r_c
    except Exception as e_c:
        logger.debug(f"[關鍵位-C] {base} taker集中位異常: {e_c}")

    logger.warning(f"[關鍵位-全失敗] {base}: A/B/C 三方案均無有效關鍵位，TP/SL 回退 ATR/結構計算")
    _flow_cache[cache_key] = (empty_result, now)
    return empty_result


def compute_flow_score(
    taker_ratio: Optional[float],
    net_pos_delta: Optional[float],
    is_long: bool,
) -> int:
    """訂單流綜合評分（0~4）。每個有效確認因素 +1 分。
    - taker_ratio 方向對齊（做多>58% / 做空<42%）→ +1
    - taker_ratio 強力（做多>65% / 做空<35%）→ 再+1
    - net_pos_delta 方向對齊（做多>0 / 做空<0）→ +1
    - net_pos_delta 強力（|delta| > 0.3）→ 再+1
    分數 0=中性，1-2=中度確認，3-4=強力確認
    """
    score = 0
    if taker_ratio is not None:
        if is_long and taker_ratio > 58:
            score += 1
            if taker_ratio > 65:
                score += 1
        elif (not is_long) and taker_ratio < 42:
            score += 1
            if taker_ratio < 35:
                score += 1
    if net_pos_delta is not None:
        if is_long and net_pos_delta > 0.05:
            score += 1
            if net_pos_delta > 0.3:
                score += 1
        elif (not is_long) and net_pos_delta < -0.05:
            score += 1
            if net_pos_delta < -0.3:
                score += 1
    return min(score, 4)


def sl_plain_desc(sl_source: str, is_long: bool, fp_data_source: str = "") -> str:
    """將止損來源轉成白話說明（簡短，一句話）。"""
    # 依數據來源加上補充說明
    if "footprint_history" in fp_data_source:
        src_note = "（腳步圖實際逐筆成交驗證）"
    elif "ob_depth_agg" in fp_data_source:
        src_note = "（全網聚合訂單簿掛單密集位）"
    elif "ob_depth_binance" in fp_data_source:
        src_note = "（Binance 訂單簿掛單密集位）"
    elif "taker_concentration" in fp_data_source:
        src_note = "（近期主動買方成交最集中的區域）"
    elif "ob_depth" in fp_data_source:
        src_note = "（訂單簿掛單密集位估算）"
    else:
        src_note = ""

    if "腳步圖" in sl_source or "訂單簿支撐" in sl_source:
        return f"掛單最密集的買盤支撐區{src_note}，跌破代表買方全面退場"
    if "taker支撐" in sl_source:
        return f"近期主動買入最集中的區域{src_note}，這裡是真實成交形成的支撐"
    if "結構低點" in sl_source or "結構高點" in sl_source:
        return f"近2小時K線{'最低' if is_long else '最高'}點下方，破位則{'多' if is_long else '空'}方結構崩潰"
    if "K線結構" in sl_source:
        return f"當前15m K線{'低' if is_long else '高'}點，這根K線守住才有效"
    return "ATR動態計算，根據近期真實波動幅度自動設定安全距離"


def tp_plain_desc(tp_label: str, is_long: bool, fp_data_source: str = "") -> str:
    """將止盈標籤轉成白話說明（簡短，一句話）。"""
    if not tp_label:
        return ""
    if "footprint_history" in fp_data_source:
        src_note = "（腳步圖成交密集的賣盤壓力區）"
    elif "ob_depth_agg" in fp_data_source or "ob_depth_binance" in fp_data_source:
        src_note = "（訂單簿掛單牆，賣壓集中區）"
    elif "taker_concentration" in fp_data_source:
        src_note = "（主動賣出最集中的區域）"
    else:
        src_note = ""

    if "腳步圖阻力" in tp_label or "訂單簿阻力" in tp_label:
        return f"掛單最密集的賣盤阻力區{src_note}，到這裡賣盤壓力大增"
    if "taker阻力" in tp_label:
        return f"近期主動賣出最集中的價位{src_note}，是市場公認的出貨壓力區"
    if "主力成本" in tp_label:
        return "2h均線VWAP的對稱位，主力成本區上方，通常是套牢盤出清點"
    if "1.2R" in tp_label or "1.0R" in tp_label:
        r = tp_label.replace("R", "").strip()
        return f"風報比{r}倍目標，即賺{r}倍止損距離的利潤"
    if "R" in tp_label:
        r_val = tp_label.replace("R", "").strip().split("(")[-1] if "(" in tp_label else tp_label.replace("R", "").strip()
        return f"約{r_val}倍風報比目標"
    return ""


# ── 大額掛單牆監控 ─────────────────────────────────────────────────────────────

_orderbook_wall_cache: Dict[str, Tuple[Optional[Dict], float]] = {}
_OB_WALL_TTL = 45.0       # 45 秒快取（掛單牆變動較快）
_OB_WALL_MIN_USD = 800_000  # 80 萬 USD 以上才算「巨量牆」


def check_orderbook_wall(
    symbol: str,
    current_price: float,
    is_long: bool,
    preferred_symbol: Optional[str] = None,
    scan_pct: float = 2.0,
) -> Optional[Dict[str, Any]]:
    """【大額掛單牆】掃描進場方向的巨量訂單層。

    優先使用 CoinGlass /api/futures/orderbook/large-limit-order（直接提供大額掛單）。
    CoinGlass 失敗時備援 BingX /openApi/swap/v2/quote/depth（掃描前 50 層）。

    做多：掃描現價「上方 scan_pct%」內的大額 Asks（賣牆/阻力）
    做空：掃描現價「下方 scan_pct%」內的大額 Bids（買牆/支撐）

    回傳：
        {"wall_usd": float, "wall_price": float,
         "pct_away": float, "label": str}
        或 None（無明顯大牆 / API 失敗）
    """
    if not current_price or current_price <= 0:
        return None

    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    cache_key = f"{base}:{'ask' if is_long else 'bid'}"
    now = time.time()

    if cache_key in _orderbook_wall_cache:
        cached, ts = _orderbook_wall_cache[cache_key]
        if now - ts < _OB_WALL_TTL:
            return cached

    scan_limit_hi = current_price * (1 + scan_pct / 100)
    scan_limit_lo = current_price * (1 - scan_pct / 100)

    def _build_result(wall_usd: float, wall_price: float) -> Optional[Dict]:
        if wall_usd < _OB_WALL_MIN_USD:
            return None
        direction = "上方賣壓" if is_long else "下方買盤"
        pct_away = abs(wall_price - current_price) / current_price * 100
        lbl = (
            f"🧱 {direction}牆 ${wall_usd/1e6:.2f}M "
            f"@ {wall_price:.6g} "
            f"(距現價 {pct_away:.2f}%)"
        )
        logger.info(f"[掛單牆] {base}: {lbl}")
        return {"wall_usd": wall_usd, "wall_price": wall_price,
                "pct_away": pct_away, "label": lbl}

    # ── 優先：CoinGlass large-limit-order ─────────────────────────────────
    try:
        _respect_coinglass_rate_limit()
        cg_side = "asks" if is_long else "bids"
        r_cg = requests.get(
            f"{CG_API_BASE}/api/futures/orderbook/large-limit-order",
            headers={"CG-API-KEY": CG_API_KEY, "accept": "application/json"},
            params={"symbol": base, "side": cg_side},
            timeout=8,
        )
        if r_cg.status_code == 200:
            j_cg = r_cg.json()
            if j_cg.get("code") in (0, "0", 200, "200", None):
                orders = j_cg.get("data") or j_cg.get("list") or []
                if isinstance(orders, list) and orders:
                    best_usd, best_price = 0.0, 0.0
                    for order in orders:
                        try:
                            p = float(order.get("price") or order.get("p") or 0)
                            # 金額可能直接給 USD，或給數量需自行換算
                            usd = float(order.get("amount_usd") or order.get("amountUsd")
                                        or order.get("value") or order.get("size") or 0)
                            qty = float(order.get("qty") or order.get("amount")
                                        or order.get("quantity") or 0)
                            if usd == 0 and qty > 0 and p > 0:
                                usd = qty * p
                            if p <= 0 or usd <= 0:
                                continue
                            # 只取掃描範圍內的單
                            if is_long and not (current_price < p <= scan_limit_hi):
                                continue
                            if not is_long and not (scan_limit_lo <= p < current_price):
                                continue
                            if usd > best_usd:
                                best_usd, best_price = usd, p
                        except (TypeError, ValueError):
                            continue
                    if best_usd >= _OB_WALL_MIN_USD:
                        result = _build_result(best_usd, best_price)
                        _orderbook_wall_cache[cache_key] = (result, now)
                        return result
    except Exception as e:
        logger.debug(f"[掛單牆-CG] {base} CoinGlass large-limit-order 失敗: {e}")

    # ── 方案B：CoinGlass large-limit-order-history（歷史大單，更可靠）────────
    logger.debug(f"[掛單牆-B] {base} 嘗試 large-limit-order-history endpoint={CG_EP['ob_large_order_hist']}")
    try:
        j_hist = _cg_get(CG_EP["ob_large_order_hist"],
                         {"symbol": base, "interval": "15m", "limit": 3})
        if j_hist:
            orders_h = j_hist.get("data") or j_hist.get("list") or []
            best_usd_h, best_price_h = 0.0, 0.0
            for bar in (orders_h if isinstance(orders_h, list) else []):
                bar_orders = bar.get("orders") or bar.get("data") or (bar if isinstance(bar, list) else [])
                if not isinstance(bar_orders, list):
                    bar_orders = [bar]
                for order in bar_orders:
                    if not isinstance(order, dict):
                        continue
                    try:
                        p = float(order.get("price") or order.get("p") or 0)
                        side = str(order.get("side") or order.get("type") or "").lower()
                        if is_long and side not in ("ask", "sell", "2", ""):
                            continue
                        if not is_long and side not in ("bid", "buy", "1", ""):
                            continue
                        usd = float(order.get("amount_usd") or order.get("amountUsd") or
                                    order.get("value") or order.get("size") or 0)
                        qty = float(order.get("qty") or order.get("amount") or order.get("quantity") or 0)
                        if usd == 0 and qty > 0 and p > 0:
                            usd = qty * p
                        if p <= 0 or usd <= 0:
                            continue
                        if is_long and not (current_price < p <= scan_limit_hi):
                            continue
                        if not is_long and not (scan_limit_lo <= p < current_price):
                            continue
                        if usd > best_usd_h:
                            best_usd_h, best_price_h = usd, p
                    except (TypeError, ValueError):
                        continue
            if best_usd_h >= _OB_WALL_MIN_USD:
                result_h = _build_result(best_usd_h, best_price_h)
                if result_h:
                    logger.info(f"[掛單牆-B✅] {base}: 方案B(歷史大單)成功 ${best_usd_h/1e6:.2f}M @ {best_price_h:.6g}")
                    _orderbook_wall_cache[cache_key] = (result_h, now)
                    return result_h
            logger.debug(f"[掛單牆-B] {base}: 解析到資料但金額未達門檻 (max={best_usd_h:.0f} USD)")
    except Exception as e_b:
        logger.debug(f"[掛單牆-B] {base} large-limit-order-history 異常: {e_b}")

    # ── 方案C：orderbook aggregated ask-bids（訂單簿深度，最後手段）──────────
    logger.debug(f"[掛單牆-C] {base} 嘗試 aggregated ask-bids endpoint={CG_EP['ob_agg_ask_bids']}")
    try:
        side_c = "asks" if is_long else "bids"
        j_c = _cg_get(CG_EP["ob_agg_ask_bids"],
                       {"symbol": base, "interval": "15m", "limit": 2, "range": "2"})
        if j_c:
            rows_c = j_c.get("data") or j_c.get("list") or []
            best_usd_c, best_price_c = 0.0, 0.0
            for bar_c in (rows_c if isinstance(rows_c, list) else []):
                for order_c in (bar_c.get(side_c) or [] if isinstance(bar_c, dict) else []):
                    try:
                        if isinstance(order_c, list):
                            p_c, vol_c = float(order_c[0]), float(order_c[1])
                        elif isinstance(order_c, dict):
                            p_c = float(order_c.get("price") or 0)
                            vol_c = float(order_c.get("volume") or order_c.get("amount") or 0)
                        else:
                            continue
                        usd_c = vol_c * p_c if p_c > 0 else 0
                        if is_long and not (current_price < p_c <= scan_limit_hi):
                            continue
                        if not is_long and not (scan_limit_lo <= p_c < current_price):
                            continue
                        if usd_c > best_usd_c:
                            best_usd_c, best_price_c = usd_c, p_c
                    except (TypeError, ValueError, IndexError):
                        continue
            if best_usd_c >= _OB_WALL_MIN_USD:
                result_c = _build_result(best_usd_c, best_price_c)
                if result_c:
                    logger.info(f"[掛單牆-C✅] {base}: 方案C(訂單簿深度)成功 ${best_usd_c/1e6:.2f}M @ {best_price_c:.6g}")
                    _orderbook_wall_cache[cache_key] = (result_c, now)
                    return result_c
    except Exception as e_c:
        logger.debug(f"[掛單牆-C] {base} 訂單簿深度異常: {e_c}")

    logger.debug(f"[掛單牆-全失敗] {base}: A/B/C 三方案均無明顯大牆（門檻 ${_OB_WALL_MIN_USD/1e6:.1f}M）")
    _orderbook_wall_cache[cache_key] = (None, now)
    return None


def normalize_symbol(coin: Dict) -> Optional[str]:
    """從幣種數據中提取 symbol"""
    return coin.get('symbol') or coin.get('pair') or coin.get('name') or coin.get('coin') or coin.get('symbolName')


def extract_price_change_15m(coin: Dict) -> float:
    """提取 15 分鐘價格變化%（其他模組用）"""
    change = coin.get('price_change_percent_15m')
    if isinstance(change, (int, float)):
        return float(change)
    if isinstance(change, str) and change:
        try:
            parsed = float(change)
            if not (parsed != parsed):
                return parsed
        except ValueError:
            pass
    change = coin.get('price_change_percent_1h')
    if isinstance(change, (int, float)):
        return float(change)
    change = coin.get('price_change_percent_24h')
    if isinstance(change, (int, float)):
        return float(change)
    return 0.0


def extract_price_change_30m(coin: Dict) -> float:
    """提取 15 分鐘價格變化%（持倉篩選 15m 高頻版：價格與 OI 皆 15m）"""
    change = coin.get('price_change_percent_30m')
    if isinstance(change, (int, float)):
        return float(change)
    if isinstance(change, str) and change:
        try:
            parsed = float(change)
            if not (parsed != parsed):
                return parsed
        except ValueError:
            pass
    change = coin.get('price_change_percent_15m')
    if isinstance(change, (int, float)):
        return float(change)
    change = coin.get('price_change_percent_1h')
    if isinstance(change, (int, float)):
        return float(change)
    change = coin.get('price_change_percent_24h')
    if isinstance(change, (int, float)):
        return float(change)
    return 0.0


def extract_price_change_24h(coin: Dict) -> Optional[float]:
    """提取 24 小時價格變化%（用於假抄底/假摸頭過濾：24h 大漲不標抄底、24h 大跌不標摸頭）"""
    for key in ('price_change_percent_24h', 'priceChange24h', 'change_24h', 'change24h'):
        change = coin.get(key)
        if isinstance(change, (int, float)) and change == change:
            return float(change)
        if isinstance(change, str) and change:
            try:
                parsed = float(change)
                if parsed == parsed:
                    return parsed
            except ValueError:
                pass
    return None


def fetch_coinglass_indicator(
    symbol: str,
    indicator_name: str,
    interval: str = "30m",
) -> Optional[Union[float, Dict[str, Any]]]:
    """
    通用 CoinGlass 技術指標 API：支援 ATR（平均真實波幅）、BOLL（布林帶）。
    - indicator_name: 'atr' | 'boll'
    - 回傳：atr 為最新一筆 ATR 數值 (float)；boll 為完整 API 回應 (dict，含 data/list)。
    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    symbol_param = base + "USDT"
    path_map = {"atr": "avg-true-range", "boll": "boll"}
    path = path_map.get(indicator_name.lower()) if indicator_name else None
    if not path:
        return None
    url = f"{CG_API_BASE}/api/futures/indicators/{path}"
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}
    if indicator_name.lower() == "atr":
        tries = [("Binance", symbol_param)]
    else:
        tries = [("Binance", symbol_param), ("BingX", symbol_param), ("BingX", base)]
    for exchange, sym in tries:
        params = {"exchange": exchange, "symbol": sym, "interval": interval}
        try:
            time.sleep(0.3 if indicator_name.lower() == "atr" else 0.2)
            r = requests.get(url, params=params, headers=headers, timeout=8)
            if r.status_code == 429 or "Too Many Requests" in r.text:
                return None
            if r.status_code != 200:
                continue
            data = r.json()
            if data.get("code") not in (0, "0", 200, "200", None):
                continue
            msg = (data.get("msg") or data.get("message") or "").lower()
            if "instrument" in msg:
                continue
            raw = data.get("data", data.get("list", []))
            if indicator_name.lower() == "atr":
                if isinstance(raw, list) and raw:
                    last = raw[-1] if isinstance(raw[-1], dict) else None
                elif isinstance(raw, dict):
                    last = raw
                else:
                    continue
                if not last:
                    continue
                for key in ("avg_true_range_value", "atr", "value", "avg_true_range", "avgTrueRange"):
                    v = last.get(key)
                    if v is not None:
                        try:
                            val = float(v)
                            if val > 0:
                                return val
                        except (TypeError, ValueError):
                            pass
            else:
                if (isinstance(raw, list) and len(raw) > 0) or (isinstance(raw, dict) and raw):
                    return data
        except Exception:
            continue
    return None


def _fetch_coinglass_rsi(symbol: str) -> Optional[Dict]:
    """CoinGlass V4 RSI：降速版，失敗直接放棄切換本地計算（省 API 額度）。"""
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    symbol_pair = base + "USDT"
    url = f"{CG_API_BASE}/api/futures/indicators/rsi"
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}
    tries = [("Binance", symbol_pair)]

    for exchange, sym_param in tries:
        params = {"exchange": exchange, "interval": "15m", "symbol": sym_param}
        try:
            time.sleep(1.0)
            r = requests.get(url, params=params, headers=headers, timeout=8)
            if r.status_code == 429 or "Too Many Requests" in r.text:
                logger.warning(f"CoinGlass RSI 限流，跳過 {base}")
                return None
            if r.status_code != 200:
                continue
            data = r.json()
            if data.get("code") not in (0, "0", 200, "200", None):
                continue
            raw = data.get("data", data.get("list", []))
            if raw:
                return data
        except Exception:
            pass
    return None


def _fetch_coinglass_boll(symbol: str) -> Optional[Dict]:
    """CoinGlass V4 布林帶：委由 fetch_coinglass_indicator(symbol, 'boll', '15m')。"""
    out = fetch_coinglass_indicator(symbol, "boll", "15m")
    return out if isinstance(out, dict) else None


def _fetch_coinglass_atr(symbol: str, interval: str = "1d") -> Optional[float]:
    """CoinGlass V4 平均真實波幅（ATR）：委由 fetch_coinglass_indicator(symbol, 'atr', interval)。"""
    out = fetch_coinglass_indicator(symbol, "atr", interval)
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    if isinstance(out, (int, float)) and out > 0:
        logger.info(f"ATR 取得 {base} ({interval}): 已取得 = {out}")
        return float(out)
    logger.info(f"ATR 取得 {base} ({interval}): 未取得（API 無回傳／限流／或非正數）")
    return None


def _fetch_coinglass_ema(symbol: str, interval: str = "15m") -> Optional[float]:
    """CoinGlass V4 EMA：/api/futures/indicators/ema，取 EMA20。"""
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    symbol_param = base + "USDT"
    url = f"{CG_API_BASE}/api/futures/indicators/ema"
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}
    for exchange in ("Binance", "BingX"):
        params = {"exchange": exchange, "symbol": symbol_param, "interval": interval}
        try:
            time.sleep(0.25)
            r = requests.get(url, params=params, headers=headers, timeout=8)
            if r.status_code != 200 or "Too Many Requests" in (r.text or ""):
                continue
            data = r.json()
            if data.get("code") not in (0, "0", 200, "200", None):
                continue
            raw = data.get("data", data.get("list", []))
            if isinstance(raw, list) and raw:
                last = raw[-1] if isinstance(raw[-1], dict) else None
                if last:
                    for k in ("ema20", "ema_20", "value", "ema"):
                        v = last.get(k)
                        if v is not None:
                            try:
                                val = float(v)
                                if val > 0:
                                    logger.info(f"[技術指標] {base}: EMA API 取得 ema20={val}")
                                    return val
                            except (TypeError, ValueError):
                                pass
        except Exception:
            pass
    logger.info(f"[技術指標] {base}: EMA API 無回傳或無 EMA20")
    return None


def _fetch_coinglass_macd(symbol: str, interval: str = "15m") -> Optional[Dict]:
    """CoinGlass V4 MACD：/api/futures/indicators/macd。回傳最後一筆 MACD 相關數值或 None。"""
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    symbol_param = base + "USDT"
    url = f"{CG_API_BASE}/api/futures/indicators/macd"
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}
    for exchange in ("Binance", "BingX"):
        params = {"exchange": exchange, "symbol": symbol_param, "interval": interval}
        try:
            time.sleep(0.25)
            r = requests.get(url, params=params, headers=headers, timeout=8)
            if r.status_code != 200 or "Too Many Requests" in (r.text or ""):
                continue
            data = r.json()
            if data.get("code") not in (0, "0", 200, "200", None):
                continue
            raw = data.get("data", data.get("list", []))
            if isinstance(raw, list) and raw and isinstance(raw[-1], dict):
                logger.info(f"[技術指標] {base}: MACD API 取得")
                return raw[-1]
        except Exception:
            pass
    logger.info(f"[技術指標] {base}: MACD API 無回傳")
    return None


def _fetch_coinglass_cgdi_history(symbol: Optional[str] = None, interval: str = "1d") -> Optional[Dict]:
    """CoinGlass CGDI 指數：/api/futures/cgdi-index/history，用於大盤情緒。可傳 symbol 或取整體。"""
    url = f"{CG_API_BASE}/api/futures/cgdi-index/history"
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}
    params = {"exchange": "Binance", "interval": interval}
    if symbol:
        base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
        params["symbol"] = base + "USDT"
    try:
        time.sleep(0.2)
        r = requests.get(url, params=params, headers=headers, timeout=8)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("code") not in (0, "0", 200, "200", None):
            return None
        if data.get("data") or data.get("list"):
            return data
    except Exception:
        pass
    return None


def fetch_coinglass_whale_index_history(
    symbol: str,
    exchange: str = "Binance",
    interval: str = "1d",
) -> Optional[Dict[str, Any]]:
    """
    CoinGlass V4 鯨魚指數歷史數據。
    GET /api/futures/whale-index/history?exchange=Binance&symbol=BTCUSDT&interval=1d
    回傳完整 API 回應 (含 data/list)，失敗回傳 None。
    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    symbol_param = base + "USDT"
    url = f"{CG_API_BASE}/api/futures/whale-index/history"
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}
    params = {"exchange": exchange, "symbol": symbol_param, "interval": interval}
    try:
        time.sleep(0.3)
        r = requests.get(url, params=params, headers=headers, timeout=10)
        if r.status_code == 429 or "Too Many Requests" in r.text:
            return None
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("code") not in (0, "0", 200, "200", None):
            return None
        return data
    except Exception:
        return None


def _whale_index_latest(symbol: str, interval: str = "1d") -> Optional[float]:
    """
    取得鯨魚方向指標：先試 CoinGlass 鯨魚指數 API，無數據則 fallback 大戶持倉多空比。
    回傳值統一為 0~100 概念（>50 偏多、<50 偏空），供背離濾網使用。
    文件: https://docs.coinglass.com/v4.0-zh/reference/鲸鱼指数
    """
    data = fetch_coinglass_whale_index_history(symbol, interval=interval)
    if data:
        raw = data.get("data", data.get("list", []))
        if isinstance(raw, list) and raw:
            last = raw[-1] if isinstance(raw[-1], dict) else None
            if last:
                for key in ("whale_index", "value", "index", "values"):
                    v = last.get(key)
                    if v is not None:
                        try:
                            val = float(v)
                            if 0 <= val <= 100:
                                return val
                            if 0 <= val <= 1:
                                return val * 100
                            return val
                        except (TypeError, ValueError):
                            pass
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    symbol_param = base + "USDT"
    fallback_data = fetch_top_position_ratio(symbol_param, interval)
    if not fallback_data:
        logger.info(f"鯨魚指數 {base}: 無數據（鯨魚指數 API 與 fallback 大戶持倉比皆無回傳）")
        return None
    point = get_latest_data_point(fallback_data)
    if not point or not isinstance(point, dict):
        logger.info(f"鯨魚指數 {base}: 無數據（fallback 大戶比無最新一筆）")
        return None
    ratio = point.get("top_position_long_short_ratio")
    if ratio is None:
        logger.info(f"鯨魚指數 {base}: 無數據（fallback 大戶比無 top_position_long_short_ratio）")
        return None
    try:
        r = float(ratio)
        if r <= 0:
            return None
        normalized = 50.0 * r
        logger.info(f"鯨魚指數 {base}: 使用 fallback 大戶持倉比 ratio={r:.3f} → 標準化 {normalized:.1f}")
        return normalized
    except (TypeError, ValueError):
        return None


def _fetch_bingx_funding_rate(symbol: str, preferred_symbol: Optional[str] = None) -> Optional[float]:
    """
    直接從 BingX API 取得該幣種資金費率。若傳入 preferred_symbol（來自 contracts），優先使用。
    """
    clean = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    try_symbols = [preferred_symbol] if preferred_symbol else []
    if preferred_symbol and "USDC" in preferred_symbol.upper():
        try_symbols.append(preferred_symbol.upper().replace("-USDC", "-USDT"))
    try_symbols += [f"{clean}-USDT", f"1000{clean}-USDT"]
    try_symbols = list(dict.fromkeys(try_symbols))  # 去重且保留順序
    base_url = "https://open-api.bingx.com"
    for sym_param in try_symbols:
        try:
            r = requests.get(f"{base_url}/openApi/swap/v2/quote/premiumIndex", params={"symbol": sym_param}, timeout=5)
            time.sleep(0.1)
            if r.status_code != 200:
                continue
            j = r.json()
            if j.get("code") != 0:
                continue
            data = j.get("data")
            if isinstance(data, dict):
                rate = data.get("lastFundingRate") or data.get("fundingRate") or data.get("nextFundingRate")
                if rate is not None:
                    return float(rate)
            if isinstance(data, (int, float)):
                return float(data)
        except Exception:
            continue
    for sym_param in try_symbols:
        try:
            r = requests.get(f"{base_url}/openApi/swap/v2/quote/fundingRate", params={"symbol": sym_param, "limit": 2}, timeout=5)
            time.sleep(0.1)
            if r.status_code != 200:
                continue
            j = r.json()
            if j.get("code") != 0:
                continue
            data = j.get("data", [])
            if isinstance(data, list) and data:
                last = data[0] if data else {}
                if isinstance(last, dict):
                    rate = last.get("fundingRate")
                    if rate is not None:
                        return float(rate)
        except Exception:
            continue
    return None


def _fetch_bingx_current_price(symbol: str, preferred_symbol: Optional[str] = None) -> Optional[float]:
    """從 BingX swap ticker 取得即時最新價（相容舊呼叫，只回傳價格）。"""
    snap = _fetch_bingx_ticker_snapshot(symbol, preferred_symbol)
    return snap.get("price") if snap else None


def _fetch_bingx_ticker_snapshot(symbol: str, preferred_symbol: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    從 BingX swap v2 ticker 一次取得：最新價 + 24h 成交額(USDT)。
    回傳 {"price": float, "volume_usd": float or None}，失敗回傳 None。
    """
    clean = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    try_symbols = [preferred_symbol] if preferred_symbol else []
    try_symbols += [f"{clean}-USDT", f"1000{clean}-USDT"]
    try_symbols = list(dict.fromkeys([s for s in try_symbols if s]))
    base_url = "https://open-api.bingx.com"
    for sym_param in try_symbols:
        try:
            r = requests.get(
                f"{base_url}/openApi/swap/v2/quote/ticker",
                params={"symbol": sym_param},
                timeout=5
            )
            time.sleep(0.08)
            if r.status_code != 200:
                continue
            j = r.json()
            if j.get("code") != 0:
                continue
            data = j.get("data")
            if not isinstance(data, dict):
                continue
            price = data.get("lastPrice") or data.get("price") or data.get("last")
            if price is None:
                continue
            price_f = float(price)
            volume_usd = None
            qv = data.get("quoteVolume") or data.get("volume") or data.get("turnover")
            if qv is not None:
                try:
                    volume_usd = float(qv)
                except (TypeError, ValueError):
                    pass
            # 若成交額為 0 或缺失，用原始 symbol 再試一次（避免 1000PEPE 等誤判）
            raw_sym = symbol.strip()
            if (volume_usd is None or volume_usd == 0) and raw_sym and raw_sym not in try_symbols:
                try_sym = raw_sym if ("-" in raw_sym or "USDT" in raw_sym.upper()) else f"{raw_sym}-USDT"
                try:
                    time.sleep(0.08)
                    r2 = requests.get(
                        f"{base_url}/openApi/swap/v2/quote/ticker",
                        params={"symbol": try_sym},
                        timeout=5
                    )
                    if r2.status_code == 200:
                        j2 = r2.json()
                        if j2.get("code") == 0 and isinstance(j2.get("data"), dict):
                            qv2 = j2["data"].get("quoteVolume") or j2["data"].get("volume") or j2["data"].get("turnover")
                            if qv2 is not None:
                                try:
                                    volume_usd = float(qv2)
                                except (TypeError, ValueError):
                                    pass
                except Exception:
                    pass
            return {"price": price_f, "volume_usd": volume_usd}
        except Exception:
            continue
    return None


def _fetch_funding_rate_map() -> Dict[str, float]:
    """
    一次取得 CoinGlass 全量資金費率，回傳 symbol(base) -> 費率。
    方案A：OI加權費率歷史（最精準市場情緒，反映主力成本）
    方案B：exchange-list（各所費率，取 Binance 優先）
    """
    out: Dict[str, float] = {}
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}

    # ── 方案A：OI 加權費率（主力建倉成本最接近的指標）─────────────
    # 用最新一根 K 線的 OI 加權費率作為「真實市場費率」
    logger.debug(f"[資金費率-A] 嘗試 OI加權費率 endpoint={CG_EP['fr_oi_weight']}")
    try:
        _respect_coinglass_rate_limit()
        r_oi = requests.get(
            f"{CG_API_BASE}{CG_EP['fr_oi_weight']}",
            headers=headers,
            params={"symbol": "BTC", "interval": "8h", "limit": 1},  # 單一查詢測試結構
            timeout=10
        )
        if r_oi.status_code == 200:
            j_oi = r_oi.json()
            if j_oi.get("code") in (0, "0", 200, "200", None):
                logger.debug(f"[資金費率-A] OI加權費率 API 可用，但此端點為單幣種查詢，後續按需調用")
    except Exception:
        pass

    # ── 方案B：exchange-list（全量，可一次取得所有幣種）─────────────
    # 優先使用與 fetch_funding_fortune_list（費率排行榜）完全相同的 kebab-case 端點（已驗證有效）
    # camelCase 版本曾回傳 404，作為次要備援
    fr_ep_candidates = [CG_EP["fr_exchange_list_old"], CG_EP["fr_exchange_list"]]
    lst = []
    url_used = ""
    for fr_ep_path in fr_ep_candidates:
        url = f"{CG_API_BASE}{fr_ep_path}"
        logger.debug(f"[資金費率-B] 嘗試全量費率列表 endpoint={fr_ep_path}")
        succeeded = False
        for attempt in range(2):
            try:
                _respect_coinglass_rate_limit()
                r = requests.get(url, headers=headers, timeout=12)
                if r.status_code == 429:
                    logger.warning("資金費率 API 429 Too Many Requests，2 秒後重試一次")
                    time.sleep(2)
                    continue
                if r.status_code == 404:
                    logger.info(f"[資金費率-B] {fr_ep_path} 404，切換備援路徑")
                    break  # 嘗試下一個 endpoint
                if r.status_code != 200:
                    logger.warning(f"資金費率-B status={r.status_code} body={r.text[:200]}")
                    break
                data = r.json()
                if data.get("code") not in (0, "0", 200, "200", None):
                    logger.warning(f"資金費率-B code={data.get('code')} msg={data.get('msg')}")
                    break
                candidate = data.get("data", [])
                if isinstance(candidate, list) and candidate:
                    lst = candidate
                    url_used = fr_ep_path
                    succeeded = True
                    logger.info(f"[資金費率-B✅] 使用 {fr_ep_path} 取得 {len(lst)} 筆費率資料")
                    break
            except Exception as e:
                if attempt == 0:
                    logger.warning(f"資金費率-B 請求異常: {e}，重試一次")
                    time.sleep(1)
                else:
                    logger.warning(f"資金費率-B 全部失敗: {e}")
        if succeeded:
            break
    if not lst:
        logger.warning(f"[資金費率-B❌] 新舊路徑均無法取得費率資料，返回空表")

    # ── 解析邏輯：與 fetch_funding_fortune_list 完全對齊（幣安 stablecoin_margin_list 優先）──
    try:
        for coin_data in (lst if isinstance(lst, list) else []):
            if not isinstance(coin_data, dict):
                continue
            base = coin_data.get("symbol") or coin_data.get("coin") or coin_data.get("base")
            if not base:
                continue
            base = str(base).strip().upper()

            rate_found: Optional[float] = None

            # 優先：USDT 永續（stablecoin_margin_list → Binance）
            stablecoin_list = coin_data.get("stablecoin_margin_list") or []
            for item in (stablecoin_list if isinstance(stablecoin_list, list) else []):
                if not isinstance(item, dict):
                    continue
                if item.get("exchange") != "Binance":
                    continue
                raw_rate = item.get("funding_rate")
                if raw_rate is None:
                    continue
                try:
                    # CoinGlass exchange-list 的 funding_rate 是百分比格式
                    # (如 -0.1316 = -0.1316%)，需除以 100 轉為小數供後續計算
                    rate_found = float(raw_rate) / 100.0
                    break
                except (TypeError, ValueError):
                    continue

            # 備援：幣本位永續（token_margin_list → Binance）
            if rate_found is None:
                token_list = coin_data.get("token_margin_list") or []
                for item in (token_list if isinstance(token_list, list) else []):
                    if not isinstance(item, dict):
                        continue
                    if item.get("exchange") != "Binance":
                        continue
                    raw_rate = item.get("funding_rate")
                    if raw_rate is None:
                        continue
                    try:
                        rate_found = float(raw_rate) / 100.0
                        break
                    except (TypeError, ValueError):
                        continue

            if rate_found is not None:
                out[base] = rate_found

        if out:
            logger.info(f"[資金費率✅] 成功解析 {len(out)} 幣種幣安費率（stablecoin_margin_list 優先）")
        elif lst:
            _sample = list(lst[0].keys()) if lst and isinstance(lst[0], dict) else "n/a"
            logger.warning(f"[資金費率⚠️] 解析 0 筆，首筆結構 keys={_sample}")
    except Exception as e:
        logger.warning(f"資金費率解析異常: {e}")
    return out


def fetch_oi_weighted_funding_rate(symbol: str, interval: str = "8h", limit: int = 3) -> Optional[float]:
    """取得單一幣種的 OI 加權資金費率（最能反映主力槓桿成本的指標）。
    方案A：fr_oi_weight（OI加權）
    方案B：fr_vol_weight（成交量加權）
    方案C：fr_history（原始費率，Binance 優先）
    快取 15 分鐘（費率 8 小時結算一次，短期變化不大）。
    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    cache_key = f"fr_oi_w:{base}:{interval}"
    now = time.time()
    if cache_key in _flow_cache:
        val, ts = _flow_cache[cache_key]
        if now - ts < 900:  # 15 分鐘快取
            return val

    logger.debug(f"[OI加權費率] {base} endpoint={CG_EP['fr_oi_weight']} interval={interval}")
    for ep_key in ["fr_oi_weight", "fr_vol_weight", "fr_history"]:
        try:
            j = _cg_get(CG_EP[ep_key], {"symbol": base, "interval": interval, "limit": limit,
                                          "exchange": "Binance"})
            if not j:
                continue
            rows = j.get("data") or j.get("list") or []
            if not isinstance(rows, list) or not rows:
                logger.debug(f"[OI加權費率-{ep_key}] {base}: 空數據")
                continue
            last = rows[-1]
            if isinstance(last, dict):
                rate_val = (last.get("fundingRate") or last.get("funding_rate") or
                            last.get("rate") or last.get("c") or last.get("close"))
            elif isinstance(last, (list, tuple)) and len(last) >= 2:
                rate_val = last[-1]
            else:
                continue
            if rate_val is None:
                continue
            rate_f = float(rate_val)
            logger.info(f"[OI加權費率✅] {base} {ep_key}: {rate_f*100:.4f}%")
            _flow_cache[cache_key] = (rate_f, now)
            return rate_f
        except Exception as e:
            logger.debug(f"[OI加權費率-{ep_key}] {base} 異常: {e}")
            continue

    _flow_cache[cache_key] = (None, now)
    return None


def fetch_accumulated_funding_score(symbol: str) -> Dict[str, Any]:
    """累積資金費率極端值偵測。
    用途：判斷市場是否已「費率過熱」或「費率極度負值（嘎空潛力）」。
    方案A：accumulated-exchange-list（累積費率，7 日/30 日）
    回傳：{
      "accumulated_7d": float,      # 7日累積費率
      "accumulated_30d": float,     # 30日累積費率
      "squeeze_risk": str,          # "long_squeeze" / "short_squeeze" / "neutral"
      "squeeze_label": str,         # 推播文案
    }
    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    cache_key = f"accum_fr:{base}"
    now = time.time()
    if cache_key in _flow_cache:
        val, ts = _flow_cache[cache_key]
        if now - ts < 1800:  # 30 分鐘快取（累積費率緩慢變動）
            return val if val else {"accumulated_7d": None, "accumulated_30d": None,
                                    "squeeze_risk": "unknown", "squeeze_label": ""}

    empty = {"accumulated_7d": None, "accumulated_30d": None, "squeeze_risk": "unknown", "squeeze_label": ""}
    logger.debug(f"[累積費率] {base} endpoint={CG_EP['fr_accum_exchange']}")
    try:
        j = _cg_get(CG_EP["fr_accum_exchange"], {"symbol": base})
        if not j:
            _flow_cache[cache_key] = (empty, now)
            return empty
        data = j.get("data") or j.get("list") or []

        accum_7d = accum_30d = None
        # 嘗試從各所中取 Binance 或聚合值
        if isinstance(data, list):
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                ex = entry.get("exchange") or entry.get("exchangeName") or ""
                if ex not in ("Binance", "", "All", "Aggregated"):
                    continue
                try:
                    a7 = entry.get("accumulated7d") or entry.get("accum7d") or entry.get("fundingRate7d")
                    a30 = entry.get("accumulated30d") or entry.get("accum30d") or entry.get("fundingRate30d")
                    if a7 is not None:
                        accum_7d = float(a7)
                    if a30 is not None:
                        accum_30d = float(a30)
                    if accum_7d is not None:
                        break
                except (TypeError, ValueError):
                    continue
        elif isinstance(data, dict):
            try:
                accum_7d = float(data.get("accumulated7d") or data.get("accum7d") or 0)
                accum_30d = float(data.get("accumulated30d") or data.get("accum30d") or 0)
            except (TypeError, ValueError):
                pass

        # 判斷擠壓風險
        # 累積費率 > 2% (7d) 代表多頭持續付費 = 軋空燃料（多頭過熱，空頭嘎空機率大）
        # 累積費率 < -1% (7d) 代表空頭持續付費 = 嘎空燃料（空頭過熱，多頭嘎空機率大）
        squeeze_risk = "neutral"
        squeeze_label = ""
        val_for_check = accum_7d
        if val_for_check is not None:
            if val_for_check > 0.02:   # > 2% 7d 累積 = 多頭極度過熱
                squeeze_risk = "long_squeeze"
                squeeze_label = f"⚠️ 7日累積費率 `{val_for_check*100:.2f}%`（多頭費用過高，嘎空風險↑）"
                logger.info(f"[累積費率⚠️] {base}: 多頭極度過熱 7d累積={val_for_check*100:.2f}%")
            elif val_for_check < -0.01:  # < -1% 7d 累積 = 空頭極度過熱
                squeeze_risk = "short_squeeze"
                squeeze_label = f"🔥 7日累積費率 `{val_for_check*100:.2f}%`（空頭費用過高，嘎空潛力巨大）"
                logger.info(f"[累積費率🔥] {base}: 空頭極度過熱 7d累積={val_for_check*100:.2f}%")
            else:
                squeeze_label = f"💱 7日累積費率 `{val_for_check*100:.3f}%`（正常）"
        else:
            logger.debug(f"[累積費率] {base}: 無法解析累積費率數據 data={str(data)[:100]}")

        result = {"accumulated_7d": accum_7d, "accumulated_30d": accum_30d,
                  "squeeze_risk": squeeze_risk, "squeeze_label": squeeze_label}
        _flow_cache[cache_key] = (result, now)
        return result
    except Exception as e:
        logger.debug(f"[累積費率] {base} 異常: {e}")
        _flow_cache[cache_key] = (empty, now)
        return empty


# ══════════════════════════════════════════════════════════════════════════════
# 市場宏觀指標（Fear&Greed、BTC ETF、Coinbase溢價、合約基差、期權最大痛點）
# ══════════════════════════════════════════════════════════════════════════════

def fetch_fear_greed_index() -> Dict[str, Any]:
    """恐懼貪婪指數（整合市場情緒的最佳單一指標）。
    endpoint: /api/index/fear-greed-history
    快取 30 分鐘（每日更新一次，短期快取防重複請求）。
    回傳: {"value": int, "label": str, "emoji": str, "signal": str}
    """
    cache_key = "fear_greed_index"
    now = time.time()
    if cache_key in _flow_cache:
        val, ts = _flow_cache[cache_key]
        if now - ts < 1800:
            return val if val else {}

    logger.debug(f"[恐懼貪婪] endpoint={CG_EP['fear_greed']}")
    empty = {"value": None, "label": "N/A", "emoji": "❓", "signal": "neutral"}
    try:
        j = _cg_get(CG_EP["fear_greed"], {"limit": 1})
        if not j:
            _flow_cache[cache_key] = (empty, now)
            return empty
        data = j.get("data") or j.get("list") or j
        if isinstance(data, list) and data:
            data = data[-1]  # 最新一筆
        if not isinstance(data, dict):
            _flow_cache[cache_key] = (empty, now)
            return empty

        val_raw = data.get("value") or data.get("score") or data.get("index")
        label_raw = data.get("value_classification") or data.get("label") or data.get("classification") or ""
        if val_raw is None:
            _flow_cache[cache_key] = (empty, now)
            return empty

        fg_val = int(float(val_raw))
        # 標準化標籤
        if fg_val >= 80:
            emoji, label, signal = "🔥", "極度貪婪", "overbought"
        elif fg_val >= 60:
            emoji, label, signal = "🟢", "貪婪", "bullish"
        elif fg_val >= 40:
            emoji, label, signal = "🟡", "中性", "neutral"
        elif fg_val >= 20:
            emoji, label, signal = "🔴", "恐懼", "bearish"
        else:
            emoji, label, signal = "💀", "極度恐懼", "oversold"

        result = {"value": fg_val, "label": label_raw or label, "emoji": emoji,
                  "signal": signal, "score": fg_val}
        logger.info(f"[恐懼貪婪✅] 當前指數={fg_val} {emoji} {label}")
        _flow_cache[cache_key] = (result, now)
        return result
    except Exception as e:
        logger.debug(f"[恐懼貪婪] 異常: {e}")
        _flow_cache[cache_key] = (empty, now)
        return empty


def fetch_btc_etf_flow(limit: int = 3) -> Dict[str, Any]:
    """比特幣ETF資金流向（機構資金進出場最直接信號）。
    endpoint: /api/etf/bitcoin/flow-history
    方案B: /api/etf/bitcoin/net-assets/history
    快取 1 小時。
    回傳: {"net_flow_usd": float, "direction": "inflow"/"outflow"/"neutral",
           "label": str, "total_assets_usd": float}
    """
    cache_key = "btc_etf_flow"
    now = time.time()
    if cache_key in _flow_cache:
        val, ts = _flow_cache[cache_key]
        if now - ts < 3600:
            return val if val else {}

    logger.debug(f"[BTC ETF] endpoint={CG_EP['btc_etf_flow']}")
    empty = {"net_flow_usd": None, "direction": "unknown", "label": "", "total_assets_usd": None}
    try:
        j = _cg_get(CG_EP["btc_etf_flow"], {"limit": limit})
        if not j:
            j = _cg_get(CG_EP["btc_etf_net_assets"], {"limit": limit})
        if not j:
            _flow_cache[cache_key] = (empty, now)
            return empty

        data = j.get("data") or j.get("list") or []
        if isinstance(data, list) and data:
            latest = data[-1]
        elif isinstance(data, dict):
            latest = data
        else:
            _flow_cache[cache_key] = (empty, now)
            return empty

        net_flow = (latest.get("netFlow") or latest.get("net_flow") or
                    latest.get("netInflow") or latest.get("netOutflow") or
                    latest.get("flow") or 0)
        total_assets = (latest.get("totalAssets") or latest.get("total_assets") or
                        latest.get("netAssets") or 0)
        try:
            net_f = float(net_flow)
            total_a = float(total_assets)
        except (TypeError, ValueError):
            _flow_cache[cache_key] = (empty, now)
            return empty

        if net_f > 50_000_000:       # > 5千萬 USD 淨流入
            direction = "inflow"
            label = f"🟢 BTC ETF淨流入 `${net_f/1e6:.0f}M`（機構積極買入）"
        elif net_f < -50_000_000:    # 5千萬以上淨流出
            direction = "outflow"
            label = f"🔴 BTC ETF淨流出 `${abs(net_f)/1e6:.0f}M`（機構減倉警告）"
        else:
            direction = "neutral"
            label = f"🟡 BTC ETF資金流 `${net_f/1e6:+.0f}M`（中性）" if net_f != 0 else ""

        result = {"net_flow_usd": net_f, "direction": direction, "label": label,
                  "total_assets_usd": total_a if total_a > 0 else None}
        logger.info(f"[BTC ETF✅] 淨流 ${net_f/1e6:+.0f}M 總資產 ${total_a/1e9:.1f}B")
        _flow_cache[cache_key] = (result, now)
        return result
    except Exception as e:
        logger.debug(f"[BTC ETF] 異常: {e}")
        _flow_cache[cache_key] = (empty, now)
        return empty


def fetch_coinbase_premium() -> Dict[str, Any]:
    """Coinbase 溢價指數（美國機構買盤強度最直接指標）。
    Coinbase 溢價 > 0 = 美國資金正在買入（機構牛訊號）
    Coinbase 溢價 < 0 = 美國資金正在賣出（機構熊訊號）
    endpoint: /api/coinbase-premium-index
    快取 15 分鐘。
    """
    cache_key = "coinbase_premium"
    now = time.time()
    if cache_key in _flow_cache:
        val, ts = _flow_cache[cache_key]
        if now - ts < 900:
            return val if val else {}

    logger.debug(f"[Coinbase溢價] endpoint={CG_EP['coinbase_premium']}")
    empty = {"premium": None, "label": "", "signal": "neutral"}
    try:
        j = _cg_get(CG_EP["coinbase_premium"], {"limit": 1})
        if not j:
            _flow_cache[cache_key] = (empty, now)
            return empty
        data = j.get("data") or j.get("list") or j
        if isinstance(data, list) and data:
            data = data[-1]
        if not isinstance(data, dict):
            _flow_cache[cache_key] = (empty, now)
            return empty

        prem = data.get("premium") or data.get("premiumIndex") or data.get("value")
        if prem is None:
            _flow_cache[cache_key] = (empty, now)
            return empty
        prem_f = float(prem)

        if prem_f > 0.1:
            signal, label = "bullish", f"🟢 Coinbase溢價 `+{prem_f:.3f}%`（美國機構主動買入）"
        elif prem_f < -0.1:
            signal, label = "bearish", f"🔴 Coinbase折價 `{prem_f:.3f}%`（美國機構主動賣出）"
        else:
            signal, label = "neutral", f"🟡 Coinbase溢價 `{prem_f:+.3f}%`（中性）"

        result = {"premium": prem_f, "label": label, "signal": signal}
        logger.info(f"[Coinbase溢價✅] {prem_f:+.3f}% {signal}")
        _flow_cache[cache_key] = (result, now)
        return result
    except Exception as e:
        logger.debug(f"[Coinbase溢價] 異常: {e}")
        _flow_cache[cache_key] = (empty, now)
        return empty


def fetch_options_max_pain(symbol: str = "BTC") -> Dict[str, Any]:
    """期權最大痛點價格（市場價格的引力中心，到期日前常回歸此價）。
    方案A: /api/option/max-pain
    方案B: /api/option/info（從期權信息中提取）
    快取 1 小時（每日更新一次）。
    回傳: {"max_pain_price": float, "expiry": str, "label": str, "distance_pct": float}
    """
    base = symbol.replace("USDT", "").upper()
    cache_key = f"opt_max_pain:{base}"
    now = time.time()
    if cache_key in _flow_cache:
        val, ts = _flow_cache[cache_key]
        if now - ts < 3600:
            return val if val else {}

    logger.debug(f"[最大痛點] {base} endpoint={CG_EP['opt_max_pain']}")
    empty = {"max_pain_price": None, "expiry": None, "label": "", "distance_pct": None}
    try:
        for ep_key in ["opt_max_pain", "opt_info"]:
            j = _cg_get(CG_EP[ep_key], {"symbol": base})
            if not j:
                continue
            data = j.get("data") or j.get("list") or j
            if isinstance(data, list) and data:
                data = data[0]
            if not isinstance(data, dict):
                continue
            mp = data.get("maxPain") or data.get("max_pain") or data.get("maxPainPrice")
            expiry = data.get("expiry") or data.get("expiryDate") or data.get("deliveryDate") or ""
            if mp is None:
                continue
            mp_f = float(mp)
            result = {"max_pain_price": mp_f, "expiry": str(expiry), "label": "", "distance_pct": None}
            result["label"] = f"🎯 期權最大痛點：`{mp_f:,.0f}` USD（到期日 {expiry}）"
            logger.info(f"[最大痛點✅] {base}: {mp_f:,.0f} 到期={expiry}")
            _flow_cache[cache_key] = (result, now)
            return result
    except Exception as e:
        logger.debug(f"[最大痛點] {base} 異常: {e}")
    _flow_cache[cache_key] = (empty, now)
    return empty


def fetch_contract_basis(symbol: str = "BTC", interval: str = "1h", limit: int = 4) -> Dict[str, Any]:
    """合約基差（期貨價格 - 現貨價格）。
    正基差（期貨溢價）= 市場看多預期
    負基差（期貨折價）= 市場看空預期或現貨更強
    endpoint: /api/futures/basis/history
    快取 15 分鐘。
    回傳: {"basis_pct": float, "trend": "widening"/"narrowing"/"stable",
           "label": str, "signal": str}
    """
    base = symbol.replace("USDT", "").upper()
    cache_key = f"basis:{base}:{interval}"
    now = time.time()
    if cache_key in _flow_cache:
        val, ts = _flow_cache[cache_key]
        if now - ts < 900:
            return val if val else {}

    logger.debug(f"[合約基差] {base} endpoint={CG_EP['contract_basis']}")
    empty = {"basis_pct": None, "trend": "unknown", "label": "", "signal": "neutral"}
    try:
        j = _cg_get(CG_EP["contract_basis"], {"symbol": base, "exchange": "Binance",
                                                "interval": interval, "limit": limit})
        if not j:
            _flow_cache[cache_key] = (empty, now)
            return empty
        rows = j.get("data") or j.get("list") or []
        if not isinstance(rows, list) or len(rows) < 2:
            _flow_cache[cache_key] = (empty, now)
            return empty

        # 解析最近幾根 K 線的基差值
        basis_vals = []
        for bar in rows:
            if not isinstance(bar, dict):
                continue
            b = bar.get("basis") or bar.get("basisRate") or bar.get("c") or bar.get("close")
            if b is not None:
                try:
                    basis_vals.append(float(b))
                except (TypeError, ValueError):
                    pass

        if len(basis_vals) < 2:
            _flow_cache[cache_key] = (empty, now)
            return empty

        latest_basis = basis_vals[-1]
        prev_basis = basis_vals[-2]
        # 判斷趨勢
        if abs(latest_basis) > abs(prev_basis) * 1.1:
            trend = "widening"
        elif abs(latest_basis) < abs(prev_basis) * 0.9:
            trend = "narrowing"
        else:
            trend = "stable"

        # 生成信號
        if latest_basis > 0.005:     # > 0.5%
            signal = "bullish"
            label = f"📈 期貨溢價 `+{latest_basis*100:.3f}%`（市場看多，期貨>現貨）"
        elif latest_basis < -0.003:  # < -0.3%
            signal = "bearish"
            label = f"📉 期貨折價 `-{abs(latest_basis)*100:.3f}%`（現貨更強，資金回流現貨）"
        else:
            signal = "neutral"
            label = f"〰️ 基差中性 `{latest_basis*100:+.3f}%`"

        if trend == "widening" and signal == "bullish":
            label += "（基差擴大，看多情緒升溫）"
        elif trend == "narrowing" and signal == "bullish":
            label += "（基差收窄，多頭熱情降溫）"

        result = {"basis_pct": latest_basis * 100, "trend": trend, "label": label, "signal": signal}
        logger.info(f"[合約基差✅] {base}: {latest_basis*100:+.4f}% trend={trend}")
        _flow_cache[cache_key] = (result, now)
        return result
    except Exception as e:
        logger.debug(f"[合約基差] {base} 異常: {e}")
        _flow_cache[cache_key] = (empty, now)
        return empty


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI(period)，純 pandas 實作，與常見交易所一致。"""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _bbands(close: pd.Series, length: int = 20, std_dev: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """布林帶：middle=SMA(close), upper=middle+std*std(close), lower=middle-std*std(close)。回傳 (upper, middle, lower)。"""
    middle = close.rolling(window=length).mean()
    std = close.rolling(window=length).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


def _fetch_bingx_klines_and_calc(symbol: str, preferred_symbol: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    以 BingX 15m K 線為主：用本地 pandas 計算 RSI(14)、布林帶(20,2)、ATR、EMA20、VWAP_2h 等，
    並回傳「最後一根 15m K 線」的 open/high/low/close 供 SL/TP 結構防守使用。
    包含重試機制，解決 Rate Limit (429) 問題。
    """
    clean = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    try_symbols = []
    if preferred_symbol:
        try_symbols.append(preferred_symbol)
        if "USDC" in preferred_symbol.upper():
            try_symbols.append(preferred_symbol.upper().replace("-USDC", "-USDT"))
    try_symbols += [f"{clean}-USDT", f"1000{clean}-USDT", f"{clean}USDT"]
    try_symbols = list(dict.fromkeys(try_symbols))
    url = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"
    raw = []
    found_symbol = None
    for sym_param in try_symbols:
        for attempt in range(2):
            params = {"symbol": sym_param, "interval": "15m", "limit": 60}
            try:
                r = requests.get(url, params=params, timeout=8)
                if r.status_code == 429:
                    logger.warning(f"BingX API 429 限流 ({sym_param})，休息 1 秒後重試...")
                    time.sleep(1.5)
                    continue
                if r.status_code == 200:
                    j = r.json()
                    if j.get("code") == 0:
                        data = j.get("data", [])
                        if isinstance(data, list) and len(data) >= 30:
                            raw = data
                            found_symbol = sym_param
                            break
            except Exception as e:
                logger.debug(f"K線請求異常 {sym_param}: {e}")
                time.sleep(0.5)
        if raw:
            break
    if not raw:
        logger.warning(f"[本地換算] {clean}: BingX K線取得失敗，嘗試交易對 {try_symbols} 皆無數據")
        return None
    logger.info(f"[本地換算] {clean}: BingX 15m K線取得 {len(raw)} 根，使用交易對 {found_symbol}，開始本地計算 RSI(14)/布林(20,2)/EMA20/VWAP_2h/ATR(14) 與最後一根 K 線結構")
    opens, highs, lows, closes, volumes = [], [], [], [], []
    for row in raw:
        o = h = l = c = vol = None
        if isinstance(row, dict):
            o = row.get("open") or row.get("o")
            h = row.get("high") or row.get("h")
            l = row.get("low") or row.get("l")
            c = row.get("close") or row.get("c")
            vol = row.get("volume") or row.get("v")
        elif isinstance(row, (list, tuple)) and len(row) >= 5:
            # 常見格式：[ts, open, high, low, close, volume]
            o = row[1] if len(row) > 1 else None
            h = row[2] if len(row) > 2 else None
            l = row[3] if len(row) > 3 else None
            c = row[4] if len(row) > 4 else row[3]
            vol = row[5] if len(row) > 5 else None
        if o is not None and h is not None and l is not None and c is not None:
            try:
                opens.append(float(o))
                highs.append(float(h))
                lows.append(float(l))
                closes.append(float(c))
                volumes.append(float(vol) if vol is not None else 0.0)
            except (TypeError, ValueError):
                pass
    if len(closes) < 20:
        logger.warning(f"[本地換算] {clean}: K線有效根數 {len(closes)} < 20，無法計算")
        return None
    # 15分K 最常用「關鍵均線」：EMA20（指數移動平均），對近期價格權重高、反應快，實戰多當動態支撐/阻力
    # 做空時停損至少設在 EMA20 上方、做多時在 EMA20 下方，避免被回測均線洗掉
    ema20_close = None
    if len(closes) >= 20:
        period = 20
        alpha = 2.0 / (period + 1)
        ema = float(np.mean(closes[:period]))
        for i in range(period, len(closes)):
            ema = alpha * float(closes[i]) + (1.0 - alpha) * ema
        ema20_close = ema
    # VWAP：小幣深度不足時 24h VWAP 易失真。Plan B 用「最近 2 小時（8 根 15m K 線）」成交量加權，更貼近短線狙擊成本位
    vwap_2h = None
    if len(closes) >= 8 and len(volumes) >= 8:
        use_c = closes[-8:]
        use_h = highs[-8:]
        use_l = lows[-8:]
        use_v = volumes[-8:]
        if sum(use_v) > 0:
            typical = [(use_h[i] + use_l[i] + use_c[i]) / 3.0 for i in range(len(use_c))]
            vwap_2h = sum(typical[i] * use_v[i] for i in range(len(typical))) / sum(use_v)
            logger.info(f"[本地換算] {clean}: VWAP_2h 使用最近 8 根 15m K 線成交量加權 (典型價 H+L+C/3)，避免小幣 24h VWAP 失真")
    series = pd.Series(closes)
    rsi_series = _rsi(series, period=14)
    if rsi_series.empty or pd.isna(rsi_series.iloc[-1]):
        logger.warning(f"[本地換算] {clean}: RSI(14) 計算無效")
        return None
    rsi_val = float(rsi_series.iloc[-1])
    upper_bb, _, lower_bb = _bbands(series, length=20, std_dev=2.0)
    ub_value = float(upper_bb.iloc[-1]) if not pd.isna(upper_bb.iloc[-1]) else None
    lb_value = float(lower_bb.iloc[-1]) if not pd.isna(lower_bb.iloc[-1]) else None
    current_price = float(closes[-1]) if closes else None
    touch_upper = current_price is not None and ub_value is not None and current_price >= ub_value
    touch_lower = current_price is not None and lb_value is not None and current_price <= lb_value
    # ATR(14): TR = max(high-low, abs(high-prev_close), abs(low-prev_close)), ATR = TR.rolling(14).mean()
    atr_val = None
    if len(highs) >= 15 and len(lows) >= 15:
        df = pd.DataFrame({"high": highs, "low": lows, "close": closes})
        prev_close = df["close"].shift(1)
        tr = np.maximum(
            df["high"] - df["low"],
            np.maximum(
                (df["high"] - prev_close).abs(),
                (df["low"] - prev_close).abs(),
            ),
        )
        atr_series = tr.rolling(14).mean()
        if not atr_series.empty and not pd.isna(atr_series.iloc[-1]) and atr_series.iloc[-1] > 0:
            atr_val = float(atr_series.iloc[-1])

    # MACD(12, 26, 9)：用於能量背離偵測
    macd_hist = None
    energy_exhausted = False
    if len(closes) >= 35:
        ser = pd.Series(closes, dtype=float)
        ema12 = ser.ewm(span=12, adjust=False).mean()
        ema26 = ser.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - signal_line
        # 能量背離：價格創新高但 MACD 柱狀縮短
        lookback = 5
        if len(closes) >= lookback and len(macd_hist) >= 3:
            recent_closes = closes[-lookback:]
            recent_hist = macd_hist.iloc[-3:].tolist()
            price_new_high = (recent_closes[-1] >= max(recent_closes))
            hist_shortening = len(recent_hist) >= 2 and recent_hist[-1] < recent_hist[-2]
            if price_new_high and hist_shortening:
                energy_exhausted = True
                logger.info(f"[本地換算] {clean}: 能量背離偵測 價格創高但 MACD 柱狀縮短 → energy_exhausted=True")

    # ── ⚡ 爆量偵測：最新一根 15m 成交量 vs 前 96 根（24h）均值 ────────────────
    # vol_spike_ratio > 2.0 = 超過均值 2 倍 → 爆量啟動訊號
    vol_spike_ratio: Optional[float] = None
    if len(volumes) >= 10:
        _compare_window = volumes[:-1]  # 排除可能仍在形成中的最後一根
        _sample = _compare_window[-min(96, len(_compare_window)):]
        _avg_vol = float(np.mean(_sample)) if _sample else 0.0
        if _avg_vol > 0 and volumes[-1] > 0:
            vol_spike_ratio = volumes[-1] / _avg_vol
            if vol_spike_ratio >= 1.5:
                logger.info(
                    f"[本地換算] {clean}: ⚡ 爆量偵測 最新根={volumes[-1]:.2f} "
                    f"均值={_avg_vol:.2f} 比率={vol_spike_ratio:.2f}×"
                )

    out: Dict[str, Any] = {
        "rsi": rsi_val,
        "touch_upper": touch_upper,
        "touch_lower": touch_lower,
        "current_price": current_price,
        "ub_value": ub_value,
        "lb_value": lb_value,
        "atr": atr_val,
        "source": "BingX",
        "plan_b_used": False,
        "real_symbol": found_symbol,
        "energy_exhausted": energy_exhausted,
        "vol_spike_ratio": vol_spike_ratio,  # ⚡ 爆量倍率（None=無法計算）
    }
    # 最後一根 15m K 線的 open/high/low/close（觸發訊號當下 K 線結構）
    if opens and highs and lows and closes:
        out["last_kline_open_30m"] = float(opens[-1])
        out["last_kline_high_30m"] = float(highs[-1])
        out["last_kline_low_30m"] = float(lows[-1])
        out["last_kline_close_30m"] = float(closes[-1])
    if vwap_2h is not None:
        out["vwap_2h"] = vwap_2h
    if ema20_close is not None:
        out["ema20_close"] = ema20_close
    # 近期結構（2h 內高低）：15m × 8 根 = 2h，供 SL/TP「OI 起漲點防守」使用
    if len(highs) >= 8 and len(lows) >= 8:
        out["recent_high_2h"] = max(highs[-8:])
        out["recent_low_2h"] = min(lows[-8:])
    logger.info(
        f"[本地換算] {clean}: 完成 RSI={rsi_val:.2f} 布林上={ub_value} 布林下={lb_value} "
        f"現價={current_price} ATR={atr_val} VWAP_2h={vwap_2h} EMA20={ema20_close} "
        f"最近2h高低=({out.get('recent_high_2h')}, {out.get('recent_low_2h')}) "
        f"最後K線OHLC=({out.get('last_kline_open_30m')}, {out.get('last_kline_high_30m')}, "
        f"{out.get('last_kline_low_30m')}, {out.get('last_kline_close_30m')})"
    )
    return out


def _calc_indicators_from_ohlcv(
    opens: list, highs: list, lows: list, closes: list, volumes: list,
    clean: str, source_label: str, real_symbol: str,
) -> Optional[Dict[str, Any]]:
    """共用指標計算核心：輸入 OHLCV list，輸出與 _fetch_bingx_klines_and_calc 相同格式的 dict。
    被 _fetch_cg_klines_and_calc 與 _fetch_bingx_klines_and_calc 共用，避免重複邏輯。
    """
    if len(closes) < 20:
        logger.warning(f"[指標計算] {clean}: 有效 K 線根數 {len(closes)} < 20，無法計算")
        return None

    # EMA20
    ema20_close = None
    period = 20
    alpha = 2.0 / (period + 1)
    ema = float(np.mean(closes[:period]))
    for i in range(period, len(closes)):
        ema = alpha * float(closes[i]) + (1.0 - alpha) * ema
    ema20_close = ema

    # VWAP_2h（最近 8 根 15m K 線）
    vwap_2h = None
    if len(closes) >= 8 and len(volumes) >= 8:
        uc, uh, ul, uv = closes[-8:], highs[-8:], lows[-8:], volumes[-8:]
        typical = [(uh[i] + ul[i] + uc[i]) / 3.0 for i in range(len(uc))]
        total_vol = sum(uv)
        if total_vol > 0:
            vwap_2h = sum(typical[i] * uv[i] for i in range(len(typical))) / total_vol
            logger.info(f"[指標計算] {clean}: VWAP_2h 使用最近 8 根 K 線成交量加權 (典型價 H+L+C/3)")
        else:
            # CoinGlass price/history 不含 volume → 退為等權典型價均值（TWAP 近似）
            vwap_2h = sum(typical) / len(typical)
            logger.info(f"[指標計算] {clean}: VWAP_2h 無 volume，改用等權典型價均值 (TWAP 近似)")

    series = pd.Series(closes)
    rsi_series = _rsi(series, period=14)
    if rsi_series.empty or pd.isna(rsi_series.iloc[-1]):
        logger.warning(f"[指標計算] {clean}: RSI(14) 計算無效")
        return None
    rsi_val = float(rsi_series.iloc[-1])

    upper_bb, _, lower_bb = _bbands(series, length=20, std_dev=2.0)
    ub_value = float(upper_bb.iloc[-1]) if not pd.isna(upper_bb.iloc[-1]) else None
    lb_value = float(lower_bb.iloc[-1]) if not pd.isna(lower_bb.iloc[-1]) else None

    current_price = float(closes[-1])
    touch_upper = ub_value is not None and current_price >= ub_value
    touch_lower = lb_value is not None and current_price <= lb_value

    # ATR(14)
    atr_val = None
    if len(highs) >= 15:
        df = pd.DataFrame({"high": highs, "low": lows, "close": closes})
        prev_close = df["close"].shift(1)
        tr = np.maximum(df["high"] - df["low"],
                        np.maximum((df["high"] - prev_close).abs(),
                                   (df["low"] - prev_close).abs()))
        atr_s = tr.rolling(14).mean()
        if not atr_s.empty and not pd.isna(atr_s.iloc[-1]) and atr_s.iloc[-1] > 0:
            atr_val = float(atr_s.iloc[-1])

    # MACD(12,26,9) 能量背離
    energy_exhausted = False
    if len(closes) >= 35:
        ser = pd.Series(closes, dtype=float)
        ema12 = ser.ewm(span=12, adjust=False).mean()
        ema26 = ser.ewm(span=26, adjust=False).mean()
        macd_hist = ema12 - ema26 - (ema12 - ema26).ewm(span=9, adjust=False).mean()
        lookback = 5
        if len(closes) >= lookback and len(macd_hist) >= 3:
            recent_hist = macd_hist.iloc[-3:].tolist()
            price_new_high = closes[-1] >= max(closes[-lookback:])
            hist_shortening = len(recent_hist) >= 2 and recent_hist[-1] < recent_hist[-2]
            if price_new_high and hist_shortening:
                energy_exhausted = True

    # 爆量偵測
    vol_spike_ratio: Optional[float] = None
    if len(volumes) >= 10:
        sample = volumes[:-1][-min(96, len(volumes) - 1):]
        avg_vol = float(np.mean(sample)) if sample else 0.0
        if avg_vol > 0 and volumes[-1] > 0:
            vol_spike_ratio = volumes[-1] / avg_vol
            if vol_spike_ratio >= 1.5:
                logger.info(f"[指標計算] {clean}: ⚡ 爆量 最新={volumes[-1]:.2f} 均={avg_vol:.2f} {vol_spike_ratio:.2f}×")

    out: Dict[str, Any] = {
        "rsi": rsi_val, "touch_upper": touch_upper, "touch_lower": touch_lower,
        "current_price": current_price, "ub_value": ub_value, "lb_value": lb_value,
        "atr": atr_val, "source": source_label, "plan_b_used": False,
        "real_symbol": real_symbol, "energy_exhausted": energy_exhausted,
        "vol_spike_ratio": vol_spike_ratio,
    }
    if opens and highs and lows and closes:
        out["last_kline_open_30m"] = float(opens[-1])
        out["last_kline_high_30m"] = float(highs[-1])
        out["last_kline_low_30m"] = float(lows[-1])
        out["last_kline_close_30m"] = float(closes[-1])
    if vwap_2h is not None:
        out["vwap_2h"] = vwap_2h
    if ema20_close is not None:
        out["ema20_close"] = ema20_close
    if len(highs) >= 8:
        out["recent_high_2h"] = max(highs[-8:])
        out["recent_low_2h"] = min(lows[-8:])

    logger.info(
        f"[{source_label}指標] {clean}: RSI={rsi_val:.2f} BB上={ub_value} BB下={lb_value} "
        f"現價={current_price} ATR={atr_val} VWAP_2h={vwap_2h} EMA20={ema20_close} "
        f"2h高低=({out.get('recent_high_2h')}, {out.get('recent_low_2h')}) "
        f"末K=({out.get('last_kline_open_30m')},{out.get('last_kline_high_30m')},"
        f"{out.get('last_kline_low_30m')},{out.get('last_kline_close_30m')})"
    )
    return out


def _parse_kline_rows(raw: list) -> Tuple[list, list, list, list, list]:
    """解析 OHLCV K 線列表（相容 dict 格式與 [ts,o,h,l,c,v] 格式）。"""
    opens, highs, lows, closes, volumes = [], [], [], [], []
    for row in raw:
        o = h = l = c = vol = None
        if isinstance(row, dict):
            o = row.get("open") or row.get("o") or row.get("openPrice")
            h = row.get("high") or row.get("h") or row.get("highPrice")
            l = row.get("low") or row.get("l") or row.get("lowPrice")
            c = row.get("close") or row.get("c") or row.get("closePrice") or row.get("close_price")
            vol = row.get("volume") or row.get("v") or row.get("vol") or row.get("baseVolume")
        elif isinstance(row, (list, tuple)) and len(row) >= 5:
            o, h, l, c = row[1], row[2], row[3], row[4]
            vol = row[5] if len(row) > 5 else None
        if o is not None and c is not None:
            try:
                opens.append(float(o)); highs.append(float(h or c))
                lows.append(float(l or c)); closes.append(float(c))
                volumes.append(float(vol) if vol is not None else 0.0)
            except (TypeError, ValueError):
                pass
    return opens, highs, lows, closes, volumes


def _fetch_cg_klines_and_calc(symbol: str, interval: str = "15m", limit: int = 60) -> Optional[Dict[str, Any]]:
    """【CoinGlass-First K 線】使用 CoinGlass /api/futures/price/history 取 OHLCV，
    本地計算 RSI/布林帶/ATR/EMA20/VWAP_2h/MACD/結構高低點，回傳與 BingX 版本相同格式。
    依序嘗試 Binance → OKX → Bybit，直到取得足夠 K 線。
    """
    clean = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    # CoinGlass symbol 格式：通常為 {base}USDT（如 BTCUSDT）或 1000PEPEUSDT
    try_pairs = [f"{clean}USDT", f"1000{clean}USDT"]
    # 優先用大所（流動性佳），BingX 和 Bitget 作為後補覆蓋 BingX-only 小幣
    # → 讓 CoinGlass 統一拉 K 線，不再需要 Plan B 直接打 BingX
    exchanges_to_try = ["Binance", "OKX", "Bybit", "BingX", "Bitget"]
    headers_cg = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}

    for exchange in exchanges_to_try:
        for sym_pair in try_pairs:
            try:
                _respect_coinglass_rate_limit()
                r = requests.get(
                    f"{CG_API_BASE}/api/futures/price/history",
                    headers=headers_cg,
                    params={"exchange": exchange, "symbol": sym_pair,
                            "interval": interval, "limit": limit},
                    timeout=10,
                )
                if r.status_code == 429:
                    logger.warning(f"[CG K線] {clean} 429 限流，短暫等待")
                    time.sleep(1.5)
                    continue
                if r.status_code != 200:
                    continue
                j = r.json()
                if j.get("code") not in (0, "0", 200, "200", None):
                    continue
                raw = j.get("data") or j.get("list") or []
                if not isinstance(raw, list) or len(raw) < 20:
                    continue
                opens, highs, lows, closes, volumes = _parse_kline_rows(raw)
                if len(closes) < 20:
                    continue
                logger.info(
                    f"[CG K線] {clean}: {exchange} {sym_pair} {interval} {len(raw)} 根，"
                    f"開始計算 RSI/BB/ATR/EMA/VWAP"
                )
                result = _calc_indicators_from_ohlcv(
                    opens, highs, lows, closes, volumes,
                    clean, f"CoinGlass/{exchange}", sym_pair,
                )
                if result:
                    result["source"] = "CoinGlass"
                    return result
            except Exception as e:
                logger.debug(f"[CG K線] {clean}/{exchange}/{sym_pair} 異常: {e}")
                continue

    logger.warning(f"[CG K線] {clean}: 所有交易所/交易對均無法取得足夠 K 線（嘗試 {exchanges_to_try}）")
    return None


def calculate_technicals(symbol: str, bingx_symbol_override: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    技術指標：
    - Plan A = CoinGlass futures/price/history K 線本地計算（RSI/布林/EMA20/VWAP_2h/ATR/MACD/結構高低點）
    - Plan B = BingX 15m K 線本地計算（CoinGlass 不可用時備援）
    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()

    # ── Plan A：CoinGlass futures/price/history K 線（主數據源）────────────
    logger.info(f"[技術指標] {base}: Plan A CoinGlass K 線計算技術指標")
    tech = _fetch_cg_klines_and_calc(symbol)
    if tech:
        tech["source"] = "CoinGlass"
        return tech

    # ── Plan B：BingX K 線備援（CoinGlass 五所均無資料時）─────────────────────────
    logger.warning(f"[技術指標] {base}: CoinGlass K 線失敗（已試 Binance/OKX/Bybit/BingX/Bitget），切換 Plan B 直接打 BingX")
    tech_bx = _fetch_bingx_klines_and_calc(symbol, preferred_symbol=bingx_symbol_override)
    if tech_bx:
        tech_bx["source"] = "BingX"
        logger.info(f"[技術指標] {base}: Plan B BingX 完成 RSI={tech_bx.get('rsi')}")
        return tech_bx

    logger.warning(f"[技術指標] {base}: CoinGlass 與 BingX 均無法取得 K 線，技術指標失敗")
    return None


# 四區塊 + 五星制：zone 為推播區塊名，stars 1=最差 5=最佳
ZONE_DIP = "抄底區"
ZONE_TOP = "摸頭區"
ZONE_BREAKOUT_LONG = "突破追漲區"
ZONE_BREAKOUT_SHORT = "跌破追跌區"

# 資金費率門檻（持倉異常＋費率為主，少依賴 K 線）
FUNDING_POSITIVE = 0.0005   # 0.05%，高於此視為多頭擁擠
FUNDING_NEGATIVE = -0.0005  # -0.05%，低於此視為空頭擁擠（易嘎空）
FUNDING_EXTREME = 0.0003    # v3.0 極端費率 0.03%，用於嘎空/殺多加權標註

# 【持倉異常 = 99% 山寨幣】15m 高頻模式，門檻已針對 15m 週期 + 山寨幣精品化校準
# 目標：每輪推播 3~6 個高勝率「精品」，過濾小幣/資料不足的垃圾訊號
MAIN_COINS = {"BTC", "ETH"}   # 主流幣
OI_MAIN_COIN_MIN = 3.0       # 主流幣初選門檻
OI_ALTCOIN_MIN = 1.5         # 山寨幣初選門檻（提高至 1.5%，過濾微小 OI 波動）

# 星等門檻（基礎值；實際 4/5 星門檻會在 runtime 依 OI 分佈動態調整，以適應不同波動環境）
OI_FOR_5_STAR = 3.0   # 5星門檻（15m 山寨 ≥3% 才算真正強勢）
OI_FOR_4_STAR = 2.2   # 4星門檻（≥2.2% 有明確資金進場）
OI_FOR_ELITE = 3.0    # 鑽石 💎 門檻（同 5 星）

# 狙擊鏡止盈風報門檻：止盈若低於此 R 不推播（避免賠率差、維持勝率品質）
MIN_TP1_R_FOR_PUSH = 0.8

# 抄底/摸頭 15m 門檻（山寨）：略放寬仍算低位/高位，減少誤殺
PRICE_DIP_MAX = 3.0    # 抄底：15m 漲幅 ≤ 3% 才算低位，超過改標追漲
PRICE_TOP_MIN = -3.0   # 摸頭：15m 跌幅 ≥ -3% 才算高位，跌破改標追跌

# 24H 趨勢門檻（保守山寨）：12% 以上才當假抄底/假摸頭，適應日波動
TREND_24H_THRESHOLD = 12.0

# ── 訊號分級策略設計（三級制）────────────────────────────────────────────────
# ┌──────────────┬──────────────────────────────────────────┬──────────────┬──────────────────────────────────────┐
# │ 訊號         │ 門檻                                      │ 倉位         │ 目標勝率 / 說明                        │
# ├──────────────┼──────────────────────────────────────────┼──────────────┼──────────────────────────────────────┤
# │ 👻賭鬼 ⭐×4  │ OI≥2.2%，CVD 未確認                      │ 試單 2.5%    │ 樂透策略，低勝率高風報比 TP2 可達 4R   │
# │ 🚅列車 ⭐×5  │ OI≥3.0%+CVD 同向確認                     │ 標準倉 5%    │ 目標勝率 ≥65%，TP1=1.2R TP2=2~3R      │
# │ ✈️頭等 ⭐×6  │ 5★+摸頭/抄底+量≥1M+訂單流+RSI 極端      │ 重倉 7~10%   │ 目標勝率 ≥75%，三重確認最高 10%        │
# └──────────────┴──────────────────────────────────────────┴──────────────┴──────────────────────────────────────┘
# 頭等艙內部細分：普通頭等(7%) / 鑽石共振(三重確認 10%)，對外統一顯示 ✈️ 頭等機艙
# 價格位階：抄底 15m≤3%；摸頭 15m≥-3%。24h 假訊號門檻 12%。主流幣 OI≥3.0% 才進榜。

# 鑽石 RSI 輔助：摸頭≥60 / 抄底≤40，無 RSI 不擋
RSI_FILTER_TOP_MIN = 60
RSI_FILTER_DIP_MAX = 40
RSI_FILTER_BREAKOUT_LONG_MIN = 45   # v3.0 追漲時 RSI 不低於 45
RSI_FILTER_BREAKOUT_SHORT_MAX = 55  # v3.0 追跌時 RSI 不高於 55
# 鑽石級 CVD 驗證：CVD 變化量需至少佔 OI 變化的一定比例，避免主力對敲假量
CVD_ELITE_MIN_RATIO = 0.3
# 鑽石級 24h 成交量門檻（略放寬：5M 即給頭等艙）
VOLUME_ELITE_MIN_USD = 1_000_000   # 山寨幣成交量普遍較低，1M 即可
# A 級費率硬過濾：資金費率 > 0.05% 視為多頭擁擠，不推 A 級防接盤殺多
FUNDING_A_GRADE_MAX = 0.0005


def _classify_signal_and_tier(
    item: Dict,
    category: str,
    tech: Optional[Dict],
    funding_rate: Optional[float] = None,
    price_chg_24h: Optional[float] = None,
    cvd_change_1h: Optional[float] = None,
    whale_index: Optional[float] = None,
    retail_ratio: Optional[float] = None,
) -> Optional[Tuple[str, str, int, str, str]]:
    """
    分級核心邏輯 (v4.0 極速版)：
    1. 刪除所有 3 星邏輯，未達 4 星直接回傳 None (提升效率)。
    2. 引入 CVD 驗證：5 星必須 CVD 同向，否則降級為 4 星 (區分共識 vs 博弈)。
    """

    # 0. 效率過濾：OI 未達 4 星門檻，直接丟棄，不進行後續運算
    oi = item.get("oiChange30m") or 0
    abs_oi = abs(oi)

    # 動態 OI 門檻：若本輪樣本數足夠，採用「平均 + k*標準差」作為 4/5 星實際門檻，
    # 讓 5 星只落在極端 OI 爆發（>2σ）的標的上，適應牛熊不同波動環境。
    global _dynamic_oi_4star, _dynamic_oi_5star, _dynamic_oi_sample_size
    oi_4 = OI_FOR_4_STAR
    oi_5 = OI_FOR_5_STAR
    if _dynamic_oi_sample_size and _dynamic_oi_sample_size >= 10:
        if _dynamic_oi_4star is not None:
            oi_4 = max(OI_FOR_4_STAR, _dynamic_oi_4star)
        if _dynamic_oi_5star is not None:
            oi_5 = max(OI_FOR_5_STAR, _dynamic_oi_5star)

    if abs_oi < oi_4:
        return None

    # A 級費率硬過濾：
    # - 多單：賭鬼樂透(A級) 且 資金費率 > 0.05% 不推，防接盤殺多
    # - 空單：賭鬼樂透(A級) 且 資金費率 < -0.05% 不推，防錯殺空頭（費率過度負值）
    def _ret_4(label: str, zone: str, rsi_desc: str, reason: str):
        if funding_rate is not None:
            # 多單 A 級：費率過高不推
            if zone in (ZONE_BREAKOUT_LONG, ZONE_DIP) and funding_rate > FUNDING_A_GRADE_MAX:
                return None
            # 空單 A 級：費率過負不推
            if zone in (ZONE_BREAKOUT_SHORT, ZONE_TOP) and funding_rate < -FUNDING_A_GRADE_MAX:
                return None
        return _apply_retail_funding(apply_24h(label, zone, 4, rsi_desc, reason))

    # 輔助函數：24H 趨勢修正 (Truth Protocol)
    def apply_24h(label: str, zone: str, stars: int, rsi_desc: str, reason: str) -> Tuple[str, str, int, str, str]:
        if price_chg_24h is not None and isinstance(price_chg_24h, (int, float)):
            if zone == ZONE_DIP and price_chg_24h > TREND_24H_THRESHOLD:
                return ("🟢 強勢嘎空", ZONE_BREAKOUT_LONG, stars, rsi_desc, f"🔥 強勢嘎空 (24h漲 {price_chg_24h:.1f}%)")
            if zone == ZONE_TOP and price_chg_24h < -TREND_24H_THRESHOLD:
                return ("🔴 恐慌下殺", ZONE_BREAKOUT_SHORT, stars, rsi_desc, f"🩸 恐慌下殺 (24h跌 {abs(price_chg_24h):.1f}%)")
            if zone == ZONE_BREAKOUT_LONG and price_chg_24h < -TREND_24H_THRESHOLD:
                return ("🟢 深跌反彈", ZONE_DIP, stars, rsi_desc, f"📉 深跌反彈 (24h跌 {abs(price_chg_24h):.1f}%)")
        return (label, zone, stars, rsi_desc, reason)

    # 輔助函數：v3.0 散戶濾網 & 極端費率 & 槓桿擁擠聯合降級
    def _apply_retail_funding(tup: Tuple[str, str, int, str, str]) -> Tuple[str, str, int, str, str]:
        label, zone, stars, rsi_desc, reason = tup
        # 做多訊號：突破追漲(long_open) 或 多軍斷頭抄底(long_close)
        is_long = category in ("long_open", "long_close")
        # 做空訊號：跌破追跌(short_open) 或 空軍被軋摸頭(short_close)
        is_short = category in ("short_open", "short_close")

        # 散戶過熱降級
        if retail_ratio is not None and retail_ratio > 1.45:
            if is_long:
                stars = max(4, stars - 1)
                reason = reason + " ⚠️ 散戶過熱"
            elif is_short:
                reason = reason + " ✅ 散戶接盤"

        # 【槓桿極度擁擠聯合濾網】
        # Funding > 0.1% + L/S Ratio > 2.0 → 多頭擠壓（Short Squeeze 的反面）前兆
        # 大量多頭以高費率堆積，一旦行情反轉，連鎖爆倉極易產生插針
        _fr = funding_rate if isinstance(funding_rate, (int, float)) else None
        _rr = retail_ratio if isinstance(retail_ratio, (int, float)) else None
        if _fr is not None and _rr is not None:
            if _fr > 0.001 and _rr > 2.0:  # 0.1% 費率 且 散戶多空比 > 2.0
                if stars == 5:
                    stars = 4
                    reason = reason + f" ⚠️槓桿極度擁擠(FR={_fr*100:.3f}% L/S={_rr:.1f})，謹防插針"
                    logger.info(
                        f"[槓桿擁擠降星] {item.get('symbol','')} "
                        f"FR={_fr*100:.3f}% L/S={_rr:.1f} → 5星降4星"
                    )
                else:
                    reason = reason + f" ⚠️槓桿擁擠(FR={_fr*100:.3f}%)"

        # 極端費率方向性標註
        if funding_rate is not None:
            if funding_rate < -FUNDING_EXTREME and is_long:
                reason = reason + " 🔥 費率負(嘎空)"
            if funding_rate > FUNDING_EXTREME and is_short:
                reason = reason + " ⛽ 費率正(殺多)"
        return (label, zone, stars, rsi_desc, reason)

    # 1. 準備基礎數據
    price_chg_30m = item.get("priceChange30m")
    if price_chg_30m is not None and not isinstance(price_chg_30m, (int, float)):
        price_chg_30m = None

    rsi = tech.get("rsi") if tech else None
    touch_upper = tech.get("touch_upper", False) if tech else False
    touch_lower = tech.get("touch_lower", False) if tech else False
    rsi_desc = "RSI —"
    if tech and rsi is not None:
        if rsi > 70:
            rsi_desc = f"RSI {rsi:.0f}(超買)"
        elif rsi < 30:
            rsi_desc = f"RSI {rsi:.0f}(超賣)"
        else:
            rsi_desc = f"RSI {rsi:.0f}"
        if touch_upper:
            rsi_desc += " 觸頂"
        if touch_lower:
            rsi_desc += " 觸底"

    funding_negative = funding_rate is not None and funding_rate < FUNDING_NEGATIVE
    funding_positive = funding_rate is not None and funding_rate > FUNDING_POSITIVE

    # 2. CVD 趨勢驗證 (用於決定是否降級)
    # 順勢：開多且 CVD 買 / 開空且 CVD 賣 / 平倉視為「原趨勢的反向」確認
    is_trend_confirmed = False
    cvd_price_divergent = False
    if cvd_change_1h is not None:
        if category == "long_open" and cvd_change_1h > 0:
            # 開多 + CVD 買 → 多頭共識
            is_trend_confirmed = True
        elif category == "short_open" and cvd_change_1h < 0:
            # 開空 + CVD 賣 → 空頭共識
            is_trend_confirmed = True
        elif category == "long_close" and cvd_change_1h < 0:
            # 多頭斷頭 + CVD 恐慌賣極值 → 抄底方向強確認
            is_trend_confirmed = True
        elif category == "short_close" and cvd_change_1h > 0:
            # 空頭被軋 + CVD FOMO買極值 → 摸頭方向強確認
            is_trend_confirmed = True

    # CVD 與價格 30m 方向相反 → 強制僅視為賭鬼級 (4 星) 或不推播
    price_chg_30m = item.get("priceChange30m")
    if isinstance(price_chg_30m, (int, float)) and cvd_change_1h is not None and isinstance(cvd_change_1h, (int, float)):
        if price_chg_30m > 0 and cvd_change_1h < 0:
            cvd_price_divergent = True
        elif price_chg_30m < 0 and cvd_change_1h > 0:
            cvd_price_divergent = True

    # 3. 判斷邏輯開始

    # === 5 星邏輯 (需 OI 達標 且 CVD 確認) ===
    if abs_oi >= oi_5 and not cvd_price_divergent:
        if is_trend_confirmed:
            # [真 5 星]：量大 + 方向對 = 共識盤
            reason_suffix = " (CVD確認)"

            if oi > 0 and (funding_negative or (funding_rate is not None and funding_rate < -0.001)):
                return _apply_retail_funding(apply_24h("🟢 潛在嘎空", ZONE_BREAKOUT_LONG, 5, rsi_desc, "🚀 主力共識+費率負" + reason_suffix))

            # 多頭斷頭（long_close）→ 鑽石抄底
            if category == "long_close":
                return _apply_retail_funding(
                    apply_24h("🟢 鑽石抄底", ZONE_DIP, 5, rsi_desc, "🩸 恐慌殺跌+CVD賣極值" + reason_suffix)
                )

            # 空頭被軋（short_close）→ 鑽石摸頭
            if category == "short_close":
                return _apply_retail_funding(
                    apply_24h("🔴 鑽石摸頭", ZONE_TOP, 5, rsi_desc, "⛽ 軋空爆發+CVD買極值" + reason_suffix)
                )

            if oi > 0 and category == "long_open":
                return _apply_retail_funding(apply_24h("🟢 順勢追多", ZONE_BREAKOUT_LONG, 5, rsi_desc, "🚀 量價齊揚" + reason_suffix))

            if oi > 0 and category == "short_open":
                return _apply_retail_funding(apply_24h("🔴 順勢追空", ZONE_BREAKOUT_SHORT, 5, rsi_desc, "📉 量價齊跌" + reason_suffix))

            zone = ZONE_BREAKOUT_LONG if oi > 0 else ZONE_BREAKOUT_SHORT
            return _apply_retail_funding(apply_24h("🟡 順勢觀察", zone, 5, rsi_desc, "🚀 主力共識盤" + reason_suffix))

        else:
            # [降級 4 星]：OI 很大 但 CVD 背離 = 激戰/轉折盤
            downgrade_reason = "⚠️ 持倉大增但CVD背離 (多空激戰)"
            if oi > 0:
                zone = ZONE_BREAKOUT_LONG if category == "long_open" else ZONE_BREAKOUT_SHORT
                return _ret_4("🟡 激戰博弈", zone, rsi_desc, downgrade_reason)
            else:
                zone = ZONE_DIP if category == "short_close" else ZONE_TOP
                return _ret_4("🟡 激戰博弈", zone, rsi_desc, downgrade_reason)

    # === 4 星邏輯 (OI 中等 或 5 星降級) ===

    # 4 星 + CVD 順向：對「空頭平倉 / 多頭平倉」給更精確的摸底 / 摸頭文案
    if is_trend_confirmed:
        reason_suffix_4 = " (CVD確認)"
        if category == "short_close":
            # 空頭平倉 + CVD 賣極值 → 恐慌殺跌（抄底）
            return _apply_retail_funding(
                apply_24h("🟢 抄底", ZONE_DIP, 4, rsi_desc, "🩸 恐慌殺跌+CVD賣極值" + reason_suffix_4)
            )
        elif category == "long_close":
            # 多頭平倉 + CVD 買極值 → 軋空爆發（摸頭）
            return _apply_retail_funding(
                apply_24h("🔴 摸頭", ZONE_TOP, 4, rsi_desc, "⛽ 軋空爆發+CVD買極值" + reason_suffix_4)
            )

    # 4 星一般邏輯（含 CVD 背離分開 open/close 文案）
    if oi > 0 and (funding_negative or category == "long_open"):
        return _ret_4("🟢 試單做多", ZONE_BREAKOUT_LONG, rsi_desc, "持倉增加 (觀察動能)")

    if oi < 0 and (funding_positive or category == "short_close"):
        if price_chg_30m is not None and price_chg_30m < PRICE_TOP_MIN:
            return _ret_4("🟡 順勢觀察", ZONE_BREAKOUT_SHORT, rsi_desc, "價已跌→追空")
        # 空頭平倉＝持倉驟降，用「被軋」語氣
        return _ret_4("🔴 偏空過熱", ZONE_TOP, rsi_desc, "空頭被軋 (摸頭試單)")

    if oi < 0 and (funding_negative or category == "long_close"):
        if price_chg_30m is not None and price_chg_30m > PRICE_DIP_MAX:
            return _ret_4("🟢 潛在嘎空", ZONE_BREAKOUT_LONG, rsi_desc, "價已漲→追多")
        # 多頭平倉＝持倉驟降，用「斷頭」語氣
        return _ret_4("🟢 超跌試多", ZONE_DIP, rsi_desc, "多頭斷頭 (摸底試單)")

    if oi > 0:
        zone = ZONE_BREAKOUT_LONG if category == "long_open" else ZONE_BREAKOUT_SHORT
        return _ret_4("🟡 順勢觀察", zone, rsi_desc, "持倉異動順勢")

    if oi < 0:
        if price_chg_30m is not None and price_chg_30m < PRICE_TOP_MIN:
            return _ret_4("🟡 順勢觀察", ZONE_BREAKOUT_SHORT, rsi_desc, "價跌→追空")
        # 持倉減少的一般摸頭情境
        return _ret_4("🔴 摸頭做空", ZONE_TOP, rsi_desc, "持倉減 (偏空)")

    return None


def build_report_message_tiered(
    enriched_items: List[Dict],
    processed_count: int = 0,
    oi_success_count: int = 0,
) -> str:
    """
    【傑克 15分狙擊鏡 - 暴力喊單版】
    文案極度簡化，只給重點：方向、點位、理由。SL 與 TP 同源（主力成本 或 ATR）。
    """
    def star_str(n: int) -> str:
        return "⭐" * (n or 0)

    def fmt_pct(num):
        if num is None or (isinstance(num, float) and (num != num)):
            return "0.00%"
        return f"{'+' if num >= 0 else ''}{num:.2f}%"

    def calc_sl_tp(
        atr: Optional[float],
        price: float,
        zone: str,
        is_long: bool,
        stars: int,
        is_elite: bool,
        vwap_2h: Optional[float] = None,
        ema20_close: Optional[float] = None,
        ub_value: Optional[float] = None,
        lb_value: Optional[float] = None,
        cvd_divergence: bool = False,
        recent_high_2h: Optional[float] = None,
        recent_low_2h: Optional[float] = None,
        last_kline_high_30m: Optional[float] = None,
        last_kline_low_30m: Optional[float] = None,
        fp_support: Optional[float] = None,    # 腳步圖最近支撐位
        fp_resistance: Optional[float] = None,  # 腳步圖最近阻力位
        fp_poc: Optional[float] = None,         # 腳步圖 Point of Control
    ):
        """
        【OI 起漲點防守法 v2 — 腳步圖增強版】
        SL 優先順序（做多）：腳步圖支撐 → 結構低點(recent_low_2h) → ATR floor
        TP 優先順序（做多）：腳步圖阻力 → VWAP對稱 → R倍數
        - 列車(S/S+)：SL 上限 8~12%（依波動動態）
        - 賭鬼(A)：SL 上限 6~10%（依波動動態）
        - 腳步圖阻力在 1.0R~3.5R 範圍內才採用，超出則回退 R 倍數
        """
        _na = "-"
        def fmt_p(p):
            if p is None or (isinstance(p, float) and (p != p)) or p <= 0:
                return _na
            if p < 0.0001:
                return f"{p:.8f}"
            if p < 0.01:
                return f"{p:.6f}"
            if p < 1:
                return f"{p:.5f}"
            if p < 10:
                return f"{p:.4f}"
            return f"{p:.2f}"

        # 確保回傳長度固定 13 個值（避免解包錯誤）
        def _ret(
            sl_price: Optional[float],
            tp1_price: Optional[float],
            tp2_price: Optional[float],
            r_tp1: Optional[float],
            r_tp2: Optional[float],
            tp1_label: Optional[str],
            tp2_label: Optional[str],
            sl_capped: bool,
            energy_exhausted: bool,
            tp1_real_note: str = "",
            tp1_atr_str: str = "-",
            tp1_atr_note: str = "",
        ):
            sl_str = fmt_p(sl_price) if sl_price is not None else _na
            tp1_str = fmt_p(tp1_price) if tp1_price is not None else _na
            tp2_str = fmt_p(tp2_price) if tp2_price is not None else _na
            tp1_real_str = tp1_str
            return (
                sl_str,
                tp1_str,
                tp2_str,
                r_tp1,
                r_tp2,
                tp1_label or "",
                tp2_label or "",
                sl_capped,
                energy_exhausted,
                tp1_real_str,
                tp1_real_note,
                tp1_atr_str,
                tp1_atr_note,
            )

        if price is None or price <= 0:
            return _ret(None, None, None, None, None, None, None, False, False)

        # 分級：列車 (S/S+) vs 賭鬼 (A)
        is_train = (stars or 0) >= 5
        is_gambler = (stars or 0) == 4

        # 結構 SL 基礎：recent_low_2h / recent_high_2h 優先，否則用當前 30m K 線 high/low
        basis_low = None
        basis_high = None
        if recent_low_2h is not None and isinstance(recent_low_2h, (int, float)) and recent_low_2h > 0:
            basis_low = float(recent_low_2h)
        elif last_kline_low_30m is not None and isinstance(last_kline_low_30m, (int, float)) and last_kline_low_30m > 0:
            basis_low = float(last_kline_low_30m)

        if recent_high_2h is not None and isinstance(recent_high_2h, (int, float)) and recent_high_2h > 0:
            basis_high = float(recent_high_2h)
        elif last_kline_high_30m is not None and isinstance(last_kline_high_30m, (int, float)) and last_kline_high_30m > 0:
            basis_high = float(last_kline_high_30m)

        atr_val = float(atr) if atr is not None and isinstance(atr, (int, float)) and atr > 0 else None
        buffer_pct = 0.005  # 0.5% buffer（結構位微調）
        sl_price = None
        sl_capped = False
        sl_source = "ATR"  # 紀錄 SL 來源，用於訊息說明

        # ── ATR 波動等級分類（決定 ATR 倍率與上限）────────────────────────────
        # 15m 高頻模式：SL 須貼近結構，過寬意味信號已失效
        # 列車(5星) / 賭鬼(4星)
        # 高波動(ATR>3%)：最大 8% / 6%
        # 中波動(ATR 2-3%)：最大 6% / 5%
        # 低波動(ATR<2%)：最大 5% / 4%
        vol_pct = (atr_val / price) * 100.0 if (atr_val is not None and price > 0) else 0.0
        if vol_pct > 3.0:
            atr_mult = 1.5    # 高波動：PEPE/SOL 系，避免 15M 正常波動洗出場
            max_pct = 0.08 if is_train else 0.06
        elif vol_pct > 2.0:
            atr_mult = 1.35   # 中波動：適度放寬
            max_pct = 0.06 if is_train else 0.05
        else:
            atr_mult = 1.2    # 低波動：保守
            max_pct = 0.05 if is_train else 0.04

        # ── 腳步圖支撐/阻力有效性校驗 ─────────────────────────────────────────
        # fp_support 必須在現價下方（做多），且距現價不超過 max_pct×1.1
        _fp_sup_valid = (
            fp_support is not None
            and isinstance(fp_support, (int, float))
            and fp_support > 0
            and is_long
            and fp_support < price
            and abs(price - fp_support) / price <= max_pct * 1.1
        )
        _fp_res_valid = (
            fp_resistance is not None
            and isinstance(fp_resistance, (int, float))
            and fp_resistance > 0
            and fp_resistance > price
            and abs(fp_resistance - price) / price <= 0.15  # 阻力不超過 15%
        )
        # 對應做空的鏡像
        if not is_long:
            _fp_sup_valid = (
                fp_support is not None
                and isinstance(fp_support, (int, float))
                and fp_support > price
                and abs(fp_support - price) / price <= max_pct * 1.1
            )
            _fp_res_valid = (
                fp_resistance is not None
                and isinstance(fp_resistance, (int, float))
                and fp_resistance > 0
                and fp_resistance < price
                and abs(price - fp_resistance) / price <= 0.15
            )

        if is_train:
            # ── 列車：SL 優先順序：腳步圖支撐 > 結構低點 > ATR floor ──
            if _fp_sup_valid:
                fp_sl = (float(fp_support) * (1.0 - buffer_pct) if is_long
                         else float(fp_support) * (1.0 + buffer_pct))
                # ATR 保底（不能比 ATR floor 更窄）
                atr_floor = (atr_mult * atr_val) if atr_val is not None else price * 0.02
                proposed_dist = abs(price - fp_sl)
                sl_dist = max(proposed_dist, atr_floor)
                sl_price = price - sl_dist if is_long else price + sl_dist
                sl_source = "腳步圖支撐"
            else:
                basis_price = basis_low if is_long else basis_high
                struct_dist = None
                if basis_price is not None:
                    adj = basis_price * (1 - buffer_pct) if is_long else basis_price * (1 + buffer_pct)
                    struct_dist = abs(price - adj)
                atr_floor = (atr_mult * atr_val) if atr_val is not None else price * 0.02
                sl_dist = max(struct_dist, atr_floor) if struct_dist is not None else atr_floor
                sl_price = price - sl_dist if is_long else price + sl_dist
                sl_source = "結構低點" if struct_dist is not None else "ATR"
            dist_pct = abs(price - sl_price) / price if price > 0 else 0
            if dist_pct > max_pct:
                sl_capped = True
                sl_price = price * (1.0 - max_pct) if is_long else price * (1.0 + max_pct)
        else:
            # ── 賭鬼：SL 優先順序：腳步圖支撐 > 結構優先 > ATR ──
            if _fp_sup_valid:
                fp_sl = (float(fp_support) * (1.0 - buffer_pct) if is_long
                         else float(fp_support) * (1.0 + buffer_pct))
                atr_floor = (atr_mult * atr_val) if atr_val is not None else price * 0.02
                proposed_dist = abs(price - fp_sl)
                sl_dist = max(proposed_dist, atr_floor * 0.8)
                sl_price = price - sl_dist if is_long else price + sl_dist
                sl_source = "腳步圖支撐"
            elif is_long and basis_low is not None:
                sl_price = basis_low * (1.0 - buffer_pct)
                sl_source = "結構低點"
            elif (not is_long) and basis_high is not None:
                sl_price = basis_high * (1.0 + buffer_pct)
                sl_source = "結構高點"

            if sl_price is None:
                sl_dist = (atr_mult * atr_val) if atr_val is not None else price * (0.03 if is_gambler else 0.02)
                sl_price = price - sl_dist if is_long else price + sl_dist
                sl_source = "ATR"

            dist_pct = abs(price - sl_price) / price if price > 0 else 0
            if dist_pct > max_pct:
                sl_capped = True
                sl_price = price * (1.0 - max_pct) if is_long else price * (1.0 + max_pct)

        # 風險距離（R 的母數）
        risk_dist = (price - sl_price) if is_long else (sl_price - price)
        # 若 SL 在錯誤側（空單 SL 低於現價 / 多單 SL 高於現價）導致 risk_dist <= 0，
        # 用 ATR 或 1% 價當 fallback，仍算出 TP 避免推播顯示「暫無數據」
        if not risk_dist or risk_dist <= 0:
            atr_floor = (atr_mult * atr_val) if atr_val is not None else price * 0.02
            risk_dist = max(atr_floor, price * 0.005)
        # 避免太小距離導致 R 異常飆高
        min_risk = price * 0.005
        if risk_dist < min_risk:
            risk_dist = min_risk

        # 列車 / 賭鬼 TP 設定
        tp1_price = tp2_price = None
        tp1_label = tp2_label = ""
        r_tp1 = r_tp2 = None

        # 列車 (S/S+)：TP 優先順序：腳步圖阻力 > VWAP對稱 > 1.2R
        if is_train:
            # 腳步圖阻力在 1.0R~3.5R 區間才採用（避免目標過近/過遠）
            if _fp_res_valid:
                fp_res_val = float(fp_resistance) if is_long else float(fp_resistance)
                r_candidate = abs(fp_res_val - price) / risk_dist
                if 1.0 <= r_candidate <= 3.5:
                    tp1_price = fp_res_val
                    tp1_label = f"腳步圖阻力({r_candidate:.1f}R)"
                    r_tp1 = round(r_candidate, 1)

            if tp1_price is None:
                if vwap_2h is not None and isinstance(vwap_2h, (int, float)) and vwap_2h > 0:
                    vwap = float(vwap_2h)
                    if is_long:
                        cand = 2.0 * price - vwap
                        if cand > price:
                            tp1_price = cand
                            tp1_label = "主力成本"
                        else:
                            tp1_price = price + 1.2 * risk_dist
                            tp1_label = "1.2R"
                    else:
                        cand = 2.0 * vwap - price
                        if 0 < cand < price:
                            tp1_price = cand
                            tp1_label = "主力成本"
                        else:
                            tp1_price = price - 1.2 * risk_dist
                            tp1_label = "1.2R"
                else:
                    tp1_price = price + 1.2 * risk_dist if is_long else price - 1.2 * risk_dist
                    tp1_label = "1.2R"
            if r_tp1 is None and tp1_price is not None:
                r_tp1 = round(((tp1_price - price) / risk_dist) if is_long else ((price - tp1_price) / risk_dist), 1)

            # 列車/頭等艙 TP2：延伸目標 2.0R~3.0R（高信心訊號有更遠空間）
            # 腳步圖有遠端阻力（> TP1 且 ≤ 5.0R）→ 用腳步圖，否則依波動度給 2.0~3.0R
            _atr2_train = float(atr) if atr is not None and isinstance(atr, (int, float)) and atr > 0 else None
            _target_r2_train = 2.5
            if _atr2_train is not None and price > 0:
                _vp2_train = (_atr2_train / price) * 100.0
                if _vp2_train >= 3.0:
                    _target_r2_train = 2.0   # 高波動：目標保守
                elif _vp2_train <= 1.5:
                    _target_r2_train = 3.0   # 低波動：目標遠一些
                else:
                    _ratio_train = (_vp2_train - 1.5) / (3.0 - 1.5)
                    _target_r2_train = 3.0 - _ratio_train * 1.0
            _target_r2_train = max(2.0, min(3.0, _target_r2_train))
            if _fp_res_valid:
                _fp_r2_val = float(fp_resistance)
                _r_fp2_train = abs(_fp_r2_val - price) / risk_dist
                if _r_fp2_train > (r_tp1 or 1.2) and _r_fp2_train <= 5.0:
                    tp2_price = _fp_r2_val
                    tp2_label = f"腳步圖阻力({_r_fp2_train:.1f}R)"
                    r_tp2 = round(_r_fp2_train, 1)
            if tp2_price is None:
                tp2_price = price + _target_r2_train * risk_dist if is_long else price - _target_r2_train * risk_dist
                tp2_label = f"{_target_r2_train:.1f}R"
                r_tp2 = round(_target_r2_train, 1)

        # 賭鬼 (A)：TP1 優先腳步圖阻力(0.8R~2.5R 內才採)，否則固定 1.0R；TP2 理論目標
        else:
            # TP1：嘗試腳步圖阻力
            if _fp_res_valid:
                fp_res_val = float(fp_resistance)
                r_candidate = abs(fp_res_val - price) / risk_dist
                if 0.8 <= r_candidate <= 2.5:
                    tp1_price = fp_res_val
                    tp1_label = f"腳步圖阻力({r_candidate:.1f}R)"
                    r_tp1 = round(r_candidate, 1)
            if tp1_price is None:
                tp1_price = price + 1.0 * risk_dist if is_long else price - 1.0 * risk_dist
                tp1_label = "1.0R"
                r_tp1 = 1.0
            # TP2：依波動度動態選擇 2.5R~4.0R（績效評估用理論目標）
            _atr2 = float(atr) if atr is not None and isinstance(atr, (int, float)) and atr > 0 else None
            target_r2 = 3.0
            if _atr2 is not None and price > 0:
                _vp2 = (_atr2 / price) * 100.0
                if _vp2 >= 3.0:
                    target_r2 = 2.5
                elif _vp2 <= 1.5:
                    target_r2 = 3.5
                else:
                    ratio = (_vp2 - 1.5) / (3.0 - 1.5)
                    target_r2 = 3.5 - ratio * (3.5 - 2.5)
            target_r2 = max(2.5, min(4.0, target_r2))
            # 如果腳步圖阻力比 TP2 更遠，用腳步圖做 TP2
            if _fp_res_valid:
                fp_res_val = float(fp_resistance)
                r_fp2 = abs(fp_res_val - price) / risk_dist
                if r_fp2 > (r_tp1 or 1.0) and r_fp2 <= 5.0:
                    tp2_price = fp_res_val
                    tp2_label = f"腳步圖阻力({r_fp2:.1f}R)"
                    r_tp2 = round(r_fp2, 1)
            if tp2_price is None:
                tp2_price = price + target_r2 * risk_dist if is_long else price - target_r2 * risk_dist
                tp2_label = f"{target_r2:.1f}R"
                r_tp2 = round(target_r2, 1)

        # energy_exhausted / cvd_divergence 保留為附註旗標
        energy_exhausted = bool(cvd_divergence)
        # tp1_note 只保留乾淨的 R 比值標籤，不混入止損來源（避免 tp_plain_desc 解析出錯）
        tp1_note = tp1_label
        return _ret(sl_price, tp1_price, tp2_price, r_tp1, r_tp2, tp1_label, tp2_label, sl_capped, energy_exhausted, tp1_note)

    def _is_bull(x: Dict) -> bool:
        sig = x.get("signal_label") or ""
        return "做多" in sig or "追多" in sig or "嘎空" in sig or "抄底" in sig

    def _pass_rsi_filter(x: Dict, z: str) -> bool:
        rsi = x.get("rsi")
        if z == ZONE_TOP and RSI_FILTER_TOP_MIN is not None:
            return rsi is not None and rsi >= RSI_FILTER_TOP_MIN
        if z == ZONE_DIP and RSI_FILTER_DIP_MAX is not None:
            return rsi is not None and rsi <= RSI_FILTER_DIP_MAX
        if z == ZONE_BREAKOUT_LONG and RSI_FILTER_BREAKOUT_LONG_MIN is not None:
            return rsi is not None and rsi >= RSI_FILTER_BREAKOUT_LONG_MIN
        if z == ZONE_BREAKOUT_SHORT and RSI_FILTER_BREAKOUT_SHORT_MAX is not None:
            return rsi is not None and rsi <= RSI_FILTER_BREAKOUT_SHORT_MAX
        return True

    # 頭等艙 ✈️ = 摸頭/抄底 + 5星 + |OI|>=OI_FOR_ELITE + 成交量≥1M + 至少一項訂單流數據 + RSI 輔助
    # 鯨魚指數不強制（山寨幣普遍無覆蓋）；成交量門檻放寬（山寨幣量級較低）
    def _is_elite(x: Dict) -> bool:
        if (x.get("stars") or 0) != 5:
            return False
        z = x.get("zone")
        if z not in (ZONE_TOP, ZONE_DIP):
            return False
        if abs(x.get("oiChange30m") or 0) < OI_FOR_ELITE:
            return False
        if (x.get("volume_usd") or 0) < VOLUME_ELITE_MIN_USD:
            return False  # 最低流動性門檻（1M），避免極端冷門幣
        # 至少一項訂單流數據（taker/大戶多空比/OI趨勢任一有值）
        # 三項全 None 代表市場微結構資料完全缺失，無法確認方向，不給頭等艙
        _has_flow_data = (
            x.get("taker_ratio") is not None or
            x.get("top_ls_ratio") is not None or
            x.get("oi_trend") is not None
        )
        if not _has_flow_data:
            return False
        rsi = x.get("rsi")
        if rsi is not None and isinstance(rsi, (int, float)):
            if z == ZONE_TOP:
                if rsi < RSI_FILTER_TOP_MIN:
                    return False
            if z == ZONE_DIP:
                if rsi > RSI_FILTER_DIP_MAX:
                    return False
        return True

    # 💎 鑽石共振 = elite + 5M動能共振 + 全網資金共識（三重確認，最高信號）
    def _is_diamond(x: Dict) -> bool:
        # 路徑 A（原三重確認）：頭等艙 + 5m OI 共振 + 全網共識
        path_a = (
            _is_elite(x)
            and x.get("has_5m_resonance") is True
            and x.get("is_global_consensus") is True
        )
        # 路徑 B（RSI 底部鑽石）：頭等艙 + 5m RSI 極端超賣/超買 → 底部/頂部共振升等
        path_b = (
            _is_elite(x)
            and x.get("has_5m_rsi_extreme") is True
        )
        return path_a or path_b

    def _action_label(zone: str, is_bull: bool) -> str:
        if zone == ZONE_TOP:
            return "摸頭做空"
        if zone == ZONE_DIP:
            return "抄底做多"
        if zone == ZONE_BREAKOUT_LONG:
            return "追多"
        if zone == ZONE_BREAKOUT_SHORT:
            return "追空"
        return "做多" if is_bull else "做空"

    def _reason_plain(reason: str) -> str:
        """
        把邏輯裡的持倉 / 費率 / CVD 專業術語，轉成跟單者聽得懂的白話說明。
        盡量描述「主力在做什麼」與「對我代表什麼風險 / 機會」。
        """
        if not reason:
            return "籌碼有異動"
        r = (reason or "").strip()

        # 1) 平倉 / 減倉類：多頭平倉 / 空頭平倉
        if "持倉大減 (空頭平倉→摸底)" in r or ("空頭平倉" in r and "摸底" in r):
            r = r.replace(
                "持倉大減 (空頭平倉→摸底)",
                "做空的人正在陸續回補，空方力量在退場，這裡有機會變成短線低點"
            )
        if "持倉大減 (多頭平倉→摸頭)" in r or ("多頭平倉" in r and "摸頭" in r):
            r = r.replace(
                "持倉大減 (多頭平倉→摸頭)",
                "做多的人開始一批一批賣出，短線漲多容易回跌，適合考慮短空"
            )

        # 2) 持倉大增 + 費率：解釋為主力加碼 / 軋空 / 殺多
        if "持倉大增+費率負" in r:
            r = r.replace(
                "持倉大增+費率負 (嘎空潛力)",
                "主力瘋狂買入，做空的人反而要付錢，空單隨時可能被軋爆，上漲動能非常強"
            )
        if "持倉大增 (追多)" in r:
            r = r.replace(
                "持倉大增 (追多)",
                "多頭大舉加碼，市場正在順勢往上走"
            )
        if "費率/持倉偏多" in r:
            r = r.replace(
                "費率/持倉偏多",
                "多頭人數和成本都偏多，短線偏向上漲，但要留意一旦反轉會殺多"
            )
        if "費率正/多頭平倉 (摸頭)" in r:
            r = r.replace(
                "費率正/多頭平倉 (摸頭)",
                "做多的人在高位慢慢出貨，還要付高費率持倉，這裡容易變成短線高點"
            )
        if "費率負/空頭平倉 (摸底)" in r:
            r = r.replace(
                "費率負/空頭平倉 (摸底)",
                "做空的人在低位回補了結，多頭還拿著負費率優勢，這裡容易變成短線低點"
            )

        # 3) 強勢嘎空 / 大漲描述：告訴使用者是「一路噴」的盤
        if "強勢嘎空 (24h漲" in r:
            # 例: "🔥 強勢嘎空 (24h漲 25.0%)" → "24h 上漲 25.0%，空單一路被軋，行情非常強勢"
            r = r.replace("🔥 強勢嘎空 (24h漲 ", "24h 上漲 ")
            if "%)" in r and "空單一路被軋" not in r:
                r = r.replace("%)", "%，空單一路被軋，行情非常強勢", 1)

        # 4) 其它較生硬的描述，盡量講成「誰在出貨 / 誰在吸籌」
        if "持倉大減 (多頭平倉" in r and "摸頭" in r:
            r = r.replace(
                "⛽ 持倉大減 (多頭平倉→摸頭)",
                "原本做多的人正在賣出離場，這裡屬於高位轉弱區，適合找空點"
            )
        if "持倉減 (偏空)" in r:
            r = r.replace(
                "持倉減 (偏空)",
                "合約部位在縮小，整體籌碼略偏空"
            )

        # 5) CVD / 大單確認：補上「大單實打實」的白話說明
        if " (CVD確認)" in r:
            r = r.replace(
                " (CVD確認)",
                " —— 大單實打實成交，並非虛假掛單，主力真的有在進出"
            )

        return r if r.strip() else "籌碼有異動"

    # 四象限：只推 4 星以上（3 星不推播）
    long_dip = [x for x in enriched_items if x.get("zone") == ZONE_DIP and _is_bull(x) and (x.get("stars") or 0) >= 4]
    long_break = [x for x in enriched_items if x.get("zone") == ZONE_BREAKOUT_LONG and _is_bull(x) and (x.get("stars") or 0) >= 4]
    short_top = [x for x in enriched_items if x.get("zone") == ZONE_TOP and not _is_bull(x) and (x.get("stars") or 0) >= 4]
    short_break = [x for x in enriched_items if x.get("zone") == ZONE_BREAKOUT_SHORT and not _is_bull(x) and (x.get("stars") or 0) >= 4]

    blocks = [
        ("🟢 *做多區*", [
            ("📌 抄底（跌深撿便宜）", long_dip),
            ("📌 追漲（順勢做多）", long_break),
        ]),
        ("🔴 *做空區*", [
            ("📌 摸頭（漲多放空）", short_top),
            ("📌 追跌（順勢做空）", short_break),
        ]),
    ]

    # 統計 S+/S/A 數量（依「不重複標的」計，避免同標的出現在多區塊時顯示多台列車）
    # 先套用 RSI 過濾，確保統計與顯示一致
    eligible_items_raw = long_dip + long_break + short_top + short_break
    eligible_items = [x for x in eligible_items_raw if _pass_rsi_filter(x, x.get("zone") or "")]
    eligible_by_sym = {str(x.get("symbol") or "").strip(): x for x in eligible_items if x.get("symbol")}
    eligible_unique = list(eligible_by_sym.values())
    count_diamond = sum(1 for x in eligible_unique if _is_diamond(x))
    count_s_plus = sum(1 for x in eligible_unique if _is_elite(x) and not _is_diamond(x))
    count_s = sum(1 for x in eligible_unique if (x.get("stars") or 0) == 5 and not _is_elite(x))
    count_a = sum(1 for x in eligible_unique if (x.get("stars") or 0) == 4)

    stats_parts = []
    if count_diamond > 0:
        stats_parts.append(f"💎{count_diamond}")
    if count_s_plus > 0:
        stats_parts.append(f"✈️{count_s_plus}")
    if count_s > 0:
        stats_parts.append(f"🚅{count_s}")
    if count_a > 0:
        stats_parts.append(f"👻{count_a}")
    stats_str = " ".join(stats_parts) or "😴 無訊號"

    # 全網共識與共振統計
    consensus_count = sum(1 for x in eligible_unique if x.get("is_global_consensus"))
    resonance_count = sum(1 for x in eligible_unique if x.get("has_5m_resonance"))
    badges = []
    if consensus_count > 0:
        badges.append(f"🌍全網共識{consensus_count}")
    if resonance_count > 0:
        badges.append(f"🔥5M共振{resonance_count}")
    consensus_badge = f" [{' | '.join(badges)}]" if badges else ""

    lines = []
    lines.append(f"🎯 *{stats_str}｜傑克持倉狙擊鏡*{consensus_badge}")
    lines.append(f"⚡ *15M 閃電監控* | 🕐 {datetime.now(TAIPEI_TZ).strftime('%m/%d %H:%M')} (台灣)")
    lines.append("━━━━━━━━━━━━━━")

    has_any = False
    push_count = 0      # 實際通過所有篩選、進入訊息的訊號數
    seen_syms = set()  # 同幣只顯示一次，避免 1000PEPE 等重複出現
    for section_title, subs in blocks:
        section_printed = False
        for sub_label, items in subs:
            if not items:
                continue
            items_sorted = sorted(items, key=lambda x: (-(x.get("stars") or 0), -(abs(x.get("oiChange30m") or 0))))
            # 暫存基準點：若本子區塊沒有任何項目通過 RSI/風報比篩選，則回滾標題
            sub_start_idx = len(lines)
            if not section_printed:
                lines.append("")
                lines.append(section_title)
            lines.append(sub_label)
            had_any_in_sub = False
            for x in items_sorted:
                sym = x.get("symbol", "")
                if sym and sym in seen_syms:
                    continue
                if sym:
                    seen_syms.add(sym)
                zone = x.get("zone")
                # RSI 過濾：追漲 RSI<45 / 追跌 RSI>55 → 動能不符，跳過不推
                if not _pass_rsi_filter(x, zone or ""):
                    _rsi_v = x.get("rsi")
                    logger.info(f"狙擊鏡跳過 {sym}: RSI={_rsi_v} 不符合 {zone} 動能門檻，過濾")
                    continue
                sym = x.get("symbol", "")
                stars = x.get("stars", 1)
                is_bull = _is_bull(x)
                dir_emoji = "🟢" if is_bull else "🔴"
                coin_url = f"https://www.coinglass.com/zh-TW/currencies/{sym}"
                # 分級顯示邏輯：鑽石共振＞頭等機艙＞穩健列車＞賭鬼
                is_diamond_sig = _is_diamond(x)
                is_elite_sig = _is_elite(x)
                is_consensus = bool(x.get("is_global_consensus"))
                has_resonance = bool(x.get("has_5m_resonance"))
                resonance_5m_raw = x.get("resonance_5m_raw")

                if resonance_5m_raw is False:
                    # 5M 動能已竭警示標籤
                    resonance_tag = " ⚠️5M竭"
                elif has_resonance:
                    resonance_tag = " 🔥"
                else:
                    resonance_tag = ""

                consensus_tag = " 🌍" if is_consensus else ""

                if is_elite_sig or is_diamond_sig:
                    tier_emoji = "✈️"
                    _elite_tag = " 🔥三重確認" if is_diamond_sig else ""
                    star_display = f"✈️【頭等機艙】高勝率 ⭐⭐⭐⭐⭐⭐{_elite_tag}{resonance_tag}{consensus_tag}"
                    x["tier"] = "elite"
                elif stars >= 5:
                    tier_emoji = "🚅"
                    star_display = f"🚅【穩健列車】標準倉 ⭐⭐⭐⭐⭐{resonance_tag}{consensus_tag}"
                    x["tier"] = "train"
                else:
                    tier_emoji = "👻"
                    star_display = f"👻【賭鬼樂透】高風報比💣{resonance_tag}{consensus_tag}"
                    x["tier"] = "gambler"

                # 策略與風控建議（策略前加分級 emoji）
                atr_val, current_price = x.get("atr"), x.get("current_price")
                is_high_vol = False
                vol_desc = ""
                if atr_val and current_price and (atr_val / current_price) * 100 > 2.0:
                    is_high_vol = True
                    vol_desc = " (波動大⚠️)"

                if is_diamond_sig:  # 頭等艙最高確信版（三重確認）
                    if is_high_vol:
                        strength, pos_rec = "頭等艙但波動大", "標準倉 5% (動態縮倉)"
                    else:
                        strength, pos_rec = "✈️ 頭等機艙 S+", "重倉 10% (三重確認)"
                elif is_elite_sig:  # 頭等艙標準版
                    if is_high_vol:
                        strength, pos_rec = "頭等艙但波動大", "標準倉 5% (已風控)"
                    else:
                        strength, pos_rec = "✈️ 頭等機艙 S+", "重倉 7% (信心足)"
                elif stars >= 5:  # S
                    if is_high_vol:
                        strength, pos_rec = "穩健但波動大", "減半倉 2.5% (防洗盤)"
                    else:
                        strength, pos_rec = "穩健列車 S", "標準倉 5%"
                else:  # A (賭鬼樂透)
                    strength = "賭鬼樂透 A"
                    if is_high_vol:
                        pos_rec = "蟻倉 1%"
                    else:
                        pos_rec = "試單 2.5%"
                strength = f"{tier_emoji} {strength}"

                # 計算 SL/TP（OI 起漲點結構防守：BingX K 線為主）
                cvd_div = "CVD背離" in (x.get("reason") or "")
                tech_exhausted = bool(x.get("energy_exhausted"))
                sl_val, tp1_val, tp2_val, r_tp1, r_tp2, tp1_label, tp2_label, sl_capped, energy_exhausted, tp1_real_str, tp1_real_note, tp1_atr_str, tp1_atr_note = calc_sl_tp(
                    x.get("atr"), x.get("current_price"), zone or ZONE_TOP, is_bull, stars, is_elite_sig,
                    x.get("vwap_2h"), x.get("ema20_close"), x.get("ub_value"), x.get("lb_value"),
                    cvd_divergence=(cvd_div or tech_exhausted),
                    recent_high_2h=x.get("recent_high_2h"),
                    recent_low_2h=x.get("recent_low_2h"),
                    last_kline_high_30m=x.get("last_kline_high_30m"),
                    last_kline_low_30m=x.get("last_kline_low_30m"),
                    fp_support=x.get("fp_support"),
                    fp_resistance=x.get("fp_resistance"),
                    fp_poc=x.get("fp_poc"),
                )
                # 將 SL/TP 價位與標籤存回，供 24h 出場追蹤
                x["sl_price_str"] = sl_val
                x["tp1_price_str"] = tp1_val
                x["tp1_label"] = tp1_label
                x["tp1_real_str"] = tp1_real_str
                x["tp1_real_note"] = tp1_real_note
                x["r_tp1"] = r_tp1
                # 推導 SL 來源（用於止損通知訊息精準描述）
                _is_long_for_sl = is_bull
                _fp_sup_stored = x.get("fp_support")
                _fp_sup_ok = (
                    _fp_sup_stored is not None
                    and isinstance(_fp_sup_stored, (int, float))
                    and _fp_sup_stored > 0
                    and ((_is_long_for_sl and _fp_sup_stored < (x.get("current_price") or 999999)) or
                         (not _is_long_for_sl and _fp_sup_stored > (x.get("current_price") or 0)))
                )
                if _fp_sup_ok:
                    # 根據關鍵位實際數據來源產生精準 SL 標籤
                    _fp_ds = x.get("fp_data_source") or ""
                    if "footprint_history" in _fp_ds:
                        x["sl_source"] = "腳步圖支撐"
                    elif "ob_depth_agg" in _fp_ds:
                        x["sl_source"] = "訂單簿支撐(聚合)"
                    elif "ob_depth_binance" in _fp_ds:
                        x["sl_source"] = "訂單簿支撐(Binance)"
                    elif "taker_concentration" in _fp_ds:
                        x["sl_source"] = "taker支撐(主動買入集中位)"
                    else:
                        x["sl_source"] = "訂單簿支撐"
                elif (x.get("recent_low_2h") if _is_long_for_sl else x.get("recent_high_2h")):
                    x["sl_source"] = "結構低點" if _is_long_for_sl else "結構高點"
                elif x.get("last_kline_low_30m" if _is_long_for_sl else "last_kline_high_30m"):
                    x["sl_source"] = "K線結構"
                else:
                    x["sl_source"] = "ATR動態止損"
                # 賭鬼虛擬 TP2 目標：僅用於績效評估與 TP2 命中統計，不改變實際出場邏輯
                x["tp2_price_str"] = tp2_val
                x["r_tp2"] = r_tp2
                # 風報比過低不推播：止盈 < 門檻 R 代表賠率差，寧可少出手保勝率
                if r_tp1 is not None and r_tp1 < MIN_TP1_R_FOR_PUSH:
                    logger.info(f"狙擊鏡跳過 {sym}: 止盈 風報比 {r_tp1}R < {MIN_TP1_R_FOR_PUSH}R，不推播")
                    continue

                had_any_in_sub = True
                has_any = True
                push_count = push_count + 1  # noqa: (defined below at init)
                # 標記：此標的是「實際有推播」的訊號，供後續冷卻/倉位追蹤使用
                x["selected_for_push"] = True
                price = x.get("current_price")
                if price is not None and isinstance(price, (int, float)):
                    price_str = f"{price:.4f}" if price < 10 else f"{price:.2f}"
                    if price < 0.01:
                        price_str = f"{price:.6f}"
                else:
                    price_str = "—"
                atr_for_log = x.get("atr")
                cap_note = " (SL已觸發10%上限限制)" if sl_capped else ""
                source = (x.get("source") or "").strip()
                data_src_warn = bool(x.get("data_source_warning"))
                if data_src_warn:
                    logger.warning(
                        f"[推播] {sym} 現價={price_str} ATR={atr_for_log} 止損={sl_val} 止盈={tp1_val}{cap_note} | 數據源={source} (數據源偏差預警：BingX 多次失敗改用 CoinGlass)"
                    )
                else:
                    logger.info(
                        f"[推播] {sym} 現價={price_str} ATR={atr_for_log} 止損={sl_val} 止盈={tp1_val}{cap_note} | 數據源={source or 'BingX'}"
                    )

                # 1. Header: 標的＋換方向＋波動提示（精簡，不顯示數據來源與分級標籤）
                sym_base = sym.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
                flip_tag = " 🔄換方向" if x.get("direction_flip") else ""
                lines.append(f"{dir_emoji} `{sym_base}`{flip_tag}")
                # 2. 策略（白話，含分級 emoji）
                lines.append(f"🎲 策略：{strength}")
                flip = x.get("direction_flip")
                if flip:
                    if "多轉空" in flip:
                        cta = "請立即平倉多單 ➔ 反手做空"
                    elif "空轉多" in flip:
                        cta = "請立即平倉空單 ➔ 反手做多"
                    else:
                        cta = flip
                    lines.append(f"🚨 *【訊號反轉】* {cta}")
                # 3. Funding Rate (with interpretation for beginners)
                fr = x.get("funding_rate")
                if fr is not None and isinstance(fr, (int, float)):
                    fr_pct = fr * 100
                    # 費率顯示與極端標註一致化：
                    # - 絕對值 < FUNDING_EXTREME(0.03%)：視為中性
                    # - 介於 EXTREME 與 1%：偏正/偏負（殺多/嘎空）
                    # - 絕對值 > 1%：車重/軋空（極端擁擠）
                    if fr >= 0.01:
                        fr_desc = "🔥 車重 (多頭擁擠)"
                    elif fr <= -0.01:
                        fr_desc = "❄️ 軋空 (空頭擁擠)"
                    elif fr > FUNDING_EXTREME:
                        fr_desc = "⛽ 殺多(費率偏正)"
                    elif fr < -FUNDING_EXTREME:
                        fr_desc = "🔥 嘎空(費率偏負)"
                    else:
                        fr_desc = "⚖️ 中性"
                    lines.append(f"💸 費率：`{fr_pct:.4f}%` {fr_desc}")
                # 4. 15m 觸發數值（CoinGlass 四象限座標：持倉變化 vs 價格變化）
                p30 = x.get("priceChange30m")
                oi30 = x.get("oiChange30m")
                try:
                    p30_val = float(p30) if isinstance(p30, (int, float, str)) and p30 is not None else None
                except (TypeError, ValueError):
                    p30_val = None
                try:
                    oi30_val = float(oi30) if isinstance(oi30, (int, float, str)) and oi30 is not None else None
                except (TypeError, ValueError):
                    oi30_val = None
                if oi30_val is not None and p30_val is not None:
                    _oi_str = f"{oi30_val:+.2f}%"
                    _p30_str = f"{p30_val:+.2f}%"
                    # 四象限標籤（對應 CoinGlass Visual Screener 象限名稱）
                    _cat = x.get("category", "")
                    _quadrant_map = {
                        "long_open":   "🟢 多方開倉",
                        "short_close": "🟢 空方平倉",
                        "short_open":  "🔴 空方開倉",
                        "long_close":  "🔴 多方平倉",
                    }
                    _quadrant_label = _quadrant_map.get(_cat, "")
                    _quadrant_tag = f" ({_quadrant_label})" if _quadrant_label else ""
                    lines.append(f"📊 15m{_quadrant_tag}：持倉 `{_oi_str}` 價格 `{_p30_str}`")

                # 邏輯（持倉變化白話）+ 訂單簿（白話）
                reason = x.get("reason", "籌碼異動")
                if flip:
                    reason = (reason or "") + " 趨勢已改變，舊單失效。"
                # 籌碼背離預警：OI 漲但價格跌幅收斂 → 底部吸籌；OI 跌但價格漲幅收斂 → 頂部出貨
                if p30_val is not None and oi30_val is not None:
                    if oi30_val > 3.0 and p30_val < 0:
                        reason = (reason or "") + " 🕵️ 主力底部吸籌"
                    elif oi30_val < -3.0 and p30_val > 0:
                        reason = (reason or "") + " ⚠️ 主力高位出貨"
                    elif oi30_val > 0 and p30_val < 0 and abs(p30_val) < abs(oi30_val):
                        reason = (reason or "") + " 🔍 底部吸籌跡象"
                    elif oi30_val < 0 and p30_val > 0 and abs(p30_val) < abs(oi30_val):
                        reason = (reason or "") + " 🔍 頂部出貨跡象"
                cvd_in_reason = " (CVD確認)" in (reason or "")
                if cvd_in_reason:
                    reason_display = (reason or "").replace(" (CVD確認)", "").strip()
                else:
                    reason_display = reason or "籌碼異動"
                lines.append(f"💡 邏輯：{_reason_plain(reason_display)}")
                lines.append(f"📋 訂單簿：{'有大單在跟，可參考' if cvd_in_reason else '這檔量太小，查不到大單'}")
                # 5. Price targets (separate lines, card-style) + 24h 必顯示
                p24 = x.get("priceChange24h")
                if p24 is not None and isinstance(p24, (int, float)):
                    p24_emoji = "📈" if p24 > 0 else "📉"
                    p24_str = f" (24h: {p24:+.2f}% {p24_emoji})"
                else:
                    p24_str = " (24h: -)"
                _price_display = f"`{price_str}`" if price_str != "—" else "暫無數據"
                lines.append(f"📍 現價：{_price_display}{p24_str}")
                # 主力均價 + EMA20 均線參考（兩行都顯示）
                vwap_ref = x.get("vwap_2h")
                ema_ref = x.get("ema20_close")
                if vwap_ref is not None and isinstance(vwap_ref, (int, float)):
                    lines.append(f"📍 主力均價(成本)：`{vwap_ref:,.4f}`")
                else:
                    lines.append("📍 主力均價(成本)：暫無數據")
                if ema_ref is not None and isinstance(ema_ref, (int, float)):
                    lines.append(f"📍 均線參考(EMA20)：`{ema_ref:,.4f}`")
                else:
                    lines.append("📍 均線參考(EMA20)：暫無數據")
                # ── 多時框能量條（顯示在止損前，有共振時才顯示）──────────────
                _energy_score = 0
                if has_resonance:
                    _energy_score += 2
                if is_consensus:
                    _energy_score += 2
                if x.get("has_5m_rsi_extreme"):
                    _energy_score += 1
                if x.get("has_vol_spike"):
                    _energy_score += 1
                if x.get("whale_sync"):
                    _energy_score += 1
                # 訂單流強度加分（flow_score 3-4 = +1）
                _fs = x.get("flow_score") or 0
                if _fs >= 3:
                    _energy_score += 1
                # OI 加速加分
                if x.get("oi_acceleration") and x.get("oi_trend") in ("building", "declining"):
                    _energy_score += 1
                _max_energy = 9  # 擴展至 9 分滿格（新增兩個維度）
                _energy_score = min(_energy_score, _max_energy)
                if _energy_score >= 2:  # 至少兩項共振才顯示能量條
                    _filled = "🔋" * _energy_score
                    _empty = "○" * (_max_energy - _energy_score)
                    _energy_label = (
                        "滿能量" if _energy_score >= 8 else
                        "高能量" if _energy_score >= 5 else
                        "中能量"
                    )
                    lines.append(f"⚡ 能量：{_filled}{_empty} {_energy_score}/{_max_energy} ({_energy_label})")

                # 止損與止盈顯示（含距止損百分比）
                t1_note = x.get("tp1_real_note") or x.get("tp1_label") or "ATR"
                _sl_dist_pct: Optional[float] = None
                _sl_dist_str = ""
                try:
                    _sl_price_f = float(sl_val.replace(",", "")) if sl_val and sl_val != "-" else None
                    _cur_p = x.get("current_price")
                    if _sl_price_f and _cur_p and float(_cur_p) > 0:
                        _sl_dist_pct = abs(float(_cur_p) - _sl_price_f) / float(_cur_p) * 100
                        _sl_dist_str = f" 距現價 `{_sl_dist_pct:.1f}%`"
                except (TypeError, ValueError):
                    pass
                # ── 取得腳步圖數據源（用於白話說明）─────────────────────
                _fp_ds = ""
                _fp_raw = x.get("fp_support") or x.get("fp_resistance")
                if _fp_raw:
                    # 從 all_top 中取腳步圖結構（已在 enrichment 存入）
                    _fp_ds = "footprint_history"
                _x_sl_src = x.get("sl_source") or "ATR動態止損"
                _sl_white = sl_plain_desc(_x_sl_src, is_bull, _fp_ds)
                _tp_white = tp_plain_desc(t1_note, is_bull, _fp_ds)

                # 止損/止盈 無數據時顯示「暫無數據」取代「-」
                _sl_display = f"`{sl_val}`" if sl_val and sl_val not in ("-", "—", "") else "暫無數據"
                _tp1_display = f"`{tp1_val}`" if tp1_val and tp1_val not in ("-", "—", "") else "暫無數據"

                _cap_tag = " 觸發10%上限" if sl_capped else ""
                # TP2 共用邏輯（列車/賭鬼均顯示）
                _tp2_str = x.get("tp2_price_str") or "-"
                _r2 = x.get("r_tp2")
                _r2_val = None
                if _tp2_str != "-" and _r2 is not None:
                    try:
                        _r2_val = float(_r2)
                    except (TypeError, ValueError):
                        _r2_val = None

                if stars >= 5:
                    # 列車：若 SL 距現價 >5%，標注「深空防護」
                    _sl_deep_note = ""
                    if _sl_dist_pct is not None and _sl_dist_pct > 5.0:
                        _sl_deep_note = " 🔐(深空防護)"
                    lines.append(f"🛑 止損：{_sl_display}{_sl_dist_str}{_sl_deep_note}{_cap_tag}")
                    r1 = f" ({r_tp1}R)" if r_tp1 is not None else ""
                    # 避免 (t1_note) 與 r1 重複（如 t1_note="1.2R" 且 r1="(1.2R)"）
                    _note_is_r = (r_tp1 is not None and t1_note in (f"{r_tp1}R", f"{r_tp1:.1f}R", f"{r_tp1:.2f}R"))
                    if t1_note and not _note_is_r:
                        lines.append(f"✅ TP1(60%)：{_tp1_display} ({t1_note}){r1}")
                    else:
                        lines.append(f"✅ TP1(60%)：{_tp1_display}{r1}")
                    if _r2_val is not None:
                        lines.append(f"🎯 TP2 理論目標：`{_tp2_str}` (~{_r2_val:.1f}R)")
                else:
                    # 賭鬼：TP1（落袋60%）+ TP2 理論目標
                    lines.append(f"🛑 止損：{_sl_display}{_sl_dist_str}{_cap_tag}")
                    r1 = f" ({r_tp1}R)" if r_tp1 is not None else ""
                    lines.append(f"✅ TP1(落袋60%)：{_tp1_display}{r1}")
                    if _r2_val is not None:
                        lines.append(f"🎯 TP2 理論目標：`{_tp2_str}` (~{_r2_val:.1f}R)")

                # ── 📊 訂單流分析（主動買賣比 + 淨倉位 + 腳步圖關鍵位）─────────
                _flow_score = x.get("flow_score") or 0
                _taker_r = x.get("taker_ratio")
                _net_pd = x.get("net_pos_delta")
                _fp_poc_val = x.get("fp_poc")
                _fp_sup_val = x.get("fp_support")
                _fp_res_val = x.get("fp_resistance")

                _flow_lines = []
                _oi_trend_msg = x.get("oi_trend")
                _oi_accel = x.get("oi_acceleration")
                _oi_1h = x.get("oi_change_1h_pct")
                _oi_4h = x.get("oi_change_4h_pct")
                _top_ls = x.get("top_ls_ratio")

                # 主動買賣比
                if _taker_r is not None:
                    _buy_dominant = (_taker_r > 55 and is_bull) or (_taker_r < 45 and not is_bull)
                    _taker_icon = "🟢" if _buy_dominant else ("🔴" if ((_taker_r < 45 and is_bull) or (_taker_r > 55 and not is_bull)) else "🟡")
                    _taker_desc = (
                        f"買盤主導 {_taker_r:.0f}%" if _taker_r >= 55 else
                        f"賣盤主導 {100-_taker_r:.0f}%" if _taker_r <= 45 else
                        f"買賣均衡 {_taker_r:.0f}%"
                    )
                    _flow_lines.append(f"  {_taker_icon} 主動買賣：{_taker_desc}")
                # 淨多倉位
                if _net_pd is not None:
                    _net_dir = "增加" if _net_pd > 0.05 else ("減少" if _net_pd < -0.05 else "持平")
                    _net_icon = "🟢" if (_net_pd > 0.05 and is_bull) or (_net_pd < -0.05 and not is_bull) else ("🔴" if (_net_pd > 0.05 and not is_bull) or (_net_pd < -0.05 and is_bull) else "🟡")
                    _flow_lines.append(f"  {_net_icon} 淨多倉位：{_net_dir} ({_net_pd:+.2f})")
                # OI 趨勢
                if _oi_trend_msg:
                    _oi_trend_map = {
                        "building": ("🟢", "加速建倉"),
                        "declining": ("🔴", "倉位縮減"),
                        "flat": ("🟡", "倉位持平"),
                        "reversing": ("🟠", "方向轉換中"),
                    }
                    _oi_icon, _oi_label = _oi_trend_map.get(_oi_trend_msg, ("⬜", _oi_trend_msg))
                    _oi_accel_tag = " ⚡加速" if _oi_accel else ""
                    # 一致性判斷
                    _oi_consistent = (
                        (_oi_trend_msg == "building" and is_bull) or
                        (_oi_trend_msg == "declining" and not is_bull)
                    )
                    _oi_consistent_icon = "🟢" if _oi_consistent else ("🔴" if not _oi_consistent and _oi_trend_msg not in ("flat","reversing") else "🟡")
                    _flow_lines.append(f"  {_oi_consistent_icon} OI 趨勢：{_oi_label}{_oi_accel_tag}")
                    # 多時間框架 OI 情緒雷達：判斷是剛啟動還是已經拉升多小時
                    _oi_parts: list[str] = []
                    if isinstance(_oi_1h, (int, float)):
                        _oi_parts.append(f"1h `{_oi_1h:+.2f}%`")
                    if isinstance(_oi_4h, (int, float)):
                        _oi_parts.append(f"4h `{_oi_4h:+.2f}%`")
                    if _oi_parts:
                        _flow_lines.append(f"  🔎 OI 風險雷達：{' / '.join(_oi_parts)}")
                # 大戶帳戶多空比
                if _top_ls is not None:
                    _top_ls_icon = "🟢" if ((_top_ls > 1.1 and is_bull) or (_top_ls < 0.9 and not is_bull)) else ("🔴" if ((_top_ls < 0.9 and is_bull) or (_top_ls > 1.1 and not is_bull)) else "🟡")
                    _top_ls_desc = f"大戶偏多({_top_ls:.2f})" if _top_ls > 1.05 else (f"大戶偏空({_top_ls:.2f})" if _top_ls < 0.95 else f"大戶中性({_top_ls:.2f})")
                    _flow_lines.append(f"  {_top_ls_icon} 大戶帳戶：{_top_ls_desc}")
                # 成交密集點（來源動態標示）
                if _fp_poc_val is not None:
                    _fp_fmt = f"{_fp_poc_val:.6g}"
                    _fp_ds_label = x.get("fp_data_source") or ""
                    if "footprint_history" in _fp_ds_label:
                        _poc_label = "腳步圖POC"
                    elif "taker_concentration" in _fp_ds_label:
                        _poc_label = "taker主動買入密集區"
                    elif "ob_depth" in _fp_ds_label:
                        _poc_label = "訂單簿掛單密集區"
                    else:
                        _poc_label = "成交密集區"
                    _flow_lines.append(f"  📍 {_poc_label}：`{_fp_fmt}`")

                if _flow_lines:
                    _fs_label = "強力確認" if _flow_score >= 3 else ("中度確認" if _flow_score >= 2 else ("中性" if _flow_score >= 0 else "背離警示"))
                    _fs_bars = "█" * _flow_score + "░" * (4 - _flow_score)
                    lines.append(f"📊 *訂單流* [{_fs_bars}] {_fs_label}")
                    lines.extend(_flow_lines)

                # 6. 進階情報警示（爆量 / 爆倉熱力圖 / 掛單牆 / 主力同步）
                # ── ⚡ 爆量啟動 ────────────────────────────────────────────
                _vsr = x.get("vol_spike_ratio")
                if isinstance(_vsr, (int, float)) and _vsr >= 2.0:
                    lines.append(f"⚡ *爆量啟動* 成交量 `{_vsr:.1f}×` 均值（量價配合，信號可信度↑）")
                elif isinstance(_vsr, (int, float)) and _vsr >= 1.5:
                    lines.append(f"📊 成交量 `{_vsr:.1f}×` 均值（溫和放量）")
                # ── 🔥 爆倉熱力圖 ──────────────────────────────────────────
                _liq = x.get("liq_nearby")
                if isinstance(_liq, dict) and _liq.get("pct") is not None:
                    _liq_usd_m = (_liq.get("total_usd") or 0) / 1e6
                    lines.append(
                        f"🔥 {_liq.get('label','')} "
                        f"| 規模 `${_liq_usd_m:.1f}M` 距現價 `{_liq.get('pct',0):.2f}%`"
                    )
                # ── 🧱 大額掛單牆 ──────────────────────────────────────────
                _wall = x.get("ob_wall")
                if isinstance(_wall, dict) and _wall.get("wall_usd"):
                    lines.append(f"{_wall.get('label','')}")
                # ── 🐋 主力同步建倉 ────────────────────────────────────────
                if x.get("whale_sync"):
                    _wi = x.get("whale_index")
                    if isinstance(_wi, (int, float)):
                        # 星星等級：<50=1星 50-80=2星 80-120=3星 120-160=4星 ≥160=5星
                        _wi_stars = (
                            "⭐" if _wi < 50 else
                            "⭐⭐" if _wi < 80 else
                            "⭐⭐⭐" if _wi < 120 else
                            "⭐⭐⭐⭐" if _wi < 160 else
                            "⭐⭐⭐⭐⭐"
                        )
                        _wi_level = (
                            "輕微跟進" if _wi < 50 else
                            "中等跟進" if _wi < 80 else
                            "明顯跟進" if _wi < 120 else
                            "強力跟進" if _wi < 160 else
                            "極強跟進"
                        )
                        lines.append(f"🐋 *主力同步建倉* {_wi_stars} `{_wi:.0f}` {_wi_level}（大戶與訊號方向一致）")
                    else:
                        lines.append("🐋 *主力同步建倉*（大戶與訊號方向一致）")
                # ── 累積費率擠壓標籤 ──────────────────────────────────────
                _accum_lbl = x.get("accum_fr_label")
                if _accum_lbl:
                    lines.append(_accum_lbl)
                # ── 🎯 期權最大痛點（BTC/ETH 時額外顯示）─────────────────
                _sym_base = x.get("symbol", "").replace("USDT", "").upper()
                if _sym_base in ("BTC", "ETH"):
                    try:
                        _mp = fetch_options_max_pain(_sym_base)
                        if _mp.get("max_pain_price"):
                            _mp_price = _mp["max_pain_price"]
                            _mp_cur = x.get("current_price") or 0
                            _mp_dist = abs(_mp_price - _mp_cur) / _mp_cur * 100 if _mp_cur > 0 else 0
                            _mp_dir = "上方" if _mp_price > _mp_cur else "下方"
                            lines.append(f"🎯 期權最大痛點 `{_mp_price:,.0f}` ({_mp_dir} `{_mp_dist:.1f}%`)")
                    except Exception:
                        pass
                lines.append("")
            # 若本子區塊無任何項目通過 RSI/風報比篩選，回滾掉暫存的標題與子標題
            if had_any_in_sub:
                section_printed = True
            else:
                del lines[sub_start_idx:]

    return "\n".join(lines), has_any, push_count


def build_report_message(top_long_open: List, top_long_close: List, top_short_open: List, top_short_close: List, processed_count: int = 0, oi_success_count: int = 0) -> str:
    """組合推播文字（15m 高頻版：價格與持倉皆為 15 分鐘）"""
    lines = []
    lines.append("💰 *【傑克短線持倉異動排行榜】(15分)*")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")

    def fmt(num):
        if num is None or (isinstance(num, float) and (num != num)):
            return "0.00%"
        return f"{'+' if num >= 0 else ''}{num:.2f}%"

    lines.append("📈 *開倉*（新建立倉位）")
    lines.append("")
    lines.append("  *多方開倉 TOP 3*（做多方向，看漲）")
    if not top_long_open:
        lines.append("    無明顯多方開倉標的")
    else:
        for idx, item in enumerate(top_long_open):
            price_change = fmt(item.get("priceChange30m", 0))
            oi_change = fmt(item.get("oiChange30m", 0))
            lines.append(f"    {idx + 1}) *{item['symbol']}*｜價格 {price_change}｜持倉 {oi_change}")
    lines.append("")
    lines.append("  *空方開倉 TOP 3*（做空方向，看跌）")
    if not top_short_open:
        lines.append("    無明顯空方開倉標的")
    else:
        for idx, item in enumerate(top_short_open):
            price_change = fmt(item.get("priceChange30m", 0))
            oi_change = fmt(item.get("oiChange30m", 0))
            lines.append(f"    {idx + 1}) *{item['symbol']}*｜價格 {price_change}｜持倉 {oi_change}")
    lines.append("")
    lines.append("📉 *平倉*（結束既有倉位）")
    lines.append("")
    lines.append("  *多方平倉 TOP 3*（做多倉位減碼）")
    if not top_long_close:
        lines.append("    無明顯多方平倉標的")
    else:
        for idx, item in enumerate(top_long_close):
            price_change = fmt(item.get("priceChange30m", 0))
            oi_change = fmt(item.get("oiChange30m", 0))
            lines.append(f"    {idx + 1}) *{item['symbol']}*｜價格 {price_change}｜持倉 {oi_change}")
    lines.append("")
    lines.append("  *空方平倉 TOP 3*（做空倉位減碼）")
    if not top_short_close:
        lines.append("    無明顯空方平倉標的")
    else:
        for idx, item in enumerate(top_short_close):
            price_change = fmt(item.get("priceChange30m", 0))
            oi_change = fmt(item.get("oiChange30m", 0))
            lines.append(f"    {idx + 1}) *{item['symbol']}*｜價格 {price_change}｜持倉 {oi_change}")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("💡 *【換位思考主力動機】*")
    lines.append("")
    lines.append("請先判斷 *15分K價格走勢趨勢* 去換位思考主力動機")
    lines.append("")
    lines.append("📈 *開倉*：多方開倉＝看漲做多；空方開倉＝看跌做空。")
    lines.append("📉 *平倉*：多方平倉＝做多減碼；空方平倉＝做空減碼。（停利或停損）")
    return "\n".join(lines)


def process_single_symbol(coin: Dict) -> Optional[Dict]:
    """處理單個幣種（用於並行處理，使用原本的邏輯）"""
    symbol = normalize_symbol(coin)
    if not symbol:
        return None
    
    try:
        price_change_30m = extract_price_change_30m(coin)
        oi_change_30m = fetch_oi_change_30m(symbol)
        if oi_change_30m is None:
            return {'status': 'oi_failed', 'symbol': symbol}
        category = None
        # 價格 / 持倉變化 → 四象限分類（名詞依交易實務修正）
        # 價格漲 + OI 漲 = long_open  (多頭開倉)
        # 價格漲 + OI 跌 = short_close (空軍被軋，空頭平倉)
        # 價格跌 + OI 漲 = short_open (空頭開倉)
        # 價格跌 + OI 跌 = long_close  (多軍斷頭，多頭平倉)
        if price_change_30m > 0:
            if oi_change_30m > 0:
                category = 'long_open'
            elif oi_change_30m < 0:
                category = 'short_close'  # 價漲 + OI 跌 = 空軍被軋平倉
        elif price_change_30m < 0:
            if oi_change_30m > 0:
                category = 'short_open'
            elif oi_change_30m < 0:
                category = 'long_close'   # 價跌 + OI 跌 = 多軍斷頭平倉
        if category:
            price_change_24h = extract_price_change_24h(coin)
            return {
                'status': 'success',
                'category': category,
                'symbol': symbol,
                'priceChange30m': price_change_30m,
                'oiChange30m': oi_change_30m,
                'priceChange24h': price_change_24h,
            }
        else:
            return {'status': 'no_category', 'symbol': symbol}
            
    except Exception as e:
        logger.error(f"處理 {symbol} 時發生錯誤: {str(e)}")
        return {'status': 'error', 'symbol': symbol, 'error': str(e)}


def fetch_coinglass_coins_markets() -> List[Dict]:
    """【標準版 CoinGlass-First】拉取 CoinGlass 全市場幣種快照。

    優先呼叫 /api/futures/coins-markets（含成交量、各時間框價格變化）；
    若該端點不可用，回退至 /api/futures/coins-price-change。

    回傳統一格式列表，每個 item 保證含：
        symbol                 : str  (base，如 "BTC"、"1000PEPE")
        coin                   : str  (同 symbol，供舊版 normalize_symbol 讀取)
        price_change_percent_30m: float|None  (15m / 最近可用的短週期漲跌幅)
        price_change_percent_24h: float|None
        _cg_volume_usd         : float|None  (24h 成交額，USD，供成交量預篩使用)
    """
    if not CG_API_KEY:
        return []

    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}

    # ── 嘗試 coins-markets（標準版完整端點，帶分頁抓取全市場）───────────────
    def _parse_cg_item(item: Dict) -> Optional[Dict]:
        """解析單一 CoinGlass coins-markets item 為統一格式，失敗回傳 None。"""
        if not isinstance(item, dict):
            return None
        sym_raw = (
            item.get("symbol") or item.get("coin") or
            item.get("coinSymbol") or item.get("base") or ""
        )
        sym = str(sym_raw).replace("USDT", "").replace("USDT-PERP", "") \
            .replace("-", "").replace("_", "").strip().upper()
        if not sym or len(sym) > 14:
            return None
        p15 = (
            item.get("priceChangePercent15m") or
            item.get("price_change_percent_15m") or
            item.get("priceChangePercent30m") or
            item.get("price_change_percent_30m") or
            item.get("change15m") or item.get("change_15m")
        )
        p24 = (
            item.get("priceChangePercent24h") or
            item.get("price_change_percent_24h") or
            item.get("priceChange24h") or item.get("change_24h")
        )
        vol = (
            item.get("volUsd24h") or item.get("volumeUsd24h") or
            item.get("volume24h") or item.get("vol24h") or
            item.get("quoteVolume24h") or item.get("usdtVolume")
        )
        try:
            p15 = float(p15) if p15 is not None else None
        except (TypeError, ValueError):
            p15 = None
        try:
            p24 = float(p24) if p24 is not None else None
        except (TypeError, ValueError):
            p24 = None
        try:
            vol = float(vol) if vol is not None else None
        except (TypeError, ValueError):
            vol = None
        return {
            "symbol": sym,
            "coin": sym,
            "price_change_percent_30m": p15,
            "price_change_percent_24h": p24,
            "_cg_volume_usd": vol,
            "_raw_cg": item,
        }

    def _fetch_one_coins_markets_page(sort_field: str = "", sort_type: str = "0",
                                       seen: Optional[set] = None) -> List[Dict]:
        """抓取一頁 coins-markets，回傳解析後的列表（去重用 seen set）。"""
        if seen is None:
            seen = set()
        out_page: List[Dict] = []
        try:
            _respect_coinglass_rate_limit()
            params: Dict = {"pageSize": 100}
            if sort_field:
                params["sortField"] = sort_field
                params["sortType"] = sort_type  # "0"=降序 "1"=升序
            r = requests.get(
                f"{CG_API_BASE}/api/futures/coins-markets",
                headers=headers,
                params=params,
                timeout=15,
            )
            if r.status_code != 200:
                logger.debug(f"[CoinGlass多排序] sortField={sort_field} HTTP {r.status_code}")
                return out_page
            j = r.json()
            if j.get("code") not in (0, "0", 200, "200", None):
                return out_page
            raw = j.get("data", j.get("list", j if isinstance(j, list) else []))
            if not isinstance(raw, list):
                return out_page
            added = 0
            for item in raw:
                parsed = _parse_cg_item(item)
                if parsed and parsed["symbol"] not in seen:
                    seen.add(parsed["symbol"])
                    out_page.append(parsed)
                    added += 1
            logger.info(f"[CoinGlass多排序] sortField={sort_field or '(default)'} 新增 {added} 個 / 本次共 {len(raw)} 筆")
        except Exception as e:
            logger.debug(f"[CoinGlass多排序] sortField={sort_field} 異常: {e}")
        return out_page

    def _try_coins_markets() -> List[Dict]:
        """抓取 CoinGlass coins-markets Top-100（按 OI 排序）。
        CoinGlass 的 pageNum / sortField 均被 API 忽略，多次呼叫回傳相同 100 筆，
        故僅打一次預設排序，節省時間與 API 配額。
        """
        seen_syms: set = set()
        out = _fetch_one_coins_markets_page("", "0", seen_syms)
        logger.info(f"[CoinGlass-First] coins-markets 取得 {len(out)} 個幣種（OI 排序 Top-100）")
        return out

    # ── 嘗試 coins-price-change（備援端點）──────────────────────────────────
    def _try_coins_price_change() -> List[Dict]:
        try:
            r = requests.get(
                f"{CG_API_BASE}/api/futures/coins-price-change",
                headers=headers, timeout=12
            )
            if r.status_code != 200:
                return []
            j = r.json()
            if j.get("code") not in (0, "0", 200, "200", None):
                return []
            raw = j.get("data", j.get("list", j if isinstance(j, list) else []))
            if not isinstance(raw, list) or not raw:
                return []
            out = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                sym_raw = (
                    item.get("symbol") or item.get("coin") or
                    item.get("coinSymbol") or ""
                )
                sym = str(sym_raw).replace("USDT", "").replace("USDT-PERP", "") \
                    .replace("-", "").replace("_", "").strip().upper()
                if not sym or len(sym) > 14:
                    continue
                p15 = (
                    item.get("priceChangePercent15m") or
                    item.get("price_change_percent_15m") or
                    item.get("priceChangePercent30m") or
                    item.get("price_change_percent_30m") or
                    item.get("priceChangePercent1h") or
                    item.get("price_change_percent_1h")
                )
                p24 = (
                    item.get("priceChangePercent24h") or
                    item.get("price_change_percent_24h") or
                    item.get("priceChange24h")
                )
                vol = (
                    item.get("volUsd24h") or item.get("volumeUsd24h") or
                    item.get("volume24h") or item.get("vol24h")
                )
                try:
                    p15 = float(p15) if p15 is not None else None
                except (TypeError, ValueError):
                    p15 = None
                try:
                    p24 = float(p24) if p24 is not None else None
                except (TypeError, ValueError):
                    p24 = None
                try:
                    vol = float(vol) if vol is not None else None
                except (TypeError, ValueError):
                    vol = None
                out.append({
                    "symbol": sym,
                    "coin": sym,
                    "price_change_percent_30m": p15,
                    "price_change_percent_24h": p24,
                    "_cg_volume_usd": vol,
                })
            return out
        except Exception as e:
            logger.warning(f"coins-price-change 異常: {e}")
            return []

    # ── Step 1：coins-markets（top 100，帶完整 OI/Price 數據）──────────────
    result_markets = _try_coins_markets()

    # ── Step 2：coins-price-change（全量幣種價格資料，診斷 + 補充）──────────
    result_pc = _try_coins_price_change()
    if result_pc:
        _pc_sample_keys = list(result_pc[0].keys()) if isinstance(result_pc[0], dict) else []
        logger.info(f"[CoinGlass-First] coins-price-change 回傳 {len(result_pc)} 個幣種 | 首筆欄位={_pc_sample_keys}")
    else:
        logger.warning("[CoinGlass-First] coins-price-change 無回傳數據")

    # ── Step 3：supported-coins（全量幣種名單，作為最終補充）───────────────
    # 用 supported-coins 確保掃描涵蓋所有合約幣種（即使 price-change 也有上限）
    def _try_supported_coins_stubs(seen: set) -> List[Dict]:
        """從 supported-coins 取得完整幣種名單，為未覆蓋幣種建立最小存根（stub）。
        stubs 沒有 price_change 數據，會被標記 _stub=True，
        下游 price filter 會對這類幣「放行進 OI 檢查」而非直接拋棄。"""
        try:
            _respect_coinglass_rate_limit()
            r = requests.get(
                f"{CG_API_BASE}{CG_EP['supported_coins']}",
                headers={"CG-API-KEY": CG_API_KEY, "accept": "application/json"},
                timeout=10
            )
            if r.status_code != 200:
                logger.debug(f"[supported-coins] HTTP {r.status_code}")
                return []
            j = r.json()
            coin_list = j.get("data", [])
            if not isinstance(coin_list, list) or not coin_list:
                return []
            stubs = []
            for c in coin_list:
                name = str(c).strip().upper() if isinstance(c, str) else (
                    str(c.get("symbol") or c.get("coin") or "").strip().upper()
                    if isinstance(c, dict) else ""
                )
                if name and len(name) <= 14 and name not in seen:
                    seen.add(name)
                    stubs.append({
                        "symbol": name, "coin": name,
                        "price_change_percent_30m": None,
                        "price_change_percent_24h": None,
                        "_cg_volume_usd": None,
                        "_stub": True,  # 標記為 stub：price filter 放行，直接進 OI 檢查
                    })
            logger.info(f"[CoinGlass-First] supported-coins 補充 {len(stubs)} 個 stub 幣種（無 price 數據，直接進 OI 掃描）")
            return stubs
        except Exception as e:
            logger.debug(f"[supported-coins] 異常: {e}")
            return []

    # ── Step 4：三路合併 ──────────────────────────────────────────────────
    seen_syms: set = set()
    result: List[Dict] = []

    for item in result_markets:
        sym = item.get("symbol", "")
        if sym and sym not in seen_syms:
            seen_syms.add(sym)
            result.append(item)

    pc_added = 0
    for item in result_pc:
        sym = item.get("symbol", "")
        if sym and sym not in seen_syms:
            seen_syms.add(sym)
            result.append(item)
            pc_added += 1

    stub_list = _try_supported_coins_stubs(seen_syms)
    result.extend(stub_list)

    logger.info(
        f"[CoinGlass-First] 三路合併完成 → 總計 {len(result)} 個唯一幣種"
        f"（markets={len(result_markets)} | pc補充={pc_added} | supported-coins stub={len(stub_list)}）"
    )
    return result


def fetch_position_change():
    """【CoinGlass-First 架構】15M 高頻持倉狙擊主流程。
    標準版重構：以 CoinGlass 全市場數據為主軸掃描；BingX 僅在最終推播前做標的支援驗證。
    """
    global _coinglass_oi_first_failure_logged
    _coinglass_oi_first_failure_logged = False

    # 熔斷器狀態報告（每輪開始時印出，便於 GitHub Actions 日誌診斷）
    _cb_cnt = _circuit_breaker.get("consecutive_429", 0)
    if _cb_is_tripped():
        logger.warning(f"[熔斷器🚨] 本輪以 MAX_WORKERS=1 單執行緒模式啟動（連續429={_cb_cnt}）")
    elif _cb_is_warned():
        logger.warning(f"[熔斷器⚠️] 本輪以 MAX_WORKERS=2 警戒模式啟動（連續429={_cb_cnt}）")
    else:
        logger.info(f"[熔斷器✅] 正常模式（連續429={_cb_cnt}）")

    logger.info("🚀 傑克船長 2.0 閃電版正式啟動 | 精品模式：15m山寨幣優化門檻 | 頻率：15M")
    logger.info("【巨鯨高效漏斗】開始執行 15M 持倉狙擊掃描...")

    # ════════════════════════════════════════════════════════
    # 漏斗 Step 0：預先抓取 BingX 合約對照表（僅供 K 線查詢，不做過濾）
    # CoinGlass 已是主數據源，BingX 僅作為技術指標 K 線來源
    # ════════════════════════════════════════════════════════
    logger.info("📊 [掃描漏斗] Step 0：預先抓取 BingX K線合約對照表 + CoinGlass 交易所支援名單...")
    allowed_bases: Set[str] = set()
    base_to_symbol: Dict[str, str] = {}
    _ab0, base_to_symbol, _ = fetch_bingx_contracts()
    if _ab0:
        allowed_bases = _ab0
        logger.info(f"📊 [掃描漏斗] Step 0a：BingX API 對照表載入 {len(allowed_bases)} 個幣種（K線查詢備用）")
    else:
        logger.warning("📊 [掃描漏斗] Step 0a：BingX 對照表取得失敗，K線改用 CoinGlass 價格備援")
    # CoinGlass supported-exchange-pairs：用於訊息中標示各幣 BingX 支援狀態
    cg_bingx_supported: Set[str] = fetch_cg_bingx_supported_bases()
    # 合併兩個來源：BingX API + CoinGlass exchange-pairs → 最完整的支援集合
    all_bingx_supported: Set[str] = allowed_bases | cg_bingx_supported
    logger.info(
        f"📊 [掃描漏斗] Step 0b：CoinGlass 交易所支援 BingX {len(cg_bingx_supported)} 個"
        f"，合併後共 {len(all_bingx_supported)} 個 BingX 支援幣種"
    )

    # ════════════════════════════════════════════════════════
    # 漏斗 Step 1：CoinGlass 全市場數據（帶分頁，抓取 300~500 個幣種）
    # ════════════════════════════════════════════════════════
    all_symbols_data = fetch_coinglass_coins_markets()
    if not all_symbols_data:
        # 備援：舊式流程
        logger.warning("[巨鯨漏斗] CoinGlass 主流端點失敗，啟用備援流程")
        if _ab0:
            all_symbols_data = fetch_coins_price_change()
            logger.info(f"[備援] CoinGlass coins-price-change 取得 {len(all_symbols_data)} 個幣種")
        if not all_symbols_data:
            send_telegram_message("⚠️ 無法取得幣種漲跌資料，請稍後再試。", TG_THREAD_IDS['position_change'])
            return
    logger.info(f"📊 [掃描漏斗] 1. CoinGlass 全網共 {len(all_symbols_data)} 幣種")

    # ── 24h 漲跌幅快取（CoinGlass 已含此欄位，直接讀取）────────────────────────
    coinglass_24h_map: Dict[str, float] = {}
    for coin in all_symbols_data:
        pct = extract_price_change_24h(coin)
        if pct is not None:
            s = normalize_symbol(coin) or ""
            clean = s.replace("USDT", "").replace("-", "").replace("_", "").upper()
            if clean:
                coinglass_24h_map[clean] = pct
    if not coinglass_24h_map:
        coinglass_24h_map = _fetch_coinglass_24h_map()

    # ════════════════════════════════════════════════════════
    # 漏斗 Step 2：CoinGlass 全市場放行（訊號以 CoinGlass 為準，不做 BingX 過濾）
    # 訊息中會加上提示，請用戶自行確認交易所是否支援此標的
    # ════════════════════════════════════════════════════════
    bingx_filtered = all_symbols_data  # 直接放行全部 CoinGlass 幣種
    logger.info(
        f"📊 [掃描漏斗] 2. CoinGlass 全市場放行 {len(bingx_filtered)} 幣種（不做 BingX 過濾，訊號以 CoinGlass 為準）"
    )

    # ════════════════════════════════════════════════════════
    # 漏斗 Step 3：價格波動過濾（|15m 漲跌幅| >= PRICE_GATEKEEPER）
    # ════════════════════════════════════════════════════════
    PRICE_GATEKEEPER = 0.6   # 15m 山寨幣：|漲跌幅| ≥0.6% 才有動能
    active_symbols: List[Dict] = []
    stub_pass_count = 0
    for coin in bingx_filtered:
        # supported-coins stub（沒有 price 數據）→ 直接放行讓 OI 決定
        if coin.get("_stub"):
            active_symbols.append(coin)
            stub_pass_count += 1
            continue
        p_change = extract_price_change_30m(coin)
        if abs(p_change) >= PRICE_GATEKEEPER:
            active_symbols.append(coin)
    logger.info(
        f"📊 [掃描漏斗] 3. 經價格波動(>={PRICE_GATEKEEPER}%)過濾，最終 {len(active_symbols)} 幣種進入 OI 運算"
        f"（淘汰 {len(bingx_filtered) - len(active_symbols)} 個低波動標的 | stub 無數據直通 {stub_pass_count} 個）"
    )

    # ════════════════════════════════════════════════════════
    # 漏斗 Step 4：成交量預篩（直接讀 CoinGlass _cg_volume_usd）
    # ════════════════════════════════════════════════════════
    # coins-price-change 端點絕大多數幣的 _cg_volume_usd 為 None（API 本身不帶量）
    # 真正的精品門撒由 Step 4.5 BingX 支援過濾承擔；Step 4 只做「確定超小幣」淘汰
    # 邏輯：有量資料且明確 < 1M USD → 淘汰；其餘（含 None）一律放行給 BingX 過濾
    VOLUME_PREFILTER_MIN_USD = 1_000_000   # 僅淘汰確定 <1M 的超小幣
    active_above_volume: List[Dict[str, Any]] = []
    vol_no_data = 0
    vol_below = 0
    for coin in active_symbols:
        cg_vol = coin.get("_cg_volume_usd")
        if cg_vol is None:
            vol_no_data += 1
            coin["_volume_usd"] = 0.0
            active_above_volume.append(coin)   # 無量資料放行，主力信號不依賴成交量
        else:
            try:
                vol = float(cg_vol)
            except (TypeError, ValueError):
                vol = 0.0
            coin["_volume_usd"] = vol
            if vol > 0 and vol < VOLUME_PREFILTER_MIN_USD:
                vol_below += 1   # 確定 <1M 才淘汰
            else:
                active_above_volume.append(coin)
    logger.info(
        f"📊 [掃描漏斗] 4. 成交量預篩(確定<{VOLUME_PREFILTER_MIN_USD/1e6:.0f}M 才淘汰)："
        f"通過 {len(active_above_volume)} 個（無量資料放行 {vol_no_data} 個，超小幣淘汰 {vol_below} 個）"
    )

    # ── Step 4.5：BingX 支援過濾（不支援的幣不運算、不推播）────────────────
    bingx_filtered_in: List[Dict[str, Any]] = []
    bingx_filtered_out = 0
    for _coin in active_above_volume:
        _base = (_coin.get("symbol") or "").replace("USDT", "").replace("-", "").replace("_", "").upper()
        if _base in all_bingx_supported:
            bingx_filtered_in.append(_coin)
        else:
            bingx_filtered_out += 1
    logger.info(
        f"📊 [掃描漏斗] 4.5. BingX 支援過濾：通過 {len(bingx_filtered_in)} 個，"
        f"淘汰 {bingx_filtered_out} 個（BingX 不支援，跳過運算）"
    )
    active_above_volume = bingx_filtered_in

    # ── Step 5：排序 + 限制數量（前 50 固定，其餘隨機保多樣性）─────────────────
    MAX_OI_SYMBOLS = 320
    target_symbols: List[Dict[str, Any]] = []
    if active_above_volume:
        active_above_volume.sort(key=lambda c: c.get("_volume_usd", 0.0), reverse=True)
        top_fixed = active_above_volume[:50]
        rest = active_above_volume[50:]
        if rest:
            random.shuffle(rest)
        combined = top_fixed + rest
        target_symbols = combined[:MAX_OI_SYMBOLS]
    if len(active_above_volume) > MAX_OI_SYMBOLS:
        logger.info(
            f"成交量過篩後共 {len(active_above_volume)} 個，本輪僅處理前 {MAX_OI_SYMBOLS} 個以確保準時推播 "
            f"(前 50 依成交額固定，其餘隨機採樣)"
        )
    
    long_open = []
    long_close = []
    short_open = []
    short_close = []
    
    processed_count = 0
    oi_success_count = 0
    oi_fail_count = 0
    
    # 並行處理配置：標準版高頻模式，預設 12 執行緒；熔斷器啟動時自動降為 1
    MAX_WORKERS = _cb_get_max_workers(default=15)
    _cb_tripped = _circuit_breaker.get("tripped", False)
    logger.info(f"[啟動環境] CG_API_KEY={'已設定('+CG_API_KEY[:6]+'...)' if CG_API_KEY else '❌未設定'}"
                f" | MAX_WORKERS={MAX_WORKERS} | 熔斷器={'⚠️降速模式' if _cb_tripped else '✅正常'}")
    if MAX_WORKERS == 1:
        logger.warning("[熔斷器作用中] MAX_WORKERS 已降為 1，本輪採單執行緒保護模式")
    start_time = time.time()
    MAX_EXECUTION_TIME = 16 * 60  # 強制結束上限 16 分鐘（雙重保護用）
    
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    broke_early = False
    try:
        future_to_coin = {executor.submit(process_single_symbol, coin): coin for coin in target_symbols}
        completed = 0
        for future in as_completed(future_to_coin):
            elapsed_time = time.time() - start_time
            if elapsed_time > MAX_EXECUTION_TIME:
                logger.warning(f"已達 {MAX_EXECUTION_TIME/60:.0f} 分鐘上限，提前結束並推播（已處理 {processed_count} 個）")
                for f in future_to_coin:
                    f.cancel()
                broke_early = True
                break
            
            completed += 1
            result = future.result()
            if result is None:
                continue
            processed_count += 1
            if completed % 100 == 0:
                logger.info(f"處理進度: {completed}/{len(target_symbols)} | 已用時: {elapsed_time/60:.1f} 分鐘")
            status = result.get('status')
            if status == 'oi_failed':
                oi_fail_count += 1
            elif status == 'success':
                oi_success_count += 1
                category = result.get('category')
                symbol = result.get('symbol')
                price_change = result.get('priceChange30m')
                oi_change = result.get('oiChange30m')
                price_change_24h = result.get('priceChange24h')
                item = {'symbol': symbol, 'priceChange30m': price_change, 'oiChange30m': oi_change, 'priceChange24h': price_change_24h}
                base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
                oi_min = OI_MAIN_COIN_MIN if base in MAIN_COINS else OI_ALTCOIN_MIN
                if abs(oi_change) >= oi_min:
                    if category == 'long_open':
                        long_open.append(item)
                    elif category == 'long_close':
                        long_close.append(item)
                    elif category == 'short_open':
                        short_open.append(item)
                    elif category == 'short_close':
                        short_close.append(item)
    finally:
        executor.shutdown(wait=not broke_early)  # 提前結束時不等待未完成任務，以利準時推播
    
    total_time = time.time() - start_time
    in_four = len(long_open) + len(long_close) + len(short_open) + len(short_close)
    below_oi_threshold = oi_success_count - in_four
    logger.info(f"處理統計: 總共 {processed_count} 個幣種, OI 成功 {oi_success_count} 個, OI 失敗 {oi_fail_count} 個 | 總用時: {total_time/60:.1f} 分鐘")
    logger.info(f"分類結果: 多方開倉 {len(long_open)}, 多方平倉 {len(long_close)}, 空方開倉 {len(short_open)}, 空方平倉 {len(short_close)}（共 {in_four} 個達 OI 初選門檻｜其餘 {below_oi_threshold} 個 OI 成功但 |OI 變化| 未達門檻未入四類）")

    # 動態 OI 門檻計算：以本輪四類樣本的 |OI 30m| 分佈計算平均與標準差，
    # 4 星實際門檻 = max(固定 4 星門檻, mean + 1σ)
    # 5 星實際門檻 = max(固定 5 星門檻, mean + 2σ)
    global _dynamic_oi_mean_30m, _dynamic_oi_std_30m, _dynamic_oi_4star, _dynamic_oi_5star, _dynamic_oi_sample_size
    oi_samples: List[float] = []
    for _lst in (long_open, long_close, short_open, short_close):
        for _x in _lst:
            try:
                v = float(_x.get("oiChange30m") or 0.0)
            except (TypeError, ValueError):
                continue
            if v == v:
                oi_samples.append(abs(v))
    _dynamic_oi_sample_size = len(oi_samples)
    if _dynamic_oi_sample_size >= 10:
        arr = np.array(oi_samples, dtype=float)
        _dynamic_oi_mean_30m = float(arr.mean())
        _dynamic_oi_std_30m = float(arr.std())
        _dynamic_oi_4star = max(OI_FOR_4_STAR, _dynamic_oi_mean_30m + 1.0 * _dynamic_oi_std_30m)
        _dynamic_oi_5star = max(OI_FOR_5_STAR, _dynamic_oi_mean_30m + 2.0 * _dynamic_oi_std_30m)
        logger.info(
            f"【動態 OI 門檻】樣本 {_dynamic_oi_sample_size} 個 | 平均 {_dynamic_oi_mean_30m:.2f}% σ {_dynamic_oi_std_30m:.2f}% → "
            f"4星門檻 {_dynamic_oi_4star:.2f}% 5星門檻 {_dynamic_oi_5star:.2f}%"
        )
    else:
        # 樣本數不足：嘗試從 CoinGlass 抓取 24h 成交量前 20 名幣種的 OI 15m 變化作補充
        logger.info(
            f"【動態 OI 門檻】樣本數 {_dynamic_oi_sample_size} < 10，嘗試從 CoinGlass Top-20 補充初始化樣本..."
        )
        _extra_samples: List[float] = []
        try:
            _respect_coinglass_rate_limit()
            _top_resp = requests.get(
                f"{CG_API_BASE}/api/futures/coins-markets",
                headers={"CG-API-KEY": CG_API_KEY, "accept": "application/json"},
                params={"limit": 20, "sort_by": "volUsd24h", "sort_order": "desc"},
                timeout=10
            )
            if _top_resp.status_code == 200:
                _top_data = _top_resp.json()
                _top_list = _top_data.get("data") or []
                if not isinstance(_top_list, list):
                    _top_list = []
                for _item in _top_list:
                    for _k in ("oiChangePercent15m", "oiChange15m", "oi_change_15m",
                               "oiChangePercent30m", "oiChange30m"):
                        _v = _item.get(_k)
                        if _v is not None:
                            try:
                                _extra_samples.append(abs(float(_v)))
                            except (TypeError, ValueError):
                                pass
                            break
                if _extra_samples:
                    oi_samples.extend(_extra_samples)
                    logger.info(f"【動態 OI 門檻】Top-20 補充 {len(_extra_samples)} 個樣本，總計 {len(oi_samples)} 個")
        except Exception as _e:
            logger.warning(f"【動態 OI 門檻】Top-20 補充失敗（不影響主流程）: {_e}")

        if len(oi_samples) >= 10:
            arr = np.array(oi_samples, dtype=float)
            _dynamic_oi_mean_30m = float(arr.mean())
            _dynamic_oi_std_30m = float(arr.std())
            _dynamic_oi_4star = max(OI_FOR_4_STAR, _dynamic_oi_mean_30m + 1.0 * _dynamic_oi_std_30m)
            _dynamic_oi_5star = max(OI_FOR_5_STAR, _dynamic_oi_mean_30m + 2.0 * _dynamic_oi_std_30m)
            _dynamic_oi_sample_size = len(oi_samples)
            logger.info(
                f"【動態 OI 門檻 (補充後)】樣本 {_dynamic_oi_sample_size} 個 | 平均 {_dynamic_oi_mean_30m:.2f}% σ {_dynamic_oi_std_30m:.2f}% → "
                f"4星門檻 {_dynamic_oi_4star:.2f}% 5星門檻 {_dynamic_oi_5star:.2f}%"
            )
        else:
            _dynamic_oi_mean_30m = None
            _dynamic_oi_std_30m = None
            _dynamic_oi_4star = None
            _dynamic_oi_5star = None
            logger.info(
                f"【動態 OI 門檻】補充後樣本數仍不足 10（共 {len(oi_samples)} 個），沿用固定門檻 "
                f"4星 {OI_FOR_4_STAR}% / 5星 {OI_FOR_5_STAR}%"
            )

    # 只統計與計算 4 星以上：|OI| < 實際 4 星門檻 的不進 top、不跑後續運算
    oi_threshold_4 = _dynamic_oi_4star if (_dynamic_oi_4star is not None and _dynamic_oi_sample_size >= 10) else OI_FOR_4_STAR
    long_open = [x for x in long_open if abs(x.get('oiChange30m') or 0) >= oi_threshold_4]
    long_close = [x for x in long_close if abs(x.get('oiChange30m') or 0) >= oi_threshold_4]
    short_open = [x for x in short_open if abs(x.get('oiChange30m') or 0) >= oi_threshold_4]
    short_close = [x for x in short_close if abs(x.get('oiChange30m') or 0) >= oi_threshold_4]
    long_open.sort(key=lambda x: x['oiChange30m'], reverse=True)
    long_close.sort(key=lambda x: x['oiChange30m'])
    short_open.sort(key=lambda x: x['oiChange30m'], reverse=True)
    short_close.sort(key=lambda x: x['oiChange30m'])
    top_long_open = long_open[:3]
    top_long_close = long_close[:3]
    top_short_open = short_open[:3]
    top_short_close = short_close[:3]
    logger.info(f"四類 TOP 候選數: 多方開倉 {len(top_long_open)}, 多方平倉 {len(top_long_close)}, 空方開倉 {len(top_short_open)}, 空方平倉 {len(top_short_close)}（各類取前 3，供後續分類/成交量/冷卻/風報篩選）")

    # BingX 支援名單已在漏斗 Step 0 預先載入，此處直接使用 allowed_bases / base_to_symbol
    logger.info(
        f"[漏斗確認] enrichment 開始，BingX 名單 {len(allowed_bases)} 個 base，"
        f"base_to_symbol {len(base_to_symbol)} 個映射"
    )

    # 批次預載全市場 15m RSI（一次 API 呼叫，enrichment 直接查表，節省大量單幣請求）
    _cg_rsi_map: Dict[str, Optional[float]] = fetch_cg_rsi_bulk(interval="15m")
    logger.info(f"[RSI批次] 預載完成，共 {len(_cg_rsi_map)} 個幣種 15m RSI 可直接查表")

    # 批次預載全市場 Funding Rate（CoinGlass exchange-list，一次取全部）
    _cg_fr_map: Dict[str, float] = _fetch_funding_rate_map()
    logger.info(f"[FR批次] CoinGlass Funding Rate 預載完成，共 {len(_cg_fr_map)} 個幣種")

    # 對 top 標的取 RSI/布林帶（僅 4 星以上候選，3 星不計算）
    time.sleep(2)
    all_top = []
    for item, cat in [(x, "long_open") for x in top_long_open] + [(x, "long_close") for x in top_long_close] + [(x, "short_open") for x in top_short_open] + [(x, "short_close") for x in top_short_close]:
        sym = item.get("symbol", "")
        clean_base = sym.replace("USDT", "").replace("-", "").upper()
        preferred = base_to_symbol.get(clean_base) if base_to_symbol else None
        if not preferred:
            preferred = base_to_symbol.get(sym.upper()) if base_to_symbol else None
        time.sleep(0.2)
        tech = calculate_technicals(sym, bingx_symbol_override=preferred)
        # Funding Rate：優先 CoinGlass 預載批次表，找不到再逐幣呼叫 BingX 備援
        _base_fr = sym.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
        funding_rate = _cg_fr_map.get(_base_fr)
        if funding_rate is None:
            funding_rate = _fetch_bingx_funding_rate(sym, preferred_symbol=preferred)
        price_24h = item.get("priceChange24h") if isinstance(item.get("priceChange24h"), (int, float)) else None
        if price_24h is None:
            price_24h = coinglass_24h_map.get(clean_base)
        if price_24h is None:
            price_24h = fetch_price_change_24h_coinglass_klines(sym, preferred)
        if price_24h is None:
            price_24h = fetch_price_change_24h_bingx(sym, preferred)
        cvd_change_1h = _cvd_change_last2(clean_base, "1h")
        time.sleep(0.25)
        whale_idx = _whale_index_latest(clean_base, "1d")
        time.sleep(0.2)
        # v3.0 散戶多空比（僅對 4/5 星候選額外調用）
        # BingX-only 幣種不在 Binance，跳過以避免預期外 400 呼叫
        symbol_param = clean_base + "USDT"
        if get_major_exchanges_for_coin(clean_base, ["Binance"]):
            global_data = fetch_global_account_ratio(symbol_param, "1h")
            time.sleep(0.5)
        else:
            global_data = None
            logger.debug(f"全局帳戶比 {clean_base}: BingX-only 幣種，跳過 Binance 查詢")
        latest_point = get_latest_data_point(global_data) if global_data else None
        retail_ratio = latest_point.get("global_account_long_short_ratio") if isinstance(latest_point, dict) else None
        if retail_ratio is not None and isinstance(retail_ratio, (int, float)):
            logger.info(f"散戶多空比 {clean_base}: {retail_ratio}")
        classified = _classify_signal_and_tier(
            item, cat, tech, funding_rate,
            price_chg_24h=price_24h,
            cvd_change_1h=cvd_change_1h,
            whale_index=whale_idx,
            retail_ratio=retail_ratio,
        )
        if classified is None:
            logger.info(f"狙擊鏡跳過 {sym}: 分類未通過 (_classify_signal_and_tier 回傳 None，可能 OI/方向/價位未符合任一訊號分支)")
            continue  # 未達 4 星門檻或被濾掉，直接略過
        signal_label, zone, stars, rsi_desc, reason = classified
        rsi_val = tech.get("rsi") if tech else None
        # 若 BingX K 線未能計算出 RSI，嘗試用預載的 CoinGlass RSI 批次表補齊
        if rsi_val is None:
            _base_key_rsi = sym.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
            rsi_val = _cg_rsi_map.get(_base_key_rsi)
            if rsi_val is not None:
                logger.debug(f"[RSI批次補齊] {sym}: RSI={rsi_val:.1f}（CoinGlass 批次表）")
        ub_val = tech.get("ub_value") if tech else None
        lb_val = tech.get("lb_value") if tech else None
        atr_val = tech.get("atr") if tech else None

        # ── 5M 動能共振驗證（標準版核心濾網）────────────────────────────────
        resonance_5m = fetch_oi_resonance_5m(sym, cat)
        if resonance_5m is False and stars == 5:
            stars = 4
            reason = f"{reason} ⚠️[5M動能已竭:已降星]"
            logger.info(f"[5M共振] {sym} 5M OI 與 15M 反向，5星降為4星")

        # ── 5M RSI 超賣/超買升等鑽石路徑 ────────────────────────────────────
        # 做多訊號且 5m RSI < 35（超賣）→ 有底部反轉共振，標記升等旗標
        # 做空訊號且 5m RSI > 65（超買）→ 有頂部反轉共振，標記升等旗標
        rsi_5m_val = fetch_rsi_5m(sym)
        has_5m_rsi_extreme = False
        if rsi_5m_val is not None:
            if cat in ("long_open", "short_close") and rsi_5m_val < 35:
                has_5m_rsi_extreme = True
                reason = f"{reason} 📉5mRSI={rsi_5m_val:.0f}超賣底部共振"
                logger.info(f"[5mRSI鑽石] {sym} 做多 5mRSI={rsi_5m_val:.0f}<35，升等鑽石資格")
            elif cat in ("short_open", "long_close") and rsi_5m_val > 65:
                has_5m_rsi_extreme = True
                reason = f"{reason} 📈5mRSI={rsi_5m_val:.0f}超買頂部共振"
                logger.info(f"[5mRSI鑽石] {sym} 做空 5mRSI={rsi_5m_val:.0f}>65，升等鑽石資格")

        # ── 深度籌碼背離（15m OI vs 價格方向，嚴格 3% OI 門檻）─────────────
        _p15 = item.get("priceChange15m") or item.get("priceChange30m")
        _oi15 = item.get("oiChange15m") or item.get("oiChange30m")
        chip_divergence: Optional[str] = None
        try:
            _p15_f = float(_p15) if _p15 is not None else None
            _oi15_f = float(_oi15) if _oi15 is not None else None
            if _p15_f is not None and _oi15_f is not None:
                if _oi15_f > 3.0 and _p15_f < 0:
                    chip_divergence = "absorption"
                    if _is_long_signal:
                        # 多單方向：OI大增+價格下跌 → 主力逆勢在低點逢低買入（吸籌）
                        reason = f"{reason} 🕵️主力底部吸籌(OI+{_oi15_f:.1f}%↑價{_p15_f:.1f}%↓)"
                        logger.info(f"[籌碼背離] {sym} 底部吸籌(多向): OI+{_oi15_f:.1f}% 但價格{_p15_f:.1f}%")
                    else:
                        # 空單方向：OI大增+價格下跌 → 主力積極建空（空頭加倉確認下跌）
                        reason = f"{reason} 🐻主力積極建空(OI+{_oi15_f:.1f}%↑價{_p15_f:.1f}%↓)"
                        logger.info(f"[籌碼背離] {sym} 主力建空(空向): OI+{_oi15_f:.1f}% 價格{_p15_f:.1f}%")
                elif _oi15_f < -3.0 and _p15_f > 0:
                    chip_divergence = "distribution"
                    if _is_long_signal:
                        # 多單方向：OI大縮+價格上漲 → 主力高位出貨（多頭獲利了結）
                        reason = f"{reason} ⚠️主力高位出貨(OI{_oi15_f:.1f}%↓價+{_p15_f:.1f}%↑)"
                        logger.info(f"[籌碼背離] {sym} 高位出貨(多向): OI{_oi15_f:.1f}% 但價格+{_p15_f:.1f}%")
                    else:
                        # 空單方向：OI大縮+價格上漲 → 空頭撤退（空方認輸回補）
                        reason = f"{reason} ⚠️空頭撤退(OI{_oi15_f:.1f}%↓價+{_p15_f:.1f}%↑)"
                        logger.info(f"[籌碼背離] {sym} 空頭撤退(空向): OI{_oi15_f:.1f}% 但價格+{_p15_f:.1f}%")
        except (TypeError, ValueError):
            pass

        # ── 爆量偵測（從 K 線計算結果讀取）──────────────────────────────────
        vol_spike_ratio = tech.get("vol_spike_ratio") if tech else None
        has_vol_spike = isinstance(vol_spike_ratio, (int, float)) and vol_spike_ratio >= 2.0

        # ── 爆倉熱力圖 + 掛單牆（僅 4/5 星候選，降低 API 負擔）────────────────
        _cur_price = tech.get("current_price") if tech else None
        _is_long_signal = cat in ("long_open", "long_close")
        liq_nearby: Optional[Dict] = None
        ob_wall: Optional[Dict] = None
        if _cur_price and isinstance(_cur_price, (int, float)) and _cur_price > 0:
            liq_nearby = fetch_liq_heatmap_nearby(sym, _cur_price, _is_long_signal)
            time.sleep(0.1)
            ob_wall = check_orderbook_wall(sym, _cur_price, _is_long_signal, preferred_symbol=preferred)

        # ── 🐋 主力同步建倉判斷 ─────────────────────────────────────────────
        whale_sync = False
        if whale_idx is not None and isinstance(whale_idx, (int, float)):
            if _is_long_signal and whale_idx >= 65:
                whale_sync = True
                reason = reason + f" 🐋主力同步做多({whale_idx:.0f})"
            elif not _is_long_signal and whale_idx <= 35:
                whale_sync = True
                reason = reason + f" 🐋主力同步做空({whale_idx:.0f})"

        # ── 💱 累積費率極端值偵測（嘎空/殺多潛力）──────────────────────────
        _accum_fr_data: Dict[str, Any] = {}
        try:
            _accum_fr_data = fetch_accumulated_funding_score(sym)
            _squeeze_risk = _accum_fr_data.get("squeeze_risk") or "neutral"
            if _squeeze_risk == "short_squeeze" and _is_long_signal:
                # 空頭費用過高 + 做多訊號 = 嘎空機率大，加分
                reason = reason + f" 🔥費率嘎空({_accum_fr_data.get('accumulated_7d',0)*100:.2f}%累積)"
                logger.info(f"[累積費率] {sym} 空頭過熱+做多訊號=嘎空潛力")
            elif _squeeze_risk == "long_squeeze" and _is_long_signal:
                # 多頭費用過高 + 做多訊號 = 追多風險，降信心
                reason = reason + f" ⚠️費率多頭過熱({_accum_fr_data.get('accumulated_7d',0)*100:.2f}%累積)"
                logger.info(f"[累積費率] {sym} 多頭過熱+做多訊號=追多風險")
            elif _squeeze_risk == "long_squeeze" and not _is_long_signal:
                # 多頭費用過高 + 做空訊號 = 殺多潛力大
                reason = reason + f" ⛽費率殺多({_accum_fr_data.get('accumulated_7d',0)*100:.2f}%累積)"
        except Exception as _fe2:
            logger.debug(f"[累積費率] {sym} 獲取失敗: {_fe2}")

        # ── 📊 訂單流分析（主動買賣比 + 淨多倉位 + 腳步圖 + OI趨勢 + 大戶L/S）──
        _taker_ratio: Optional[float] = None
        _net_pos_delta: Optional[float] = None
        _footprint: Dict[str, Any] = {}
        _fp_sup: Optional[float] = None
        _fp_res: Optional[float] = None
        _fp_poc: Optional[float] = None
        _flow_score_val: int = 0
        _oi_trend_data: Dict[str, Any] = {}
        _top_ls_ratio: Optional[float] = None
        if _cur_price and isinstance(_cur_price, (int, float)) and _cur_price > 0:
            try:
                _taker_ratio = fetch_taker_bvs_ratio(sym)
                time.sleep(0.12)
                _net_pos_delta = fetch_net_position_delta(sym)
                time.sleep(0.12)
                _footprint = fetch_footprint_key_levels(sym, _cur_price, _is_long_signal)
                time.sleep(0.12)
                _oi_trend_data = fetch_oi_trend_analysis(sym)
                time.sleep(0.12)
                _top_ls_ratio = fetch_top_account_ls_ratio(sym)
                if _is_long_signal:
                    _fp_sup = _footprint.get("nearest_support")
                    _fp_res = _footprint.get("nearest_resistance")
                else:
                    # 做空方向：阻力在上（壓力SL），支撐在下（TP目標）
                    _fp_sup = _footprint.get("nearest_resistance")
                    _fp_res = _footprint.get("nearest_support")
                _fp_poc = _footprint.get("poc")
                _flow_score_val = compute_flow_score(_taker_ratio, _net_pos_delta, _is_long_signal)
                # OI 加速建倉 → 額外加分
                if _oi_trend_data.get("acceleration") and (
                    (_is_long_signal and _oi_trend_data.get("trend") == "building") or
                    (not _is_long_signal and _oi_trend_data.get("trend") == "declining")
                ):
                    _flow_score_val = min(_flow_score_val + 1, 4)
                if _flow_score_val >= 3:
                    reason = reason + f" 🌊訂單流強確認(+{_flow_score_val})"
                elif _flow_score_val <= 0 and _taker_ratio is not None:
                    pass  # 中性不加標籤
            except Exception as _fe:
                logger.debug(f"[訂單流] {sym} 獲取失敗: {_fe}")

        # ── 🔬 訂單流智能升降星（基於 flow_score / oi_trend / top_ls / taker）────
        # 規則：
        #   升 4→5：flow_score≥3 + oi_trend=building(多)/declining(空) + 大戶同向
        #   升 5→保持：flow_score=4 且 top_ls 確認 → 加「強升」標籤
        #   降 5→4：flow_score≤0 且 taker 反向 且 top_ls 反向 → 降星
        #   降 4→None(丟棄)：flow_score=0 且 taker 強烈反向(>65%反向) → 丟棄
        _oi_trend_str = _oi_trend_data.get("trend") if _oi_trend_data else None
        _oi_accel_b = _oi_trend_data.get("acceleration") if _oi_trend_data else False
        _taker_aligned = (
            (_is_long_signal and _taker_ratio is not None and _taker_ratio > 55) or
            (not _is_long_signal and _taker_ratio is not None and _taker_ratio < 45)
        )
        _taker_strongly_opposed = (
            (_is_long_signal and _taker_ratio is not None and _taker_ratio < 38) or
            (not _is_long_signal and _taker_ratio is not None and _taker_ratio > 62)
        )
        _oi_trend_aligned = (
            (_is_long_signal and _oi_trend_str == "building") or
            (not _is_long_signal and _oi_trend_str == "declining")
        )
        _top_ls_aligned = (
            (_is_long_signal and _top_ls_ratio is not None and _top_ls_ratio > 1.05) or
            (not _is_long_signal and _top_ls_ratio is not None and _top_ls_ratio < 0.95)
        )
        _top_ls_opposed = (
            (_is_long_signal and _top_ls_ratio is not None and _top_ls_ratio < 0.92) or
            (not _is_long_signal and _top_ls_ratio is not None and _top_ls_ratio > 1.08)
        )

        # 升 4→5：三重訂單流確認（taker強確認 + OI趨勢對齊 + 大戶同向）
        if stars == 4 and _flow_score_val >= 3 and _oi_trend_aligned and _top_ls_aligned:
            stars = 5
            reason = reason + f" ⬆️訂單流三重升星(flow={_flow_score_val}/OI={_oi_trend_str}/大戶={_top_ls_ratio:.2f})"
            logger.info(f"[升星] {sym} 4→5星：flow={_flow_score_val} oi={_oi_trend_str} top_ls={_top_ls_ratio}")

        # 強加速升等標記：5星 + flow_score=4 + OI加速建倉 → 信心最強
        elif stars == 5 and _flow_score_val == 4 and _oi_accel_b and _oi_trend_aligned:
            reason = reason + " 🚀訂單流極強(三重加速確認)"
            logger.info(f"[極強] {sym} 5星訂單流極強加速確認")

        # 降 5→4：taker反向 + 大戶反向 + flow_score低
        elif stars == 5 and _flow_score_val <= 0 and _taker_strongly_opposed and _top_ls_opposed:
            stars = 4
            reason = reason + f" ⬇️訂單流警告降星(taker={_taker_ratio:.0f}%反向,大戶={_top_ls_ratio:.2f}反向)" if _taker_ratio and _top_ls_ratio else reason + " ⬇️訂單流警告降星"
            logger.info(f"[降星] {sym} 5→4星：taker={_taker_ratio} top_ls={_top_ls_ratio} flow={_flow_score_val}")

        # 降 5→4：市場微結構資料完全缺失（taker/大戶/OI趨勢全為 None）→ 無法確認，降為賭鬼
        # 5星(列車)代表「高度確認的趨勢」，資料全無則缺乏確認，只能算賭鬼級
        elif stars == 5 and _taker_ratio is None and _top_ls_ratio is None and _flow_score_val == 0:
            stars = 4
            reason = reason + " ⬇️微結構資料不足降4★"
            logger.info(f"[降星] {sym} 5→4星：taker/大戶/訂單流全部無法取得，資料不足無法確認為列車級")

        # 丟棄4星：taker強烈反向且訂單流完全背離（保護勝率）
        elif stars == 4 and _taker_strongly_opposed and _flow_score_val <= 0 and _top_ls_opposed:
            logger.info(f"[丟棄] {sym} 4星被訂單流反向丟棄：taker={_taker_ratio} top_ls={_top_ls_ratio} flow={_flow_score_val}")
            continue  # 不進 all_top，相當於無訊號

        # 判斷此幣 BingX 是否支援（合併 BingX API + CoinGlass exchange-pairs 兩個來源）
        _base_key = sym.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
        _bingx_ok = _base_key in all_bingx_supported

        all_top.append({
            **item,
            "priceChange24h": price_24h,
            "category": cat,
            "current_price": _cur_price,
            "rsi": rsi_val,
            "atr": atr_val,
            "ub_value": tech.get("ub_value") if tech else None,
            "lb_value": tech.get("lb_value") if tech else None,
            "vwap_2h": tech.get("vwap_2h") if tech else None,
            "ema20_close": tech.get("ema20_close") if tech else None,
            "recent_high_2h": tech.get("recent_high_2h") if tech else None,
            "recent_low_2h": tech.get("recent_low_2h") if tech else None,
            "last_kline_open_30m": tech.get("last_kline_open_30m") if tech else None,
            "last_kline_high_30m": tech.get("last_kline_high_30m") if tech else None,
            "last_kline_low_30m": tech.get("last_kline_low_30m") if tech else None,
            "last_kline_close_30m": tech.get("last_kline_close_30m") if tech else None,
            "whale_index": whale_idx,
            "cvd_change_1h": cvd_change_1h,
            "signal_label": signal_label,
            "zone": zone,
            "stars": stars,
            "rsi_desc": rsi_desc,
            "reason": reason,
            "funding_rate": funding_rate,
            "plan_b_used": bool(tech.get("plan_b_used")) if tech else False,
            "energy_exhausted": bool(tech.get("energy_exhausted")) if tech else False,
            "has_5m_resonance": resonance_5m is True,
            "resonance_5m_raw": resonance_5m,
            "rsi_5m": rsi_5m_val,               # 5m RSI 數值
            "has_5m_rsi_extreme": has_5m_rsi_extreme,  # 5m RSI 極端值升等旗標
            "chip_divergence": chip_divergence,  # 籌碼背離類型
            "is_global_consensus": False,
            # ── 新增情報欄位 ──
            "vol_spike_ratio": vol_spike_ratio,
            "has_vol_spike": has_vol_spike,
            "liq_nearby": liq_nearby,     # 爆倉熱力圖
            "ob_wall": ob_wall,           # 掛單牆
            "whale_sync": whale_sync,     # 主力同步
            "bingx_supported": _bingx_ok,  # BingX 是否支援（CoinGlass exchange-pairs 驗證）
            # ── 訂單流分析 ──
            "taker_ratio": _taker_ratio,          # 主動買盤佔比 %（>50=買盤主導）
            "net_pos_delta": _net_pos_delta,       # 淨多倉位方向（+1=強做多,-1=強做空）
            "fp_support": _fp_sup,                 # 關鍵支撐位（SL 精準化用）
            "fp_resistance": _fp_res,              # 關鍵阻力位（TP 精準化用）
            "fp_poc": _fp_poc,                     # 成交密集點（Point of Control）
            "fp_data_source": _footprint.get("data_source") or "unavailable",  # 數據來源標籤
            "flow_score": _flow_score_val,         # 訂單流綜合評分 0~4
            "oi_trend": _oi_trend_data.get("trend"),  # OI趨勢: building/declining/flat/reversing
            "oi_acceleration": _oi_trend_data.get("acceleration"),  # OI加速建倉旗標
            "oi_change_1h_pct": _oi_trend_data.get("change_1h_pct"),  # 近1h OI 變化%
            "oi_change_4h_pct": _oi_trend_data.get("change_4h_pct"),  # 近4h OI 變化%（若資料不足則為 None）
            "top_ls_ratio": _top_ls_ratio,         # 大戶多空比（>1大戶偏多，<1大戶偏空）
            "accum_fr_7d": _accum_fr_data.get("accumulated_7d"),   # 7日累積資金費率
            "accum_fr_squeeze": _accum_fr_data.get("squeeze_risk") or "neutral",  # 擠壓風險類型
            "accum_fr_label": _accum_fr_data.get("squeeze_label") or "",  # 推播用標籤
        })
        resonance_str = "🔥共振" if resonance_5m is True else ("⚠️已竭" if resonance_5m is False else "❓未知")
        vol_str = f"⚡{vol_spike_ratio:.1f}×" if has_vol_spike else "-"
        logger.info(
            f"Top 入選 {sym}: 星{stars} 區={zone} RSI={rsi_val} ATR={atr_val} "
            f"5M={resonance_str} 爆量={vol_str} 鯨魚={whale_idx} | {reason}"
        )

    # ══════════════════════════════════════════════════════════
    # 品質門撒：ATR=None 代表兩路 K 線均無法取得，數據殘缺不推
    # 這類幣通常是 CG/BingX 均無上架的極小幣，非 API 問題
    # ══════════════════════════════════════════════════════════
    pre_quality = len(all_top)
    all_top = [x for x in all_top if x.get("atr") is not None]
    skipped_no_kline = pre_quality - len(all_top)
    if skipped_no_kline > 0:
        logger.info(f"[品質門撒] 淘汰 {skipped_no_kline} 個 ATR=None（K線無數據小幣），剩餘 {len(all_top)} 個精品訊號")

    # ── CoinGlass-First 架構：不做 BingX 守門過濾，訊號全數保留 ────
    # 訊息中已加入數據源提示，用戶自行確認交易所是否支援
    logger.info(f"[CoinGlass-First] 全部 {len(all_top)} 個訊號進入推播流程（訊號以 CoinGlass 為準）")

    # 用 BingX ticker 取現價 + 24h 成交額（僅標示低流動性與 5 星降星，不再做成交量門檻過濾）
    VOLUME_SOFT_MIN_USD = 500_000      # <500K 才標示低流動性（山寨幣量級調低）
    VOLUME_5STAR_MIN_USD = 500_000     # 5星僅在確認 <500K 時才降為 4星
    for x in all_top:
        sym = x.get("symbol", "")
        clean_base = sym.replace("USDT", "").replace("-", "").upper()
        preferred = base_to_symbol.get(clean_base) if base_to_symbol else None
        if not preferred:
            preferred = base_to_symbol.get(sym.upper()) if base_to_symbol else None
        snap = _fetch_bingx_ticker_snapshot(sym, preferred_symbol=preferred)
        if snap:
            if snap.get("price") is not None:
                snap_price = float(snap["price"])
                kline_price = x.get("current_price")
                # 交叉驗證：若 Ticker 快照與 K 線收盤價差距 > 8%，保留 K 線價（更可靠）
                # 避免 Ticker 瞬間閃崩/快照異常導致 SL/TP 基準錯誤、推播後立即觸損
                if kline_price and isinstance(kline_price, (int, float)) and kline_price > 0:
                    _diff_pct = abs(snap_price - kline_price) / kline_price * 100
                    if _diff_pct > 8.0:
                        logger.warning(
                            f"[現價驗證] {clean_base}: Ticker={snap_price} vs K線={kline_price:.4g} "
                            f"差距={_diff_pct:.1f}% > 8%，保留 K 線價格，捨棄可能異常的 Ticker 快照"
                        )
                        x["data_source_warning"] = True
                    else:
                        x["current_price"] = snap_price
                else:
                    x["current_price"] = snap_price
            vol = snap.get("volume_usd")
            if vol is not None:
                x["low_liquidity_warning"] = vol < VOLUME_SOFT_MIN_USD
                x["volume_usd"] = float(vol)
                # 只在確認量 < 500K 時才降星（山寨幣量級低，不應因無量數據而懲罰）
                if (x.get("stars") or 0) == 5 and vol <= VOLUME_5STAR_MIN_USD:
                    x["stars"] = 4
            else:
                # BingX ticker 未回傳量數據時：不降星，CoinGlass 預篩已過濾超小幣
                x["low_liquidity_warning"] = False
                x["volume_usd"] = 0
        else:
            # BingX ticker 完全失敗時：同上，不降星
            x["low_liquidity_warning"] = False
            x["volume_usd"] = 0
    low_liq_count = sum(1 for x in all_top if x.get("low_liquidity_warning"))
    logger.info(f"本輪 {len(all_top)} 筆進入推播；其中 {low_liq_count} 筆標示低流動性 (<{VOLUME_SOFT_MIN_USD/1e6:.1f}M)")
    if len(all_top) == 0:
        logger.info("本輪 0 筆推播：請看上方「狙擊鏡跳過」或「四類 TOP 候選數」排查（分類未通過 / 冷卻 / 止盈風報比<0.65R）")

    # 冷卻規則：同一幣 4h 內只推一次，不分多空（避免先推多、半小時後又推空同檔）
    # 例：00:02 推 BNLIFE 多 → 00:31 再出現 BNLIFE 空也跳過，不再重複推同幣。
    COOLDOWN_HOURS = 4
    HISTORY_HOURS = 24   # 冷卻歷史保留 24 小時（供 cooldown 與 direction_flip 使用）

    def _item_direction(x: Dict) -> str:
        """只回傳 多/空，冷卻不區分區塊（摸頭/追跌/頭等艙/列車等）。"""
        sig = x.get("signal_label") or ""
        return "多" if ("做多" in sig or "追多" in sig or "嘎空" in sig or "抄底" in sig) else "空"

    def _cooldown_symbol(s: str) -> str:
        """冷卻 key 統一用「幣種基底」比對，避免 BNLIFE / BNLIFEUSDT / BNLIFE-USDT 被當不同幣重複推。"""
        if not s:
            return ""
        return str(s).replace("USDT", "").replace("-", "").replace("_", "").strip().upper()

    # 本輪四類籌碼分類（全表，供出場提示比對）：當初推多→若本輪變 short_open/long_close 即反轉；當初推空→若本輪變 long_open/short_close 即反轉
    current_category_by_base: Dict[str, str] = {}
    for x in long_open:
        b = _cooldown_symbol(x.get("symbol") or "")
        if b:
            current_category_by_base[b] = "long_open"
    for x in long_close:
        b = _cooldown_symbol(x.get("symbol") or "")
        if b:
            current_category_by_base[b] = "long_close"
    for x in short_open:
        b = _cooldown_symbol(x.get("symbol") or "")
        if b:
            current_category_by_base[b] = "short_open"
    for x in short_close:
        b = _cooldown_symbol(x.get("symbol") or "")
        if b:
            current_category_by_base[b] = "short_close"

    # 冷卻檔路徑：cron/雲端環境若 data/ 不持久，可設 SNIPER_COOLDOWN_DIR 指向同一目錄（絕對路徑）
    _cooldown_dir = os.getenv("SNIPER_COOLDOWN_DIR")
    if _cooldown_dir:
        _cooldown_dir = Path(_cooldown_dir).resolve()
        _cooldown_dir.mkdir(parents=True, exist_ok=True)
        SNIPER_COOLDOWN_FILE = _cooldown_dir / "sniper_cooldown.json"
    else:
        SNIPER_COOLDOWN_FILE = (DATA_DIR / "sniper_cooldown.json").resolve()
    _cooldown_path_abs = str(SNIPER_COOLDOWN_FILE)
    # 冷卻 + 推播紀錄改為「單一 JSON」一併讀寫，避免 CI cache 還原時兩檔不一致（冷卻有、推播紀錄 0 筆）
    logger.info(f"狙擊狀態檔路徑（冷卻+推播紀錄）: {_cooldown_path_abs}")
    # 註冊緊急備援路徑，確保 GitHub Action timeout (SIGTERM / atexit) 前能寫回磁碟
    global _emergency_sniper_path, _emergency_sniper_state
    _emergency_sniper_path = _cooldown_path_abs
    now_ts = time.time()
    cooldown_sec = COOLDOWN_HOURS * 3600
    history_sec = HISTORY_HOURS * 3600
    history: List[Dict] = []
    push_log_signals: List[Dict] = []
    # 檔案鎖：避免 CI 或多進程同時寫入導致 JSON 損毀
    lock_file = SNIPER_COOLDOWN_FILE.with_suffix(".lock")

    @contextlib.contextmanager
    def _sniper_file_lock(timeout: float = 10.0, poll_interval: float = 0.2):
        start = time.time()
        while True:
            try:
                fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(fd, str(os.getpid()).encode("utf-8"))
                os.close(fd)
                try:
                    yield
                finally:
                    try:
                        os.unlink(str(lock_file))
                    except FileNotFoundError:
                        pass
                break
            except FileExistsError:
                if time.time() - start > timeout:
                    logger.warning("取得狙擊狀態檔鎖超時，放棄鎖定直接讀寫（可能存在競爭風險）")
                    yield
                    break
                time.sleep(poll_interval + random.uniform(0, poll_interval))

    try:
        with _sniper_file_lock():
            if SNIPER_COOLDOWN_FILE.exists():
                raw = json.loads(SNIPER_COOLDOWN_FILE.read_text(encoding="utf-8"))
                history = raw.get("history") or []
                # 推播紀錄與冷卻同一檔：有 "signals" 就用，無則 []；相容舊版僅有 history
                push_log_signals = raw.get("signals") or []
                # 遷移：狀態檔無 signals 但舊檔存在則讀入一次
                if not push_log_signals:
                    _legacy = SNIPER_COOLDOWN_FILE.parent / "sniper_push_log.json"
                    if _legacy.exists():
                        try:
                            leg = json.loads(_legacy.read_text(encoding="utf-8"))
                            push_log_signals = leg.get("signals") or []
                            if push_log_signals:
                                logger.info(f"已從舊檔遷移推播紀錄 {len(push_log_signals)} 筆（sniper_push_log.json），將併入狀態檔")
                        except Exception:
                            pass
                # 相容舊格式：只有 last_round 時轉成 history（ts 設為 1 小時前，讓本輪仍可能冷卻）
                if not history and raw.get("last_round"):
                    last_round = raw.get("last_round") or []
                    if last_round and isinstance(last_round[0], dict):
                        history = [{"symbol": str(p.get("symbol")), "dir": str(p.get("dir")), "ts": int(now_ts) - 3600} for p in last_round if p.get("symbol") and p.get("dir")]
                    else:
                        history = [{"symbol": str(p[0]), "dir": str(p[1]), "ts": int(now_ts) - 3600} for p in last_round if isinstance(p, (list, tuple)) and len(p) >= 2]
                _in_window = sum(1 for e in history if isinstance(e, dict) and (now_ts - e.get("ts", 0)) <= cooldown_sec)
                logger.info(f"冷卻檔已讀取: {_cooldown_path_abs} | 歷史 {len(history)} 筆，{COOLDOWN_HOURS}h 內 {_in_window} 筆 | 推播紀錄 {len(push_log_signals)} 筆 -> 同幣同方向才冷卻（換方向可推）")
            else:
                logger.info(f"狀態檔不存在，本輪無冷卻限制、無推播紀錄: {_cooldown_path_abs}")
                # 遷移：若舊版 sniper_push_log.json 存在，讀入一次併入本輪 state，下次寫回即合併
                _legacy_push = SNIPER_COOLDOWN_FILE.parent / "sniper_push_log.json"
                if _legacy_push.exists():
                    try:
                        leg = json.loads(_legacy_push.read_text(encoding="utf-8"))
                        push_log_signals = leg.get("signals") or []
                        if push_log_signals:
                            logger.info(f"已從舊檔遷移推播紀錄 {len(push_log_signals)} 筆（sniper_push_log.json），將併入狀態檔")
                    except Exception:
                        pass
    except Exception as e:
        history = []
        push_log_signals = []
        logger.warning(f"讀取狀態檔失敗，本輪無冷卻限制、無推播紀錄: {e}")
    PUSH_LOG_RETENTION_HOURS = 48
    EXIT_CHECK_WINDOW_HOURS = 48
    # 讀檔後先做一次 retention 清理，避免舊訊號長期堆積
    if push_log_signals:
        def _retain_for_read(e: Dict[str, Any]) -> bool:
            if not isinstance(e, dict):
                return False
            t = e.get("ts") or 0
            ct = e.get("closed_ts") or t
            if e.get("closed"):
                return (now_ts - ct) <= PUSH_LOG_RETENTION_HOURS * 3600
            return (now_ts - t) <= PUSH_LOG_RETENTION_HOURS * 3600

        before_clean = len(push_log_signals)
        push_log_signals = [e for e in push_log_signals if _retain_for_read(e)]
        after_clean = len(push_log_signals)
        if before_clean != after_clean:
            logger.info(f"推播紀錄讀取後已清理過期訊號: 由 {before_clean} 筆壓縮為 {after_clean} 筆 (保留 {PUSH_LOG_RETENTION_HOURS}h)")

    # 每日 00:00 那一輪（台灣時間 0:00~0:29）發送「昨日」績效總結
    now_tw = datetime.fromtimestamp(now_ts, tz=TAIPEI_TZ)
    if now_tw.hour == 0 and now_tw.minute < 30:
        summary_date = (now_tw - timedelta(days=1)).date()

        # ── 防重複發送：檢查 last_summary_date.json ────────────────────────────
        _last_summary_file = DATA_DIR / "last_summary_date.json"
        _last_summary_data = load_json_file(_last_summary_file, default={})
        _last_sent_str = _last_summary_data.get("last_sent_date", "")
        if _last_sent_str == str(summary_date):
            logger.info(f"每日績效總結今日已發送過（{summary_date}），跳過重複推播")
        else:
            pushed_today = [e for e in push_log_signals if isinstance(e, dict) and e.get("ts") and datetime.fromtimestamp(e["ts"], tz=TAIPEI_TZ).date() == summary_date]
            closed_today = [e for e in push_log_signals if isinstance(e, dict) and e.get("closed") and e.get("closed_ts") and datetime.fromtimestamp(e["closed_ts"], tz=TAIPEI_TZ).date() == summary_date]
            # 勝負只計「止盈(tp1/tp1_sl) / 止損(sl)」，timeout / reversal 視為平局，不納入分母
            n_sl = sum(1 for e in closed_today if e.get("exit_reason") == "sl")
            n_tp1 = sum(1 for e in closed_today if e.get("exit_reason") in ("tp1", "tp1_sl"))
            n_timeout = sum(1 for e in closed_today if e.get("exit_reason") == "timeout")
            n_reversal = 0
            n_win = n_tp1
            n_closed = len(closed_today)
            n_pushed = len(pushed_today)
            win_rate = (n_win / (n_win + n_sl) * 100) if (n_win + n_sl) else None
            # R 統計：以各單 realized_R 加總
            sum_r = sum(
                float(e.get("realized_R") or 0.0)
                for e in closed_today
                if isinstance(e, dict)
            )
            sum_r_win = sum(
                float(e.get("realized_R") or 0.0)
                for e in closed_today
                if isinstance(e, dict) and (e.get("realized_R") or 0.0) > 0
            )
            sum_r_loss = sum(
                float(e.get("realized_R") or 0.0)
                for e in closed_today
                if isinstance(e, dict) and (e.get("realized_R") or 0.0) < 0
            )

            # ── 週/月累積 R 值：從 performance_history.json 讀取歷史日資料 ──
            _perf_hist_file = DATA_DIR / "performance_history.json"
            _perf_hist: List[Dict] = load_json_file(_perf_hist_file, default=[])
            if not isinstance(_perf_hist, list):
                _perf_hist = []
            # 寫入今日資料（先移除同日舊記錄再追加）
            _perf_hist = [r for r in _perf_hist if isinstance(r, dict) and r.get("date") != str(summary_date)]
            _perf_hist.append({
                "date": str(summary_date),
                "net_r": round(sum_r, 4),
                "n_win": n_win,
                "n_sl": n_sl,
                "n_pushed": n_pushed,
            })
            # 只保留最近 35 天
            _perf_hist = sorted(_perf_hist, key=lambda r: r.get("date", ""))[-35:]
            save_json_file(_perf_hist_file, _perf_hist)

            # 計算週累積（最近 7 筆）與月累積（最近 30 筆）
            _week_records = _perf_hist[-7:] if len(_perf_hist) >= 7 else _perf_hist
            _month_records = _perf_hist[-30:] if len(_perf_hist) >= 30 else _perf_hist
            sum_r_week = sum(float(r.get("net_r") or 0.0) for r in _week_records)
            sum_r_month = sum(float(r.get("net_r") or 0.0) for r in _month_records)
            week_days = len(_week_records)
            month_days = len(_month_records)

            # ── 動態戰報標題：正/負 R 值給不同開頭 ──────────────────────────
            if sum_r >= 0:
                battle_header = f"🏆 【昨日戰報：船長帶隊穩定收割】"
            else:
                battle_header = f"🛡️ 【昨日戰報：風控機制有效保護本金】"

            # 分級工具：依 tier / stars 判斷列車 / 賭鬼 / 飛機
            def _entry_tier(e: Dict[str, Any]) -> str:
                t = (e.get("tier") or "").strip()
                if t:
                    return t
                stars_val = e.get("stars")
                try:
                    stars_val = int(stars_val) if stars_val is not None else None
                except (TypeError, ValueError):
                    stars_val = None
                if stars_val is not None and stars_val >= 5:
                    return "train"
                if stars_val is not None and stars_val < 5:
                    return "gambler"
                return "unknown"

            summary_lines = [
                f"*{battle_header}*",
                f"📊 *【{summary_date} 每日績效總結】*",
                f"",
                f"📤 當日推播：{n_pushed} 單",
                f"✅ 已結案：{n_closed} 單（止盈 {n_tp1}｜止損 {n_sl}｜超時撤退 {n_timeout}）",
                f"🎯 R 統計：贏 {sum_r_win:.2f}R｜輸 {sum_r_loss:.2f}R｜淨 {sum_r:.2f}R",
                f"※ 止盈算贏、止損算輸（timeout 視為平局）→ {n_win} 贏 / {n_sl} 輸",
            ]
            if win_rate is not None:
                summary_lines.append(f"📈 整體勝率：{win_rate:.1f}%")
            summary_lines.append(f"")
            # 週/月累積 R 值
            week_sign = "+" if sum_r_week >= 0 else ""
            month_sign = "+" if sum_r_month >= 0 else ""
            summary_lines.append(f"📅 *複利統計（累積 R 值）*")
            summary_lines.append(f"　📆 近 {week_days} 日（週）：{week_sign}{sum_r_week:.2f}R")
            summary_lines.append(f"　🗓️ 近 {month_days} 日（月）：{month_sign}{sum_r_month:.2f}R")
            summary_lines.append(f"")
            # 依等級（飛機/列車/賭鬼）細分績效
            tier_defs = [
                ("elite", "✈️ 飛機 (S+)"),
                ("train", "🚅 列車 (S)"),
                ("gambler", "👻 賭鬼 (A)"),
            ]
            for tier_key, tier_label in tier_defs:
                pushed_t = [e for e in pushed_today if isinstance(e, dict) and _entry_tier(e) == tier_key]
                closed_t = [e for e in closed_today if isinstance(e, dict) and _entry_tier(e) == tier_key]
                if not pushed_t and not closed_t:
                    continue
                n_sl_t = sum(1 for e in closed_t if e.get("exit_reason") == "sl")
                n_tp1_t = sum(1 for e in closed_t if e.get("exit_reason") in ("tp1", "tp1_sl"))
                win_rate_t = (n_tp1_t / (n_tp1_t + n_sl_t) * 100) if (n_tp1_t + n_sl_t) else None
                sum_r_t = sum(float(e.get("realized_R") or 0.0) for e in closed_t)
                wr_str_t = f"{win_rate_t:.1f}%" if win_rate_t is not None else "-"
                if tier_key == "gambler":
                    # 賭鬼：額外統計 TP2 命中次數與理論 R
                    n_tp2_t = sum(
                        1 for e in closed_t
                        if e.get("tp2_hit") and (e.get("tp2_R") is not None)
                    )
                    sum_tp2_r_t = sum(
                        float(e.get("tp2_R") or 0.0)
                        for e in closed_t
                        if e.get("tp2_hit") and (e.get("tp2_R") is not None)
                    )
                    summary_lines.append(
                        f"{tier_label}：TP1 {n_tp1_t} 單｜TP2 命中 {n_tp2_t} 單（理論 +{sum_tp2_r_t:.2f}R）｜淨 {sum_r_t:.2f}R"
                    )
                else:
                    summary_lines.append(
                        f"{tier_label}：推播 {len(pushed_t)}｜結案 {len(closed_t)}（止盈 {n_tp1_t}｜止損 {n_sl_t}）"
                        f" 勝率 {wr_str_t}｜淨 {sum_r_t:.2f}R"
                    )
            summary_lines.append(f"")
            # 各分類（多單/空單）勝率
            for _dir, _label in (("多", "多單"), ("空", "空單")):
                p_d = [e for e in pushed_today if (e.get("dir") or "").strip() == _dir]
                c_d = [e for e in closed_today if (e.get("dir") or "").strip() == _dir]
                sl_d = sum(1 for e in c_d if e.get("exit_reason") == "sl")
                tp_win_d = sum(1 for e in c_d if e.get("exit_reason") in ("tp1", "tp1_sl"))
                w_d = tp_win_d + sl_d
                wr_d = (tp_win_d / w_d * 100) if w_d else None
                _wr_str = f"{wr_d:.1f}%" if wr_d is not None else "-"
                summary_lines.append(f"　{_label}：推播 {len(p_d)}｜結案 {len(c_d)}（止盈 {tp_win_d}｜止損 {sl_d}）勝率 {_wr_str}")
            summary_lines.append(f"")
            summary_lines.append(f"🕐 {now_tw.strftime('%Y-%m-%d %H:%M')} 台灣")
            send_telegram_message("\n".join(summary_lines), TG_THREAD_IDS["position_change"], parse_mode="Markdown")
            # 記錄已發送，防止本輪後續迭代重複推播
            save_json_file(_last_summary_file, {"last_sent_date": str(summary_date)})
            logger.info(f"每日績效總結已發送: {summary_date} 推播 {n_pushed} 結案 {n_closed} 止盈 {n_tp1} 止損 {n_sl} 超時 {n_timeout} 勝率 {win_rate}% | 週R={sum_r_week:.2f} 月R={sum_r_month:.2f}")
    # 倉位追蹤依賴「上一輪（及之前）寫入的推播紀錄」；若 data 目錄在排程間未持久化（如 CI 無 cache），此處會一直是 0 筆
    in_window = [
        e for e in push_log_signals
        if isinstance(e, dict) and not e.get("notified_exit") and (e.get("symbol") or "").strip()
        and (now_ts - (e.get("ts") or 0)) <= EXIT_CHECK_WINDOW_HOURS * 3600
    ]
    logger.info(f"推播紀錄: 共 {len(push_log_signals)} 筆，48h 內且未結案 {len(in_window)} 筆待追蹤 (倉位追蹤需 data 目錄在排程間持久化)")
    if len(push_log_signals) == 0 and len(history) > 0:
        logger.warning("推播紀錄為 0 筆但冷卻有歷史 → 若曾推播過，請確認 workflow 的 data 目錄已正確 cache/還原，否則每輪從空檔開始、無法倉位追蹤")
    logger.info(f"【倉位追蹤】本輪待追蹤 {len(in_window)} 筆歷史訊號 (48h 內未結案)，開始檢查 SL/TP…")
    exit_notified_set: Set[str] = set()
    # SL 觸發時輪播勵志文案（每次隨機選一段）
    SL_STOPLOSS_COPY = [
        "翻身打一場漂亮仗，加油兄弟！哪有小孩天天哭？哪有男人天天輸？你要不要好好想想你來到這世上是為了什麼？",
        "人要相信運氣，放平心態，父母的養老金，家裡的房子，老婆的首飾，孩子的學費，朋友的轉帳，網貸高利貸，都是咱們上岸的資本。",
        "哥們你記住，你的腎、肺都有兩個，但你有一個就能活。",
        "運鈔車保全第一發都是空包彈，人們都在銀行存錢，屁股不止可以拉屎，你輸了只是把錢給人家，但你放棄了就是真的輸了。",
        "人們都說十賭九輸，你現在已經九次輸了。你再博一把就可以收回來，如果你還想東山再起，那我還看得起你；你現在放棄了，我就真的看不起你了。你記住，一顆腎、一個角膜、一個肺都能活，可偏偏上天給了你兩個。",
        "兄弟，如果你說你需要200最後一舞，我會毫不猶豫發給你；但是你跟我說借200坐車要去進廠打工，對不起，我的錢不會借給懦夫，你太讓我失望了。",
    ]
    for entry in push_log_signals:
        if not isinstance(entry, dict) or entry.get("notified_exit"):
            continue
        sym_base = (entry.get("symbol") or "").strip().upper()
        if not sym_base:
            continue
        pushed_ts = entry.get("ts") or 0
        if (now_ts - pushed_ts) > EXIT_CHECK_WINDOW_HOURS * 3600:
            continue
        pushed_dir = (entry.get("dir") or "").strip()
        pushed_at_tw = entry.get("pushed_at_tw") or datetime.fromtimestamp(pushed_ts, tz=TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
        dir_label = "多單" if pushed_dir == "多" else "空單"

        # 1) 價格追蹤：觸及 SL 或 TP 即結案（以 BingX 30m K 線 high/low 判斷是否曾經打到價位）
        sl_level = entry.get("sl")
        tp1_level = entry.get("tp1")
        tp2_level = entry.get("tp2")
        price_closed = False
        # 初始化 cur_price，確保後續 3h 預警邏輯不會觸發 UnboundLocalError
        cur_price: Optional[float] = None

        if sl_level is not None or tp1_level is not None:
            full_sym = entry.get("full_symbol") or f"{sym_base}USDT"
            # 優先使用 BingX 30m K 線本地計算（可取得當前 K 線的 high/low，避免瞬間插針被漏判）
            kline_tech = _fetch_bingx_klines_and_calc(full_sym, preferred_symbol=None)
            cur_price = None
            kline_high = None
            kline_low = None
            recent_high_2h = None
            recent_low_2h = None
            if kline_tech:
                try:
                    cur_price = float(kline_tech.get("current_price")) if kline_tech.get("current_price") is not None else None
                except (TypeError, ValueError):
                    cur_price = None
                try:
                    kline_high = float(kline_tech.get("last_kline_high_30m")) if kline_tech.get("last_kline_high_30m") is not None else None
                except (TypeError, ValueError):
                    kline_high = None
                try:
                    kline_low = float(kline_tech.get("last_kline_low_30m")) if kline_tech.get("last_kline_low_30m") is not None else None
                except (TypeError, ValueError):
                    kline_low = None
                try:
                    recent_high_2h = float(kline_tech.get("recent_high_2h")) if kline_tech.get("recent_high_2h") is not None else None
                except (TypeError, ValueError):
                    recent_high_2h = None
                try:
                    recent_low_2h = float(kline_tech.get("recent_low_2h")) if kline_tech.get("recent_low_2h") is not None else None
                except (TypeError, ValueError):
                    recent_low_2h = None
                logger.info(
                    f"【倉位追蹤K線】{sym_base} full_symbol={full_sym} "
                    f"cur_price={cur_price} last_high_30m={kline_high} last_low_30m={kline_low} "
                    f"recent_2h=({recent_high_2h},{recent_low_2h})"
                )

            # ── 進場價 2h 範圍保護（最強防護）───────────────────────────────────
            # 若進場價完全超出近2小時K線高低點 5% 以外，代表進場價是假數據（CoinGlass 瞬間異常）
            # → 靜默取消，不記 -R
            _entry_price_raw = entry.get("entry_price")
            if _entry_price_raw and isinstance(_entry_price_raw, (int, float)) and _entry_price_raw > 0:
                _ep = float(_entry_price_raw)
                _is_long_pos = pushed_dir == "多"
                _outside_range = False
                if recent_high_2h and recent_low_2h and recent_high_2h > 0 and recent_low_2h > 0:
                    if _is_long_pos and _ep > recent_high_2h * 1.05:
                        _outside_range = True
                    elif (not _is_long_pos) and _ep < recent_low_2h * 0.95:
                        _outside_range = True
                if _outside_range:
                    logger.warning(
                        f"[進場價範圍保護] {sym_base}: 進場={_ep} 但近2h高低=({recent_high_2h},{recent_low_2h})，"
                        f"進場價在BingX從未成交，判定為 CoinGlass 假數據，靜默取消"
                    )
                    entry["notified_exit"] = True
                    entry["closed"] = True
                    entry["exit_reason"] = "entry_price_error"
                    continue
            # 若 K 線不可用，退回到 ticker 快照（確保不會整體失效）
            if kline_tech is None or cur_price is None:
                if kline_tech is None:
                    logger.warning(f"倉位追蹤 K 線取得失敗，改用 ticker 快照取價: {sym_base} full_symbol={full_sym}")
                snap = _fetch_bingx_ticker_snapshot(full_sym, preferred_symbol=None)
                if snap and snap.get("price") is not None:
                    try:
                        cur_price = float(snap.get("price"))
                    except (TypeError, ValueError):
                        cur_price = None
                if cur_price is None:
                    # K 線與快照均失敗 → 記錄並跳過本輪比對，避免任何 UnboundLocalError
                    logger.info(
                        f"[倉位追蹤] {sym_base} 本輪 BingX 取價完全失敗（K 線與快照均無效），"
                        f"跳過 SL/TP 比對，等待下一輪重試"
                    )
                    continue
                else:
                    logger.info(f"【倉位追蹤快照】{sym_base} full_symbol={full_sym} cur_price={cur_price}")

            # 僅以當下快照價/收盤價檢查是否觸及 SL/TP/TP2（不再使用 30m K 線 high/low 避免提前誤判）
            if cur_price is not None:
                is_long = pushed_dir == "多"
                hit_sl = False
                hit_tp = False
                hit_tp2 = False
                # SL 觸發條件：全系統統一僅使用當下快照價，避免 30m high 包含暴跌前價格而提前停損
                if sl_level is not None:
                    if is_long and cur_price <= sl_level:
                        hit_sl = True
                    elif (not is_long) and cur_price >= sl_level:
                        hit_sl = True

                # ── 進場價異常保護：推播後 10 分鐘內觸損 + 現價與 SL 方向差距 > 2倍 SL 距離
                # 代表進場價是異常 Ticker 快照（閃崩/stale），而非市場真的突破 SL
                # → 不記 -1R，直接取消這筆紀錄
                if hit_sl and sl_level is not None and pushed_ts and (now_ts - pushed_ts) < 1800:
                    _entry_price = entry.get("entry_price")
                    if _entry_price and isinstance(_entry_price, (int, float)) and _entry_price > 0:
                        _sl_dist = abs(_entry_price - sl_level)
                        _cur_dist = abs(cur_price - _entry_price)
                        if _sl_dist > 0 and _cur_dist > _sl_dist * 2:
                            logger.warning(
                                f"[進場價異常保護] {sym_base}: 推播後 {int(now_ts-pushed_ts)}s 即觸損，"
                                f"現價={cur_price} 進場={_entry_price} SL={sl_level} "
                                f"距離={_cur_dist:.4g} > 2×SL={_sl_dist*2:.4g}，判定進場價異常，取消此筆"
                            )
                            entry["notified_exit"] = True
                            entry["closed"] = True
                            entry["exit_reason"] = "entry_price_error"
                            hit_sl = False
                # TP1 觸發條件：只用「收盤價/現價」達標才算，避免插針後收回仍誤發 TP1 達標
                if tp1_level is not None:
                    if is_long and cur_price >= tp1_level:
                        hit_tp = True
                    elif (not is_long) and cur_price <= tp1_level:
                        hit_tp = True

                # TP2 觸發條件：同上，僅收盤價/現價達標才統計
                if tp2_level is not None:
                    if is_long and cur_price >= tp2_level:
                        hit_tp2 = True
                    elif (not is_long) and cur_price <= tp2_level:
                        hit_tp2 = True

                logger.info(
                    f"【倉位追蹤比對】{sym_base} dir={pushed_dir} "
                    f"sl={sl_level} tp1={tp1_level} tp2={tp2_level} "
                    f"cur_price={cur_price} high_30m={kline_high} low_30m={kline_low} "
                    f"hit_sl={hit_sl} hit_tp={hit_tp} hit_tp2={hit_tp2}"
                )

                # 1-1) 先檢查止損：一旦觸及 SL 即結案（賭鬼若已吃過 TP1，視為整體獲利結束）
                if sl_level is not None:
                    if hit_sl:
                        if entry.get("tp1_notified"):
                            # 賭鬼單已先落袋 TP1，再被 SL 出場 → 整體視為獲利結案
                            _cur_price_str = f"`{cur_price}`" if cur_price is not None else "N/A"
                            exit_msg = (
                                f"⚠️ *【剩餘倉位出場・整體獲利結案】*\n"
                                f"台灣時間 *{pushed_at_tw}* 推的 *{dir_label}* 標的 `{sym_base}` 價格回落觸及防守價。\n"
                                f"📍 觸發時現價：{_cur_price_str}（15分鐘K線收盤/快照）\n"
                                f"由於已落袋 TP1 (60%)，此單整體依然獲利！完美結束！"
                            )
                            send_telegram_message(exit_msg, TG_THREAD_IDS["position_change"], parse_mode="Markdown")
                            entry["notified_exit"] = True
                            entry["closed"] = True
                            entry["exit_reason"] = "tp1_sl"
                            base_r = entry.get("r_tp1")
                            try:
                                base_r = float(base_r) if base_r is not None else 1.0
                            except (TypeError, ValueError):
                                base_r = 1.0
                            # 假設 TP1 已落袋 60% 倉位、剩餘 40% 約在保本附近 → 整體約 +0.6R
                            entry["realized_R"] = round(0.6 * base_r, 3)
                            entry["closed_ts"] = int(now_ts)
                            exit_notified_set.add(sym_base)
                            price_closed = True
                            logger.info(f"倉位追蹤已發送: {sym_base} 剩餘倉位觸及止損 (tp1_sl，整體獲利結案，realized_R={entry.get('realized_R')})")
                        else:
                            sl_copy = random.choice(SL_STOPLOSS_COPY)
                            _sl_src = entry.get("sl_source") or "結構防守"
                            _sl_dir_desc = (
                                f"價格跌破 `{sl_level}` {_sl_src}底線，多方結構破壞。"
                                if is_long else
                                f"價格突破 `{sl_level}` {_sl_src}壓力，空方結構破壞。"
                            )
                            _cur_price_str = f"`{cur_price}`" if cur_price is not None else "N/A"
                            exit_msg = (
                                f"⚠️ *【已觸發止損・本倉結案】*\n"
                                f"台灣時間 *{pushed_at_tw}* 推的 *{dir_label}* 標的 `{sym_base}`\n"
                                f"止損觸發：{_sl_dir_desc}\n"
                                f"📍 觸發時現價：{_cur_price_str}（15分鐘K線收盤/快照）\n"
                                f"本次 R：`-1.0R`（控管得當，止損機制正常運作）\n\n{sl_copy}"
                            )
                            send_telegram_message(exit_msg, TG_THREAD_IDS["position_change"], parse_mode="Markdown")
                            entry["notified_exit"] = True
                            entry["closed"] = True
                            entry["exit_reason"] = "sl"
                            # 止損一律視為 -1R（風險單位）
                            entry["realized_R"] = -1.0
                            entry["closed_ts"] = int(now_ts)
                            exit_notified_set.add(sym_base)
                            price_closed = True
                            logger.info(f"倉位追蹤已發送: {sym_base} 觸發止損 (本倉結案，realized_R={entry.get('realized_R')})")
                # 1-2) 若未止損，檢查止盈：列車達 TP1 即結案；賭鬼達 TP1 僅建議部位減倉，不結案
                tp1_lbl = entry.get("tp1_label") or "主力成本"
                if not price_closed and tp1_level is not None:
                    if hit_tp:
                        stars_val = entry.get("stars", 5)
                        if stars_val < 5 and not entry.get("tp1_notified"):
                            # 賭鬼單：TP1 達標僅提醒先落袋 60%，剩餘 40% 改為保本移動止損，不立即結案
                            # 先計算保本價，用於訊息顯示
                            be_price = entry.get("entry_price")
                            try:
                                be_price_f = float(be_price) if be_price is not None else None
                            except (TypeError, ValueError):
                                be_price_f = None
                            be_str = f"`{be_price_f}`" if be_price_f else "進場價"
                            _locked_time = datetime.now(TAIPEI_TZ).strftime("%H:%M")
                            _cur_price_str_shield = f"`{cur_price}`" if cur_price is not None else "N/A"
                            tp_msg = (
                                f"🛡️ *【盾牌啟動・此單已零風險】*\n"
                                f"✅ TP1 達標 | 台灣時間 *{pushed_at_tw}* 推的賭鬼 *{dir_label}* 標的 `{sym_base}`\n"
                                f"📍 觸發時現價：{_cur_price_str_shield}（15分鐘K線收盤/快照）\n"
                                f"TP1 `{tp1_level}` 已觸及，恭喜入袋為安！\n"
                                f"✅ *已鎖定利潤* `{_locked_time}` 台灣\n\n"
                                f"📌 *建議立即操作：*\n"
                                f"  1️⃣ 先獲利了結 *60%* 倉位\n"
                                f"  2️⃣ 剩餘 40%：將止損移動至進場價 {be_str}（保本）\n"
                                f"  3️⃣ 不設止盈，順著主力移動止損讓利潤奔跑！\n\n"
                                f"⚡ 此單現已零風險，剩餘倉位輸了也不虧，贏了是純利！"
                            )
                            send_telegram_message(tp_msg, TG_THREAD_IDS["position_change"], parse_mode="Markdown")
                            entry["tp1_notified"] = True
                            # 將追蹤止損上移/下移至進場價（保本），若無 entry_price 則維持原 SL
                            if be_price_f is not None and be_price_f > 0:
                                entry["sl"] = be_price_f
                                logger.info(f"賭鬼單 {sym_base} TP1 達標，止損自動移動至保本價 {be_price_f}")
                            exit_notified_set.add(sym_base)
                            logger.info(f"倉位追蹤已發送: {sym_base} 賭鬼 TP1 達標 (盾牌啟動，不結案，保本價={be_price_f})")
                        else:
                            # 列車單或已通知過 TP1 的賭鬼：維持原本 TP1 結案邏輯
                            _tp_reason = "主力成本對稱達標" if tp1_lbl == "主力成本" else "波動如預期(ATR)達標"
                            _cur_price_str = f"`{cur_price}`" if cur_price is not None else "N/A"
                            exit_msg = (
                                f"✅ *【已達止盈・本倉完結】*\n"
                                f"台灣時間 *{pushed_at_tw}* 推的 *{dir_label}* 標的 `{sym_base}` 已達止盈({tp1_lbl}) `{tp1_level}`。\n"
                                f"📍 觸發時現價：{_cur_price_str}（15分鐘K線收盤/快照）\n"
                                f"原因：{_tp_reason}，本倉結案。"
                            )
                            send_telegram_message(exit_msg, TG_THREAD_IDS["position_change"], parse_mode="Markdown")
                            entry["notified_exit"] = True
                            entry["closed"] = True
                            entry["exit_reason"] = "tp1"
                            base_r = entry.get("r_tp1")
                            try:
                                base_r = float(base_r) if base_r is not None else 1.0
                            except (TypeError, ValueError):
                                base_r = 1.0
                            entry["realized_R"] = round(base_r, 3)
                            entry["closed_ts"] = int(now_ts)
                            exit_notified_set.add(sym_base)
                            price_closed = True
                            logger.info(f"倉位追蹤已發送: {sym_base} 已達止盈({tp1_lbl}) (本倉完結，realized_R={entry.get('realized_R')})")

                # 1-2-延伸) 賭鬼 TP2 命中：視為滿貫結案（先 TP1 再 TP2）
                if not price_closed and tp2_level is not None and hit_tp2:
                    stars_val = entry.get("stars", 5)
                    if stars_val < 5:
                        r_tp2 = entry.get("r_tp2")
                        try:
                            r_tp2_val = float(r_tp2) if r_tp2 is not None else None
                        except (TypeError, ValueError):
                            r_tp2_val = None
                        _cur_price_str_tp2 = f"`{cur_price}`" if cur_price is not None else "N/A"
                        exit_msg = (
                            f"🎯 *【TP2 滿貫結案】*\n"
                            f"台灣時間 *{pushed_at_tw}* 推的賭鬼 *{dir_label}* 標的 `{sym_base}` 價格已觸及 TP2 `{tp2_level}`。\n"
                            f"📍 觸發時現價：{_cur_price_str_tp2}（15分鐘K線收盤/快照）\n"
                            f"本單已完成 TP1 + TP2 滿貫，建議全數了結部位。"
                        )
                        send_telegram_message(exit_msg, TG_THREAD_IDS["position_change"], parse_mode="Markdown")
                        entry["notified_exit"] = True
                        entry["closed"] = True
                        entry["exit_reason"] = "tp2"
                        if r_tp2_val is not None:
                            entry["realized_R"] = round(r_tp2_val, 3)
                        else:
                            entry["realized_R"] = entry.get("realized_R") or 0.0
                        entry["closed_ts"] = int(now_ts)
                        exit_notified_set.add(sym_base)
                        price_closed = True
                        logger.info(f"倉位追蹤已發送: {sym_base} 賭鬼 TP2 滿貫結案 (realized_R={entry.get('realized_R')})")
                if not price_closed:
                    logger.info(
                        f"比對價格: {sym_base} 現價 {cur_price} | "
                        f"K線高低 ({kline_high}, {kline_low}) | 止損 {sl_level} 止盈 {tp1_level} TP2 {tp2_level} -> 未觸發"
                    )
        else:
            # 紀錄缺 sl/tp 欄位（舊格式或寫入時無價位）
            # 嘗試根據進場點補算 10% 硬性止損位，確保追蹤不中斷
            _entry_ref = entry.get("entry_price")
            _is_long_ref = pushed_dir == "多"
            _sl_fallback_added = False
            if _entry_ref:
                try:
                    _ep_f = float(_entry_ref)
                    if _ep_f > 0:
                        _sl_fb = round(_ep_f * 0.90, 8) if _is_long_ref else round(_ep_f * 1.10, 8)
                        entry["sl"] = _sl_fb
                        # 同補一個 5% TP1
                        _tp_fb = round(_ep_f * 1.05, 8) if _is_long_ref else round(_ep_f * 0.95, 8)
                        entry["tp1"] = _tp_fb
                        sl_level = _sl_fb
                        tp1_level = _tp_fb
                        _sl_fallback_added = True
                        logger.info(
                            f"[自動補算SL] {sym_base} 進場={_ep_f} → SL={_sl_fb}(10%) TP1={_tp_fb}(5%) "
                            f"[{'多' if _is_long_ref else '空'}單 硬性防守]"
                        )
                except (TypeError, ValueError):
                    pass

            if not _sl_fallback_added:
                logger.info(f"比對價格: {sym_base} 無止損/止盈價位可比對（紀錄缺 sl/tp 且無進場價），本輪僅做籌碼追蹤")

            # 嘗試取得現價供 3h 預警使用
            _full_sym_fb = entry.get("full_symbol") or f"{sym_base}USDT"
            _snap_fb = _fetch_bingx_ticker_snapshot(_full_sym_fb, preferred_symbol=None)
            if _snap_fb and _snap_fb.get("price") is not None:
                try:
                    cur_price = float(_snap_fb["price"])
                except (TypeError, ValueError):
                    cur_price = None

        # 1-3a) 中期預警：持倉超過 3 小時（12 根 15m K 線）仍未達 TP2，且 TP1 也未觸及 → 動能可疑
        _3h_elapsed = pushed_ts and (now_ts - pushed_ts) >= 3 * 3600
        if (
            not entry.get("closed")
            and not entry.get("time_warned_3h")
            and _3h_elapsed
            and not entry.get("tp1_notified")
        ):
            _elapsed_bars = int((now_ts - pushed_ts) / 900)  # 15m = 900s
            _cur_str = f"`{cur_price}`" if cur_price else "（無最新報價）"
            warn_3h_msg = (
                f"⏳ *【耗時預警・建議提高警惕】*\n"
                f"台灣時間 *{pushed_at_tw}* 推的 *{dir_label}* 標的 `{sym_base}`\n"
                f"已過 *{_elapsed_bars} 根 15m K 線*，TP1 尚未達標，現價 {_cur_str}。\n\n"
                f"📌 *建議操作：*\n"
                f"  • 若現價距止損 < 1%，建議直接保本平倉\n"
                f"  • 若仍有浮盈，請將止損提升至進場價以上（保本）\n"
                f"  • 動能延遲可能表示籌碼已換手，謹慎加倉\n\n"
                f"⚡️ `[15M 閃電監控中]`"
            )
            send_telegram_message(warn_3h_msg, TG_THREAD_IDS["position_change"], parse_mode="Markdown")
            entry["time_warned_3h"] = True
            logger.info(f"倉位追蹤已發送: {sym_base} 3小時耗時預警 ({_elapsed_bars} 根 K 線未達 TP1)")

        # 1-3b) 時間衰竭：持倉超過 24 小時仍未達標，建議保本/小虧出場
        if (not entry.get("closed")) and pushed_ts and (now_ts - pushed_ts) >= 24 * 3600:
            timeout_msg = (
                f"⏳ *【動能衰竭・建議保本平倉】*\n"
                f"標的 `{sym_base}` 已持倉超過 24 小時未達目標，主力動能減弱，"
                f"建議原價或小虧平倉，本倉結案。\n\n"
                f"⚡️ `[15M 閃電監控中]`"
            )
            send_telegram_message(timeout_msg, TG_THREAD_IDS["position_change"], parse_mode="Markdown")
            entry["notified_exit"] = True
            entry["closed"] = True
            entry["exit_reason"] = "timeout"
            entry["realized_R"] = 0.0
            entry["closed_ts"] = int(now_ts)
            exit_notified_set.add(sym_base)
            logger.info(f"倉位追蹤已發送: {sym_base} 動能衰竭超過24小時，建議保本/小虧出場 (本倉結案)")

        # 價格已結案（SL / TP / timeout），跳過
        if entry.get("closed"):
            continue

        # 每輪反查「進場理由還再不再」並寫入 LOG（肉眼可見追蹤）
        cur_cat = current_category_by_base.get(sym_base)
        entry_cat = entry.get("entry_category") or "-"
        cur_label = cur_cat if cur_cat else "未入四類"
        if not cur_cat:
            reason_status = "未入四類(觀察)"
        elif (pushed_dir == "多" and cur_cat in ("short_open", "long_close")) or (pushed_dir == "空" and cur_cat in ("long_open", "short_close")):
            reason_status = "反轉"
        elif (pushed_dir == "多" and cur_cat in ("long_open", "short_close")) or (pushed_dir == "空" and cur_cat in ("short_open", "long_close")):
            reason_status = "仍在"
        else:
            reason_status = "弱化"
        logger.info(f"【進場理由】{sym_base} 當初 {entry_cat} 本輪 {cur_label} -> {reason_status}")

    if exit_notified_set:
        try:
            SNIPER_COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
            state = {"history": history, "signals": push_log_signals}
            _emergency_sniper_state = state  # 同步緊急備援快照
            with _sniper_file_lock():
                save_json_file(SNIPER_COOLDOWN_FILE, state)
        except Exception as e:
            logger.warning(f"寫入狀態檔(出場標記)失敗: {e}")
    # 肉眼可見：本輪倉位追蹤結果摘要
    if exit_notified_set:
        logger.info(f"【倉位追蹤】本輪檢查完成，共發送 {len(exit_notified_set)} 則推播 → 標的: {', '.join(sorted(exit_notified_set))}")
    else:
        logger.info(f"【倉位追蹤】本輪檢查完成，無觸發 (待追蹤 {len(in_window)} 筆均未達 SL/TP)")
    # 冷卻：改為「綁定上一單結案狀態」的智慧冷卻：
    # - A 還在車上（未 closed）：同幣阻擋新推播，僅做同向加強/反轉提醒。
    # - B 已結案且 exit_reason in {tp1,tp2}：視為主力開下一車，無視 4h 冷卻，可重新推播。
    # - C 已結案且 exit_reason == sl：嚴格冷卻 8 小時內拒絕任何新訊號（避免連續雙殺）。
    cooldown_symbol_dir_4h: Set[Tuple[str, str]] = set()
    for e in history:
        if isinstance(e, dict) and e.get("symbol") and e.get("dir"):
            if (now_ts - e.get("ts", 0)) <= cooldown_sec:
                cooldown_symbol_dir_4h.add((_cooldown_symbol(str(e["symbol"])), str(e["dir"])))
    # 上一輪方向（用於「多轉空/空轉多」提示）：取每幣最近一次推播的方向（僅對 >4h 前推過的幣）
    last_round_by_sym = {}
    for e in sorted(history, key=lambda x: x.get("ts", 0), reverse=True):
        if isinstance(e, dict) and e.get("symbol") and e.get("dir"):
            s = _cooldown_symbol(str(e["symbol"]))
            if s and s not in last_round_by_sym:
                last_round_by_sym[s] = str(e["dir"])

    # 依 symbol 建立「最新一單」快取（來自 push_log_signals）
    latest_signal_by_sym: Dict[str, Dict[str, Any]] = {}
    for e in push_log_signals:
        if not isinstance(e, dict):
            continue
        s = _cooldown_symbol(e.get("symbol") or "")
        if not s:
            continue
        prev = latest_signal_by_sym.get(s)
        if prev is None or (e.get("ts") or 0) > (prev.get("ts") or 0):
            latest_signal_by_sym[s] = e

    cooled_top = []
    for x in all_top:
        sym = x.get("symbol") or ""
        if not sym:
            continue
        sym_norm = _cooldown_symbol(sym)
        cur_dir = _item_direction(x)
        last_sig = latest_signal_by_sym.get(sym_norm)

        skip = False
        # 情況 A：還在車上 / 未 closed → 阻擋新推播，改由倉位追蹤做同向加強或反轉提醒
        if last_sig and not last_sig.get("closed"):
            logger.info(f"智慧冷卻跳過: {sym_norm} ({cur_dir}) 上一單尚未結案(還在車上)")
            skip = True
        # 情況 C：上一單被停損 sl，8 小時內嚴格冷卻
        elif last_sig and last_sig.get("closed") and last_sig.get("exit_reason") == "sl":
            closed_ts = last_sig.get("closed_ts") or last_sig.get("ts") or 0
            if (now_ts - closed_ts) < 8 * 3600:
                logger.info(f"智慧冷卻跳過: {sym_norm} ({cur_dir}) 最近一單止損結束未滿 8 小時，嚴格冷卻中")
                skip = True
        # 情況 B：上一單已止盈 (tp1/tp2) → 無視 4 小時時間冷卻，允許重新推播
        elif last_sig and last_sig.get("closed") and last_sig.get("exit_reason") in ("tp1", "tp2"):
            logger.info(f"智慧冷卻放行: {sym_norm} ({cur_dir}) 上一單已止盈結案，允許主力開第二車")
            skip = False
        else:
            # 其他情況：沿用原本「同幣同向 4 小時內不重推」邏輯
            if (sym_norm, cur_dir) in cooldown_symbol_dir_4h:
                logger.info(f"冷卻跳過: {sym_norm} ({cur_dir}) (4h 內同幣同方向已報過)")
                skip = True

        if skip:
            continue
        # 同幣換方向：標記多轉空/空轉多，並在交易對後顯示換方向提醒
        if sym_norm in last_round_by_sym and last_round_by_sym[sym_norm] != cur_dir:
            x["direction_flip"] = last_round_by_sym[sym_norm] + "轉" + cur_dir
        else:
            x["direction_flip"] = None
        cooled_top.append(x)

    _skipped = len(all_top) - len(cooled_top)
    if _skipped > 0:
        logger.info(f"本輪冷卻跳過 {_skipped} 檔（同幣同方向 {COOLDOWN_HOURS}h 內不重推）")

    # ── 標準版：多所共識檢查（只對最終入選訊號查詢，節省 API 用量）────────────
    if cooled_top:
        for _item in cooled_top:
            _sym = _item.get("symbol", "")
            if _sym:
                _item["is_global_consensus"] = fetch_exchange_oi_consensus(_sym)

    # 僅在「實際有至少一則訊號」時才推主報表；無訊號或全被風報比篩掉 → 不推，安靜
    has_any = False
    if cooled_top:
        msg, has_any, push_count = build_report_message_tiered(cooled_top, processed_count, oi_success_count)
        if has_any:
            logger.info(
                f"【推播總結】本輪最終推播 {push_count} 檔"
                f"（冷卻後候選 {len(cooled_top)} 個，RSI+風報比篩選後實推 {push_count} 個）"
                f"，處理幣種 {processed_count} 個，OI 成功 {oi_success_count} 個"
            )
            send_telegram_message(msg, TG_THREAD_IDS['position_change'], parse_mode="Markdown")
        else:
            logger.info(f"【未推播原因】本輪 {len(cooled_top)} 筆通過冷卻，但 RSI/風報比篩選後 0 筆可推播，不發送主報表")
    else:
        if len(all_top) == 0:
            logger.info(f"【未推播原因】本輪無達 OI 門檻之標的（四類皆 0 筆），不發送主報表")
        else:
            logger.info(f"【未推播原因】本輪 {len(all_top)} 筆候選皆被冷卻（4h 內同幣同方向已推過），不發送主報表")

    # 冷卻用：僅「本輪實際有推播」的標的才寫入 history（selected_for_push 在 build_report_message_tiered 內設定）
    pairs_this_run = [
        (_cooldown_symbol(x.get("symbol")), _item_direction(x))
        for x in cooled_top
        if x.get("symbol") and x.get("selected_for_push")
    ]

    # GitHub Step Summary：若在 GitHub Actions 環境中，輸出本輪關鍵統計摘要
    step_summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if step_summary_path:
        try:
            pushed_symbols = sorted({_cooldown_symbol(x.get("symbol") or "") for x in cooled_top if x.get("symbol")}) if cooled_top else []
            pushed_list = ", ".join(pushed_symbols) if pushed_symbols else "無"
            # 動態 OI 門檻（若本輪有計算則顯示，否則顯示固定門檻）
            oi_4 = _dynamic_oi_4star if (_dynamic_oi_4star is not None and _dynamic_oi_sample_size >= 10) else OI_FOR_4_STAR
            oi_5 = _dynamic_oi_5star if (_dynamic_oi_5star is not None and _dynamic_oi_sample_size >= 10) else OI_FOR_5_STAR

            summary_lines = [
                "## 持倉變化篩選摘要",
                "",
                "| 指標 | 數值 |",
                "| --- | --- |",
                f"| 處理幣種總數 | {processed_count} |",
                f"| OI 成功數 | {oi_success_count} |",
                f"| OI 失敗數 | {oi_fail_count} |",
                f"| 動態 OI 門檻 (4★/5★) | {oi_4:.2f}% / {oi_5:.2f}% |",
                f"| 進入 TOP 候選數 | {len(all_top)} |",
                f"| 最終推播標的數 | {len(cooled_top)} |",
                f"| 推播標的列表 | {pushed_list} |",
                "",
            ]
            with open(step_summary_path, "a", encoding="utf-8") as f:
                f.write("\n".join(summary_lines) + "\n")
        except Exception as e:
            logger.warning(f"寫入 GitHub Step Summary 失敗: {e}")

    # 僅在「本輪有實際推播」時才寫入冷卻與推播紀錄（無訊號或全篩掉不寫）
    if True:  # 無論本輪是否有新訊號，都應寫回最新的冷卻與推播紀錄
        try:
            SNIPER_COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
            new_entries = [{"symbol": s, "dir": d, "ts": int(now_ts)} for (s, d) in pairs_this_run if s]
            history = history + new_entries
            history = [e for e in history if isinstance(e, dict) and (now_ts - e.get("ts", 0)) <= history_sec]
            # 推播紀錄：本輪實際推出去的訊號，與冷卻一併寫入同一狀態檔（避免 cache 還原時兩檔不一致）
            pushed_at_tw = datetime.fromtimestamp(now_ts, tz=TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")

            def _parse_price_str(s: Any) -> Optional[float]:
                try:
                    if s is None or s == "-" or s == "":
                        return None
                    return float(str(s))
                except Exception:
                    return None

            new_push_entries: List[Dict[str, Any]] = []
            for x in cooled_top:
                # 僅紀錄「真的有推播」的訊號；RSI/風報比被篩掉的標的不追蹤
                if not x.get("selected_for_push"):
                    continue
                full_sym = x.get("symbol") or ""
                base_sym = _cooldown_symbol(full_sym)
                if not base_sym:
                    continue
                dir_str = _item_direction(x)
                sl_str = x.get("sl_price_str")
                tp1_str = x.get("tp1_price_str")
                entry = {
                    "symbol": base_sym,
                    "full_symbol": full_sym,
                    "dir": dir_str,
                    "entry_category": x.get("category") or "",
                    "ts": int(now_ts),
                    "pushed_at_tw": pushed_at_tw,
                    "notified_exit": False,
                    "closed": False,
                    "entry_price": _parse_price_str(x.get("current_price")),
                    "sl": _parse_price_str(sl_str),
                    "tp1": _parse_price_str(tp1_str),
                    "tp1_label": x.get("tp1_label") or "主力成本",
                    "sl_source": x.get("sl_source") or "結構防守",
                    "tp1_notified": False,
                    "stars": x.get("stars") or 0,
                    "r_tp1": x.get("r_tp1"),
                    "tp2": _parse_price_str(x.get("tp2_price_str")),
                    "r_tp2": x.get("r_tp2"),
                    "tp2_hit": False,
                }
                new_push_entries.append(entry)
                # 48h 內同標的已有推播：同方向提醒調整防守、不同方向提醒可考慮反手
                prev_in_window = [
                    e for e in push_log_signals
                    if isinstance(e, dict) and (e.get("symbol") or "").strip().upper() == base_sym
                    and not e.get("closed") and (now_ts - (e.get("ts") or 0)) <= PUSH_LOG_RETENTION_HOURS * 3600
                ]
                if prev_in_window:
                    latest_prev = max(prev_in_window, key=lambda e: e.get("ts") or 0)
                    prev_tw = latest_prev.get("pushed_at_tw") or datetime.fromtimestamp(latest_prev.get("ts") or 0, tz=TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
                    prev_dir = (latest_prev.get("dir") or "").strip()
                    prev_label = "多單" if prev_dir == "多" else "空單"
                    if prev_dir == dir_str:
                        # 同方向：追蹤停損/止盈改為本輪（SL/TP 同源）
                        new_sl = _parse_price_str(sl_str)
                        new_tp1 = _parse_price_str(tp1_str)
                        for e in prev_in_window:
                            if (e.get("dir") or "").strip() != dir_str:
                                continue
                            if new_sl is not None:
                                e["sl"] = new_sl
                            if new_tp1 is not None:
                                o1 = e.get("tp1")
                                if dir_str == "多":
                                    e["tp1"] = min(o1, new_tp1) if o1 is not None else new_tp1
                                else:
                                    e["tp1"] = max(o1, new_tp1) if o1 is not None else new_tp1
                        reminder_msg = (
                            f"🔄 *【同標的・同方向】*\n"
                            f"台灣時間 *{prev_tw}* 已有此標的 *{prev_label}*（`{base_sym}`），本輪再次出現同方向，注意可調整防守點；"
                            f"追蹤的止損/止盈已同步更新為本輪較近版本。"
                        )
                    else:
                        reminder_msg = (
                            f"🔄 *【同標的・不同方向】*\n"
                            f"台灣時間 *{prev_tw}* 已有此標的 *{prev_label}*（`{base_sym}`），本輪不同方向，注意可考慮反手。"
                        )
                    send_telegram_message(reminder_msg, TG_THREAD_IDS["position_change"], parse_mode="Markdown")

            push_log_signals = push_log_signals + new_push_entries
            # 未結案：保留 48h；已結案：保留 48h（供每日 00:00 績效總結統計）
            def _retain_signal(e: Dict[str, Any]) -> bool:
                if not isinstance(e, dict):
                    return False
                t = e.get("ts") or 0
                ct = e.get("closed_ts") or t
                if e.get("closed"):
                    return (now_ts - ct) <= PUSH_LOG_RETENTION_HOURS * 3600
                return (now_ts - t) <= PUSH_LOG_RETENTION_HOURS * 3600
            push_log_signals = [e for e in push_log_signals if _retain_signal(e)]
            state = {"history": history, "signals": push_log_signals}
            _emergency_sniper_state = state  # 同步緊急備援快照（最終版）
            with _sniper_file_lock():
                save_json_file(SNIPER_COOLDOWN_FILE, state)
            logger.info(f"冷卻檔已寫入: 本輪 {len(pairs_this_run)} 筆，歷史共 {len(history)} 筆 (保留 {HISTORY_HOURS}h) -> {_cooldown_path_abs}")
            logger.info(f"推播紀錄已寫入: 本輪 {len(new_push_entries)} 筆，共 {len(push_log_signals)} 筆 (保留 {PUSH_LOG_RETENTION_HOURS}h)")
        except Exception as e:
            logger.warning(f"寫入狙擊狀態檔失敗: {e}")

    logger.info("持倉變化篩選執行完成並已推播")


# ==================== 4. 重要經濟數據推播 ====================

SENT_DATA_FILE = DATA_DIR / "sent_economic_data_ids.json"


def fetch_economic_data() -> List[Dict]:
    """從 CoinGlass API 抓取經濟數據"""
    url = "https://open-api-v4.coinglass.com/api/calendar/economic-data"
    params = {"language": "zh"}
    headers = {
        "CG-API-KEY": CG_API_KEY,
        "Accept": "application/json"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        result = response.json()
        
        if result.get('code') in ['0', 0, 200, '200']:
            data_list = result.get('data', [])
            # 標記數據來源
            for item in data_list:
                item['_source'] = 'economic_data'
            return data_list
        else:
            logger.error(f"Economic Data API 返回錯誤: {result.get('msg')} (錯誤碼: {result.get('code')})")
            return []
    except Exception as e:
        logger.error(f"獲取經濟數據失敗: {str(e)}")
        return []


def fetch_financial_events() -> List[Dict]:
    """從 CoinGlass API 抓取財經事件"""
    url = "https://open-api-v4.coinglass.com/api/calendar/financial-events"
    headers = {
        "CG-API-KEY": CG_API_KEY,
        "Accept": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        result = response.json()
        
        if result.get('code') in ['0', 0, 200, '200']:
            data_list = result.get('data', [])
            # 標記數據來源
            for item in data_list:
                item['_source'] = 'financial_events'
            return data_list
        else:
            logger.warning(f"Financial Events API 返回錯誤: {result.get('msg')} (錯誤碼: {result.get('code')})")
            return []
    except Exception as e:
        logger.warning(f"獲取財經事件失敗: {str(e)}")
        return []


def fetch_central_bank_activities() -> List[Dict]:
    """從 CoinGlass API 抓取央行活動"""
    url = "https://open-api-v4.coinglass.com/api/calendar/central-bank-activities"
    headers = {
        "CG-API-KEY": CG_API_KEY,
        "Accept": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        result = response.json()
        
        if result.get('code') in ['0', 0, 200, '200']:
            data_list = result.get('data', [])
            # 標記數據來源
            for item in data_list:
                item['_source'] = 'central_bank'
            return data_list
        else:
            logger.warning(f"Central Bank API 返回錯誤: {result.get('msg')} (錯誤碼: {result.get('code')})")
            return []
    except Exception as e:
        logger.warning(f"獲取央行活動失敗: {str(e)}")
        return []


def parse_publish_time(item: Dict) -> Optional[datetime]:
    """解析發布時間（返回 UTC datetime，後續會轉換為台灣時間）"""
    publish_timestamp = item.get('publish_timestamp') or item.get('publish_time') or item.get('time')
    if not publish_timestamp:
        return None
    
    try:
        if isinstance(publish_timestamp, (int, float)):
            if publish_timestamp > 1e12:  # 毫秒時間戳
                dt = datetime.fromtimestamp(publish_timestamp / 1000, tz=timezone.utc)
            else:  # 秒時間戳
                dt = datetime.fromtimestamp(publish_timestamp, tz=timezone.utc)
            return dt
        else:
            # 嘗試 ISO 格式
            time_str = str(publish_timestamp).replace('Z', '+00:00')
            dt = datetime.fromisoformat(time_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    except Exception as e:
        logger.debug(f"時間解析失敗: {publish_timestamp}, 錯誤: {str(e)}")
        return None


def filter_important_data(data_array: List[Dict], min_importance: int = 2) -> List[Dict]:
    """過濾重要經濟數據（可指定最低重要性）"""
    now = get_taipei_time()
    one_week_later = now + timedelta(days=7)
    two_hours_ago = now - timedelta(hours=2)  # 允許已發布2小時內的數據
    
    filtered = []
    for item in data_array:
        importance = item.get('importance_level') or item.get('importance') or 0
        
        # 解析發布時間
        publish_time = parse_publish_time(item)
        if not publish_time:
            continue
        
        # 檢查是否已發布（有實際發布值）
        is_published = item.get('published_value') not in [None, '']
        
        # 時間範圍：過去2小時到未來7天
        time_valid = two_hours_ago <= publish_time <= one_week_later
        
        # 根據最低重要性過濾
        if importance >= min_importance and time_valid:
            filtered.append(item)
    
    return filtered


def filter_today_events(data_array: List[Dict], min_importance: int = 4) -> List[Dict]:
    """過濾今日事件（用於早上8點預告）"""
    now = get_taipei_time()
    today_start = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=TAIPEI_TZ)
    today_end = datetime(now.year, now.month, now.day, 23, 59, 59, tzinfo=TAIPEI_TZ)
    
    filtered = []
    for item in data_array:
        importance = item.get('importance_level') or item.get('importance') or 0
        
        # 解析發布時間
        publish_time = parse_publish_time(item)
        if not publish_time:
            continue
        
        # 只取今日且未發布的事件
        is_published = item.get('published_value') not in [None, '']
        is_today = today_start <= publish_time <= today_end
        
        if importance >= min_importance and is_today and not is_published:
            filtered.append(item)
    
    return filtered


def generate_data_id(item: Dict) -> str:
    """生成唯一的數據 ID（用於去重）"""
    # 優先使用 API 提供的唯一 ID
    if item.get('id'):
        return str(item['id'])
    if item.get('calendar_id'):
        return str(item['calendar_id'])
    
    # 如果沒有唯一 ID，使用組合鍵（來源 + 名稱 + 時間戳）
    source = item.get('_source', 'unknown')
    name = item.get('calendar_name') or item.get('name') or item.get('title') or 'unknown'
    timestamp = item.get('publish_timestamp') or item.get('publish_time') or item.get('time') or '0'
    
    return f"{source}_{name}_{timestamp}"


def get_unsent_data(data_array: List[Dict]) -> List[Dict]:
    """獲取尚未推送的數據（改進版：考慮發布時間和實際值）"""
    sent_ids = load_json_file(SENT_DATA_FILE, [])
    unsent = []
    now = get_taipei_time()
    
    for item in data_array:
        data_id = generate_data_id(item)
        
        # 檢查是否在已推送列表中
        if data_id in sent_ids:
            continue
        
        # 額外檢查：如果數據已發布超過 2 小時，且已有實際值，則跳過
        # 這可以防止在 GitHub Actions 環境中重複推送
        publish_time = parse_publish_time(item)
        if publish_time:
            time_diff = (now - publish_time).total_seconds()
            published_value = item.get('published_value') or item.get('actual')
            
            # 如果已發布超過 2 小時且有實際值，視為已處理過（避免重複）
            if time_diff > 7200 and published_value:  # 2小時 = 7200秒
                logger.debug(f"跳過已發布超過2小時的數據: {data_id}")
                # 標記為已推送，避免下次再檢查
                mark_as_sent(data_id)
                continue
        
        unsent.append(item)
    
    return unsent


def mark_as_sent(data_id: str):
    """標記數據為已推送"""
    sent_ids = load_json_file(SENT_DATA_FILE, [])
    if data_id not in sent_ids:
        sent_ids.append(data_id)
        # 只保留最近 1000 條記錄
        if len(sent_ids) > 1000:
            sent_ids = sent_ids[-1000:]
        save_json_file(SENT_DATA_FILE, sent_ids)


def get_time_status(publish_time: datetime) -> tuple:
    """計算時間狀態，返回 (狀態文字, 是否已發布, 時間差秒數)"""
    # 確保兩個時間都在同一時區（台灣時間）
    now = get_taipei_time()
    publish_time_taipei = get_taipei_time(publish_time)
    diff_seconds = (publish_time_taipei - now).total_seconds()
    
    is_past = diff_seconds < 0
    abs_diff = abs(diff_seconds)
    
    if is_past:
        # 已發布時間
        if abs_diff < 3600:  # 1小時內
            minutes = int(abs_diff // 60)
            return (f"已發布 {minutes} 分鐘前", True, diff_seconds)
        elif abs_diff < 86400:  # 24小時內
            hours = int(abs_diff // 3600)
            return (f"已發布 {hours} 小時前", True, diff_seconds)
        else:
            days = int(abs_diff // 86400)
            return (f"已發布 {days} 天前", True, diff_seconds)
    else:
        # 未發布時間
        if abs_diff < 3600:  # 1小時內
            minutes = int(abs_diff // 60)
            return (f"{minutes} 分鐘後發布", False, diff_seconds)
        elif abs_diff < 86400:  # 24小時內
            hours = int(abs_diff // 3600)
            minutes = int((abs_diff % 3600) // 60)
            if minutes > 0:
                return (f"{hours} 小時 {minutes} 分鐘後", False, diff_seconds)
            else:
                return (f"{hours} 小時後", False, diff_seconds)
        else:
            days = int(abs_diff // 86400)
            hours = int((abs_diff % 86400) // 3600)
            if hours > 0:
                return (f"{days} 天 {hours} 小時後", False, diff_seconds)
            else:
                return (f"{days} 天後", False, diff_seconds)


def get_country_flag(country_name: str) -> str:
    """獲取國家旗幟 emoji"""
    flag_map = {
        '美國': '🇺🇸', '美利堅': '🇺🇸', 'US': '🇺🇸', 'United States': '🇺🇸', 'USA': '🇺🇸',
        '中國': '🇨🇳', '中華人民共和國': '🇨🇳', 'CN': '🇨🇳', 'China': '🇨🇳',
        '歐元區': '🇪🇺', '歐盟': '🇪🇺', 'EU': '🇪🇺', 'Eurozone': '🇪🇺', 'Euro Area': '🇪🇺',
        '英國': '🇬🇧', '大不列顛': '🇬🇧', 'UK': '🇬🇧', 'United Kingdom': '🇬🇧', 'GB': '🇬🇧',
        '日本': '🇯🇵', 'JP': '🇯🇵', 'Japan': '🇯🇵',
        '台灣': '🇹🇼', '臺灣': '🇹🇼', 'TW': '🇹🇼', 'Taiwan': '🇹🇼',
    }
    
    if country_name in flag_map:
        return flag_map[country_name]
    
    for key, flag in flag_map.items():
        if key in country_name or country_name in key:
            return flag
    
    return '🌍'


def get_effect_text(effect: str) -> str:
    """獲取市場影響的中文描述"""
    effect_map = {
        'Minor Impact': '輕微影響',
        'Moderate Impact': '中等影響',
        'High Impact': '重大影響',
        'Major Impact': '極大影響',
        '利多': '偏向利多', 'Bullish': '偏向利多',
        '利空': '偏向利空', 'Bearish': '偏向利空',
        '中性': '中性影響', 'Neutral': '中性影響'
    }
    
    for key, value in effect_map.items():
        if key in effect or effect in key:
            return value
    
    return effect or '待觀察'


def get_effect_emoji(effect: str) -> str:
    """獲取市場影響 emoji"""
    effect_map = {
        '利多': '📈', 'Bullish': '📈',
        '利空': '📉', 'Bearish': '📉',
        '中性': '➡️', 'Neutral': '➡️'
    }
    return effect_map.get(effect, '📊')


def get_category_info(data: Dict) -> tuple:
    """獲取數據類別資訊，返回 (類別名稱, 類別emoji)"""
    source = data.get('_source', 'economic_data')
    category_map = {
        'economic_data': ('經濟數據', '📊'),
        'financial_events': ('財經事件', '💼'),
        'central_bank': ('央行活動', '🏦')
    }
    return category_map.get(source, ('經濟事件', '📈'))


def format_economic_data_message(data: Dict) -> str:
    """格式化經濟數據訊息（全新設計）"""
    publish_time = parse_publish_time(data)
    if not publish_time:
        publish_time = get_taipei_time()
    
    time_str = format_datetime(publish_time)
    time_status, is_published, _ = get_time_status(publish_time)
    
    # 重要性
    importance_level = data.get('importance_level') or data.get('importance') or 0
    if importance_level >= 3:
        importance_emoji = '🔴'
        importance_text = '極高'
        importance_badge = '⚠️ 極高重要性'
    elif importance_level >= 2:
        importance_emoji = '🟡'
        importance_text = '高'
        importance_badge = '⚡ 高重要性'
    else:
        importance_emoji = '🟢'
        importance_text = '中'
        importance_badge = '📌 中重要性'
    
    # 類別資訊
    category_name, category_emoji = get_category_info(data)
    
    # 國家資訊
    country_flag = get_country_flag(data.get('country_name') or data.get('country') or '')
    country_name = data.get('country_name') or data.get('country') or '未知地區'
    
    # 事件名稱
    event_name = data.get('calendar_name') or data.get('name') or data.get('title') or '經濟指標'
    
    # 市場影響
    effect_emoji = get_effect_emoji(data.get('data_effect') or data.get('effect') or '')
    effect_text = get_effect_text(data.get('data_effect') or data.get('effect') or '')
    
    # 預測值與前值
    forecast_value = data.get('forecast_value') or data.get('forecast')
    previous_value = data.get('previous_value') or data.get('previous')
    published_value = data.get('published_value') or data.get('actual')
    
    # 構建訊息
    lines = []
    
    # 標題區域
    lines.append(f"{category_emoji} *【{category_name}推播】*")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    
    # 事件標題
    lines.append(f"{importance_emoji} *{event_name}*")
    lines.append(f"{country_flag} {country_name}")
    lines.append("")
    
    # 時間資訊
    lines.append("🕐 *發布時間*")
    if is_published:
        lines.append(f"✅ {time_str}")
        lines.append(f"⏰ {time_status}")
    else:
        lines.append(f"📅 {time_str}")
        lines.append(f"⏳ {time_status}")
    lines.append("")
    
    # 數據對比（如果已發布，顯示實際值；未發布顯示預測值）
    has_data = False
    if published_value:
        lines.append("📈 *實際發布值*")
        lines.append(f"`{published_value}`")
        has_data = True
        if forecast_value:
            lines.append(f"預測值：`{forecast_value}`")
        if previous_value:
            lines.append(f"前值：`{previous_value}`")
    elif forecast_value or previous_value:
        lines.append("📊 *市場預期*")
        if forecast_value:
            lines.append(f"預測值：`{forecast_value}`")
        if previous_value:
            lines.append(f"前值：`{previous_value}`")
        has_data = True
    
    if has_data:
        lines.append("")
    
    # 重要性與影響
    lines.append(f"{importance_badge}")
    if effect_text and effect_text != '待觀察':
        lines.append(f"{effect_emoji} 市場影響：{effect_text}")
    lines.append("")
    
    # 補充說明
    remark = data.get('remark') or data.get('note') or data.get('description')
    if remark:
        lines.append(f"💡 *船長解讀*")
        # 限制說明長度
        if len(remark) > 200:
            remark = remark[:200] + "..."
        lines.append(f"{remark}")
        lines.append("")
    
    # 底部資訊
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"🤖 區塊鏈船長｜{format_datetime(get_taipei_time())}")
    
    return "\n".join(lines)


def format_today_preview_message(events: List[Dict]) -> str:
    """格式化今日預告訊息（改進版：取消星級，改為高重要性和極高重要性）"""
    now = get_taipei_time()
    time_str = format_datetime(now)
    
    lines = []
    lines.append("📅 *【今日重要經濟數據預告】*")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    
    # 分組：極高重要性（>= 3）和高重要性（>= 2 且 < 3）
    very_high = [e for e in events if (e.get('importance_level') or e.get('importance') or 0) >= 3]
    high = [e for e in events if 2 <= (e.get('importance_level') or e.get('importance') or 0) < 3]
    
    # 按時間排序（使用未來時間作為 fallback）
    future_time = datetime(2099, 12, 31, 23, 59, 59, tzinfo=TAIPEI_TZ)
    very_high.sort(key=lambda x: parse_publish_time(x) or future_time)
    high.sort(key=lambda x: parse_publish_time(x) or future_time)
    
    if very_high:
        lines.append("🔴 *極高重要性（將準時推播）*：")
        lines.append("")
        for event in very_high:
            publish_time = parse_publish_time(event)
            if publish_time:
                # 轉換為台灣時間並格式化
                publish_time_taipei = get_taipei_time(publish_time)
                time_display = publish_time_taipei.strftime("%H:%M")
                event_name = event.get('calendar_name') or event.get('name') or event.get('title') or '經濟指標'
                country_flag = get_country_flag(event.get('country_name') or event.get('country') or '')
                lines.append(f"  • {time_display} | {country_flag} {event_name}")
        lines.append("")
    
    if high:
        lines.append("🟡 *高重要性（僅列出清單）*：")
        lines.append("")
        for event in high:
            publish_time = parse_publish_time(event)
            if publish_time:
                # 轉換為台灣時間並格式化
                publish_time_taipei = get_taipei_time(publish_time)
                time_display = publish_time_taipei.strftime("%H:%M")
                event_name = event.get('calendar_name') or event.get('name') or event.get('title') or '經濟指標'
                country_flag = get_country_flag(event.get('country_name') or event.get('country') or '')
                lines.append(f"  • {time_display} | {country_flag} {event_name}")
        lines.append("")
    
    if not very_high and not high:
        lines.append("今日無重要經濟數據事件")
        lines.append("")
    
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"⏰ 預告時間：{time_str}")
    
    return "\n".join(lines)


def send_today_preview():
    """早上8點發送今日預告（列出高重要性以上的事件）"""
    try:
        all_data = []
        
        # 抓取所有數據
        logger.info("正在抓取經濟數據（預告模式）...")
        economic_data = fetch_economic_data()
        all_data.extend(economic_data)
        
        financial_events = fetch_financial_events()
        all_data.extend(financial_events)
        
        central_bank = fetch_central_bank_activities()
        all_data.extend(central_bank)
        
        if not all_data:
            logger.info("沒有獲取到任何數據")
            return
        
        # 過濾今日高重要性以上的事件（>= 2）
        today_events = filter_today_events(all_data, min_importance=2)
        logger.info(f"今日高重要性以上事件: {len(today_events)} 條")
        
        if not today_events:
            logger.info("今日無重要事件")
            return
        
        # 發送預告
        message = format_today_preview_message(today_events)
        send_telegram_message(message, TG_THREAD_IDS['economic_data'], parse_mode="Markdown")
        logger.info("今日預告發送完成")
        
    except Exception as e:
        logger.error(f"發送今日預告錯誤: {str(e)}")


def fetch_and_push_economic_data():
    """主函數：抓取並推送經濟數據（只推播極高重要性事件，在事件發生時）"""
    try:
        all_data = []
        
        # 1. 抓取經濟數據
        logger.info("正在抓取經濟數據...")
        economic_data = fetch_economic_data()
        all_data.extend(economic_data)
        logger.info(f"經濟數據：{len(economic_data)} 條")
        
        # 2. 抓取財經事件
        logger.info("正在抓取財經事件...")
        financial_events = fetch_financial_events()
        all_data.extend(financial_events)
        logger.info(f"財經事件：{len(financial_events)} 條")
        
        # 3. 抓取央行活動
        logger.info("正在抓取央行活動...")
        central_bank = fetch_central_bank_activities()
        all_data.extend(central_bank)
        logger.info(f"央行活動：{len(central_bank)} 條")
        
        if not all_data:
            logger.info("沒有獲取到任何數據")
            return
        
        logger.info(f"總共獲取 {len(all_data)} 條數據（經濟數據: {len(economic_data)}, 財經事件: {len(financial_events)}, 央行活動: {len(central_bank)}）")
        
        # 只過濾極高重要性數據（>= 3），高重要性（>= 2 且 < 3）不推播
        important_data = filter_important_data(all_data, min_importance=3)
        logger.info(f"過濾後的極高重要性數據: {len(important_data)} 條")
        
        if not important_data:
            logger.info("沒有符合條件的極高重要性數據")
            return
        
        # 按發布時間排序（優先推送即將發布的）
        future_time = datetime(2099, 12, 31, 23, 59, 59, tzinfo=TAIPEI_TZ)
        important_data.sort(key=lambda x: parse_publish_time(x) or future_time)
        
        # 檢查哪些尚未推送
        new_data = get_unsent_data(important_data)
        logger.info(f"尚未推送的極高重要性數據: {len(new_data)} 條")
        
        if not new_data:
            logger.info("所有極高重要性數據均已推送過")
            return
        
        # 批量推送（避免過於頻繁）
        success_count = 0
        for idx, data in enumerate(new_data):
            try:
                message = format_economic_data_message(data)
                send_telegram_message(message, TG_THREAD_IDS['economic_data'], parse_mode="Markdown")
                
                data_id = generate_data_id(data)
                mark_as_sent(data_id)
                success_count += 1
                
                # 每條訊息間隔 1 秒，避免觸發速率限制
                if idx < len(new_data) - 1:
                    time.sleep(1)
                    
            except Exception as e:
                logger.error(f"推送單條數據失敗: {str(e)}")
        
        logger.info(f"成功推送 {success_count}/{len(new_data)} 條極高重要性經濟數據")
        
    except Exception as e:
        logger.error(f"經濟數據推播執行錯誤: {str(e)}")
        send_telegram_message("⚠️ 經濟數據暫時無法取得，請稍後再試。", TG_THREAD_IDS['economic_data'])


# ==================== 5. 新聞快訊推特中文推播 ====================

LAST_NEWS_TIME_FILE = DATA_DIR / "last_news_time.json"
COINGLASS_ARTICLE_IDS_FILE = DATA_DIR / "coinglass_article_ids.json"
COINGLASS_NEWSFLASH_IDS_FILE = DATA_DIR / "coinglass_newsflash_ids.json"


def fetch_tree_news():
    """抓取 Tree of Alpha 新聞"""
    url = "https://news.treeofalpha.com/api/news"
    params = {"limit": 10}
    headers = {"Authorization": TREE_API_KEY}
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        news_list = response.json()
        
        # 取得前一次發送的最晚時間，避免重複
        last_time = load_json_file(LAST_NEWS_TIME_FILE, 0)
        newest_time = last_time
        
        # 由舊到新排列發送
        for news in reversed(news_list):
            if news.get('time', 0) > last_time:
                process_and_send(news, "Tree of Alpha")
                if news.get('time', 0) > newest_time:
                    newest_time = news.get('time', 0)
        
        # 更新時間紀錄
        save_json_file(LAST_NEWS_TIME_FILE, newest_time)
        
    except Exception as e:
        logger.warning(f"Tree of Alpha 新聞抓取失敗: {str(e)}")


def fetch_coinglass_articles():
    """抓取 CoinGlass 新聞"""
    if not CG_API_KEY:
        logger.warning("請先設定 CoinGlass API 金鑰")
        return
    
    url = "https://open-api-v4.coinglass.com/api/article/list"
    headers = {
        "accept": "application/json",
        "CG-API-KEY": CG_API_KEY
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        result = response.json()
        
        if result.get('code') != '0':
            error_msg = result.get('msg', '')
            # 如果是速率限制錯誤，只記錄警告，不報錯
            if 'Too Many Requests' in error_msg or '429' in str(result.get('code')):
                logger.warning(f"CoinGlass 新聞 API 速率限制，稍後再試: {error_msg}")
            else:
                logger.warning(f"CoinGlass 新聞 API 錯誤: {result}")
            return
        
        article_list = result.get('data', [])
        
        # 取得已發送的新聞 ID 列表
        sent_ids = load_json_file(COINGLASS_ARTICLE_IDS_FILE, [])
        new_sent_ids = sent_ids.copy()
        
        # 處理新聞列表（由舊到新）
        for article in reversed(article_list):
            article_id = article.get('id') or article.get('articleId') or article.get('url')
            
            if article_id and article_id not in sent_ids:
                process_and_send_coinglass(article, "article")
                new_sent_ids.append(article_id)
                
                # 只保留最近 1000 條 ID，避免儲存過多
                if len(new_sent_ids) > 1000:
                    new_sent_ids = new_sent_ids[-1000:]
        
        # 更新已發送 ID 列表
        save_json_file(COINGLASS_ARTICLE_IDS_FILE, new_sent_ids)
        
    except Exception as e:
        logger.warning(f"CoinGlass 新聞抓取失敗: {str(e)}")


def fetch_coinglass_newsflash():
    """抓取 CoinGlass 快訊"""
    if not CG_API_KEY:
        logger.warning("請先設定 CoinGlass API 金鑰")
        return
    
    url = "https://open-api-v4.coinglass.com/api/newsflash/list"
    headers = {
        "accept": "application/json",
        "CG-API-KEY": CG_API_KEY
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        
        # 檢查 HTTP 狀態碼
        if response.status_code != 200:
            logger.warning(f"CoinGlass 快訊 API HTTP 錯誤: {response.status_code} - {response.text[:200]}")
            return
        
        result = response.json()
        
        if result.get('code') != '0':
            error_msg = result.get('msg', '')
            # 如果是速率限制錯誤，只記錄警告，不報錯
            if 'Too Many Requests' in error_msg or '429' in str(result.get('code')):
                logger.warning(f"CoinGlass 快訊 API 速率限制，稍後再試: {error_msg}")
            else:
                logger.warning(f"CoinGlass 快訊 API 錯誤: {result}")
            return
        
        newsflash_list = result.get('data', [])
        
        # 取得已發送的快訊 ID 列表
        sent_ids = load_json_file(COINGLASS_NEWSFLASH_IDS_FILE, [])
        new_sent_ids = sent_ids.copy()
        
        # 處理快訊列表（由舊到新）
        for newsflash in reversed(newsflash_list):
            newsflash_id = newsflash.get('id') or newsflash.get('newsflashId') or newsflash.get('url')
            
            if newsflash_id and newsflash_id not in sent_ids:
                process_and_send_coinglass(newsflash, "newsflash")
                new_sent_ids.append(newsflash_id)
                
                # 只保留最近 1000 條 ID，避免儲存過多
                if len(new_sent_ids) > 1000:
                    new_sent_ids = new_sent_ids[-1000:]
        
        # 更新已發送 ID 列表
        save_json_file(COINGLASS_NEWSFLASH_IDS_FILE, new_sent_ids)
        
    except Exception as e:
        logger.warning(f"CoinGlass 快訊抓取失敗: {str(e)}")


def process_and_send(news: Dict, source: str):
    """翻譯並發送 Tree of Alpha 新聞到 Telegram"""
    translated_title = translate_text(news.get('title', ''))
    
    message = "📰 *【全球幣圈即時快訊】*\n\n"
    message += f"🔔 *{translated_title}*\n\n"
    message += f"📄 原文：{news.get('title', '')}\n"
    message += f"🔗 [點擊查看原文]({news.get('url', '')})"
    
    send_telegram_message(message, TG_THREAD_IDS['news'])


def process_and_send_coinglass(item: Dict, type_str: str):
    """翻譯並發送 CoinGlass 新聞/快訊到 Telegram"""
    is_newsflash = type_str == "newsflash"
    emoji = "⚡" if is_newsflash else "📰"
    type_name = "快訊" if is_newsflash else "新聞"
    
    translated_title = translate_text(item.get('title') or item.get('headline') or "")
    translated_content = translate_text(item.get('content') or item.get('description') or "")
    
    message = f"{emoji} *【全球幣圈{type_name}】*\n\n"
    
    if translated_title:
        message += f"🔔 *{translated_title}*\n\n"
    
    if translated_content:
        if len(translated_content) > 500:
            translated_content = translated_content[:500] + "..."
        message += f"{translated_content}\n\n"
    
    time_val = item.get('time') or item.get('timestamp') or item.get('publishTime')
    if time_val:
        if isinstance(time_val, (int, float)):
            if time_val > 1e12:
                date = datetime.fromtimestamp(time_val / 1000, tz=timezone.utc)
            else:
                date = datetime.fromtimestamp(time_val, tz=timezone.utc)
        else:
            date = get_taipei_time()
        # 轉換為台灣時間
        date_taipei = get_taipei_time(date)
        message += f"🕐 時間：{date_taipei.strftime('%Y-%m-%d %H:%M:%S')}\n"
    
    if item.get('url') or item.get('link'):
        message += f"🔗 [點擊查看原文]({item.get('url') or item.get('link')})"
    
    send_telegram_message(message, TG_THREAD_IDS['news'])


def fetch_all_news():
    """整合執行函數：抓取所有新聞並濃縮成一個簡短訊息（每4小時推播一次）"""
    all_news_items = []
    
    # 抓取 Tree of Alpha 新聞
    try:
        url = "https://news.treeofalpha.com/api/news"
        params = {"limit": 5}  # 只取最新5條
        headers = {"Authorization": TREE_API_KEY}
        response = requests.get(url, params=params, headers=headers, timeout=10)
        news_list = response.json()
        for news in news_list[:5]:  # 只取前5條
            title = translate_text(news.get('title', ''))
            if title:
                all_news_items.append({
                    'title': title,
                    'source': 'Tree of Alpha',
                    'url': news.get('url', '')
                })
    except Exception as e:
        logger.warning(f"Tree of Alpha 新聞抓取失敗: {str(e)}")
    
    # 抓取 CoinGlass 新聞（只取最新3條）
    if CG_API_KEY:
        try:
            url = "https://open-api-v4.coinglass.com/api/article/list"
            headers = {
                "accept": "application/json",
                "CG-API-KEY": CG_API_KEY
            }
            response = requests.get(url, headers=headers, timeout=10)
            result = response.json()
            if result.get('code') == '0':
                article_list = result.get('data', [])[:3]  # 只取前3條
                for article in article_list:
                    title = translate_text(article.get('title') or article.get('headline') or "")
                    if title:
                        all_news_items.append({
                            'title': title,
                            'source': 'CoinGlass',
                            'url': article.get('url') or article.get('link') or ''
                        })
        except Exception as e:
            logger.warning(f"CoinGlass 新聞抓取失敗: {str(e)}")
    
    # 如果沒有新聞，不推播
    if not all_news_items:
        logger.info("本次監控無新新聞，跳過推播")
        return
    
    # 濃縮成一個簡短訊息
    now = get_taipei_time()
    time_str = format_datetime(now)
    
    lines = []
    lines.append("📰 *【全球幣圈即時快訊】*")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    
    # 只顯示標題，簡短格式
    for idx, item in enumerate(all_news_items[:8], 1):  # 最多8條
        lines.append(f"{idx}. {item['title']}")
        if item.get('url'):
            lines.append(f"   🔗 [查看詳情]({item['url']})")
        lines.append("")
    
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"⏰ 更新時間：{time_str}")
    
    message = "\n".join(lines)
    send_telegram_message(message, TG_THREAD_IDS['news'], parse_mode="Markdown")
    logger.info(f"新聞快訊推播完成，共 {len(all_news_items)} 條新聞")


# ==================== 6. 資金費率 ====================

def fetch_funding_fortune_list():
    """抓取資金費率排行榜"""
    url = "https://open-api-v4.coinglass.com/api/futures/funding-rate/exchange-list"
    headers = {
        "accept": "application/json",
        "CG-API-KEY": CG_API_KEY
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        logger.info(f"API 回應狀態碼: {response.status_code}")
        
        result = response.json()
        if result.get('code') not in ['0', 0]:
            logger.error(f"API 回應錯誤: {result}")
            return
        
        data_list = result.get('data', [])
        if not isinstance(data_list, list):
            logger.error("API 數據格式錯誤")
            return
        
        binance_funding_rates = []
        for coin_data in data_list:
            symbol = coin_data.get('symbol')
            
            # 優先處理 USDT 永續合約
            stablecoin_list = coin_data.get('stablecoin_margin_list', [])
            for item in stablecoin_list:
                if item.get('exchange') == 'Binance' and item.get('funding_rate') is not None:
                    binance_funding_rates.append({
                        'symbol': symbol,
                        'exchange': item.get('exchange'),
                        'fundingRate': float(item.get('funding_rate', 0)),
                        'marginType': 'USDT永續',
                        'fundingRateInterval': item.get('funding_rate_interval', 8)
                    })
            
            # 如果 USDT 永續沒有幣安的數據，再檢查幣本位永續
            token_list = coin_data.get('token_margin_list', [])
            for item in token_list:
                if item.get('exchange') == 'Binance' and item.get('funding_rate') is not None:
                    has_usdt = any(r['symbol'] == symbol and r['marginType'] == 'USDT永續' 
                                   for r in binance_funding_rates)
                    if not has_usdt:
                        binance_funding_rates.append({
                            'symbol': symbol,
                            'exchange': item.get('exchange'),
                            'fundingRate': float(item.get('funding_rate', 0)),
                            'marginType': '幣本位永續',
                            'fundingRateInterval': item.get('funding_rate_interval', 8)
                        })
        
        logger.info(f"幣安永續合約數據條數: {len(binance_funding_rates)}")
        
        # 根據費率絕對值排序，取前 5 名
        sorted_data = sorted(
            [item for item in binance_funding_rates if item['fundingRate'] != 0],
            key=lambda x: abs(x['fundingRate']),
            reverse=True
        )[:5]
        
        if not sorted_data:
            logger.warning("未找到幣安永續合約的有效資金費率數據")
            return
        
        # 構建訊息
        message = "🏦 *【U本位資金費率排行榜】*\n"
        message += "━━━━━━━━━━━━━━━━━━━━\n"
        message += "*以持倉 10,000 USDT 為例，每 4 小時結算一次：*\n\n"
        
        for index, item in enumerate(sorted_data):
            symbol = item['symbol']
            rate = item['fundingRate']
            
            rate_percent = f"{abs(rate):.6f}"
            rate_display = f"+{rate_percent}%" if rate >= 0 else f"-{rate_percent}%"
            
            rate_for_calculation = abs(rate) / 100
            single_pay = f"{10000 * 0.4 * rate_for_calculation:.2f}"
            
            message += f"{index + 1}. 💰 *{symbol}USDT 永續*\n"
            message += f"   📊 資金費率：`{rate_display}`\n"
            message += f"   💵 單次領取：`${single_pay}` USDT\n"
            message += "━━━━━━━━━━━━━━━━━━━━\n"
        
        message += "\n💡 *套利策略*（新手說明：做多=看漲買入，做空=看跌賣出）：\n"
        message += "*正費率（+）*：做空永續合約（看跌）+ 持有現貨，每 4 小時領取資金費率。\n"
        message += "*負費率（-）*：做多永續合約（看漲）+ 賣出現貨，但需注意軋空風險。\n\n"
        now_taipei = get_taipei_time()
        message += f"⏰ 更新時間：{now_taipei.strftime('%Y-%m-%d %H:%M:%S')}"
        
        send_telegram_message(message, TG_THREAD_IDS['funding_rate'])
        
    except Exception as e:
        logger.error(f"資費榜執行失敗: {str(e)}")


# ==================== 7. 長線指標：牛熊導航儀 ====================

def _coinglass_get(path: str, params: Optional[Dict] = None) -> Optional[Dict]:
    """通用的 CoinGlass GET 請求工具"""
    if not CG_API_KEY:
        logger.error("CG_API_KEY 未設定，無法呼叫 CoinGlass API")
        return None
    url = f"{CG_API_BASE}{path}"
    headers = {
        "accept": "application/json",
        "CG-API-KEY": CG_API_KEY,
    }
    try:
        resp = requests.get(url, headers=headers, params=params or {}, timeout=10)
        if resp.status_code != 200:
            logger.error(f"CoinGlass API HTTP 錯誤 {path}: {resp.status_code} - {resp.text[:200]}")
            return None
        data = resp.json()
        # 多數 CoinGlass 介面 code 為 '0' 代表成功
        code = data.get("code", 0)
        if code not in [0, "0", 200, "200"]:
            logger.error(f"CoinGlass API 返回錯誤 {path}: {data}")
            return None
        return data
    except Exception as e:
        logger.error(f"CoinGlass API 請求失敗 {path}: {str(e)}")
        return None


def _get_latest_from_data(result: Dict) -> Optional[Dict]:
    """從 CoinGlass 回應中取出最新一筆 data，確保返回 dict"""
    if not result:
        return None
    data = result.get("data", result)
    if isinstance(data, list):
        if not data:
            return None
        # 取最後一個元素，但確保它是 dict
        last_item = data[-1]
        if isinstance(last_item, dict):
            return last_item
        # 如果最後一個元素不是 dict，嘗試往前找
        for item in reversed(data):
            if isinstance(item, dict):
                return item
        logger.warning(f"列表中沒有找到 dict 類型的資料: {data}")
        return None
    if isinstance(data, dict):
        return data
    logger.warning(f"未知的資料格式: {type(data)} - {data}")
    return None


def fetch_ahr999_index() -> Optional[float]:
    """取得比特幣 Ahr999 指標數值"""
    result = _coinglass_get("/api/index/ahr999")
    point = _get_latest_from_data(result) if result else None
    if not point:
        return None
    # 確保 point 是 dict，不是 list
    if not isinstance(point, dict):
        logger.warning(f"Ahr999 資料格式錯誤，預期 dict 但得到 {type(point)}: {point}")
        return None
    # 嘗試多個常見欄位名稱（包含實際 API 回傳的 ahr999_value）
    for key in ("ahr999_value", "ahr999", "ahr999_index", "ahrIndex", "ahr_value"):
        val = point.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    logger.warning(f"Ahr999 結構未知，原始資料: {point}")
    return None


def get_rainbow_stage(price: Optional[float], levels: Optional[List[float]]) -> str:
    """
    根據當前價格與彩虹圖價格閾值，回傳文字描述。
    levels: 由低到高的價格閾值列表（通常 9 個）。
    """
    if price is None or not levels or len(levels) < 3:
        return "資料不足，暫無法判斷"

    # 確保升冪排序
    levels = sorted(levels)

    # 嚴重低估
    if price < levels[0]:
        return "基本上是火熱大特價（極度低估區）"

    # 嚴重高估
    if price > levels[-1]:
        return "最大泡沫區，建議分批逃頂、降低槓桿"

    # 落在區間中，找到對應區段
    idx = 0
    for i in range(len(levels) - 1):
        if levels[i] <= price < levels[i + 1]:
            idx = i
            break

    # 依照所在區段粗分為「低位 / 中位 / 高位」
    n = len(levels) - 1  # 有 n 個區間
    low_border = n // 3
    high_border = (2 * n) // 3

    if idx <= low_border:
        return "價格位於彩虹圖低位區，適合長線累積/分批加倉"
    elif idx <= high_border:
        return "價格位於彩虹圖中間區，屬於合理區間，偏向持有/觀望"
    else:
        return "價格位於彩虹圖高位區，市場偏 FOMO/泡沫，需謹慎控管風險"


def fetch_rainbow_zone() -> Optional[str]:
    """取得比特幣彩虹圖當前區間描述（轉成小白友善文字）"""
    result = _coinglass_get("/api/index/bitcoin/rainbow-chart")
    if not result:
        return None

    # 嘗試從回應中取得當前 BTC 價格
    price = None
    for key in ("current_price", "btc_price", "price"):
        val = result.get(key)
        if isinstance(val, (int, float)):
            price = float(val)
            break

    data = result.get("data") or result.get("list")
    levels: Optional[List[float]] = None

    if isinstance(data, list) and data:
        last_row = data[-1]
        # 典型結構：一列為 [v1, v2, ..., vN, timestamp] 或 [level1..level9]
        if isinstance(last_row, list) and len(last_row) >= 4:
            # 嘗試視最後一個元素為時間戳，其餘為價格閾值
            numeric_parts = [x for x in last_row if isinstance(x, (int, float))]
            if len(numeric_parts) >= 4:
                # 若尚未取得價格，使用最大值當前價格作為近似
                if price is None:
                    price = max(numeric_parts)
                # 取除當前價格外較小的那些作為「層級」，避免把極端最大值當作區間
                # 這裡簡化為去掉數列中的最大值，其餘視為彩虹層級
                max_val = max(numeric_parts)
                levels = [v for v in numeric_parts if v != max_val] or numeric_parts

    return get_rainbow_stage(price, levels)


def fetch_pi_cycle_signal() -> bool:
    """取得 Pi 循環頂部指標是否觸發（均線交叉）"""
    result = _coinglass_get("/api/index/pi-cycle-indicator")
    point = _get_latest_from_data(result) if result else None
    if not point:
        return False
    # 確保 point 是 dict
    if not isinstance(point, dict):
        logger.warning(f"Pi 循環指標資料格式錯誤，預期 dict 但得到 {type(point)}: {point}")
        return False

    # 1) 直接的布林欄位
    for key in ("isCross", "cross", "signal", "topSignal", "top_signal"):
        val = point.get(key)
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)) and val in (0, 1):
            return bool(val)
        if isinstance(val, str):
            low = val.lower()
            if low in ("true", "yes", "y", "1", "cross", "top", "sell"):
                return True

    # 2) 如果有兩條均線數值，可以粗略判斷是否剛交叉
    # 你的日誌顯示結構為: {'ma_110': ..., 'ma_350_mu_2': ..., 'price': ..., 'timestamp': ...}
    short_ma = (
        point.get("short_ma")
        or point.get("shortMA")
        or point.get("fast_ma")
        or point.get("ma_110")
    )
    long_ma = (
        point.get("long_ma")
        or point.get("longMA")
        or point.get("slow_ma")
        or point.get("ma_350_mu_2")
    )
    if short_ma is not None and long_ma is not None:
        try:
            short_ma = float(short_ma)
            long_ma = float(long_ma)
            # 只要短均線高於長均線，視為有頂部風險
            return short_ma >= long_ma
        except (TypeError, ValueError):
            pass

    logger.warning(f"Pi 循環指標結構未知，原始資料: {point}")
    return False


def fetch_latest_fear_greed() -> Optional[int]:
    """取得最新一筆恐懼與貪婪指數"""
    result = _coinglass_get("/api/index/fear-greed-history")
    point = _get_latest_from_data(result) if result else None
    if not point:
        return None

    # 1) 新版結構：{'data_list': [ ... 整數列表 ... ]}
    if isinstance(point, dict) and "data_list" in point:
        data_list = point.get("data_list")
        if isinstance(data_list, list) and data_list:
            try:
                return int(float(data_list[-1]))
            except (TypeError, ValueError):
                logger.warning(f"無法解析恐懼與貪婪 data_list 最後一筆數值: {data_list[-1]}")
                return None

    # 2) 傳統結構：每筆是一個 dict，含 value / score 等欄位
    if isinstance(point, dict):
        for key in ("value", "fear_greed", "score", "index"):
            val = point.get(key)
            if val is not None:
                try:
                    return int(float(val))
                except (TypeError, ValueError):
                    continue

    logger.warning(f"恐懼與貪婪指數結構未知，原始資料: {point}")
    return None


def _classify_fear_greed(value: Optional[int]) -> str:
    if value is None:
        return "未知"
    if value <= 20:
        return "極度恐懼"
    if value <= 40:
        return "恐懼"
    if value < 60:
        return "中性"
    if value <= 80:
        return "貪婪"
    return "極度貪婪"


def _describe_fear_greed(value: Optional[int]) -> str:
    """將恐懼與貪婪指數轉成更有畫面的描述文字"""
    if value is None:
        return "指標暫缺，請先觀察 Ahr999 與價格位置。"
    if value < 25:
        return "😱 大家都在逃命，情緒極度恐懼，往往是長線投資人慢慢撿便宜的區域。"
    if 45 <= value <= 55:
        return "😐 市場情緒接近中性，適合按兵不動、照原本節奏紀律操作即可。"
    if value > 75:
        return "🔥 市場極度貪婪，資金情緒瘋狂，請繫好安全帶並隨時準備減倉。"
    return "情緒尚未到極端區間，建議搭配 Ahr999 與彩虹圖一起綜合判斷。"


def _interpret_rainbow_zone(zone: Optional[str]) -> str:
    """把彩虹圖的英文區間翻成小白友善描述"""
    if not zone:
        return "資料不足，暫無法判斷"
    z = zone.lower()
    if any(k in z for k in ["buy", "cheap", "accumulate", "bargain", "btfd"]):
        return f"{zone}（還在加倉區，長線偏便宜）"
    if any(k in z for k in ["hodl", "hold"]):
        return f"{zone}（長線持有區，耐心抱緊）"
    if any(k in z for k in ["fomo", "sell", "bubble", "maximum", "overvalued"]):
        return f"{zone}（偏泡沫/高估區，適合減倉風險控管）"
    return zone


def build_long_term_message() -> Optional[str]:
    """【長線財富週期】判斷大級別買賣點，現在是底還是頂。"""
    ahr = fetch_ahr999_index()
    fg = fetch_latest_fear_greed()
    if ahr is None:
        return None

    status = "😐 尷尬區 (持有)"
    action = "多看少動，拿住現貨"
    color = "🟡"
    if ahr < 0.45:
        status, action, color = "💎 鑽石底 (大抄底)", "砸鍋賣鐵買進去！兩年後你會感謝自己！", "🟢"
    elif ahr < 1.2:
        status, action, color = "📥 定投區 (累積)", "薪水發了就買，不要管價格。", "🔵"
    elif ahr > 5.0:
        status, action, color = "☠️ 世紀頂部 (逃命)", "清倉！刪APP！去旅遊！", "🔴"
    elif ahr > 1.2 and fg is not None and fg > 80:
        status, action, color = "🔥 泡沫區 (減倉)", "人聲鼎沸時離場，分批賣出。", "🟠"

    lines = []
    lines.append("⏳ *【長線財富週期】*")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📍 *當前位置：{color} {status}*")
    lines.append("")
    lines.append(f"💰 *AHR999 指數：{ahr:.2f}*")
    lines.append(f"🌡️ *貪婪恐懼指數：{fg}*" if fg is not None else "🌡️ *貪婪恐懼指數：—*")
    lines.append("")
    lines.append("🧠 *傑克船長碎碎念*：")
    lines.append(f"👉 {action}")
    if fg is not None and fg < 20:
        lines.append("👉 現在市場極度恐懼，但這通常是富人變更有錢的時候。")
    if fg is not None and fg > 80:
        lines.append("👉 現在市場極度貪婪，擦鞋童都在問幣，你該小心了。")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(f"⏰ {datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d')}")
    return "\n".join(lines)


def run_long_term_monitor(interval_hours: int = 4):
    """24 小時常駐，每 interval_hours 小時抓取並推播一次"""
    logger.info(f"啟動長線指標監控，每 {interval_hours} 小時更新一次...")
    interval_sec = max(1, int(interval_hours * 3600))
    while True:
        try:
            message = build_long_term_message()
            if message:
                thread_id = TG_THREAD_IDS.get("long_term_index", 0)
                send_telegram_message(message, thread_id, parse_mode="Markdown")
            else:
                logger.warning("本輪長線指標分析失敗，未發送推播")
        except Exception as e:
            logger.error(f"長線指標監控執行錯誤: {str(e)}")
        # 休息 interval
        time.sleep(interval_sec)


def run_long_term_once():
    """長線財富週期推播（含按鈕）"""
    logger.info("執行單次長線指標推播...")
    message = build_long_term_message()
    if not message:
        logger.warning("本次長線指標分析失敗，未發送推播")
        return
    thread_id = TG_THREAD_IDS.get("long_term_index", 248)
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "🌈 查看比特幣彩虹圖", "url": "https://www.coinglass.com/zh-TW/pro/i/bitcoin-rainbow-chart"},
                {"text": "💰 查看 AHR999", "url": "https://www.coinglass.com/zh-TW/pro/i/ahr999"}
            ]
        ]
    }
    send_telegram_message(message, thread_id, parse_mode="Markdown", reply_markup=keyboard)


# ==================== 8. 流動性獵取雷達（極端清算監控） ====================

LIQ_SYMBOLS = [
    "BTC", "ETH", "SOL",  # 只偵測這三個主流幣種
]
LIQ_EXCHANGE_LIST = "Binance"
LIQ_REQUEST_DELAY = 1.2  # 秒


def get_liquidation_threshold(symbol: str, time_window: str = "1h") -> tuple:
    """根據幣種回傳極端爆倉門檻（USD）
    返回 (1h阈值, 24h阈值) 的元組
    注意：1小時門檻已大幅降低，以便捕捉更多極端爆倉事件
    """
    if symbol in ("BTC", "ETH"):
        return (100_000.0, 15_000_000.0)  # 1h: 10萬（大幅降低）, 24h: 1500萬
    if symbol in ("SOL", "XRP", "DOGE"):
        return (50_000.0, 5_000_000.0)  # 1h: 5萬（大幅降低）, 24h: 500萬
    return (30_000.0, 3_000_000.0)  # 1h: 3萬（大幅降低）, 24h: 300萬


def fetch_liquidation_data(symbol: str) -> Optional[List[Dict]]:
    """從 CoinGlass 抓取單一幣種的清算彙總歷史（改進版：添加調試信息）"""
    if not CG_API_KEY:
        logger.error("CG_API_KEY 未設定，無法呼叫清算 API")
        return None

    url = f"{CG_API_BASE}/api/futures/liquidation/aggregated-history"
    params = {
        "symbol": symbol,
        "interval": "1h",
        "exchange_list": LIQ_EXCHANGE_LIST,
    }
    headers = {
        "CG-API-KEY": CG_API_KEY,
        "accept": "application/json",
    }

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code != 200:
            logger.warning(f"{symbol} 清算 API 請求失敗，狀態碼: {resp.status_code}")
            return None

        data = resp.json()
        if not (data.get("success") is True or data.get("code") in (0, "0")):
            logger.warning(
                f"{symbol} 清算 API 返回失敗 - code: {data.get('code')}, msg: {data.get('msg')}"
            )
            return None

        data_array = data.get("data") or data.get("list") or []
        if not isinstance(data_array, list):
            logger.warning(f"{symbol} 清算數據格式異常: {type(data_array)}")
            return None
        
        # 調試：檢查數據結構（只對前幾個幣種）
        if symbol in ["BTC", "ETH", "SOL"] and data_array:
            sample = data_array[-1] if data_array else {}
            logger.debug(f"{symbol} API返回 - 數據筆數: {len(data_array)}, 最新一筆時間戳: {sample.get('time')}, 欄位: {list(sample.keys())[:8]}")
        
        return data_array
    except Exception as e:
        logger.error(f"獲取 {symbol} 清算數據時發生異常: {str(e)}")
        return None


def process_liquidation_data(symbol: str, data_array: List[Dict]) -> Optional[Dict]:
    """處理清算數據，判斷是否達到極端爆倉門檻，返回事件描述（改進版：修復時間戳處理）"""
    try:
        if not data_array:
            logger.debug(f"{symbol} 清算數據為空")
            return None

        now_ms = int(time.time() * 1000)
        twenty_four_hours_ago = now_ms - 24 * 60 * 60 * 1000
        one_hour_ago = now_ms - 60 * 60 * 1000

        buy_vol_usd_24h = 0.0
        sell_vol_usd_24h = 0.0
        buy_vol_usd_1h = 0.0
        sell_vol_usd_1h = 0.0

        # 調試：檢查數據結構（只對前幾個幣種）
        if symbol in ["BTC", "ETH", "SOL"] and data_array:
            sample_item = data_array[-1] if data_array else {}
            logger.debug(f"{symbol} 數據樣本 - 時間戳: {sample_item.get('time')}, 欄位: {list(sample_item.keys())[:5]}")

        # 從後往前遍歷，累加最近 24 小時與 1 小時的清算
        items_in_24h = 0
        items_in_1h = 0
        
        for item in reversed(data_array):
            try:
                item_time_raw = item.get("time") or item.get("timestamp") or 0
                
                # 處理時間戳：可能是毫秒或秒
                if isinstance(item_time_raw, str):
                    item_time = int(float(item_time_raw))
                else:
                    item_time = int(item_time_raw)
                
                # 如果時間戳看起來是秒（小於 1e12），轉換為毫秒
                if item_time < 1e12:
                    item_time = item_time * 1000
                
            except (TypeError, ValueError) as e:
                logger.debug(f"{symbol} 時間戳解析失敗: {item_time_raw}, 錯誤: {str(e)}")
                continue

            long_liq = float(item.get("aggregated_long_liquidation_usd") or item.get("long_liquidation_usd") or item.get("long") or 0)
            short_liq = float(item.get("aggregated_short_liquidation_usd") or item.get("short_liquidation_usd") or item.get("short") or 0)

            if item_time >= twenty_four_hours_ago:
                items_in_24h += 1
                buy_vol_usd_24h += long_liq
                sell_vol_usd_24h += short_liq

                if item_time >= one_hour_ago:
                    items_in_1h += 1
                    buy_vol_usd_1h += long_liq
                    sell_vol_usd_1h += short_liq
            else:
                break

        # 調試日誌（只對前幾個幣種或當數據異常時）
        if symbol in ["BTC", "ETH", "SOL"] or (items_in_1h == 0 and items_in_24h > 0):
            logger.debug(f"{symbol} 時間範圍統計 - 24h內: {items_in_24h} 筆, 1h內: {items_in_1h} 筆, 總數據: {len(data_array)} 筆")

        # 如果 24h 沒數據，用最新一筆頂上（備用邏輯）
        if buy_vol_usd_24h == 0 and sell_vol_usd_24h == 0 and data_array:
            latest = data_array[-1]
            buy_vol_usd_24h = float(latest.get("aggregated_long_liquidation_usd") or latest.get("long_liquidation_usd") or latest.get("long") or 0)
            sell_vol_usd_24h = float(latest.get("aggregated_short_liquidation_usd") or latest.get("short_liquidation_usd") or latest.get("short") or 0)
            buy_vol_usd_1h = buy_vol_usd_24h
            sell_vol_usd_1h = sell_vol_usd_24h

            logger.debug(f"{symbol} 未找到 24 小時內數據，改用最新一筆清算資料")

        total_vol_usd_24h = buy_vol_usd_24h + sell_vol_usd_24h
        total_vol_usd_1h = buy_vol_usd_1h + sell_vol_usd_1h
        threshold_1h, threshold_24h = get_liquidation_threshold(symbol)

        # 記錄實際清算數據供調試
        logger.info(
            f"{symbol} 清算統計 - 1h: ${total_vol_usd_1h/10000:.2f}萬 (門檻: ${threshold_1h/10000:.2f}萬), "
            f"24h: ${total_vol_usd_24h/10000:.2f}萬 (門檻: ${threshold_24h/10000:.2f}萬)"
        )

        # 只檢查1小時門檻：只有過去1小時達到門檻時才推播
        triggered_by_1h = total_vol_usd_1h >= threshold_1h
        
        if not triggered_by_1h:
            logger.debug(
                f"{symbol} 未達1小時門檻 - 1h: {total_vol_usd_1h/10000:.2f}萬 < {threshold_1h/10000:.2f}萬"
            )
            return None

        # 判斷主導清算方向（只用1小時數據）
        is_long_dom = buy_vol_usd_1h > sell_vol_usd_1h
        dominant_side = "多單（做多／看漲倉位）" if is_long_dom else "空單（做空／看跌倉位）"
        dominant_amount_1h = buy_vol_usd_1h if is_long_dom else sell_vol_usd_1h

        logger.info(
            f"{symbol} ⚠️ 觸發警報 (1小時極端爆倉) - 過去1h: ${(buy_vol_usd_1h + sell_vol_usd_1h)/10000:.2f}萬"
        )

        return {
            "symbol": symbol,
            "dominantSide": dominant_side,
            "dominantAmount1h": dominant_amount_1h,
            "totalVolUsd1h": total_vol_usd_1h,
            "buyVolUsd1h": buy_vol_usd_1h,
            "sellVolUsd1h": sell_vol_usd_1h,
        }
    except Exception as e:
        logger.error(f"處理 {symbol} 清算數據時發生錯誤: {str(e)}")
        return None


# 移除 generate_liq_symbol_analysis 函數（不再需要診斷文字）


def format_liquidity_consolidated_message(events: List[Dict]) -> str:
    """【主力清算·撿屍雷達】別人恐懼我貪婪，帶血籌碼最香。"""
    now_str = datetime.now(TAIPEI_TZ).strftime("%H:%M")
    lines = []
    lines.append("🩸 *【主力清算 · 撿屍雷達】*")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    total_vol = sum(e.get("totalVolUsd1h", 0) for e in events)
    lines.append(f"☠️ 過去1小時，這些幣種爆倉 *${total_vol / 10000:.0f}萬*")
    lines.append("")

    events_sorted = sorted(events, key=lambda e: e.get("totalVolUsd1h", 0), reverse=True)
    for ev in events_sorted:
        amt = ev.get("dominantAmount1h", 0) / 10_000
        side = ev.get("dominantSide", "")
        sym = ev.get("symbol", "")
        rsi_lbl = ev.get("rsi_label", "")
        pin_lbl = ev.get("pin_label", "")
        confirm = ev.get("confirm_reason", "")
        entry_low = ev.get("entry_zone_low")
        entry_high = ev.get("entry_zone_high")
        cur_price = ev.get("cur_price")

        if "多" in side:
            icon = "🟢"
            title = "多軍陣亡 → 帶血籌碼出現"
            advice = "👉 分批佈局做多，止損設最低點下方 1%"
            entry_action = "抄底進場區"
        else:
            icon = "🔴"
            title = "空軍陣亡 → 軋空行情起爆"
            advice = "👉 回測確認不破位後，考慮追空或等待反轉"
            entry_action = "摸頂進場區"

        lines.append(f"{icon} *{sym}* 💥 爆倉 *${amt:.1f}萬*")
        lines.append(f"💀 {title}")
        if rsi_lbl:
            lines.append(f"📊 {rsi_lbl}")
        if pin_lbl:
            lines.append(f"  {pin_lbl}")
        if confirm:
            lines.append(f"✅ 確認信號：{confirm}")
        # 建議進場區間
        if entry_low and entry_high and cur_price:
            lines.append(f"🎯 *{entry_action}*：`${entry_low}` ~ `${entry_high}`（現價 `${cur_price:.4f}`）")
        lines.append(f"💡 策略：{advice}")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(f"⏰ {now_str} | 別人恐懼我貪婪，帶血籌碼最香。")
    return "\n".join(lines)


def _fetch_liq_radar_analysis_1m(symbol: str) -> Dict:
    """為撿屍雷達抓取 1m K 線，計算 RSI 與長下影線（針形態）。
    優先使用 CoinGlass /api/futures/price/history（interval=1m）；
    失敗時備援 BingX 1m K 線。
    返回：{"rsi": float|None, "has_pin": bool, "lower_shadow_ratio": float,
           "cur_price": float|None, "entry_zone_low": float|None, "entry_zone_high": float|None}
    """
    result: Dict = {"rsi": None, "has_pin": False, "lower_shadow_ratio": 0.0,
                    "cur_price": None, "entry_zone_low": None, "entry_zone_high": None}
    clean = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()

    opens, highs, lows, closes = [], [], [], []

    # ── 優先：CoinGlass futures/price/history 1m ──────────────────────────
    try:
        for ex, sym_pair in [("Binance", f"{clean}USDT"), ("OKX", f"{clean}USDT")]:
            _respect_coinglass_rate_limit()
            r = requests.get(
                f"{CG_API_BASE}/api/futures/price/history",
                headers={"CG-API-KEY": CG_API_KEY, "accept": "application/json"},
                params={"exchange": ex, "symbol": sym_pair, "interval": "1m", "limit": 50},
                timeout=8,
            )
            if r.status_code == 200:
                j = r.json()
                if j.get("code") in (0, "0", 200, "200", None):
                    raw_cg = j.get("data") or j.get("list") or []
                    if isinstance(raw_cg, list) and len(raw_cg) >= 16:
                        _o, _h, _l, _c, _ = _parse_kline_rows(raw_cg)
                        if len(_c) >= 16:
                            opens, highs, lows, closes = _o, _h, _l, _c
                            break
    except Exception as e:
        logger.debug(f"[撿屍雷達-CG] {clean} 1m K線異常: {e}")

    # ── 備援：BingX 1m K 線 ───────────────────────────────────────────────
    if len(closes) < 16:
        try:
            bingx_sym = f"{clean}-USDT"
            r2 = requests.get(
                "https://open-api.bingx.com/openApi/swap/v3/quote/klines",
                params={"symbol": bingx_sym, "interval": "1m", "limit": 50},
                timeout=8,
            )
            if r2.status_code == 200:
                raw2 = r2.json()
                candles = raw2.get("data") or raw2.get("result") or (raw2 if isinstance(raw2, list) else [])
                if isinstance(candles, list) and len(candles) >= 16:
                    for c in candles:
                        if isinstance(c, dict):
                            opens.append(float(c.get("open") or c.get("o") or 0))
                            highs.append(float(c.get("high") or c.get("h") or 0))
                            lows.append(float(c.get("low") or c.get("l") or 0))
                            closes.append(float(c.get("close") or c.get("c") or 0))
                        elif isinstance(c, (list, tuple)) and len(c) >= 5:
                            opens.append(float(c[1]))
                            highs.append(float(c[2]))
                            lows.append(float(c[3]))
                            closes.append(float(c[4]))
        except Exception as e:
            logger.debug(f"[撿屍雷達-BX] {clean} 1m K線異常: {e}")

    if len(closes) < 15:
        return result

    try:
        # RSI 14 期
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [max(d, 0.0) for d in deltas]
        losses = [max(-d, 0.0) for d in deltas]
        avg_gain = sum(gains[-14:]) / 14.0
        avg_loss = sum(losses[-14:]) / 14.0
        if avg_loss > 0:
            rs = avg_gain / avg_loss
            result["rsi"] = round(100.0 - (100.0 / (1.0 + rs)), 1)
        else:
            result["rsi"] = 100.0

        # 最新一根 K 線的長下影線（下針）形態判斷
        o, h, l, c_last = opens[-1], highs[-1], lows[-1], closes[-1]
        if h > l:
            body = abs(c_last - o)
            lower_shadow = min(o, c_last) - l  # 下影線長度
            total_range = h - l
            upper_shadow = h - max(o, c_last)
            # 下影線佔總振幅 40% 以上 + 下影線 > 實體的 2 倍
            lower_shadow_ratio = lower_shadow / total_range if total_range > 0 else 0.0
            result["lower_shadow_ratio"] = round(lower_shadow_ratio, 3)
            result["has_pin"] = (
                lower_shadow_ratio >= 0.40
                and (body == 0 or lower_shadow >= body * 2.0)
                and lower_shadow >= upper_shadow  # 下影線比上影線長
            )

        # 現價與建議進場區間（基於最近 5 根 K 線的低點 + 2% 緩衝）
        result["cur_price"] = closes[-1]
        recent_low = min(lows[-5:]) if len(lows) >= 5 else lows[-1]
        recent_high = max(highs[-5:]) if len(highs) >= 5 else highs[-1]
        result["entry_zone_low"] = round(recent_low * 0.995, 6)   # 低點下方 0.5%
        result["entry_zone_high"] = round(min(c_last * 1.003, recent_high * 0.998), 6)  # 現價上方 0.3%

        logger.info(
            f"[撿屍分析] {symbol} RSI={result['rsi']} has_pin={result['has_pin']} "
            f"lower_shadow_ratio={result['lower_shadow_ratio']:.2f} cur={closes[-1]}"
        )
    except Exception as e:
        logger.warning(f"[撿屍RSI] {symbol} 1m 分析失敗: {e}")
    return result


def _fetch_liq_coin_list_snapshot() -> Dict[str, Dict]:
    """取得全市場幣種爆倉快照（liq_coin_list），回傳 base -> {long_usd, short_usd, total_usd}。
    用於雷達掃描前快速找出「正在爆倉」的幣種，而非等待逐幣輪詢。
    """
    out: Dict[str, Dict] = {}
    logger.debug(f"[爆倉快照] endpoint={CG_EP['liq_coin_list']}")
    try:
        j = _cg_get(CG_EP["liq_coin_list"], {"timeType": "0"})  # timeType=0=過去1小時
        if not j:
            return out
        rows = j.get("data") or j.get("list") or []
        for row in (rows if isinstance(rows, list) else []):
            if not isinstance(row, dict):
                continue
            base = row.get("symbol") or row.get("coin") or ""
            if not base:
                continue
            base = str(base).upper().replace("USDT", "")
            try:
                long_usd = float(row.get("longLiqUsd") or row.get("buyLiqUsd") or
                                  row.get("long") or 0)
                short_usd = float(row.get("shortLiqUsd") or row.get("sellLiqUsd") or
                                   row.get("short") or 0)
                total_usd = long_usd + short_usd
                if total_usd > 0:
                    out[base] = {"long_usd": long_usd, "short_usd": short_usd,
                                  "total_usd": total_usd}
            except (TypeError, ValueError):
                continue
        logger.debug(f"[爆倉快照] 解析到 {len(out)} 幣種爆倉數據")
    except Exception as e:
        logger.debug(f"[爆倉快照] 異常: {e}")
    return out


def run_liquidity_radar_once():
    """主流程：流動性獵取雷達（執行一次，適合排程或 HTTP 觸發）
    升級版三重確認濾網：
      1. liq_coin_list 爆倉快照：先找出正在爆倉的幣種（避免逐幣輪詢）
      2. RSI 1m 極端區間（<20 超賣 / >80 超買）+ 長下/上影線針形態
      3. 累積費率輔助確認（空頭過熱 + 多單爆倉 = 嘎空機率大）
    """
    logger.info(f"開始執行流動性獵取雷達（三重確認升級版），共 {len(LIQ_SYMBOLS)} 個幣種...")

    # 優先用 liq_coin_list 快照找出「已在爆倉」的幣種
    liq_snapshot = _fetch_liq_coin_list_snapshot()
    hot_symbols = set()
    if liq_snapshot:
        # 門檻：過去1小時爆倉量 > 50萬 USD
        for base_s, data_s in liq_snapshot.items():
            if data_s["total_usd"] >= 500_000:
                hot_symbols.add(base_s)
        logger.info(f"[撿屍雷達] 快照找到 {len(hot_symbols)} 個熱點幣種（爆倉>50萬USD）：{sorted(hot_symbols)[:10]}")

    # 合併 LIQ_SYMBOLS 與快照熱點（熱點優先）
    scan_symbols = []
    for sym_s in LIQ_SYMBOLS:
        base_s = sym_s.replace("USDT", "").replace("-", "").upper()
        is_hot = base_s in hot_symbols
        scan_symbols.append((sym_s, is_hot))
    # 熱點優先排序
    scan_symbols.sort(key=lambda x: (0 if x[1] else 1))

    events: List[Dict] = []

    for idx, (symbol, is_hot_sym) in enumerate(scan_symbols):
        base_sym = symbol.replace("USDT", "").replace("-", "").upper()
        try:
            # 快照前置過濾：非熱點幣種且快照有資料時，降低 OI 門檻
            snap_data = liq_snapshot.get(base_sym)
            if snap_data:
                logger.debug(f"[撿屍雷達] {symbol} 快照爆倉量 ${snap_data['total_usd']/1e6:.2f}M"
                             f" 多:{snap_data['long_usd']/1e6:.2f}M 空:{snap_data['short_usd']/1e6:.2f}M")

            data_array = fetch_liquidation_data(symbol)
            if data_array is None:
                if idx < len(scan_symbols) - 1:
                    time.sleep(LIQ_REQUEST_DELAY)
                continue
            event = process_liquidation_data(symbol, data_array)
            if not event:
                if idx < len(scan_symbols) - 1:
                    time.sleep(LIQ_REQUEST_DELAY)
                continue

            # 三重確認：RSI 極端區間 + 針形態 + 累積費率
            analysis = _fetch_liq_radar_analysis_1m(symbol)
            rsi_1m = analysis.get("rsi")
            has_pin = analysis.get("has_pin", False)
            lower_shadow_ratio = analysis.get("lower_shadow_ratio", 0.0)
            cur_price = analysis.get("cur_price")
            entry_low = analysis.get("entry_zone_low")
            entry_high = analysis.get("entry_zone_high")

            dominant_side = event.get("dominantSide", "")
            is_long_liq = "多" in dominant_side  # 多單爆倉 → 價格急跌

            rsi_ok = False
            rsi_label = ""
            pin_label = ""
            confirm_reason = []

            if rsi_1m is None:
                rsi_ok = True
                rsi_label = "RSI 未確認（資料不足）"
                logger.warning(f"[撿屍雷達] {symbol} 無法取得 1m RSI，放行但標記未確認")
            else:
                if is_long_liq:
                    if rsi_1m < 20:
                        rsi_ok = True
                        rsi_label = f"🔴 RSI 1m={rsi_1m:.0f} 極度超賣（恐慌衰竭）"
                        confirm_reason.append("RSI極端超賣")
                    elif rsi_1m < 25:
                        rsi_ok = True
                        rsi_label = f"🟡 RSI 1m={rsi_1m:.0f} 深度超賣"
                        confirm_reason.append("RSI深度超賣")
                else:
                    if rsi_1m > 80:
                        rsi_ok = True
                        rsi_label = f"🔴 RSI 1m={rsi_1m:.0f} 極度超買（軋空衰竭）"
                        confirm_reason.append("RSI極端超買")
                    elif rsi_1m > 75:
                        rsi_ok = True
                        rsi_label = f"🟡 RSI 1m={rsi_1m:.0f} 深度超買"
                        confirm_reason.append("RSI深度超買")

            # 針形態獨立確認
            if has_pin and is_long_liq:
                rsi_ok = True
                pin_label = f"📌 長下影線針（下影={lower_shadow_ratio:.0%}），恐慌衰竭形態"
                confirm_reason.append("針形態")
            elif has_pin and not is_long_liq:
                pin_label = f"📌 長上影線針（上影形態），軋空衰竭"
                confirm_reason.append("針形態")

            # 累積費率輔助確認（第三重）
            accum_fr_label_liq = ""
            try:
                accum_data_liq = fetch_accumulated_funding_score(symbol)
                sq_risk = accum_data_liq.get("squeeze_risk") or "neutral"
                if is_long_liq and sq_risk == "short_squeeze":
                    # 多單爆倉 + 空頭費用過高 = 嘎空前的最後洗盤，逆轉機率極大
                    accum_fr_label_liq = f"🔥 空頭費用過高({accum_data_liq.get('accumulated_7d',0)*100:.2f}%累積)，嘎空潛力巨大"
                    if not rsi_ok:  # 費率條件可以部分補強
                        confirm_reason.append("費率嘎空")
                        rsi_ok = True
                        rsi_label = rsi_label or "費率嘎空補強"
                elif not is_long_liq and sq_risk == "long_squeeze":
                    accum_fr_label_liq = f"⛽ 多頭費用過高({accum_data_liq.get('accumulated_7d',0)*100:.2f}%累積)，殺多潛力大"
            except Exception:
                pass

            if not rsi_ok:
                logger.info(f"[撿屍雷達] {symbol} 爆倉但未通過三重確認 RSI={rsi_1m} pin={has_pin}，跳過")
                if idx < len(scan_symbols) - 1:
                    time.sleep(LIQ_REQUEST_DELAY)
                continue

            event["rsi_1m"] = rsi_1m
            event["rsi_label"] = rsi_label
            event["pin_label"] = pin_label
            event["confirm_reason"] = "、".join(confirm_reason) if confirm_reason else "條件放行"
            event["accum_fr_label"] = accum_fr_label_liq
            event["is_hot"] = is_hot_sym
            event["snap_total_usd"] = snap_data["total_usd"] if snap_data else 0
            event["cur_price"] = cur_price
            event["entry_zone_low"] = entry_low
            event["entry_zone_high"] = entry_high
            events.append(event)
            logger.info(f"[撿屍雷達] {symbol} 通過確認（{event['confirm_reason']}），加入推播"
                        + (" 🔥熱點" if is_hot_sym else ""))

            if idx < len(scan_symbols) - 1:
                time.sleep(LIQ_REQUEST_DELAY)
        except Exception as e:
            logger.error(f"處理 {symbol} 流動性數據時發生錯誤: {str(e)}")

    if not events:
        logger.info("本次監控無幣種達到極端爆倉門檻（或均未通過雙重確認：RSI<20 / 針形態）")
        return

    msg = format_liquidity_consolidated_message(events)
    thread_id = TG_THREAD_IDS.get("liquidity_radar", 3)
    keyboard = {
        "inline_keyboard": [[{"text": "💀 查看詳細爆倉數據", "url": "https://www.coinglass.com/zh-TW/LiquidationData"}]]
    }
    send_telegram_message(msg, thread_id, parse_mode="Markdown", reply_markup=keyboard)
    logger.info(f"流動性獵取雷達完成，推送 {len(events)} 個幣種（雙重確認通過）")


# ==================== 9. 山寨爆發雷達（Altcoin Season + RSI + Buy Ratio） ====================

def _coinglass_simple_get(path: str, params: Optional[Dict] = None) -> Optional[Dict]:
    """簡化版 GET，主要給 Altseason / RSI 這類單次查詢用"""
    if not CG_API_KEY:
        logger.error("CG_API_KEY 未設定，無法呼叫 CoinGlass API")
        return None
    url = f"{CG_API_BASE}{path}"
    headers = {
        "accept": "application/json",
        "CG-API-KEY": CG_API_KEY,
    }
    try:
        resp = requests.get(url, headers=headers, params=params or {}, timeout=10)
        if resp.status_code != 200:
            logger.error(f"CoinGlass API HTTP 錯誤 {path}: {resp.status_code} - {resp.text[:200]}")
            return None
        data = resp.json()
        if data.get("code") not in (0, "0", 200, "200", None) and not data.get("success", True):
            logger.error(f"CoinGlass API 返回錯誤 {path}: {data}")
            return None
        return data
    except Exception as e:
        logger.error(f"CoinGlass API 請求失敗 {path}: {str(e)}")
        return None


def fetch_altseason_index() -> Optional[float]:
    """取得山寨季指數 (0-100)"""
    data = _coinglass_simple_get("/api/index/altcoin-season")
    if not data:
        logger.warning("Altseason API 回傳為空")
        return None

    # 記錄原始數據結構以便調試
    logger.debug(f"Altseason API 原始回傳: {json.dumps(data, ensure_ascii=False)[:500]}")

    # 嘗試多種可能的數據結構
    val = None
    
    # 1) 如果 data 是 dict
    if isinstance(data.get("data"), dict):
        inner = data["data"]
        # 嘗試更多可能的欄位名稱
        for key in ("value", "index", "altcoinSeasonIndex", "altcoin_season_index", 
                    "seasonIndex", "season_index", "altcoinIndex", "altcoin_index",
                    "score", "ratio", "percentage"):
            if inner.get(key) is not None:
                val = inner.get(key)
                logger.debug(f"從 data[dict] 中找到欄位 {key}: {val}")
                break
    
    # 2) 如果 data 是 list
    elif isinstance(data.get("data"), list) and data["data"]:
        # 取最後一筆（最新的）
        inner = data["data"][-1]
        if isinstance(inner, dict):
            for key in ("value", "index", "altcoinSeasonIndex", "altcoin_season_index",
                        "seasonIndex", "season_index", "altcoinIndex", "altcoin_index",
                        "score", "ratio", "percentage"):
                if inner.get(key) is not None:
                    val = inner.get(key)
                    logger.debug(f"從 data[list][-1] 中找到欄位 {key}: {val}")
                    break
    
    # 3) 直接在頂層找
    if val is None:
        for key in ("value", "index", "altcoinSeasonIndex", "altcoin_season_index",
                    "seasonIndex", "season_index", "altcoinIndex", "altcoin_index",
                    "score", "ratio", "percentage"):
            if data.get(key) is not None:
                val = data.get(key)
                logger.debug(f"從頂層找到欄位 {key}: {val}")
                break
    
    # 4) 如果還是找不到，嘗試遍歷所有數值欄位
    if val is None:
        def find_numeric_value(obj, depth=0):
            if depth > 3:  # 避免遞迴太深
                return None
            if isinstance(obj, (int, float)):
                if 0 <= obj <= 100:  # 山寨季指數應該在 0-100 之間
                    return obj
            elif isinstance(obj, dict):
                for v in obj.values():
                    result = find_numeric_value(v, depth + 1)
                    if result is not None:
                        return result
            elif isinstance(obj, list):
                for item in obj:
                    result = find_numeric_value(item, depth + 1)
                    if result is not None:
                        return result
            return None
        
        val = find_numeric_value(data)
        if val is not None:
            logger.debug(f"透過深度搜尋找到數值: {val}")

    # 轉換為 float
    if val is not None:
        try:
            result = float(val)
            # 驗證範圍
            if 0 <= result <= 100:
                logger.info(f"成功取得 Altseason 指數: {result}")
                return result
            else:
                logger.warning(f"Altseason 指數超出範圍 (0-100): {result}")
        except (TypeError, ValueError) as e:
            logger.warning(f"Altseason 指數轉換失敗: {val} - {str(e)}")
    
    logger.warning(f"無法從 Altseason API 回傳中提取指數，原始數據: {json.dumps(data, ensure_ascii=False)[:500]}")
    return None


def describe_altseason(index_val: Optional[float]) -> str:
    if index_val is None:
        return "資料暫缺，暫時無法明確判斷是山寨季還是比特幣季。"
    if index_val > 75:
        return "🌋 山寨季狂歡：資金大幅流向山寨幣，波動與風險同步放大，小幣暴漲暴跌機率極高。"
    if index_val < 25:
        return "🛡 比特幣季：資金主要圍繞 BTC 等主流資產，山寨普漲可能還需要耐心等待。"
    return "⚖ 資金在比特幣與山寨之間相對均衡，領頭羊個別表現更重要。"


def fetch_rsi_list() -> List[Dict]:
    """取得 RSI 列表並轉成標準化的 dict list，不依賴 pandas"""
    data = _coinglass_simple_get("/api/futures/rsi/list")
    if not data:
        return []

    raw = data.get("data") or data.get("list") or []
    if not isinstance(raw, list) or not raw:
        logger.warning("RSI 列表為空或格式異常")
        return []

    # 標準化欄位名稱
    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        
        # 找 symbol 欄位
        symbol = None
        for key in ["symbol", "pair", "coin", "symbolName"]:
            if key in item:
                symbol = str(item[key])
                break
        if not symbol:
            continue

        # 找 RSI 欄位
        rsi_1h = None
        rsi_4h = None
        for key, val in item.items():
            kl = key.lower()
            if "rsi" in kl:
                if "1h" in kl or "h1" in kl:
                    try:
                        rsi_1h = float(val) if val is not None else None
                    except (TypeError, ValueError):
                        pass
                elif "4h" in kl or "h4" in kl:
                    try:
                        rsi_4h = float(val) if val is not None else None
                    except (TypeError, ValueError):
                        pass

        # 找成交量欄位
        volume = None
        for key, val in item.items():
            kl = key.lower()
            if "volume" in kl or "turnover" in kl or "amount" in kl:
                try:
                    volume = float(val) if val is not None else None
                except (TypeError, ValueError):
                    pass
                if volume is not None:
                    break

        result.append({
            "symbol": symbol,
            "rsi_1h": rsi_1h,
            "rsi_4h": rsi_4h,
            "volume": volume
        })

    return result


# ── 全市場 RSI 批次快取（供掃描周期前一次性預載，enrichment 直接查表）─────────────
_cg_rsi_bulk_cache: Dict[str, Any] = {"ts": 0.0, "data": {}}  # {base: {rsi_15m, rsi_1h, ...}}
_CG_RSI_BULK_TTL = 120.0  # 2 分鐘 TTL，配合 15m 週期


def fetch_cg_rsi_bulk(interval: str = "15m") -> Dict[str, Optional[float]]:
    """批次取得全市場 RSI（CoinGlass /api/futures/rsi/list）。
    回傳 {base_symbol: rsi_float} dict；快取 2 分鐘。
    在 fetch_position_change 掃描開始前呼叫一次，enrichment 階段直接 dict 查表，
    不需要對每個幣種單獨呼叫 API。
    """
    global _cg_rsi_bulk_cache
    now = time.time()
    if now - _cg_rsi_bulk_cache["ts"] < _CG_RSI_BULK_TTL and _cg_rsi_bulk_cache["data"]:
        return _cg_rsi_bulk_cache["data"]

    if not CG_API_KEY:
        return {}

    try:
        _respect_coinglass_rate_limit()
        r = requests.get(
            f"{CG_API_BASE}/api/futures/rsi/list",
            headers={"CG-API-KEY": CG_API_KEY, "accept": "application/json"},
            params={"interval": interval},
            timeout=12,
        )
        if r.status_code != 200:
            logger.debug(f"[RSI批次] HTTP {r.status_code}")
            return _cg_rsi_bulk_cache.get("data", {})
        j = r.json()
        if j.get("code") not in (0, "0", 200, "200", None):
            logger.debug(f"[RSI批次] code={j.get('code')}")
            return _cg_rsi_bulk_cache.get("data", {})

        raw = j.get("data") or j.get("list") or (j if isinstance(j, list) else [])
        out: Dict[str, Optional[float]] = {}
        for item in (raw if isinstance(raw, list) else []):
            if not isinstance(item, dict):
                continue
            # symbol 欄位
            sym = (item.get("symbol") or item.get("pair") or item.get("coin") or "").upper()
            sym = sym.replace("USDT", "").replace("-", "").replace("_", "").strip()
            if not sym:
                continue
            # RSI 值：優先指定 interval，再試各種欄位名
            rsi_val = None
            interval_key = interval.lower().replace("m", "m").replace("h", "h")
            for k in item:
                kl = k.lower()
                if "rsi" in kl and (interval_key in kl or kl in ("rsi", "rsivalue", "rsi_value")):
                    try:
                        rsi_val = float(item[k])
                        break
                    except (TypeError, ValueError):
                        pass
            # 若未找到，嘗試直接的 rsi 欄位
            if rsi_val is None:
                for k in ("rsi", "RSI", "rsiValue", "rsi_value", "value"):
                    if k in item and item[k] is not None:
                        try:
                            rsi_val = float(item[k])
                            break
                        except (TypeError, ValueError):
                            pass
            out[sym] = rsi_val

        logger.info(f"[RSI批次] 取得 {len(out)} 個幣種 {interval} RSI（CoinGlass rsi/list）")
        _cg_rsi_bulk_cache = {"ts": now, "data": out}
        return out
    except Exception as e:
        logger.debug(f"[RSI批次] 異常: {e}")
        return _cg_rsi_bulk_cache.get("data", {})


def fetch_buy_ratio(symbol: str) -> Optional[float]:
    """
    近似計算某幣種的 Buy Ratio（由聚合掛單深度近似，bids / (bids + asks)）
    使用 /api/futures/orderbook/aggregated-ask-bids-history
    """
    data = _coinglass_simple_get(
        "/api/futures/orderbook/aggregated-ask-bids-history",
        params={"exchange_list": "Binance", "symbol": symbol, "interval": "h1"},
    )
    if not data:
        return None

    arr = data.get("data") or data.get("list") or []
    if not isinstance(arr, list) or not arr:
        return None

    last = arr[-1]
    if isinstance(last, dict):
        # 嘗試多種欄位名稱
        bid_keys = [k for k in last.keys() if "bid" in k.lower()]
        ask_keys = [k for k in last.keys() if "ask" in k.lower()]
        bid_val = float(last.get(bid_keys[0]) or 0) if bid_keys else 0.0
        ask_val = float(last.get(ask_keys[0]) or 0) if ask_keys else 0.0
    elif isinstance(last, list):
        # 假設結構 [bids, asks, time] 或 [asks, bids, time]，儘量容錯
        numeric = [x for x in last if isinstance(x, (int, float))]
        if len(numeric) >= 2:
            # 假設第一個是 bids，第二個是 asks
            bid_val, ask_val = float(numeric[0]), float(numeric[1])
        else:
            return None
    else:
        return None

    total = bid_val + ask_val
    if total <= 0:
        return None
    return bid_val / total * 100.0  # 轉成百分比


def fetch_price_history(symbol: str, interval: str = "1h") -> Optional[List[Dict]]:
    """獲取價格歷史數據（OHLC）
    注意：CoinGlass API v4 可能沒有直接的 price/history 端點
    這裡使用 OI history 端點，因為它通常包含 markPrice 等價格信息
    """
    url = f"{CG_API_BASE}/api/futures/open-interest/history"
    params = {
        "exchange": "Binance",
        "symbol": symbol,
        "interval": interval
    }
    headers = {
        "CG-API-KEY": CG_API_KEY,
        "accept": "application/json"
    }
    
    try:
        logger.debug(f"嘗試獲取價格歷史 {symbol}，使用 OI history 端點")
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('code') in ['0', 0, 200, '200']:
                data_list = data.get('data', [])
                if isinstance(data_list, list) and len(data_list) > 0:
                    # 檢查數據結構，看是否有價格字段
                    sample = data_list[0]
                    sample_keys = list(sample.keys()) if isinstance(sample, dict) else []
                    logger.debug(f"價格歷史數據樣本 {symbol}: 字段 {sample_keys[:15]}")
                    logger.debug(f"價格歷史數據樣本 {symbol}: 內容 {json.dumps(sample, ensure_ascii=False)[:200]}")
                    
                    # 返回數據列表（即使沒有標準價格字段也返回，讓後續邏輯處理）
                    logger.debug(f"從 OI 端點獲取到數據 {symbol}: {len(data_list)} 條")
                    # 輸出數據樣本以便調試
                    if isinstance(sample, dict):
                        logger.debug(f"數據樣本字段: {list(sample.keys())[:20]}")
                    return data_list
        
        logger.debug(f"無法從 OI 端點獲取價格數據 for {symbol} (狀態碼: {response.status_code})")
        return None
    except Exception as e:
        logger.warning(f"獲取價格歷史失敗 {symbol}: {str(e)}")
        import traceback
        logger.debug(traceback.format_exc())
        return None


def fetch_aggregated_cvd_history(symbol: str, interval: str = "1h") -> Optional[List[Dict]]:
    """獲取聚合累計成交量差值（CVD）歷史數據。
    優先嘗試 /api/futures/aggregated-cvd/history（跨交易所聚合）；
    失敗時備援 /api/futures/cvd/history（單一交易所，預設 Binance）。
    統一回傳標準化 list[dict]（含 time/cvd 欄位）。
    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    headers_cg = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}

    def _parse_cvd_list(data_list: list) -> Optional[List[Dict]]:
        if not isinstance(data_list, list) or not data_list:
            return None
        # 標準化欄位：統一為 {"time": ts, "cvd": value}
        out = []
        for item in data_list:
            if not isinstance(item, dict):
                continue
            ts = (item.get("time") or item.get("timestamp") or item.get("t")
                  or item.get("createTime") or 0)
            cvd = None
            for k in ("cum_vol_delta", "cvd", "value", "cvdValue",
                      "cumulativeVolumeDelta", "volumeDelta", "netVolume"):
                if item.get(k) is not None:
                    try:
                        cvd = float(item[k])
                        break
                    except (TypeError, ValueError):
                        pass
            if cvd is not None:
                out.append({"time": int(ts), "cvd": cvd, "_raw": item})
        return out if out else None

    # ── 優先：聚合 CVD（多所 aggregated）────────────────────────────────────
    try:
        _respect_coinglass_rate_limit()
        r = requests.get(
            f"{CG_API_BASE}/api/futures/aggregated-cvd/history",
            headers=headers_cg,
            params={"exchange_list": "Binance,OKX,Bybit", "symbol": base, "interval": interval},
            timeout=10,
        )
        if r.status_code == 200:
            j = r.json()
            if j.get("code") in ("0", 0, 200, "200"):
                result = _parse_cvd_list(j.get("data") or j.get("list") or [])
                if result:
                    logger.debug(f"[CVD-聚合] {base} {interval}: {len(result)} 條")
                    return result
    except Exception as e:
        logger.debug(f"[CVD-聚合] {base} 異常: {e}")

    # ── 備援：單所 CVD（Binance）────────────────────────────────────────────
    try:
        _respect_coinglass_rate_limit()
        r2 = requests.get(
            f"{CG_API_BASE}/api/futures/cvd/history",
            headers=headers_cg,
            params={"exchange": "Binance", "symbol": base, "interval": interval},
            timeout=10,
        )
        if r2.status_code == 200:
            j2 = r2.json()
            if j2.get("code") in ("0", 0, 200, "200"):
                result2 = _parse_cvd_list(j2.get("data") or j2.get("list") or [])
                if result2:
                    logger.debug(f"[CVD-單所] {base} {interval}: {len(result2)} 條")
                    return result2
    except Exception as e:
        logger.debug(f"[CVD-單所] {base} 異常: {e}")

    logger.debug(f"[CVD] {base} {interval} 兩個端點均無數據")
    return None


def _cvd_change_last2(symbol: str, interval: str = "1h") -> Optional[float]:
    """取最近 2 根 K 的 CVD 變化值 (Current - Prev)，用於過濾量價背離。
    fetch_aggregated_cvd_history 已回傳標準化 {"time", "cvd"} 格式，直接使用。
    """
    data = fetch_aggregated_cvd_history(symbol, interval)
    if not data or len(data) < 2:
        return None
    sorted_data = sorted(data, key=lambda x: x.get("time", 0))
    last_two = sorted_data[-2:]
    cvd_vals = []
    for item in last_two:
        # 優先取標準化欄位 "cvd"，再試舊式欄位名（相容舊資料）
        v = item.get("cvd")
        if v is None:
            for key in ("value", "cvdValue", "cumulativeVolumeDelta",
                        "cum_vol_delta", "volumeDelta", "netVolume"):
                if item.get(key) is not None:
                    try:
                        v = float(item[key])
                        break
                    except (TypeError, ValueError):
                        pass
        if v is not None:
            try:
                cvd_vals.append(float(v))
            except (TypeError, ValueError):
                pass
    if len(cvd_vals) != 2:
        return None
    return cvd_vals[1] - cvd_vals[0]


def detect_cvd_divergence(symbol: str) -> Optional[str]:
    """檢測 CVD 背離（看漲/看跌）
    返回: 'bullish' (看漲背離), 'bearish' (看跌背離), None (無背離)
    
    優化版本：
    - 擴大比較窗口到 20 根 K 線（約 24 小時數據）
    - 對比當前價格與過去 20 根 K 線的高低點
    - 對比當前 CVD 與對應價格高低點時的 CVD 值
    """
    try:
        # 獲取最近 24 小時的 1h 數據
        logger.info(f"CVD 背離檢測 {symbol}: 開始檢測...")
        price_data = fetch_price_history(symbol + "USDT", "1h")
        base_symbol = symbol.replace("USDT", "")
        cvd_data = fetch_aggregated_cvd_history(base_symbol, "1h")
        
        logger.info(f"CVD 背離檢測 {symbol}: 獲取到價格數據 {len(price_data) if price_data else 0} 條, CVD 數據 {len(cvd_data) if cvd_data else 0} 條")
        
        if not price_data or not cvd_data:
            logger.info(f"CVD 背離檢測 {symbol}: 數據不足（價格: {len(price_data) if price_data else 0}, CVD: {len(cvd_data) if cvd_data else 0}）")
            return None
        
        if len(price_data) < 20 or len(cvd_data) < 20:
            logger.info(f"CVD 背離檢測 {symbol}: 數據點不足（需要至少 20 個，價格: {len(price_data)}, CVD: {len(cvd_data)}）")
            return None
        
        # 定義排序鍵函數（處理 None 值）
        def get_sort_key(x):
            time_val = x.get('time') or x.get('timestamp') or x.get('t') or 0
            if isinstance(time_val, str):
                try:
                    return int(time_val)
                except:
                    return 0
            return int(time_val) if time_val else 0
        
        # 確保數據按時間排序
        price_sorted = sorted(price_data, key=get_sort_key)
        cvd_sorted = sorted(cvd_data, key=get_sort_key)
        
        # 取最近 20 根 K 線
        p_slice = price_sorted[-20:]
        c_slice = cvd_sorted[-20:]
        
        # 提取價格的輔助函數（嘗試多種字段）
        def extract_price(item: Dict, field: str) -> Optional[float]:
            """從數據項中提取價格字段"""
            if not isinstance(item, dict):
                return None
            if field in item:
                val = item[field]
                if isinstance(val, (int, float)) and val > 0:
                    return float(val)
            return None
        
        # 提取當前 K 線的 high 和 low
        curr_item = p_slice[-1]
        curr_p_high = extract_price(curr_item, 'high') or extract_price(curr_item, 'markPrice') or extract_price(curr_item, 'mark_price') or extract_price(curr_item, 'close') or extract_price(curr_item, 'price') or extract_price(curr_item, 'value')
        curr_p_low = extract_price(curr_item, 'low') or extract_price(curr_item, 'markPrice') or extract_price(curr_item, 'mark_price') or extract_price(curr_item, 'close') or extract_price(curr_item, 'price') or extract_price(curr_item, 'value')
        
        if not curr_p_high or not curr_p_low:
            logger.info(f"CVD 背離檢測 {symbol}: 無法提取當前價格（high: {curr_p_high}, low: {curr_p_low}），數據樣本字段: {list(curr_item.keys())[:10]}")
            return None
        
        # 提取當前 K 線的 CVD
        curr_cvd_item = c_slice[-1]
        curr_cvd = None
        # 添加實際的字段名稱：cum_vol_delta（累計成交量差值）
        for key in ['cum_vol_delta', 'cvd', 'value', 'close', 'cvdValue', 'cumulativeVolumeDelta', 'volumeDelta', 'agg_taker_buy_vol', 'agg_taker_sell_vol']:
            if key in curr_cvd_item:
                val = curr_cvd_item[key]
                if isinstance(val, (int, float)) and val != 0:
                    curr_cvd = float(val)
                    logger.debug(f"CVD 背離檢測 {symbol}: 從字段 '{key}' 提取到當前 CVD: {curr_cvd}")
                    break
        
        if curr_cvd is None:
            logger.info(f"CVD 背離檢測 {symbol}: 無法提取當前 CVD 值，CVD 數據樣本字段: {list(curr_cvd_item.keys())[:10]}")
            return None
        
        # 找到過去 19 根 K 線的最高/最低價
        prev_prices_high = []
        prev_prices_low = []
        
        # 輸出第一個過去 K 線的字段以便調試
        if len(p_slice) > 1:
            sample_prev_item = p_slice[0]
            logger.debug(f"CVD 背離檢測 {symbol}: 過去 K 線樣本字段: {list(sample_prev_item.keys())[:15]}")
        
        for idx, item in enumerate(p_slice[:-1]):  # 過去 19 根
            if not isinstance(item, dict):
                continue
                
            # 嘗試提取 high（優先使用 high，如果沒有則使用其他字段）
            high = extract_price(item, 'high')
            if not high:
                # 如果沒有 high，嘗試使用其他價格字段
                high = extract_price(item, 'markPrice') or extract_price(item, 'mark_price') or extract_price(item, 'close') or extract_price(item, 'price') or extract_price(item, 'value')
            
            # 嘗試提取 low（優先使用 low，如果沒有則使用其他字段）
            low = extract_price(item, 'low')
            if not low:
                # 如果沒有 low，嘗試使用其他價格字段
                low = extract_price(item, 'markPrice') or extract_price(item, 'mark_price') or extract_price(item, 'close') or extract_price(item, 'price') or extract_price(item, 'value')
            
            # 如果還是沒有，嘗試所有數值字段
            if not high or not low:
                for key, val in item.items():
                    if isinstance(val, (int, float)) and val > 0:
                        key_lower = key.lower()
                        # 跳過明顯不是價格的字段
                        if ('time' not in key_lower and 'timestamp' not in key_lower and 
                            'volume' not in key_lower and 'openInterest' not in key_lower and
                            'oi' not in key_lower and 'open_interest' not in key_lower and
                            'funding' not in key_lower and 'rate' not in key_lower and
                            'cvd' not in key_lower and 'delta' not in key_lower):
                            if not high:
                                high = float(val)
                            if not low:
                                low = float(val)
                            if high and low:
                                break
            
            if high:
                prev_prices_high.append(high)
            if low:
                prev_prices_low.append(low)
        
        if not prev_prices_high or not prev_prices_low:
            logger.info(f"CVD 背離檢測 {symbol}: 無法提取過去價格數據（high: {len(prev_prices_high)}, low: {len(prev_prices_low)}），當前 K 線字段: {list(p_slice[-1].keys())[:15] if p_slice else []}")
            return None
        
        prev_p_high = max(prev_prices_high)
        prev_p_low = min(prev_prices_low)
        
        # 獲取最高價與最低價對應的 CVD 值
        # 找到最高價對應的索引（使用更寬鬆的匹配，找到最接近的值）
        high_idx = None
        min_diff = float('inf')
        for idx, item in enumerate(p_slice[:-1]):
            high = extract_price(item, 'high') or extract_price(item, 'markPrice') or extract_price(item, 'mark_price') or extract_price(item, 'close') or extract_price(item, 'price') or extract_price(item, 'value')
            if high:
                diff = abs(high - prev_p_high)
                if diff < min_diff:
                    min_diff = diff
                    high_idx = idx
                    if diff < 0.01:  # 如果找到非常接近的值，直接使用
                        break
        
        # 找到最低價對應的索引（使用更寬鬆的匹配，找到最接近的值）
        low_idx = None
        min_diff = float('inf')
        for idx, item in enumerate(p_slice[:-1]):
            low = extract_price(item, 'low') or extract_price(item, 'markPrice') or extract_price(item, 'mark_price') or extract_price(item, 'close') or extract_price(item, 'price') or extract_price(item, 'value')
            if low:
                diff = abs(low - prev_p_low)
                if diff < min_diff:
                    min_diff = diff
                    low_idx = idx
                    if diff < 0.01:  # 如果找到非常接近的值，直接使用
                        break
        
        if high_idx is None or low_idx is None:
            logger.info(f"CVD 背離檢測 {symbol}: 無法找到對應的價格索引（high_idx: {high_idx}, low_idx: {low_idx}, 過去最高價: {prev_p_high:.4f}, 過去最低價: {prev_p_low:.4f}）")
            return None
        
        # 提取對應索引的 CVD 值
        cvd_at_p_high = None
        cvd_at_p_low = None
        
        if high_idx < len(c_slice[:-1]):
            high_cvd_item = c_slice[high_idx]
            # 添加實際的字段名稱：cum_vol_delta（累計成交量差值）
            for key in ['cum_vol_delta', 'cvd', 'value', 'close', 'cvdValue', 'cumulativeVolumeDelta', 'volumeDelta', 'agg_taker_buy_vol', 'agg_taker_sell_vol']:
                if key in high_cvd_item:
                    val = high_cvd_item[key]
                    if isinstance(val, (int, float)) and val != 0:
                        cvd_at_p_high = float(val)
                        break
        
        if low_idx < len(c_slice[:-1]):
            low_cvd_item = c_slice[low_idx]
            # 添加實際的字段名稱：cum_vol_delta（累計成交量差值）
            for key in ['cum_vol_delta', 'cvd', 'value', 'close', 'cvdValue', 'cumulativeVolumeDelta', 'volumeDelta', 'agg_taker_buy_vol', 'agg_taker_sell_vol']:
                if key in low_cvd_item:
                    val = low_cvd_item[key]
                    if isinstance(val, (int, float)) and val != 0:
                        cvd_at_p_low = float(val)
                        break
        
        if cvd_at_p_high is None or cvd_at_p_low is None:
            logger.info(f"CVD 背離檢測 {symbol}: 無法提取對應的 CVD 值（high_idx: {high_idx}, low_idx: {low_idx}, cvd_at_p_high: {cvd_at_p_high}, cvd_at_p_low: {cvd_at_p_low}）")
            return None
        
        # 看跌背離：價格創高，但 CVD 低於當時高點的 CVD
        if curr_p_high > prev_p_high and curr_cvd < cvd_at_p_high:
            logger.info(f"CVD 背離檢測 {symbol}: ✅ 看跌背離 (價格: {curr_p_high:.4f} > {prev_p_high:.4f}, CVD: {curr_cvd:.2f} < {cvd_at_p_high:.2f})")
            return 'bearish'
        
        # 看漲背離：價格創低，但 CVD 高於當時低點的 CVD
        if curr_p_low < prev_p_low and curr_cvd > cvd_at_p_low:
            logger.info(f"CVD 背離檢測 {symbol}: ✅ 看漲背離 (價格: {curr_p_low:.4f} < {prev_p_low:.4f}, CVD: {curr_cvd:.2f} > {cvd_at_p_low:.2f})")
            return 'bullish'
        
        logger.info(f"CVD 背離檢測 {symbol}: 無背離信號 (當前價格: {curr_p_high:.4f}/{curr_p_low:.4f}, 過去高低: {prev_p_high:.4f}/{prev_p_low:.4f}, 當前 CVD: {curr_cvd:.2f})")
        return None
        
    except Exception as e:
        logger.error(f"CVD 背離檢測出錯 {symbol}: {str(e)}")
        import traceback
        logger.debug(traceback.format_exc())
        return None


def build_altseason_message() -> Optional[str]:
    """【山寨暴富列車】抓板塊輪動，強者恆強。"""
    index_val = fetch_altseason_index()
    rsi_list = fetch_rsi_list()
    if not rsi_list:
        return None

    rsi_with_vol = [r for r in rsi_list if r.get("volume") is not None]
    if rsi_with_vol:
        rsi_with_vol.sort(key=lambda x: x.get("volume") or 0, reverse=True)
        rsi_list = rsi_with_vol[:60]
    for item in rsi_list:
        item.setdefault("rsi_4h", item.get("rsi_base"))
        item.setdefault("rsi_base", item.get("rsi_4h"))

    def attach_buy_ratio(items: List[Dict]) -> List[Dict]:
        res = []
        for item in items:
            sym = (item.get("symbol") or "").replace("USDT", "")
            ratio = fetch_buy_ratio(sym) or fetch_buy_ratio(item.get("symbol", ""))
            item["buy_ratio"] = ratio if ratio is not None else 50.0
            res.append(item)
            time.sleep(0.3)
        return res

    strong = [r for r in rsi_list if (r.get("rsi_4h") or r.get("rsi_base") or 0) >= 65]
    if strong:
        strong = attach_buy_ratio(strong[:8])
        strong.sort(key=lambda x: x.get("buy_ratio", 0), reverse=True)

    lines = []
    lines.append("🎢 *【山寨暴富列車】*")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    season_status = "🛡️ 比特幣吸血中 (防守)"
    if index_val is not None and index_val > 70:
        season_status = "🌋 群魔亂舞 (山寨季)"
    elif index_val is not None and index_val > 40:
        season_status = "⚖️ 資金輪動中 (選幣)"
    lines.append(f"🌍 *當前週期*：{season_status}")
    lines.append(f"📊 *山寨指數*：`{index_val:.0f}` / 100" if index_val is not None else "📊 *山寨指數*：—")
    lines.append("")
    lines.append("🔥 *強勢領頭羊 (資金正在炒)*")
    if not strong:
        lines.append("暫無強勢幣種，市場低迷。")
    else:
        for i, item in enumerate(strong[:5], 1):
            sym = item.get("symbol", "")
            br = item.get("buy_ratio", 50)
            rsi = item.get("rsi_4h") or item.get("rsi_base", 50)
            lines.append(f"{i}. *{sym}* (買盤 {br:.0f}%)")
            lines.append(f"   👉 RSI {rsi:.0f} ｜ 動能強勁，回調可接")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append("💡 *操作心法*：強者恆強。在山寨季，不要買落後補漲的垃圾，要買就買龍頭！")
    return "\n".join(lines)


def run_altseason_radar_once():
    """山寨暴富列車主流程（含按鈕）"""
    logger.info("開始執行山寨爆發雷達...")
    msg = build_altseason_message()
    if not msg:
        logger.warning("本次山寨爆發雷達未能產生有效訊息")
        return
    thread_id = TG_THREAD_IDS.get("altseason_radar", 0)
    keyboard = {"inline_keyboard": [[{"text": "🎢 查看山寨季指數", "url": "https://www.blockchaincenter.net/en/altcoin-season-index/"}]]}
    send_telegram_message(msg, thread_id or int(CHAT_ID or 0), parse_mode="Markdown", reply_markup=keyboard)
    logger.info("山寨爆發雷達推播完成")


# ==================== 10. Hyperliquid 聰明錢監控 ====================

HYPERLIQUID_SENT_ALERTS_FILE = DATA_DIR / "hyperliquid_sent_alerts.json"
WHALE_ALERT_THRESHOLD = 200_000  # 保留供其他地方引用，實際邏輯改為動態門檻
SMART_MONEY_PNL_MIN = 50_000  # $50k USD（放寬）
MONEY_PRINTER_PNL_MIN = 500_000  # $50萬 USD（放寬）

# 動態門檻配置
_WHALE_MAINSTREAM_COINS = {"BTC", "ETH", "SOL"}
_WHALE_THRESHOLD_MAINSTREAM = 500_000   # 主流幣 $50萬
_WHALE_THRESHOLD_ALTCOIN_RATIO = 0.005  # 山寨幣：24h 成交量的 0.5%
_WHALE_THRESHOLD_ALTCOIN_DEFAULT = 50_000  # 山寨幣備援門檻 $5萬


def _get_whale_threshold(symbol: str, alert: Dict) -> float:
    """根據幣種計算動態鯨魚門檻。
    主流幣 (BTC/ETH/SOL) → $50萬固定；山寨幣 → 24h 成交量 × 0.5%（無資料則 $5萬）。
    """
    base = symbol.replace("USDT", "").replace("-PERP", "").replace("PERP", "").strip().upper()
    if base in _WHALE_MAINSTREAM_COINS:
        return _WHALE_THRESHOLD_MAINSTREAM

    # 嘗試從 alert 中提取 24h 成交量
    vol_keys = [
        'volume_24h', 'vol_24h', 'volume24h', 'daily_volume', 'turnover_24h',
        'quoteVolume24h', 'quote_volume_24h',
    ]
    vol_24h: Optional[float] = None
    for k in vol_keys:
        raw = alert.get(k)
        if raw is not None:
            try:
                v = float(str(raw).replace(',', '').replace('$', '').strip())
                if v > 0:
                    vol_24h = v
                    break
            except (TypeError, ValueError):
                pass

    if vol_24h and vol_24h > 0:
        dynamic = vol_24h * _WHALE_THRESHOLD_ALTCOIN_RATIO
        return max(dynamic, 10_000)  # 最低 $1 萬保護

    return _WHALE_THRESHOLD_ALTCOIN_DEFAULT


def fetch_hyperliquid_whale_alert() -> List[Dict]:
    """獲取 Hyperliquid 鯨魚提醒（動態門檻版：主流幣 $50萬；山寨幣 24h 量 0.5%）"""
    url = f"{CG_API_BASE}/api/hyperliquid/whale-alert"
    headers = {
        "CG-API-KEY": CG_API_KEY,
        "accept": "application/json"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.error(f"Hyperliquid Whale Alert API 錯誤: {response.status_code}")
            return []

        result = response.json()
        if result.get('code') not in ['0', 0, 200, '200']:
            logger.error(f"Hyperliquid Whale Alert API 返回錯誤: {result}")
            return []

        data_list = result.get('data', [])
        if not isinstance(data_list, list):
            logger.warning(f"Hyperliquid Whale Alert 數據格式異常: {type(data_list)}")
            return []

        # 調試：記錄原始數據
        logger.info(f"Hyperliquid Whale Alert 原始數據: {len(data_list)} 條")
        if data_list:
            sample = data_list[0]
            logger.info(f"數據樣本欄位: {list(sample.keys())}")
            logger.info(f"數據樣本完整內容: {json.dumps(sample, ensure_ascii=False, indent=2)}")

        filtered_alerts = []
        value_stats = []  # 調試用

        for idx, alert in enumerate(data_list):
            value = None
            value_key = None

            # 按優先順序嘗試各種字段名稱（優先使用 position_value_usd）
            possible_keys = [
                'position_value_usd', 'positionValueUsd', 'position_value', 'positionValue',
                'notional_value', 'notionalValue', 'notional', 'notional_usd',
                'value', 'value_usd', 'usd_value', 'usdValue',
                'size_usd', 'sizeUSD', 'size',
                'amount', 'amount_usd', 'amountUSD',
                'volume', 'volume_usd', 'volumeUSD',
                'trade_value', 'tradeValue', 'trade_value_usd',
                'order_value', 'orderValue', 'order_value_usd',
                'total_value', 'totalValue', 'total_value_usd'
            ]

            for key in possible_keys:
                if key in alert and alert[key] is not None:
                    value = alert[key]
                    value_key = key
                    break

            if value is None:
                excluded_keys = {'entry_price', 'liq_price', 'mark_price', 'leverage',
                                 'position_size', 'create_time', 'update_time'}
                for key, val in alert.items():
                    if key.lower() in excluded_keys:
                        continue
                    if isinstance(val, (int, float)) and val >= 1000:
                        value = val
                        value_key = key
                        break

            if value is None:
                logger.warning(f"Alert #{idx} 無法找到數值字段，所有字段: {list(alert.keys())}")
                continue

            try:
                if isinstance(value, str):
                    value_clean = value.replace(',', '').replace('$', '').replace(' ', '').replace('USD', '').replace('usd', '')
                    value_float = float(value_clean)
                else:
                    value_float = float(value)

                sym_raw = alert.get('symbol') or alert.get('coin') or alert.get('asset') or '未知'
                # 計算動態門檻
                threshold = _get_whale_threshold(str(sym_raw), alert)
                base_sym = str(sym_raw).replace("USDT", "").replace("-PERP", "").replace("PERP", "").strip().upper()
                threshold_label = (
                    f"主流幣 ${threshold/10000:.0f}萬"
                    if base_sym in _WHALE_MAINSTREAM_COINS
                    else f"山寨動態 ${threshold/10000:.1f}萬"
                )

                if idx < 10:
                    value_stats.append({
                        'symbol': sym_raw,
                        'key': value_key,
                        'value': value_float,
                        'threshold': threshold,
                        'formatted': f"${value_float/10000:.2f}萬 (門檻:{threshold_label})"
                    })

                if value_float >= threshold:
                    filtered_alerts.append(alert)
                    logger.info(f"✅ 聰明錢進場: {sym_raw} - ${value_float/10000:.2f}萬 ≥ {threshold_label} (字段: {value_key})")
                else:
                    if idx < 5:
                        logger.info(f"❌ 未達動態門檻: {sym_raw} - ${value_float/10000:.2f}萬 < {threshold_label} (字段: {value_key})")
            except (TypeError, ValueError) as e:
                logger.warning(f"Alert #{idx} 數值解析失敗: 字段={value_key}, 值={value}, 錯誤: {str(e)}")
                continue

        if value_stats:
            logger.info("前10條數據的數值統計:")
            for stat in value_stats:
                logger.info(f"  {stat['symbol']}: {stat['formatted']} (字段: {stat['key']})")

        logger.info(f"符合動態門檻的 Whale Alert: {len(filtered_alerts)} 條（主流幣 ${_WHALE_THRESHOLD_MAINSTREAM/10000:.0f}萬 | 山寨幣動態0.5%量）")
        return filtered_alerts
    except Exception as e:
        logger.error(f"獲取 Hyperliquid Whale Alert 失敗: {str(e)}")
        return []


def fetch_hyperliquid_coin_position(symbol: str) -> Optional[Dict]:
    """【新】取得特定幣種在 Hyperliquid 的持倉分布（多空方向、槓桿、規模）。
    用途：判斷 HL 上的真實持倉方向，輔助鯨魚意圖分析。
    endpoint: /api/hyperliquid/position
    """
    base = symbol.replace("USDT", "").replace("-PERP", "").replace("PERP", "").upper()
    logger.debug(f"[HL持倉] {base} endpoint={CG_EP['hl_position']}")
    try:
        j = _cg_get(CG_EP["hl_position"], {"symbol": base})
        if not j:
            return None
        data = j.get("data") or j.get("list") or j
        if not data:
            return None
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict):
            return None

        long_pos = float(data.get("longPositionUsd") or data.get("longUsd") or
                         data.get("long") or 0)
        short_pos = float(data.get("shortPositionUsd") or data.get("shortUsd") or
                          data.get("short") or 0)
        total = long_pos + short_pos
        ls_ratio = long_pos / short_pos if short_pos > 0 else None

        logger.info(
            f"[HL持倉] {base}: 多倉 ${long_pos/1e6:.2f}M 空倉 ${short_pos/1e6:.2f}M "
            f"多空比={ls_ratio:.2f}" if ls_ratio else
            f"[HL持倉] {base}: 多倉 ${long_pos/1e6:.2f}M 空倉 ${short_pos/1e6:.2f}M"
        )
        return {"long_usd": long_pos, "short_usd": short_pos, "total_usd": total,
                "ls_ratio": ls_ratio, "symbol": base}
    except Exception as e:
        logger.debug(f"[HL持倉] {base} 異常: {e}")
        return None


def fetch_hyperliquid_smart_money_score(symbol: str) -> Dict[str, Any]:
    """【新】聰明錢評分：整合 HL 持倉分布 + 錢包盈虧分布，判斷方向與信心。
    評分邏輯：
      - HL 多空比 > 1.2（多頭主導）+ 盈利錢包佔多數 → 聰明錢偏多，score > 0
      - HL 多空比 < 0.8（空頭主導）+ 盈利錢包佔多數 → 聰明錢偏空，score < 0
      - 盈虧分布 + 持倉方向一致 → 信心加分
    回傳：{"score": int, "direction": "long"/"short"/"neutral", "label": str, "hl_ls": float}
    """
    base = symbol.replace("USDT", "").replace("-PERP", "").upper()
    cache_key = f"hl_smart:{base}"
    now = time.time()
    if cache_key in _flow_cache:
        val, ts = _flow_cache[cache_key]
        if now - ts < 600:  # 10 分鐘快取
            return val if val else {"score": 0, "direction": "neutral", "label": "", "hl_ls": None}

    empty = {"score": 0, "direction": "neutral", "label": "", "hl_ls": None}

    # ── 取 HL 幣種持倉 ────────────────────────────────────────────────
    hl_pos = fetch_hyperliquid_coin_position(symbol)
    hl_ls = hl_pos.get("ls_ratio") if hl_pos else None

    # ── 取 HL 錢包盈虧分布（全市場，反映整體聰明錢狀態）────────────
    pnl_score = 0
    pnl_label_part = ""
    try:
        j_pnl = _cg_get(CG_EP["hl_wallet_pnl_dist"], {})
        if j_pnl:
            data_pnl = j_pnl.get("data") or j_pnl.get("list") or j_pnl
            if isinstance(data_pnl, dict):
                profit_pct = float(data_pnl.get("profitablePercent") or
                                   data_pnl.get("winRate") or
                                   data_pnl.get("profitable") or 0)
                # 盈利錢包 > 60% = 聰明錢環境，大多數都在賺
                if profit_pct > 60:
                    pnl_score = 1
                    pnl_label_part = f"(HL盈利錢包{profit_pct:.0f}%)"
                elif profit_pct < 40:
                    pnl_score = -1
                    pnl_label_part = f"(HL虧損錢包多{100-profit_pct:.0f}%)"
                logger.debug(f"[HL聰明錢] {base} 全市場盈利率={profit_pct:.1f}%")
    except Exception as e_pnl:
        logger.debug(f"[HL聰明錢] {base} 盈虧分布異常: {e_pnl}")

    # ── 取 HL 錢包持倉分布（判斷大錢包 vs 小錢包方向）────────────────
    pos_dist_label = ""
    try:
        j_dist = _cg_get(CG_EP["hl_wallet_pos_dist"], {"symbol": base})
        if j_dist:
            data_dist = j_dist.get("data") or j_dist.get("list") or j_dist
            if isinstance(data_dist, dict):
                large_long = float(data_dist.get("largeWalletLong") or
                                   data_dist.get("whaleNetLong") or 0)
                large_short = float(data_dist.get("largeWalletShort") or
                                    data_dist.get("whaleNetShort") or 0)
                if large_long > large_short * 1.3:
                    pos_dist_label = "大錢包偏多"
                    pnl_score += 1
                elif large_short > large_long * 1.3:
                    pos_dist_label = "大錢包偏空"
                    pnl_score -= 1
                logger.debug(f"[HL聰明錢] {base} 大錢包多={large_long:.0f} 空={large_short:.0f}")
    except Exception as e_dist:
        logger.debug(f"[HL聰明錢] {base} 持倉分布異常: {e_dist}")

    # ── 綜合評分 ─────────────────────────────────────────────────────
    final_score = pnl_score
    if hl_ls is not None:
        if hl_ls > 1.2:
            final_score += 1
        elif hl_ls < 0.8:
            final_score -= 1

    direction = "neutral"
    if final_score >= 2:
        direction = "long"
    elif final_score <= -2:
        direction = "short"

    hl_ls_str = f"{hl_ls:.2f}" if hl_ls else "N/A"
    if direction == "long":
        label = f"🟢 HL聰明錢偏多(評分{final_score:+d} L/S={hl_ls_str}{pnl_label_part}{' '+pos_dist_label if pos_dist_label else ''})"
    elif direction == "short":
        label = f"🔴 HL聰明錢偏空(評分{final_score:+d} L/S={hl_ls_str}{pnl_label_part}{' '+pos_dist_label if pos_dist_label else ''})"
    else:
        label = f"🟡 HL聰明錢中性(L/S={hl_ls_str})" if hl_ls else ""

    result = {"score": final_score, "direction": direction, "label": label, "hl_ls": hl_ls}
    logger.info(f"[HL聰明錢✅] {base}: {label}")
    _flow_cache[cache_key] = (result, now)
    return result


def fetch_hyperliquid_pnl_distribution() -> Optional[Dict]:
    """獲取 Hyperliquid 錢包盈虧分佈（全市場）"""
    logger.debug(f"[HL盈虧] endpoint={CG_EP['hl_wallet_pnl_dist']}")
    try:
        j = _cg_get(CG_EP["hl_wallet_pnl_dist"], {})
        return j.get("data", j) if j else None
    except Exception as e:
        logger.error(f"[HL盈虧] 異常: {e}")
        return None


def fetch_hyperliquid_whale_position() -> List[Dict]:
    """獲取 Hyperliquid 鯨魚持倉（價值 > $100k）"""
    url = f"{CG_API_BASE}/api/hyperliquid/whale-position"
    headers = {
        "CG-API-KEY": CG_API_KEY,
        "accept": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.error(f"Hyperliquid Whale Position API 錯誤: {response.status_code}")
            return []
        
        result = response.json()
        if result.get('code') not in ['0', 0, 200, '200']:
            logger.error(f"Hyperliquid Whale Position API 返回錯誤: {result}")
            return []
        
        data_list = result.get('data', [])
        if not isinstance(data_list, list):
            return []
        
        # 記錄第一個位置的數據結構以便調試（只在有數據時）
        if data_list:
            first_item = data_list[0]
            logger.info(f"Hyperliquid Whale Position 數據結構示例（前 3 個欄位）: {list(first_item.keys())[:10]}")
            logger.info(f"完整數據結構: {json.dumps(first_item, ensure_ascii=False, indent=2)[:1000]}")
        
        # 嘗試提取持倉價值的多種可能欄位
        def get_position_value(item: Dict) -> float:
            # 嘗試直接的值欄位
            value = (
                item.get('position_value') or 
                item.get('positionValue') or 
                item.get('value') or 
                item.get('notional_value') or
                item.get('notionalValue') or
                item.get('size_usd') or
                item.get('sizeUSD') or
                item.get('usd_value') or
                item.get('usdValue') or
                0
            )
            
            # 如果直接值不存在，嘗試用 size * price 計算
            if value == 0 or (isinstance(value, (int, float)) and value == 0):
                size = float(item.get('size') or item.get('position_size') or item.get('positionSize') or 0)
                price = float(item.get('price') or item.get('mark_price') or item.get('markPrice') or 0)
                if size > 0 and price > 0:
                    value = abs(size * price)
            
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0
        
        # 排序並取前 5 名（按持倉價值）
        sorted_positions = sorted(
            data_list,
            key=get_position_value,
            reverse=True
        )[:5]
        
        return sorted_positions
    except Exception as e:
        logger.error(f"獲取 Hyperliquid Whale Position 失敗: {str(e)}")
        return []


def process_smart_money_pnl(pnl_data: Dict) -> Dict:
    """處理聰明錢 PNL 分佈數據"""
    if not pnl_data or not isinstance(pnl_data, dict):
        return {}
    
    smart_money_info = {
        'money_printers': [],  # > $1M 獲利
        'smart_money': [],     # $100k - $1M 獲利
        'top_symbols': {}
    }
    
    # 嘗試解析分層數據
    # 可能的結構：分層列表或直接包含數據
    distribution_list = (
        pnl_data.get('distribution') or 
        pnl_data.get('data') or 
        pnl_data.get('list') or 
        []
    )
    
    if isinstance(distribution_list, list):
        for item in distribution_list:
            if not isinstance(item, dict):
                continue
            
            # 獲取 PNL 範圍
            pnl_min = float(item.get('pnl_min') or item.get('pnlMin') or item.get('min_pnl') or 0)
            pnl_max = float(item.get('pnl_max') or item.get('pnlMax') or item.get('max_pnl') or float('inf'))
            address_count = int(item.get('address_count') or item.get('addressCount') or item.get('count') or 0)
            
            # 判斷層級
            if pnl_min >= MONEY_PRINTER_PNL_MIN:
                smart_money_info['money_printers'].append({
                    'pnl_range': f"${pnl_min/1000:.0f}k - ${pnl_max/1000:.0f}k" if pnl_max < float('inf') else f"> ${pnl_min/1000:.0f}k",
                    'address_count': address_count
                })
            elif pnl_min >= SMART_MONEY_PNL_MIN and pnl_max <= MONEY_PRINTER_PNL_MIN:
                smart_money_info['smart_money'].append({
                    'pnl_range': f"${pnl_min/1000:.0f}k - ${pnl_max/1000:.0f}k",
                    'address_count': address_count
                })
    
    # 嘗試獲取持倉分佈（按幣種）
    position_dist = pnl_data.get('position_distribution') or pnl_data.get('top_symbols') or {}
    if isinstance(position_dist, dict):
        # 排序並取前 3 個幣種
        sorted_symbols = sorted(
            position_dist.items(),
            key=lambda x: float(x[1].get('value') or x[1].get('total_value') or 0) if isinstance(x[1], dict) else float(x[1] or 0),
            reverse=True
        )[:3]
        
        for symbol, data in sorted_symbols:
            if isinstance(data, dict):
                bias = data.get('bias') or data.get('long_ratio') or 0
                smart_money_info['top_symbols'][symbol] = {
                    'bias': float(bias) * 100 if bias < 1 else float(bias)
                }
    
    return smart_money_info


def format_alert_message(alert: Dict) -> str:
    """格式化單個 Whale Alert 訊息"""
    symbol = alert.get('symbol') or alert.get('coin') or '未知'
    direction = alert.get('side') or alert.get('direction') or alert.get('type') or '未知'
    value = float(
        alert.get('notional_value') or 
        alert.get('notionalValue') or 
        alert.get('value') or 
        0
    )
    
    # 判斷方向 emoji（做多=看漲，做空=看跌）
    direction_emoji = "🟢" if str(direction).lower() in ['long', 'buy', '多', 'long'] else "🔴"
    direction_text = "大額開多（看漲）" if str(direction).lower() in ['long', 'buy', '多', 'long'] else "大額開空（看跌）"
    
    return f"項目：`{symbol}`\n方向：{direction_emoji} {direction_text}\n規模：${value:,.0f} USD (名目價值)"


def format_whale_position_message(position: Dict, index: int) -> str:
    """格式化單個鯨魚持倉訊息"""
    address = position.get('address') or position.get('user') or position.get('user_address') or '未知'
    symbol = position.get('symbol') or position.get('coin') or position.get('asset') or '未知'
    side = position.get('side') or position.get('direction') or position.get('position_side') or '未知'
    
    # 嘗試多種方式獲取持倉價值
    size = (
        position.get('position_value') or 
        position.get('positionValue') or 
        position.get('value') or 
        position.get('notional_value') or
        position.get('notionalValue') or
        position.get('size_usd') or
        position.get('sizeUSD') or
        position.get('usd_value') or
        position.get('usdValue') or
        0
    )
    
    # 如果直接值不存在，嘗試用 size * price 計算
    try:
        size_float = float(size) if size else 0.0
    except (TypeError, ValueError):
        size_float = 0.0
    
    if size_float == 0:
        position_size = float(position.get('size') or position.get('position_size') or position.get('positionSize') or 0)
        price = float(position.get('price') or position.get('mark_price') or position.get('markPrice') or 0)
        if position_size > 0 and price > 0:
            size_float = abs(position_size * price)
    
    leverage = float(position.get('leverage') or position.get('leverage_ratio') or position.get('leverageRatio') or 1)
    
    # 簡化地址顯示（只顯示後 4 位）
    address_short = address[-4:] if len(address) > 4 else address
    
    # 判斷多空方向（白話文：做多=看漲，做空=看跌）
    side_lower = str(side).lower()
    side_text = "做多（看漲）" if side_lower in ['long', 'buy', '多', 'l'] else "做空（看跌）"
    
    # 格式化金額顯示
    if size_float >= 1_000_000:
        size_display = f"${size_float/1_000_000:.2f}M"
    elif size_float >= 1_000:
        size_display = f"${size_float/1_000:.2f}K"
    else:
        size_display = f"${size_float:.2f}"
    
    return f"{index}. 地址 `...{address_short}` | 倉位：{size_display} [{symbol} {side_text}] | 槓桿：{leverage:.1f}x"


def build_hyperliquid_message() -> Optional[str]:
    """組合 Hyperliquid 聰明錢監控訊息（僅在有新的 Whale Alert 時推播）"""
    logger.info("開始構建 Hyperliquid 聰明錢監控訊息...")
    
    # 1. 獲取 Whale Alert
    alerts = fetch_hyperliquid_whale_alert()
    logger.info(f"獲取到 {len(alerts)} 個 Whale Alert")
    
    # 檢查是否有新的 Alert（避免重複推播）
    sent_alert_ids = load_json_file(HYPERLIQUID_SENT_ALERTS_FILE, [])
    new_alerts = []
    new_alert_ids = []
    
    for alert in alerts:
        # 生成唯一 ID（使用時間戳 + symbol + value）
        alert_id = f"{alert.get('time') or alert.get('timestamp')}_{alert.get('symbol')}_{alert.get('notional_value') or alert.get('notionalValue')}"
        if alert_id not in sent_alert_ids:
            new_alerts.append(alert)
            new_alert_ids.append(alert_id)
    
    # ⚠️ 重要：只在有新的 Whale Alert 時才推播，避免洗頻
    if not new_alerts:
        logger.info("本次監控期間無新的大額交易提醒（> $1M），跳過推播")
        return None
    
    # 2. 獲取 PNL Distribution（僅作為補充資訊）
    pnl_data = fetch_hyperliquid_pnl_distribution()
    smart_money_info = process_smart_money_pnl(pnl_data) if pnl_data else {}
    
    # 3. 獲取 Whale Position（僅作為補充資訊）
    whale_positions = fetch_hyperliquid_whale_position()
    logger.info(f"獲取到 {len(whale_positions)} 個鯨魚持倉")
    
    # 構建訊息（僅在有新的 Alert 時才構建）
    lines = []
    lines.append("🐳 *【區塊鏈船長 - Hyperliquid 鯨魚追蹤】*")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    
    # Whale Alert 部分（主要內容，包含開倉時間、標的、方向）
    lines.append("🚨 *巨鯨即時預警 (Whale Alert)*：")
    for alert in new_alerts[:5]:  # 最多顯示 5 個
        symbol = alert.get('symbol') or alert.get('coin') or '未知'
        
        # 獲取USD價值（優先使用 position_value_usd）
        value = float(
            alert.get('position_value_usd') or 
            alert.get('positionValueUsd') or 
            alert.get('position_value') or 
            alert.get('positionValue') or 
            alert.get('notional_value') or 
            alert.get('notionalValue') or 
            alert.get('value') or 
            0
        )
        
        # 獲取開倉時間（create_time 是毫秒時間戳）
        alert_time = alert.get('create_time') or alert.get('time') or alert.get('timestamp') or alert.get('open_time')
        time_str = "時間未知"
        if alert_time:
            try:
                if isinstance(alert_time, (int, float)):
                    # create_time 是毫秒時間戳（例如 1768536078000）
                    if alert_time > 1e12:
                        dt = datetime.fromtimestamp(alert_time / 1000, tz=timezone.utc)
                    else:
                        dt = datetime.fromtimestamp(alert_time, tz=timezone.utc)
                    # 轉換為台灣時間
                    dt_taipei = get_taipei_time(dt)
                    time_str = dt_taipei.strftime("%Y-%m-%d %H:%M")
                else:
                    time_str = str(alert_time)
            except Exception as e:
                logger.debug(f"時間解析失敗: {alert_time}, 錯誤: {str(e)}")
                time_str = "時間未知"
        
        # 判斷方向（根據 position_size 正負或 position_action）
        position_size = alert.get('position_size') or alert.get('positionSize') or 0
        position_action = alert.get('position_action') or alert.get('positionAction')
        side = alert.get('side') or alert.get('direction') or alert.get('type')
        
        # 判斷方向邏輯：
        # 1. 如果有 side/direction/type 字段，直接使用
        # 2. 如果 position_size > 0，可能是做多；< 0 可能是做空
        # 3. position_action: 1=開多, 2=開空, 3=平多, 4=平空
        if side:
            direction_text = "做多（看漲）" if str(side).lower() in ['long', 'buy', '多', 'l', '1'] else "做空（看跌）"
        elif position_action is not None:
            # position_action: 1=開多, 2=開空
            if position_action == 1:
                direction_text = "做多（看漲）"
            elif position_action == 2:
                direction_text = "做空（看跌）"
            else:
                direction_text = "未知"
        elif isinstance(position_size, (int, float)):
            # 根據 position_size 正負判斷（正數可能是做多，負數可能是做空）
            direction_text = "做多（看漲）" if position_size > 0 else "做空（看跌）"
        else:
            direction_text = "未知"
        
        direction_emoji = "🟢" if "做多" in direction_text else "🔴"
        
        # 格式化價值顯示
        if value >= 1_000_000:
            value_display = f"${value/1_000_000:.2f}M"
        elif value >= 1_000:
            value_display = f"${value/1_000:.2f}K"
        else:
            value_display = f"${value:,.0f}"
        
        # 取得進場價（用於 VWAP 成本比對）
        entry_price = alert.get('entry_price') or alert.get('entryPrice') or alert.get('avg_price') or alert.get('avgPrice')
        mark_price = alert.get('mark_price') or alert.get('markPrice') or alert.get('price')
        liq_price = alert.get('liq_price') or alert.get('liquidationPrice') or alert.get('liquidation_price')
        leverage = alert.get('leverage') or alert.get('leverageRatio') or alert.get('leverage_ratio')

        # 嘗試抓取現價（從 Binance 公開 API，免費）
        current_market_price: Optional[float] = None
        _sym_clean = str(symbol).replace("-PERP", "").replace("PERP", "").replace("-", "").upper()
        if not _sym_clean.endswith("USDT"):
            _binance_sym = f"{_sym_clean}USDT"
        else:
            _binance_sym = _sym_clean
        try:
            _bp_resp = requests.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": _binance_sym}, timeout=4
            )
            if _bp_resp.status_code == 200:
                current_market_price = float(_bp_resp.json().get("price", 0)) or None
        except Exception:
            pass

        # VWAP 成本 vs 現價分析
        cost_analysis = ""
        whale_intent = ""
        cost_ref = None
        if entry_price:
            try:
                cost_ref = float(entry_price)
            except (TypeError, ValueError):
                cost_ref = None
        if cost_ref is None and mark_price:
            try:
                cost_ref = float(mark_price)
            except (TypeError, ValueError):
                pass

        if cost_ref and current_market_price and current_market_price > 0 and cost_ref > 0:
            deviation_pct = (current_market_price - cost_ref) / cost_ref * 100.0
            if "做多" in direction_text:
                if deviation_pct > 2.0:
                    cost_analysis = f"⚠️ 追高風險：現價已比大戶成本高 `+{deviation_pct:.1f}%`，跟單需謹慎"
                elif deviation_pct < -1.0:
                    cost_analysis = f"🛡️ 強力支撐位：現價回測至大戶成本 `{cost_ref:.4f}`（偏差 `{deviation_pct:.1f}%`），支撐有效"
                else:
                    cost_analysis = f"✅ 貼近大戶成本 `{cost_ref:.4f}`（偏差 `{deviation_pct:+.1f}%`），跟單風險低"
            else:  # 做空
                if deviation_pct < -2.0:
                    cost_analysis = f"⚠️ 追空風險：現價已比大戶成本低 `{deviation_pct:.1f}%`，跟單需謹慎"
                elif deviation_pct > 1.0:
                    cost_analysis = f"🛡️ 壓力區：現價高於大戶空單成本 `{cost_ref:.4f}`（偏差 `+{deviation_pct:.1f}%`），壓力明顯"
                else:
                    cost_analysis = f"✅ 現價貼近大戶空單成本（偏差 `{deviation_pct:+.1f}%`），跟空風險低"

        # 鯨魚意圖分析
        lev_float = None
        if leverage:
            try:
                lev_float = float(leverage)
            except (TypeError, ValueError):
                pass
        if lev_float is not None:
            if lev_float >= 10:
                whale_intent = f"🎯 *趨勢建倉*（槓桿 {lev_float:.0f}x 高槓桿，強方向性押注）"
            elif lev_float >= 3:
                whale_intent = f"📊 *中性建倉*（槓桿 {lev_float:.0f}x，趨勢建倉或波段佈局）"
            else:
                whale_intent = f"🛡️ *對沖/保守*（槓桿 {lev_float:.0f}x 低槓桿，疑似對沖保值）"
        else:
            whale_intent = "📊 意圖待觀察（槓桿未知）"

        # ── HL 幣種持倉 + 聰明錢評分（新增）────────────────────────
        hl_smart = {}
        try:
            hl_smart = fetch_hyperliquid_smart_money_score(str(symbol))
        except Exception:
            pass
        hl_pos_data = fetch_hyperliquid_coin_position(str(symbol))
        hl_ls_val = hl_pos_data.get("ls_ratio") if hl_pos_data else None

        lines.append(f"⏰ 時間：{time_str}")
        lines.append(f"標的：`{symbol}`")
        lines.append(f"方向：{direction_emoji} {direction_text}")
        lines.append(f"規模：{value_display} USD")
        if cost_ref:
            lines.append(f"成本位：`{cost_ref:.4f}`" + (f" | 現價：`{current_market_price:.4f}`" if current_market_price else ""))
        if cost_analysis:
            lines.append(cost_analysis)
        if liq_price:
            try:
                lines.append(f"爆倉價：`{float(liq_price):.4f}`")
            except (TypeError, ValueError):
                pass
        lines.append(f"🧠 鯨魚意圖：{whale_intent}")
        # HL 整體持倉方向（機構級數據）
        if hl_ls_val is not None:
            _hl_emoji = "🟢" if hl_ls_val > 1.1 else ("🔴" if hl_ls_val < 0.9 else "🟡")
            lines.append(f"{_hl_emoji} HL整體多空比：`{hl_ls_val:.2f}`（>1偏多 <1偏空）")
        if hl_smart.get("label"):
            lines.append(f"🎯 {hl_smart['label']}")
        lines.append("")
    
    # 更新已發送 ID 列表
    sent_alert_ids.extend(new_alert_ids)
    # 只保留最近 500 條
    if len(sent_alert_ids) > 500:
        sent_alert_ids = sent_alert_ids[-500:]
    save_json_file(HYPERLIQUID_SENT_ALERTS_FILE, sent_alert_ids)
    
    # 聰明錢 PNL 分佈部分（補充資訊）
    has_smart_money_data = (
        smart_money_info.get('money_printers') or 
        smart_money_info.get('smart_money') or 
        smart_money_info.get('top_symbols')
    )
    
    if has_smart_money_data:
        lines.append("💰 *聰明錢 PNL 分佈觀察*：")
        
        # 顯示層級統計
        if smart_money_info.get('money_printers'):
            printer_count = sum(mp.get('address_count', 0) for mp in smart_money_info['money_printers'])
            if printer_count > 0:
                lines.append(f"Money Printer (> $1M 獲利)：{printer_count} 個錢包")
        
        if smart_money_info.get('smart_money'):
            smart_count = sum(sm.get('address_count', 0) for sm in smart_money_info['smart_money'])
            if smart_count > 0:
                lines.append(f"Smart Money ($100k - $1M 獲利)：{smart_count} 個錢包")
        
        # 顯示持倉集中度
        top_symbols = smart_money_info.get('top_symbols', {})
        if top_symbols:
            symbol_list = []
            for symbol, info in list(top_symbols.items())[:3]:
                bias = info.get('bias', 0)
                symbol_list.append(f"`{symbol}`")
                if bias > 0:
                    lines.append(f"其中 {symbol} 的看漲情緒 (Bias) 達 {bias:.1f}%")
            
            if symbol_list:
                lines.append(f"目前獲利 > $100k 的錢包，主要持倉集中在：{', '.join(symbol_list)}")
        
        lines.append("")
    
    # 船長提示
    if new_alerts:
        top_symbol = new_alerts[0].get('symbol', '特定標的')
        lines.append(f"💡 *船長提示*：聰明錢正在關注 {top_symbol}，請注意該幣種的流動性變化！")
        lines.append("")
    
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"⏰ 更新時間：{format_datetime(get_taipei_time())}")
    
    return "\n".join(lines)


def run_hyperliquid_monitor_once():
    """執行一次 Hyperliquid 聰明錢監控（適合排程觸發）"""
    logger.info("開始執行 Hyperliquid 聰明錢監控...")
    
    message = build_hyperliquid_message()
    if not message:
        logger.info("本次 Hyperliquid 監控無有效數據，未發送推播")
        return
    
    thread_id = TG_THREAD_IDS.get("hyperliquid", 252)
    send_telegram_message(message, thread_id, parse_mode="Markdown")
    logger.info("Hyperliquid 聰明錢監控推播完成")


def run_gold_signal():
    """黃金 XAUUSD 多空訊號（ORB+MA），推播到同一個 Telegram 機器人、指定 topic。"""
    import sys
    base = Path(__file__).resolve().parent
    cwd = Path.cwd()
    logger.info("[黃金訊號] 開始執行 | jackbot 所在目錄=%s | 當前工作目錄=%s", base, cwd)
    # 依序嘗試：同層 gold_signal_bot（repo 根目錄）、黃金策略/gold_signal_bot、當前工作目錄
    candidates = [
        base / "gold_signal_bot",
        base / "黃金策略" / "gold_signal_bot",
        cwd / "gold_signal_bot",
    ]
    gold_bot_dir = None
    for p in candidates:
        if p.is_dir():
            gold_bot_dir = p
            logger.info("[黃金訊號] 使用模組路徑: %s", gold_bot_dir)
            break
        logger.info("[黃金訊號] 路徑不存在，跳過: %s", p)
    if gold_bot_dir is None:
        logger.error("[黃金訊號] 所有候選路徑皆不存在: %s", candidates)
        send_telegram_message(
            "⚠️ 黃金訊號：找不到 gold_signal_bot 目錄（已嘗試 黃金策略/gold_signal_bot 與 gold_signal_bot），請確認專案結構並部署該資料夾。",
            TG_THREAD_IDS.get("gold_signal", 254),
        )
        return
    _path_insert = str(gold_bot_dir)
    if _path_insert not in sys.path:
        sys.path.insert(0, _path_insert)
    try:
        os.environ["TELEGRAM_BOT_TOKEN"] = os.environ.get("TG_TOKEN", "") or (TG_TOKEN or "")
        os.environ["TELEGRAM_CHAT_ID"] = os.environ.get("CHAT_ID", "") or (CHAT_ID or "")
        from datetime import datetime, timezone
        from config import get_config
        from data_provider import fetch_ohlc
        from strategy_orb import compute_signal
        from filters import apply_filters
        from telegram_sender import format_signal_message, format_tp_sl_hit_message, get_gold_chart_keyboard
        logger.info("[黃金訊號] 模組 import 成功")
    except ImportError as e:
        logger.exception("[黃金訊號] 模組 import 失敗: %s", e)
        send_telegram_message(
            f"⚠️ 黃金訊號：依賴缺失（請確認已安裝 yfinance）。{str(e)}",
            TG_THREAD_IDS.get("gold_signal", 254),
        )
        return
    cfg = get_config()
    data_src = getattr(cfg, "DATA_SOURCE", "yfinance")
    symbol = getattr(cfg, "SYMBOL_GOLD", "GC=F")
    logger.info("[黃金訊號] 數據源=%s 符號=%s 開始拉取 1h K 線", data_src, symbol)
    df_1h = fetch_ohlc(cfg.SYMBOL_GOLD, interval="1h", period="5d", config=cfg)
    if df_1h is None or df_1h.empty:
        logger.warning("[黃金訊號] 黃金 1h 數據為空 (df is None=%s, empty=%s)，本輪不推播",
                      df_1h is None, df_1h.empty if df_1h is not None else "N/A")
        return
    n_rows = len(df_1h)
    if n_rows < 24:
        logger.warning("[黃金訊號] 黃金 1h 數據不足 24 根 (目前 %s 根)，本輪不推播", n_rows)
        return
    logger.info("[黃金訊號] 黃金 1h 數據 OK，共 %s 根 | 時間範圍: %s ~ %s",
                n_rows, df_1h.index.min() if hasattr(df_1h.index, 'min') and len(df_1h) else "N/A", df_1h.index.max() if hasattr(df_1h.index, 'max') and len(df_1h) else "N/A")

    # 狀態檔路徑：與 gold_signal_bot 同層，方便 repo 內放 gold_signal_bot/gold_signal_state/state.json
    state_dir = cwd / "gold_signal_bot" / "gold_signal_state"
    state_path = state_dir / "state.json"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        state_path = cwd / "gold_signal_state.json"

    def _load_gold_state():
        try:
            if state_path.exists():
                with open(state_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning("[黃金訊號] 讀取狀態檔失敗: %s", e)
        return {}

    def _save_gold_state(s):
        try:
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(s, f, ensure_ascii=False, indent=0)
        except Exception as e:
            logger.warning("[黃金訊號] 寫入狀態檔失敗: %s", e)

    state = _load_gold_state()
    now_utc = datetime.now(timezone.utc)
    # 今日交易日日期（以 SESSION_START_HOUR_UTC=1 為基準）
    orb_hour = getattr(cfg, "SESSION_START_HOUR_UTC", 1)
    if now_utc.hour < orb_hour:
        today_trade_date = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        today_trade_date = now_utc.strftime("%Y-%m-%d")

    last_bar_row = df_1h.iloc[-1]
    bar_high = float(last_bar_row["High"])
    bar_low = float(last_bar_row["Low"])
    last_dir = state.get("last_direction")
    last_sl = state.get("last_sl")
    last_tp = state.get("last_tp")
    last_entry = state.get("last_entry")
    if last_dir and last_sl is not None and last_tp is not None and last_entry is not None:
        hit = None
        if last_dir == "long":
            if bar_high >= last_tp:
                hit = "tp"
            elif bar_low <= last_sl:
                hit = "sl"
        else:
            if bar_low <= last_tp:
                hit = "tp"
            elif bar_high >= last_sl:
                hit = "sl"
        if hit:
            msg_tpsl = format_tp_sl_hit_message(hit, last_dir, last_entry, last_sl, last_tp)
            send_telegram_message(msg_tpsl, TG_THREAD_IDS.get("gold_signal", 254), parse_mode=None)
            logger.info("[黃金訊號] 已推播 %s 觸及，本輪結束（今日同方向不再開新倉）", "止盈" if hit == "tp" else "止損")
            # 記錄「今日已結束的方向」，防止同一交易日重複開同方向
            _save_gold_state({
                "closed_direction": last_dir,
                "closed_trade_date": today_trade_date,
            })
            return  # 本輪直接結束，不再找新單

    df_dxy = None
    if cfg.USE_DXY_FILTER:
        df_dxy = fetch_ohlc(cfg.SYMBOL_DXY, interval="1h", period="5d", config=None)
        logger.info("[黃金訊號] DXY 濾網用數據: %s 根", len(df_dxy) if df_dxy is not None and not df_dxy.empty else 0)
    signal = compute_signal(df_1h, cfg)
    if signal is None:
        logger.info("[黃金訊號] 本輪無符合條件的 ORB+MA 訊號，跳過推播")
        return
    logger.info("[黃金訊號] 取得訊號: 方向=%s 進場=%s", signal.direction, signal.entry)
    # 數據過舊（例如週末休市）則不推播，避免「今天沒開盤卻收到訊號」
    last_bar = df_1h.index[-1]
    try:
        last_bar_utc = last_bar.tz_convert("UTC") if getattr(last_bar, "tzinfo", None) else last_bar.tz_localize("UTC")
    except Exception:
        last_bar_utc = last_bar
    try:
        age_sec = (pd.Timestamp(now_utc) - pd.Timestamp(last_bar_utc)).total_seconds()
    except Exception:
        age_sec = 0
    if age_sec > 24 * 3600:
        logger.info("[黃金訊號] 數據過舊（最後 K 線已逾 24h，可能休市），跳過推播")
        return
    # 同向持倉中：不重複推
    if state.get("last_direction") == signal.direction:
        logger.info("[黃金訊號] 同向訊號重疊（目前仍有 %s 倉），跳過推播", signal.direction)
        return
    # 同日同方向已觸及 TP/SL：本交易日不再開同方向新倉
    if (
        state.get("closed_direction") == signal.direction
        and state.get("closed_trade_date") == today_trade_date
    ):
        logger.info("[黃金訊號] 今日 %s 方向已觸及 TP/SL，同交易日不再開同方向新倉", signal.direction)
        return
    ok, reason = apply_filters(
        signal.direction, cfg, df_1h, df_dxy=df_dxy, now=now_utc
    )
    if not ok:
        logger.info("[黃金訊號] 訊號被濾網拒絕: %s", reason)
        return
    thread_id = TG_THREAD_IDS.get("gold_signal", 254)
    msg = format_signal_message(signal, data_cutoff_utc=last_bar_utc)
    keyboard = get_gold_chart_keyboard()
    sent = send_telegram_message(msg, thread_id, parse_mode=None, reply_markup=keyboard)
    if sent:
        _save_gold_state({
            "last_direction": signal.direction,
            "last_entry": signal.entry,
            "last_sl": signal.sl,
            "last_tp": signal.tp,
            "last_time_utc": now_utc.isoformat(),
            "trade_date": today_trade_date,
        })
    logger.info("[黃金訊號] 推播完成 | thread_id=%s 發送結果=%s", thread_id, sent)


# ══════════════════════════════════════════════════════════════════════════════
# API 健康檢查（啟動時自動驗證所有重要端點的數據可用性）
# ══════════════════════════════════════════════════════════════════════════════

def run_api_health_check(symbol: str = "BTC") -> None:
    """啟動時逐一測試所有重要 API 端點，LOG 清楚標記 ✅/❌/⛔。
    呼叫方式：python jackbot.py api_check
    也在 position_change 啟動時自動執行一次（非阻塞）。
    """
    base = symbol.upper().replace("USDT", "")

    # 每個測試項目：(顯示名, ep_key, 測試 params, 必要性)
    # 必要性：🔴=核心  🟡=重要  🟢=加分
    checks = [
        # ── OI ──
        ("聚合持倉K線",          "oi_agg_history",       {"symbol": base, "interval": "15m", "limit": 2}, "🔴"),
        ("穩定幣保證金OI",       "oi_agg_stable",        {"symbol": base, "interval": "15m", "limit": 2}, "🟡"),
        ("幣本位OI",             "oi_agg_coin",          {"symbol": base, "interval": "15m", "limit": 2}, "🟡"),
        ("各所持倉列表",         "oi_exchange_list",     {"symbol": base}, "🟡"),
        ("各所持倉歷史",         "oi_exchange_history",  {"symbol": base + "USDT", "exchange": "Binance", "interval": "15m", "limit": 2}, "🟡"),
        # ── 資金費率 ──
        ("費率列表(各所)",       "fr_exchange_list",     {}, "🔴"),
        ("OI加權費率K線",        "fr_oi_weight",         {"symbol": base, "interval": "8h", "limit": 2, "exchange": "Binance"}, "🟡"),
        ("累積費率",             "fr_accum_exchange",    {"symbol": base}, "🟡"),
        ("費率套利機會",         "fr_arbitrage",         {}, "🟢"),
        # ── 多空比 ──
        ("全網帳戶多空比",       "ls_global_history",    {"symbol": base, "exchange": "Binance", "interval": "1h", "limit": 2}, "🔴"),
        ("大戶帳戶多空比",       "ls_top_account",       {"symbol": base, "exchange": "Binance", "interval": "1h", "limit": 2}, "🔴"),
        ("大戶持倉多空比",       "ls_top_position",      {"symbol": base, "exchange": "Binance", "interval": "1h", "limit": 2}, "🟡"),
        # ── 主動買賣 ──
        ("幣種聚合主動買賣",     "taker_agg_history",    {"symbol": base, "interval": "15m", "limit": 3}, "🔴"),
        ("交易對主動買賣",       "taker_pair_history",   {"symbol": base + "USDT", "exchange": "Binance", "interval": "15m", "limit": 3}, "🟡"),
        ("各所主動買賣比",       "taker_exchange_list",  {"symbol": base}, "🟢"),
        # ── 爆倉 ──
        ("幣種聚合爆倉歷史",     "liq_agg_history",      {"symbol": base, "interval": "15m", "limit": 4}, "🔴"),
        ("即時爆倉訂單",         "liq_order",            {"symbol": base, "limit": 5}, "🔴"),
        ("幣種爆倉列表",         "liq_coin_list",        {"timeType": "0"}, "🔴"),
        ("聚合爆倉熱力圖M2",     "liq_agg_heatmap_m2",   {"symbol": base, "exchange": "Binance"}, "🟡"),
        ("聚合爆倉熱力圖M1",     "liq_agg_heatmap_m1",   {"symbol": base, "exchange": "Binance"}, "🟡"),
        ("爆倉地圖(聚合)",       "liq_agg_map",          {"symbol": base}, "🟢"),
        # ── 訂單簿 ──
        ("聚合訂單簿深度歷史",   "ob_agg_ask_bids",      {"symbol": base, "interval": "15m", "limit": 3, "range": "5"}, "🔴"),
        ("大額掛單",             "ob_large_order",       {"symbol": base, "side": "asks"}, "🟡"),
        ("大額掛單歷史",         "ob_large_order_hist",  {"symbol": base, "interval": "15m", "limit": 2}, "🟡"),
        # ── 合約市場 ──
        ("合約幣種市場行情",     "coins_markets",        {"page": "1", "size": "10"}, "🔴"),
        # ── 現貨主動買賣 ──
        ("現貨幣種聚合主動買賣", "spot_taker_agg",       {"symbol": base, "interval": "15m", "limit": 2}, "🟢"),
        # ── 期權 ──
        ("期權最大痛點",         "opt_max_pain",         {"symbol": base}, "🟡"),
        ("期權持倉歷史",         "opt_exchange_oi",      {"symbol": base, "limit": 2}, "🟢"),
        # ── 指標 ──
        ("恐懼貪婪指數",         "fear_greed",           {"limit": 1}, "🟡"),
        ("Coinbase溢價指數",     "coinbase_premium",     {"limit": 1}, "🟡"),
        ("合約基差歷史",         "contract_basis",       {"symbol": base, "exchange": "Binance", "interval": "1h", "limit": 2}, "🟡"),
        ("BTC ETF資金流",        "btc_etf_flow",         {"limit": 1}, "🟡"),
        ("BTC ETF淨資產",        "btc_etf_net_assets",   {"limit": 1}, "🟢"),
        # ── ETF & 鏈上 ──
        ("交易所餘額列表",       "exchange_balance_list",{}, "🟢"),
        ("灰度持倉",             "grayscale_holdings",   {}, "🟢"),
        # ── Hyperliquid ──
        ("HL鯨魚預警",           "hl_whale_alert",       {}, "🟡"),
        ("HL幣種持倉",           "hl_position",          {"symbol": base}, "🟡"),
        ("HL錢包盈虧分布",       "hl_wallet_pnl_dist",   {}, "🟢"),
    ]

    logger.info("=" * 70)
    logger.info("🔍 [API健康檢查] 開始逐一驗證所有重要端點...")
    logger.info(f"   測試幣種：{base}USDT  |  CG_API_KEY: {'已設定' if CG_API_KEY else '❌未設定'}")
    logger.info("=" * 70)

    results = {"✅": 0, "❌": 0, "⚠️": 0}

    for name, ep_key, params, priority in checks:
        ep = CG_EP.get(ep_key, "")
        if not ep:
            logger.warning(f"  [{priority}] {name:30s} ⚠️  CG_EP 中找不到 key={ep_key}")
            results["⚠️"] += 1
            continue
        try:
            _respect_coinglass_rate_limit()
            r = requests.get(
                f"{CG_API_BASE}{ep}",
                headers={"CG-API-KEY": CG_API_KEY or "", "accept": "application/json"},
                params=params, timeout=10
            )
            status = r.status_code
            if status == 200:
                j = r.json()
                code = j.get("code")
                data = j.get("data") or j.get("list") or []
                has_data = bool(data) if isinstance(data, (list, dict)) else False
                if code in (0, "0", 200, "200", None) and has_data:
                    logger.info(f"  [{priority}] {name:30s} ✅  HTTP200 數據筆數={len(data) if isinstance(data, list) else '有'}")
                    results["✅"] += 1
                elif code in (0, "0", 200, "200", None):
                    logger.warning(f"  [{priority}] {name:30s} ⚠️  HTTP200 但data為空 code={code}")
                    results["⚠️"] += 1
                else:
                    msg = j.get("msg") or j.get("message") or ""
                    logger.warning(f"  [{priority}] {name:30s} ❌  HTTP200 但API錯誤 code={code} msg={msg[:60]}")
                    results["❌"] += 1
            elif status == 401:
                logger.error(f"  [{priority}] {name:30s} ❌  HTTP401 API Key 無效或未設定")
                results["❌"] += 1
            elif status == 403:
                logger.warning(f"  [{priority}] {name:30s} ❌  HTTP403 權限不足（需升級帳號）")
                results["❌"] += 1
            elif status == 404:
                logger.warning(f"  [{priority}] {name:30s} ⚠️  HTTP404 端點不存在（路徑可能有誤）")
                results["⚠️"] += 1
            elif status == 429:
                logger.warning(f"  [{priority}] {name:30s} ⚠️  HTTP429 速率限制（API Key正確但頻率過高）")
                results["⚠️"] += 1
            else:
                logger.warning(f"  [{priority}] {name:30s} ❌  HTTP{status}")
                results["❌"] += 1
        except Exception as e_hc:
            logger.warning(f"  [{priority}] {name:30s} ⚠️  請求異常: {str(e_hc)[:50]}")
            results["⚠️"] += 1
        time.sleep(0.3)  # 避免健康檢查本身觸發429

    # 彙整報告
    total = sum(results.values())
    logger.info("=" * 70)
    logger.info(f"🔍 [API健康檢查完成] 共測試 {total} 個端點")
    logger.info(f"   ✅ 正常：{results['✅']}   ❌ 失敗/無權限：{results['❌']}   ⚠️ 空數據/異常：{results['⚠️']}")
    if results["❌"] > 0:
        logger.warning("   ⚠️ 有 ❌ 項目請向 CoinGlass 確認帳號權限，或檢查 CG_API_KEY 環境變數")
    logger.info("=" * 70)


# ==================== 資料重置工具 ====================

def run_reset_data() -> None:
    """
    清除所有冷卻、推播紀錄、績效報告，讓系統全新重啟。
    呼叫方式：python jackbot.py reset_data

    清除範圍：
      ✅ sniper_cooldown.json   - 冷卻歷史 + 推播紀錄（含倉位追蹤）
      ✅ performance_history.json - 每日績效累積（週/月 R 值）
      ✅ last_summary_date.json  - 每日績效總結發送日期鎖
      ✅ backup_state.json       - 關鍵狀態備份
    """
    logger.info("=" * 60)
    logger.info("【資料重置】開始清除所有冷卻與績效記錄...")

    files_to_reset: list[tuple[str, object]] = [
        ("sniper_cooldown.json",     {"history": [], "signals": []}),
        ("performance_history.json", []),
        ("last_summary_date.json",   {}),
        ("backup_state.json",        {}),
    ]

    cleared = []
    for fname, empty_val in files_to_reset:
        fpath = DATA_DIR / fname
        try:
            save_json_file(fpath, empty_val)
            logger.info(f"  ✅ 已清除: {fname}")
            cleared.append(fname)
        except Exception as e:
            logger.warning(f"  ⚠️ 清除失敗 {fname}: {e}")

    logger.info(f"【資料重置】完成，共清除 {len(cleared)} 個檔案：{cleared}")
    logger.info("=" * 60)

    # 發送 Telegram 通知
    from datetime import datetime as _dt
    _now_str = _dt.now(TAIPEI_TZ).strftime("%m/%d %H:%M")
    _msg = (
        f"🔄 *【系統重置】資料已全部清除*\n"
        f"🕐 {_now_str} (台灣)\n"
        f"━━━━━━━━━━━━━━\n"
        f"✅ 冷卻歷史 & 推播紀錄：已清空\n"
        f"✅ 倉位追蹤記錄：已清空\n"
        f"✅ 績效歷史（週/月 R 值）：已清空\n"
        f"✅ 每日總結發送鎖：已清空\n"
        f"━━━━━━━━━━━━━━\n"
        f"📌 下一輪掃描將從零開始記錄，無冷卻限制。"
    )
    try:
        _thread = TG_THREAD_IDS.get("sniper", 0) or int(CHAT_ID or 0)
        send_telegram_message(_msg, _thread, parse_mode="Markdown")
        logger.info("【資料重置】Telegram 通知已發送")
    except Exception as e:
        logger.warning(f"【資料重置】Telegram 通知失敗（不影響重置結果）: {e}")


# ==================== 主程序 ====================

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        function_name = sys.argv[1]
        
        if function_name == "sector_ranking":
            fetch_sector_ranking()
        elif function_name == "buying_power_monitor":
            buying_power_monitor()
        elif function_name == "whale_position":
            # 向後兼容：舊名稱仍可使用
            logger.info("使用舊函數名稱 whale_position，建議改用 buying_power_monitor")
            buying_power_monitor()
        elif function_name == "api_check":
            run_api_health_check("BTC")
        elif function_name == "position_change":
            fetch_position_change()
        elif function_name == "economic_data":
            fetch_and_push_economic_data()
        elif function_name == "economic_data_preview":
            send_today_preview()
        elif function_name == "news":
            fetch_all_news()
        elif function_name == "funding_rate":
            fetch_funding_fortune_list()
        elif function_name == "long_term_index":
            run_long_term_monitor()
        elif function_name == "long_term_index_once":
            run_long_term_once()
        elif function_name == "liquidity_radar":
            run_liquidity_radar_once()
        elif function_name == "altseason_radar":
            run_altseason_radar_once()
        elif function_name == "hyperliquid":
            run_hyperliquid_monitor_once()
        elif function_name == "gold_signal":
            run_gold_signal()
        elif function_name == "reset_data":
            run_reset_data()
        else:
            print("可用的功能:")
            print("  sector_ranking   - 主流板塊排行榜推播")
            print("  buying_power_monitor - 購買力監控（穩定幣市值 + OI 監控）")
            print("  whale_position       - 已廢棄，請使用 buying_power_monitor")
            print("  position_change  - 持倉變化篩選")
            print("  economic_data    - 重要經濟數據推播")
            print("  news             - 新聞快訊推播")
            print("  funding_rate     - 資金費率排行榜")
            print("  long_term_index       - 長線牛熊導航儀（24 小時每 4 小時更新）")
            print("  long_term_index_once  - 長線牛熊導航儀（只執行一次，適合排程）")
            print("  liquidity_radar       - 流動性獵取雷達（極端爆倉彙整）")
            print("  altseason_radar       - 山寨爆發雷達（Altseason + RSI + Buy Ratio）")
            print("  hyperliquid           - Hyperliquid 聰明錢監控")
            print("  gold_signal           - 黃金 XAUUSD 多空訊號（ORB+MA）")
            print("  api_check             - API 健康檢查（驗證所有端點是否可用）")
            print("  reset_data            - 清除所有冷卻/推播/績效記錄，全新重啟")
    else:
        print("請指定要執行的功能，例如: python jackbot.py sector_ranking")

