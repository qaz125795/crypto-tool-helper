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
from kline_card_renderer import fetch_ohlc_5m, fetch_coinglass_oi_5m, render_kline_oi_card

# 台灣台北時區（UTC+8）
TAIPEI_TZ = timezone(timedelta(hours=8))

# 配置日誌：執行時終端顯示 + 寫入 log 檔，方便排查
_log_fmt = '%(asctime)s - %(levelname)s - %(message)s'
_stream_handler = logging.StreamHandler(sys.stdout)
_stream_handler.stream.reconfigure(encoding="utf-8", errors="replace") if hasattr(_stream_handler.stream, "reconfigure") else None
logging.basicConfig(
    level=logging.INFO,
    format=_log_fmt,
    handlers=[_stream_handler],
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

# 大盤環境順勢濾網：掃描前取得 BTC / ETH 30m / 1H 漲跌幅，供訊號備註大盤狀態用
_btc_30m_pct: Optional[float] = None
_btc_1h_pct: Optional[float] = None   # BTC 1H 方向，配合 30m 判斷大盤強弱
_eth_30m_pct: Optional[float] = None
_eth_1h_pct: Optional[float] = None   # ETH 1H 方向，供山寨幣大盤參考

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


def send_telegram_photo(
    photo_path: str,
    caption: str,
    thread_id: int,
    parse_mode: str = "Markdown",
    reply_markup: Optional[Dict] = None,
) -> bool:
    """發送圖片到 Telegram（sendPhoto；caption 可能超出上限時，外層可改用 sendMessage 備援）"""
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto"
    payload = {
        "chat_id": CHAT_ID,
        "message_thread_id": thread_id,
        "caption": caption,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        with open(photo_path, "rb") as f:
            files = {"photo": f}
            resp = requests.post(url, data=payload, files=files, timeout=30)
        if resp.status_code == 200:
            result = resp.json()
            if result.get("ok"):
                logger.info("Telegram 圖片發送成功")
                return True
            logger.error(f"Telegram sendPhoto API 錯誤: {result}")
            return False
        logger.error(f"Telegram sendPhoto HTTP 錯誤: {resp.status_code} - {resp.text}")
        return False
    except Exception as e:
        logger.error(f"發送 Telegram 圖片失敗: {str(e)}")
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

    logger.debug("[SmartMoneyOI] fetch stable/coin OI split symbol=%s" % base)

    stable_bars, coin_bars = None, None
    try:
        j_s = _cg_get(CG_EP["oi_agg_stable"], params)
        rows_s = j_s.get("data") or j_s.get("list") or [] if j_s else []
        stable_bars = _parse_oi_bars_from_rows(rows_s) if rows_s else None
        _n_stable = len(stable_bars) if stable_bars else 0
        logger.debug("[SmartMoneyOI] stable OI bars: " + str(_n_stable))
    except Exception as e_s:
        logger.debug("[SmartMoneyOI] stable OI error: " + str(e_s))
    try:
        j_c = _cg_get(CG_EP["oi_agg_coin"], params)
        rows_c = j_c.get("data") or j_c.get("list") or [] if j_c else []
        coin_bars = _parse_oi_bars_from_rows(rows_c) if rows_c else None
        _n_coin = len(coin_bars) if coin_bars else 0
        logger.debug("[SmartMoneyOI] coin OI bars: " + str(_n_coin))
    except Exception as e_c:
        logger.debug("[SmartMoneyOI] coin OI error: " + str(e_c))

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




# ==================== 3. 持倉變化篩選器 ====================



# ── 幣種→交易所反向對照表（由 CoinGlass supported-exchange-pairs 填充）──────
# 格式：{"BTC": {"Binance", "OKX", "Bybit", ...}, ...}
# 快取未建立時 get_major_exchanges_for_coin 保守回傳完整 pool
_cg_full_exchange_map: Dict[str, Set[str]] = {}


def get_major_exchanges_for_coin(base: str, pool: Optional[List[str]] = None) -> List[str]:
    """
    從 _cg_full_exchange_map 快取查詢 pool 內哪些大所支援該幣種。
    快取未建立 → 保守回傳完整 pool；幣不在 map → 同上。
    """
    if pool is None:
        pool = ["Binance", "OKX", "Bybit"]
    if not _cg_full_exchange_map:
        return pool
    base_upper = base.upper()
    if base_upper not in _cg_full_exchange_map:
        return pool
    supported = _cg_full_exchange_map[base_upper]
    return [ex for ex in pool if ex in supported]


def fetch_coins_price_change() -> List[Dict]:
    """CoinGlass coins-price-change 備援端點（coins-markets 失敗時啟用，純 CoinGlass 模式）。"""
    url = f"{CG_API_BASE}/api/futures/coins-price-change"
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.error(f"[備援] coins-price-change HTTP {response.status_code}")
            return []
        result = response.json()
        data = result.get('data', result if isinstance(result, list) else [])
        if not data:
            logger.warning(f"[備援] coins-price-change 回傳空資料 code={result.get('code')} msg={result.get('msg','')}")
            return []
        logger.info(f"[備援] coins-price-change 取得 {len(data)} 個幣種")
        return data
    except Exception as e:
        logger.error(f"[備援] coins-price-change 失敗: {e}")
        return []


def _fetch_coinglass_24h_map() -> Dict[str, float]:
    """CoinGlass coins-price-change → {clean_symbol: 24h_pct}（coins-markets 失敗時的備援 24h 漲跌幅來源）。"""
    if not CG_API_KEY:
        return {}
    try:
        r = requests.get(
            f"{CG_API_BASE}/api/futures/coins-price-change",
            headers={"CG-API-KEY": CG_API_KEY, "accept": "application/json"},
            timeout=10
        )
        if r.status_code != 200:
            logger.warning(f"[24h映射] coins-price-change HTTP {r.status_code}")
            return {}
        j = r.json()
        if j.get("code") not in (0, "0", 200, "200", None):
            logger.warning(f"[24h映射] coins-price-change code={j.get('code')} msg={j.get('msg','')}")
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
        return out
    except Exception as e:
        logger.warning(f"[24h映射] coins-price-change 失敗: {e}")
        return {}


def fetch_bingx_futures_24h_vol() -> Dict[str, float]:
    """
    Plan B 成交值備援：BingX 永續合約 24h quoteVolume（USDT）批次取得。
    單一 API call 涵蓋所有 BingX 上市幣種，市場數據端點無需 API Key。
    回傳 {base_symbol: vol_usdt_24h}，例如 {"BTC": 2.3e10, "ETH": 5e9}。
    失敗時靜默回傳空 dict，不影響主流程。
    """
    try:
        r = requests.get(
            "https://open-api.bingx.com/openApi/swap/v2/quote/ticker",
            timeout=10
        )
        if r.status_code != 200:
            logger.warning(f"[備援B-BingX] HTTP {r.status_code}，跳過")
            return {}
        j = r.json()
        # BingX 回傳格式：{"code": 0, "data": [...]} 或直接 list
        data = j.get("data") if isinstance(j, dict) else j
        if not isinstance(data, list):
            return {}
        result: Dict[str, float] = {}
        for item in data:
            sym = item.get("symbol", "")           # 格式："BTC-USDT"
            if not sym.endswith("-USDT"):
                continue
            base = sym[:-5]                         # "BTC-USDT" → "BTC"
            # 處理 1000xxx / 1000000xxx 命名（BingX 用全稱，CoinGlass 用縮寫）
            # 例：BingX "1000PEPE" → CoinGlass "1KPEPE"（但此處保留 BingX 原名供對照）
            try:
                vol = float(item.get("quoteVolume") or 0)
                if vol > 0:
                    result[base] = vol
                    # 同時建立縮寫別名：1000xxx → 1Kxxx；1000000xxx → 1Mxxx
                    if base.startswith("1000000"):
                        result.setdefault("1M" + base[7:], vol)
                    elif base.startswith("10000"):
                        result.setdefault("1W" + base[5:], vol)
                    elif base.startswith("1000"):
                        result.setdefault("1K" + base[4:], vol)
            except (TypeError, ValueError):
                pass
        logger.info(f"[備援B-BingX✅] 取得 {len(result)} 幣種 24h USDT 成交值")
        return result
    except Exception as e:
        logger.warning(f"[備援B-BingX] 失敗: {type(e).__name__}: {e}")
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


def fetch_oi_change_tf(
    symbol: str, interval: str = "1h", return_ts: bool = False,
    return_candles: int = 0
) -> "Optional[float] | tuple":
    """
    計算單一 symbol 指定時框 OI 變化%（支援 1h / 30m / 15m / 5m）。
    return_ts=True     → 回傳 (change_pct, candle_start_ts)
    return_candles > 0 → 回傳 (change_pct, [{"t": ts, "c": oi_value}, ...]) 最近 N 根 K棒
                          c = 收盤 OI 值（絕對值），供陷阱偵測用
    """
    global _coinglass_oi_rate_limiter, _coinglass_oi_first_failure_logged

    with _oi_rate_limit_lock:
        if _coinglass_oi_rate_limiter is None:
            _coinglass_oi_rate_limiter = {"last_call": 0.0}
        now = time.time()
        elapsed = now - _coinglass_oi_rate_limiter.get("last_call", 0.0)
        wait_time = random.uniform(0.1, 0.25) * _cb_get_wait_multiplier()
        if elapsed < wait_time:
            time.sleep(wait_time - elapsed)
        _coinglass_oi_rate_limiter["last_call"] = time.time()

    base_symbol = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    url = f"{CG_API_BASE}/api/futures/open-interest/aggregated-history"
    params = {"symbol": base_symbol, "interval": interval, "limit": 5}
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
                        # ── return_candles：回傳最近 N 根完整 K棒的 OI 數值列表 ──
                        if return_candles > 0:
                            try:
                                _sorted = sorted(
                                    data_list,
                                    key=lambda d: d.get("t") or d.get("time") or d.get("timestamp") or 0
                                )
                                # 去掉最後一根（當前未收盤），取前 return_candles 根
                                _closed = _sorted[:-1] if len(_sorted) > 1 else _sorted
                                _n = min(return_candles, len(_closed))
                                _candles_out = []
                                for _d in _closed[-_n:]:
                                    _ts = int(_d.get("t") or _d.get("time") or _d.get("timestamp") or 0)
                                    if _ts > 1e12:
                                        _ts = int(_ts / 1000)
                                    _oi_val = (
                                        _d.get("c") or _d.get("openInterest") or
                                        _d.get("oi") or _d.get("v") or 0
                                    )
                                    _px = _d.get("price") or _d.get("close") or _d.get("p") or None
                                    _candles_out.append({"t": _ts, "c": float(_oi_val), "price": _px})
                            except Exception:
                                _candles_out = []
                            return change, _candles_out
                        if return_ts:
                            # 取最近已收盤 K線的起始時間（data_list[-2]）
                            try:
                                _sorted = sorted(
                                    data_list,
                                    key=lambda x: x.get("t") or x.get("time") or x.get("timestamp") or 0
                                )
                                _candle_ts = int(
                                    _sorted[-2].get("t") or _sorted[-2].get("time") or
                                    _sorted[-2].get("timestamp") or 0
                                )
                                if _candle_ts > 1e12:
                                    _candle_ts = int(_candle_ts / 1000)
                            except Exception:
                                _candle_ts = 0
                            return change, _candle_ts
                        return change
                msg = result.get("msg", "")
                if "Too Many Requests" in msg or result.get("code") in ("400", "429"):
                    _cb_record_429()
                    sleep_for = backoff + random.uniform(0, 1.0)
                    logger.warning(f"[CG限流] {base_symbol} [{interval}] 休息 {sleep_for:.1f}s（重試 {attempt+1}）")
                    time.sleep(sleep_for)
                    backoff *= 2.0
                    continue
            elif response.status_code == 429:
                _cb_record_429()
                sleep_for = backoff + random.uniform(0, 1.0)
                logger.warning(f"[CG 429] {base_symbol} [{interval}] 休息 {sleep_for:.1f}s（重試 {attempt+1}）")
                time.sleep(sleep_for)
                backoff *= 2.0
                continue
        except Exception as e:
            logger.debug(f"OI 請求異常 {base_symbol} [{interval}]: {e}")
            time.sleep(backoff)
            backoff *= 2.0
    return None


def _fetch_oi_multi_tf(symbol: str) -> Dict[str, Optional[float]]:
    """Enrichment 專用：對 top 候選幣種補取 CoinGlass 1H OI，推算 1H/4H 變化%。
    只在 enrichment 少量幣種時呼叫，不影響主掃描效能。
    回傳 {"1h": float|None, "4h": float|None}
    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    url = f"{CG_API_BASE}/api/futures/open-interest/aggregated-history"
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}
    try:
        _respect_coinglass_rate_limit()
        r = requests.get(url, params={"symbol": base, "interval": "h1", "limit": 5},
                         headers=headers, timeout=8)
        if r.status_code != 200:
            return {"1h": None, "4h": None}
        j = r.json()
        if j.get("code") not in (0, "0", 200, "200", None):
            return {"1h": None, "4h": None}
        raw = j.get("data") or j.get("list") or []
        bars: List[float] = []
        for row in raw:
            v = row.get("v") or row.get("c") or row.get("close") or row.get("oi")
            if v is not None:
                try:
                    bars.append(float(v))
                except (TypeError, ValueError):
                    pass
        oi_1h = round((bars[-1] - bars[-2]) / bars[-2] * 100, 3) if len(bars) >= 2 and bars[-2] != 0 else None
        oi_4h = round((bars[-1] - bars[-5]) / bars[-5] * 100, 3) if len(bars) >= 5 and bars[-5] != 0 else (
                round((bars[-1] - bars[-4]) / bars[-4] * 100, 3) if len(bars) >= 4 and bars[-4] != 0 else None)
        return {"1h": oi_1h, "4h": oi_4h}
    except Exception:
        return {"1h": None, "4h": None}


# ── 標準版特有：5M 動能共振驗證 ───────────────────────────────────────────────
_resonance_cache: Dict[str, Tuple[Optional[bool], float]] = {}
_RESONANCE_CACHE_TTL = 30.0  # 30 秒快取（比共識更短，動量瞬息萬變）



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


def normalize_symbol(coin: Dict) -> Optional[str]:
    """從幣種數據中提取 symbol"""
    return coin.get('symbol') or coin.get('pair') or coin.get('name') or coin.get('coin') or coin.get('symbolName')


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
            # 交易所優先順序：Binance > Bybit > OKX > BingX > Bitget（量大流動性佳的排前）
            _EXCHANGE_PRIORITY = ["Binance", "Bybit", "OKX", "BingX", "Bitget"]

            def _parse_rate_from_list(ex_list: list, priority: list) -> Optional[float]:
                """從 exchange list 中按優先順序找第一個有效費率。
                CoinGlass exchange-list 的 funding_rate 是百分比格式
                （如 0.007343 = 0.007343%），除以 100 轉為小數供後續計算。
                """
                if not isinstance(ex_list, list):
                    return None
                _ex_map: Dict[str, float] = {}
                for _entry in ex_list:
                    if not isinstance(_entry, dict):
                        continue
                    _ex = _entry.get("exchange")
                    _r  = _entry.get("funding_rate")
                    if _ex and _r is not None:
                        try:
                            _ex_map[_ex] = float(_r) / 100.0
                        except (TypeError, ValueError):
                            pass
                for _ex in priority:
                    if _ex in _ex_map:
                        return _ex_map[_ex]
                if _ex_map:
                    return next(iter(_ex_map.values()))
                return None

            # 優先：USDT 永續（stablecoin_margin_list）
            stablecoin_list = coin_data.get("stablecoin_margin_list") or []
            rate_found = _parse_rate_from_list(stablecoin_list, _EXCHANGE_PRIORITY)

            # 備援：幣本位永續（token_margin_list）
            if rate_found is None:
                token_list = coin_data.get("token_margin_list") or []
                rate_found = _parse_rate_from_list(token_list, _EXCHANGE_PRIORITY)

            if rate_found is not None:
                out[base] = rate_found

        if out:
            logger.info(f"[資金費率✅] 成功解析 {len(out)} 幣種（CoinGlass exchange-list，Binance>Bybit>OKX>BingX>Bitget 優先）")
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


def fetch_funding_rate_trend(symbol: str, interval: str = "8h", limit: int = 2) -> Tuple[Optional[float], Optional[float]]:
    """取得資金費率與上一週期的變化斜率 (fr_trend = current - previous)。
    若費率短時間內急劇下降（fr_trend < -0.02%）且 OI 上升，可標記為潛在軋空訊號。
    回傳：(current_rate, fr_trend)，無數據時為 (None, None)。
    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    cache_key = f"fr_trend:{base}:{interval}"
    now = time.time()
    if cache_key in _flow_cache:
        val, ts = _flow_cache[cache_key]
        if now - ts < 900:  # 15 分鐘快取
            return val if val else (None, None)

    for ep_key in ["fr_oi_weight", "fr_vol_weight", "fr_history"]:
        try:
            j = _cg_get(CG_EP[ep_key], {"symbol": base, "interval": interval, "limit": max(limit, 2),
                                        "exchange": "Binance"})
            if not j:
                continue
            rows = j.get("data") or j.get("list") or []
            if not isinstance(rows, list) or len(rows) < 2:
                continue
            def _rate_from_row(r):
                if isinstance(r, dict):
                    v = r.get("fundingRate") or r.get("funding_rate") or r.get("rate") or r.get("c") or r.get("close")
                elif isinstance(r, (list, tuple)) and len(r) >= 2:
                    v = r[-1]
                else:
                    v = None
                return float(v) if v is not None else None
            current_f = _rate_from_row(rows[-1])
            prev_f = _rate_from_row(rows[-2])
            if current_f is None or prev_f is None:
                continue
            fr_trend = current_f - prev_f
            out = (current_f, fr_trend)
            _flow_cache[cache_key] = (out, now)
            logger.debug(f"[費率斜率] {base} current={current_f*100:.4f}% prev={prev_f*100:.4f}% fr_trend={fr_trend*100:.4f}%")
            return out
        except Exception as e:
            logger.debug(f"[費率斜率-{ep_key}] {base} 異常: {e}")
            continue

    _flow_cache[cache_key] = (None, now)
    return (None, None)


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

    # EMA20（同時保留逐根序列，供「EMA20 回踩結構低」計算）
    ema20_close = None
    ema20_series: list = []   # index 對齊 closes[period:]
    period = 20
    alpha = 2.0 / (period + 1)
    ema = float(np.mean(closes[:period]))
    for i in range(period, len(closes)):
        ema = alpha * float(closes[i]) + (1.0 - alpha) * ema
        ema20_series.append(ema)
    ema20_close = ema
    # 還原成與 closes 等長的完整序列（前 period 根填 None）
    ema20_full = [None] * period + ema20_series

    # VWAP_2h（最近 8 根 15m K 線）與收盤價相對 VWAP 的標準差（供 TP2 軌道用）
    vwap_2h = None
    vwap_std = None
    if len(closes) >= 8 and len(volumes) >= 8:
        uc, uh, ul, uv = closes[-8:], highs[-8:], lows[-8:], volumes[-8:]
        typical = [(uh[i] + ul[i] + uc[i]) / 3.0 for i in range(len(uc))]
        total_vol = sum(uv)
        if total_vol > 0:
            vwap_2h = sum(typical[i] * uv[i] for i in range(len(typical))) / total_vol
            logger.info(f"[指標計算] {clean}: VWAP_2h 使用最近 8 根 K 線成交量加權 (典型價 H+L+C/3)")
        else:
            vwap_2h = sum(typical) / len(typical)
            logger.info(f"[指標計算] {clean}: VWAP_2h 無 volume，改用等權典型價均值 (TWAP 近似)")
        if vwap_2h is not None and uc:
            try:
                devs = [float(c) - float(vwap_2h) for c in uc]
                vwap_std = float(np.std(devs)) if len(devs) >= 2 else None
                if vwap_std is not None and (vwap_std != vwap_std or vwap_std <= 0):
                    vwap_std = None
            except (TypeError, ValueError):
                vwap_std = None

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
    if vwap_std is not None:
        out["vwap_std"] = vwap_std
    if ema20_close is not None:
        out["ema20_close"] = ema20_close
    if len(highs) >= 8:
        out["recent_high_2h"] = max(highs[-8:])
        out["recent_low_2h"] = min(lows[-8:])
    if len(lows) >= 4:
        out["pre_breakout_low"] = min(lows[-4:-1])
    if len(highs) >= 4:
        out["pre_breakout_high"] = max(highs[-4:-1])

    # ── EMA20 回踩結構低/高：往回最多掃 30 根，找最近一次 K 線低點觸碰 EMA20 的位置
    # 「市場驗證過 EMA20 守住的最低點」= 比靜態 EMA20-pad 更精準的 SL 錨點
    _scan_end = len(closes) - 1           # 排除訊號 K 線本身（最後一根）
    _scan_start = max(period, _scan_end - 30)
    ema20_touch_low = None   # 供做多 SL 用
    ema20_touch_high = None  # 供做空 SL 用
    for _i in range(_scan_end - 1, _scan_start - 1, -1):
        _ev = ema20_full[_i]
        if _ev is None:
            continue
        _ev = float(_ev)
        # 做多方向：找 K 線低點曾觸碰/略低於 EMA20（允差 1.5%），且收盤在 EMA20 附近或上方
        if ema20_touch_low is None:
            if float(lows[_i]) <= _ev * 1.015:
                ema20_touch_low = float(lows[_i])
        # 做空方向：找 K 線高點曾觸碰/略高於 EMA20
        if ema20_touch_high is None:
            if float(highs[_i]) >= _ev * 0.985:
                ema20_touch_high = float(highs[_i])
        if ema20_touch_low is not None and ema20_touch_high is not None:
            break
    if ema20_touch_low is not None:
        out["ema20_touch_low"] = ema20_touch_low
    if ema20_touch_high is not None:
        out["ema20_touch_high"] = ema20_touch_high

    # ── Plan C：從 K 線估算 24h USD 成交值（close × volume 加總後按比例推估至 24h）
    # 用於當 CoinGlass 與 Binance 備援均無成交值資料時的最後防線
    try:
        n_candles = len(closes)
        if n_candles >= 2 and len(volumes) == n_candles:
            # 使用最近 96 根 (=24h 若為 15m K) 或全部現有 K 線
            n_use = min(n_candles, 96)
            kline_vol_usd = sum(
                float(closes[-(n_use - i)]) * float(volumes[-(n_use - i)])
                for i in range(n_use)
                if closes[-(n_use - i)] and volumes[-(n_use - i)]
            )
            # 如果 K 線數量不足 96 根，按比例外推至 24h
            if n_use < 96 and kline_vol_usd > 0:
                kline_vol_usd = kline_vol_usd * (96 / n_use)
            if kline_vol_usd > 0:
                out["kline_vol_usd_24h"] = kline_vol_usd
    except Exception:
        pass  # 估算失敗不影響主流程

    logger.info(
        f"[{source_label}指標] {clean}: RSI={rsi_val:.2f} BB上={ub_value} BB下={lb_value} "
        f"現價={current_price} ATR={atr_val} VWAP_2h={vwap_2h} EMA20={ema20_close} "
        f"EMA20回踩低={ema20_touch_low} "
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


def _try_binance_futures_klines_direct(
    symbol_base: str, interval: str = "15m", limit: int = 60
) -> Optional[Dict[str, Any]]:
    """
    直接打 Binance 期貨公開 K 線 API（免 API Key），取得帶 volume 的完整 OHLCV。
    解決 CoinGlass price/history 只回傳 OHLC 而無 volume 導致 VWAP 退化的問題。
    """
    clean = symbol_base.replace("USDT", "").replace("-", "").replace("_", "").upper()
    for sym_pair in [f"{clean}USDT", f"1000{clean}USDT"]:
        try:
            r = requests.get(
                "https://fapi.binance.com/fapi/v1/klines",
                params={"symbol": sym_pair, "interval": interval, "limit": limit},
                timeout=5,
            )
            if r.status_code != 200:
                continue
            raw = r.json()
            if not isinstance(raw, list) or len(raw) < 20:
                continue
            # Binance 期貨 K 線格式：
            # [ts, open, high, low, close, vol, close_ts, quote_vol, trades, taker_buy_base, taker_buy_quote, ignore]
            opens, highs, lows, closes, volumes = [], [], [], [], []
            for bar in raw:
                try:
                    opens.append(float(bar[1]))
                    highs.append(float(bar[2]))
                    lows.append(float(bar[3]))
                    closes.append(float(bar[4]))
                    volumes.append(float(bar[5]))  # base volume（幣本位成交量）
                except (IndexError, TypeError, ValueError):
                    pass
            if len(closes) < 20:
                continue
            result = _calc_indicators_from_ohlcv(
                opens, highs, lows, closes, volumes, clean, "Binance-Direct", sym_pair
            )
            if result:
                result["source"] = "Binance-Direct"
                logger.info(
                    f"[BinanceDirect✅] {clean}: {sym_pair} {interval} {len(closes)} 根（含 volume）"
                )
                return result
        except Exception as e:
            logger.debug(f"[BinanceDirect] {clean}/{sym_pair} 異常: {e}")
    return None


def _try_bybit_futures_klines_direct(
    symbol_base: str, interval: str = "15m", limit: int = 60
) -> Optional[Dict[str, Any]]:
    """
    直接打 Bybit V5 線性永續 K 線 API（免 API Key），取得帶 volume 的完整 OHLCV。
    覆蓋 Bybit-only 幣種（如 XION, WHITEWHALE, PLAYSOUT 等不在 Binance 上的幣）。
    Bybit interval 格式：1/3/5/15/30/60/120/240/D/W/M（分鐘以數字表示）
    注意：Bybit list 為新到舊，需反轉後計算指標。
    """
    clean = symbol_base.replace("USDT", "").replace("-", "").replace("_", "").upper()
    # Binance "15m" → Bybit "15"；"1h" → "60"；"4h" → "240"
    _interval_map = {
        "1m": "1", "3m": "3", "5m": "5", "15m": "15",
        "30m": "30", "1h": "60", "2h": "120", "4h": "240",
    }
    bybit_interval = _interval_map.get(interval, "15")

    for sym_pair in [f"{clean}USDT", f"1000{clean}USDT"]:
        try:
            r = requests.get(
                "https://api.bybit.com/v5/market/kline",
                params={"category": "linear", "symbol": sym_pair,
                        "interval": bybit_interval, "limit": limit},
                timeout=5,
            )
            if r.status_code != 200:
                continue
            j = r.json()
            if j.get("retCode") != 0:
                continue
            raw = j.get("result", {}).get("list", [])
            if not isinstance(raw, list) or len(raw) < 20:
                continue
            # Bybit 格式：[timestamp, open, high, low, close, volume, turnover]，新→舊，需反轉
            raw = list(reversed(raw))
            opens, highs, lows, closes, volumes = [], [], [], [], []
            for bar in raw:
                try:
                    opens.append(float(bar[1]))
                    highs.append(float(bar[2]))
                    lows.append(float(bar[3]))
                    closes.append(float(bar[4]))
                    volumes.append(float(bar[5]))
                except (IndexError, TypeError, ValueError):
                    pass
            if len(closes) < 20:
                continue
            result = _calc_indicators_from_ohlcv(
                opens, highs, lows, closes, volumes, clean, "Bybit-Direct", sym_pair
            )
            if result:
                result["source"] = "Bybit-Direct"
                logger.info(
                    f"[BybitDirect✅] {clean}: {sym_pair} {interval} {len(closes)} 根（含 volume）"
                )
                return result
        except Exception as e:
            logger.debug(f"[BybitDirect] {clean}/{sym_pair} 異常: {e}")
    return None


def _try_bingx_spot_klines_direct(
    symbol_base: str, interval: str = "15m", limit: int = 60
) -> Optional[Dict[str, Any]]:
    """
    BingX 現貨公開 K 線（免簽名），作為最後 fallback。
    覆蓋只在 BingX 上線、其他大所未上的山寨幣，同樣帶 volume。
    格式：[ts, open, high, low, close, volume, close_ts, quote_vol]
    """
    clean = symbol_base.replace("USDT", "").replace("-", "").replace("_", "").upper()
    sym_pair = f"{clean}-USDT"
    try:
        r = requests.get(
            "https://open-api.bingx.com/openApi/spot/v2/market/kline",
            params={"symbol": sym_pair, "interval": interval, "limit": limit},
            timeout=5,
        )
        if r.status_code != 200:
            return None
        j = r.json()
        raw = j.get("data") if isinstance(j, dict) else j
        if not isinstance(raw, list) or len(raw) < 20:
            return None
        opens, highs, lows, closes, volumes = [], [], [], [], []
        for bar in raw:
            try:
                opens.append(float(bar[1]))
                highs.append(float(bar[2]))
                lows.append(float(bar[3]))
                closes.append(float(bar[4]))
                volumes.append(float(bar[5]))
            except (IndexError, TypeError, ValueError):
                pass
        if len(closes) < 20:
            return None
        result = _calc_indicators_from_ohlcv(
            opens, highs, lows, closes, volumes, clean, "BingX-Spot", sym_pair
        )
        if result:
            result["source"] = "BingX-Spot"
            logger.info(
                f"[BingX-Spot✅] {clean}: {sym_pair} {interval} {len(closes)} 根（含 volume）"
            )
            return result
    except Exception as e:
        logger.debug(f"[BingX-Spot] {clean}/{sym_pair} 異常: {e}")
    return None


def _fetch_15m_klines_raw(symbol: str, limit: int = 6) -> Optional[list]:
    """
    輕量取得最近 N 根 15m K 線的 open/close，供陷阱偵測用。
    回傳: [{"open": float, "close": float}, ...] 由舊到新，或 None
    """
    clean = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    for sym_pair in [f"{clean}USDT", f"1000{clean}USDT"]:
        try:
            r = requests.get(
                "https://fapi.binance.com/fapi/v1/klines",
                params={"symbol": sym_pair, "interval": "15m", "limit": limit},
                timeout=5,
            )
            if r.status_code != 200:
                continue
            raw = r.json()
            if not isinstance(raw, list) or len(raw) < 4:
                continue
            out = []
            for bar in raw[-5:]:  # 取最後 5 根
                try:
                    o, c = float(bar[1]), float(bar[4])
                    out.append({"open": o, "close": c})
                except (IndexError, TypeError, ValueError):
                    pass
            if len(out) >= 4:
                return out
        except Exception:
            pass
    # Bybit fallback
    _interval_map = {"15m": "15"}
    bybit_interval = _interval_map.get("15m", "15")
    for sym_pair in [f"{clean}USDT", f"1000{clean}USDT"]:
        try:
            r = requests.get(
                "https://api.bybit.com/v5/market/kline",
                params={"category": "linear", "symbol": sym_pair, "interval": bybit_interval, "limit": limit},
                timeout=5,
            )
            if r.status_code != 200:
                continue
            j = r.json()
            if j.get("retCode") != 0:
                continue
            raw = j.get("result", {}).get("list", [])
            if not isinstance(raw, list) or len(raw) < 4:
                continue
            raw = list(reversed(raw))[-5:]
            out = []
            for bar in raw:
                try:
                    o, c = float(bar[1]), float(bar[4])
                    out.append({"open": o, "close": c})
                except (IndexError, TypeError, ValueError):
                    pass
            if len(out) >= 4:
                return out
        except Exception:
            pass
    return None


def _fetch_cg_klines_and_calc(symbol: str, interval: str = "15m", limit: int = 60) -> Optional[Dict[str, Any]]:
    """
    K 線四層降級策略（優先有 volume 的直連來源）：
      1. Binance 期貨直連（免 Key，有 volume）→ 覆蓋 Binance 上所有幣種
      2. Bybit 永續直連（免 Key，有 volume）  → 覆蓋 Bybit-only 幣種（如 XION, WHITEWHALE）
      3. CoinGlass 代理 OKX/BingX/Bitget     → 無 volume，但覆蓋剩餘冷門幣種
      4. BingX 現貨直連（免 Key，有 volume）  → 最終 fallback
    """
    clean = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()

    # ── Step 1: Binance 期貨直連（最優先，有 volume）───────────────────────
    _direct = _try_binance_futures_klines_direct(clean, interval, limit)
    if _direct:
        return _direct

    # ── Step 2: Bybit 永續直連（覆蓋 Bybit-only 幣種，有 volume）──────────
    _bybit = _try_bybit_futures_klines_direct(clean, interval, limit)
    if _bybit:
        return _bybit

    # ── Step 3: CoinGlass 代理（覆蓋剩餘幣種，無 volume）──────────────────
    # 保留 Bybit 作為 CoinGlass 代理備援，防止 Bybit 直連偶爾超時/失敗時完全無資料
    try_pairs = [f"{clean}USDT", f"1000{clean}USDT"]
    exchanges_to_try = ["OKX", "Bybit", "BingX", "Bitget"]
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

    # ── Step 4: BingX 現貨直連（最終 fallback，有 volume）────────────────
    _bingx = _try_bingx_spot_klines_direct(clean, interval, limit)
    if _bingx:
        return _bingx

    logger.warning(
        f"[CG K線] {clean}: 所有來源均無法取得足夠 K 線"
        f"（Binance直連 + Bybit直連 + CoinGlass/{exchanges_to_try} + BingX現貨）"
        f" → 可能為 Hyperliquid/Gate.io 專屬幣種"
    )
    return None


def calculate_technicals(
    symbol: str,
    bingx_symbol_override: Optional[str] = None,
    interval: str = "1h",
    limit: int = 48,
) -> Optional[Dict[str, Any]]:
    """
    技術指標計算入口（1H K 線為預設，適合中期波段策略）。
    interval="1h" limit=48 → 取得 2 天 1H 蠟燭，計算 RSI/ATR/EMA20/VWAP 等中期指標。
    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    logger.info(f"[技術指標] {base}: {interval} K 線計算技術指標")
    tech = _fetch_cg_klines_and_calc(symbol, interval=interval, limit=limit)
    if tech:
        return tech
    logger.warning(f"[技術指標] {base}: K 線失敗，技術指標無法計算")
    return None


# 四區塊 + 五星制：zone 為推播區塊名，stars 1=最差 5=最佳
ZONE_DIP = "抄底區"
ZONE_TOP = "摸頭區"
ZONE_BREAKOUT_LONG = "突破追漲區"
ZONE_BREAKOUT_SHORT = "跌破追跌區"

# 資金費率門檻
FUNDING_EXTREME = 0.0003    # 極端費率 0.03%，用於標註

# ── 資金費率多空壅擠過濾門檻 ─────────────────────────────────────────────
# 費率為小數（0.001 = 0.1%）
# 空頭壅擠（費率偏負）：做空訊號時觸發警戒/封鎖
FR_SHORT_SQUEEZE_RISK  = 0.001   # -0.1%：空頭開始壅擠 → 做空訊號降級
FR_SHORT_SQUEEZE_BLOCK = 0.003   # -0.3%：空頭嚴重壅擠，嘎空風險高 → 做空訊號封鎖
# 多頭壅擠（費率偏正）：做多訊號時觸發警戒/封鎖
FR_LONG_LIQUIDATION_RISK  = 0.002  # +0.2%：多頭開始壅擠 → 做多訊號降級
FR_LONG_LIQUIDATION_BLOCK = 0.005  # +0.5%：多頭嚴重壅擠，爆倉風險高 → 做多訊號封鎖

# ══════════════════════════════════════════════════════════════════════
# 1H MTF 四層漏斗策略門檻（集中管理，調參只需改這裡）
# ══════════════════════════════════════════════════════════════════════
MAIN_COINS = {"BTC", "ETH", "SOL"}  # 主流幣：1H OI > 4% 即達標

# ── 流動性門檻（24h 成交值，低於此深度不足）──────────────────────────
MTF_VOLUME_MIN_USD  = 9_990_000    # 999 萬 USD（過濾低流動性/單一莊家畫門）

# ── 1H OI 扳機門檻（動態分層，依幣種流動性調整）────────────────────────
# 嚴格版：主流 4% | 高流動山寨 6% | 其他小幣 8%（減少雜訊）
OI_THRESHOLD_MAIN   = 4.0           # 主流幣門檻
OI_THRESHOLD_HIGH_LIQ = 6.0        # 高流動性山寨（24h 成交值 > 50M USD）
OI_THRESHOLD_SMALL  = 8.0          # 其他小幣種
HIGH_LIQ_VOLUME_USD = 50_000_000   # 24h 成交值 > 50M 視為高流動性
OI_THRESHOLD_1H     = 5.0          # 向後相容預設值（實際由 _get_oi_threshold_for_item 動態決定）
PRICE_THRESHOLD_1H  = 1.5           # 1H 價格扳機門檻

# ── 風報比：1R = |進場價 − 止損價|；TP 為 1R 的倍數（與推播 R 標示一致）──────
TP1_R_MULTIPLIER = 1.0   # TP1 至少 1:1（相對於 1R）
TP2_R_MULTIPLIER = 3.0  # TP2 延伸目標（須 > TP1）
SL_R_LABEL = 1.0        # 推播顯示用：止損標為 -1.0R（1R = 進場到 SL 的距離）
MIN_SL_PERCENT = 0.015  # 最小止損距離：one_r/進場 < 1.5% 時強制拉開（避免一碰就碎）
# 綜合評分低於此不分級推播（S / A / R 皆不推）
MIN_SIGNAL_PUSH_SCORE = 70


def compute_structural_sl_tp(
    entry: float,
    is_long: bool,
    vwap_2h: Optional[float],
    ema20: Optional[float],
    recent_low_2h: Optional[float],
    recent_high_2h: Optional[float],
) -> Tuple[Optional[float], Optional[float], Optional[float], float, float]:
    """
    以 K 線結構主力防守位定 SL，再套用最小距離保底，最後以 1R 映射 TP1/TP2。

    做多：結構防守位 = min(2h低, EMA20, VWAP_2h)（略過 None）
    做空：結構防守位 = max(2h高, EMA20, VWAP_2h)（略過 None）

    one_r = |進場 − 結構 SL|；若 one_r/進場 < MIN_SL_PERCENT，強制 one_r = 進場×MIN_SL_PERCENT
    並反推 SL（多：進場−one_r；空：進場+one_r）。

    回傳 (sl, tp1, tp2, one_r, sl_pct)
    """
    if not entry or not isinstance(entry, (int, float)) or float(entry) <= 0:
        return None, None, None, 0.0, 0.0
    entry = float(entry)

    def _num(v: Optional[float]) -> Optional[float]:
        if v is None:
            return None
        try:
            x = float(v)
            return x if x > 0 and x == x else None
        except (TypeError, ValueError):
            return None

    vwap = _num(vwap_2h)
    ema = _num(ema20)
    lo2 = _num(recent_low_2h)
    hi2 = _num(recent_high_2h)

    if is_long:
        cands = [v for v in (lo2, ema, vwap) if v is not None]
        if cands:
            structural_sl = min(cands)
        else:
            structural_sl = entry * (1.0 - MIN_SL_PERCENT)
        if structural_sl >= entry:
            structural_sl = entry * (1.0 - MIN_SL_PERCENT)
        one_r = abs(entry - structural_sl)
        if one_r / entry < MIN_SL_PERCENT:
            one_r = entry * MIN_SL_PERCENT
            structural_sl = entry - one_r
        sl = structural_sl
        tp1 = entry + one_r * TP1_R_MULTIPLIER
        tp2 = entry + one_r * TP2_R_MULTIPLIER
    else:
        cands = [v for v in (hi2, ema, vwap) if v is not None]
        if cands:
            structural_sl = max(cands)
        else:
            structural_sl = entry * (1.0 + MIN_SL_PERCENT)
        if structural_sl <= entry:
            structural_sl = entry * (1.0 + MIN_SL_PERCENT)
        one_r = abs(structural_sl - entry)
        if one_r / entry < MIN_SL_PERCENT:
            one_r = entry * MIN_SL_PERCENT
            structural_sl = entry + one_r
        sl = structural_sl
        tp1 = entry - one_r * TP1_R_MULTIPLIER
        tp2 = entry - one_r * TP2_R_MULTIPLIER

    sl_pct = (one_r / entry * 100.0) if entry > 0 else 0.0
    return sl, tp1, tp2, one_r, sl_pct


def derive_limit_order_from_inputs(
    category: str,
    cur_price: Optional[float],
    vwap_2h: Optional[float],
    ema20: Optional[float],
    signal_version: str,
    energy_exhausted: bool,
) -> Tuple[bool, Optional[float]]:
    """
    與 build_report_message_tiered 進場邏輯一致（順序相同）：
      1) 衰竭反轉 → 限價於 VWAP，否則 EMA20
      2) 動能透支 → 限價於 EMA20
      3) VWAP：在帶內 → 市價；否則 掛單價 = VWAP×0.975（與現行程式相同）
    回傳 (is_limit_order, limit_price)；市價進場為 (False, None)。
    """
    is_bull = category in ("long_open", "short_close")
    try:
        price = float(cur_price) if cur_price is not None else None
    except (TypeError, ValueError):
        price = None
    if not price or price <= 0:
        return False, None

    sv = (signal_version or "").strip()
    try:
        vwap = float(vwap_2h) if vwap_2h is not None and float(vwap_2h) > 0 else None
    except (TypeError, ValueError):
        vwap = None
    try:
        ema = float(ema20) if ema20 is not None and float(ema20) > 0 else None
    except (TypeError, ValueError):
        ema = None

    if sv == "exhaustion_reversal":
        lp = vwap if vwap is not None else ema
        if lp is not None:
            return True, lp
        return False, None

    if energy_exhausted and ema is not None:
        return True, ema

    if vwap is not None:
        if is_bull and price <= vwap * 1.025:
            return False, None
        if (not is_bull) and price >= vwap * 0.975:
            return False, None
        return True, vwap * 0.975

    return False, None


# ── RSI 過熱/過冷阻斷（確定籌碼追高/追低保護）───────────────────────
MTF_RSI_OVERBOUGHT = 85             # 做多降級線（>85 降為 Tier2 觀察；單邊牛市容忍 RSI 鈍化）
MTF_RSI_OVERSOLD   = 15             # 做空降級線（<15 降為 Tier2 觀察；單邊熊市容忍 RSI 鈍化）

# ── 衍生/向後相容別名（供其他函數引用）──────────────────────────────
OI_MAIN_COIN_MIN    = OI_THRESHOLD_MAIN
OI_ALTCOIN_MIN      = OI_THRESHOLD_HIGH_LIQ  # 預設用高流動性門檻，實際由動態函數決定
OI_FOR_5_STAR       = OI_THRESHOLD_1H
OI_FOR_4_STAR       = OI_THRESHOLD_1H
OI_FOR_ELITE        = OI_THRESHOLD_1H
OI_THRESHOLD_30M    = OI_THRESHOLD_1H
PRICE_THRESHOLD_30M = PRICE_THRESHOLD_1H

def _get_oi_threshold_for_item(item: Dict) -> float:
    """
    動態 OI 門檻：依幣種流動性分層（嚴格版）。
    - 主流幣 (BTC/ETH/SOL)：4%
    - 高流動性山寨（24h 成交值 > 50M USD）：6%
    - 其他小幣種：8%
    """
    sym = item.get("symbol") or item.get("coin") or ""
    base = str(sym).replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
    if base in MAIN_COINS:
        return OI_THRESHOLD_MAIN
    vol = (
        item.get("_volume_usd") or item.get("_cg_volume_usd") or
        item.get("volume_usd") or item.get("volUsd24h") or 0
    )
    try:
        vol_f = float(vol)
        if vol_f >= HIGH_LIQ_VOLUME_USD:
            return OI_THRESHOLD_HIGH_LIQ
    except (TypeError, ValueError):
        pass
    return OI_THRESHOLD_SMALL


# ── 黑名單：永久禁止推播的標的（可隨時新增/移除）────────────────────────────────
# 原則：歷史表現差、流動性不足、長期被操控的幣種
SYMBOL_BLACKLIST: set = {
    # ── 已知問題幣（操縱/流動性/無意義/極小市值）──
    "BULLA", "FIO", "ORBS", "NEIROCTO", "DENT",
    "RTX", "IKA", "POND", "1000NEIROCTO",
    "ULTIMA", "REAL", "CRCLX", "TFUEL",
    "WHITEWHALE", "PYR",
    "MANYU",      # 極小市值 meme 幣，價格 ~7e-9 USD，無交易意義
    "CITY",       # 用戶手動加入黑名單
    "REQ", "STEEM", "ROAM",  # 用戶手動加入黑名單（2026-03-02）
    "CELR", "ATA", "ICX", "AGT", "ALU", "CAMP",  # 用戶手動加入黑名單（2026-03-02/03）
    "BOBA", "AIO", "BTR",  # 用戶手動加入黑名單（2026-03-03）
    "BSU", "AVL",  # 用戶手動加入黑名單（2026-03-04）
    "GODS", "ASP", "VFY", "FHE",  # 用戶手動加入黑名單（2026-03-05）
    "HOT",  # 用戶手動加入黑名單（2026-03-06）
    "WAXP",  # 用戶手動加入黑名單（waxp）
    "NG",   # 用戶手動加入黑名單（流動性/表現問題）
    "BICO", "GIGA", "CLOUD", "JELLYJELLY",  # 用戶手動加入黑名單（2026-03-06）
    "CVX", "L3", "DOGS", "ETHW", "1000QUBIC",  # 用戶手動加入黑名單（2026-03-07）
    "JOE", "RONIN", "1000XEC", "XAUT",  # 用戶手動加入黑名單（2026-03-07）
    "BTT", "BDXN", "LIGHT", "SC", "DEXE", "XVG", "RDNT", "BAND", "0GN", "GTC", "G", "BAN", "BNT",  # 用戶手動加入黑名單
    "MASTOCK",    # 代幣化股票，OI 數據異常（曾觸發 621% 極端值）
    "PLTRSTOCK",  # Palantir 代幣化股票（STOCK 後綴格式）
    # ── 其他非加密貨幣期貨 ──
    "XTI",        # WTI 原油期貨（XTI/USD）
    "XBR",        # Brent 原油期貨
    "KO",         # Coca-Cola 股票
    # ── 代幣化股票（Bybit/BingX/Bitget 合約，非加密貨幣）──
    # Bybit 以不帶 STOCK 後綴的格式上架，需明確列出
    "TSLAX", "TSLA",
    "NVDAX", "NVDA",
    "AAPLX", "AAPL",
    "AMZNX", "AMZN",
    "GOOGLX", "GOOGL",
    "METAX",
    "MSFTX", "MSFT",
    "COINX", "COIN",
    "NFLXX", "NFLX",
    "GME", "GMEX",          # GameStop
    "LMT", "LMTX",          # Lockheed Martin
    "BABA", "BABAX",        # Alibaba
    "ABNBX", "ABNB",        # Airbnb
    "PLTR",                 # Palantir（Bybit 無 STOCK 後綴格式）
    "AMD", "AMDX",          # Advanced Micro Devices
    "INTC", "INTCX",        # Intel
    "HOOD", "HOODX",        # Robinhood
    "MSTR", "MSTRX",        # MicroStrategy
    "MARA", "MARAX",        # Marathon Digital
    "RIOT", "RIOTX",        # Riot Platforms
    "BA", "BAX",            # Boeing
    "DIS", "DISX",          # Disney
    "SBUX", "SBUXS",        # Starbucks
    "JPM", "JPMX",          # JPMorgan
    "PYPL", "PYPLX",        # PayPal
    # ── 傳統商品期貨 ──
    "COPPER", "SILVER", "GOLD", "XAU", "XAG",
    "XBR", "OIL", "BRENT", "WTI", "USOIL",
    # ── 波動率指數 / 傳統指數 ──
    "VIX", "VIXINDEX",
    "DXY", "SPX", "NDX", "ES",
    "US2000", "US30", "US500", "NAS100",  # 美股指數
    # ── 亞洲股票指數期貨（BingX/Bitget 有交易）──
    "HK50", "HKTECH",                     # 恒生指數 / 恒生科技
    "JP225", "NIKKEI", "NIKKEI225",       # 日經指數
    "CN50", "CHINA50", "CSI300",          # 中國A50 / 滬深300
    "AU200", "SG30",                      # 澳洲/新加坡指數
    "UK100", "DE40", "FR40", "EU50",      # 歐洲指數
}


def _check_manipulation_risk(
    item: Dict,
    tech: Optional[Dict],
    atr_val: Optional[float],
    category: str = "",
) -> Tuple[str, bool]:
    """
    反畫門防護（Anti-Manipulation Gate）。

    莊家用法：花 $50萬美金在冷門山寨幣開一個巨大倉位，讓 15m OI 暴增 5%、價格拉抬 2%，
    機器人觸發推播，下一根 15m 蠟燭立刻砸盤出貨，追進者被套在山頂（俗稱「畫門」）。

    ┌─────────────────────────────────────────────────────────────────────┐
    │  類型差異（關鍵設計）                                                 │
    │  long_open / short_open  = 順勢突破型（容易被畫門，三條件全套用）     │
    │  long_close / short_close = 逆勢摸頂底型（大蠟燭反而是訊號本身）      │
    │    → 條件1（蠟燭大小）和條件3（1H OI逆向）對逆勢型無意義，跳過        │
    │    → 條件2（薄幣大OI）仍套用，但閾值放寬至不影響正常摸頂底            │
    └─────────────────────────────────────────────────────────────────────┘

    回傳 (block_reason, energy_exhausted)。
    block_reason 非空 = 封鎖；energy_exhausted = True 時標記「動能透支」，強制限價掛單於 EMA20。
    """
    energy_exhausted = False
    oi30 = float(item.get("oiChange30m") or 0)
    vol_usd = (
        item.get("_volume_usd")
        or item.get("_cg_volume_usd")
        or item.get("volume_usd")
        or 0
    )
    try:
        vol_m = float(vol_usd) / 1e6
    except (TypeError, ValueError):
        vol_m = 0.0

    # 逆勢訊號（摸頂底）：long_close = 頂部做空、short_close = 底部做多
    is_reversal = category in ("long_close", "short_close")

    abs_oi = abs(oi30)

    # ── 條件 1：蠟燭實體過大（順勢突破型才適用，門檻嚴格化為 1.5x ATR）────────────────
    # 摸頂底本身就是在大蠟燭出現後入場（大陽棒後做空、大陰棒後做多），
    # 所以逆勢型跳過此條件，只對 long_open / short_open 套用。
    # 2026-03-07：body_atr >= 1.5 不封鎖，改為標記「動能透支」，強制限價掛單於 EMA20
    if not is_reversal and tech and atr_val and atr_val > 0:
        _ko = tech.get("last_kline_open_30m")
        _kc = tech.get("last_kline_close_30m")
        if _ko and _kc:
            candle_body = abs(float(_kc) - float(_ko))
            body_atr = candle_body / atr_val
            if body_atr >= 1.5:
                energy_exhausted = True
                # 不封鎖，僅標記動能透支，推播時強制限價掛單

    # ── 條件 2：薄流動性 + 大幅 OI 暴增（順勢 / 逆勢均套用，閾值不同）────
    # 逆勢型（摸頂底）放寬閾值：正常的踩踏/軋空 在小幣也可能有 4~5% OI，
    # 只封鎖明顯異常的極端情況（vol < 2M + OI ≥ 8%）。
    if is_reversal:
        if 0 < vol_m < 2.0 and abs_oi >= 8.0:
            return (
                f"極薄幣劇烈OI：成交值 {vol_m:.1f}M 但 OI 波動 {abs_oi:.1f}%，"
                f"單人資金即可偽造踩踏/軋空訊號"
            ), False
    else:
        # 順勢突破型：較嚴格，少量資金即可偽造突破
        if 0 < vol_m < 3.0 and abs_oi >= 4.0:
            return (
                f"薄幣大OI：成交值 {vol_m:.1f}M 但 OI 暴增 {abs_oi:.1f}%，"
                f"少量資金即可偽造此突破訊號"
            ), False
        if 0 < vol_m < 5.0 and abs_oi >= 7.0:
            return (
                f"低流動性劇烈波動：成交值 {vol_m:.1f}M & OI {abs_oi:.1f}%，"
                f"畫門風險極高"
            ), False

    # ── 條件 3：1H OI 逆向 + 低流動性（順勢突破型才適用）──────────────────
    # 逆勢摸頂底訊號本來就預期 15m 和 1H OI 方向不一致（這是訊號本身的邏輯），
    # 套用此條件會錯誤地封鎖幾乎所有摸頂底，因此逆勢型跳過。
    if not is_reversal:
        oi_1h_pct = item.get("oi_change_1h_pct")
        oi_1h_same_dir = (
            isinstance(oi_1h_pct, (int, float))
            and oi30 != 0
            and (oi30 * oi_1h_pct) > 0
        )
        if (
            isinstance(oi_1h_pct, (int, float))
            and not oi_1h_same_dir
            and 0 < vol_m < 7.0
        ):
            return (
                f"1H OI逆向（{oi_1h_pct:+.2f}%）+ 成交值僅 {vol_m:.1f}M，"
                f"15m 孤立操縱嫌疑高，1H 大週期未確認"
            ), False

    return "", energy_exhausted  # 放行


def _classify_mtf_signal(item: Dict) -> Optional[Dict[str, Any]]:
    """
    MTF 四層訊號分類器（嚴格版 v3 — 寧缺勿濫）

    四象限籌碼狀態定義：
      🟢 long_open:   OI↑ + Price↑ → 多方建倉
      🔴 short_open:  OI↑ + Price↓ → 空方建倉
      🟡 long_close:  OI↓ + Price↓ → 多方平倉
      🔵 short_cover: OI↓ + Price↑ → 空方回補

    ★ 推播類型（優先順序）：
      🔥 E. exhaustion_reversal (衰竭反轉)：動能衰竭後二次確認，勝率極高狙擊點
         恐慌抄底：1H long_close + RSI<35 + 15m/5m short_cover 或 long_open → 做多
         誘多摸頭：1H short_cover + RSI>65 + 15m/5m long_close 或 short_open → 做空
      ✅ A. confirmed (確定籌碼)：1H/30m/15m/5m 四層方向完全一致
                                   + RSI 未達追高/追空極端（做多≤75，做空≥25）
      🎯 B. pullback  (完美回踩)：1H/30m 同向 + 15m/5m 呈現短線反向平倉/開倉
                                   （大趨勢回調低接點）

    ✘ 以下情況一律 return None：
      - Step 2 衝突：30m 主力方向與 1H 相反
      - RSI 過熱/過冷：確定籌碼條件已達但 RSI 極端
      - 逆勢反轉：1H 末段特徵（雖有邏輯但風險高，排除）
      - 弱共振：只有 1H 達標，其他時區方向凌亂
    """
    cat_1h = item.get("category") or ""
    # 標準化：1H 掃描產生的 "short_close" 與內部 _get_cat() 的 "short_cover" 是同一籌碼狀態
    # （OI↓ + Price↑ = 空方回補），統一轉為 short_cover 供後續方向比對
    if cat_1h == "short_close":
        cat_1h = "short_cover"
    oi_1h  = item.get("oiChange1h") or item.get("oiChange30m") or 0
    p_1h   = item.get("priceChange1h") or 0
    oi_30m = item.get("oiChange_30m")
    p_30m  = item.get("priceChange30m")
    oi_15m = item.get("oiChange_15m")
    oi_5m  = item.get("oiChange_5m")
    rsi    = item.get("rsi")

    # ── 四象限分類函數 ─────────────────────────────────────────────────────────
    def _get_cat(oi_val: Optional[float], price_val: Optional[float]) -> Optional[str]:
        """根據 OI 方向 + 價格方向決定籌碼四象限狀態"""
        if oi_val is None:
            return None
        if oi_val > 0:
            return "long_open" if (price_val is None or price_val >= 0) else "short_open"
        else:
            return "short_cover" if (price_val is None or price_val > 0) else "long_close"

    # 30m：有 priceChange30m 原始數據
    cat_30m = _get_cat(oi_30m, p_30m)
    # 15m/5m：無獨立 price，以 1H price 方向作為近似（同趨勢推估）
    cat_15m = _get_cat(oi_15m, p_1h)
    cat_5m  = _get_cat(oi_5m,  p_1h)

    # ── Step 2 衝突判定 ────────────────────────────────────────────────────────
    is_1h_bull = cat_1h in ("long_open", "short_cover")
    is_1h_bear = cat_1h in ("short_open", "long_close")
    # 僅攔「1H 多頭陣營 vs 30m 空方主動建倉」等硬對做；回調類 long_close / short_cover 不視為衝突。
    # （與 _calc_signal_grade 推播門檻 ≥MIN_SIGNAL_PUSH_SCORE 並用。）
    step2_conflict = (
        (is_1h_bull and cat_30m == "short_open") or
        (is_1h_bear and cat_30m == "long_open")
    )

    # ── RSI 極端判定 ──────────────────────────────────────────────────────────
    rsi_f          = float(rsi) if rsi is not None and isinstance(rsi, (int, float)) else None
    rsi_overbought = rsi_f is not None and rsi_f > MTF_RSI_OVERBOUGHT
    rsi_oversold   = rsi_f is not None and rsi_f < MTF_RSI_OVERSOLD

    # ── 顯示標籤定義 ──────────────────────────────────────────────────────────
    _cat_emoji = {
        "long_open": "🟢", "short_open": "🔴",
        "long_close": "🟡", "short_cover": "🔵", None: "❓",
    }
    _cat_name = {
        "long_open": "多方建倉", "short_open": "空方建倉",
        "long_close": "多方平倉", "short_cover": "空方回補", None: "無數據",
    }
    oi_1h_s  = f"{oi_1h:+.1f}%"
    oi_30m_s = f"{oi_30m:+.1f}%" if oi_30m is not None else "—"
    oi_15m_s = f"{oi_15m:+.1f}%" if oi_15m is not None else "—"
    oi_5m_s  = f"{oi_5m:+.1f}%"  if oi_5m  is not None else "—"
    rsi_tag  = f", RSI: {rsi_f:.0f}" if rsi_f is not None else ""
    warn_30m = " ⚠️衝突" if step2_conflict else ""

    # 供訊息顯示的 MTF 漏斗文字（每層一行）
    mtf_funnel = (
        f"1H：{_cat_emoji.get(cat_1h,'❓')} {_cat_name.get(cat_1h,'—')} "
        f"(OI {oi_1h_s}{rsi_tag})\n"
        f"30m：{_cat_emoji.get(cat_30m,'❓')} {_cat_name.get(cat_30m,'—')}{warn_30m}\n"
        f"15m：{_cat_emoji.get(cat_15m,'❓')} {_cat_name.get(cat_15m,'—')}\n"
        f"5m：{_cat_emoji.get(cat_5m,'❓')} {_cat_name.get(cat_5m,'—')}"
    )
    mtf_oi_line = (
        f"📡 OI: 1H`{oi_1h_s}` 30m`{oi_30m_s}` 15m`{oi_15m_s}` 5m`{oi_5m_s}`"
    )
    base = {
        "mtf_desc": mtf_funnel, "mtf_oi_line": mtf_oi_line,
        "cat_30m": cat_30m, "cat_15m": cat_15m, "cat_5m": cat_5m,
        "step2_conflict": step2_conflict,
    }

    # ══════════════════════════════════════════════════════════
    # 優先：衰竭反轉（exhaustion_reversal）— 動能衰竭後的二次確認反轉，勝率極高
    # 不與順勢突破衝突：此為逆勢抄底/摸頭，條件滿足時優先回傳
    # ─────────────────────────────────────────────────────────
    # 【恐慌抄底 Bottom】1H long_close（多頭爆倉）+ RSI<35 + 15m/5m 出現 short_cover 或 long_open
    # 【誘多摸頭 Top】   1H short_cover（空軍被軋空）+ RSI>65 + 15m/5m 出現 long_close 或 short_open
    # ══════════════════════════════════════════════════════════
    small_confirm_bottom = any(
        c in ("short_cover", "long_open") for c in [cat_15m, cat_5m] if c
    )
    small_confirm_top = any(
        c in ("long_close", "short_open") for c in [cat_15m, cat_5m] if c
    )
    if cat_1h == "long_close" and rsi_f is not None and rsi_f < 35 and small_confirm_bottom:
        return {**base, "version": "exhaustion_reversal", "subtype": "bottom",
                "aligned_count": 2, "reversal_hint": "空方動能衰竭，出現獲利回補跡象",
                "exhaustion_direction": "long"}
    if cat_1h == "short_cover" and rsi_f is not None and rsi_f > 65 and small_confirm_top:
        return {**base, "version": "exhaustion_reversal", "subtype": "top",
                "aligned_count": 2, "reversal_hint": "多方動能衰竭，出現獲利回補跡象",
                "exhaustion_direction": "short"}

    # ══════════════════════════════════════════════════════════
    # 三層決策樹 v4（鐵三角 + 5m 雜訊容忍 + Tier2 觀察名單）
    #
    # ✅ A. confirmed（確定籌碼）：1H = 30m = 15m 三層完全一致，5m 允許雜訊
    #       RSI 極端（>85 / <15）→ 降為 Tier2，不直接丟棄
    # 🎯 B. pullback（完美回踩）：1H/30m 同向 + 15m/5m 呈短線反向
    # ⚠️ C. tier2（觀察名單）：1H/30m 同方向陣營，但 15m 凌亂或 RSI 極端
    #       推播時加「⚠️ 觀察名單」標籤，提醒輕倉
    # ✘ D. 其餘（Step2 衝突 / 1H&30m 大方向完全相反）→ return None 丟棄
    # ══════════════════════════════════════════════════════════

    # Step 2 衝突 → 直接丟（Lazy Fetching 外層已提前攔截，此為第二道防線）
    if step2_conflict:
        return None

    # ──────────── 前置：方向陣營判定 ─────────────────────────────────────
    is_30m_bull = cat_30m in ("long_open", "short_cover")
    is_30m_bear = cat_30m in ("short_open", "long_close")

    # ══ A. 確定籌碼（鐵三角）：1H = 30m = 15m 精確一致，5m 不強制 ══════════
    iron_triangle = (
        cat_1h is not None
        and cat_1h == cat_30m
        and (cat_15m == cat_1h or cat_15m is None)
    )
    if iron_triangle:
        if (is_1h_bull and rsi_overbought) or (is_1h_bear and rsi_oversold):
            # RSI 極端：趨勢仍明確，但追高/追空風險高 → 降級為 Tier2 觀察
            return {**base, "version": "tier2", "subtype": "RSI極端",
                    "aligned_count": 3,
                    "reversal_hint": f"⚠️ RSI={rsi_f:.0f} 已達極端，鐵三角成立但建議輕倉觀察"}
        return {**base, "version": "confirmed", "subtype": "",
                "aligned_count": 3, "reversal_hint": ""}

    # ══ B. 完美回踩（Pullback）：1H/30m 同向 + 15m/5m 短線反向 ════════════
    small_reversing_from_bull = any(
        c in ("long_close", "short_open") for c in [cat_15m, cat_5m] if c
    )
    small_reversing_from_bear = any(
        c in ("short_cover", "long_open") for c in [cat_15m, cat_5m] if c
    )
    if is_1h_bull and is_30m_bull and small_reversing_from_bull:
        return {**base, "version": "potential", "subtype": "pullback",
                "aligned_count": 2, "reversal_hint": ""}
    if is_1h_bear and is_30m_bear and small_reversing_from_bear:
        return {**base, "version": "potential", "subtype": "pullback",
                "aligned_count": 2, "reversal_hint": ""}

    # ══ C. 次級訊號（Tier2）：1H/30m 大方向同陣營，但 15m 凌亂 ═══════════
    # 比 confirmed/pullback 弱，但仍有方向性價值 → 觀察名單推播
    big_picture_aligned = (
        (is_1h_bull and is_30m_bull) or
        (is_1h_bear and is_30m_bear)
    )
    if big_picture_aligned:
        _t2_dir  = "多" if is_1h_bull else "空"
        _t2_hint = (
            f"1H/30m 同{_t2_dir}方向，"
            f"但 15m={_cat_name.get(cat_15m,'N/A')} / 5m={_cat_name.get(cat_5m,'N/A')} 尚未確認"
        )
        return {**base, "version": "tier2", "subtype": "弱共振",
                "aligned_count": 2,
                "reversal_hint": ""}

    # ══ D. 其他（大方向矛盾）→ 丟棄 ════════════════════════════════════════
    return None


def _classify_signal_and_tier(
    item: Dict,
    category: str,
    tech: Optional[Dict],
    funding_rate: Optional[float] = None,
    price_chg_24h: Optional[float] = None,
    price_chg_1h: Optional[float] = None,
    cvd_change_1h: Optional[float] = None,
    whale_index: Optional[float] = None,
    retail_ratio: Optional[float] = None,
    btc_30m_pct: Optional[float] = None,
) -> Optional[Tuple[str, str, int, str, str]]:
    """
    四象限訊號分類（1H MTF 四層漏斗策略：1H 扳機 + 24H 趨勢濾網）。

    扳機條件（1H）：
      |OI 1H| >= 1.5%  且  |Price 1H| >= 1.5%

    趨勢濾網（24H）：
      多頭訊號（long_open / short_close）：price_24h > 0 代表大方向順風
      空頭訊號（short_open / long_close）：price_24h < 0 代表大方向順風
      ⚠️ 逆風：放行但加標記（中期波段允許逆勢佈局）
    """
    oi = item.get("oiChange1h") or item.get("oiChange30m") or 0
    price_chg_1h_main = item.get("priceChange1h") or item.get("priceChange30m")
    if price_chg_1h_main is not None and not isinstance(price_chg_1h_main, (int, float)):
        price_chg_1h_main = None

    # 扳機條件：1H OI 絕對值 >= 動態門檻（主流 4% / 高流動性山寨 6% / 小幣 8%）
    oi_threshold = _get_oi_threshold_for_item(item)
    if abs(oi) < oi_threshold:
        return None

    # 1H 趨勢濾網：多頭訊號需 1h > 0，空頭訊號需 1h < 0
    is_bull_signal = category in ("long_open", "short_close")
    is_bear_signal = category in ("short_open", "long_close")

    # 24H 大趨勢濾網（1H 格局以 24H 為「大方向」判斷順逆風）
    mtf_trend_ok = True
    mtf_note = ""
    _p24h = price_chg_24h
    if _p24h is not None and isinstance(_p24h, (int, float)):
        if is_bull_signal and _p24h <= -5.0:
            mtf_trend_ok = False
            mtf_note = f" ⚠️24H下跌趨勢({_p24h:+.1f}%)"
        elif is_bear_signal and _p24h >= 5.0:
            mtf_trend_ok = False
            mtf_note = f" ⚠️24H上漲趨勢({_p24h:+.1f}%)"
        else:
            mtf_note = f" 24H{_p24h:+.1f}%"
    else:
        mtf_note = ""

    # ── 4H OI 累積分析（波段末段警示）──────────────────────────────────────
    oi_4h_pct = item.get("oi_change_4h_pct")
    _oi_mtf_note = ""
    oi_1h_confirmed = True   # 1H 已是主時框，預設確認
    if isinstance(oi_4h_pct, (int, float)):
        _abs_4h = abs(oi_4h_pct)
        if _abs_4h >= 5.0:
            _oi_mtf_note = f" ⚠️4H OI已累積{_abs_4h:.1f}%（波段末段，縮目標）"
        elif _abs_4h >= 2.5:
            _oi_mtf_note = f" 🟠4H OI{_abs_4h:.1f}%（中段，謹慎）"
        else:
            _oi_mtf_note = " 🟢4H OI剛啟動（波段初期，空間足）"

    # RSI 描述
    rsi = tech.get("rsi") if tech else None
    if rsi is not None:
        if rsi > 70:
            rsi_desc = f"RSI {rsi:.0f}(超買)"
        elif rsi < 30:
            rsi_desc = f"RSI {rsi:.0f}(超賣)"
        else:
            rsi_desc = f"RSI {rsi:.0f}"
    else:
        rsi_desc = "RSI —"

    # 資金費率標註
    fr_note = ""
    if funding_rate is not None and isinstance(funding_rate, (int, float)):
        if funding_rate > FUNDING_EXTREME:
            fr_note = " ⛽費率偏正"
        elif funding_rate < -FUNDING_EXTREME:
            fr_note = " 🔥費率偏負"

    # 四象限分類（依 category 決定訊號標籤與 zone）
    _counter_hint = ""
    if not mtf_trend_ok:
        if category in ("long_open", "short_close"):
            _counter_hint = "（24H逆勢做多，適合逆勢左側佈局，嚴控倉位）"
        elif category in ("short_open", "long_close"):
            _counter_hint = "（24H逆勢做空，適合逆勢左側佈局，嚴控倉位）"

    if category == "long_open":
        label = "🚀 多頭入場"
        zone = ZONE_BREAKOUT_LONG
        _trend = "逆勢左側佈局" if not mtf_trend_ok else "順勢右側追多"
        reason = f"1H OI↑+Price↑，主力積極建多倉，{_trend}{fr_note}{mtf_note}{_counter_hint}{_oi_mtf_note}"
    elif category == "short_open":
        label = "🐻 空頭入場"
        zone = ZONE_BREAKOUT_SHORT
        _trend = "逆勢左側佈局" if not mtf_trend_ok else "順勢右側追空"
        reason = f"1H OI↑+Price↓，空頭積極建倉，{_trend}{fr_note}{mtf_note}{_counter_hint}{_oi_mtf_note}"
    elif category == "long_close":
        label = "💥 多頭平倉"
        zone = ZONE_DIP
        _trend = "逆勢摸底機會" if not mtf_trend_ok else "1H下行加速"
        reason = f"1H OI↓+Price↓，多頭斷頭出場，空方平倉做空或等反彈做多，{_trend}{fr_note}{mtf_note}{_counter_hint}{_oi_mtf_note}"
    elif category == "short_close":
        label = "🔥 空頭平倉"
        zone = ZONE_TOP
        _trend = "逆勢摸頂機會" if not mtf_trend_ok else "1H上行加速"
        reason = f"1H OI↓+Price↑，空頭遭軋空回補，多方平倉做空或追多，{_trend}{fr_note}{mtf_note}{_counter_hint}{_oi_mtf_note}"
    else:
        return None

    # oi_1h_confirmed 存回 item 供 build_report_message_tiered 的 💎 升級邏輯使用
    item["_oi_1h_confirmed"] = oi_1h_confirmed

    return (label, zone, 5, rsi_desc, reason)


def detect_trap_setup(oi_candles: list, trap_type: str, kline_candles: Optional[list] = None) -> dict:
    """
    偵測「3 步反轉陷阱」——OI + 價格雙重確認。

    輸入：
      oi_candles: 最近 4 根 15m 已收盤 K棒的 OI 數值（由舊到新）
      trap_type: "short" = 摸頭（short_open） / "long" = 摸底（long_open）
      kline_candles: [{"open": float, "close": float}, ...] 由舊到新，至少 4 根；None 則僅用 OI
    輸出: {"detected": bool, "matched_steps": int, "note": str}

    摸頭（short_open）：
      Step 1 (K-2): 空方平倉 = OI↓ 且 價格上漲
      Step 2 (K-1): 多方平倉 = OI↓ 且 價格下跌或停滯
      Step 3 (K-0): short_open = OI↑ 且 價格下跌
    摸底（long_open）：
      Step 1 (K-2): 多方平倉 = OI↓ 且 價格下跌
      Step 2 (K-1): 空方平倉 = OI↓ 且 價格上漲或停滯
      Step 3 (K-0): long_open = OI↑ 且 價格上漲
    """
    result = {"detected": False, "matched_steps": 0, "note": ""}
    if not oi_candles or len(oi_candles) < 4:
        return result

    try:
        def _oi(d):  return float(d.get("c") or 0)

        k3, k2, k1, k0 = oi_candles[-4], oi_candles[-3], oi_candles[-2], oi_candles[-1]

        # 價格方向（有 kline 時使用；無則僅用 OI）
        # idx 0=K-2, 1=K-1, 2=K-0 → kline_candles[-3], [-2], [-1]
        def _price_up(idx: int) -> bool:
            if not kline_candles or len(kline_candles) < 4 - idx:
                return True  # 無資料時寬鬆通過
            b = kline_candles[-(3 - idx)]
            o, c = b.get("open"), b.get("close")
            if o is None or c is None:
                return True
            return float(c) > float(o)

        def _price_down(idx: int) -> bool:
            if not kline_candles or len(kline_candles) < 4 - idx:
                return True
            b = kline_candles[-(3 - idx)]
            o, c = b.get("open"), b.get("close")
            if o is None or c is None:
                return True
            return float(c) < float(o)

        def _price_down_or_flat(idx: int) -> bool:
            if not kline_candles or len(kline_candles) < 4:
                return True
            b = kline_candles[-(3 - idx)]
            o, c = b.get("open"), b.get("close")
            if o is None or c is None:
                return True
            o, c = float(o), float(c)
            if o <= 0:
                return True
            return c <= o or abs(c - o) / o < 0.003  # 0.3% 內視為停滯

        def _price_up_or_flat(idx: int) -> bool:
            if not kline_candles or len(kline_candles) < 4:
                return True
            b = kline_candles[-(3 - idx)]
            o, c = b.get("open"), b.get("close")
            if o is None or c is None:
                return True
            o, c = float(o), float(c)
            if o <= 0:
                return True
            return c >= o or abs(c - o) / o < 0.003

        # idx: 0=K-2, 1=K-1, 2=K-0（對應 kline_candles[-3], [-2], [-1]）
        if trap_type == "short":
            step1 = _oi(k2) < _oi(k3) and _price_up(0)  # K-2: OI↓ 且 價格上漲（空平）
            step2 = _oi(k1) < _oi(k2) and _price_down_or_flat(1)  # K-1: OI↓ 且 價格跌或停滯（多平）
            step3 = _oi(k0) > _oi(k1) and _price_down(2)  # K-0: OI↑ 且 價格下跌（空開）
        else:
            step1 = _oi(k2) < _oi(k3) and _price_down(0)  # K-2: OI↓ 且 價格下跌（多平）
            step2 = _oi(k1) < _oi(k2) and _price_up_or_flat(1)  # K-1: OI↓ 且 價格漲或停滯（空平）
            step3 = _oi(k0) > _oi(k1) and _price_up(2)  # K-0: OI↑ 且 價格上漲（多開）

        matched = sum([step1, step2, step3])
        result["matched_steps"] = matched

        if matched >= 3:
            result["detected"] = True
            if trap_type == "short":
                result["note"] = (
                    "🎯 *【頂級摸頭】完美符合：*\n"
                    "_空平推升 👉 多平出貨 👉 空軍大舉進場！_"
                )
            else:
                result["note"] = (
                    "🎯 *【頂級摸底】完美符合：*\n"
                    "_多軍斷頭 👉 空平回補 👉 主力進場抄底！_"
                )
        elif matched >= 2:
            if trap_type == "short":
                result["note"] = (
                    "⚠️ *【潛在摸頭】*\n"
                    "_多平出貨後空軍進場，留意反轉。_"
                )
            else:
                result["note"] = (
                    "⚠️ *【潛在摸底】*\n"
                    "_空平回補後主力進場，留意反轉。_"
                )
    except Exception:
        pass

    return result


def _calc_signal_grade(x: dict, is_bull_sig: bool) -> tuple:
    """
    計算訊號綜合評級（S / A / R / B），含 4H 趨勢對齊 (Trend Alignment)。
    返回 (grade_str, score_int, brief_reason_str, already_moving_bool, motion_note_str)

    ── 4H 天候（is_above_4h_ema）────────────────────────────────────────
      順勢：做多/偏多類 且 價在 4H EMA 上；做空/偏空類 且 價在 4H EMA 下
      逆勢：與上相反（左側摸底/摸頭）

    ── 分級（先算滿分流程分數，再依 4H 對齊分流）──────────────────────────
      S：score ≥ 80 且 順應 4H
      A：score ≥ 60 且 順應 4H
      R：score ≥ MIN_SIGNAL_PUSH_SCORE 且 明確違背 4H（逆勢左側）
      B：score < MIN_SIGNAL_PUSH_SCORE，或 4H 天候無法確認 → 不推播

    ── 順勢訊號評分（滿分 100）─────────────────────────────────────────
      1. 訊號版本強度    (max 40) ── confirmed=40 / tier2=20 / potential=10
      2. MTF 多框架對齊  (max 25) ── 4框=25 / 3框=18 / 2框=10 / 1框=3
      3. 4H 宏觀天候     (max 15) ── 順勢+15 / 未知 0
      4. RSI 技術位       (−5~+10) ── 理想區間+10 / 中性+5 / 危險 −5
      5. 1H OI 強度      (max 10) ── >8%=10 / 5-8%=7 / 3-5%=5 / <3%=2
      6. 趨勢情境        (−15~+15) ── 下跌段做多/上漲段做空=+15；上漲段做多/下跌段做空=−10
      7. 跨類別互確認    (max 20) ── 同輪出現互補訊號（嘎空+多開 / 出貨+空開）加分
      8. 大盤敬畏：做多且 BTC 1H<0 或 做空且 BTC 1H>0 → 強制 −10

    ── 車已發動 / 大盤逆風：分數硬上限 74（最高 A）──────────────────────
    """
    # ══════════════════════════════════════════════════════════════
    # 第一步：硬過濾（勝率優先）→ 直接 B 級（不推播）
    # ══════════════════════════════════════════════════════════════
    _sym = str(x.get("symbol") or "").replace("USDT", "").replace("-", "").replace("_", "").upper()
    _cat = x.get("category", "")

    # 1) 聰明錢 OI 驗證：散戶槓桿主導 = 假突破高風險，直接淘汰
    _smart_money = x.get("smart_money")
    if _smart_money is False:
        logger.info(f"[過濾] 散戶槓桿假突破 {(_sym or 'N/A')} ({_cat}) → 強制 B 級不推播")
        return "B", 0, "🥈 *B 級* 散戶槓桿主導，假突破風險高（不推播）", False, ""

    # 2) CVD 背離一票否決：方向相反 = 主力吸收/出貨，直接淘汰
    _cvd_div = str(x.get("cvd_divergence") or "").lower().strip()
    _is_bull_sig = _cat in ("long_open", "short_close")
    _is_bear_sig = _cat in ("short_open", "long_close")
    if (_is_bull_sig and _cvd_div == "bearish") or (_is_bear_sig and _cvd_div == "bullish"):
        logger.info(
            f"[過濾] CVD背離方向相反 {(_sym or 'N/A')} ({_cat}) cvd={_cvd_div} "
            f"→ 強制 B 級不推播"
        )
        return "B", 0, "🥈 *B 級* CVD 背離方向相反（主力吸收/出貨，風險高）", False, ""

    # ══════════════════════════════════════════════════════════════
    # 第二步：4H 趨勢對齊（供最後 S/A/R 分流；分數門檻與逆勢 R 一併判定）
    # ══════════════════════════════════════════════════════════════
    is_above_4h = x.get("is_above_4h_ema")
    _align_4h = (
        (is_above_4h is True and is_bull_sig) or
        (is_above_4h is False and (not is_bull_sig))
    )
    _counter_4h = (
        (is_above_4h is True and (not is_bull_sig)) or
        (is_above_4h is False and is_bull_sig)
    )

    # ══════════════════════════════════════════════════════════════
    # 第三步：車已發動偵測（行情已先行）
    # 邏輯：做多訊號出現但 1H 已漲 >5% → 追高風險大，限制最高 A 級
    #        做空訊號出現但 1H 已跌 >5% → 追低風險大，限制最高 A 級
    # ══════════════════════════════════════════════════════════════
    _price_15m = x.get("priceChange15m") or 0
    _price_1h  = x.get("priceChange1h")  or 0
    _already_moving = False
    _motion_note = ""
    try:
        _p15m = float(_price_15m)
        _p1h  = float(_price_1h)
        if is_bull_sig and _p15m > 5.0:
            # 15m 已漲 >5%：車已發動，硬限 A 級
            _already_moving = True
            _motion_note = f"⚠️ 車已發動：15m 已漲 {_p15m:+.1f}%，注意追高風險，行情可能進入末段"
        elif is_bull_sig and _p1h > 8.0:
            # 1H 已漲 >8%：即使 15m 未超標，1H 大漲也視為車已發動
            _already_moving = True
            _motion_note = f"⚠️ 車已發動：1H 已漲 {_p1h:+.1f}%，注意追高風險"
        elif not is_bull_sig and _p15m < -5.0:
            _already_moving = True
            _motion_note = f"⚠️ 車已發動：15m 已跌 {_p15m:+.1f}%，注意追空風險，嘎空風險偏高"
        elif not is_bull_sig and _p1h < -8.0:
            _already_moving = True
            _motion_note = f"⚠️ 車已發動：1H 已跌 {_p1h:+.1f}%，注意追空風險"
    except (TypeError, ValueError):
        pass

    # ══════════════════════════════════════════════════════════════
    # 第四步：大盤同向濾網（主流幣用 BTC，山寨優先參考 ETH）
    # 目的：大盤明顯逆風時，即使單幣訊號強，也限制最高 A 級
    # ══════════════════════════════════════════════════════════════
    _macro_block_s = False
    sym_raw = x.get("symbol") or ""
    base = sym_raw.replace("USDT", "").replace("-", "").replace("_", "").upper()
    ref_1h = None
    if base in ("BTC", "WBTC"):
        ref_1h = _btc_1h_pct
    elif base == "ETH":
        ref_1h = _eth_1h_pct if _eth_1h_pct is not None else _btc_1h_pct
    elif base in MAIN_COINS:
        ref_1h = _btc_1h_pct
    else:
        # 山寨：優先參考 ETH，其次 BTC
        ref_1h = _eth_1h_pct if _eth_1h_pct is not None else _btc_1h_pct

    if ref_1h is not None:
        try:
            _ref_1h_val = float(ref_1h)
            if is_bull_sig and _ref_1h_val < -0.5:
                _macro_block_s = True
                if _motion_note:
                    _motion_note += f"  🌐 大盤偏弱 {_ref_1h_val:+.2f}%：限制最高 A 級"
                else:
                    _motion_note = f"🌐 大盤偏弱 {_ref_1h_val:+.2f}%：限制最高 A 級"
            elif (not is_bull_sig) and _ref_1h_val > 0.5:
                _macro_block_s = True
                if _motion_note:
                    _motion_note += f"  🌐 大盤偏強 {_ref_1h_val:+.2f}%：限制最高 A 級"
                else:
                    _motion_note = f"🌐 大盤偏強 {_ref_1h_val:+.2f}%：限制最高 A 級"
        except (TypeError, ValueError):
            pass

    # ══════════════════════════════════════════════════════════════
    # 第五步：順勢訊號評分（S / A / B）
    # ══════════════════════════════════════════════════════════════
    score = 0
    reasons = []

    # ── 1. 訊號版本強度 ──────────────────────────────────────────
    version = x.get("signal_version") or "potential"
    subtype = x.get("signal_subtype") or ""
    if version == "exhaustion_reversal":
        score += 50
        reasons.append("衰竭反轉")
    elif version == "confirmed":
        score += 40
        reasons.append("三層共振")
    elif version == "tier2":
        score += 20
        reasons.append("部分共振")
    elif subtype == "pullback":
        # 提高 pullback（完美回踩）權重，系統更傾向推播回踩訊號
        score += 30
        reasons.append("完美回踩")
    else:
        score += 10
        reasons.append("潛在訊號")

    # ── 2. MTF 多框架對齊 ──────────────────────────────────────
    mtf_aligned = x.get("mtf_aligned") or 1
    if mtf_aligned >= 4:
        score += 25
        reasons.append("4框共振")
    elif mtf_aligned >= 3:
        score += 18
        reasons.append("3框共振")
    elif mtf_aligned >= 2:
        score += 10
        reasons.append("2框共振")
    else:
        score += 3

    # ── 3. 4H 宏觀天候 ────────────────────────────────────────
    if (is_above_4h is True and is_bull_sig) or (is_above_4h is False and not is_bull_sig):
        score += 15
        reasons.append("4H順勢")

    # ── 4. RSI 技術位 ────────────────────────────────────────
    rsi_v = x.get("rsi")
    if rsi_v is not None:
        try:
            rsi_v = float(rsi_v)
            if is_bull_sig:
                if 30 <= rsi_v <= 55:
                    score += 10
                    reasons.append(f"RSI{rsi_v:.0f}理想")
                elif 55 < rsi_v <= 70:
                    score += 5
                elif rsi_v > 75:
                    score -= 5
            else:
                if 45 <= rsi_v <= 70:
                    score += 10
                    reasons.append(f"RSI{rsi_v:.0f}理想")
                elif 25 <= rsi_v < 45:
                    score += 5
                elif rsi_v < 25:
                    score -= 5
        except (TypeError, ValueError):
            pass

    # ── 5. 1H OI 強度 ────────────────────────────────────────
    oi_1h = abs(x.get("oiChange1h") or 0)
    if oi_1h >= 8.0:
        score += 10
        reasons.append(f"OI{oi_1h:.1f}%強")
    elif oi_1h >= 5.0:
        score += 7
    elif oi_1h >= 3.0:
        score += 5
    else:
        score += 2

    # ── 5b. CVD 1h / Taker：雙確認加分、反向扣分 ──────────────────────────
    # 真盤中莊家常掛被動單接貨：市價賣出多 → CVD 負、Taker 賣壓高，仍可能是吸籌左側。
    _cvd_1h_chg = x.get("_cvd_1h")
    _cvd_confirmed = bool(x.get("_cvd_confirmed", False))
    _cvd_conflict_strong = bool(x.get("_cvd_conflict_strong", False))
    _taker_pct = x.get("_taker_ratio_15m")
    if _taker_pct is not None:
        try:
            _taker_pct = float(_taker_pct)
        except (TypeError, ValueError):
            _taker_pct = None
    if _cat == "long_open":
        if _cvd_confirmed:
            score += 8
            reasons.append("CVD/Taker同向")
        if _cvd_1h_chg is not None and _cvd_1h_chg < 0:
            score -= 10
            reasons.append("CVD1h負(可能限價吸籌)")
        if _taker_pct is not None and _taker_pct < 45:
            score -= 5
            reasons.append("Taker賣壓主導")
    elif _cat == "short_open":
        if _cvd_confirmed:
            score += 8
            reasons.append("CVD/Taker同向")
        if _cvd_1h_chg is not None and _cvd_1h_chg > 0:
            score -= 10
            reasons.append("CVD1h正(可能限價吸籌)")
        if _taker_pct is not None and _taker_pct > 55:
            score -= 5
            reasons.append("Taker買壓主導")
    if _cvd_conflict_strong:
        score -= 12
        reasons.append("CVD/Taker強衝突")

    # ── 6. 趨勢情境評分（核心策略：找「布局中」而非「已發動」）──────
    # 策略邏輯：
    #   做多訊號 + 24h 下跌 → 在下跌段中建多倉 = 可能買在起漲點 → 加分
    #   做多訊號 + 24h 大漲 → 在上漲末段追多 = 追高/被出貨風險 → 扣分
    #   做空訊號 + 24h 上漲 → 在上漲段中建空倉 = 可能摸頭 → 加分
    #   做空訊號 + 24h 大跌 → 在下跌末段追空 = 追低/嘎空風險 → 扣分
    cat = x.get("category", "")
    p24h = x.get("priceChange24h") or 0
    try:
        p24h = float(p24h)
        if cat == "long_open":
            if p24h < -3.0:
                score += 15
                reasons.append("下跌段布局")   # 下跌段建多 = 買底
            elif p24h > 10.0:
                score -= 10                     # 大漲後建多 = 追高
        elif cat in ("short_cover", "short_close"):
            # 空方回補 = 看多，在下跌段出現更好
            if p24h < -3.0:
                score += 10
                reasons.append("跌後軋空")
        elif cat == "short_open":
            if p24h > 3.0:
                score += 15
                reasons.append("上漲段摸頭")   # 上漲段建空 = 摸頭
            elif p24h < -10.0:
                score -= 10                     # 大跌後建空 = 追低
        elif cat == "long_close":
            # 多方平倉 = 看空，在上漲段出現更好（出貨）
            if p24h > 3.0:
                score += 10
                reasons.append("漲後出貨")
    except (TypeError, ValueError):
        pass

    # ── 7. 資金費率順風加分 ──────────────────────────────────────
    # 費率為小數（0.001 = 0.1%）
    # 做多 + 費率偏負（空頭支付費率給多頭）→ 順風，空頭持續補倉壓力
    # 做空 + 費率偏正（多頭支付費率給空頭）→ 順風，多頭持續爆倉壓力
    # 注意：費率嚴重反向已在 enrichment 層被封鎖/降級，此處只計算「費率助力」
    _fr = x.get("funding_rate")
    if _fr is not None and isinstance(_fr, (int, float)):
        if is_bull_sig and _fr < -FR_SHORT_SQUEEZE_RISK:          # 做多 + 費率偏負
            if _fr < -FR_SHORT_SQUEEZE_BLOCK:                     # < -0.3%：極度順風
                score += 10
                reasons.append(f"費率極偏負({_fr*100:+.2f}%)")
            else:                                                  # -0.1% ~ -0.3%：順風
                score += 5
                reasons.append(f"費率偏負({_fr*100:+.2f}%)")
        elif not is_bull_sig and _fr > FR_LONG_LIQUIDATION_RISK:  # 做空 + 費率偏正
            if _fr > FR_LONG_LIQUIDATION_BLOCK:                   # > +0.5%：極度順風
                score += 10
                reasons.append(f"費率極偏正({_fr*100:+.2f}%)")
            else:                                                  # +0.2% ~ +0.5%：順風
                score += 5
                reasons.append(f"費率偏正({_fr*100:+.2f}%)")

    # ── 8. 跨類別互確認獎勵 ──────────────────────────────────────
    # 同輪推播中，如果互補訊號都出現，代表兩股力量同時確認同一方向
    # long_open + short_close = 多方建倉 + 空方回補 = 雙重做多確認（完美多單）
    # long_close + short_open = 多方出貨 + 空方建倉 = 雙重做空確認（完美空單）
    if x.get("_cross_confirm"):
        score += 20
        reasons.append("雙向互確認")

    # ── 9. 聰明錢真實建倉加分 ─────────────────────────────────────
    # 僅在 smart_money=True 時加分；None（無資料）保持中性
    if _smart_money is True:
        score += 15
        reasons.append("🧠聰明錢真實建倉")

    # ── 10. 籌碼三步驟陷阱偵測加分（short_open 摸頭 / long_open 摸底）───
    # 完整三步驟吻合 → +25 分（直接衝 S 級）
    # 部分吻合（2 步）→ +12 分（訊號有結構支撐）
    _trap_detected = x.get("_bull_trap_detected", False)
    _trap_steps    = x.get("_bull_trap_steps", 0)
    if _trap_detected:
        score += 25
        reasons.append("籌碼三步驟完整形態")
    elif _trap_steps >= 2:
        score += 12
        reasons.append(f"籌碼陷阱跡象({_trap_steps}/3步)")

    # ── 11. 大盤 (BTC) 敬畏：與訊號方向逆風時強制扣分 ─────────────────
    try:
        _btc_1h_pen = float(_btc_1h_pct) if _btc_1h_pct is not None else None
    except (TypeError, ValueError):
        _btc_1h_pen = None
    if _btc_1h_pen is not None:
        if is_bull_sig and _btc_1h_pen < 0:
            score -= 10
            reasons.append(f"BTC1H弱勢({_btc_1h_pen:+.2f}%)")
        elif (not is_bull_sig) and _btc_1h_pen > 0:
            score -= 10
            reasons.append(f"BTC1H強勢({_btc_1h_pen:+.2f}%)")

    # ── 評級（S / A / R / B；車已發動或大盤逆風 → 上限 A）────────────
    score = max(0, min(100, score))
    if _already_moving or _macro_block_s:
        score = min(score, 74)   # 車已發動或大盤明顯逆風：硬上限 74 分 = 最高 A 級

    if score < MIN_SIGNAL_PUSH_SCORE:
        grade = "B"
        grade_badge = "🥈 *B 級*"
        grade_desc = f"訊號不足（<{MIN_SIGNAL_PUSH_SCORE}分不推播）"
        brief = f"{grade_badge} {grade_desc}（{'・'.join(reasons[:3])}）"
        return grade, score, brief, _already_moving, _motion_note

    # score ≥ MIN：依 4H 順逆分流 S/A/R
    _dir_label = "摸頭・逆勢做空" if not is_bull_sig else "摸底・逆勢做多"
    if _counter_4h:
        grade = "R"
        brief = (
            f"⚡ *R 級* 逆勢左側（{_dir_label}，嚴控倉位）"
            f"（{'・'.join(reasons[:3])}）"
        )
        return grade, score, brief, _already_moving, _motion_note

    # 4H 無法確認：不給 S/A/R，一律不推播（減少雜訊）
    if is_above_4h is None:
        grade = "B"
        grade_badge = "🥈 *B 級*"
        grade_desc = "4H天候未確認（不推播）"
        brief = f"{grade_badge} {grade_desc}（{'・'.join(reasons[:3])}）"
        return grade, score, brief, _already_moving, _motion_note

    if _align_4h and score >= 80:
        grade = "S"
        grade_badge = "🏆 *S 級*"
        grade_desc = "訊號極強・順勢"
    elif _align_4h:
        grade = "A"
        grade_badge = "🥇 *A 級*"
        grade_desc = "訊號強"
    else:
        grade = "B"
        grade_badge = "🥈 *B 級*"
        grade_desc = "狀態異常（不推播）"
        brief = f"{grade_badge} {grade_desc}（{'・'.join(reasons[:3])}）"
        return grade, score, brief, _already_moving, _motion_note

    brief = f"{grade_badge} {grade_desc}（{'・'.join(reasons[:3])}）"
    return grade, score, brief, _already_moving, _motion_note


def build_report_message_tiered(
    enriched_items: List[Dict],
    processed_count: int = 0,
    oi_success_count: int = 0,
    *,
    sa_conflict_history: Optional[List[Dict]] = None,
    sa_conflict_max_age_sec: float = 0.0,
    pipeline_now_ts: float = 0.0,
) -> tuple:
    """
    【傑克 1H MTF 四層漏斗訊號推播】
    確定籌碼（四層共振）+ 潛在機會（順勢回踩/逆勢摸頂底）雙版本。
    技術指標基準：1H K 線（中期波段視角）。

    sa_conflict_history：冷卻檔 history（含 grade），用於阻擋「先 S/A 順勢後又反向 R」。
    """
    def fmt_pct(num):
        if num is None or (isinstance(num, float) and (num != num)):
            return "0.00%"
        return f"{'+' if num >= 0 else ''}{num:.2f}%"

    def calc_sl_tp(
        price: float,
        is_long: bool,
        atr: Optional[float] = None,
        recent_high_2h: Optional[float] = None,
        recent_low_2h: Optional[float] = None,
        signal_type: str = "trend",
        pre_breakout_low: Optional[float] = None,
        pre_breakout_high: Optional[float] = None,
        ema20: Optional[float] = None,
        rsi: Optional[float] = None,
        ema20_touch_low: Optional[float] = None,
        ema20_touch_high: Optional[float] = None,
        vwap_2h: Optional[float] = None,
    ):
        """
        與檔首 compute_structural_sl_tp 一致：2H 高低 + EMA20 + VWAP 結構防守、MIN_SL_PERCENT 保底、TP 倍率映射。
        舊參數（ATR/軋空/回踩）保留簽名以相容，計算已不再使用。
        """
        if not price or price <= 0:
            return None, None, None, None, None, "—", "normal", TP1_R_MULTIPLIER, TP2_R_MULTIPLIER
        sl, tp1, tp2, _one_r, sl_pct = compute_structural_sl_tp(
            float(price), is_long, vwap_2h, ema20, recent_low_2h, recent_high_2h
        )
        if sl is None:
            return None, None, None, None, None, "—", "normal", TP1_R_MULTIPLIER, TP2_R_MULTIPLIER
        warn_pct = sl_pct if sl_pct > 8.0 else None
        rsi_val_f = float(rsi) if rsi and isinstance(rsi, (int, float)) else None
        if (signal_type or "") == "squeeze":
            tp_mode = "squeeze"
        elif rsi_val_f is not None and ((is_long and rsi_val_f >= 75) or (not is_long and rsi_val_f <= 25)):
            tp_mode = "rsi_hot"
        else:
            tp_mode = "normal"
        return sl, tp1, tp2, sl_pct, warn_pct, "結構防守(2H/EMA/VWAP)", tp_mode, TP1_R_MULTIPLIER, TP2_R_MULTIPLIER

    def _is_bull(x: Dict) -> bool:
        cat = x.get("category", "")
        return cat in ("long_open", "short_close")

    def _pass_rsi_filter(x: Dict, z: str) -> bool:
        return True  # 新版不過濾 RSI（OI+Price 絕對條件已足夠）

    # 頭等艙 ✈️ = 摸頭/抄底 + 5星 + |OI|>=OI_FOR_ELITE + 成交量≥1M + 至少一項訂單流數據 + RSI 輔助
    # 鯨魚指數不強制（山寨幣普遍無覆蓋）；成交量門檻放寬（山寨幣量級較低）
    def _is_elite(x: Dict) -> bool:
        return (x.get("stars") or 0) >= 5  # 新版：所有通過條件的訊號皆為5星
    # ── 以下為新版渲染邏輯起始點 ──────────────────────────────────────────────

    def _fmt_price(p: Optional[float]) -> str:
        if p is None or (isinstance(p, float) and p != p) or p <= 0:
            return "—"
        # 極小價格（如 meme 幣 7.18e-9）：動態計算需要幾位才能顯示 4 位有效數字
        if p < 0.0001:
            import math
            sig_dec = max(8, -int(math.floor(math.log10(abs(p)))) + 3)
            return f"{p:.{sig_dec}f}".rstrip('0').rstrip('.')
        if p < 0.01:
            return f"{p:.6f}"
        if p < 1:
            return f"{p:.5f}"
        if p < 10:
            return f"{p:.4f}"
        return f"{p:.2f}"

    def _action_label(zone: str, is_bull_flag: bool) -> str:
        if zone == ZONE_TOP:
            return "摸頭做空"
        if zone == ZONE_DIP:
            return "抄底做多"
        if zone == ZONE_BREAKOUT_LONG:
            return "追多"
        if zone == ZONE_BREAKOUT_SHORT:
            return "追空"
        return "做多" if is_bull_flag else "做空"

    def _reason_plain(reason: str) -> str:
        return (reason or "籌碼有異動").strip()
        # ── 以下舊版邏輯已棄用（新版 reason 已直接在 _classify_signal_and_tier 產生） ──
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

    # ══════════════════════════════════════════════════
    # 新版渲染邏輯：30m 四象限極簡格式
    # ══════════════════════════════════════════════════

    # ── 小白友善標題對應（直球做多/做空指令）────────────────────────────
    _signal_title = {
        "long_open":   "🟢 【強勢做多 Long】",
        "short_close": "🟢 【報復反彈 (做多)】",
        "short_open":  "🔴 【順勢做空 Short】",
        "long_close":  "🔴 【恐慌崩跌 (做空)】",
    }
    # ── 白話文進場邏輯（一句話秒懂）────────────────────────────────────
    _signal_reason = {
        "long_open":   "莊家正投入真金白銀買進，且大趨勢偏多，順勢跟上！",
        "short_close": "做空的人正在被嘎空強制平倉，引發燃料上漲，搶短多！",
        "short_open":  "大戶正在大舉建倉做空，且大趨勢偏空，順勢看跌！",
        "long_close":  "做多的人正在恐慌拋售踩踏，引發連鎖跌勢，搶短空！",
    }

    now_str = datetime.now(TAIPEI_TZ).strftime("%m/%d %H:%M")
    messages_out: List[str] = []
    grade_per_msg: List[str] = []   # 與 messages_out 同步，記錄每則訊號的評級
    s_grade_msgs: List[str] = []    # S 級訊號獨立收集，供額外推播
    cards_payload: List[Dict[str, Any]] = []  # 每檔一張圖所需資料
    push_count = 0
    has_any = False
    seen_syms: set = set()

    # ── 跨類別互確認預計算 ────────────────────────────────────────────────
    # 策略核心：兩股力量同時確認同一方向 = 最強訊號
    #   多單完美組合：long_open（主力建多）＋ short_close（空方被迫回補）= 雙向推升
    #   空單完美組合：long_close（主力出貨）＋ short_open（空方主動建倉）= 雙向壓制
    _cats_in_batch = {x.get("category") for x in enriched_items}
    _bull_cross = "long_open"  in _cats_in_batch and "short_close" in _cats_in_batch
    _bear_cross = "long_close" in _cats_in_batch and "short_open"  in _cats_in_batch
    for _xi in enriched_items:
        _xi_cat = _xi.get("category", "")
        if _bull_cross and _xi_cat in ("long_open", "short_close"):
            _xi["_cross_confirm"] = True
        elif _bear_cross and _xi_cat in ("long_close", "short_open"):
            _xi["_cross_confirm"] = True
        else:
            _xi["_cross_confirm"] = False

    # 敘事防火牆：近 N 秒內曾對該幣推過 S/A「順勢」的方向集合（與冷卻檔 grade 欄配合）
    def _hist_sym_norm(_s: str) -> str:
        if not _s:
            return ""
        return str(_s).replace("USDT", "").replace("-", "").replace("_", "").strip().upper()

    sa_hist_dirs: Dict[str, Set[str]] = {}
    if sa_conflict_history and sa_conflict_max_age_sec > 0 and pipeline_now_ts > 0:
        for _he in sa_conflict_history:
            if not isinstance(_he, dict):
                continue
            _ts_e = float(_he.get("ts") or 0)
            if pipeline_now_ts - _ts_e > sa_conflict_max_age_sec:
                continue
            _g_e = str(_he.get("grade") or "")
            if _g_e not in ("S", "A"):
                continue
            _sn_e = _hist_sym_norm(str(_he.get("symbol") or ""))
            _dr_e = str(_he.get("dir") or "")
            if not _sn_e or _dr_e not in ("多", "空"):
                continue
            sa_hist_dirs.setdefault(_sn_e, set()).add(_dr_e)

    trend_sa_dirs_this_run: Dict[str, Set[str]] = {}

    for x in enriched_items:
        sym = x.get("symbol", "")
        if not sym or sym in seen_syms:
            continue
        seen_syms.add(sym)
        category = x.get("category", "")
        _sig_ver = x.get("signal_version") or "potential"
        if _sig_ver == "exhaustion_reversal":
            _ex_dir = x.get("_exhaustion_reversal_direction")
            if _ex_dir == "long":
                is_bull_sig = True
                title = "🟢 【恐慌衰竭 (抄底做多)】"
            else:
                is_bull_sig = False
                title = "🔴 【狂熱衰竭 (摸頭做空)】"
        else:
            title = _signal_title.get(category)
            is_bull_sig = category in ("long_open", "short_close")
        if not title:
            continue

        sym_base = sym.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
        price = x.get("current_price")
        if not price or not isinstance(price, (int, float)) or price <= 0:
            continue

        # 提早評級：B 級不推播，直接跳過（節省 entry/SL/TP 計算與訊息組裝）
        _grade, _grade_score, _grade_brief, _already_moving, _motion_note = _calc_signal_grade(x, is_bull_sig)
        try:
            x["score"] = int(round(float(_grade_score)))
        except (TypeError, ValueError):
            x["score"] = 0
        if _grade == "B":
            continue

        # 曾推 S/A 順勢「空」後，時窗內不再推 R「多」（摸底）；反之亦然（摸頭 vs 順勢多）
        if _grade == "R":
            _cur_d = "多" if is_bull_sig else "空"
            _opp = "空" if _cur_d == "多" else "多"
            _hist_blk = _opp in sa_hist_dirs.get(sym_base, set())
            _batch_blk = _opp in trend_sa_dirs_this_run.get(sym_base, set())
            if _hist_blk or _batch_blk:
                logger.info(
                    f"[敘事防火牆] {sym_base}: 略過 R（{_cur_d}）— 已有 S/A 順勢{_opp}"
                    f"（{'歷史' if _hist_blk else ''}{'+' if _hist_blk and _batch_blk else ''}{'本輪' if _batch_blk else ''}）"
                )
                continue

        oi30 = x.get("oiChange30m")
        p30 = x.get("priceChange30m")
        p1h = x.get("priceChange1h")
        vol_usd = x.get("volume_usd") or x.get("_cg_volume_usd") or 0
        funding_rate = x.get("funding_rate")
        atr_val = x.get("atr")
        taker_ratio_15m = x.get("_taker_ratio_15m")  # 主動買盤%（coins-markets top-100 才有）
        rsi_val = x.get("rsi")
        detected_ts = x.get("_detected_ts")
        vwap_2h_val = x.get("vwap_2h")
        _now_ts = time.time()

        # ══════════════════════════════════════════════════════════
        # 進場價邏輯：現價優於主力均價 → 市價進場；否則 → 計畫委託掛單（主力均價）
        # 動能透支時：強制限價掛單於 EMA20，拒絕市價進場
        # TP/SL：結構防守位（2H 高/低 + EMA20 + VWAP）+ MIN_SL_PERCENT 保底 → 1R → TP1/TP2
        # ══════════════════════════════════════════════════════════
        sl, tp1, tp2 = None, None, None
        _r1, _r2 = TP1_R_MULTIPLIER, TP2_R_MULTIPLIER
        sl_pct_val = None
        _entry_price = price
        _entry_mode = "市價"  # 市價進場 or 掛單進場
        _energy_exhausted = x.get("_energy_exhausted", False)
        ema20_val = x.get("ema20") or x.get("ema20_close")
        _is_exhaustion_reversal = (_sig_ver == "exhaustion_reversal")
        # 與 enrichment 的 derive_limit_order_from_inputs 一致：已寫入則優先採用（衰竭反轉／動能透支／純 VWAP 掛單）
        if x.get("is_limit_order") and x.get("limit_price") is not None:
            _entry_price = float(x["limit_price"])
            if _is_exhaustion_reversal:
                _entry_mode = "掛單（限價於 VWAP/EMA20）"
            elif _energy_exhausted:
                _entry_mode = "掛單（限價於 EMA20）"
            else:
                _entry_mode = "掛單"
        elif _is_exhaustion_reversal:
            # 衰竭反轉：強制限價掛單於 VWAP/EMA20（有 15m EMA20 則優先，此處以 VWAP/1H EMA20 為主）
            _entry_price = price
            if vwap_2h_val and isinstance(vwap_2h_val, (int, float)) and vwap_2h_val > 0:
                _entry_price = float(vwap_2h_val)
            elif ema20_val and isinstance(ema20_val, (int, float)) and ema20_val > 0:
                _entry_price = float(ema20_val)
            _entry_mode = "掛單（限價於 VWAP/EMA20）"
        elif _energy_exhausted and ema20_val and isinstance(ema20_val, (int, float)) and ema20_val > 0:
            # 動能透支/乖離過大：強制限價掛單於 EMA20，拒絕市價進場
            _entry_price = float(ema20_val)
            _entry_mode = "掛單（限價於 EMA20）"
        elif vwap_2h_val and isinstance(vwap_2h_val, (int, float)) and vwap_2h_val > 0:
            _vwap_f = float(vwap_2h_val)
            # 做多：現價 ≤ 主力均價×102.5% = 在範圍內 → 市價進場
            # 做空：現價 ≥ 主力均價×97.5%  = 在範圍內 → 市價進場
            if is_bull_sig and price <= _vwap_f * 1.025:
                _entry_price = price
                _entry_mode = "市價"
            elif not is_bull_sig and price >= _vwap_f * 0.975:
                _entry_price = price
                _entry_mode = "市價"
            else:
                # 掛單：主力均價 × 97.5%（等價格進入範圍）
                _entry_price = _vwap_f * 0.975
                _entry_mode = "掛單"

        if _entry_price and _entry_price > 0:
            _recent_lo = x.get("recent_low_2h")
            _recent_hi = x.get("recent_high_2h")
            sl, tp1, tp2, _one_r_u, sl_pct_val = compute_structural_sl_tp(
                float(_entry_price),
                is_bull_sig,
                vwap_2h_val,
                ema20_val,
                _recent_lo,
                _recent_hi,
            )
            if sl is None or tp1 is None:
                logger.warning(f"[SL/TP] {sym_base} 結構計算失敗，跳過此訊號")
                continue
            logger.info(
                f"[SL/TP結構計算] 幣種: {sym_base}, 方向: {'多' if is_bull_sig else '空'}, "
                f"進場: {_entry_price}, 結構SL: {sl} (距離 {sl_pct_val:.2f}%), TP1: {tp1}"
            )

        # ══════════════════════════════════════════════════════════
        # 訊號版本 / 標籤 / 策略短評
        # ══════════════════════════════════════════════════════════
        _sig_version   = x.get("signal_version") or "potential"
        _sig_subtype   = x.get("signal_subtype") or ""
        _mtf_desc      = x.get("mtf_desc") or ""
        _reversal_hint = x.get("reversal_hint") or ""

        # 標題標籤（exhaustion_reversal / confirmed / pullback / tier2）
        _dir_str   = "做多" if is_bull_sig else "做空"
        _dir_emoji = "🟢"   if is_bull_sig else "🔴"
        if _sig_version == "exhaustion_reversal":
            _type_str  = "衰竭反轉・抄底" if is_bull_sig else "衰竭反轉・摸頭"
            _badge_emo = "🎯"
            _ver_label = "🔥 *衰竭反轉*（動能衰竭後二次確認，限價掛單）"
            sig_emoji  = "🔥"
        elif _sig_version == "confirmed":
            _type_str  = "確定籌碼・右側突破"
            _badge_emo = "🚀"
            _ver_label = "✅ *確定籌碼*（鐵三角共振 1H/30m/15m 一致）"
            sig_emoji  = "💎"
        elif _sig_version == "tier2":
            _t2_sub    = _sig_subtype or "弱共振"
            _type_str  = f"觀察名單・{_t2_sub}"
            _badge_emo = "⚠️"
            _ver_label = f"⚠️ *觀察名單*（{_t2_sub}，建議輕倉）"
            sig_emoji  = "👀"
        else:  # pullback
            _type_str  = "潛在機會・牛回頭低接" if is_bull_sig else "潛在機會・熊反彈做空"
            _badge_emo = "🧲"
            _ver_label = "🎯 *潛在機會*（完美回踩）"
            sig_emoji  = "🏎️"

        x["_sig_emoji"] = sig_emoji  # 供 header 彙總列用

        # ── 策略短評（自動生成）───────────────────────────────────────
        def _gen_comment(cat: str, ver: str, sub: str, hint: str, rsi_v) -> str:
            # short_close（原始值）與 short_cover（內部標準化值）語意相同：OI↓+Price↑ = 空方回補
            _is_bull_cat = cat in ("long_open", "short_cover", "short_close")
            _is_bear_cat = cat in ("short_open", "long_close")
            if ver == "exhaustion_reversal":
                return hint if hint else ("空方動能衰竭，出現獲利回補跡象，抄底做多。" if is_bull_sig else "多方動能衰竭，出現獲利回補跡象，摸頭做空。")
            if ver == "confirmed":
                if cat == "long_open":                    return "主力三層共振建多倉，動能明確，右側追多機會！"
                if cat == "short_open":                   return "主力三層共振建空倉，空頭動能確認，右側追空機會！"
                if cat in ("short_cover", "short_close"): return "空方三層共振回補，軋空燃料充足，右側做多機會！"
                return "多方三層共振平倉，看空動能聚積，右側做空機會！"
            if sub == "pullback":
                if _is_bull_cat:
                    return "大時框多頭趨勢確立，小週期短暫回調，是低接進場的黃金時機。"
                return "大時框空頭趨勢確立，小週期短暫反彈，是逢高做空的黃金時機。"
            if ver == "tier2":
                if sub == "RSI極端":
                    rsi_str = f"RSI={rsi_v:.0f}" if rsi_v else "RSI偏高"
                    if _is_bull_cat:
                        return f"鐵三角成立但 {rsi_str} 已偏熱，單邊行情可輕倉，嚴控止損。"
                    return f"鐵三角成立但 {rsi_str} 已偏冷，反彈可輕倉，嚴控止損。"
                return "籌碼方向確認中，嚴守止損。"
            return "籌碼方向確認中，嚴守止損。"

        _strategy_comment = _gen_comment(category, _sig_version, _sig_subtype, _reversal_hint, rsi_val)

        # ── 4H 宏觀天候 ────────────────────────────────────────────────
        _ema20_4h_val    = x.get("ema20_4h")
        _rsi_4h_val      = x.get("rsi_4h")
        _is_above_4h_ema = x.get("is_above_4h_ema")
        if _is_above_4h_ema is True:
            _macro_trend   = "順勢" if is_bull_sig else "逆勢"
            _macro_ema_txt = "站上 4H EMA20"
        elif _is_above_4h_ema is False:
            _macro_trend   = "逆勢" if is_bull_sig else "順勢"
            _macro_ema_txt = "跌破 4H EMA20"
        else:
            _macro_trend   = "—"
            _macro_ema_txt = "4H EMA20 無數據"
        _rsi_4h_str  = f" · RSI {_rsi_4h_val:.0f}" if _rsi_4h_val is not None else ""
        _macro_line  = f"🌍 *4H天候：* {_macro_trend} · {_macro_ema_txt}{_rsi_4h_str}"

        # ── 資金費率（含多空壅擠判讀）────────────────────────────────────────
        # 費率偏負 = 空頭支付費率（空頭壅擠）→ 做多是順風，做空風險高（嘎空）
        # 費率偏正 = 多頭支付費率（多頭壅擠）→ 做空是順風，做多風險高（爆倉）
        if funding_rate is not None and isinstance(funding_rate, (int, float)):
            _fr_val = funding_rate * 100
            # 中性區間
            if abs(funding_rate) <= 0.0001:
                _fr_desc = "中性"
            # 嚴重壅擠（已被 FR 封鎖門不應到達這裡，但顯示層仍需標註）
            elif funding_rate <= -FR_SHORT_SQUEEZE_BLOCK:
                _fr_desc = "🔥 空頭嚴重壅擠，嘎空風險極高！" if is_bull_sig else "🚨 空頭嚴重壅擠，切勿追空！"
            elif funding_rate >= FR_LONG_LIQUIDATION_BLOCK:
                _fr_desc = "🚨 多頭嚴重壅擠，切勿追多！" if is_bull_sig else "🔥 多頭嚴重壅擠，空方順風！"
            # 壅擠警戒（降級訊號可能出現在此）
            elif funding_rate < -FR_SHORT_SQUEEZE_RISK:
                _fr_desc = "⚠️ 空頭壅擠，做多順風" if is_bull_sig else "⚠️ 空頭壅擠，嘎空風險偏高"
            elif funding_rate > FR_LONG_LIQUIDATION_RISK:
                _fr_desc = "⚠️ 多頭壅擠，做空順風" if not is_bull_sig else "⚠️ 多頭壅擠，爆倉風險偏高"
            # 輕微偏向
            elif funding_rate < -0.0005:
                _fr_desc = "略偏空（做多略有費率加成）" if is_bull_sig else "略偏空（空頭稍多）"
            elif funding_rate > 0.0005:
                _fr_desc = "略偏多（做空略有費率加成）" if not is_bull_sig else "略偏多（多頭稍多）"
            elif funding_rate > 0:
                _fr_desc = "略偏多"
            else:
                _fr_desc = "略偏空"
            # 自動去尾零：0.0100% → 0.01%，-0.0500% → -0.05%
            _fr_str = f"{_fr_val:+.4f}".rstrip('0').rstrip('.')
            _fr_line = f"💸 *費率：* `{_fr_str}%` {_fr_desc}"
        else:
            _fr_line = "💸 *費率：* 無數據"

        # ── 成交值 ────────────────────────────────────────────────────
        vol_m_val    = float(vol_usd) / 1e6 if vol_usd and float(vol_usd) > 0 else 0.0
        _vol_src_tag = x.get("_vol_source", "CoinGlass")
        _src_note    = f" _{_vol_src_tag}_" if _vol_src_tag not in ("CoinGlass", "") else ""
        if vol_m_val >= 50:
            _vol_line = f"📊 成交值 `{vol_m_val:.0f}M` ✅ 機構級"
        elif vol_m_val >= 20:
            _vol_line = f"📊 成交值 `{vol_m_val:.0f}M` ✅ 深度充足"
        elif vol_m_val >= 5:
            _vol_line = f"📊 成交值 `{vol_m_val:.1f}M`{_src_note} ⚠️ 流動性偏低"
        elif vol_m_val > 0:
            _vol_line = f"📊 成交值 `{vol_m_val:.1f}M`{_src_note} ⚠️ 極低流動性"
        else:
            _vol_line = ""  # 無成交值資料時不顯示此行，避免誤導

        # ══════════════════════════════════════════════════════════
        # 組裝電報訊息（新手友善：先「怎麼做」再「為什麼」）
        # ══════════════════════════════════════════════════════════
        msg_lines: List[str] = []

        def _rel_dev_pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
            """|a-b|/b，用於現價 vs 掛單／主力均價。"""
            try:
                if a is None or b is None:
                    return None
                af, bf = float(a), float(b)
                if bf <= 0:
                    return None
                return abs(af - bf) / bf
            except (TypeError, ValueError):
                return None

        def _limit_dev_hint(pct: Optional[float]) -> str:
            if pct is None:
                return ""
            if pct <= 0.02:
                return " ✅偏離小（好接）"
            if pct <= 0.05:
                return " ⚠️略偏（確認價格再下）"
            return " ⚠️偏離較大"

        # ─ 標題行 ─
        _copy_sym = sym if sym.endswith("USDT") else f"{sym_base}USDT"
        try:
            _score = int(round(float(x.get("score", 0))))
        except (TypeError, ValueError):
            _score = 0
        msg_lines.append(f"{_dir_emoji} *{_dir_str}* `{sym_base}` ({_score}分) {_badge_emo}")
        msg_lines.append(_grade_brief)
        # 冷卻視窗內「反向 S 信號」：允許推播，但提醒使用者時間線上發生過方向切換
        if x.get("cooldown_reverse_recent") and _grade == "S":
            msg_lines.append("🧠 冷卻期間反向出現 S：已允許推播（注意敘事切換）")

        # 主力均價（2h VWAP，與結構 SL/掛單邏輯一致）
        try:
            _vwap_show = (
                float(vwap_2h_val)
                if vwap_2h_val is not None
                and isinstance(vwap_2h_val, (int, float))
                and float(vwap_2h_val) > 0
                else None
            )
        except (TypeError, ValueError):
            _vwap_show = None

        _entry_now_txt = _fmt_price(price) if price is not None else "N/A"
        _entry_plan_txt = _fmt_price(_entry_price) if _entry_price is not None else "N/A"
        _sl_txt = _fmt_price(sl) if sl is not None else "N/A"
        _tp1_txt = _fmt_price(tp1) if tp1 is not None else "N/A"
        _tp2_txt = _fmt_price(tp2) if tp2 is not None else None
        _exec_mode = "限價" if _entry_mode != "市價" else "市價"
        _energy_exh = bool(x.get("_energy_exhausted"))
        _exec_note = "（EMA20 限價、不追市價）" if _energy_exh else ""

        msg_lines.append("*📌 怎麼跟單*")
        if _entry_mode == "市價":
            _vw_dev = _rel_dev_pct(float(price) if price is not None else None, _vwap_show)
            _vw_dev_s = f"｜與主力均價差 `{_vw_dev:.1%}`" if _vw_dev is not None else ""
            msg_lines.append(f"• 進場：市價 ≈ `{_entry_now_txt}`{_vw_dev_s}")
        else:
            _p_now = float(price) if price is not None else None
            _p_plan = float(_entry_price) if _entry_price is not None else None
            _lim_pct = _rel_dev_pct(_p_now, _p_plan)
            _lim_pct_s = f"{_lim_pct:.1%}" if _lim_pct is not None else "—"
            _hint = _limit_dev_hint(_lim_pct)
            msg_lines.append(
                f"• 進場：限價掛單價 `{_entry_plan_txt}`｜現價 `{_entry_now_txt}`｜偏離 `{_lim_pct_s}`{_hint}"
            )
            msg_lines.append("  （限價＝等成交；勿用市價追價）")
        msg_lines.append(f"• 止損：`{_sl_txt}`（到價認錯出場）")
        msg_lines.append(f"• 目標：TP1 `{_tp1_txt}`" + (f" → TP2 `{_tp2_txt}`" if _tp2_txt else ""))

        msg_lines.append("*🌍 環境與籌碼*")
        if _vwap_show is not None:
            msg_lines.append(f"• 主力均價（2h VWAP）：`{_fmt_price(_vwap_show)}`")
        msg_lines.append(f"• 4H：{_macro_trend}｜下單方式：{_exec_mode}{_exec_note}")

        msg_lines.append("*💡 策略說明*")
        msg_lines.append(_strategy_comment)

        msg_lines.append("*📎 附圖怎麼看*")
        msg_lines.append("上排＝最近 60 根 5 分鐘K；紫線＝EMA20；淺藍線＝VWAP（主力均價）")
        msg_lines.append("下排＝全網 OI 量柱（藍＝增／橘＝減，看籌碼是否在動）")

        # ─ 風險與環境（有觸發才顯示）─
        if _motion_note:
            msg_lines.append(f"⚠️ {_motion_note}")
        if funding_rate is not None and isinstance(funding_rate, (int, float)):
            _fr_val = funding_rate * 100
            _fr_str = f"{_fr_val:+.4f}".rstrip("0").rstrip(".")
            msg_lines.append(f"費率 `{_fr_str}%`")
        if _vol_line:
            msg_lines.append(_vol_line)

        # 雙核 AI 風控：機讀資料包（供外層審查服務解析，勿刪行首 [AI_DATA] 標記）
        _fr_ai = (
            round(float(funding_rate) * 100, 4)
            if funding_rate is not None and isinstance(funding_rate, (int, float))
            else 0
        )
        _rsi_ai = float(rsi_val) if rsi_val is not None and isinstance(rsi_val, (int, float)) else 50.0
        ai_data = {
            "sym": sym_base,
            "dir": "long" if is_bull_sig else "short",
            "ep": _entry_price,
            "sl": sl,
            "tp1": tp1,
            "btc_1h": _btc_1h_pct if _btc_1h_pct is not None else 0,
            "fr": _fr_ai,
            "rsi": _rsi_ai,
        }
        msg_lines.append(f"\n`[AI_DATA] {json.dumps(ai_data, ensure_ascii=False)}`")

        # ─ 儲存供後續使用 ─
        x["sl_price_str"]    = _fmt_price(sl)
        x["tp1_price_str"]   = _fmt_price(tp1)
        x["tp2_price_str"]   = _fmt_price(tp2)
        x["r_tp1"]           = _r1
        x["r_tp2"]           = _r2
        x["sl_source"]       = (
            f"結構防守 min/max(2H,EMA20,VWAP)+保底{MIN_SL_PERCENT*100:.1f}% "
            f"(TP1={TP1_R_MULTIPLIER}R)"
        )
        x["selected_for_push"] = True
        x["_push_grade"] = _grade
        x["tier"]            = "train"
        x["stars"]           = 5
        x["dir"]             = "多" if is_bull_sig else "空"
        if _grade in ("S", "A"):
            trend_sa_dirs_this_run.setdefault(sym_base, set()).add(x["dir"])

        _msg_str = "\n".join(msg_lines)
        messages_out.append(_msg_str)
        # K 線卡片：caption 用原推播訊息文字不變；圖上用同一套 SL/TP/進場/主力均價位
        try:
            _entry_val = float(price) if _entry_mode == "市價" else float(_entry_price)
        except Exception:
            _entry_val = None
        try:
            _vwap_val = float(vwap_2h_val) if vwap_2h_val is not None else None
        except Exception:
            _vwap_val = None
        cards_payload.append(
            {
                "symbol_base": sym_base,
                "caption": _msg_str,
                "direction_is_long": is_bull_sig,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "entry": _entry_val,
                "vwap": _vwap_val,
                "ema20": x.get("ema20"),
                "ema20_touch_low": x.get("ema20_touch_low"),
                "ema20_touch_high": x.get("ema20_touch_high"),
                "ema20_4h": x.get("ema20_4h"),
            }
        )
        grade_per_msg.append(_grade)
        push_count += 1
        has_any = True
        logger.info(
            f"[推播] {sym_base} {title} 現價=${_fmt_price(price)} "
            f"SL=${_fmt_price(sl)} TP1=${_fmt_price(tp1)} TP2=${_fmt_price(tp2)}"
        )

    if not has_any:
        no_sig_msg = (
            f"🔍 *傑克持倉異常狙擊鏡* 本輪無訊號\n"
            f"🕐 {now_str}  條件：1H OI≥動態門檻(主流4%/高流動6%/小幣8%) & 量≥{MTF_VOLUME_MIN_USD/1e6:.0f}M & MTF共振\n"
            f"繼續監控中..."
        )
        return no_sig_msg, False, 0, [], []

    # 收集各訊號的 emoji，組成 header 彙總列
    pushed_items = [
        x for x in enriched_items
        if x.get("selected_for_push") and x.get("symbol") in seen_syms
    ]
    emoji_summary = " ".join(x.get("_sig_emoji", "🏎️") for x in pushed_items)

    # 多單相關性警示：同輪 ≥3 個同向訊號
    bull_count = sum(1 for x in pushed_items if x.get("category") in ("long_open", "short_close"))
    bear_count = sum(1 for x in pushed_items if x.get("category") in ("short_open", "long_close"))
    correlation_warn = ""
    if bull_count >= 3:
        correlation_warn = (
            f"\n{'─' * 20}\n"
            f"⚠️ *相關性警示：本輪 {bull_count} 個多單同時出現*\n"
            f"BTC 若急跌可能同步觸損，請控制總倉位，勿全倉押入"
        )
    elif bear_count >= 3:
        correlation_warn = (
            f"\n{'─' * 20}\n"
            f"⚠️ *相關性警示：本輪 {bear_count} 個空單同時出現*\n"
            f"BTC 若急漲可能同步觸損，請控制總倉位，勿全倉押入"
        )

    # ── 評級統計（S/A/B/R）──────────────────────────────────────────
    _grade_counts = {"S": 0, "A": 0, "R": 0}
    for _g in grade_per_msg:
        if _g in _grade_counts:
            _grade_counts[_g] += 1

    _grade_parts = []
    for _g, _badge in [("S", "🏆S"), ("A", "🥇A"), ("R", "⚡R")]:
        if _grade_counts[_g] > 0:
            _grade_parts.append(f"{_badge}×{_grade_counts[_g]}")
    _grade_tag = "  ".join(_grade_parts) if _grade_parts else "─"

    header = (
        f"🔍 *傑克持倉異常狙擊鏡*  本輪 {push_count} 個訊號\n"
        f"🕐 {now_str} 台北  |  {_grade_tag}\n"
        f"{'─' * 20}\n"
    )
    sep = f"\n{'─' * 20}\n"
    _footer = f"\n{'─' * 20}\n⚠️ _計畫委託若超過 8 小時以上請撤單，代表已失效_"
    body = sep.join(messages_out) + correlation_warn + _footer

    # ── 以下為舊版渲染殘留（已棄用，直接 return 跳過）──────────────
    return header + body, has_any, push_count, s_grade_msgs, cards_payload

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
                    x.get("vwap_2h"), x.get("vwap_std"), x.get("ema20_close"), x.get("ub_value"), x.get("lb_value"),
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
                if x.get("btc_regime_warn"):
                    lines.append("⚠️ *BTC 處於短線急跌，山寨做多勝率低*")
                if x.get("volume_oi_warn"):
                    lines.append("⚠️ *量倉比異常 (<3x)，當心莊家假突破畫門*")
                # 順勢追多：進場點與上方阻力牆距離 < 1.5% → 盈虧比極差，強制警告
                _res = x.get("fp_resistance")
                _px = x.get("current_price")
                if is_bull and zone == ZONE_BREAKOUT_LONG and _res is not None and _px is not None and _px > 0:
                    try:
                        _dist_pct = (float(_res) - float(_px)) / float(_px)
                        if 0 < _dist_pct < 0.015:
                            lines.append("⚠️ *距離上方強阻力 < 1.5%，潛在利潤空間受限，建議觀望或等待突破*")
                    except (TypeError, ValueError):
                        pass
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


def process_single_symbol(coin: Dict) -> Optional[Dict]:
    """
    處理單個幣種（1H MTF 漏斗 Stage 1：1H OI/Price 大格局掃描）。
    四象限分類邏輯：
      price↑ + OI↑ = long_open  (主力積極建多倉)
      price↑ + OI↓ = short_close (空方被迫回補)
      price↓ + OI↑ = short_open (主力積極建空倉)
      price↓ + OI↓ = long_close  (多方恐慌平倉)
    """
    symbol = normalize_symbol(coin)
    if not symbol:
        return None

    try:
        # 1H 價格變化（漏斗 Stage 1 主時框）
        price_change_1h = coin.get("price_change_percent_1h")
        try:
            price_change_1h = float(price_change_1h) if price_change_1h is not None else None
        except (TypeError, ValueError):
            price_change_1h = None

        if price_change_1h is None:
            return {'status': 'no_category', 'symbol': symbol}

        # 1H OI 變化（Stage 1 核心指標）
        oi_change_1h = fetch_oi_change_tf(symbol, "1h")
        if oi_change_1h is None:
            return {'status': 'oi_failed', 'symbol': symbol}

        category = None
        if price_change_1h > 0:
            if oi_change_1h > 0:
                category = 'long_open'
            elif oi_change_1h < 0:
                category = 'short_close'
        elif price_change_1h < 0:
            if oi_change_1h > 0:
                category = 'short_open'
            elif oi_change_1h < 0:
                category = 'long_close'

        if category:
            price_change_24h = extract_price_change_24h(coin)
            price_change_30m = coin.get("price_change_percent_30m")
            try:
                price_change_30m = float(price_change_30m) if price_change_30m is not None else price_change_1h
            except (TypeError, ValueError):
                price_change_30m = price_change_1h
            return {
                'status': 'success',
                'category': category,
                'symbol': symbol,
                'priceChange1h': price_change_1h,
                'priceChange30m': price_change_30m,  # 保留供顯示用
                'oiChange1h': oi_change_1h,
                'oiChange30m': oi_change_1h,          # 向後相容
                'priceChange24h': price_change_24h,
                'price_change_percent_1h': price_change_1h,
                '_cg_volume_usd': coin.get("_volume_usd") or coin.get("_cg_volume_usd"),
                '_taker_ratio_15m': coin.get("_taker_ratio_15m"),  # CVD 假突破過濾用
                '_scan_ts': time.time(),  # 1H OI 異動首次偵測時間
            }
        else:
            return {'status': 'no_category', 'symbol': symbol}

    except Exception as e:
        logger.error(f"處理 {symbol} 時發生錯誤: {str(e)}")
        return {'status': 'error', 'symbol': symbol, 'error': str(e)}


def _gist_load_cooldown() -> Optional[Dict]:
    """從 GitHub Gist 讀取冷卻狀態（需設定 GIST_ID + GITHUB_TOKEN 環境變數）。
    回傳解析後的 dict，或 None（未設定 / 讀取失敗）。
    """
    gist_id = os.getenv("GIST_ID")
    token   = os.getenv("GITHUB_TOKEN")
    if not gist_id or not token:
        return None
    try:
        resp = requests.get(
            f"https://api.github.com/gists/{gist_id}",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"},
            timeout=8,
        )
        if resp.status_code == 200:
            files = resp.json().get("files", {})
            file_obj = files.get("sniper_cooldown.json") or next(iter(files.values()), None)
            if file_obj:
                raw = file_obj.get("content") or "{}"
                data = json.loads(raw)
                logger.info(f"[Gist冷卻✅] 從 GitHub Gist 讀取成功，history={len(data.get('history',[]))} 筆")
                return data
        else:
            logger.warning(f"[Gist冷卻] 讀取失敗 HTTP {resp.status_code}")
    except Exception as e:
        logger.warning(f"[Gist冷卻] 讀取例外: {e}")
    return None


def _gist_save_cooldown(data: Dict) -> bool:
    """將冷卻狀態寫回 GitHub Gist。回傳是否成功。"""
    gist_id = os.getenv("GIST_ID")
    token   = os.getenv("GITHUB_TOKEN")
    if not gist_id or not token:
        return False
    try:
        payload = {
            "files": {
                "sniper_cooldown.json": {
                    "content": json.dumps(data, ensure_ascii=False, indent=2)
                }
            }
        }
        resp = requests.patch(
            f"https://api.github.com/gists/{gist_id}",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"},
            json=payload,
            timeout=8,
        )
        if resp.status_code == 200:
            logger.info(f"[Gist冷卻✅] 寫回 GitHub Gist 成功，history={len(data.get('history',[]))} 筆")
            return True
        else:
            logger.warning(f"[Gist冷卻] 寫回失敗 HTTP {resp.status_code}")
    except Exception as e:
        logger.warning(f"[Gist冷卻] 寫回例外: {e}")
    return False




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
        p1h = (
            item.get("priceChangePercent1h") or
            item.get("price_change_percent_1h") or
            item.get("priceChange1h") or
            item.get("change1h") or item.get("change_1h")
        )
        p24 = (
            item.get("priceChangePercent24h") or
            item.get("price_change_percent_24h") or
            item.get("priceChange24h") or item.get("change_24h")
        )
        # 成交量欄位：CoinGlass 命名混亂，盡量窮舉所有可能的 key
        # 成交值：CoinGlass coins-markets 提供 long_volume_usd_24h + short_volume_usd_24h
        # 兩者相加 = 全市場 24h 合約成交額（USD）
        try:
            long_vol = float(item.get("long_volume_usd_24h") or 0)
            short_vol = float(item.get("short_volume_usd_24h") or 0)
            vol = long_vol + short_vol if (long_vol + short_vol) > 0 else 0.0
        except (TypeError, ValueError):
            vol = 0.0
        try:
            p15 = float(p15) if p15 is not None else None
        except (TypeError, ValueError):
            p15 = None
        try:
            p1h = float(p1h) if p1h is not None else None
        except (TypeError, ValueError):
            p1h = None
        try:
            p24 = float(p24) if p24 is not None else None
        except (TypeError, ValueError):
            p24 = None
        # CoinGlass coins-markets 已含 15m OI 變化率，直接保留，供後續「OI快速通道」用
        oi_15m_raw = (
            item.get("open_interest_change_percent_15m") or
            item.get("openInterestChangePercent15m") or
            item.get("oi_change_percent_15m") or
            item.get("oiChangePercent15m") or
            item.get("openInterestChangePercent30m") or
            item.get("open_interest_change_percent_30m")
        )
        try:
            cg_oi_15m = float(oi_15m_raw) if oi_15m_raw is not None else None
        except (TypeError, ValueError):
            cg_oi_15m = None

        # CoinGlass coins-markets 包含 15m/1h 主動買賣量（免費的 CVD 資料）
        # long_volume_usd_15m = 主動買入（taker buy）； short_volume_usd_15m = 主動賣出（taker sell）
        try:
            taker_buy_15m = float(item.get("long_volume_usd_15m") or 0)
            taker_sell_15m = float(item.get("short_volume_usd_15m") or 0)
            taker_total_15m = taker_buy_15m + taker_sell_15m
            # 主動買盤佔比 0~100；None 代表此幣無 15m taker 資料（非 coins-markets top-100）
            taker_ratio_15m = round(taker_buy_15m / taker_total_15m * 100, 1) if taker_total_15m > 0 else None
        except (TypeError, ValueError):
            taker_ratio_15m = None

        return {
            "symbol": sym,
            "coin": sym,
            "price_change_percent_15m": p15,   # 15m 漲跌幅（獨立欄位，供車已發動偵測用）
            "price_change_percent_30m": p15,   # 保留向後兼容
            "price_change_percent_1h": p1h,
            "price_change_percent_24h": p24,
            "_cg_volume_usd": vol,
            "_cg_oi_change_15m": cg_oi_15m,
            "_taker_ratio_15m": taker_ratio_15m,
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
                    item.get("price_change_percent_30m")
                )
                p1h = (
                    item.get("priceChangePercent1h") or
                    item.get("price_change_percent_1h") or
                    item.get("priceChange1h") or
                    item.get("change1h") or item.get("change_1h")
                )
                p24 = (
                    item.get("priceChangePercent24h") or
                    item.get("price_change_percent_24h") or
                    item.get("priceChange24h")
                )
                vol = (
                    item.get("volUsd24h") or item.get("volumeUsd24h") or
                    item.get("volume24h") or item.get("vol24h") or
                    item.get("turnover24h") or item.get("turnover") or
                    item.get("quoteVolume24h") or item.get("quoteVolume") or
                    item.get("usdVolume") or item.get("usdtVolume") or
                    item.get("volUsd") or item.get("vol")
                )
                try:
                    p15 = float(p15) if p15 is not None else None
                except (TypeError, ValueError):
                    p15 = None
                try:
                    p1h = float(p1h) if p1h is not None else None
                except (TypeError, ValueError):
                    p1h = None
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
                    "price_change_percent_15m": p15,   # 15m 漲跌幅（獨立欄位）
                    "price_change_percent_30m": p15,   # 保留向後兼容
                    "price_change_percent_1h": p1h,
                    "price_change_percent_24h": p24,
                    "_cg_volume_usd": vol,
                })
            return out
        except Exception as e:
            logger.warning(f"coins-price-change 異常: {e}")
            return []

    # ── Step 0：交易對白名單（最先執行，作為合約幣種過濾閘門）────────────────
    # 只取 Binance / Bybit / OKX 三大純加密交易所的永續合約基礎資產。
    # 這三所絕對不上代幣化股票 / 股指 / 商品期貨，因此可當做「加密貨幣白名單」。
    # BingX/Bitget 有 PLTR、GME、HK50 等代幣化商品，故意排除在外。
    _supported_whitelist: set = set()

    def _fetch_supported_whitelist() -> set:
        # 純加密交易所：Binance / Bybit / OKX 均不上架代幣化股票或指數
        _TARGET_EXCHANGES = {"Binance", "Bybit", "OKX"}
        try:
            _respect_coinglass_rate_limit()
            r = requests.get(
                f"{CG_API_BASE}/api/futures/supported-exchange-pairs",
                headers={"CG-API-KEY": CG_API_KEY, "accept": "application/json"},
                timeout=15
            )
            if r.status_code != 200:
                logger.warning(f"[exchange-pairs⚠️] HTTP {r.status_code}，白名單停用，改用黑名單保護")
                return set()
            data = r.json().get("data", {})
            if not isinstance(data, dict) or not data:
                return set()
            wl: set = set()
            ex_counts: dict = {}
            for exchange, pairs in data.items():
                if exchange not in _TARGET_EXCHANGES:
                    continue
                if not isinstance(pairs, list):
                    continue
                cnt = 0
                for pair in pairs:
                    base = str(pair.get("base_asset", "")).strip().upper()
                    if base and not base.endswith("STOCK") and not base.endswith("TOKEN"):
                        wl.add(base)
                        cnt += 1
                ex_counts[exchange] = cnt
            ex_summary = " | ".join(f"{ex}={n}" for ex, n in sorted(ex_counts.items()))
            logger.info(
                f"[exchange-pairs✅] 交易對白名單載入 {len(wl)} 個幣種"
                f"（{ex_summary}）"
            )
            return wl
        except Exception as e:
            logger.warning(f"[exchange-pairs⚠️] 異常: {e}，白名單停用，改用黑名單保護")
            return set()

    _supported_whitelist = _fetch_supported_whitelist()

    # ── Step 1：coins-markets（top 100，帶完整 OI/Price 數據）──────────────
    result_markets = _try_coins_markets()

    # ── Step 2：coins-price-change（全量幣種價格資料，診斷 + 補充）──────────
    result_pc = _try_coins_price_change()
    if result_pc:
        _pc_sample_keys = list(result_pc[0].keys()) if isinstance(result_pc[0], dict) else []
        logger.info(f"[CoinGlass-First] coins-price-change 回傳 {len(result_pc)} 個幣種 | 首筆欄位={_pc_sample_keys}")
    else:
        logger.warning("[CoinGlass-First] coins-price-change 無回傳數據")

    # ── Step 3：三路合併（所有來源均需通過白名單閘門）──────────────────────
    seen_syms: set = set()
    result: List[Dict] = []
    mkt_filtered = 0  # markets top-100 被白名單過濾的數量
    pc_filtered = 0   # price-change 被白名單過濾的數量

    def _wl_pass(sym: str) -> bool:
        """白名單檢查：sym_base 必須在 Binance/Bybit/OKX 的永續合約清單內。"""
        if not _supported_whitelist:
            return True  # 白名單載入失敗時放行，改靠黑名單保護
        base = sym.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
        return base in _supported_whitelist

    # markets top-100 同樣套白名單（PLTR/HK50 可能在 Hyperliquid OI 排行前列）
    for item in result_markets:
        sym = item.get("symbol", "")
        if not sym or sym in seen_syms:
            continue
        if not _wl_pass(sym):
            mkt_filtered += 1
            continue
        seen_syms.add(sym)
        result.append(item)

    # price-change 的補充幣種：同樣套白名單
    pc_added = 0
    for item in result_pc:
        sym = item.get("symbol", "")
        if not sym or sym in seen_syms:
            continue
        if not _wl_pass(sym):
            pc_filtered += 1
            continue
        seen_syms.add(sym)
        result.append(item)
        pc_added += 1

    wl_filtered = mkt_filtered + pc_filtered
    if wl_filtered > 0:
        logger.info(f"[白名單🚫] 過濾掉 {wl_filtered} 個非加密貨幣幣種（markets={mkt_filtered} | pc={pc_filtered}，Hyperliquid股票/指數/商品等）")

    # supported-coins 中尚未納入的合約幣種，補充為 stub（確保全覆蓋）
    stub_added = 0
    for name in _supported_whitelist:
        if name not in seen_syms and len(name) <= 14:
            seen_syms.add(name)
            result.append({
                "symbol": name, "coin": name,
                "price_change_percent_30m": None,
                "price_change_percent_24h": None,
                "_cg_volume_usd": None,
                "_stub": True,
            })
            stub_added += 1
    if stub_added:
        logger.info(f"[CoinGlass-First] supported-coins 補充 {stub_added} 個 stub 幣種（無 price 數據，直接進 OI 掃描）")

    markets_passed = len(result_markets) - mkt_filtered
    logger.info(
        f"[CoinGlass-First] 三路合併完成 → 總計 {len(result)} 個唯一幣種"
        f"（markets通過={markets_passed} | pc補充={pc_added} | stub={stub_added} | 白名單過濾={wl_filtered}）"
    )
    return result


def fetch_position_change():
    """
    【1H MTF 四層漏斗策略】中期波段持倉狙擊主流程。
    漏斗：1H OI/Price 大格局掃描 → 30m OI 確認延續性 → 15m OI 短期結構 → 5m OI 精準進場點。
    訊號：✅ 確定籌碼（四層共振）｜🎯 潛在機會（順勢回踩 / 逆勢摸頂底）。
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

    logger.info("🚀 山寨幣莊家狙擊鏡 啟動 | 純 CoinGlass 模式 | 1H MTF 四層漏斗掃描")

    # ════════════════════════════════════════════════════════
    # 漏斗 Step 0：資料源初始化（純 CoinGlass 模式）
    # ════════════════════════════════════════════════════════
    logger.info("📊 [掃描漏斗] Step 0：純 CoinGlass 模式，所有數據（成交值/K線/OI）均來自 CoinGlass API")

    # ════════════════════════════════════════════════════════
    # 漏斗 Step 1：CoinGlass 全市場數據（帶分頁，抓取 300~500 個幣種）
    # ════════════════════════════════════════════════════════
    all_symbols_data = fetch_coinglass_coins_markets()
    if not all_symbols_data:
        logger.warning("[漏斗] coins-markets 失敗，嘗試 coins-price-change 備援")
        all_symbols_data = fetch_coins_price_change()
        if all_symbols_data:
            logger.info(f"[備援✅] coins-price-change 取得 {len(all_symbols_data)} 個幣種")
    if not all_symbols_data:
        send_telegram_message("⚠️ 無法取得幣種漲跌資料，請稍後再試。", TG_THREAD_IDS['position_change'])
        return
    logger.info(f"📊 [漏斗 1] CoinGlass 全網 {len(all_symbols_data)} 幣種")

    # ── 單次迴圈完成兩件事：BTC/ETH 大盤、24h快取 ──────────────────────────────
    global _btc_30m_pct, _btc_1h_pct, _eth_30m_pct, _eth_1h_pct
    _btc_30m_pct = None
    _btc_1h_pct = None
    _eth_30m_pct = None
    _eth_1h_pct = None
    coinglass_24h_map: Dict[str, float] = {}
    active_symbols: List[Dict] = []
    for coin in all_symbols_data:
        sym_raw = normalize_symbol(coin) or ""
        clean_sym = sym_raw.replace("USDT", "").replace("-", "").replace("_", "").upper()

        # ① BTC 大盤環境
        if clean_sym == "BTC" and _btc_30m_pct is None:
            _btc_30m_pct = extract_price_change_30m(coin)
            _btc_1h_pct_raw = coin.get("price_change_percent_1h")
            try:
                _btc_1h_pct = float(_btc_1h_pct_raw) if _btc_1h_pct_raw is not None else None
            except (TypeError, ValueError):
                _btc_1h_pct = None
            logger.info(f"📊 [大盤濾網] BTC 30m {(_btc_30m_pct or 0):+.2f}%  1H {(_btc_1h_pct or 0):+.2f}%")

        # ①-2 ETH 大盤環境（山寨幣主要參考）
        if clean_sym == "ETH" and _eth_30m_pct is None:
            _eth_30m_pct = extract_price_change_30m(coin)
            _eth_1h_pct_raw = coin.get("price_change_percent_1h")
            try:
                _eth_1h_pct = float(_eth_1h_pct_raw) if _eth_1h_pct_raw is not None else None
            except (TypeError, ValueError):
                _eth_1h_pct = None
            logger.info(f"📊 [大盤濾網] ETH 30m {(_eth_30m_pct or 0):+.2f}%  1H {(_eth_1h_pct or 0):+.2f}%")

        # ② 24h 漲跌幅快取
        pct24 = extract_price_change_24h(coin)
        if pct24 is not None and clean_sym:
            coinglass_24h_map[clean_sym] = pct24

        active_symbols.append(coin)

    if not coinglass_24h_map:
        coinglass_24h_map = _fetch_coinglass_24h_map()

    # ════════════════════════════════════════════════════════
    # Plan B：BingX 永續合約 24h USDT 成交值（備援，用於 CoinGlass 無資料的幣種）
    # 單一 API call，失敗時靜默回傳空 dict 不影響主流程
    # ════════════════════════════════════════════════════════
    _binance_vol_map: Dict[str, float] = fetch_bingx_futures_24h_vol()

    # ════════════════════════════════════════════════════════
    # 漏斗 Step 4：成交值預篩（三路來源：CoinGlass A → Binance B → 待 K 線估算 C）
    # 規則：
    #   combined_vol ≥ MTF_VOLUME_MIN_USD → 放行（門檻由頂部常數控制，預設 5M）
    #   combined_vol = 0                  → A+B 均無資料 → 放行，等 K 線估算（Plan C）
    #   0 < combined_vol < MTF_VOLUME_MIN_USD → 確認流動性不足 → 過濾
    # ════════════════════════════════════════════════════════
    VOLUME_PREFILTER_MIN_USD = MTF_VOLUME_MIN_USD  # 從常數讀取（預設 5M，可在頂部常數區調整）

    active_above_volume: List[Dict[str, Any]] = []
    vol_cg = 0         # Plan A (CoinGlass) 有資料且 ≥ MTF_VOLUME_MIN_USD
    vol_binance = 0    # Plan B (BingX備援) 補救且 ≥ MTF_VOLUME_MIN_USD
    vol_no_data = 0    # A+B 均無資料 → 放行等 Plan C
    vol_below = 0      # 確認不足門檻 → 過濾

    for coin in active_symbols:
        # ── Plan A：CoinGlass 成交值 ─────────────────────────────
        cg_vol = coin.get("_cg_volume_usd")
        try:
            cg_vol = float(cg_vol) if cg_vol is not None else 0.0
        except (TypeError, ValueError):
            cg_vol = 0.0

        # ── Plan B：Binance 備援（CoinGlass 無資料時使用）──────────
        combined_vol = cg_vol
        _vol_source = "CoinGlass"
        if cg_vol == 0.0 and _binance_vol_map:
            base_key = (coin.get("symbol") or coin.get("coin") or "").replace("USDT", "").replace("-", "").upper()
            b_vol = _binance_vol_map.get(base_key, 0.0)
            if b_vol > 0:
                combined_vol = b_vol
                _vol_source = "BingX"

        coin["_volume_usd"] = combined_vol
        coin["_cg_volume_usd"] = combined_vol
        coin["_vol_source"] = _vol_source

        if combined_vol == 0.0:
            # A+B 均無資料 → 放行，等 enrichment 階段 Plan C（K 線估算）補充
            coin["_vol_need_planc"] = True
            vol_no_data += 1
            active_above_volume.append(coin)
        elif combined_vol >= VOLUME_PREFILTER_MIN_USD:
            if _vol_source == "CoinGlass":
                vol_cg += 1
            else:
                vol_binance += 1
            active_above_volume.append(coin)
        else:
            vol_below += 1

    logger.info(
        f"📊 [漏斗 4] 成交值篩選 ≥{MTF_VOLUME_MIN_USD/1e6:.1f}M: 通過 {len(active_above_volume)} 個"
        f"（CoinGlass: {vol_cg} | BingX備援: {vol_binance} | 待K線估算: {vol_no_data} | 淘汰[確認<{MTF_VOLUME_MIN_USD/1e6:.1f}M]: {vol_below}）"
    )

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
                price_change_1h = result.get('priceChange1h')
                price_change = result.get('priceChange30m') or price_change_1h
                oi_change = result.get('oiChange1h') or result.get('oiChange30m')
                price_change_24h = result.get('priceChange24h')
                item = {
                    'symbol': symbol,
                    'priceChange1h': price_change_1h,
                    'priceChange30m': price_change,
                    'oiChange1h': oi_change,
                    'oiChange30m': oi_change,    # 向後相容
                    'priceChange24h': price_change_24h,
                    'price_change_percent_1h': price_change_1h,
                    '_cg_volume_usd': result.get('_cg_volume_usd'),
                    '_taker_ratio_15m': result.get('_taker_ratio_15m'),
                }
                base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
                oi_min = OI_THRESHOLD_MAIN if base in MAIN_COINS else (
                    OI_THRESHOLD_HIGH_LIQ if (result.get("_cg_volume_usd") or 0) >= HIGH_LIQ_VOLUME_USD
                    else OI_THRESHOLD_SMALL
                )
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
    logger.info(
        f"📊 [Step1 1H OI掃描] 共 {processed_count} 幣 | 成功 {oi_success_count} 失敗 {oi_fail_count} | 用時 {total_time/60:.1f}min | "
        f"入選: 多開 {len(long_open)} 多平 {len(long_close)} 空開 {len(short_open)} 空平 {len(short_close)} "
        f"（達門檻 {in_four} / OI成功 {oi_success_count}）"
    )

    # 分層 OI 門檻：一律依幣種套用 4%（主流）/ 6%（高流動）/ 8%（小幣），無樣本 fallback
    logger.info("【OI門檻】強制分層：主流 4% / 高流動 6% / 小幣 8%")
    long_open = [x for x in long_open if abs(x.get('oiChange30m') or 0) >= _get_oi_threshold_for_item(x)]
    long_close = [x for x in long_close if abs(x.get('oiChange30m') or 0) >= _get_oi_threshold_for_item(x)]
    short_open = [x for x in short_open if abs(x.get('oiChange30m') or 0) >= _get_oi_threshold_for_item(x)]
    short_close = [x for x in short_close if abs(x.get('oiChange30m') or 0) >= _get_oi_threshold_for_item(x)]
    # ── 按 1H OI 絕對值排名（取前3名，OI越大=主力動作越明確）────────────────
    # 目的：找「持倉變化最劇烈」的幣，不是隨機取樣
    long_open.sort(key=lambda x: abs(x.get('oiChange1h') or x.get('oiChange30m') or 0), reverse=True)
    long_close.sort(key=lambda x: abs(x.get('oiChange1h') or x.get('oiChange30m') or 0), reverse=True)
    short_open.sort(key=lambda x: abs(x.get('oiChange1h') or x.get('oiChange30m') or 0), reverse=True)
    short_close.sort(key=lambda x: abs(x.get('oiChange1h') or x.get('oiChange30m') or 0), reverse=True)
    top_long_open  = long_open[:5]
    top_long_close = long_close[:5]
    top_short_open = short_open[:5]
    top_short_close = short_close[:5]
    # 記錄各類別排名（1=OI最大），供後續評級使用
    for _cat_list in (top_long_open, top_long_close, top_short_open, top_short_close):
        for _rank_i, _item in enumerate(_cat_list):
            _item["_oi_rank"] = _rank_i + 1
    logger.info(
        f"📊 [TOP候選] 多開 {len(top_long_open)} 多平 {len(top_long_close)} 空開 {len(top_short_open)} 空平 {len(top_short_close)}（各取前3）→ 開始 enrichment"
    )

    # ════════════════════════════════════════════════════════
    # Enrichment：核心資料（CoinGlass 技術指標 + 資金費率）
    # ════════════════════════════════════════════════════════
    _cg_fr_map: Dict[str, float] = _fetch_funding_rate_map()
    logger.info(f"[FR批次] CoinGlass Funding Rate 預載完成，共 {len(_cg_fr_map)} 個幣種")

    all_top = []
    for item, cat in [(x, "long_open") for x in top_long_open] + [(x, "long_close") for x in top_long_close] + [(x, "short_open") for x in top_short_open] + [(x, "short_close") for x in top_short_close]:
        sym = item.get("symbol", "")

        # ── 黑名單前置過濾（在 K 線抓取前攔截，節省 API 次數）──────────────────────
        _sym_base = sym.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
        # 代幣化股票自動攔截：PLTRSTOCK / MASTOCK / NVDASTOCK 等以 STOCK 結尾的格式
        _is_tokenized_stock = _sym_base.endswith("STOCK") or _sym_base.endswith("TOKEN")
        if _sym_base in SYMBOL_BLACKLIST or _is_tokenized_stock:
            logger.info(f"[黑名單🚫] {sym} 在 enrichment 前即封鎖，跳過 K 線抓取")
            continue

        # 技術指標：CoinGlass K 線計算 RSI / ATR / 結構高低點
        # （_fetch_cg_klines_and_calc 內部已有 _respect_coinglass_rate_limit 限速，無需額外 sleep）
        tech = calculate_technicals(sym)
        # K 線無效則立即結束本幣種 enrichment：不呼叫 OI 多週期 / CVD 背離，節省 API
        if not tech:
            logger.info(f"[K線無效⚠️] {sym}: 無法取得技術指標，跳過 enrichment（不呼叫 CVD/30m/15m/5m）")
            continue
        if tech.get("recent_high_2h") is None or tech.get("recent_low_2h") is None:
            logger.info(
                f"[K線無效⚠️] {sym}: 缺 2H 結構高低（recent_high_2h/recent_low_2h），"
                f"跳過 enrichment（不呼叫 CVD/30m/15m/5m）"
            )
            continue

        # ── Plan C：K 線估算成交值（補充 CoinGlass + Binance 均無資料的幣種）──────
        if item.get("_vol_need_planc") and tech:
            kline_vol_est = tech.get("kline_vol_usd_24h")
            if kline_vol_est and kline_vol_est > 0:
                item["_volume_usd"] = kline_vol_est
                item["_cg_volume_usd"] = kline_vol_est
                item["_vol_source"] = "K線估算"
                item.pop("_vol_need_planc", None)
                logger.debug(f"[Plan C] {sym}: K線估算 24h 成交值 {kline_vol_est/1e6:.2f}M USD")

        # 補取 1H/4H OI（Enrichment 用，僅對 top 少量候選幣種呼叫）
        _oi_tf = _fetch_oi_multi_tf(sym)
        item["oi_change_1h_pct"] = _oi_tf.get("1h")
        item["oi_change_4h_pct"] = _oi_tf.get("4h")

        # 4H 宏觀天候：EMA20 + RSI（Google 建議新增，僅作輔助資訊不作濾網阻斷）
        _tech_4h = _fetch_cg_klines_and_calc(sym, interval="4h", limit=20)
        _ema20_4h = _tech_4h.get("ema20_close") if _tech_4h else None
        _rsi_4h   = _tech_4h.get("rsi")        if _tech_4h else None
        # 判斷現價是否站上 4H EMA20（順/逆勢天候）
        # CoinGlass 有 price 的幣優先用 CoinGlass；BingX-only 幣（price=None）
        # 用 1H K線收盤（tech.current_price）作備援，確保 4H EMA 比對不失效
        _cur_price_prelim = item.get("price") or (tech.get("current_price") if tech else None)
        _is_above_4h_ema  = (
            bool(_cur_price_prelim > _ema20_4h)
            if (_cur_price_prelim and _ema20_4h and _ema20_4h > 0)
            else None
        )

        # 資金費率：CoinGlass 批次表（純 CoinGlass 模式，不再呼叫 BingX）
        _base_fr = sym.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
        funding_rate = _cg_fr_map.get(_base_fr)

        # ── 勝率強化防線 A：聰明錢 OI 驗證（API 失敗時中性放行）──────────────
        _smart_money_pack = {"smart_money": None, "stable_chg": None, "coin_chg": None}
        try:
            _sm = _fetch_smart_money_oi_split(_base_fr)
            if isinstance(_sm, dict):
                _smart_money_pack["smart_money"] = _sm.get("smart_money")
                _smart_money_pack["stable_chg"] = _sm.get("stable_chg")
                _smart_money_pack["coin_chg"] = _sm.get("coin_chg")
        except Exception as _e:
            logger.debug(f"[SmartMoneyOI] {sym} 取得失敗（中性放行）: {_e}")

        # ── 勝率強化防線 B：CVD 背離（API 失敗時中性放行）────────────────────
        _cvd_div = None
        try:
            _cvd_div = detect_cvd_divergence(_base_fr)  # 回傳 bullish / bearish / None
        except Exception as _e:
            logger.debug(f"[CVD] {sym} 背離檢測失敗（中性放行）: {_e}")

        # 24h 漲跌幅
        clean_base = sym.replace("USDT", "").replace("-", "").upper()
        price_24h = item.get("priceChange24h") if isinstance(item.get("priceChange24h"), (int, float)) else None
        if price_24h is None:
            price_24h = coinglass_24h_map.get(clean_base)

        # 1H 趨勢方向（MTF 濾網）
        price_1h = item.get("priceChange1h")
        try:
            price_1h = float(price_1h) if price_1h is not None else None
        except (TypeError, ValueError):
            price_1h = None

        # 四象限分類（15m 扳機 + 1h 趨勢濾網）
        classified = _classify_signal_and_tier(
            item, cat, tech, funding_rate,
            price_chg_24h=price_24h,
            price_chg_1h=price_1h,
        )
        if classified is None:
            logger.debug(f"[MTF] 跳過 {sym}: OI<動態門檻 或 Price<{PRICE_THRESHOLD_30M}%")
            continue
        signal_label, zone, stars, rsi_desc, reason = classified
        rsi_val = tech.get("rsi") if tech else None
        atr_val = tech.get("atr") if tech else None

        # ── 反畫門防護（Anti-Manipulation Gate）────────────────────────────
        # 放在分類後（已知是真實訊號候選）、推播前，封鎖莊家假突破/畫門特徵
        _manip_result = _check_manipulation_risk(item, tech, atr_val, category=cat)
        _manip_reason = _manip_result[0] if isinstance(_manip_result, tuple) else _manip_result
        _energy_exhausted_manip = _manip_result[1] if isinstance(_manip_result, tuple) else False
        if _manip_reason:
            logger.info(
                f"[反畫門🚫] {sym}（{cat}）封鎖推播：{_manip_reason}"
            )
            continue

        # ── K 線新鮮度驗證（防止 BingX/Bybit 回傳舊蠟燭導致進場價嚴重偏差）──────────
        # 若 K 線最新收盤與 CoinGlass 即時現價偏差 > 3%，代表 K 線已過期（例如幣種剛暴噴
        # 但 API 仍回傳噴前的收盤），整組技術指標全部失效，直接跳過此訊號。
        _cg_price = item.get("price")  # CoinGlass 即時現價（掃描週期取得，較即時）
        _kline_close = tech.get("current_price") if tech else None
        if _cg_price and _kline_close and _cg_price > 0 and _kline_close > 0:
            _kline_divergence = abs(_kline_close - _cg_price) / _cg_price
            if _kline_divergence > 0.03:
                logger.warning(
                    f"[K線過期⚠️] {sym}: K線收盤 {_kline_close:.6f} 與 CoinGlass現價 "
                    f"{_cg_price:.6f} 偏差 {_kline_divergence:.1%}（>3%），K線為舊數據，跳過此訊號"
                )
                continue

        # 現價：優先採用 CoinGlass 即時現價，K 線收盤作備援
        _cur_price = _cg_price if (_cg_price and _cg_price > 0) else _kline_close

        # ════════════════════════════════════════════════════════════════════
        # 漏斗式延遲 API 請求（Lazy Fetching）— 貼合 300次/分鐘 商業標準版
        # 速率控制：每筆請求前 sleep(0.2) = 5次/秒，完全不觸發 429
        # 策略：不符合條件就立刻 continue，不浪費後續 API 額度
        # ════════════════════════════════════════════════════════════════════

        # ── Step 2：取 30m OI，立即做方向衝突預篩 ────────────────────────────
        time.sleep(0.2)
        _oi_30m = fetch_oi_change_tf(sym, "30m")
        _p_30m  = item.get("priceChange30m")

        # 30m 四象限分類（行內計算，不依賴外部函數）
        if _oi_30m is not None:
            if _oi_30m > 0:
                _cat_30m_prelim = "long_open"  if (_p_30m is None or _p_30m >= 0) else "short_open"
            else:
                _cat_30m_prelim = "short_cover" if (_p_30m is not None and _p_30m > 0) else "long_close"
        else:
            _cat_30m_prelim = None

        logger.info(
            f"[Step2 30m OI] {sym}: OI={(_oi_30m or 0):+.2f}% → {_cat_30m_prelim or 'N/A'}"
            f"  (1H={cat})"
        )

        # Step 2 衝突阻斷：主力方向相反 → 節省 API，直接放棄
        _is_1h_bull_ctx = cat in ("long_open", "short_cover")
        _is_1h_bear_ctx = cat in ("short_open", "long_close")
        if _cat_30m_prelim is not None:
            if (_is_1h_bull_ctx and _cat_30m_prelim == "short_open") or \
               (_is_1h_bear_ctx and _cat_30m_prelim == "long_open"):
                logger.info(
                    f"[Step2❌漏斗阻斷] {sym}: 30m={_cat_30m_prelim} 與 1H={cat} "
                    f"方向衝突，節省 15m+5m API，放棄"
                )
                continue

        # ── Step 3 & 4：15m + 5m OI（僅針對通過 Step 2 的極少數幣種）──────────
        # short_open / long_open 訊號額外抓取 OI 歷史（4 根），供籌碼三步驟陷阱偵測使用
        time.sleep(0.2)
        _need_oi_history = (cat in ("short_open", "long_open"))
        if _need_oi_history:
            _oi_15m_result = fetch_oi_change_tf(sym, "15m", return_candles=6)
            if isinstance(_oi_15m_result, tuple):
                _oi_15m, _oi_15m_candles = _oi_15m_result
            else:
                _oi_15m, _oi_15m_candles = _oi_15m_result, []
            _oi_15m_candle_ts = _oi_15m_candles[-1]["t"] if _oi_15m_candles else 0
        else:
            _oi_15m_result = fetch_oi_change_tf(sym, "15m", return_ts=True)
            if isinstance(_oi_15m_result, tuple):
                _oi_15m, _oi_15m_candle_ts = _oi_15m_result
            else:
                _oi_15m, _oi_15m_candle_ts = _oi_15m_result, 0
            _oi_15m_candles = []
        logger.info(f"[Step3 15m OI] {sym}: OI={(_oi_15m or 0):+.2f}%")
        time.sleep(0.2)
        _oi_5m  = fetch_oi_change_tf(sym, "5m")
        logger.info(f"[Step4  5m OI] {sym}: OI={(_oi_5m or 0):+.2f}%")

        # ── MTF 訊號分類（嚴格版：不符合 A/B → None → continue）──────────────
        _mtf_item_preview = {
            "category":         cat,
            "oiChange1h":       item.get("oiChange1h") or item.get("oiChange30m") or 0,
            "priceChange1h":    price_1h or 0,
            "oiChange_30m":     _oi_30m,
            "priceChange30m":   _p_30m,
            "oiChange_15m":     _oi_15m,
            "oiChange_5m":      _oi_5m,
            "rsi":              rsi_val,
            "oi_change_4h_pct": _oi_tf.get("4h"),
        }
        _mtf_result = _classify_mtf_signal(_mtf_item_preview)

        # 嚴格訊號過濾：None = 弱訊號/方向凌亂，寧缺勿濫直接放棄
        if _mtf_result is None:
            logger.info(
                f"[嚴格過濾❌] {sym}: 不符合確定籌碼/完美回踩條件"
                f"（1H={cat}, 30m={_cat_30m_prelim}, OI15m={_oi_15m}, OI5m={_oi_5m}），放棄"
            )
            continue

        # ── CVD / Taker（順勢突破型）：加入「雙確認 / 強衝突」結構 ──────────────
        # 參考實戰判斷：方向一致（CVD + Taker 同向）才算主動資金真突破；
        # 若兩者同時反向，視為「強衝突」，先降級訊號版本以提高勝率。
        _cvd_1h = None
        _cvd_conflict_strong = False
        _cvd_confirmed = False
        if cat in ("long_open", "short_open"):
            try:
                time.sleep(0.15)
                _cvd_1h = _cvd_change_last2(sym, "1h")
            except Exception:
                pass
            _taker_chk = item.get("_taker_ratio_15m")
            try:
                _taker_chk = float(_taker_chk) if _taker_chk is not None else None
            except (TypeError, ValueError):
                _taker_chk = None
            if cat == "long_open":
                _cvd_support = (_cvd_1h is not None and _cvd_1h > 0)
                _taker_support = (_taker_chk is not None and _taker_chk >= 52)
                _cvd_opp = (_cvd_1h is not None and _cvd_1h < 0)
                _taker_opp = (_taker_chk is not None and _taker_chk < 45)
                _cvd_confirmed = bool(_cvd_support and _taker_support)
                _cvd_conflict_strong = bool(_cvd_opp and _taker_opp)
                if _cvd_conflict_strong:
                    logger.info(
                        f"[CVD/Taker強衝突🚫] {sym}: 做多但 CVD1h={_cvd_1h} taker%={_taker_chk} "
                        f"→ 雙反向，降級為觀察名單以提高勝率"
                    )
                elif _cvd_opp or _taker_opp:
                    logger.info(
                        f"[CVD/Taker⚠️扣分] {sym}: 做多但 CVD1h={_cvd_1h} taker%={_taker_chk} "
                        f"→ 不封鎖，改由綜合評分扣減（可能限價吸籌）"
                    )
            else:  # short_open
                _cvd_support = (_cvd_1h is not None and _cvd_1h < 0)
                _taker_support = (_taker_chk is not None and _taker_chk <= 48)
                _cvd_opp = (_cvd_1h is not None and _cvd_1h > 0)
                _taker_opp = (_taker_chk is not None and _taker_chk > 55)
                _cvd_confirmed = bool(_cvd_support and _taker_support)
                _cvd_conflict_strong = bool(_cvd_opp and _taker_opp)
                if _cvd_conflict_strong:
                    logger.info(
                        f"[CVD/Taker強衝突🚫] {sym}: 做空但 CVD1h={_cvd_1h} taker%={_taker_chk} "
                        f"→ 雙反向，降級為觀察名單以提高勝率"
                    )
                elif _cvd_opp or _taker_opp:
                    logger.info(
                        f"[CVD/Taker⚠️扣分] {sym}: 做空但 CVD1h={_cvd_1h} taker%={_taker_chk} "
                        f"→ 不封鎖，改由綜合評分扣減"
                    )

        # ── 資金費率多空壅擠過濾 ──────────────────────────────────────────────
        # 原理：費率偏負 = 空頭支付費率給多頭 = 空頭部位壅擠
        #       → 做空時風險高（嘎空）；做多時是順風（空頭補倉推升）
        #       費率偏正 = 多頭支付費率給空頭 = 多頭部位壅擠
        #       → 做多時風險高（多頭爆倉拋售）；做空時是順風
        _effective_version = _mtf_result.get("version", "potential")
        _fr_crowding_note = ""
        # CVD + Taker 強衝突：版本降級（後續版本門檻會濾掉），只保留更乾淨訊號
        if _cvd_conflict_strong and _effective_version == "confirmed":
            _effective_version = "tier2"
            _fr_crowding_note = "CVD/Taker 強衝突（疑似被動吸收，先觀察）"

        if funding_rate is not None and isinstance(funding_rate, (int, float)):
            _fr_abs = abs(funding_rate)
            _is_short_sig = cat in ("long_close", "short_open")
            _is_long_sig  = cat in ("long_open", "short_close")
            _fr_pct_str   = f"{funding_rate * 100:+.4f}%"

            if _is_short_sig and funding_rate < -FR_SHORT_SQUEEZE_BLOCK:
                # 費率 < -0.3%：空頭嚴重壅擠，嘎空風險極高，封鎖做空訊號
                logger.info(
                    f"[FR封鎖🚫] {sym}: 做空訊號 費率={_fr_pct_str}"
                    f"（空頭嚴重壅擠 ≤ -{FR_SHORT_SQUEEZE_BLOCK*100}%），封鎖"
                )
                continue
            elif _is_short_sig and funding_rate < -FR_SHORT_SQUEEZE_RISK:
                # 費率 -0.1%~-0.3%：空頭壅擠警戒，做空訊號降級
                _effective_version = "tier2"
                _fr_crowding_note = f"空頭壅擠警示（費率{_fr_pct_str}，嘎空風險偏高）"
                logger.info(
                    f"[FR降級⚠️] {sym}: 做空訊號 費率={_fr_pct_str} 空頭壅擠 → 降為觀察名單"
                )
            elif _is_long_sig and funding_rate > FR_LONG_LIQUIDATION_BLOCK:
                # 費率 > +0.5%：多頭嚴重壅擠，爆倉風險高，封鎖做多訊號
                logger.info(
                    f"[FR封鎖🚫] {sym}: 做多訊號 費率={_fr_pct_str}"
                    f"（多頭嚴重壅擠 ≥ +{FR_LONG_LIQUIDATION_BLOCK*100}%），封鎖"
                )
                continue
            elif _is_long_sig and funding_rate > FR_LONG_LIQUIDATION_RISK:
                # 費率 +0.2%~+0.5%：多頭壅擠警戒，做多訊號降級
                _effective_version = "tier2"
                _fr_crowding_note = f"多頭壅擠警示（費率{_fr_pct_str}，爆倉風險偏高）"
                logger.info(
                    f"[FR降級⚠️] {sym}: 做多訊號 費率={_fr_pct_str} 多頭壅擠 → 降為觀察名單"
                )

        # ── 3 步反轉陷阱偵測（short_open 摸頭 / long_open 摸底，OI+價格雙重確認）──
        _bull_trap_result = {"detected": False, "matched_steps": 0, "note": ""}
        if cat in ("short_open", "long_open") and _oi_15m_candles:
            _trap_type = "short" if cat == "short_open" else "long"
            time.sleep(0.15)
            _kline_15m = _fetch_15m_klines_raw(sym, limit=6) if sym else None
            _bull_trap_result = detect_trap_setup(_oi_15m_candles, _trap_type, _kline_15m)
            if _bull_trap_result.get("detected"):
                _label = "摸頭" if _trap_type == "short" else "摸底"
                logger.info(
                    f"[籌碼陷阱🎯] {sym}: 三步驟形態完整吻合"
                    f"（{_bull_trap_result['matched_steps']}/3 步）→ 強化 {_label} 訊號"
                )
            elif _bull_trap_result.get("matched_steps", 0) >= 2:
                logger.info(
                    f"[籌碼陷阱⚡] {sym}: 部分吻合"
                    f"（{_bull_trap_result['matched_steps']}/3 步）"
                )

        # ── 動能透支/乖離過大：confirmed 訊號價格偏離 VWAP > 1% → 強制限價掛單 ─────
        _energy_exhausted = _energy_exhausted_manip
        if _effective_version == "confirmed" and not _energy_exhausted:
            _vwap = tech.get("vwap_2h") if tech else None
            if _vwap and _cur_price and float(_vwap) > 0:
                _dev_pct = abs(float(_cur_price) - float(_vwap)) / float(_vwap) * 100
                if _dev_pct > 1.0:
                    _energy_exhausted = True
                    logger.info(
                        f"[動能透支⚠️] {sym}: confirmed 訊號價格偏離 VWAP {_dev_pct:.1f}% > 1%，"
                        f"強制限價掛單於 EMA20"
                    )

        _io_flag, _lp_val = derive_limit_order_from_inputs(
            cat,
            _cur_price,
            tech.get("vwap_2h") if tech else None,
            tech.get("ema20_close") if tech else None,
            _effective_version,
            _energy_exhausted,
        )

        all_top.append({
            **item,
            "priceChange24h": price_24h,
            "priceChange1h": price_1h,
            # 15m 價格變動（獨立欄位，供車已發動偵測使用）
            "priceChange15m": item.get("price_change_percent_15m") or item.get("priceChange15m"),
            "category": cat,
            "current_price": _cur_price,
            "rsi": rsi_val,
            "atr": atr_val,
            "recent_high_2h": tech.get("recent_high_2h") if tech else None,
            "recent_low_2h": tech.get("recent_low_2h") if tech else None,
            "pre_breakout_low": tech.get("pre_breakout_low") if tech else None,
            "pre_breakout_high": tech.get("pre_breakout_high") if tech else None,
            "ema20": tech.get("ema20_close") if tech else None,
            "ema20_touch_low": tech.get("ema20_touch_low") if tech else None,
            "ema20_touch_high": tech.get("ema20_touch_high") if tech else None,
            "last_kline_high_30m": tech.get("last_kline_high_30m") if tech else None,
            "last_kline_low_30m": tech.get("last_kline_low_30m") if tech else None,
            "last_kline_open_30m": tech.get("last_kline_open_30m") if tech else None,
            "last_kline_close_30m": tech.get("last_kline_close_30m") if tech else None,
            "signal_label": signal_label,
            "zone": zone,
            "stars": stars,
            "rsi_desc": rsi_desc,
            "reason": reason,
            "funding_rate": funding_rate,
            # 勝率強化欄位：smart money + CVD（grade 層做硬過濾與加分）
            "smart_money": _smart_money_pack.get("smart_money"),
            "stable_oi_chg": _smart_money_pack.get("stable_chg"),
            "coin_oi_chg": _smart_money_pack.get("coin_chg"),
            "cvd_divergence": _cvd_div,
            # 1h CVD 變化（僅 long_open/short_open 有值），供 _calc_signal_grade 5b 扣分
            "_cvd_1h": _cvd_1h,
            "_cvd_confirmed": _cvd_confirmed,
            "_cvd_conflict_strong": _cvd_conflict_strong,
            "vwap_2h": tech.get("vwap_2h") if tech else None,
            # _scan_ts = 1H OI 首次偵測時間（process_single_symbol 打上），保留原始時間
            # 若 item 無此欄位（舊路徑），以當前時間補足
            "_detected_ts": item.get("_scan_ts") or time.time(),
            # 15m OI K線起始時間（CoinGlass 資料本身的時間戳，代表持倉異動發生的時間窗）
            "_oi_15m_candle_ts": locals().get("_oi_15m_candle_ts") or 0,
            # MTF 四層數據
            "oiChange_30m": _oi_30m,
            "oiChange_15m": _oi_15m,
            "oiChange_5m":  _oi_5m,
            # MTF 訊號版本（已套入 FR 壅擠過濾，_effective_version 可能降級）
            "signal_version":  _effective_version,
            "signal_subtype":  _mtf_result.get("subtype", "") or _fr_crowding_note,
            "mtf_desc":        _mtf_result.get("mtf_desc", ""),
            "mtf_oi_line":     _mtf_result.get("mtf_oi_line", ""),
            "mtf_aligned":     _mtf_result.get("aligned_count", 1),
            "reversal_hint":   _mtf_result.get("reversal_hint", ""),
            # 4H 宏觀天候（輔助資訊）
            "ema20_4h":        _ema20_4h,
            "rsi_4h":          _rsi_4h,
            "is_above_4h_ema": _is_above_4h_ema,
            # 誘多摸頭陷阱偵測（short_open 專屬）
            "_bull_trap_detected": _bull_trap_result.get("detected", False),
            "_bull_trap_steps":    _bull_trap_result.get("matched_steps", 0),
            "_bull_trap_note":     _bull_trap_result.get("note", ""),
            # 動能透支/乖離過大：強制限價掛單於 EMA20，拒絕市價進場
            "_energy_exhausted": _energy_exhausted,
            # 限價單統一標記（與推播進場價、即時觸損/達標略過邏輯一致）
            "is_limit_order": _io_flag,
            "limit_price": _lp_val,
            # 衰竭反轉：抄底/摸頭方向（long/short），供推播覆寫 is_bull_sig 與標題
            "_exhaustion_reversal_direction": _mtf_result.get("exhaustion_direction"),
        })
        _ver_tag = (
            "🔥衰竭反轉" if _effective_version == "exhaustion_reversal"
            else "✅確定籌碼（鐵三角）" if _effective_version == "confirmed"
            else f"⚠️觀察名單({_fr_crowding_note or _mtf_result.get('subtype','')})" if _effective_version == "tier2"
            else f"🎯潛在機會({_mtf_result.get('subtype','')})"
        )
        logger.info(f"[Enrichment] {sym} 已加入 all_top：RSI={rsi_val} ATR={atr_val} 現價={_cur_price} | {_ver_tag} | {reason}")

    # 品質門撒①：ATR=None → K 線無數據，SL/TP/RSI 均無法計算，不推播
    pre_quality = len(all_top)
    all_top = [x for x in all_top if x.get("atr") is not None]
    skipped_no_kline = pre_quality - len(all_top)
    if skipped_no_kline > 0:
        logger.info(f"[品質門撒①] 淘汰 {skipped_no_kline} 個 ATR=None（K線無數據小幣），剩餘 {len(all_top)} 個訊號")

    # 品質門撒②：成交值仍未確認（三路均無資料：CoinGlass / BingX / K線估算全失敗）
    # 這些幣是在漏斗4以「待K線估算」名義放行的，但 Plan C 也沒估出來
    # → 無法確認流動性達標，不推播，避免推出「成交值 無數據」的訊號
    pre_vol = len(all_top)
    all_top = [x for x in all_top if not x.get("_vol_need_planc")]
    skipped_no_vol = pre_vol - len(all_top)
    if skipped_no_vol > 0:
        logger.info(f"[品質門撒②] 淘汰 {skipped_no_vol} 個成交值未確認（三路均無資料），剩餘 {len(all_top)} 個訊號")

    # 成交額同步（從 _cg_volume_usd 寫入供推播使用）
    for x in all_top:
        x["volume_usd"] = x.get("_volume_usd") or x.get("_cg_volume_usd") or 0

    # 品質門撒③：OI 續航一致性（15m/5m 與訊號類型方向一致），降低假突破噪音
    # - long_open / short_open 代表「建倉」：短週期 OI 應持續增加
    # - long_close / short_close 代表「平倉」：短週期 OI 應持續下降
    def _oi_flow_consistent(_x: Dict) -> bool:
        _cat = (_x.get("category") or "").strip()
        try:
            _oi15 = float(_x.get("oiChange_15m") or 0.0)
            _oi5 = float(_x.get("oiChange_5m") or 0.0)
        except (TypeError, ValueError):
            return False
        _is_open = _cat in ("long_open", "short_open")
        _is_close = _cat in ("long_close", "short_close")
        if not (_is_open or _is_close):
            return False
        # 持倉「建倉」要看得到持續加倉；「平倉」要看得到持續減倉
        if _is_open:
            return (_oi15 >= 0.12) and (_oi5 >= 0.05)
        return (_oi15 <= -0.12) and (_oi5 <= -0.05)

    _pre_oi_flow = len(all_top)
    all_top = [x for x in all_top if _oi_flow_consistent(x)]
    _drop_oi_flow = _pre_oi_flow - len(all_top)
    if _drop_oi_flow > 0:
        logger.info(
            f"[品質門撒③ OI續航] 淘汰 {_drop_oi_flow} 個 15m/5m OI 與類型不一致訊號，"
            f"剩餘 {len(all_top)} 個"
        )

    # 訊號版本門檻：僅保留「確定籌碼」與「衰竭反轉」（關閉 tier2、潛在/pullback 等）
    _ALLOW_PUSH_SIGNAL_VERSIONS = frozenset({"confirmed", "exhaustion_reversal"})
    _pre_ver_filt = len(all_top)
    all_top = [
        x for x in all_top
        if (x.get("signal_version") or "") in _ALLOW_PUSH_SIGNAL_VERSIONS
    ]
    if _pre_ver_filt - len(all_top) > 0:
        logger.info(
            f"[版本門檻] 淘汰 {_pre_ver_filt - len(all_top)} 個非 confirmed/衰竭反轉，"
            f"剩餘 {len(all_top)} 個"
        )

    _confirmed_cnt = sum(1 for x in all_top if x.get("signal_version") == "confirmed")
    _exhaust_cnt   = sum(1 for x in all_top if x.get("signal_version") == "exhaustion_reversal")
    _tier2_cnt     = sum(1 for x in all_top if x.get("signal_version") == "tier2")
    logger.info(
        f"[Enrichment 完成] {len(all_top)} 個訊號進入推播流程"
        f"（✅確定籌碼 {_confirmed_cnt} | 🔥衰竭反轉 {_exhaust_cnt}"
        f"{' | ⚠️觀察名單 ' + str(_tier2_cnt) if _tier2_cnt else ''}）"
    )
    if len(all_top) == 0:
        logger.info(f"本輪無符合條件訊號（1H OI≥動態門檻 & 成交值≥{MTF_VOLUME_MIN_USD/1e6:.0f}M USD & MTF共振未達標）")

    # 冷卻規則：同幣同方向 N 小時內不重複推；同輪每方向最多 M 檔（強籌碼優先）
    COOLDOWN_HOURS = 8   # 同幣同方向 8h 冷卻（拉長以降低重複推播）
    MAX_SIGNALS_PER_DIRECTION_PER_ROUND = 2  # 本輪「多」「空」各最多保留檔數
    HISTORY_HOURS = 24   # 冷卻歷史保留 24h（每日自動清理）
    # 順勢 S/A 推過後，此時間內不推「反向」R（S 為主、R 為輔；避免敘事打架）
    TREND_VS_R_OPPOSITE_HOURS = 12

    def _item_direction(x: Dict) -> str:
        """只回傳 多/空。優先用 build_report 已設定的 dir 欄位，其次用 category，最後才解析 signal_label。"""
        # 1. 最可靠：build_report_message_tiered 在每個推播項目上直接設定的 dir
        d = (x.get("dir") or "").strip()
        if d in ("多", "空"):
            return d
        # 2. 從 category 判斷（long_open / short_close = 看多訊號）
        cat = (x.get("category") or x.get("entry_category") or "").strip()
        if cat in ("long_open", "short_close"):
            return "多"
        if cat in ("short_open", "long_close"):
            return "空"
        # 3. fallback：嘗試解析 signal_label（關鍵字擴充）
        sig = x.get("signal_label") or ""
        bull_kws = ("做多", "追多", "嘎空", "抄底", "多頭入場", "空頭平倉", "強勢做多", "Long")
        return "多" if any(kw in sig for kw in bull_kws) else "空"

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

    # ── Gist 優先讀取冷卻狀態，失敗則 fallback 到本地 JSON ──────────
    _gist_data = _gist_load_cooldown()
    if _gist_data is not None:
        history = _gist_data.get("history") or []
        _in_window = sum(1 for e in history if isinstance(e, dict) and (now_ts - e.get("ts", 0)) <= cooldown_sec)
        logger.info(f"冷卻檔已讀取(Gist): history {len(history)} 筆，{COOLDOWN_HOURS}h 內 {_in_window} 筆")

    try:
        with _sniper_file_lock():
            if SNIPER_COOLDOWN_FILE.exists() and _gist_data is None:
                raw = json.loads(SNIPER_COOLDOWN_FILE.read_text(encoding="utf-8"))
                history = raw.get("history") or []
                # 相容舊格式：只有 last_round 時轉成 history
                if not history and raw.get("last_round"):
                    last_round = raw.get("last_round") or []
                    if last_round and isinstance(last_round[0], dict):
                        history = [{"symbol": str(p.get("symbol")), "dir": str(p.get("dir")), "ts": int(now_ts) - 3600} for p in last_round if p.get("symbol") and p.get("dir")]
                    else:
                        history = [{"symbol": str(p[0]), "dir": str(p[1]), "ts": int(now_ts) - 3600} for p in last_round if isinstance(p, (list, tuple)) and len(p) >= 2]
                logger.info(f"冷卻檔已讀取: {_cooldown_path_abs} | 歷史 {len(history)} 筆")
            else:
                if _gist_data is None:
                    logger.info(f"冷卻狀態檔不存在，本輪無冷卻限制: {_cooldown_path_abs}")
    except Exception as e:
        history = []
        logger.warning(f"讀取冷卻狀態檔失敗，本輪無冷卻限制: {e}")

    now_tw = datetime.fromtimestamp(now_ts, tz=TAIPEI_TZ)
    _in_window = sum(1 for e in history if isinstance(e, dict) and (now_ts - e.get("ts", 0)) <= cooldown_sec)
    logger.info(f"冷卻狀態: {len(history)} 筆歷史，{COOLDOWN_HOURS}h 內 {_in_window} 筆（同幣同方向才冷卻）")

    # 冷卻集合：同幣同方向在 COOLDOWN_HOURS 內已推過則阻擋
    cooldown_symbol_dir_4h: Set[Tuple[str, str]] = set()
    last_round_by_sym: Dict[str, str] = {}
    last_push_ts_by_sym_dir: Dict[Tuple[str, str], float] = {}
    for e in history:
        if not isinstance(e, dict) or not e.get("symbol") or not e.get("dir"):
            continue
        s = _cooldown_symbol(str(e["symbol"]))
        d = str(e["dir"])
        if (now_ts - e.get("ts", 0)) <= cooldown_sec:
            cooldown_symbol_dir_4h.add((s, d))
        if s not in last_round_by_sym:
            last_round_by_sym[s] = d
        key = (s, d)
        if key not in last_push_ts_by_sym_dir or (e.get("ts") or 0) > last_push_ts_by_sym_dir[key]:
            last_push_ts_by_sym_dir[key] = float(e.get("ts") or 0)
    latest_signal_by_sym: Dict[str, Dict[str, Any]] = {}

    # ── 黑名單二道防線（enrichment 前已擋一次，此處確保無漏網之魚）────────────────
    _before_bl = len(all_top)
    all_top = [
        x for x in all_top
        if _cooldown_symbol(x.get("symbol") or "").upper() not in SYMBOL_BLACKLIST
    ]
    _bl_removed = _before_bl - len(all_top)
    if _bl_removed > 0:
        logger.info(f"[黑名單🚫] 二道防線攔截 {_bl_removed} 個標的")

    cooled_top = []
    for x in all_top:
        sym = x.get("symbol") or ""
        if not sym:
            continue
        sym_norm = _cooldown_symbol(sym)
        cur_dir = _item_direction(x)
        # 冷卻視窗內是否剛推過「反向」：同標的、異方向（允許推播，但需要提醒）
        _opp_dir = "空" if cur_dir == "多" else "多"
        x["cooldown_reverse_recent"] = (sym_norm, _opp_dir) in cooldown_symbol_dir_4h

        # 同幣同方向：COOLDOWN_HOURS 內阻擋重推
        if (sym_norm, cur_dir) in cooldown_symbol_dir_4h:
            logger.info(f"冷卻跳過: {sym_norm} ({cur_dir}) ({COOLDOWN_HOURS}h 內同幣同方向已報過)")
            continue

        # 同幣換方向：標記多轉空/空轉多提醒
        if sym_norm in last_round_by_sym and last_round_by_sym[sym_norm] != cur_dir:
            x["direction_flip"] = last_round_by_sym[sym_norm] + "轉" + cur_dir
        else:
            x["direction_flip"] = None
        cooled_top.append(x)

    _skipped = len(all_top) - len(cooled_top)
    if _skipped > 0:
        logger.info(f"本輪冷卻跳過 {_skipped} 檔（同幣同方向 {COOLDOWN_HOURS}h 內不重推）")

    # 同輪限額：每方向（多/空）最多 N 檔，依 |1H OI%| 大者優先保留
    def _oi_abs_round_cap(xx: Dict) -> float:
        try:
            return abs(float(xx.get("oiChange1h") or 0))
        except (TypeError, ValueError):
            return 0.0

    _by_dir_lists: Dict[str, List] = {"多": [], "空": []}
    for _cx in cooled_top:
        _dkey = _item_direction(_cx)
        if _dkey in _by_dir_lists:
            _by_dir_lists[_dkey].append(_cx)
    _cooled_limited: List = []
    for _dkey in ("多", "空"):
        _lst = _by_dir_lists[_dkey]
        _lst.sort(key=_oi_abs_round_cap, reverse=True)
        _cooled_limited.extend(_lst[:MAX_SIGNALS_PER_DIRECTION_PER_ROUND])
    _cap_removed = len(cooled_top) - len(_cooled_limited)
    if _cap_removed > 0:
        logger.info(
            f"[同輪限額] 每方向最多 {MAX_SIGNALS_PER_DIRECTION_PER_ROUND} 檔（依|1H OI|），"
            f"剔除 {_cap_removed} 檔，剩餘 {len(_cooled_limited)} 檔"
        )
    cooled_top = _cooled_limited

    # ── 多所共識已移除（原 fetch_exchange_oi_consensus API 回傳資料與 15m 時間窗口不符，誤判多）────
    # is_global_consensus 欄位保留但固定為 False，is_premium 已不依賴此欄位
    if cooled_top:
        for _item in cooled_top:
            _item["is_global_consensus"] = False
            _item["volume_oi_warn"] = False

    # ── 推播前即時報價快照 + 結構 SL 觸損／達標防護 ─────────────────────────────
    # 與 build_report_message_tiered 相同：compute_structural_sl_tp；即時價已破 SL 或已過 TP1 → 不推。
    if cooled_top:
        _drop_low_r: List = []
        for _x in cooled_top:
            _sym_rt = _x.get("symbol") or ""
            _sig_price = _x.get("current_price")
            _x["signal_price"] = _sig_price
            if not _sig_price or not isinstance(_sig_price, (int, float)) or float(_sig_price) <= 0:
                continue
            try:
                _snap = _fetch_bingx_ticker_snapshot(_sym_rt)
                if _snap and _snap.get("price") and float(_snap["price"]) > 0:
                    _live = float(_snap["price"])
                    _drift = abs(_live - float(_sig_price)) / float(_sig_price)
                    _x["current_price"] = _live
                    if _drift >= 0.003:
                        logger.info(
                            f"[即時報價🔄] {_sym_rt}: 觸發 {_sig_price:.6f} → 即時 {_live:.6f}"
                            f"（偏差 {_drift:.1%}）"
                        )
                    _is_long_rt = (_x.get("category") or "") in ("long_open", "short_close")
                    _vwap_2h = _x.get("vwap_2h")
                    _ema_rt = _x.get("ema20") or _x.get("ema20_close")
                    _is_limit_rt = bool(_x.get("is_limit_order", False))
                    _lp_rt = _x.get("limit_price")
                    try:
                        _lp_f = (
                            float(_lp_rt)
                            if _lp_rt is not None and isinstance(_lp_rt, (int, float)) and float(_lp_rt) > 0
                            else None
                        )
                    except (TypeError, ValueError):
                        _lp_f = None
                    # 限價單：結構 SL/TP 以掛單價為進場（與推播一致）；市價則沿用即時價 + VWAP 帶修正
                    if _is_limit_rt and _lp_f is not None:
                        _entry_rt = _lp_f
                    else:
                        _entry_rt = _live
                        if _vwap_2h and isinstance(_vwap_2h, (int, float)) and float(_vwap_2h) > 0:
                            _vwap_f = float(_vwap_2h)
                            if _is_long_rt and _live > _vwap_f * 1.025:
                                _entry_rt = _vwap_f * 0.975
                            elif not _is_long_rt and _live < _vwap_f * 0.975:
                                _entry_rt = _vwap_f * 1.025
                    _sl_i, _tp1_i, _tp2_i, _one_ri, _slp_rt = compute_structural_sl_tp(
                        _entry_rt,
                        _is_long_rt,
                        _vwap_2h,
                        _ema_rt,
                        _x.get("recent_low_2h"),
                        _x.get("recent_high_2h"),
                    )
                    if _sl_i is None or _tp1_i is None:
                        continue

                    # ── 限價掛單（統一）：偏離掛單價 ≤5% 視為尚未成交 → 略過即時觸損/達標；>5% 阻斷推播 ──
                    _limit_dev_pct = (
                        abs(_live - _lp_f) / _lp_f if (_lp_f and _lp_f > 0) else 0.0
                    )
                    _blocked = False
                    if _is_limit_rt and _lp_f is not None:
                        if _limit_dev_pct > 0.05:
                            logger.info(
                                f"[限價偏離過大跳過] {_sym_rt}: 限價掛單價={_lp_f:.6f}，"
                                f"即時 {_live:.6f} 偏離 {_limit_dev_pct:.1%} > 5%"
                            )
                            _blocked = True
                        else:
                            logger.debug(
                                f"[即時RR略過] {_sym_rt}: 限價單尚未成交（偏離掛單價 {_limit_dev_pct:.2%}），"
                                f"不檢查觸損/達標"
                            )
                    else:
                        # 市價追入：嚴格檢查即時價是否已破結構 SL 或已過 TP1
                        if _is_long_rt:
                            if _live <= _sl_i:
                                logger.info(
                                    f"[已觸損跳過] {_sym_rt}: 即時 {_live:.6f} ≤ 結構SL {_sl_i:.6f}"
                                )
                                _blocked = True
                            elif _live >= _tp1_i:
                                logger.info(
                                    f"[已達標跳過] {_sym_rt}: 即時 {_live:.6f} ≥ TP1 {_tp1_i:.6f}"
                                )
                                _blocked = True
                        else:
                            if _live >= _sl_i:
                                logger.info(
                                    f"[已觸損跳過] {_sym_rt}: 即時 {_live:.6f} ≥ 結構SL {_sl_i:.6f}"
                                )
                                _blocked = True
                            elif _live <= _tp1_i:
                                logger.info(
                                    f"[已達標跳過] {_sym_rt}: 即時 {_live:.6f} ≤ TP1 {_tp1_i:.6f}"
                                )
                                _blocked = True
                    if _blocked:
                        _drop_low_r.append(_x)
            except Exception as _e:
                logger.debug(f"[即時報價] {_sym_rt} 快照失敗，沿用 K 線價格: {_e}")
        # 移除觸損／已達 TP1 的訊號
        for _drop in _drop_low_r:
            if _drop in cooled_top:
                cooled_top.remove(_drop)

    # 僅在「實際有至少一則訊號」時才推主報表；無訊號或全被風報比篩掉 → 不推，安靜
    has_any = False
    if cooled_top:
        msg, has_any, push_count, s_grade_msgs, cards_payload = build_report_message_tiered(
            cooled_top,
            processed_count,
            oi_success_count,
            sa_conflict_history=history,
            sa_conflict_max_age_sec=TREND_VS_R_OPPOSITE_HOURS * 3600,
            pipeline_now_ts=now_ts,
        )
        if has_any:
            logger.info(
                f"【推播總結】本輪最終推播 {push_count} 檔"
                f"（冷卻後候選 {len(cooled_top)} 個，RSI+風報比篩選後實推 {push_count} 個）"
                f"，處理幣種 {processed_count} 個，OI 成功 {oi_success_count} 個"
            )
            # ── 每檔訊號一張 K 線卡片（caption 用原推播訊息文字不變）────────────────────
            card_dir = (DATA_DIR / "kline_cards").resolve()
            card_dir.mkdir(parents=True, exist_ok=True)

            _ohlc_cache: Dict[str, Optional[List[Dict]]] = {}
            _oi_cache: Dict[str, Optional[List[Dict]]] = {}

            sent_cnt = 0
            for idx, payload in enumerate(cards_payload or []):
                sym_b = payload.get("symbol_base") or ""
                if not sym_b:
                    continue
                caption_txt = payload.get("caption") or ""
                if not caption_txt:
                    continue

                if sym_b not in _ohlc_cache:
                    _ohlc_cache[sym_b] = fetch_ohlc_5m(sym_b, limit=60)
                ohlc = _ohlc_cache.get(sym_b)

                if sym_b not in _oi_cache:
                    _oi_cache[sym_b] = fetch_coinglass_oi_5m(sym_b, limit=60)
                oi = _oi_cache.get(sym_b)

                # 若 K 線資料不足，仍至少推文字（不影響原推播）
                img_path = str(card_dir / f"{sym_b}_{int(now_ts)}_{idx}.png")
                if ohlc and len(ohlc) >= 2:
                    try:
                        def _posf(v):
                            try:
                                vf = float(v)
                                return vf if vf > 0 else None
                            except Exception:
                                return None
                        render_kline_oi_card(
                            symbol_base=sym_b,
                            direction_is_long=bool(payload.get("direction_is_long")),
                            ohlc_5m=ohlc,
                            oi_5m=oi,
                            sl=_posf(payload.get("sl")),
                            tp1=_posf(payload.get("tp1")),
                            tp2=_posf(payload.get("tp2")),
                            entry=_posf(payload.get("entry")),
                            vwap=_posf(payload.get("vwap")),
                            ema20=payload.get("ema20"),
                            ema20_touch_low=payload.get("ema20_touch_low"),
                            ema20_touch_high=payload.get("ema20_touch_high"),
                            ema20_4h=payload.get("ema20_4h"),
                            out_path=img_path,
                            title_line=f"{sym_b} | 60x5m(~5h) EMA20=purple VWAP=cyan OI=bars",
                        )
                        ok = send_telegram_photo(
                            img_path,
                            caption_txt,
                            TG_THREAD_IDS['position_change'],
                            parse_mode="Markdown",
                        )
                        if ok:
                            sent_cnt += 1
                        else:
                            # caption 可能超長/格式衝突：退回文字推播，確保內容不變
                            send_telegram_message(
                                caption_txt,
                                TG_THREAD_IDS['position_change'],
                                parse_mode="Markdown",
                            )
                    except Exception as e:
                        logger.warning(f"[K線卡片渲染/推送失敗] {sym_b}: {e}；改推文字")
                        send_telegram_message(
                            caption_txt,
                            TG_THREAD_IDS['position_change'],
                            parse_mode="Markdown",
                        )
                else:
                    logger.warning(
                        f"[K線卡片跳過] {sym_b}: fetch_ohlc_5m 回傳不足 "
                        f"(ohlc_len={len(ohlc) if ohlc else None})；改推文字"
                    )
                    send_telegram_message(
                        caption_txt,
                        TG_THREAD_IDS['position_change'],
                        parse_mode="Markdown",
                    )

            logger.info(f"[推播] 本輪已送出 {sent_cnt}/{len(cards_payload or [])} 張 K 線卡片")
        else:
            logger.info(
                f"【未推播原因】本輪 {len(cooled_top)} 筆通過冷卻，"
                f"但即時觸損/達標或評級篩選後 0 筆可推播，不發送主報表"
            )
    else:
        if len(all_top) == 0:
            logger.info(f"【未推播原因】本輪無達 OI 門檻之標的（四類皆 0 筆），不發送主報表")
        else:
            logger.info(f"【未推播原因】本輪 {len(all_top)} 筆候選皆被冷卻（4h 內同幣同方向已推過），不發送主報表")

    # 冷卻用：僅「本輪實際有推播」的標的才寫入 history（selected_for_push 在 build_report_message_tiered 內設定）
    pairs_this_run = [
        (
            _cooldown_symbol(x.get("symbol")),
            _item_direction(x),
            str(x.get("_push_grade") or ""),
        )
        for x in cooled_top
        if x.get("symbol") and x.get("selected_for_push")
    ]

    # GitHub Step Summary：若在 GitHub Actions 環境中，輸出本輪關鍵統計摘要
    step_summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if step_summary_path:
        try:
            pushed_symbols = sorted({_cooldown_symbol(x.get("symbol") or "") for x in cooled_top if x.get("symbol")}) if cooled_top else []
            pushed_list = ", ".join(pushed_symbols) if pushed_symbols else "無"
            summary_lines = [
                "## 持倉變化篩選摘要",
                "",
                "| 指標 | 數值 |",
                "| --- | --- |",
                f"| 處理幣種總數 | {processed_count} |",
                f"| OI 成功數 | {oi_success_count} |",
                f"| OI 失敗數 | {oi_fail_count} |",
                "| OI 門檻 | 分層：主流 4% / 高流動 6% / 小幣 8% |",
                f"| 進入 TOP 候選數 | {len(all_top)} |",
                f"| 最終推播標的數 | {len(cooled_top)} |",
                f"| 推播標的列表 | {pushed_list} |",
                "",
            ]
            with open(step_summary_path, "a", encoding="utf-8") as f:
                f.write("\n".join(summary_lines) + "\n")
        except Exception as e:
            logger.warning(f"寫入 GitHub Step Summary 失敗: {e}")

    # 寫回冷卻狀態（只保留 history，移除倉位追蹤）
    try:
        SNIPER_COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
        new_entries = [
            {"symbol": s, "dir": d, "grade": g, "ts": int(now_ts)}
            for (s, d, g) in pairs_this_run
            if s
        ]
        history = history + new_entries
        history = [e for e in history if isinstance(e, dict) and (now_ts - e.get("ts", 0)) <= history_sec]
        state = {"history": history}
        _emergency_sniper_state = state
        with _sniper_file_lock():
            save_json_file(SNIPER_COOLDOWN_FILE, state)
        logger.info(f"冷卻檔已寫入: 本輪 {len(new_entries)} 筆，歷史共 {len(history)} 筆 (保留 {HISTORY_HOURS}h)")
        _gist_save_cooldown(state)
    except Exception as e:
        logger.warning(f"寫入冷卻狀態檔失敗: {e}")

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


def _normalize_coinglass_ts(ts) -> int:
    """將 API 時間戳統一為「秒」級 Unix（抹平毫秒/字串差異）。"""
    if ts is None:
        return 0
    try:
        t = int(float(str(ts).strip()))
    except (ValueError, TypeError):
        return 0
    if t > 10_000_000_000:  # 毫秒級（13 位）
        t //= 1000
    return t


def _coinglass_binance_futures_symbol_alias(base: str) -> str:
    """
    CoinGlass / Binance 合約代碼與「基底幣簡稱」不一致時的映射（如 PEPE → 1000PEPE）。
    CVD 與 K 線必須使用同一套代碼，否則會一邊有資料一邊 0 條。
    """
    b = (base or "").replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
    if not b:
        return b
    _aliases = {
        "PEPE": "1000PEPE",
        "SHIB": "1000SHIB",
        "FLOKI": "1000FLOKI",
        "BONK": "1000BONK",
        "RATS": "1000RATS",
        "SATS": "1000SATS",
        "MOG": "1000MOG",
        "LUNC": "1000LUNC",
        "XEC": "1000XEC",
        "X": "1000X",
    }
    return _aliases.get(b, b)


def _coerce_positive_float(val) -> Optional[float]:
    """API 常回傳字串數字；需轉 float 才能當價格用。"""
    if val is None:
        return None
    try:
        if isinstance(val, str):
            v = float(val.strip())
        elif isinstance(val, (int, float)):
            v = float(val)
        else:
            return None
        if v > 0 and v == v:
            return v
    except (ValueError, TypeError):
        pass
    return None


def _extract_ohlc_high_low_close(item: Dict) -> tuple:
    """
    從 K 線 dict 取 high / low（相容小寫 key、字串數值）。
    回傳 (high, low) 任一缺漏時可 fallback close。
    """
    if not isinstance(item, dict):
        return None, None
    for hk, lk, ck in (
        ("high", "low", "close"),
        ("High", "Low", "Close"),
        ("h", "l", "c"),
    ):
        h = _coerce_positive_float(item.get(hk))
        l = _coerce_positive_float(item.get(lk))
        if h is not None and l is not None:
            return h, l
    c = _coerce_positive_float(
        item.get("close") or item.get("Close") or item.get("c")
        or item.get("markPrice") or item.get("mark_price") or item.get("price")
    )
    if c is not None:
        return c, c
    return None, None


def fetch_price_history(symbol: str, interval: str = "1h") -> Optional[List[Dict]]:
    """獲取價格歷史數據（OHLC）
    使用 futures open-interest/history（通常含 time/open/high/low/close）。
    若條數過少，會再試 Binance 合約別名（如 PEPEUSDT → 1000PEPEUSDT）。
    """
    url = f"{CG_API_BASE}/api/futures/open-interest/history"
    headers = {
        "CG-API-KEY": CG_API_KEY,
        "accept": "application/json"
    }

    def _fetch_one(sym: str) -> Optional[List[Dict]]:
        params = {"exchange": "Binance", "symbol": sym, "interval": interval}
        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            if response.status_code != 200:
                return None
            data = response.json()
            if data.get("code") not in ("0", 0, 200, "200"):
                return None
            data_list = data.get("data", [])
            if isinstance(data_list, list) and len(data_list) > 0:
                sample = data_list[0]
                if isinstance(sample, dict):
                    logger.debug(f"價格歷史 {sym}: 欄位 {list(sample.keys())[:15]} 共{len(data_list)}條")
                return data_list
        except Exception as e:
            logger.debug(f"價格歷史請求失敗 {sym}: {e}")
        return None

    try:
        base = symbol.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
        alt_base = _coinglass_binance_futures_symbol_alias(base)
        alt_sym = f"{alt_base}USDT" if alt_base else symbol

        best = _fetch_one(symbol)
        if (not best or len(best) < 5) and alt_sym != symbol:
            alt_list = _fetch_one(alt_sym)
            if alt_list and len(alt_list) > len(best or []):
                logger.info(f"[價格歷史] {symbol} 條數不足，改用合約別名 {alt_sym}（{len(alt_list)} 條）")
                best = alt_list
        return best
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
    base = _coinglass_binance_futures_symbol_alias(base)
    headers_cg = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}

    def _parse_cvd_list(data_list: list) -> Optional[List[Dict]]:
        if not isinstance(data_list, list) or not data_list:
            return None
        # 標準化欄位：統一為 {"time": 秒級 ts, "cvd": value}，與 K 線對齊用
        out = []
        for item in data_list:
            if not isinstance(item, dict):
                continue
            ts_raw = (
                item.get("time") or item.get("timestamp") or item.get("t")
                or item.get("createTime") or 0
            )
            ts_sec = _normalize_coinglass_ts(ts_raw)
            cvd = None
            for k in ("cum_vol_delta", "cvd", "value", "cvdValue",
                      "cumulativeVolumeDelta", "volumeDelta", "netVolume"):
                if item.get(k) is not None:
                    try:
                        cvd = float(item[k])
                        break
                    except (TypeError, ValueError):
                        if isinstance(item.get(k), str):
                            try:
                                cvd = float(str(item[k]).strip())
                                break
                            except (TypeError, ValueError):
                                pass
            if cvd is not None and ts_sec:
                out.append({"time": ts_sec, "cvd": cvd, "_raw": item})
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
    """檢測 CVD 背離（看漲/看跌）。
    - 價格 OHLC 支援字串數值、小寫 key（time/open/high/low/close）。
    - 價格與 CVD 以「秒級時間戳」內連集對齊，避免索引錯位。
    - PEPE 等合約自動映射為 1000PEPE，與 fetch_aggregated_cvd_history 一致。
    返回: 'bullish' | 'bearish' | None
    """
    try:
        raw_base = (symbol or "").replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
        cg_base = _coinglass_binance_futures_symbol_alias(raw_base)
        price_sym = f"{cg_base}USDT"

        logger.info(f"CVD 背離檢測 {raw_base}: 開始（合約代碼={cg_base}）")
        price_data = fetch_price_history(price_sym, "1h")
        cvd_data = fetch_aggregated_cvd_history(cg_base, "1h")

        logger.info(
            f"CVD 背離檢測 {raw_base}: 價格 {len(price_data) if price_data else 0} 條, "
            f"CVD {len(cvd_data) if cvd_data else 0} 條"
        )
        if not price_data or not cvd_data:
            return None

        price_by_t: Dict[int, Dict] = {}
        for row in price_data:
            if not isinstance(row, dict):
                continue
            ts = _normalize_coinglass_ts(row.get("time") or row.get("timestamp") or row.get("t"))
            if ts:
                price_by_t[ts] = row

        cvd_by_t: Dict[int, Dict] = {}
        for row in cvd_data:
            if not isinstance(row, dict):
                continue
            ts = _normalize_coinglass_ts(row.get("time"))
            if ts:
                cvd_by_t[ts] = row

        common_ts = sorted(set(price_by_t.keys()) & set(cvd_by_t.keys()))
        if len(common_ts) < 20:
            logger.info(
                f"CVD 背離檢測 {raw_base}: 時間對齊後僅 {len(common_ts)} 根（需≥20），"
                f"price_ts={len(price_by_t)} cvd_ts={len(cvd_by_t)}"
            )
            return None

        aligned_ts = common_ts[-20:]
        p_slice = [price_by_t[t] for t in aligned_ts]
        c_slice = [cvd_by_t[t] for t in aligned_ts]

        def _cvd_from_row(row: Dict) -> Optional[float]:
            if not isinstance(row, dict):
                return None
            v = row.get("cvd")
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    if isinstance(v, str):
                        try:
                            return float(v.strip())
                        except ValueError:
                            pass
            raw = row.get("_raw")
            if isinstance(raw, dict):
                for k in (
                    "cum_vol_delta", "cvd", "value", "cvdValue",
                    "cumulativeVolumeDelta", "volumeDelta", "netVolume",
                ):
                    if raw.get(k) is not None:
                        try:
                            return float(raw[k])
                        except (TypeError, ValueError):
                            if isinstance(raw.get(k), str):
                                try:
                                    return float(str(raw[k]).strip())
                                except ValueError:
                                    pass
            return None

        curr_item = p_slice[-1]
        curr_p_high, curr_p_low = _extract_ohlc_high_low_close(curr_item)
        if curr_p_high is None or curr_p_low is None:
            logger.info(
                f"CVD 背離檢測 {raw_base}: 無法解析當前 OHLC，keys={list(curr_item.keys())[:12]}"
            )
            return None

        curr_cvd = _cvd_from_row(c_slice[-1])
        if curr_cvd is None:
            logger.info(f"CVD 背離檢測 {raw_base}: 無法解析當前 CVD")
            return None

        prev_highs: List[float] = []
        prev_lows: List[float] = []
        for item in p_slice[:-1]:
            h, l_ = _extract_ohlc_high_low_close(item)
            if h is not None:
                prev_highs.append(h)
            if l_ is not None:
                prev_lows.append(l_)

        if not prev_highs or not prev_lows:
            logger.info(f"CVD 背離檢測 {raw_base}: 過去區間 OHLC 為空（字串價格未解析？）")
            return None

        prev_p_high = max(prev_highs)
        prev_p_low = min(prev_lows)

        high_idx = None
        best_d = float("inf")
        for idx, item in enumerate(p_slice[:-1]):
            h, _ = _extract_ohlc_high_low_close(item)
            if h is None:
                continue
            d = abs(h - prev_p_high)
            if d < best_d:
                best_d = d
                high_idx = idx
                if d < 0.01:
                    break

        low_idx = None
        best_d = float("inf")
        for idx, item in enumerate(p_slice[:-1]):
            _, l_ = _extract_ohlc_high_low_close(item)
            if l_ is None:
                continue
            d = abs(l_ - prev_p_low)
            if d < best_d:
                best_d = d
                low_idx = idx
                if d < 0.01:
                    break

        if high_idx is None or low_idx is None:
            logger.info(f"CVD 背離檢測 {raw_base}: 無法對應過去高低索引")
            return None

        cvd_at_p_high = _cvd_from_row(c_slice[high_idx])
        cvd_at_p_low = _cvd_from_row(c_slice[low_idx])
        if cvd_at_p_high is None or cvd_at_p_low is None:
            logger.info(
                f"CVD 背離檢測 {raw_base}: 無法解析歷史 CVD（hi={high_idx} lo={low_idx}）"
            )
            return None

        if curr_p_high > prev_p_high and curr_cvd < cvd_at_p_high:
            logger.info(
                f"CVD 背離檢測 {raw_base}: ✅ 看跌背離 "
                f"(價 {curr_p_high:.4f}>{prev_p_high:.4f}, CVD {curr_cvd:.2f}<{cvd_at_p_high:.2f})"
            )
            return "bearish"

        if curr_p_low < prev_p_low and curr_cvd > cvd_at_p_low:
            logger.info(
                f"CVD 背離檢測 {raw_base}: ✅ 看漲背離 "
                f"(價 {curr_p_low:.4f}<{prev_p_low:.4f}, CVD {curr_cvd:.2f}>{cvd_at_p_low:.2f})"
            )
            return "bullish"

        logger.info(
            f"CVD 背離檢測 {raw_base}: 無背離 "
            f"(價 {curr_p_high:.4f}/{curr_p_low:.4f} 過去 {prev_p_high:.4f}/{prev_p_low:.4f} CVD={curr_cvd:.2f})"
        )
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

    # 跨日自動清除：ORB 為日內策略，前一交易日未觸及 TP/SL 的殘留倉位不應封鎖新一天偵測
    _active_trade_date = state.get("trade_date")
    if state.get("last_direction") and _active_trade_date and _active_trade_date != today_trade_date:
        logger.info(
            "[黃金訊號] 前一交易日（%s）%s 倉 TP/SL 未觸及，跨日自動清除過期狀態，今日（%s）重新偵測",
            _active_trade_date, state.get("last_direction"), today_trade_date,
        )
        state = {}
        _save_gold_state({})

    last_bar_row = df_1h.iloc[-1]
    bar_high = float(last_bar_row["High"])
    bar_low  = float(last_bar_row["Low"])
    last_dir   = state.get("last_direction")
    last_sl    = state.get("last_sl")
    last_tp1   = state.get("last_tp1")
    last_tp2   = state.get("last_tp2") or state.get("last_tp")  # 向下相容舊 state
    last_entry = state.get("last_entry")
    last_tp1_hit = state.get("last_tp1_hit", False)

    if last_dir and last_sl is not None and last_tp2 is not None and last_entry is not None:
        hit = None
        if last_dir == "long":
            if bar_high >= last_tp2:
                hit = "tp2"                             # 目標二達成（優先判斷）
            elif last_tp1 and bar_high >= last_tp1 and not last_tp1_hit:
                hit = "tp1"                             # 目標一達成（尚未通知過）
            elif bar_low <= last_sl:
                hit = "sl"
        else:
            if bar_low <= last_tp2:
                hit = "tp2"
            elif last_tp1 and bar_low <= last_tp1 and not last_tp1_hit:
                hit = "tp1"
            elif bar_high >= last_sl:
                hit = "sl"

        if hit:
            _tp1_for_msg = last_tp1 if last_tp1 is not None else last_entry
            _tp2_for_msg = last_tp2
            msg_tpsl = format_tp_sl_hit_message(
                hit, last_dir, last_entry, last_sl, _tp1_for_msg, _tp2_for_msg
            )
            send_telegram_message(msg_tpsl, TG_THREAD_IDS.get("gold_signal", 254), parse_mode=None)

            if hit == "tp1":
                # 目標一達成：持倉繼續（追蹤 TP2），記錄 tp1 已通知，防止下輪重複推播
                logger.info("[黃金訊號] 目標一 (TP1) 達成，持倉繼續追蹤目標二 (TP2)")
                _save_gold_state({
                    **state,
                    "last_tp1_hit": True,
                })
                # 本輪不 return，繼續執行（但後面會因 same-direction 被擋住不再開新倉）
            else:
                # TP2 或 SL 達成：倉位結案
                logger.info("[黃金訊號] %s 觸及，本輪結案", "目標二 (TP2)" if hit == "tp2" else "止損 (SL)")
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
    # 反向持倉中：目前有一筆尚未結案的倉位，不開反向新倉（避免訊號矛盾且覆蓋 TP/SL 追蹤）
    active_direction = state.get("last_direction")
    opposite = {"long": "short", "short": "long"}.get(active_direction)
    if active_direction and signal.direction == opposite:
        logger.info(
            "[黃金訊號] 目前仍有 %s 倉尚未結案（TP/SL 未觸及），忽略反向 %s 訊號，避免覆蓋追蹤狀態",
            active_direction,
            signal.direction,
        )
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
            "last_tp1": signal.tp1,
            "last_tp2": signal.tp2,
            "last_tp1_hit": False,
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

