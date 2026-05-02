#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
區塊鏈船長：自動化推播系統
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
from kline_card_renderer import (
    fetch_ohlc_5m,
    fetch_coinglass_oi_5m,
    render_kline_oi_card,
    _load_cjk_font,
)
from whale_wallet_tracker import run_whale_wallet_tracker_once

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

# sendPhoto 最近一次 TG 失敗原因（供外層決定是否做文字備援，避免雙發）
_LAST_TG_PHOTO_FAILURE_REASON = ""

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
    # 官方 v4：/api/futures/coins-price-change（舊表曾寫成 /futures/price-change-list 缺 /api)
    "price_change_list":     "/api/futures/coins-price-change",                     # 幣種價格變化列表
    # 與文件一致：合約價格 K 線用 /api/futures/price/history（見 fetch_price_change_24h_coinglass_klines）
    "price_ohlc_history":    "/api/futures/price/history",                           # 交易對價格K線歷史
    # ── 現有路徑備援別名 ──
    "price_history_futures": "/api/futures/price/history",                           # 合約價格K線
    "price_history_spot":    "/api/spot/price/history",                              # 現貨價格K線
    "delisted_pairs":        "/api/futures/delisted-exchange-pairs",                 # 已下架交易對

    # ════════════════ 持倉 Open Interest（官方 v4 均為 /api/futures/open-interest/...）════
    "oi_history":            "/api/futures/open-interest/history",                    # 合約持倉量 K 線歷史
    "oi_agg_history":        "/api/futures/open-interest/aggregated-history",          # 聚合持倉K線（主力）
    "oi_agg_stable":         "/api/futures/open-interest/aggregated-stablecoin-history",
    "oi_agg_coin":           "/api/futures/open-interest/aggregated-coin-margin-history",
    "oi_exchange_list":      "/api/futures/open-interest/exchange-list",
    "oi_exchange_history":   "/api/futures/open-interest/exchange-history-chart",
    # 舊版 camelCase 路徑（若極少數環境仍只回應此路徑，供除錯對照；主流程已不用）
    "oi_history_camel":      "/api/futures/openInterest/ohlc-history",
    "oi_agg_history_camel":  "/api/futures/openInterest/ohlc-aggregated-history",
    "oi_agg_stable_camel":   "/api/futures/openInterest/ohlc-aggregated-stablecoin",
    "oi_agg_coin_camel":     "/api/futures/openInterest/ohlc-aggregated-coin-margin-history",

    # ════════════════ 資金費率 Funding Rate（官方 v4：/api/futures/funding-rate/...）════
    "fr_history":            "/api/futures/funding-rate/history",
    "fr_oi_weight":          "/api/futures/funding-rate/oi-weight-history",
    "fr_vol_weight":         "/api/futures/funding-rate/vol-weight-history",
    "fr_exchange_list":      "/api/futures/funding-rate/exchange-list",
    "fr_accum_exchange":     "/api/futures/funding-rate/accumulated-exchange-list",
    "fr_arbitrage":          "/api/futures/funding-rate/arbitrage",
    # 舊版 *Rate/ohlc-* 路徑（僅備查）
    "fr_history_camel":      "/api/futures/fundingRate/ohlc-history",
    "fr_oi_weight_camel":    "/api/futures/fundingRate/oi-weight-ohlc-history",
    "fr_vol_weight_camel":   "/api/futures/fundingRate/vol-weight-ohlc-history",

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
    "eth_etf_net_assets":    "/api/etf/ethereum/net-assets/history",                 # 以太坊ETF淨資產 🆕
    "eth_etf_list":          "/api/etf/ethereum/list",                              # 以太坊ETF列表 🆕
    "eth_etf_flow":          "/api/etf/ethereum/flow-history",                      # 以太坊ETF資金流 🆕
    "grayscale_holdings":    "/api/grayscale/holdings-list",                        # 灰度持倉列表 🆕
    "grayscale_premium":     "/api/grayscale/premium-history",                      # 灰度溢價歷史 🆕

    # ════════════════ 市場指標 Indicators ════════════════
    "rsi_list":              "/api/futures/rsi/list",                               # RSI列表
    "ema_list":              "/api/futures/ema/list",                               # 全市場 EMA（Standard+）🆕
    "td_list":               "/api/futures/td/list",                                # 全市場 TD Sequential（Standard+）🆕
    "atr_list":              "/api/futures/avg-true-range/list",                    # 全市場 ATR（Standard+；見 docs）🆕
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
    "btc_rainbow":           "/api/index/bitcoin-rainbow-chart",                     # BTC彩虹圖 🆕
    "btc_bubble_index":      "/api/index/bitcoin-bubble-index",                     # BTC泡沫指數 🆕
    "ma_2yr_multiplier":     "/api/index/2-year-ma-multiplier",                     # 2年均線乘數 🆕
    "ma_200wk_heatmap":      "/api/index/200-week-moving-average-heatmap",          # 200週均線熱力圖 🆕

    # ════════════════ Hyperliquid ════════════════
    "hl_whale_alert":        "/api/hyperliquid/whale-alert",                        # HL鯨魚預警
    "hl_whale_position":     "/api/hyperliquid/whale-position",                     # HL鯨魚持倉
    "hl_position":           "/api/hyperliquid/position",                           # HL幣種持倉
    "hl_wallet_pos_dist":    "/api/hyperliquid/wallet/position-distribution",       # HL錢包持倉分布
    "hl_wallet_pnl_dist":    "/api/hyperliquid/wallet/pnl-distribution",            # HL錢包盈虧分布
    "hl_global_ls_hist":     "/api/hyperliquid/global-long-short-account-ratio/history",  # HL 帳戶多空比歷史

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
DC_TOKEN = os.getenv('DC_TOKEN')

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
            'crit_radar': 11040,
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
        'crit_radar': int(os.environ.get('TG_THREAD_CRIT_RADAR') or 11040),
        'hyperliquid': int(os.environ.get('TG_THREAD_HYPERLIQUID', 252)),
        'gold_signal': int(os.environ.get('TG_THREAD_GOLD_SIGNAL') or 254),
    }

# Discord Thread/Channel IDs（可選）
# 建議使用 JSON 一次設定，例如：
# DC_THREAD_IDS='{"liquidity_radar":1493134385346777170,"news":1493134289456599060}'
dc_thread_ids_str = os.environ.get('DC_THREAD_IDS', '')
if dc_thread_ids_str:
    try:
        DC_THREAD_IDS = json.loads(dc_thread_ids_str)
    except Exception:
        DC_THREAD_IDS = {}
else:
    DC_THREAD_IDS = {}

# 兼容單獨環境變數（Zeabur 常用）
# 支援兩種命名：
# 1) DC_THREAD_IDS_<key>      e.g. DC_THREAD_IDS_LIQUIDITY_RADAR
# 2) DC_THREAD_IDS<key>       e.g. DC_THREAD_IDSliquidity_radar
for _k in (
    "sector_ranking",
    "buying_power_monitor",
    "position_change",
    "economic_data",
    "news",
    "funding_rate",
    "long_term_index",
    "liquidity_radar",
    "altseason_radar",
    "crit_radar",
    "hyperliquid",
    "gold_signal",
):
    if _k in DC_THREAD_IDS and str(DC_THREAD_IDS.get(_k)).strip():
        continue
    _env1 = f"DC_THREAD_IDS_{_k.upper()}"
    _env2 = f"DC_THREAD_IDS{_k}"
    _raw = os.getenv(_env1) or os.getenv(_env2) or ""
    _raw = str(_raw).strip()
    if _raw:
        DC_THREAD_IDS[_k] = _raw

try:
    _cr_tid = TG_THREAD_IDS.get("crit_radar")
    if _cr_tid is None or int(_cr_tid) <= 0:
        TG_THREAD_IDS["crit_radar"] = int(os.environ.get("TG_THREAD_CRIT_RADAR") or 11040)
except Exception:
    TG_THREAD_IDS["crit_radar"] = int(os.environ.get("TG_THREAD_CRIT_RADAR") or 11040)

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

# Gate 技術指標失敗次數（每輪用於判斷是否啟用 CoinGlass Plan B）
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
_btc_oi_1h_pct: Optional[float] = None  # BTC 1H OI 變化（僅供信心加分與推播輔助）
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

RISK_DISCLAIMER_LINE = "⚠️ *風險提示：* 本頻道內容僅供研究與教育用途，非投資建議、非任何形式帶單；請自行評估風險並嚴格控倉。"


def _append_push_taipei_timestamp(text: str) -> str:
    """在訊息末端附加當下台北時間（推播當下時刻）；已含同類標記則不重複。"""
    base = (text or "").rstrip()
    if not base:
        now_s = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
        return f"🕐 *推播時間：* {now_s}（台北）"
    if "推播時間：" in base and "台北" in base:
        return text
    now_s = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
    return f"{base}\n\n🕐 *推播時間：* {now_s}（台北）"


def _append_risk_disclaimer(text: str) -> str:
    """所有推播統一附上法規風險提示；若已包含則不重複附加。末端再附台北推播時間。"""
    base = (text or "").strip()
    if not base:
        out = RISK_DISCLAIMER_LINE
    elif ("非投資建議" in base) and ("帶單" in base):
        out = base
    else:
        out = f"{base}\n\n{RISK_DISCLAIMER_LINE}"
    return _append_push_taipei_timestamp(out)


def send_telegram_message(
    text: str,
    thread_id: int,
    parse_mode: str = "Markdown",
    reply_markup: Optional[Dict] = None,
    *,
    mirror_discord: bool = True,
    discord_force_everyone: bool = False,
) -> bool:
    """
    發送訊息到 Telegram（支援 Inline Keyboard 按鈕）。

    回傳值僅代表 **Telegram 是否成功**（與舊版 `tg_ok or dc_ok` 不同），
    避免「TG 失敗但 DC 成功」時外層誤判為成功而跳過 TG 備援（例如 K 線 caption 失敗）。

    mirror_discord=False：僅發 TG（用於已嘗試過 sendPhoto 且 DC 已收到圖時的文字備援，避免 DC 重複洗版）。
    discord_force_everyone=True：Discord 鏡像訊息前綴 @everyone（須頻道允許 Bot mention everyone）。
    """
    text = _append_risk_disclaimer(text)
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

    tg_ok = False
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            result = response.json()
            if result.get("ok"):
                logger.info("Telegram 訊息發送成功")
                tg_ok = True
            else:
                logger.error(f"Telegram API 錯誤: {result}")
        else:
            logger.error(f"Telegram HTTP 錯誤: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"發送 Telegram 訊息失敗: {str(e)}")

    if not tg_ok and mirror_discord:
        logger.warning(
            "[推播] Telegram 文字發送失敗，仍將嘗試 Discord 鏡像（若已設定 DC）"
        )

    # Discord 同步推播（不影響 Telegram 回傳值）
    dc_ok = False
    try:
        if not mirror_discord:
            if not tg_ok:
                logger.info("[推播] 已跳過 Discord 鏡像（mirror_discord=False，通常為圖片失敗後的純文字備援）")
            return tg_ok
        dc_channel_id = _resolve_dc_channel_id(thread_id)
        if DC_TOKEN and dc_channel_id:
            dc_url = f"https://discord.com/api/v10/channels/{dc_channel_id}/messages"
            dc_headers = {
                "Authorization": f"Bot {DC_TOKEN}",
                "Content-Type": "application/json",
            }
            dc_payload = {
                "content": _discord_content_with_mentions(
                    text, thread_id, force_everyone=discord_force_everyone
                ),
                "allowed_mentions": {"parse": ["everyone"]},
            }
            components = _convert_reply_markup_to_discord_components(reply_markup)
            if components:
                dc_payload["components"] = components

            dc_resp = requests.post(dc_url, headers=dc_headers, json=dc_payload, timeout=10)
            if 200 <= dc_resp.status_code < 300:
                dc_ok = True
                logger.info("Discord 訊息發送成功")
            else:
                logger.error(f"Discord sendMessage HTTP 錯誤: {dc_resp.status_code} - {dc_resp.text}")
        elif not DC_TOKEN:
            logger.info("Discord 略過：未設定 DC_TOKEN")
        else:
            logger.info(f"Discord 略過：找不到 thread_id={thread_id} 對應的 DC 頻道 ID")
    except Exception as e:
        logger.error(f"發送 Discord 訊息失敗: {str(e)}")

    if not tg_ok and dc_ok:
        logger.warning(
            "[推播不一致] Telegram 失敗但 Discord 已成功：請檢查 TG 的 Markdown/caption 長度、"
            "thread_id、或 Bot 權限；外層應以「回傳 False」觸發備援。"
        )

    return tg_ok


def send_telegram_photo(
    photo_path: str,
    caption: str,
    thread_id: int,
    parse_mode: str = "Markdown",
    reply_markup: Optional[Dict] = None,
    *,
    mirror_discord: bool = True,
) -> bool:
    """發送圖片到 Telegram（sendPhoto；caption 可能超出上限時，外層可改用 sendMessage 備援）

    回傳值僅代表 **Telegram sendPhoto 是否成功**。mirror_discord=False 時不發 DC。
    """
    caption = _append_risk_disclaimer(caption)
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

    global _LAST_TG_PHOTO_FAILURE_REASON
    _LAST_TG_PHOTO_FAILURE_REASON = ""
    tg_ok = False
    try:
        with open(photo_path, "rb") as f:
            files = {"photo": f}
            resp = requests.post(url, data=payload, files=files, timeout=30)
        if resp.status_code == 200:
            result = resp.json()
            if result.get("ok"):
                logger.info("Telegram 圖片發送成功")
                tg_ok = True
            else:
                _LAST_TG_PHOTO_FAILURE_REASON = str(result.get("description") or result)
                logger.error(f"Telegram sendPhoto API 錯誤: {result}")
        else:
            _LAST_TG_PHOTO_FAILURE_REASON = f"HTTP {resp.status_code}: {resp.text[:240]}"
            logger.error(f"Telegram sendPhoto HTTP 錯誤: {resp.status_code} - {resp.text}")
    except Exception as e:
        _LAST_TG_PHOTO_FAILURE_REASON = str(e)
        logger.error(f"發送 Telegram 圖片失敗: {str(e)}")

    if not tg_ok and mirror_discord:
        logger.warning("[推播] Telegram 圖片發送失敗，仍將嘗試 Discord 鏡像（若已設定 DC）")

    # Discord 同步推播（不影響 Telegram 回傳值）
    dc_ok = False
    try:
        if not mirror_discord:
            if not tg_ok:
                logger.info("[推播] 已跳過 Discord 圖片鏡像（mirror_discord=False）")
            return tg_ok
        dc_channel_id = _resolve_dc_channel_id(thread_id)
        if DC_TOKEN and dc_channel_id:
            dc_url = f"https://discord.com/api/v10/channels/{dc_channel_id}/messages"
            dc_headers = {
                "Authorization": f"Bot {DC_TOKEN}",
            }
            dc_payload = {
                "content": _discord_content_with_mentions(caption or "", thread_id),
                "allowed_mentions": {"parse": ["everyone"]},
            }
            components = _convert_reply_markup_to_discord_components(reply_markup)
            if components:
                dc_payload["components"] = components

            with open(photo_path, "rb") as f:
                files = {
                    "files[0]": (Path(photo_path).name, f, "application/octet-stream"),
                    "payload_json": (None, json.dumps(dc_payload, ensure_ascii=False)),
                }
                dc_resp = requests.post(dc_url, headers=dc_headers, files=files, timeout=30)
            if 200 <= dc_resp.status_code < 300:
                dc_ok = True
                logger.info("Discord 圖片發送成功")
            else:
                logger.error(f"Discord sendPhoto HTTP 錯誤: {dc_resp.status_code} - {dc_resp.text}")
        elif not DC_TOKEN:
            logger.info("Discord 圖片略過：未設定 DC_TOKEN")
        else:
            logger.info(f"Discord 圖片略過：找不到 thread_id={thread_id} 對應的 DC 頻道 ID")
    except Exception as e:
        logger.error(f"發送 Discord 圖片失敗: {str(e)}")

    if not tg_ok and dc_ok:
        logger.warning(
            "[推播不一致] Telegram 圖片失敗但 Discord 已成功：外層應以回傳 False 觸發純文字備援至 TG"
        )

    return tg_ok


def _resolve_dc_channel_id(thread_id: int) -> Optional[int]:
    """將 Telegram thread_id 對應到 Discord channel_id。"""
    try:
        for key, tg_tid in TG_THREAD_IDS.items():
            if int(tg_tid) == int(thread_id):
                raw = DC_THREAD_IDS.get(key)
                if raw is None or str(raw).strip() == "":
                    return None
                return int(raw)
    except Exception:
        return None
    return None


def _resolve_thread_key_by_id(thread_id: int) -> Optional[str]:
    """由 Telegram thread_id 反查功能鍵名（如 position_change/economic_data/hyperliquid）。"""
    try:
        for key, tg_tid in TG_THREAD_IDS.items():
            if int(tg_tid) == int(thread_id):
                return str(key)
    except Exception:
        return None
    return None


def _discord_content_with_mentions(
    text: str, thread_id: int, *, force_everyone: bool = False
) -> str:
    """特定主題或呼叫端指定時，在 Discord 訊息前加 @everyone。"""
    base = _convert_text_for_discord(text)
    key = _resolve_thread_key_by_id(thread_id) or ""
    if force_everyone or key in {"position_change", "economic_data"}:
        return f"@everyone\n{base}".strip()
    return base


def _convert_text_for_discord(text: str) -> str:
    """Discord 不吃 Telegram Markdown；按需求把單星號改雙星號。"""
    if not text:
        return ""
    return text.replace("*", "**")


def _convert_reply_markup_to_discord_components(reply_markup: Optional[Dict]) -> List[Dict]:
    """將 Telegram inline_keyboard 轉為 Discord link buttons。"""
    if not isinstance(reply_markup, dict):
        return []
    rows = reply_markup.get("inline_keyboard")
    if not isinstance(rows, list):
        return []

    components: List[Dict] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        btns: List[Dict] = []
        for btn in row:
            if not isinstance(btn, dict):
                continue
            label = str(btn.get("text") or "").strip()
            url = str(btn.get("url") or "").strip()
            if not label or not url:
                continue
            btns.append({
                "type": 2,      # button
                "style": 5,     # LINK
                "label": label[:80],
                "url": url,
            })
            if len(btns) >= 5:
                break
        if btns:
            components.append({
                "type": 1,      # action row
                "components": btns,
            })
        if len(components) >= 5:
            break
    return components


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

    message += "\n💡 _由 AI 每四小時自動監控資金流向_"

    keyboard = {
        "inline_keyboard": [
            [{"text": "🔥 查看族群熱力圖 (點我)", "url": "https://www.coingecko.com/zh-tw/categories#key-stats"}]
        ]
    }
    if not jackbot_universal_pre_send_gatekeeper("sector_ranking", text=message):
        return
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


def _coinglass_extract_data_list(payload: Optional[Dict]) -> Optional[List[Dict]]:
    """從 CoinGlass JSON 取出 OI K 線列表（data 可為 list 或巢狀）。"""
    if not payload or not isinstance(payload, dict):
        return None
    raw = payload.get("data")
    if isinstance(raw, list) and raw:
        return raw
    if isinstance(raw, dict):
        for k in ("list", "data_list", "history", "items"):
            v = raw.get(k)
            if isinstance(v, list) and v:
                return v
    return None


def fetch_aggregated_stablecoin_oi_history(symbol: str = "BTC", interval: str = "1h") -> Optional[List[Dict]]:
    """獲取聚合穩定幣保證金持倉歷史（優先與狙擊鏡相同的 open-interest 路徑 + 正確 interval 格式）。"""
    if not CG_API_KEY:
        return None
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    intv = _cg_interval(interval)

    # (A) kebab-case 歷史端點（舊版直連 URL，保留為備援）
    urls_try = [
        (
            f"{CG_API_BASE}/api/futures/open-interest/aggregated-stablecoin-history",
            {
                "exchange": "Binance",
                "symbol": base,
                "interval": intv,
            },
        ),
        (
            f"{CG_API_BASE}/api/futures/open-interest/aggregated-stablecoin-history",
            {
                "exchange_list": "Binance,Bybit,OKX,Gate",
                "symbol": base,
                "interval": intv,
            },
        ),
    ]
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}
    for url, params in urls_try:
        try:
            _respect_coinglass_rate_limit()
            response = requests.get(url, params=params, headers=headers, timeout=12)
            if response.status_code != 200:
                continue
            data = response.json()
            if data.get("code") not in ("0", 0, 200, "200", None):
                continue
            dl = _coinglass_extract_data_list(data)
            if dl:
                logger.info(f"[穩定幣OI歷史✅] {url.split('/')[-1]} interval={intv} 筆數={len(dl)}")
                return dl
        except Exception as e:
            logger.debug(f"穩定幣 OI 請求失敗 {url}: {e}")

    # (B) CG_EP 備援（路徑已與官方 v4 kebab-case 對齊）
    j = _cg_get(CG_EP["oi_agg_stable"], {"symbol": base, "interval": intv, "limit": 48})
    dl = _coinglass_extract_data_list(j)
    if dl:
        logger.info(f"[穩定幣OI歷史✅] oi_agg_stable(interval={intv}) 筆數={len(dl)}")
        return dl

    j2 = _cg_get(CG_EP["oi_agg_history"], {"symbol": base, "interval": intv, "limit": 48})
    dl2 = _coinglass_extract_data_list(j2)
    if dl2:
        logger.info(f"[穩定幣OI歷史✅] aggregated-history（全網合約 OI 備援）interval={intv} 筆數={len(dl2)}")
        return dl2

    logger.warning("[穩定幣OI歷史] 所有端點均未取得有效 K 線列表")
    return None


def fetch_aggregated_coin_margin_oi_history(symbol: str = "BTC", interval: str = "1h") -> Optional[List[Dict]]:
    """幣本位保證金聚合 OI 歷史（與穩定幣路徑對稱，供聰明錢拆分）。"""
    if not CG_API_KEY:
        return None
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    intv = _cg_interval(interval)
    urls_try = [
        (
            f"{CG_API_BASE}/api/futures/open-interest/aggregated-coin-margin-history",
            {"exchange_list": "Binance,Bybit,OKX,Gate", "symbol": base, "interval": intv},
        ),
        (
            f"{CG_API_BASE}/api/futures/open-interest/aggregated-coin-margin-history",
            {"exchange": "Binance", "symbol": base, "interval": intv},
        ),
    ]
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}
    for url, params in urls_try:
        try:
            _respect_coinglass_rate_limit()
            response = requests.get(url, params=params, headers=headers, timeout=12)
            if response.status_code != 200:
                continue
            data = response.json()
            if data.get("code") not in ("0", 0, 200, "200", None):
                continue
            dl = _coinglass_extract_data_list(data)
            if dl:
                logger.info(f"[幣本位OI歷史✅] {url.split('/')[-1]} interval={intv} 筆數={len(dl)}")
                return dl
        except Exception as e:
            logger.debug(f"幣本位 OI 請求失敗 {url}: {e}")
    j = _cg_get(CG_EP["oi_agg_coin"], {"symbol": base, "interval": intv, "limit": 48})
    dl = _coinglass_extract_data_list(j)
    if dl:
        logger.info(f"[幣本位OI歷史✅] ohlc-coin-margin interval={intv} 筆數={len(dl)}")
        return dl
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
        'change_24h': None,
        'change_prev': None,
    }
    # 最後兩個數據點的步長%（API 無時間戳時，供燃料「短線」維度用）
    if len(sorted_data) >= 2:
        try:
            prev_pt = sorted_data[-2]
            prev_mcap = prev_pt.get('marketCap') or prev_pt.get('market_cap') or prev_pt.get('value')
            if prev_mcap and float(prev_mcap) > 0:
                result['change_prev'] = ((float(latest_mcap) - float(prev_mcap)) / float(prev_mcap)) * 100
        except (TypeError, ValueError):
            pass
    
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


def _oi_bar_close(bar: Dict) -> Optional[float]:
    """從單根 OI K 線取出收盤持倉數值。注意：部分端點的 `v` 為成交量而非 OI，`v` 放最後。"""
    if not isinstance(bar, dict):
        return None
    for k in (
        "c", "close",
        "openInterest", "open_interest", "sumOpenInterest", "oi", "openInterestUsd",
        "value",
        "v",  # 少數文檔混用；最後才用，避免誤把成交量當 OI
    ):
        v = bar.get(k)
        if v is None:
            continue
        try:
            f = float(v)
            if f == f and f >= 0:
                return f
        except (TypeError, ValueError):
            pass
    return None


def _oi_bar_ts_ms(bar: Dict) -> int:
    """統一時間戳為毫秒整數（API 可能給秒或毫秒）。"""
    t = bar.get("t") or bar.get("time") or bar.get("timestamp") or 0
    try:
        ti = int(t)
        if ti <= 0:
            return 0
        if ti < 1_000_000_000_000:  # 秒
            return ti * 1000
        return ti
    except (TypeError, ValueError):
        return 0


def calculate_oi_change(data_list: List[Dict]) -> Optional[Dict]:
    """計算 OI K 線序列：最新名目量 + 近似 1H 變化% + 24H 變化%；缺時間戳時用「倒數第二根」近似。"""
    if not data_list or len(data_list) < 2:
        return None

    sorted_data = sorted(data_list, key=_oi_bar_ts_ms)
    latest = sorted_data[-1]
    latest_oi = _oi_bar_close(latest)
    if latest_oi is None or latest_oi <= 0:
        return None

    result: Dict[str, Any] = {
        "latest_oi": float(latest_oi),
        "change_1h": None,
        "change_24h": None,
        "change_prev_bar": None,
    }

    prev = sorted_data[-2]
    prev_oi = _oi_bar_close(prev)
    if prev_oi is not None and prev_oi > 0:
        result["change_prev_bar"] = ((latest_oi - prev_oi) / prev_oi) * 100.0

    now = get_taipei_time()
    one_hour_ago_ts = int((now - timedelta(hours=1)).timestamp() * 1000)
    day_ago_ts = int((now - timedelta(hours=24)).timestamp() * 1000)

    one_hour_data = None
    twenty_four_hours_data = None
    for item in sorted_data:
        ts = _oi_bar_ts_ms(item)
        if ts and ts <= one_hour_ago_ts:
            one_hour_data = item
        if ts and ts <= day_ago_ts:
            twenty_four_hours_data = item

    def _pct_from(base_bar: Optional[Dict]) -> Optional[float]:
        if not base_bar:
            return None
        o = _oi_bar_close(base_bar)
        if o is None or o <= 0:
            return None
        return ((latest_oi - o) / o) * 100.0

    ch1 = _pct_from(one_hour_data)
    ch24 = _pct_from(twenty_four_hours_data)
    # 時間戳不足時：用「較舊一根」近似短線變化，避免整段變成 0% 與 $0
    if ch1 is None and result.get("change_prev_bar") is not None:
        ch1 = result["change_prev_bar"]
    if ch24 is None and len(sorted_data) >= 3:
        ch24 = _pct_from(sorted_data[0])

    result["change_1h"] = ch1
    result["change_24h"] = ch24
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
    """聰明錢 OI 拆分：穩定幣保證金 vs 幣本位保證金。
    CoinGlass OHLC 端點需使用 m15/h1 格式（傳 15m 常回空列表→拆分永遠不可用）。
    """
    base = symbol.upper().replace("USDT", "").replace("-", "").replace("_", "")
    _interval_try = (_cg_interval("15m"), _cg_interval("1h"))

    logger.debug("[SmartMoneyOI] fetch stable/coin OI split symbol=%s" % base)

    def _rows_from(j: Optional[Dict]) -> list:
        if not j:
            return []
        raw = j.get("data")
        if isinstance(raw, list):
            return raw
        if isinstance(raw, dict):
            for k in ("list", "data_list", "history"):
                v = raw.get(k)
                if isinstance(v, list):
                    return v
        return []

    stable_bars, coin_bars = None, None
    for intv in _interval_try:
        if stable_bars and len(stable_bars) >= 2:
            break
        try:
            params_s = {"symbol": base, "interval": intv, "limit": 12}
            j_s = _cg_get(CG_EP["oi_agg_stable"], params_s)
            rows_s = _rows_from(j_s)
            stable_bars = _parse_oi_bars_from_rows(rows_s) if rows_s else None
            if stable_bars and len(stable_bars) >= 2:
                logger.info(f"[SmartMoneyOI] stable OK interval={intv} bars={len(stable_bars)}")
                break
        except Exception as e_s:
            logger.debug("[SmartMoneyOI] stable OI error: " + str(e_s))

    for intv in _interval_try:
        if coin_bars and len(coin_bars) >= 2:
            break
        try:
            params_c = {"symbol": base, "interval": intv, "limit": 12}
            j_c = _cg_get(CG_EP["oi_agg_coin"], params_c)
            rows_c = _rows_from(j_c)
            coin_bars = _parse_oi_bars_from_rows(rows_c) if rows_c else None
            if coin_bars and len(coin_bars) >= 2:
                logger.info(f"[SmartMoneyOI] coin OK interval={intv} bars={len(coin_bars)}")
                break
        except Exception as e_c:
            logger.debug("[SmartMoneyOI] coin OI error: " + str(e_c))

    # 備援：camelCase 端點在部分方案下回空，改與「燃料表」相同之 kebab aggregated-history（與持倉異常鏡同源）
    if not stable_bars or len(stable_bars) < 2:
        dl_s = fetch_aggregated_stablecoin_oi_history(base, "15m")
        if dl_s:
            stable_bars = _parse_oi_bars_from_rows(dl_s)
            if stable_bars and len(stable_bars) >= 2:
                logger.info(f"[SmartMoneyOI] stable kebab 備援 bars={len(stable_bars)}")
    if not coin_bars or len(coin_bars) < 2:
        dl_c = fetch_aggregated_coin_margin_oi_history(base, "15m")
        if dl_c:
            coin_bars = _parse_oi_bars_from_rows(dl_c)
            if coin_bars and len(coin_bars) >= 2:
                logger.info(f"[SmartMoneyOI] coin kebab 備援 bars={len(coin_bars)}")

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
    if oi_15m > 0.22:
        score += 1
    if oi_1h > 0.55:
        score += 1
    if usdt_premium is not None and usdt_premium > 0.05:
        score += 1
    if smart_money is True:
        score += 1
    if smart_money is True and oi_1h > 0.55:
        score += 1
    return score


def _format_oi_notional_billions(latest_oi: Optional[float]) -> str:
    """將聚合 OI 名目（多為 USD）轉成易讀字串；避免 `.0f` 把小於 1M 的四捨五入成 0M。"""
    if latest_oi is None or latest_oi <= 0:
        return "—"
    x = float(latest_oi)
    if x >= 1e9:
        return f"${x / 1e9:.2f}B"
    if x >= 1e6:
        return f"${x / 1e6:.2f}M"
    if x >= 1e3:
        return f"${x / 1e3:.1f}K"
    return f"${x:.0f}"


def _fuel_buying_power_dc_ping_everyone(
    fuel_score: int,
    headline: str,
    smart_money: Optional[bool],
    mcap_1h: float,
    oi_1h_chg: float,
    premium_boost: bool,
) -> bool:
    """
    Discord 是否加 @everyone：
    - 偏多特殊情境：維持中燃料以上(>=4)才提醒，避免一般盤整洗版。
    - 偏空/風險特殊情境：放寬到 >=3 也提醒，避免空方警訊被低估。
    """
    special_short_or_risk = (
        mcap_1h < -0.04
        or (smart_money is False and oi_1h_chg > 1.0)
        or any(
            w in headline
            for w in ("抽離", "過熱", "散戶槓桿", "清洗", "撤退")
        )
    )
    if fuel_score >= 3 and special_short_or_risk:
        return True
    if fuel_score < 4:
        return False
    special_long = (
        fuel_score >= 5
        or smart_money is True
        or premium_boost
        or (mcap_1h > 0.04 and oi_1h_chg > 0.25)
    )
    return bool(special_long or special_short_or_risk)


def _fuel_direction_summary(
    fuel_score: int,
    mcap_1h: float,
    mcap_15m: float,
    oi_15m_chg: float,
    oi_1h_chg: float,
    stable_chg: Optional[float],
    coin_chg: Optional[float],
    smart_money: Optional[bool],
    premium_boost: bool,
    fg_val: Optional[int],
    etf_direction: Optional[str],
    cb_signal: Optional[str],
) -> Tuple[str, str]:
    """
    綜合場外池子、OI、聰明錢／散戶、情緒與機構訊號，給使用者一眼懂的偏多／偏空／中性結論。
    回傳 (標籤, 一句話理由)。
    """
    def _f(x: Any, default: float = 0.0) -> float:
        try:
            return float(x)
        except (TypeError, ValueError):
            return default

    b = 0  # 偏多權重
    r = 0  # 偏空權重

    mh = _f(mcap_1h)
    m15 = _f(mcap_15m)
    o15 = _f(oi_15m_chg)
    o1h = _f(oi_1h_chg)

    if mh > 0.02:
        b += 2
    elif mh < -0.02:
        r += 2

    if m15 > 0.02:
        b += 1
    elif m15 < -0.02:
        r += 1

    if smart_money is True:
        b += 2
    elif smart_money is False:
        r += 2

    if stable_chg is not None:
        sc = _f(stable_chg)
        if sc > 0.08:
            b += 1
        elif sc < -0.08:
            r += 1

    if coin_chg is not None and smart_money is False:
        cc = _f(coin_chg)
        if cc > 0.35:
            r += 1

    if o1h > 0.12:
        b += 1
    elif o1h < -0.12:
        r += 1

    if o15 > 0.12:
        b += 1
    elif o15 < -0.12:
        r += 1

    if premium_boost:
        b += 2

    if fg_val is not None:
        try:
            fv = int(fg_val)
        except (TypeError, ValueError):
            fv = 50
        if fv >= 55:
            b += 1
        elif fv <= 35:
            r += 1

    ed = (etf_direction or "").strip().lower()
    if ed == "inflow":
        b += 2
    elif ed == "outflow":
        r += 1

    cs = (cb_signal or "").strip().lower()
    if cs == "bullish":
        b += 1
    elif cs == "bearish":
        r += 1

    if fuel_score >= 6:
        b += 1
    elif fuel_score <= 2:
        r += 1

    diff = b - r

    def _bear_parts() -> List[str]:
        parts: List[str] = []
        if mh < -0.02:
            parts.append("場外穩定幣池略縮")
        if smart_money is False:
            parts.append("結構偏散戶槓桿")
        if stable_chg is not None and _f(stable_chg) < -0.08:
            parts.append("機構端穩定幣 OI 回落")
        if o1h < -0.12 or o15 < -0.12:
            parts.append("全網合約槓桿在降")
        if fg_val is not None:
            try:
                if int(fg_val) <= 35:
                    parts.append("情緒偏防守（恐懼區）")
            except (TypeError, ValueError):
                pass
        if not parts:
            parts.append("綜合資金與槓桿訊號略偏空")
        return parts[:3]

    def _bull_parts() -> List[str]:
        parts: List[str] = []
        if mh > 0.02:
            parts.append("場外穩定幣池在補")
        if smart_money is True:
            parts.append("結構偏機構買盤（穩定幣 OI）")
        if premium_boost:
            parts.append("USDT 溢價偏高（真實買盤）")
        if o1h > 0.12 or o15 > 0.12:
            parts.append("全網合約槓桿在擴")
        if not parts:
            parts.append("綜合資金與槓桿訊號略偏多")
        return parts[:3]

    if diff >= 2:
        tag = "偏多（環境對風險資產相對友善）"
        why = "、".join(_bull_parts()) + "。"
    elif diff <= -2:
        tag = "偏空（環境偏防守／去槓桿）"
        why = "、".join(_bear_parts()) + "。"
    else:
        tag = "中性觀望（先看區間）"
        why = "場外與場內訊號不同步或變化不大，不宜硬判單邊；等池子或槓桿明顯表態再跟。"

    return tag, why


def buying_power_monitor():
    """【牛市燃料監控】場外穩定幣 + 場內 OI + 情緒／機構輔助指標（CoinGlass 為主）。"""
    logger.info("開始執行牛市燃料監控（CoinGlass 聚合 + 聰明錢拆分）...")
    marketcap_data = fetch_stablecoin_marketcap_history()
    mcap_change = calculate_marketcap_change(marketcap_data) if marketcap_data else {}

    # 升級：同時抓取 15m 與 1h OI
    oi_data_15m = fetch_aggregated_stablecoin_oi_history("BTC", "15m")
    oi_data_1h = fetch_aggregated_stablecoin_oi_history("BTC", "1h")
    oi_change_15m = calculate_oi_change(oi_data_15m) if oi_data_15m else None
    oi_change_1h = calculate_oi_change(oi_data_1h) if oi_data_1h else None
    oi_change_15m = oi_change_15m or {}
    oi_change_1h = oi_change_1h or {}

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
    try:
        mcap_15m = float(mcap_change.get("change_prev") or 0)
    except (TypeError, ValueError):
        mcap_15m = 0.0
    try:
        oi_15m_chg = float(oi_change_15m.get("change_prev_bar") or 0)
    except (TypeError, ValueError):
        oi_15m_chg = 0.0
    oi_1h_chg = (oi_change_1h.get("change_1h") or 0)

    # 「USDT 溢價>0.05%」才視為真實買盤
    premium_boost = (usdt_premium is not None and usdt_premium > 0.05)
    if premium_boost:
        logger.info(f"[牛市燃料] USDT 溢價 {usdt_premium:+.4f}% > 0.05%，加權燃料等級")

    # 積分（升級至 7 分滿，引入聰明錢維度）
    fuel_score = _calc_fuel_score(mcap_15m, mcap_1h, oi_15m_chg, oi_1h_chg, usdt_premium, smart_money)
    # 急跌／池子抽離：給「波動燃料」分（不必 OI 仍為正），避免崩盤永遠卡在低燃料
    try:
        oi_1h_f = float(oi_1h_chg)
    except (TypeError, ValueError):
        oi_1h_f = 0.0
    try:
        if mcap_1h < -0.025:
            fuel_score += 1
        if mcap_1h < -0.04 and abs(oi_1h_f) >= 0.1:
            fuel_score += 1
        if mcap_1h < -0.02 and oi_1h_f > 0.18:
            fuel_score += 1
    except (TypeError, ValueError):
        pass
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
    # 基礎分最高 7，加上情緒／ETF／CB 後可 >7；進度條與顯示統一用 10 格滿分
    FUEL_DISPLAY_MAX = 10
    fuel_bar = _make_fuel_bar(min(fuel_score, FUEL_DISPLAY_MAX), max_score=FUEL_DISPLAY_MAX)

    # 根據積分決定主標籤（白話版，給社群一眼懂）
    if fuel_score >= 6:
        headline = "🔥 錢跟槓桿一起衝（偏香）"
        advice = "該進的錢、該開的倉好像都來了，這種「全車發動」常常是一小段主升的燃料味；有賺記得分批。"
        bar_label = "燃料滿載"
    elif fuel_score >= 5:
        headline = "🚀 今天偏硬：錢跟槓桿都有在動" if not smart_money else "🚀 聰明錢有在買單的感覺"
        advice = (
            "機構那邊穩定幣 OI 有在長，跟著偏多通常比較不心累。"
            if smart_money
            else "場外＋場內一起加油，拉回很多人會當買點；別梭哈、部位自己拿捏。"
        )
        bar_label = "高燃料"
    elif fuel_score >= 4:
        headline = "💰 有感：錢慢慢流進來"
        advice = "還不是暴衝那種，但底下墊子變厚，做多有點底；想上車也別一次滿倉。"
        bar_label = "中燃料"
    elif fuel_score >= 2:
        headline = "➡️ 盤整味比較重"
        advice = "方向還在裝死，先當吃瓜看戲，等大戶表態再跟也不遲。"
        bar_label = "低燃料"
    elif oi_1h_chg > 1.5 and smart_money is False:
        headline = "⚠️ 散戶槓桿堆太兇（小心被洗）"
        advice = "幣本位 OI 衝很快，常常是插針前戲；多單別追太滿，空單也別覺得穩贏。"
        bar_label = "危險燃料"
    elif oi_1h_chg > 1.5:
        headline = "⚠️ 槓桿熱過頭（波動要來了）"
        advice = "只有槓桿在嗨、現貨沒跟時，畫門機率變高，心臟小的先閃一邊。"
        bar_label = "危險燃料"
    elif mcap_1h < -0.05:
        headline = "❄️ 錢在撤退（偏冷）"
        advice = "穩定幣池子在縮，反彈先當逃命波看；想空也要控一下，別當送分題。"
        bar_label = "無燃料"
    else:
        headline = "➡️ 盤整味比較重"
        advice = "方向還在裝死，先當吃瓜看戲，等大戶表態再跟也不遲。"
        bar_label = "低燃料"

    # 推播規則：積分 <3 完全不推播（連文字都不要）；僅在「會對 Discord @everyone」
    # 的同一條件成立時才發 Telegram／Discord（避免低燃料洗版）。
    if fuel_score < 3:
        logger.info("[牛市燃料] 燃料積分=%s < 3，不推播（無文字）", fuel_score)
        return
    _dc_ping = _fuel_buying_power_dc_ping_everyone(
        fuel_score, headline, smart_money, mcap_1h, oi_1h_chg, premium_boost
    )
    if not _dc_ping:
        logger.info(
            "[牛市燃料] 未達 @everyone 條件（積分=%s），不推播",
            fuel_score,
        )
        return

    dir_tag, dir_why = _fuel_direction_summary(
        fuel_score,
        mcap_1h,
        mcap_15m,
        oi_15m_chg,
        oi_1h_chg,
        stable_chg,
        coin_chg,
        smart_money,
        premium_boost,
        fg_val,
        etf_data.get("direction"),
        cb_data.get("signal"),
    )

    lines = []
    lines.append("⛽ *【牛市燃料儀表板】*")
    lines.append(f"🕐 {datetime.now(TAIPEI_TZ).strftime('%H:%M')} 台北｜CoinGlass")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(f"*{headline}*")
    lines.append(f"*🧭 方向結論*：*{dir_tag}*")
    lines.append(f"👉 {dir_why}")
    lines.append(f"燃料條：`{fuel_bar}` {min(fuel_score, FUEL_DISPLAY_MAX)}/{FUEL_DISPLAY_MAX}（{bar_label}）")
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
    lines.append("💵 *場外：穩定幣池子（大概＝有多少彈藥在場邊）*")
    lines.append(f"• 現在大概：`${mcap_val:.2f}B`")
    lines.append(f"• 最近 1 小時：{mcap_emoji} `{mcap_1h:+.3f}%`（正的多半當好事看）")

    # 聰明錢 OI 拆分區塊
    lines.append("")
    lines.append("🧠 *誰在開倉？（粗分機構 vs 散戶槓桿）*")
    if stable_chg is not None:
        _s_emoji = "🟢" if stable_chg > 0.1 else ("🔴" if stable_chg < -0.1 else "🟡")
        lines.append(f"• 穩定幣保證金(機構)：{_s_emoji} `{stable_chg:+.3f}%`")
    else:
        lines.append("• 穩定幣保證金：`暫無法拆分`（API 無回傳或限額；可參考下方全網 OI）")
    if coin_chg is not None:
        _c_emoji = "🟢" if coin_chg > 0.1 else ("🔴" if coin_chg < -0.1 else "🟡")
        lines.append(f"• 幣本位保證金(散戶)：{_c_emoji} `{coin_chg:+.3f}%`")
    else:
        lines.append("• 幣本位保證金：`暫無法拆分`（同上）")
    if smart_money is True:
        lines.append("• 🎯 *偏機構味*：穩定幣保證金開倉比較兇（當「職業盤在買」參考）")
    elif smart_money is False:
        lines.append("• ⚠️ *偏散戶槓桿*：幣本位那邊比較嗨，洗一下很正常，別太戀戰")
    else:
        lines.append("• ❓ 兩邊差不多或資料糊掉，下面全網 OI% 自己對一下")

    lines.append("")
    oi_snap_15m = _format_oi_notional_billions((oi_change_15m or {}).get("latest_oi") if oi_change_15m else None)
    oi_snap_1h = _format_oi_notional_billions((oi_change_1h or {}).get("latest_oi") if oi_change_1h else None)
    oi_emoji_15m = "🔥" if oi_15m_chg > 0 else "❄️"
    oi_emoji_1h = "🔥" if oi_1h_chg > 0 else "❄️"
    lines.append("🎰 *場內：合約 OI（大家槓桿堆多少）*")
    lines.append(
        f"• 短一點（{_cg_interval('15m')}）：名目約 {oi_snap_15m} {oi_emoji_15m} "
        f"這根跟前一根比 `{oi_15m_chg:+.2f}%`"
    )
    lines.append(
        f"• 拉長看（{_cg_interval('1h')}）：名目約 {oi_snap_1h} {oi_emoji_1h} "
        f"變化 `{oi_1h_chg:+.2f}%`"
    )

    # ── 機構資金區塊（Fear&Greed + BTC ETF + Coinbase溢價）──────────
    lines.append("")
    lines.append("🏦 *情緒與美股那邊的錢（參考用）*")
    if fg_val is not None:
        lines.append(f"• 恐懼貪婪：{fg_data.get('emoji','❓')} `{fg_val}` {fg_data.get('label','')}")
    if etf_data.get("label"):
        lines.append(f"• BTC ETF：{etf_data['label']}")
    if etf_data.get("total_assets_usd"):
        lines.append(f"• ETF總資產：`${etf_data['total_assets_usd']/1e9:.1f}B`")
    if cb_data.get("label"):
        lines.append(f"• {cb_data['label']}")
    if not any([fg_val, etf_data.get("label"), cb_data.get("label")]):
        lines.append("• 這塊今天沒撈到資料，略過")

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📌 *一句人話*：{advice}")
    lines.append("_就是給你看錢跟槓桿在幹嘛，不是一串單；自己斟酌部位，賺了請喝手搖。_")

    msg = "\n".join(lines)
    keyboard = {"inline_keyboard": [[{"text": "💰 去 CoinGlass 看圖", "url": "https://www.coinglass.com/zh-TW/pro/futures/OpenInterest"}]]}
    if not jackbot_universal_pre_send_gatekeeper("buying_power_monitor", text=msg):
        return
    send_telegram_message(
        msg,
        TG_THREAD_IDS.get("buying_power_monitor", 246),
        parse_mode="Markdown",
        reply_markup=keyboard,
        discord_force_everyone=True,
    )
    logger.info(
        "[牛市燃料] 推播完成（積分=%s，Discord 鏡像帶 @everyone）",
        fuel_score,
    )


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


def _to_gate_contract(symbol: str) -> str:
    clean = str(symbol).replace("USDT", "").replace("-", "").replace("_", "").upper()
    return f"{clean}_USDT"


def fetch_gate_usdt_contract_bases() -> Set[str]:
    """
    取得 Gate USDT 永續可交易標的（base 集合）。
    用於最終推播前白名單，避免用戶在 Gate 下單時遇到不可交易或流動性極差標的。
    """
    out: Set[str] = set()
    try:
        r = requests.get("https://api.gateio.ws/api/v4/futures/usdt/contracts", timeout=15)
        if r.status_code != 200:
            logger.warning(f"[Gate合約白名單] HTTP {r.status_code}，略過 Gate 白名單過濾")
            return out
        rows = r.json()
        if not isinstance(rows, list):
            return out
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "")
            status = str(row.get("status") or "").lower()
            if not name.endswith("_USDT"):
                continue
            if status and status not in ("trading", "tradable", "online"):
                continue
            base = name[:-5].upper()
            if base:
                out.add(base)
        logger.info(f"[Gate合約白名單✅] 取得 {len(out)} 個 USDT 永續標的")
    except Exception as e:
        logger.warning(f"[Gate合約白名單] 讀取失敗: {e}")
    return out


def fetch_bingx_futures_24h_vol() -> Dict[str, float]:
    """
    Plan B 成交值備援：Gate 永續合約 24h quoteVolume（USDT）批次取得。
    單一 API call 涵蓋 Gate 上市幣種，市場數據端點無需 API Key。
    回傳 {base_symbol: vol_usdt_24h}，例如 {"BTC": 2.3e10, "ETH": 5e9}。
    失敗時靜默回傳空 dict，不影響主流程。
    """
    try:
        r = requests.get("https://api.gateio.ws/api/v4/futures/usdt/tickers", timeout=10)
        if r.status_code != 200:
            logger.warning(f"[備援B-Gate] HTTP {r.status_code}，跳過")
            return {}
        data = r.json()
        if not isinstance(data, list):
            return {}
        result: Dict[str, float] = {}
        for item in data:
            sym = str(item.get("contract") or "")
            if not sym.endswith("_USDT"):
                continue
            base = sym[:-5]
            try:
                vol = float(item.get("volume_24h_quote") or item.get("volume_24h_usd") or 0)
                if vol > 0:
                    result[base] = vol
                    if base.startswith("1000000"):
                        result.setdefault("1M" + base[7:], vol)
                    elif base.startswith("10000"):
                        result.setdefault("1W" + base[5:], vol)
                    elif base.startswith("1000"):
                        result.setdefault("1K" + base[4:], vol)
            except (TypeError, ValueError):
                pass
        logger.info(f"[備援B-Gate✅] 取得 {len(result)} 幣種 24h USDT 成交值")
        return result
    except Exception as e:
        logger.warning(f"[備援B-Gate] 失敗: {type(e).__name__}: {e}")
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
    """Gate 24h 漲跌幅：用 1h K 線取 24h 前開盤與最新收盤計算（CoinGlass 無資料時 fallback）。"""
    clean = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    for sym_fmt in ([preferred_symbol] if preferred_symbol else []) + [f"{clean}_USDT", f"1000{clean}_USDT"]:
        if not sym_fmt:
            continue
        try:
            r = requests.get(
                "https://api.gateio.ws/api/v4/futures/usdt/candlesticks",
                params={"contract": sym_fmt, "interval": "1h", "limit": 25},
                timeout=5
            )
            time.sleep(0.08)
            if r.status_code != 200:
                continue
            data = r.json()
            if not isinstance(data, list) or len(data) < 2:
                continue
            first_open = float(data[0].get("o") or data[0].get("open") or 0)
            last_close = float(data[-1].get("c") or data[-1].get("close") or 0)
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
    """通用 OI K 線收盤值（與 _oi_bar_close 同一套欄位，避免把 v=成交量當 OI）。"""
    sorted_rows = sorted(rows, key=lambda x: _oi_bar_ts_ms(x) if isinstance(x, dict) else 0)
    oi_bars: List[float] = []
    for row in sorted_rows:
        if not isinstance(row, dict):
            continue
        v = _oi_bar_close(row)
        if v is not None:
            oi_bars.append(v)
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


def extract_oi_change_1h(coin: Dict) -> Optional[float]:
    """提取 1H OI 變化%，優先讀扁平欄位；若無則讀 `_raw_cg`（coins-markets 解析後完整 payload）。"""
    _OI_1H_KEYS = (
        "open_interest_change_percent_1h",
        "openInterestChangePercent1h",
        "oi_change_percent_1h",
        "oiChangePercent1h",
        "oiChange1h",
        "open_interest_change_1h",
        "openInterestChange1h",
    )

    def _read_from(d: Dict) -> Optional[float]:
        for key in _OI_1H_KEYS:
            v = d.get(key)
            if isinstance(v, (int, float)) and v == v:
                return float(v)
            if isinstance(v, str) and v.strip():
                try:
                    p = float(v.strip())
                    if p == p:
                        return p
                except ValueError:
                    pass
        return None

    hit = _read_from(coin)
    if hit is not None:
        return hit
    raw = coin.get("_raw_cg")
    if isinstance(raw, dict):
        return _read_from(raw)
    return None


def format_btc_macro_1h_plain_lines(
    price_change_1h: Optional[float],
    oi_change_1h: Optional[float],
) -> List[str]:
    """
    用「1h BTC 漲跌 + 1h 全網 OI 變化」合成白話（單一時間切面，方便讀者一眼看懂大盤氛圍）。
    註：非嚴格多空因果，僅作市場情緒／參與度參考。
    """
    P_TH = 0.12
    O_TH = 0.8

    def _tri(v: Optional[float], th: float) -> int:
        if v is None:
            return 0
        try:
            vf = float(v)
        except (TypeError, ValueError):
            return 0
        if vf > th:
            return 1
        if vf < -th:
            return -1
        return 0

    if price_change_1h is None and oi_change_1h is None:
        return []

    # 僅單邊有資料：短句交代，避免空白
    if price_change_1h is not None and oi_change_1h is None:
        px = float(price_change_1h)
        tp = _tri(px, P_TH)
        tone = "短線偏多一點" if tp > 0 else "短線偏空一點" if tp < 0 else "大致橫著走"
        return [
            f"• *BTC 大盤 1h*：價格 `{px:+.2f}%`（{tone}）。"
            " OI 暫無資料——槓桿籌碼面略過。"
        ]
    if price_change_1h is None and oi_change_1h is not None:
        oi = float(oi_change_1h)
        return [
            f"• *BTC 大盤 1h*：價格暫缺；全網 OI `{oi:+.2f}%`（僅供槓桿參與度參考）。"
        ]

    px = float(price_change_1h)  # type: ignore[arg-type]
    oi = float(oi_change_1h)  # type: ignore[arg-type]
    p = _tri(px, P_TH)
    o = _tri(oi, O_TH)

    table: Dict[Tuple[int, int], str] = {
        (1, 1): "漲、全網槓桿也在變多——多方還敢加碼，偏多氛圍延續感較強。",
        (1, 0): "有漲、但槓桿沒明顯跟進——像『溫度有上來、火力還沒全開』。",
        (1, -1): "價格在上、槓桿反而在收——不少人逢高減倉，續漲要保守點看。",
        (0, 1): "價格橫向、槓桿變多——籌碼在堆，等方向出來。",
        (0, 0): "價格與 OI 變化都不大——大盤像在等小時級表態。",
        (0, -1): "盤整中去槓桿——市場先縮倉，波動有時會接著放大。",
        (-1, 1): "價格在跌、OI 卻變多——常見空方加碼或多方硬扛，波動容易放大。",
        (-1, 0): "價格走弱、OI 幾乎沒動——偏空一點，還沒看到明顯加倉對賭。",
        (-1, -1): (
            "價格在跌、OI 也跟著收——比較像多頭撤退、市場變冷，"
            "氛圍偏『由熱轉冷／多轉空』一點。"
        ),
    }
    body = table.get((p, o), "價格與槓桿籌碼自行交叉參考即可。")

    line = (
        f"• *BTC 大盤 1h*：價格 `{px:+.2f}%`｜未平倉 OI `{oi:+.2f}%`。"
        f"{body}"
    )
    return [line]


# ── CoinGlass 全市場 ATR list（/api/futures/avg-true-range/list）────────────────
# 文件：https://docs.coinglass.com/reference/futures-avg-true-range-list
# Standard+ 方案；Startup 以下可能 403，自動降級為 indicators/avg-true-range 單幣查詢。
_cg_atr_list_cache: Dict[str, Any] = {"ts": 0.0, "by_base": {}}
_CG_ATR_LIST_TTL = 90.0
_cg_atr_list_plan_logged: bool = False


def _cg_atr_list_column(interval: str) -> str:
    """對應 list API 回傳欄位 avg_true_range_{1m|5m|15m|...}。"""
    s = (interval or "15m").strip().lower()
    alias = {
        "m1": "1m", "m3": "3m", "m5": "5m", "m15": "15m", "m30": "30m",
        "h1": "1h", "h4": "4h", "d1": "1d", "w1": "1w",
    }
    s = alias.get(s, s)
    if s not in ("1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"):
        s = "15m"
    return f"avg_true_range_{s}"


def _refresh_cg_atr_list_cache() -> None:
    """拉取全市場 ATR list，寫入 _cg_atr_list_cache（by_base → 欄位名→值）。"""
    global _cg_atr_list_cache, _cg_atr_list_plan_logged
    if not CG_API_KEY:
        return
    now = time.time()
    if (
        now - float(_cg_atr_list_cache.get("ts") or 0) < _CG_ATR_LIST_TTL
        and _cg_atr_list_cache.get("by_base")
    ):
        return
    ep = CG_EP.get("atr_list", "/api/futures/avg-true-range/list")
    try:
        _respect_coinglass_rate_limit()
        r = requests.get(
            f"{CG_API_BASE}{ep}",
            headers={"CG-API-KEY": CG_API_KEY, "accept": "application/json"},
            timeout=18,
        )
        if r.status_code in (401, 403):
            if not _cg_atr_list_plan_logged:
                logger.info(
                    "[ATR批次] list HTTP %s（多為方案不含 avg-true-range/list），"
                    "ATR 改走 indicators/avg-true-range 單幣",
                    r.status_code,
                )
                _cg_atr_list_plan_logged = True
            _cg_atr_list_cache = {"ts": now, "by_base": {}}
            return
        if r.status_code != 200:
            logger.debug("[ATR批次] list HTTP %s", r.status_code)
            return
        j = r.json()
        if j.get("code") not in (0, "0", 200, "200", None):
            logger.debug("[ATR批次] list code=%s msg=%s", j.get("code"), j.get("msg"))
            return
        raw = j.get("data") or j.get("list") or []
        by_base: Dict[str, Dict[str, float]] = {}
        if not isinstance(raw, list):
            _cg_atr_list_cache = {"ts": now, "by_base": {}}
            return
        for item in raw:
            if not isinstance(item, dict):
                continue
            sym_raw = item.get("symbol") or item.get("coin") or item.get("base") or ""
            sym = str(sym_raw).replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
            if not sym:
                continue
            row: Dict[str, float] = {}
            for k, v in item.items():
                if not isinstance(k, str) or "avg_true_range" not in k.lower():
                    continue
                try:
                    fv = float(v)
                    if fv > 0:
                        row[k] = fv
                except (TypeError, ValueError):
                    continue
            if row:
                by_base[sym] = row
        logger.info("[ATR批次] list 載入 %d 幣種（%s）", len(by_base), ep)
        _cg_atr_list_cache = {"ts": now, "by_base": by_base}
    except Exception as e:
        logger.debug("[ATR批次] list 異常: %s", e)


def _get_atr_from_coinglass_list(base: str, interval: str) -> Optional[float]:
    """從 list 快取取單幣 ATR；無則 None（由上層改走 indicators）。"""
    _refresh_cg_atr_list_cache()
    col = _cg_atr_list_column(interval)
    by_base = _cg_atr_list_cache.get("by_base") or {}
    row = by_base.get(base)
    if not row and base.startswith("1000"):
        row = by_base.get(base[4:])
    if not row:
        return None
    if col in row:
        return float(row[col])
    col_l = col.lower()
    for k, v in row.items():
        if isinstance(k, str) and k.lower() == col_l:
            return float(v)
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
    - ATR：優先 /api/futures/avg-true-range/list（全市場批次），無資料再呼叫
      /api/futures/indicators/avg-true-range（單幣種）。
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
        list_atr = _get_atr_from_coinglass_list(base, interval)
        if list_atr is not None and list_atr > 0:
            return float(list_atr)
        tries = [("Binance", symbol_param)]
    else:
        tries = [("Binance", symbol_param), ("Gate", symbol_param), ("Gate", base)]
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
    直接從 Gate API 取得該幣種資金費率。若傳入 preferred_symbol（來自 contracts），優先使用。
    """
    clean = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    try_symbols = [preferred_symbol] if preferred_symbol else []
    if preferred_symbol and "USDC" in preferred_symbol.upper():
        try_symbols.append(preferred_symbol.upper().replace("-USDC", "-USDT"))
    try_symbols += [f"{clean}_USDT", f"1000{clean}_USDT"]
    try_symbols = list(dict.fromkeys(try_symbols))  # 去重且保留順序
    base_url = "https://api.gateio.ws/api/v4"
    for sym_param in try_symbols:
        try:
            r = requests.get(f"{base_url}/futures/usdt/tickers", params={"contract": sym_param}, timeout=5)
            time.sleep(0.1)
            if r.status_code != 200:
                continue
            j = r.json()
            if not isinstance(j, list) or not j:
                continue
            data = j[0]
            if isinstance(data, dict):
                rate = data.get("funding_rate")
                if rate is not None:
                    return float(rate)
            if isinstance(data, (int, float, str)):
                return float(data)
        except Exception:
            continue
    for sym_param in try_symbols:
        try:
            r = requests.get(
                f"{base_url}/futures/usdt/funding_rate",
                params={"contract": sym_param, "limit": 2},
                timeout=5,
            )
            time.sleep(0.1)
            if r.status_code != 200:
                continue
            data = r.json()
            if isinstance(data, list) and data:
                last = data[0] if data else {}
                if isinstance(last, dict):
                    rate = last.get("r")
                    if rate is not None:
                        return float(rate)
        except Exception:
            continue
    return None


def _fetch_bingx_current_price(symbol: str, preferred_symbol: Optional[str] = None) -> Optional[float]:
    """從 Gate futures ticker 取得即時最新價（相容舊呼叫，只回傳價格）。"""
    snap = _fetch_bingx_ticker_snapshot(symbol, preferred_symbol)
    return snap.get("price") if snap else None


def _fetch_bingx_ticker_snapshot(symbol: str, preferred_symbol: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    從 Gate futures ticker 一次取得：最新價 + 24h 成交額(USDT)。
    回傳 {"price": float, "volume_usd": float or None}，失敗回傳 None。
    """
    clean = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    try_symbols = [preferred_symbol] if preferred_symbol else []
    try_symbols += [f"{clean}_USDT", f"1000{clean}_USDT"]
    try_symbols = list(dict.fromkeys([s for s in try_symbols if s]))
    base_url = "https://api.gateio.ws/api/v4"
    for sym_param in try_symbols:
        try:
            r = requests.get(
                f"{base_url}/futures/usdt/tickers",
                params={"contract": sym_param},
                timeout=5
            )
            time.sleep(0.08)
            if r.status_code != 200:
                continue
            j = r.json()
            if not isinstance(j, list) or not j:
                continue
            data = j[0]
            if not isinstance(data, dict):
                continue
            price = data.get("last") or data.get("mark_price") or data.get("index_price")
            if price is None:
                continue
            price_f = float(price)
            volume_usd = None
            qv = data.get("volume_24h_quote") or data.get("volume_24h_usd") or data.get("volume_24h")
            if qv is not None:
                try:
                    volume_usd = float(qv)
                except (TypeError, ValueError):
                    pass
            # 若成交額為 0 或缺失，用原始 symbol 再試一次（避免 1000PEPE 等誤判）
            raw_sym = symbol.strip()
            if (volume_usd is None or volume_usd == 0) and raw_sym and raw_sym not in try_symbols:
                try_sym = raw_sym if ("_" in raw_sym or "USDT" in raw_sym.upper()) else f"{raw_sym}_USDT"
                try:
                    time.sleep(0.08)
                    r2 = requests.get(
                        f"{base_url}/futures/usdt/tickers",
                        params={"contract": try_sym},
                        timeout=5
                    )
                    if r2.status_code == 200:
                        j2 = r2.json()
                        if isinstance(j2, list) and j2 and isinstance(j2[0], dict):
                            qv2 = j2[0].get("volume_24h_quote") or j2[0].get("volume_24h_usd") or j2[0].get("volume_24h")
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
    # 使用官方 v4：/api/futures/funding-rate/exchange-list（已寫入 CG_EP["fr_exchange_list"]）
    fr_ep_candidates = [CG_EP["fr_exchange_list"]]
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
            # 交易所優先順序：Binance > Bybit > OKX > Gate > Bitget（量大流動性佳的排前）
            _EXCHANGE_PRIORITY = ["Binance", "Bybit", "OKX", "Gate", "Bitget"]

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
            logger.info(f"[資金費率✅] 成功解析 {len(out)} 幣種（CoinGlass exchange-list，Binance>Bybit>OKX>Gate>Bitget 優先）")
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
    """恐懼貪婪指數。與【長線財富週期】共用同一解析：`fetch_latest_fear_greed`（含 data_list 整數序列）。
    舊版曾用 `_cg_get(..., limit=1)` 只讀 `value` 欄，與實際 API 結構不一致→誤判無資料。
    """
    cache_key = "fear_greed_index"
    now = time.time()
    if cache_key in _flow_cache:
        val, ts = _flow_cache[cache_key]
        if now - ts < 1800:
            return val if val else {}

    logger.debug("[恐懼貪婪] 與長線週期同源：fetch_latest_fear_greed")
    empty = {"value": None, "label": "N/A", "emoji": "❓", "signal": "neutral"}
    try:
        fg_val = fetch_latest_fear_greed()
        if fg_val is None:
            # 解析失敗不寫入快取，避免誤快取空包 30 分鐘（與長線週期不同路時已發生）
            return empty

        # 標準化標籤（與原邏輯相同）
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

        result = {"value": fg_val, "label": label, "emoji": emoji, "signal": signal, "score": fg_val}
        logger.info(f"[恐懼貪婪✅] 當前指數={fg_val} {emoji} {label}")
        _flow_cache[cache_key] = (result, now)
        return result
    except Exception as e:
        logger.debug(f"[恐懼貪婪] 異常: {e}")
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

    # EMA20（同時保留逐根序列，供回踩位計算）
    ema20_close = None
    ema20_series: list = []   # index 對齊 closes[period:]
    period = 20
    alpha = 2.0 / (period + 1)
    ema = float(np.mean(closes[:period]))
    for i in range(period, len(closes)):
        ema = alpha * float(closes[i]) + (1.0 - alpha) * ema
        ema20_series.append(ema)
    ema20_close = ema
    ema100_close = None
    ema100_full: list = [None] * len(closes)
    if len(closes) >= 100:
        try:
            _ema100_series = pd.Series(closes, dtype=float).ewm(span=100, adjust=False).mean()
            ema100_close = float(_ema100_series.iloc[-1])
            ema100_full = _ema100_series.tolist()
        except Exception:
            ema100_close = None
    # 還原成與 closes 等長的完整序列（前 period 根填 None）
    ema20_full = [None] * period + ema20_series

    # VWAP_2h（最近 8 根 15m K 線）與收盤價相對 VWAP 的標準差（供 TP2 軌道用）
    vwap_2h = None
    vwap_std = None
    vwap_vol_weighted = True
    if len(closes) >= 8 and len(volumes) >= 8:
        uc, uh, ul, uv = closes[-8:], highs[-8:], lows[-8:], volumes[-8:]
        typical = [(uh[i] + ul[i] + uc[i]) / 3.0 for i in range(len(uc))]
        total_vol = sum(uv)
        if total_vol > 0:
            vwap_2h = sum(typical[i] * uv[i] for i in range(len(typical))) / total_vol
            vwap_vol_weighted = True
            logger.info(f"[指標計算] {clean}: VWAP_2h 使用最近 8 根 K 線成交量加權 (典型價 H+L+C/3)")
        else:
            vwap_2h = sum(typical) / len(typical)
            vwap_vol_weighted = False
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
        out["vwap_2h_volume_weighted"] = bool(vwap_vol_weighted)
    if vwap_std is not None:
        out["vwap_std"] = vwap_std
    if ema20_close is not None:
        out["ema20_close"] = ema20_close
    if ema100_close is not None:
        out["ema100_close"] = ema100_close
    if len(highs) >= 8:
        out["recent_high_2h"] = max(highs[-8:])
        out["recent_low_2h"] = min(lows[-8:])
    if len(lows) >= 4:
        out["pre_breakout_low"] = min(lows[-4:-1])
    if len(highs) >= 4:
        out["pre_breakout_high"] = max(highs[-4:-1])

    # ── EMA20/EMA100 回踩結構低/高：往回最多掃 30 根，抓最近一次回踩位
    # 「市場驗證過 EMA20 守住的最低點」= 比靜態 EMA20-pad 更精準的 SL 錨點
    _scan_end = len(closes) - 1           # 排除訊號 K 線本身（最後一根）
    _scan_start = max(period, _scan_end - 30)
    ema20_touch_low = None   # 供做多 SL 用
    ema20_touch_high = None  # 供做空 SL 用
    ema100_touch_low = None
    ema100_touch_high = None
    for _i in range(_scan_end - 1, _scan_start - 1, -1):
        _ev = ema20_full[_i]
        if _ev is None:
            _ev = None
        _ev100 = ema100_full[_i] if _i < len(ema100_full) else None
        try:
            _lv = float(lows[_i])
            _hv = float(highs[_i])
        except (TypeError, ValueError):
            continue
        if _ev is not None:
            _ev = float(_ev)
            if ema20_touch_low is None and _lv <= _ev * 1.015:
                ema20_touch_low = _lv
            if ema20_touch_high is None and _hv >= _ev * 0.985:
                ema20_touch_high = _hv
        if _ev100 is not None:
            _ev100 = float(_ev100)
            if ema100_touch_low is None and _lv <= _ev100 * 1.02:
                ema100_touch_low = _lv
            if ema100_touch_high is None and _hv >= _ev100 * 0.98:
                ema100_touch_high = _hv
        if (
            ema20_touch_low is not None and ema20_touch_high is not None
            and ema100_touch_low is not None and ema100_touch_high is not None
        ):
            break
    if ema20_touch_low is not None:
        out["ema20_touch_low"] = ema20_touch_low
    if ema20_touch_high is not None:
        out["ema20_touch_high"] = ema20_touch_high
    if ema100_touch_low is not None:
        out["ema100_touch_low"] = ema100_touch_low
    if ema100_touch_high is not None:
        out["ema100_touch_high"] = ema100_touch_high

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
                _finalize_tech_after_klines(result, clean, interval)
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
                _finalize_tech_after_klines(result, clean, interval)
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
    Gate 永續 K 線（免簽名），作為最後 fallback。
    覆蓋只在 Gate 上線、其他大所未上的山寨幣，同樣帶 volume。
    """
    clean = symbol_base.replace("USDT", "").replace("-", "").replace("_", "").upper()
    sym_pair = f"{clean}_USDT"
    try:
        r = requests.get(
            "https://api.gateio.ws/api/v4/futures/usdt/candlesticks",
            params={"contract": sym_pair, "interval": interval, "limit": limit},
            timeout=5,
        )
        if r.status_code != 200:
            return None
        raw = r.json()
        if not isinstance(raw, list) or len(raw) < 20:
            return None
        opens, highs, lows, closes, volumes = [], [], [], [], []
        for bar in raw:
            try:
                if isinstance(bar, dict):
                    opens.append(float(bar.get("o") or 0))
                    highs.append(float(bar.get("h") or 0))
                    lows.append(float(bar.get("l") or 0))
                    closes.append(float(bar.get("c") or 0))
                    volumes.append(float(bar.get("v") or 0))
                elif isinstance(bar, (list, tuple)) and len(bar) >= 6:
                    opens.append(float(bar[1]))
                    highs.append(float(bar[2]))
                    lows.append(float(bar[3]))
                    closes.append(float(bar[4]))
                    volumes.append(float(bar[5]))
            except (IndexError, TypeError, ValueError, AttributeError):
                pass
        if len(closes) < 20:
            return None
        result = _calc_indicators_from_ohlcv(
            opens, highs, lows, closes, volumes, clean, "Gate-Futures", sym_pair
        )
        if result:
            result["source"] = "Gate-Futures"
            _finalize_tech_after_klines(result, clean, interval)
            logger.info(
                f"[Gate-Futures✅] {clean}: {sym_pair} {interval} {len(closes)} 根（含 volume）"
            )
            return result
    except Exception as e:
        logger.debug(f"[Gate-Futures] {clean}/{sym_pair} 異常: {e}")
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
      3. CoinGlass 代理 OKX/Gate/Bitget      → 無 volume，但覆蓋剩餘冷門幣種
      4. Gate 永續直連（免 Key，有 volume）   → 最終 fallback
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
    exchanges_to_try = ["OKX", "Bybit", "Gate", "Bitget"]
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
                    _finalize_tech_after_klines(result, clean, interval)
                    return result
            except Exception as e:
                logger.debug(f"[CG K線] {clean}/{exchange}/{sym_pair} 異常: {e}")
                continue

    # ── Step 4: Gate 永續直連（最終 fallback，有 volume）────────────────
    _bingx = _try_bingx_spot_klines_direct(clean, interval, limit)
    if _bingx:
        return _bingx

    logger.warning(
        f"[CG K線] {clean}: 所有來源均無法取得足夠 K 線"
        f"（Binance直連 + Bybit直連 + CoinGlass/{exchanges_to_try} + Gate永續）"
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
# MTF_VOLUME_MIN_USD 於 _env_float 定義後初始化（預設 9.9M USD）

# ── OI 扳機門檻（動態分層，依幣種流動性調整）───────────────────────────
# 微調版：改為「30m 主判斷 + 1H 保險閥」，避免訊號過少與過度滯後
OI_THRESHOLD_MAIN   = 2.4           # 主流幣 1H 參考門檻（原 3.0）
OI_THRESHOLD_HIGH_LIQ = 4.0         # 高流動山寨（原 5.0）
OI_THRESHOLD_SMALL  = 5.8           # 其他小幣種（原 7.0）
HIGH_LIQ_VOLUME_USD = 50_000_000   # 24h 成交值 > 50M 視為高流動性
OI_THRESHOLD_1H     = 5.0          # 向後相容預設值（實際由 _get_oi_threshold_for_item 動態決定）
OI_THRESHOLD_30M_RATIO = 0.7        # 30m 主判斷門檻 = 1H門檻 × 0.7
OI_THRESHOLD_1H_GUARD_RATIO = 0.5   # 1H 保險閥（低於 1H門檻一半才視為動能不足）
PRICE_THRESHOLD_1H  = 1.5           # 1H 價格扳機門檻

# ── 風報比：1R = |進場價 − 止損價|；TP 為 1R 的倍數（與推播 R 標示一致）──────
def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        return default


MTF_VOLUME_MIN_USD = int(max(100_000, round(_env_float("MTF_VOLUME_MIN_USD", 9_900_000))))  # 預設 9.9M

SNIPER_FAST_MODE = os.getenv("SNIPER_FAST_MODE", "").strip().lower() in ("1", "true", "yes", "on")
_default_tp1 = 1.2 if SNIPER_FAST_MODE else 1.5
_default_tp2 = 2.4 if SNIPER_FAST_MODE else 3.0
_default_min_sl = 0.008 if SNIPER_FAST_MODE else 0.015

TP1_R_MULTIPLIER = max(1.0, _env_float("SNIPER_TP1_R", _default_tp1))   # TP1 至少 1R
TP2_R_MULTIPLIER = max(TP1_R_MULTIPLIER + 0.5, _env_float("SNIPER_TP2_R", _default_tp2))   # TP2 必須高於 TP1
SL_R_LABEL = 1.0        # 推播顯示用：止損標為 -1.0R（1R = 進場到 SL 的距離）
MIN_SL_PERCENT = _env_float("SNIPER_MIN_SL_PCT", _default_min_sl)  # 快進快出建議 0.006~0.010
MAX_SL_PERCENT = _env_float("SNIPER_MAX_SL_PCT", 0.055)  # 過寬止損上限（預設 5.5%），由 EMA20/EMA100（15m）優先收斂
SL_EMA_GUARD_BUFFER_ATR = _env_float("SNIPER_SL_EMA_GUARD_BUFFER_ATR", 0.12)  # EMA 防守位外再留一點呼吸空間
SL_TOUCH_BUFFER_ATR = _env_float("SNIPER_SL_TOUCH_BUFFER_ATR", 0.08)  # EMA 回踩點位防守緩衝
MIN_TP1_R_FOR_PUSH = max(1.0, _env_float("SNIPER_MIN_TP1_R_FOR_PUSH", 1.0))
MAX_MARKET_VWAP_GAP_ATR = _env_float("SNIPER_MAX_MARKET_VWAP_GAP_ATR", 1.5)    # 防追價：與 VWAP 偏離最多 1.5*ATR
MARKET_ENTRY_ZONE_ATR = _env_float("SNIPER_ENTRY_ZONE_ATR", 0.2)                 # 市價可進場區：Entry ± 0.2*ATR
MIN_SL_ATR_MULTIPLIER = _env_float("SNIPER_MIN_SL_ATR", 1.0)                     # 最低 SL 距離：至少 1.0*ATR
# EMA 回踩止損：允許比 SNIPER_MAX_SL_PCT 更寬，否則瘋狗幣「明顯回踩高」永遠進不了 if（例：1.528 vs 進場 1.28）
SNIPER_TOUCH_MAX_SL_ATR = max(1.5, _env_float("SNIPER_TOUCH_MAX_SL_ATR", 3.5))
PENDING_PUMP_ATR_MULTIPLIER = _env_float("SNIPER_PENDING_PUMP_ATR", 0.5)         # 已噴發待辦池判定
# Tier2 推播至少需籌碼陷阱步數（0～3）；預設 1＝崩盤/弱共振日仍可有觀察單（設 2 恢復較嚴）
SNIPER_TIER2_MIN_TRAP_STEPS = int(max(0, min(3, round(_env_float("SNIPER_TIER2_MIN_TRAP_STEPS", 1)))))
PENDING_TTL_HOURS = _env_float("SNIPER_PENDING_TTL_HOURS", 4.0)                  # 待辦訊號最長存活
# 批次版 Anchored VWAP：5m 爆量 + OI 變化（視窗內最大階梯）→ 錨點起算加權均價；無 OI 時可降級為僅爆量
SNIPER_ANCHOR_ENABLED = os.getenv("SNIPER_ANCHOR_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on")
SNIPER_ANCHOR_VOL_SPIKE_RATIO = _env_float("SNIPER_ANCHOR_VOL_SPIKE_RATIO", 2.2)  # 當根 volume ≥ 近 M 根均量×此倍數
SNIPER_ANCHOR_OI_STEP_PCT = _env_float("SNIPER_ANCHOR_OI_STEP_PCT", 2.5)  # 與視窗內任一前棒 OI 比，|Δ|/ref ≥ %
SNIPER_ANCHOR_OI_LOOKBACK_BARS = int(max(2, round(_env_float("SNIPER_ANCHOR_OI_LOOKBACK_BARS", 6))))  # 最多回看幾根 5m 算 OI 變化
SNIPER_ANCHOR_LOOKBACK_BARS = int(max(24, round(_env_float("SNIPER_ANCHOR_LOOKBACK_BARS", 72))))   # 5m 根數（預設 6h）
SNIPER_ANCHOR_VOL_BASELINE_BARS = int(max(3, round(_env_float("SNIPER_ANCHOR_VOL_BASELINE_BARS", 10))))
TP1_EXIT_RATIO = 0.50
TP2_EXIT_RATIO = 0.50
# TP2 箱體等幅（15m）：箱頂/箱底 ± 箱高；與原 TP2_R 取「多=max／空=min」，且保證比 TP1 更遠
SNIPER_TP2_BOX_ENABLED = os.getenv("SNIPER_TP2_BOX", "1").strip().lower() in ("1", "true", "yes", "on")
SNIPER_TP2_BOX_FETCH_LIMIT = int(max(24, round(_env_float("SNIPER_TP2_BOX_FETCH_LIMIT", 48))))
SNIPER_TP2_BOX_LOOKBACK_BARS = int(max(16, round(_env_float("SNIPER_TP2_BOX_LOOKBACK", 32))))
SNIPER_TP2_BOX_MIN_HEIGHT_ATR = _env_float("SNIPER_TP2_BOX_MIN_H_ATR", 0.35)
SNIPER_TP2_BOX_MIN_HEIGHT_R = _env_float("SNIPER_TP2_BOX_MIN_H_R", 0.12)
SNIPER_TP2_BOX_MIN_HEIGHT_PCT = _env_float("SNIPER_TP2_BOX_MIN_H_PCT", 0.12)  # 箱高 ≥ 現價×此%
SNIPER_TP2_MAX_R = max(TP2_R_MULTIPLIER, _env_float("SNIPER_TP2_MAX_R", 6.0))
# 綜合評分低於此不分級推播（S / A / R 皆不推）
MIN_SIGNAL_PUSH_SCORE = 74          # 平衡：過高會配合「車已發動」壓分誤殺陡坡結構單
# Tier2（觀察名單）略降底分，避免崩盤日僅剩觀察單卻全日無推播
MIN_SIGNAL_PUSH_SCORE_TIER2 = int(round(_env_float("MIN_SIGNAL_PUSH_SCORE_TIER2", 66)))
# 逆勢 R 級預設略嚴；但「機構成交 + 平倉浪摸頭/摸底」可用 MIN_R_STRUCT_TOUCH_SCORE 放行（見下方）
MIN_R_SIGNAL_PUSH_SCORE = int(round(_env_float("MIN_R_SIGNAL_PUSH_SCORE", 70)))  # 逆勢 R；測試期預設略降
# 非「完美回踩」的潛在訊號（signal_version=potential 且非 pullback）須達此分
MIN_WEAK_POTENTIAL_PUSH_SCORE = 78
# 1H 多平/空回補 + MTF≥3 + 成交值達標：逆勢 R 允許的最低分（避免 SIREN 類被 76 線砍成 B）
MIN_R_STRUCT_TOUCH_SCORE = 64
MIN_R_OI30_ABS = _env_float("MIN_R_OI30_ABS", 3.5)  # R 級至少要有足夠 30m OI 力道
MIN_R_TRAP_STEPS = int(max(1, min(3, round(_env_float("MIN_R_TRAP_STEPS", 2)))))  # R 級最少陷阱步數
SNIPER_STRUCT_MEGA_LIQUIDITY_USD = float(
    _env_float("SNIPER_STRUCT_MEGA_LIQUIDITY_USD", 80_000_000)
)  # 預設 8000 萬 USD 等值成交額
# S 級至少要有此 24h 成交額（USDT），避免「7.9M 仍顯示 S」與 UI 流動性警告打架
SNIPER_MIN_VOL_USD_FOR_S = _env_float("SNIPER_MIN_VOL_USD_FOR_S", 15_000_000)


def _s_grade_brief_to_a(brief: str) -> str:
    """將 _calc_signal_grade 產生的 S 級 brief 改為 A 級用語（不重算分數）。"""
    br = brief
    if "🏆 *S 級*" in br:
        br = br.replace("🏆 *S 級*", "🥇 *A 級*", 1)
    if "訊號極強・順勢" in br:
        br = br.replace("訊號極強・順勢", "訊號強", 1)
    elif "訊號極強" in br:
        br = br.replace("訊號極強", "訊號強", 1)
    return br


def _apply_sniper_s_grade_guards(
    grade: str, brief: str, x: dict, sym_base: str
) -> Tuple[str, str]:
    """
    S 級額外守門：
    - 24h 成交額未達門檻 → 降 A（與「流動性偏低」標籤一致）
    - 待辦池價格觸發推播 → 降 A（payload 為較早掃描快照，避免過度樂觀 S）
    可用 SNIPER_MIN_VOL_USD_FOR_S、SNIPER_PENDING_DOWNGRADE_S=0 覆寫。
    """
    if grade != "S":
        return grade, brief
    try:
        vol_u = float(x.get("volume_usd") or x.get("_cg_volume_usd") or 0)
    except (TypeError, ValueError):
        vol_u = 0.0
    reasons: List[str] = []
    if vol_u > 0 and vol_u < float(SNIPER_MIN_VOL_USD_FOR_S):
        reasons.append(
            f"24h成交≈{vol_u/1e6:.1f}M < S門檻{float(SNIPER_MIN_VOL_USD_FOR_S)/1e6:.0f}M"
        )
    _pending_down = os.getenv("SNIPER_PENDING_DOWNGRADE_S", "1").strip().lower() in (
        "1", "true", "yes", "on",
    )
    if bool(x.get("_triggered_from_pending")) and _pending_down:
        reasons.append("待辦池觸發(非本輪即時掃描快照)")
    if not reasons:
        return grade, brief
    logger.info(f"[S級降級→A] {sym_base}: " + "；".join(reasons))
    return "A", _s_grade_brief_to_a(brief)


def _sniper_structural_cascade_touch(x: dict, is_bull_sig: bool) -> bool:
    """1H 平倉浪摸頭/摸底敘事 + 多時框至少 3 框對齊（與日誌裡『部分共振・3框』一致）。"""
    try:
        mtf_al = int(x.get("mtf_aligned") or 0)
    except (TypeError, ValueError):
        mtf_al = 0
    cat = str(x.get("category") or "")
    if cat == "short_close":
        cat = "short_cover"
    touch_short = (not is_bull_sig) and cat == "long_close"
    touch_long = is_bull_sig and cat in ("short_cover", "short_close")
    return mtf_al >= 3 and (touch_short or touch_long)


def _sniper_mega_liquidity_ok(x: dict) -> bool:
    try:
        vol_u = float(x.get("volume_usd") or x.get("_cg_volume_usd") or 0)
    except (TypeError, ValueError):
        vol_u = 0.0
    return vol_u >= SNIPER_STRUCT_MEGA_LIQUIDITY_USD
# 訊號持倉時間過濾：若以近 1H 動能推估，TP1 可能超過此時數，則不推播（避免「等兩天沒到」）
MAX_ESTIMATED_HOLD_HOURS = _env_float("SNIPER_MAX_HOLD_HOURS", 8.0)
# 確定籌碼／衰竭反轉：結構 SL 常較寬，TP1 距離% 大時易誤觸 8h 線；另設上限（可用 SNIPER_MAX_HOLD_HOURS_CONFIRMED 覆寫）
MAX_ESTIMATED_HOLD_HOURS_CONFIRMED = _env_float("SNIPER_MAX_HOLD_HOURS_CONFIRMED", 18.0)
MIN_1H_MOMENTUM_PCT = _env_float("SNIPER_MIN_1H_MOMENTUM_PCT", 1.0)  # 低於此視為慢盤
MIN_1H_MOMENTUM_TIER2_PCT = _env_float("SNIPER_MIN_1H_MOMENTUM_TIER2_PCT", 0.35)  # tier2 略放寬（崩盤後 1H 常鈍化）


def compute_structural_sl_tp(
    entry: float,
    is_long: bool,
    vwap_2h: Optional[float],
    ema20: Optional[float],
    ema100: Optional[float],
    recent_low_2h: Optional[float],
    recent_high_2h: Optional[float],
    atr: Optional[float] = None,
    ema20_touch_low: Optional[float] = None,
    ema20_touch_high: Optional[float] = None,
    ema100_touch_low: Optional[float] = None,
    ema100_touch_high: Optional[float] = None,
) -> Tuple[Optional[float], Optional[float], Optional[float], float, float]:
    """
    以 K 線結構主力防守位定 SL，再套用最小距離保底，最後以 1R 映射 TP1/TP2。

    做多：結構防守位 = min(2h低, EMA20, VWAP_2h)（略過 None）
    做空：結構防守位 = max(2h高, EMA20, VWAP_2h)（略過 None）

    one_r = |進場 − 結構 SL|；若 one_r/進場 < MIN_SL_PERCENT，強制 one_r = 進場×MIN_SL_PERCENT
    並反推 SL（多：進場−one_r；空：進場+one_r）。
    若 one_r/進場 > MAX_SL_PERCENT，優先嘗試用 15m EMA20/EMA100 防守位收斂（不破壞最小風控距離）。

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
    ema_100 = _num(ema100)
    lo2 = _num(recent_low_2h)
    hi2 = _num(recent_high_2h)

    min_sl_distance = entry * MIN_SL_PERCENT
    try:
        atr_num = float(atr) if atr is not None else None
        if atr_num is not None and atr_num > 0:
            min_sl_distance = max(min_sl_distance, atr_num * MIN_SL_ATR_MULTIPLIER)
    except (TypeError, ValueError):
        pass

    max_sl_distance = entry * MAX_SL_PERCENT if (MAX_SL_PERCENT and MAX_SL_PERCENT > 0) else None
    sl_guard_buffer = entry * 0.0012
    if atr_num is not None and atr_num > 0:
        sl_guard_buffer = max(sl_guard_buffer, atr_num * SL_EMA_GUARD_BUFFER_ATR)

    if is_long:
        cands = [v for v in (lo2, ema, vwap) if v is not None]
        if cands:
            structural_sl = min(cands)
        else:
            structural_sl = entry - min_sl_distance
        if structural_sl >= entry:
            structural_sl = entry - min_sl_distance
        one_r = abs(entry - structural_sl)
        if one_r < min_sl_distance:
            one_r = min_sl_distance
            structural_sl = entry - one_r
        _touch_sl_applied = False
        # 回踩點優先：抓最近 EMA20/EMA100 回踩低點作防守（可明顯縮短持倉時間）
        touch_lows = [
            v for v in (
                _num(ema20_touch_low),
                _num(ema100_touch_low),
            )
            if v is not None and v < entry
        ]
        if touch_lows:
            _touch_base = max(touch_lows)  # 最近、最貼近現價的回踩低點
            _touch_buf = entry * 0.0008
            if atr_num is not None and atr_num > 0:
                _touch_buf = max(_touch_buf, atr_num * SL_TOUCH_BUFFER_ATR)
            _touch_sl = _touch_base - _touch_buf
            _touch_dist = entry - _touch_sl
            _touch_allow = (max_sl_distance * 1.02) if max_sl_distance is not None else float("inf")
            if atr_num is not None and atr_num > 0:
                _touch_allow = max(_touch_allow, min_sl_distance, atr_num * SNIPER_TOUCH_MAX_SL_ATR)
            if min_sl_distance <= _touch_dist and _touch_dist <= _touch_allow:
                structural_sl = _touch_sl
                one_r = _touch_dist
                _touch_sl_applied = True
        if not _touch_sl_applied and max_sl_distance is not None and one_r > max_sl_distance:
            guard_levels = [v for v in (ema, ema_100, vwap) if v is not None and v < entry]
            if guard_levels:
                guard_base = max(guard_levels)  # 最靠近進場的防守均線
                guard_sl = guard_base - sl_guard_buffer
                guard_dist = entry - guard_sl
                if min_sl_distance <= guard_dist <= max_sl_distance * 1.02:
                    structural_sl = guard_sl
                    one_r = guard_dist
        sl = structural_sl
        tp1 = entry + one_r * TP1_R_MULTIPLIER
        tp2 = entry + one_r * TP2_R_MULTIPLIER

        # 多單：停損必須在 2h 結構低點「之下」（含緩衝）。
        # 否則 EMA/VWAP 回踩縮停損時，常把 SL 縮在「前低上方」形成真空區，容易被掃後再延續走勢。
        _enf_sw = os.getenv("SNIPER_SL_ENFORCE_SWING", "1").strip().lower() in (
            "1", "true", "yes", "on",
        )
        if _enf_sw and lo2 is not None and lo2 < entry:
            _swing_floor = lo2 - sl_guard_buffer
            if sl > _swing_floor:
                sl = _swing_floor
                one_r = entry - sl
                if one_r < min_sl_distance:
                    one_r = min_sl_distance
                    sl = entry - one_r
                if max_sl_distance is not None and one_r > max_sl_distance:
                    _sl_cap = entry - max_sl_distance
                    if _sl_cap <= _swing_floor:
                        sl = _sl_cap
                        one_r = max_sl_distance
                tp1 = entry + one_r * TP1_R_MULTIPLIER
                tp2 = entry + one_r * TP2_R_MULTIPLIER
    else:
        cands = [v for v in (hi2, ema, vwap) if v is not None]
        if cands:
            structural_sl = max(cands)
        else:
            structural_sl = entry + min_sl_distance
        if structural_sl <= entry:
            structural_sl = entry + min_sl_distance
        one_r = abs(structural_sl - entry)
        if one_r < min_sl_distance:
            one_r = min_sl_distance
            structural_sl = entry + one_r
        _touch_sl_applied = False
        # 回踩點優先：抓最近 EMA20/EMA100 回踩高點作防守（縮短空單停損距離）
        touch_highs = [
            v for v in (
                _num(ema20_touch_high),
                _num(ema100_touch_high),
            )
            if v is not None and v > entry
        ]
        if touch_highs:
            _touch_base = min(touch_highs)  # 最近、最貼近現價的回踩高點
            _touch_buf = entry * 0.0008
            if atr_num is not None and atr_num > 0:
                _touch_buf = max(_touch_buf, atr_num * SL_TOUCH_BUFFER_ATR)
            _touch_sl = _touch_base + _touch_buf
            _touch_dist = _touch_sl - entry
            # 舊版僅允許 ≤MAX_SL%%，高波動幣「回踩高」常 >5.5% 而被捨棄→改為 max(MAX_SL, min_sl, ATR×係數)
            _touch_allow = (max_sl_distance * 1.02) if max_sl_distance is not None else float("inf")
            if atr_num is not None and atr_num > 0:
                _touch_allow = max(_touch_allow, min_sl_distance, atr_num * SNIPER_TOUCH_MAX_SL_ATR)
            if min_sl_distance <= _touch_dist and _touch_dist <= _touch_allow:
                structural_sl = _touch_sl
                one_r = _touch_dist
                _touch_sl_applied = True
        if not _touch_sl_applied and max_sl_distance is not None and one_r > max_sl_distance:
            guard_levels = [v for v in (ema, ema_100, vwap) if v is not None and v > entry]
            if guard_levels:
                guard_base = min(guard_levels)  # 最靠近進場的防守均線
                guard_sl = guard_base + sl_guard_buffer
                guard_dist = guard_sl - entry
                if min_sl_distance <= guard_dist <= max_sl_distance * 1.02:
                    structural_sl = guard_sl
                    one_r = guard_dist
        sl = structural_sl
        tp1 = entry - one_r * TP1_R_MULTIPLIER
        tp2 = entry - one_r * TP2_R_MULTIPLIER

        # 空單：停損必須在 2h 結構高點「之上」（含緩衝），避免回踩縮在「前高下方」。
        _enf_sw_s = os.getenv("SNIPER_SL_ENFORCE_SWING", "1").strip().lower() in (
            "1", "true", "yes", "on",
        )
        if _enf_sw_s and hi2 is not None and hi2 > entry:
            _swing_ceil = hi2 + sl_guard_buffer
            if sl < _swing_ceil:
                sl = _swing_ceil
                one_r = sl - entry
                if one_r < min_sl_distance:
                    one_r = min_sl_distance
                    sl = entry + one_r
                if max_sl_distance is not None and one_r > max_sl_distance:
                    _sl_cap = entry + max_sl_distance
                    if _sl_cap >= _swing_ceil:
                        sl = _sl_cap
                        one_r = max_sl_distance
                tp1 = entry - one_r * TP1_R_MULTIPLIER
                tp2 = entry - one_r * TP2_R_MULTIPLIER

    sl_pct = (one_r / entry * 100.0) if entry > 0 else 0.0
    return sl, tp1, tp2, one_r, sl_pct


_FETCH_15M_HLC_TTL_SEC = 75.0
_FETCH_15M_HLC_CACHE: Dict[str, Tuple[float, Tuple[List[float], List[float], List[float]]]] = {}


def _fetch_15m_hlc_arrays(symbol_base: str, limit: int) -> Optional[Tuple[List[float], List[float], List[float]]]:
    """Binance 期貨 15m：僅取 high/low/close，供箱體 TP2；失敗回 None。"""
    clean = symbol_base.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
    lim = int(max(24, min(1500, limit)))
    _ck = f"{clean}|{lim}"
    _now = time.time()
    _hit = _FETCH_15M_HLC_CACHE.get(_ck)
    if _hit and (_now - _hit[0]) <= _FETCH_15M_HLC_TTL_SEC:
        return _hit[1]
    for sym_pair in (f"{clean}USDT", f"1000{clean}USDT"):
        try:
            r = requests.get(
                "https://fapi.binance.com/fapi/v1/klines",
                params={"symbol": sym_pair, "interval": "15m", "limit": lim},
                timeout=5,
            )
            if r.status_code != 200:
                continue
            raw = r.json()
            if not isinstance(raw, list) or len(raw) < 24:
                continue
            highs: List[float] = []
            lows: List[float] = []
            closes: List[float] = []
            for bar in raw:
                try:
                    highs.append(float(bar[2]))
                    lows.append(float(bar[3]))
                    closes.append(float(bar[4]))
                except (IndexError, TypeError, ValueError):
                    continue
            if len(highs) < 24:
                continue
            _tup = (highs, lows, closes)
            _FETCH_15M_HLC_CACHE[_ck] = (_now, _tup)
            return _tup
        except Exception:
            continue
    return None


def refine_tp2_box_measured_move(
    symbol_base: str,
    entry: float,
    is_long: bool,
    tp1: float,
    tp2_from_r: float,
    one_r: float,
    atr: Optional[float],
) -> Tuple[float, str]:
    """
    15m 近視窗箱體：高=max(high)、低=min(low)，高 H=箱高。
    多：投射價 box_tp = 箱頂 + H；空：box_tp = 箱底 − H。
    結果與原 R 倍 TP2 合併：多取較遠（max）、空取較遠（min），再夹 SNIPER_TP2_MAX_R，且嚴格比 TP1 更遠。
    無資料或箱體太扁 → 回傳 tp2_from_r。
    """
    try:
        e = float(entry)
        t1 = float(tp1)
        t2r = float(tp2_from_r)
        r1 = float(one_r)
    except (TypeError, ValueError):
        return tp2_from_r, "r_mult"
    if e <= 0 or r1 <= 0:
        return tp2_from_r, "r_mult"
    if not SNIPER_TP2_BOX_ENABLED:
        return t2r, "r_mult"

    hlc = _fetch_15m_hlc_arrays(symbol_base, SNIPER_TP2_BOX_FETCH_LIMIT)
    if not hlc:
        return t2r, "r_mult"
    highs, lows, _closes = hlc
    n = min(SNIPER_TP2_BOX_LOOKBACK_BARS, len(highs), len(lows))
    if n < 16:
        return t2r, "r_mult"
    seg_h = highs[-n:]
    seg_l = lows[-n:]
    try:
        box_hi = float(max(seg_h))
        box_lo = float(min(seg_l))
    except (TypeError, ValueError):
        return t2r, "r_mult"
    H = box_hi - box_lo
    if H <= 0 or box_hi <= 0 or box_lo <= 0:
        return t2r, "r_mult"

    atr_f = None
    try:
        if atr is not None:
            atr_f = float(atr)
            if atr_f != atr_f or atr_f <= 0:
                atr_f = None
    except (TypeError, ValueError):
        atr_f = None

    h_min = max(
        e * (SNIPER_TP2_BOX_MIN_HEIGHT_PCT / 100.0),
        r1 * SNIPER_TP2_BOX_MIN_HEIGHT_R,
    )
    if atr_f is not None:
        h_min = max(h_min, atr_f * SNIPER_TP2_BOX_MIN_HEIGHT_ATR)
    if H < h_min:
        return t2r, "r_mult"

    tp_cap_dist = r1 * SNIPER_TP2_MAX_R
    min_gap = max(r1 * 0.02, e * 0.0002)
    eps = max(e * 1e-8, 1e-12)

    if is_long:
        box_tp = box_hi + H
        if box_tp <= e + eps:
            return t2r, "r_mult"
        merged = max(box_tp, t2r)
        if merged <= t1 + min_gap:
            return t2r, "r_mult"
        tp2 = min(merged, e + tp_cap_dist)
        if tp2 <= t1 + min_gap:
            tp2 = min(max(t2r, t1 + min_gap), e + tp_cap_dist)
        used_box = box_tp > t2r + eps
        return tp2, ("box1:1" if used_box else "r_mult")

    box_tp = box_lo - H
    if box_tp >= e - eps:
        return t2r, "r_mult"
    merged = min(box_tp, t2r)
    if merged >= t1 - min_gap:
        return t2r, "r_mult"
    tp2 = max(merged, e - tp_cap_dist)
    if tp2 >= t1 - min_gap:
        tp2 = max(min(t2r, t1 - min_gap), e - tp_cap_dist)
    used_box = box_tp < t2r - eps
    return tp2, ("box1:1" if used_box else "r_mult")


def _calc_tp1_r_ratio(entry: Optional[float], sl: Optional[float], tp1: Optional[float]) -> Optional[float]:
    """用實際價位計算 TP1 風報比（R 值）；無效資料回傳 None。"""
    try:
        e = float(entry) if entry is not None else 0.0
        s = float(sl) if sl is not None else 0.0
        t = float(tp1) if tp1 is not None else 0.0
    except (TypeError, ValueError):
        return None
    if e <= 0 or s <= 0 or t <= 0:
        return None
    risk = abs(e - s)
    if risk <= 0:
        return None
    reward = abs(t - e)
    return reward / risk


def derive_limit_order_from_inputs(
    category: str,
    cur_price: Optional[float],
    vwap_2h: Optional[float],
    ema20: Optional[float],
    signal_version: str,
    energy_exhausted: bool,
    atr: Optional[float] = None,
    vwap_volume_weighted: bool = True,
) -> Tuple[bool, Optional[float]]:
    """
    與 build_report_message_tiered 進場邏輯一致（順序相同）：
      1) 衰竭反轉 → 限價於 VWAP，否則 EMA20
      2) 動能透支 → 限價於 EMA20
      3) VWAP：在帶內 → 市價；否則 掛單價 = VWAP×0.975（與現行程式相同）
    回傳 (is_limit_order, limit_price)；市價進場為 (False, None)。
    推播策略：僅市價進場；若此函式回傳需限價（True, …）則該標的不推播。
    vwap_volume_weighted=False：K 線無 volume 時的 TWAP 近似，不作「主力 VWAP 帶」
    以免崩盤後現價與錨點脫節導致整筆被進場濾網誤殺。
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
    if not vwap_volume_weighted:
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

    atr_num = None
    try:
        atr_num = float(atr) if atr is not None and float(atr) > 0 else None
    except (TypeError, ValueError):
        atr_num = None
    if vwap is not None and atr_num is not None:
        if abs(price - vwap) <= MAX_MARKET_VWAP_GAP_ATR * atr_num:
            return False, None
        return True, vwap * 0.975
    if vwap is not None:
        if is_bull and price <= vwap:
            return False, None
        if (not is_bull) and price >= vwap:
            return False, None
        return True, vwap * 0.975

    return False, None


def _asof_oi_per_ohlc_bar(
    ohlc: List[Dict[str, Any]], oi_list: Optional[List[Dict[str, Any]]]
) -> List[Optional[float]]:
    """將 OI 時間序列前向對齊到每根 OHLC bar（每根 bar 取 t≤bar_t 的最後一筆 OI）。"""
    if not ohlc:
        return []
    if not oi_list:
        return [None] * len(ohlc)

    oi_sorted = sorted(
        [x for x in oi_list if x.get("t") is not None],
        key=lambda x: int(x["t"]),
    )
    out: List[Optional[float]] = []
    j = 0
    cur: Optional[float] = None
    for bar in ohlc:
        t = int(bar.get("t") or 0)
        while j < len(oi_sorted) and int(oi_sorted[j].get("t") or 0) <= t:
            try:
                cur = float(oi_sorted[j].get("v"))
            except (TypeError, ValueError):
                pass
            j += 1
        out.append(cur)
    return out


def _max_oi_step_pct_in_window(
    oi_vals: List[Optional[float]], i: int, lookback: int
) -> float:
    """
    與區間 [i-lookback, i-1] 內任一非空 OI 比較，回傳 |Oi-Oj|/|Oj|*100 的最大值（捕捉非相鄰棒更新／API 對齊延遲）。
    """
    if i < 1 or i >= len(oi_vals) or oi_vals[i] is None:
        return 0.0
    try:
        curr = float(oi_vals[i])
    except (TypeError, ValueError):
        return 0.0
    start = max(0, i - int(lookback))
    best = 0.0
    for j in range(start, i):
        if oi_vals[j] is None:
            continue
        try:
            prev = float(oi_vals[j])
        except (TypeError, ValueError):
            continue
        ap = abs(prev)
        if ap < 1e-12:
            continue
        best = max(best, abs(curr - prev) / ap * 100.0)
    return best


def compute_anchored_launch_vwap_snapshot(symbol_base: str) -> Optional[Dict[str, Any]]:
    """
    批次版「發動錨定 VWAP」：在最近 N 根 5m K 內，自右向左找最近一次
    「爆量（volume ≥ ratio×近 M 根均量）且 OI 在視窗內相對前段有足夠變化」的棒為錨點；
    若全視窗無法滿足 OI 條件（無 API、OI 重複採樣等），則降級為「僅爆量」錨點。
    自錨點棒起至最新棒的成交量加權均價（典型價）。
    """
    clean = (symbol_base or "").upper().replace("USDT", "").replace("-", "").replace("/", "")
    if not clean:
        return None
    ohlc = fetch_ohlc_5m(clean, limit=SNIPER_ANCHOR_LOOKBACK_BARS)
    if not ohlc or len(ohlc) < SNIPER_ANCHOR_VOL_BASELINE_BARS + 2:
        return None
    ohlc = sorted(ohlc, key=lambda x: int(x.get("t") or 0))
    oi_raw = fetch_coinglass_oi_5m(clean, limit=SNIPER_ANCHOR_LOOKBACK_BARS)
    oi_vals = _asof_oi_per_ohlc_bar(ohlc, oi_raw)

    def _ty(p: Dict[str, Any]) -> float:
        try:
            h = float(p.get("h"))
            l = float(p.get("l"))
            c = float(p.get("c"))
        except (TypeError, ValueError):
            return float("nan")
        return (h + l + c) / 3.0

    def _vol_metrics_at(i: int) -> Tuple[bool, float, float]:
        """(vol_ok, vi, base) — base 為 i 前 b0 根均量。"""
        try:
            vols = [float(ohlc[j].get("v") or 0) for j in range(i - b0, i)]
        except (TypeError, ValueError):
            return False, 0.0, 0.0
        base = sum(vols) / max(len(vols), 1)
        try:
            vi = float(ohlc[i].get("v") or 0)
        except (TypeError, ValueError):
            vi = 0.0
        vol_ok = base > 0 and vi >= SNIPER_ANCHOR_VOL_SPIKE_RATIO * base
        return vol_ok, vi, base

    anchor_idx: Optional[int] = None
    vol_ratio_at_anchor: Optional[float] = None
    b0 = SNIPER_ANCHOR_VOL_BASELINE_BARS
    oi_lb = SNIPER_ANCHOR_OI_LOOKBACK_BARS
    for i in range(len(ohlc) - 1, b0, -1):
        vol_ok, vi, base = _vol_metrics_at(i)
        if not vol_ok:
            continue
        oi_step = _max_oi_step_pct_in_window(oi_vals, i, oi_lb)
        if oi_step >= SNIPER_ANCHOR_OI_STEP_PCT:
            anchor_idx = i
            vol_ratio_at_anchor = (vi / base) if base > 0 else None
            break

    if anchor_idx is None:
        for i in range(len(ohlc) - 1, b0, -1):
            vol_ok, vi, base = _vol_metrics_at(i)
            if vol_ok:
                anchor_idx = i
                vol_ratio_at_anchor = (vi / base) if base > 0 else None
                break

    if anchor_idx is None:
        return {"ok": False, "note": "未偵測到發動錨點（爆量／近段無明顯量能集中）"}

    num = 0.0
    den = 0.0
    for j in range(anchor_idx, len(ohlc)):
        tp = _ty(ohlc[j])
        if tp != tp:
            continue
        try:
            vj = float(ohlc[j].get("v") or 0)
        except (TypeError, ValueError):
            vj = 0.0
        num += tp * vj
        den += vj
    vwap = (num / den) if den > 0 else _ty(ohlc[-1])

    return {
        "ok": True,
        "vwap_anchor": float(vwap),
        "anchor_ts": int(ohlc[anchor_idx].get("t") or 0),
        "vol_ratio": vol_ratio_at_anchor,
        "note": "發動錨定 VWAP",
    }


def _anchor_vwap_fit_bonus(x: Dict[str, Any]) -> Tuple[int, int, str, str]:
    """
    依「現價 vs 錨定 VWAP」貼合度給綜合分加分與 0～3 星，並附倉位提示（非投資建議）。

    回傳：(score_delta, stars_0_3, reason_snippet, sizing_hint)
    """
    va = x.get("vwap_anchor")
    if va is None:
        return 0, 0, "", ""
    try:
        va_f = float(va)
        px = float(x.get("current_price") or 0)
    except (TypeError, ValueError):
        return 0, 0, "", ""
    if va_f <= 0 or px <= 0 or px != px:
        return 0, 0, "", ""
    dev_pct = abs(px - va_f) / va_f * 100.0

    if dev_pct <= 1.2:
        return (
            15,
            3,
            "錨定均價高貼合",
            "籌碼錨定佳：結構上較利於計畫風險報酬時，可考慮在個人承受範圍內略加大倉位（仍須嚴守止損）",
        )
    if dev_pct <= 2.5:
        return (
            10,
            2,
            "錨定均價可接受",
            "貼合度尚可：倉位可比平常略積極，勿滿倉、勿放大槓桿超過習慣",
        )
    if dev_pct <= 4.0:
        return (
            5,
            1,
            "錨定均價略開",
            "貼合度一般：建議維持常規倉位",
        )
    return (
        2,
        0,
        "價格偏離發動均價",
        "已偏離發動籌碼成本帶：建議偏保守／縮小倉位",
    )


def _macro_regime_snapshot() -> Dict[str, Any]:
    """大盤防護網：BTC/ETH 1H/4H EMA20 + 15m CVD 趨勢。"""
    def _ema_state(sym: str, interval: str) -> Optional[bool]:
        data = fetch_price_history(sym, interval)
        if not data or len(data) < 20:
            return None
        closes: List[float] = []
        for row in data[-20:]:
            v = row.get("close")
            try:
                closes.append(float(v))
            except (TypeError, ValueError):
                continue
        if len(closes) < 20:
            return None
        ema20 = sum(closes) / len(closes)
        return closes[-1] >= ema20

    btc_1h = _ema_state("BTCUSDT", "1h")
    btc_4h = _ema_state("BTCUSDT", "4h")
    eth_1h = _ema_state("ETHUSDT", "1h")
    eth_4h = _ema_state("ETHUSDT", "4h")
    btc_cvd_15m = _cvd_change_last2("BTC", "15m")
    eth_cvd_15m = _cvd_change_last2("ETH", "15m")

    bearish = (btc_1h is False or btc_4h is False) and (btc_cvd_15m is not None and btc_cvd_15m < 0)
    bullish = (btc_1h is True or btc_4h is True) and (btc_cvd_15m is not None and btc_cvd_15m > 0)
    _mv_m = (os.getenv("SNIPER_MACRO_VETO_MODE") or "relaxed").strip().lower()
    if bullish:
        badge = "🛡️ 大盤環境：BTC 趨勢向上（逆勢空單見 SNIPER_MACRO_VETO_MODE）"
    elif bearish:
        badge = (
            "🛡️ 大盤環境：BTC 趨勢向下（逆勢多單見 SNIPER_MACRO_VETO_MODE）"
            if _mv_m != "strict"
            else "🛡️ 大盤環境：BTC 趨勢向下，僅放行順勢空單"
        )
    else:
        badge = "🛡️ 大盤環境：中性（EMA/CVD 未完全共振）"
    return {
        "bullish": bullish,
        "bearish": bearish,
        "badge": badge,
        "btc_1h": btc_1h,
        "btc_4h": btc_4h,
        "btc_cvd_15m": btc_cvd_15m,
        "eth_1h": eth_1h,
        "eth_4h": eth_4h,
        "eth_cvd_15m": eth_cvd_15m,
    }


def _macro_veto_should_skip_alt(
    base: str,
    x: Dict[str, Any],
    macro_ctx: Dict[str, Any],
    btc_1h_pct: Optional[float],
) -> bool:
    """
    BTC/ETH 大盤環境與山寨訊號方向不一致時，是否略過該山寨。
    - strict：舊版一律擋逆勢（BTC 空不做多、BTC 多不做空）。
    - relaxed（預設）：確定籌碼／衰竭反轉／回踩仍放行；或 1H 相對強度優／劣於 BTC 時放行。
    - off：不擋。
    環境變數：SNIPER_MACRO_VETO_MODE、SNIPER_MACRO_RS_MIN_PCT。
    """
    mode = (os.getenv("SNIPER_MACRO_VETO_MODE") or "relaxed").strip().lower()
    if mode in ("off", "0", "false", "none", "disabled", "no"):
        return False
    bearish = bool(macro_ctx.get("bearish"))
    bullish = bool(macro_ctx.get("bullish"))
    _cat = x.get("category") or ""
    _is_long_sig = _cat in ("long_open", "short_close")
    _is_short_sig = _cat in ("short_open", "long_close")
    if not ((bearish and _is_long_sig) or (bullish and _is_short_sig)):
        return False

    if mode == "strict":
        return True

    sv = str(x.get("signal_version") or "").strip()
    if sv in ("confirmed", "exhaustion_reversal", "pullback"):
        logger.info(
            "[MacroVeto] %s: 大盤逆勢但訊號類型=%s → 放行（確定／反轉／回踩通道）",
            base,
            sv,
        )
        return False

    try:
        alt_1h = float(
            x.get("priceChange1h")
            if x.get("priceChange1h") is not None
            else (x.get("price_change_percent_1h") or 0)
        )
    except (TypeError, ValueError):
        alt_1h = 0.0
    btc_ref = btc_1h_pct
    rs_min = max(0.1, _env_float("SNIPER_MACRO_RS_MIN_PCT", 1.25))

    if bearish and _is_long_sig and btc_ref is not None:
        rs = alt_1h - btc_ref
        if rs >= rs_min:
            logger.info(
                "[MacroVeto] %s: BTC 偏空但山寨 1H 相對強度 %+0.2f%%（門檻 %+0.2f%%）→ 放行",
                base,
                rs,
                rs_min,
            )
            return False

    if bullish and _is_short_sig and btc_ref is not None:
        rs = btc_ref - alt_1h
        if rs >= rs_min:
            logger.info(
                "[MacroVeto] %s: BTC 偏多但山寨 1H 相對偏弱 %+0.2f%%（門檻 %+0.2f%%）→ 放行",
                base,
                rs,
                rs_min,
            )
            return False

    if bearish and _is_long_sig:
        logger.info(f"[MacroVeto] {base}: BTC 空頭環境，略過做多訊號（觀察/弱共振且未達相對強度）")
    else:
        logger.info(f"[MacroVeto] {base}: BTC 多頭環境，略過做空訊號（觀察/弱共振且未達相對偏弱）")
    return True


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


def _get_oi_threshold_30m_for_item(item: Dict) -> float:
    """30m 主判斷門檻：沿用動態分層，門檻較 1H 更靈敏。"""
    return _get_oi_threshold_for_item(item) * OI_THRESHOLD_30M_RATIO


# ── 黑名單：永久禁止推播的標的（可隨時新增/移除）────────────────────────────────
# 原則：歷史表現差、流動性不足、長期被操控的幣種
SYMBOL_BLACKLIST: set = {
    # 保留封鎖：美股代幣／代幣化股票／傳統商品與指數（非純加密幣）
    "MASTOCK",    # 代幣化股票，OI 數據異常（曾觸發 621% 極端值）
    "PLTRSTOCK",  # Palantir 代幣化股票（STOCK 後綴格式）
    # ── 其他非加密貨幣期貨 ──
    "XTI",        # WTI 原油期貨（XTI/USD）
    "XBR",        # Brent 原油期貨
    "KO",         # Coca-Cola 股票
    # ── 代幣化股票（Bybit/Gate/Bitget 合約，非加密貨幣）──
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
    # ── 亞洲股票指數期貨 ──
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

    【與「反人性／掃損摸頭摸底」敘事的對照（設計意圖，非逐根 K 狀態機）】
    - 上漲段多平後再空開、或空平後再空開：以 1H 分類 + 短週期反向（pullback／tier2）與
      `detect_trap_setup` 的 15m 三步（空平→多平→空開 等）共同近似「出貨／掃空損／摸頭」。
    - 下跌段多平後再多開、或空平後再多開：以 1H bear 陣營 + 短週期 short_cover／long_open
      與陷阱三步（多平→空平→多開）近似「掃多損／摸底／獲利回補後再攻」。
    本模組以「多時區 OI×價格共振 + 衰竭反轉 + 籌碼陷阱」量化上述直覺；無法保證與主觀盤感逐句等同。

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
    # ✘ D. 其餘（1H&30m 大方向完全相反）→ return None 丟棄
    # ══════════════════════════════════════════════════════════
    _tf_conflict_soft = bool(item.get("tf_conflict_soft"))
    # Step2 方向衝突不再硬丟：降為 Tier2（逆勢/觀察）並明確標示。
    if step2_conflict or _tf_conflict_soft:
        return {
            **base,
            "version": "tier2",
            "subtype": "30m衝突",
            "aligned_count": 1,
            "reversal_hint": "30m 與 1H 方向相反，屬逆勢切入；請小倉並嚴守止損。",
        }

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
    oi_30m = item.get("oiChange30m") or 0
    oi_1h = item.get("oiChange1h")
    oi = oi_30m
    price_chg_1h_main = item.get("priceChange1h") or item.get("priceChange30m")
    if price_chg_1h_main is not None and not isinstance(price_chg_1h_main, (int, float)):
        price_chg_1h_main = None

    # 扳機條件（微調）：
    # 1) 30m OI 為主判斷（避免 1H 過慢、追高）
    # 2) 1H OI 僅作保險閥（太弱才擋），用來降低畫門假突破
    oi_threshold_30m = _get_oi_threshold_30m_for_item(item)
    oi_threshold_1h_guard = _get_oi_threshold_for_item(item) * OI_THRESHOLD_1H_GUARD_RATIO
    try:
        oi_1h_f = float(oi_1h) if oi_1h is not None else None
    except (TypeError, ValueError):
        oi_1h_f = None
    if abs(oi_30m) < oi_threshold_30m:
        return None
    if oi_1h_f is not None and abs(oi_1h_f) < oi_threshold_1h_guard:
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
        zone = ZONE_TOP
        _trend = "逆勢摸頭機會" if not mtf_trend_ok else "1H下行加速"
        reason = f"1H OI↓+Price↓，多頭斷頭出場，空方平倉做空或等反彈做多，{_trend}{fr_note}{mtf_note}{_counter_hint}{_oi_mtf_note}"
    elif category == "short_close":
        label = "🔥 空頭平倉"
        zone = ZONE_DIP
        _trend = "逆勢摸底機會" if not mtf_trend_ok else "1H上行加速"
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
      9. 錨定 VWAP 貼合（max ~15）── 現價接近 5m 發動加權成本者加分（見 _anchor_vwap_fit_bonus）

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

    # 逆勢且車已發動：這種最容易變成「追漲追空」的晚段單
    # 直接強制降級為 B（不推播），優先保護你不被連損。
    if _counter_4h and _already_moving:
        return (
            "B",
            0,
            "🥈 *B 級* 逆勢且車已發動（追漲追空風險高），不推播",
            _already_moving,
            _motion_note,
        )

    # ══════════════════════════════════════════════════════════════
    # 第四步：大盤方向提示（僅提示，不作為硬過濾）
    # 目的：保留 BTC/ETH 作為閱讀輔助，不再限制山寨訊號評級上限
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
                if _motion_note:
                    _motion_note += f"  🌐 大盤偏弱 {_ref_1h_val:+.2f}%：僅作參考"
                else:
                    _motion_note = f"🌐 大盤偏弱 {_ref_1h_val:+.2f}%：僅作參考"
            elif (not is_bull_sig) and _ref_1h_val > 0.5:
                if _motion_note:
                    _motion_note += f"  🌐 大盤偏強 {_ref_1h_val:+.2f}%：僅作參考"
                else:
                    _motion_note = f"🌐 大盤偏強 {_ref_1h_val:+.2f}%：僅作參考"
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

    # ── 4b. CoinGlass rsi/list 與 K 線 RSI 交叉（Skills：多源一致較可信）──
    _rsi_k_ref = x.get("rsi")
    _rsi_cg_ref2 = x.get("cg_rsi_15m_ref")
    if _rsi_k_ref is not None and _rsi_cg_ref2 is not None:
        try:
            _rk = float(_rsi_k_ref)
            _rc = float(_rsi_cg_ref2)
            if _rk == _rk and _rc == _rc and abs(_rk - _rc) >= 22:
                score -= 4
                reasons.append("CG RSI與K線RSI分歧")
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
                # 崩盤日多數標的 24h 深跌；若 1H OI 仍強，視為空頭加倉而非單純追低
                try:
                    _oi1_abs = abs(float(x.get("oiChange1h") or 0))
                except (TypeError, ValueError):
                    _oi1_abs = 0.0
                if _oi1_abs >= 4.0:
                    score -= 3
                    reasons.append("大跌段空頭加倉")
                else:
                    score -= 10
                    reasons.append("大跌後弱OI追空")
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

    # ── 11. BTC OI 1H 信心加分（僅作輔助，不作硬過濾）───────────────
    try:
        _btc_oi_ref = float(_btc_oi_1h_pct) if _btc_oi_1h_pct is not None else None
    except (TypeError, ValueError):
        _btc_oi_ref = None
    if _btc_oi_ref is not None:
        if abs(_btc_oi_ref) >= 1.5:
            score += 5
            reasons.append(f"BTC_OI活躍({_btc_oi_ref:+.2f}%)")
        elif abs(_btc_oi_ref) >= 0.8:
            score += 2
            reasons.append(f"BTC_OI輔助({_btc_oi_ref:+.2f}%)")

    # ── 12. 錨定 VWAP 貼合度（現價 vs 5m 發動加權成本）──────────────────
    _ab, _ast, _ar_snip, _ahint = _anchor_vwap_fit_bonus(x)
    x["_anchor_fit_stars"] = int(max(0, min(3, _ast)))
    x["_anchor_sizing_hint"] = _ahint or ""
    if _ab:
        score += _ab
        if _ar_snip:
            reasons.append(_ar_snip)

    # ── 13. TD Sequential（15m；CoinGlass td/list 預載）加／扣分 ───────────
    _td_raw = x.get("cg_td_15m")
    td: Optional[float] = None
    if _td_raw is not None:
        try:
            td = float(_td_raw)
        except (TypeError, ValueError):
            td = None
    if td is not None and td == td:
        mag = abs(td)
        if is_bull_sig:
            if td > 0:
                if mag >= 8:
                    score -= 4
                    reasons.append("TD15m買計數偏高(衰竭風險)")
                elif mag >= 4:
                    score += 5
                    reasons.append("TD15m買勢延續")
                else:
                    score += 2
                    reasons.append("TD15m偏多")
            else:
                if mag >= 6:
                    score -= 5
                    reasons.append("TD15m賣壓計數")
                elif mag >= 3:
                    score -= 2
                    reasons.append("TD15m偏空")
        else:
            if td < 0:
                if mag >= 8:
                    score -= 4
                    reasons.append("TD15m賣計數偏高(衰竭風險)")
                elif mag >= 4:
                    score += 5
                    reasons.append("TD15m賣勢延續")
                else:
                    score += 2
                    reasons.append("TD15m偏空")
            else:
                if mag >= 6:
                    score -= 5
                    reasons.append("TD15m買壓計數")
                elif mag >= 3:
                    score -= 2
                    reasons.append("TD15m偏多")

    # ── 14. 1h 爆倉邊（liquidation/coin-list；與守門員同源）加／扣分 ─────
    _l1w = x.get("cg_liq_long_1h_usd")
    _s1w = x.get("cg_liq_short_1h_usd")
    if _l1w is not None and _s1w is not None:
        try:
            _lfq = float(_l1w)
            _sfq = float(_s1w)
        except (TypeError, ValueError):
            _lfq = _sfq = -1.0
        if _lfq >= 0 and _sfq >= 0:
            _totq = _lfq + _sfq
            _min_sc = max(45_000.0, _env_float("SNIPER_SCORE_LIQ_MIN_USD", 78_000.0))
            if _totq >= _min_sc:
                if is_bull_sig:
                    if _sfq >= 1.45 * max(_lfq, 500.0):
                        score += 4
                        reasons.append("1h空頭清算偏多")
                    elif _lfq >= 2.05 * max(_sfq, 500.0):
                        score -= 5
                        reasons.append("1h多頭清算壓力")
                else:
                    if _lfq >= 1.45 * max(_sfq, 500.0):
                        score += 4
                        reasons.append("1h多頭清算偏多")
                    elif _sfq >= 2.05 * max(_lfq, 500.0):
                        score -= 5
                        reasons.append("1h空頭清算壓力")

    # ── 評級（S / A / R / B；僅「車已發動」限制上限）────────────
    score = max(0, min(100, score))
    if _already_moving:
        score = min(score, 74)   # 車已發動：硬上限 74 分 = 最高 A 級

    # 逆勢摸頭/摸底 + 機構級成交：放寬「最低分」以免壓分後變 B（仍須 ≥ MIN_R_STRUCT_TOUCH_SCORE）
    _min_floor = MIN_SIGNAL_PUSH_SCORE
    if (
        _counter_4h
        and _sniper_structural_cascade_touch(x, is_bull_sig)
        and _sniper_mega_liquidity_ok(x)
    ):
        _min_floor = min(_min_floor, MIN_R_STRUCT_TOUCH_SCORE)

    if score < _min_floor:
        _eff_floor = _min_floor
        if (x.get("signal_version") or "") == "tier2":
            _eff_floor = max(MIN_R_STRUCT_TOUCH_SCORE, MIN_SIGNAL_PUSH_SCORE_TIER2)
        if score < _eff_floor:
            grade = "B"
            grade_badge = "🥈 *B 級*"
            grade_desc = f"訊號不足（<{_eff_floor}分不推播）"
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
    【1H MTF 四層漏斗訊號推播】
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
        ema100: Optional[float] = None,
        rsi: Optional[float] = None,
        ema20_touch_low: Optional[float] = None,
        ema20_touch_high: Optional[float] = None,
        ema100_touch_low: Optional[float] = None,
        ema100_touch_high: Optional[float] = None,
        vwap_2h: Optional[float] = None,
    ):
        """
        與檔首 compute_structural_sl_tp 一致：2H 高低 + EMA20 + VWAP 結構防守、MIN_SL_PERCENT 保底、TP 倍率映射。
        舊參數（ATR/軋空/回踩）保留簽名以相容，計算已不再使用。
        """
        if not price or price <= 0:
            return None, None, None, None, None, "—", "normal", TP1_R_MULTIPLIER, TP2_R_MULTIPLIER
        sl, tp1, tp2, _one_r, sl_pct = compute_structural_sl_tp(
            float(price), is_long, vwap_2h, ema20, ema100, recent_low_2h, recent_high_2h, atr,
            ema20_touch_low, ema20_touch_high, ema100_touch_low, ema100_touch_high
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

    # ── 小白友善標題對應（維持 2 月基底：摸頭/摸底/追漲/追跌）──────────────
    _signal_title = {
        "long_open":   "🚀 【追漲做多】",
        "short_close": "📌 【摸底做多】",
        "short_open":  "💥 【追跌做空】",
        "long_close":  "🎯 【摸頭做空】",
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

    def _tactic_from_zone(zone: str, is_bull_flag: bool, grade: str, category: str, price_change_24h: Optional[float]) -> Tuple[str, str]:
        """回傳 (戰術文案, emoji)"""
        # R 級或明顯逆勢（例如 24h 大漲後做空 / 大跌後做多）強制用左側語彙，避免與順勢標籤打架
        if grade == "R":
            if is_bull_flag:
                return ("左側摸底（支撐承接）", "📌")
            return ("左側摸頭（阻力做空）", "🎯")
        try:
            p24 = float(price_change_24h) if price_change_24h is not None else None
        except (TypeError, ValueError):
            p24 = None
        if category == "short_open" and p24 is not None and p24 >= 8.0:
            return ("左側摸頭（阻力做空）", "🎯")
        if category in ("long_open", "short_close") and p24 is not None and p24 <= -8.0:
            return ("左側摸底（支撐承接）", "📌")
        if zone == ZONE_DIP:
            return ("摸底（跌深撿便宜）", "📌")
        if zone == ZONE_TOP:
            return ("摸頭（漲多放空）", "🎯")
        if zone == ZONE_BREAKOUT_LONG:
            return ("追漲（順勢做多）", "🚀")
        if zone == ZONE_BREAKOUT_SHORT:
            return ("追跌（順勢做空）", "💥")
        return (("做多" if is_bull_flag else "做空"), ("🟢" if is_bull_flag else "🔴"))

    def _regime_from_grade_zone(grade: str, zone: str) -> Tuple[str, str]:
        """
        三盤型標籤：
        - R 級固定逆勢
        - 摸頭/摸底歸震盪
        - 追漲/追跌歸趨勢
        """
        if grade == "R":
            return ("逆勢左側", "⚠️")
        if zone in (ZONE_DIP, ZONE_TOP):
            return ("震盪訊號", "🌀")
        return ("趨勢訊號", "📈")

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
        _grade, _grade_brief = _apply_sniper_s_grade_guards(_grade, _grade_brief, x, sym_base)
        if _grade == "B":
            continue

        try:
            _grade_score_int = int(round(float(_grade_score)))
        except (TypeError, ValueError):
            _grade_score_int = 0

        # 逆勢 R：預設須達 MIN_R；「平倉浪結構 + 機構成交」允許降至 MIN_R_STRUCT（仍排除極弱單）
        if _grade == "R" and _grade_score_int < MIN_R_SIGNAL_PUSH_SCORE:
            _r_struct_ok = (
                _sniper_structural_cascade_touch(x, is_bull_sig)
                and _sniper_mega_liquidity_ok(x)
                and _grade_score_int >= MIN_R_STRUCT_TOUCH_SCORE
            )
            if not _r_struct_ok:
                logger.info(
                    f"[勝率過濾] {sym_base}: R 級綜合分 {_grade_score_int} < {MIN_R_SIGNAL_PUSH_SCORE}"
                    "（且未符合結構+機構成交放行），略過推播"
                )
                continue
        # R 級額外品質閥：逆勢單至少要有 OI 力道或陷阱步數，避免「輕微逆勢噪音單」
        if _grade == "R":
            try:
                _r_oi30_abs = abs(float(x.get("oiChange30m") or 0))
            except (TypeError, ValueError):
                _r_oi30_abs = 0.0
            try:
                _r_trap_steps = int(x.get("_bull_trap_steps") or 0)
            except (TypeError, ValueError):
                _r_trap_steps = 0
            if _r_oi30_abs < MIN_R_OI30_ABS and _r_trap_steps < MIN_R_TRAP_STEPS:
                logger.info(
                    f"[R品質過濾] {sym_base}: OI30m={_r_oi30_abs:.2f}% < {MIN_R_OI30_ABS:.2f}% "
                    f"且陷阱步數 {_r_trap_steps}/3 < {MIN_R_TRAP_STEPS}，略過"
                )
                continue

        # 弱「潛在」訊號（非完美回踩）：須更高分才推，回踩型維持原門檻
        _sv_early = str(x.get("signal_version") or "")
        _sub_early = str(x.get("signal_subtype") or "")
        if (
            _sv_early == "potential"
            and _sub_early != "pullback"
            and _grade_score_int < MIN_WEAK_POTENTIAL_PUSH_SCORE
        ):
            logger.info(
                f"[勝率過濾] {sym_base}: 潛在訊號（非回踩）綜合分 {_grade_score_int} < "
                f"{MIN_WEAK_POTENTIAL_PUSH_SCORE}，略過推播"
            )
            continue

        # Tier2：原則上須陷阱步數；若僅因「費率壅擠」降級 + 平倉浪結構 + 機構成交 → 仍放行
        if _sv_early == "tier2":
            _trap_steps = int(x.get("_bull_trap_steps") or 0)
            _trap_full = bool(x.get("_bull_trap_detected"))
            _sub_t2 = str(x.get("signal_subtype") or "")
            _tier2_fr_only = ("壅擠" in _sub_t2 or "擁擠" in _sub_t2) and "警示" in _sub_t2
            _tier2_struct_fr = (
                _tier2_fr_only
                and _sniper_structural_cascade_touch(x, is_bull_sig)
                and _sniper_mega_liquidity_ok(x)
            )
            _tier2_need_trap = SNIPER_TIER2_MIN_TRAP_STEPS
            if _tier2_fr_only:
                _tier2_need_trap = min(_tier2_need_trap, 1)
            # 已因「30m vs 1H 方向衝突」降級為觀察：不再強制籌碼陷阱步數（否則常 0 步 → 整輪無推播）
            if "30m衝突" in _sub_t2:
                _tier2_need_trap = 0
            if not _tier2_struct_fr and not _trap_full and _trap_steps < _tier2_need_trap:
                logger.info(
                    f"[持倉過濾] {sym_base}: Tier2 且籌碼陷阱未達 {_tier2_need_trap}/3 步"
                    f"（非費率降級+結構單），略過推播"
                )
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
        _vwap_vol_ok = bool(x.get("vwap_2h_volume_weighted", True))
        vwap_for_struct = vwap_2h_val if _vwap_vol_ok else None
        _now_ts = time.time()

        # ══════════════════════════════════════════════════════════
        # 進場：統一市價；若判定需限價，改以「現價與主力均價差」決定是否仍可推播（<=3% 才保留）。
        # TP/SL：以「實際進場價（市價）」為基準，結構防守位（2H 高/低 + EMA20 + VWAP）+ MIN_SL_PERCENT 保底 → 1R → TP1/TP2
        # ══════════════════════════════════════════════════════════
        sl, tp1, tp2 = None, None, None
        _r1, _r2 = TP1_R_MULTIPLIER, TP2_R_MULTIPLIER
        sl_pct_val = None
        ema20_val = x.get("ema20") or x.get("ema20_close")
        ema100_val = x.get("ema100") or x.get("ema100_close")
        _need_limit, _lp_hint = derive_limit_order_from_inputs(
            category,
            float(price),
            vwap_2h_val,
            ema20_val,
            _sig_ver,
            bool(x.get("_energy_exhausted")),
            atr_val,
            _vwap_vol_ok,
        )
        _entry_price = float(price)
        if _need_limit:
            try:
                _lp_vwap = float(vwap_2h_val) if vwap_2h_val is not None else None
                _atr_f = float(atr_val) if atr_val is not None else None
                _lp_vw_dev_atr = abs(float(_entry_price) - _lp_vwap) / _atr_f if _lp_vwap and _atr_f and _atr_f > 0 else None
            except (TypeError, ValueError):
                _lp_vw_dev_atr = None
            if _lp_vw_dev_atr is not None and _lp_vw_dev_atr > MAX_MARKET_VWAP_GAP_ATR:
                logger.info(
                    f"[進場過濾] {sym_base}: 現價與主力均價偏離 {_lp_vw_dev_atr:.2f}ATR > {MAX_MARKET_VWAP_GAP_ATR:.2f}ATR，略過"
                )
                continue

        if _entry_price and _entry_price > 0:
            _recent_lo = x.get("recent_low_2h")
            _recent_hi = x.get("recent_high_2h")
            sl, tp1, tp2, _one_r_u, sl_pct_val = compute_structural_sl_tp(
                float(_entry_price),
                is_bull_sig,
                vwap_for_struct,
                ema20_val,
                ema100_val,
                _recent_lo,
                _recent_hi,
                atr_val,
                x.get("ema20_touch_low"),
                x.get("ema20_touch_high"),
                x.get("ema100_touch_low"),
                x.get("ema100_touch_high"),
            )
            if sl is None or tp1 is None:
                logger.warning(f"[SL/TP] {sym_base} 結構計算失敗，跳過此訊號")
                continue
            _tp2_raw = float(tp2)
            tp2, _tp2_src = refine_tp2_box_measured_move(
                sym_base,
                float(_entry_price),
                is_bull_sig,
                float(tp1),
                _tp2_raw,
                float(_one_r_u),
                atr_val,
            )
            x["_tp2_src"] = _tp2_src
            logger.info(
                f"[SL/TP結構計算] 幣種: {sym_base}, 方向: {'多' if is_bull_sig else '空'}, "
                f"進場: {_entry_price}, 結構SL: {sl} (距離 {sl_pct_val:.2f}%), TP1: {tp1}, TP2({_tp2_src}): {tp2}"
            )
            _rr_real = _calc_tp1_r_ratio(_entry_price, sl, tp1)
            if _rr_real is None or _rr_real < MIN_TP1_R_FOR_PUSH:
                logger.info(
                    f"[風報比硬閥] {sym_base}: 實際TP1風報比 {_rr_real if _rr_real is not None else 'N/A'}R < "
                    f"{MIN_TP1_R_FOR_PUSH}R，略過"
                )
                continue

        # ══════════════════════════════════════════════════════════
        # 訊號版本 / 標籤 / 策略短評
        # ══════════════════════════════════════════════════════════
        _sig_version   = x.get("signal_version") or "potential"
        _sig_subtype   = x.get("signal_subtype") or ""

        # 訊號版本（僅供 emoji 彙總）
        _dir_str   = "做多" if is_bull_sig else "做空"
        _dir_emoji = "🟢"   if is_bull_sig else "🔴"
        if _sig_version == "exhaustion_reversal":
            sig_emoji = "🔥"
        elif _sig_version == "confirmed":
            sig_emoji = "💎"
        elif _sig_version == "tier2":
            sig_emoji = "👀"
        else:
            sig_emoji = "🏎️"

        x["_sig_emoji"] = sig_emoji  # 供 header 彙總列用

        def _build_oi_plain_lines() -> Tuple[str, str]:
            """白話解釋 OI 在這筆訊號代表的資金行為與風險。"""
            def _f(v):
                try:
                    return float(v) if v is not None else None
                except (TypeError, ValueError):
                    return None

            oi_30 = _f(x.get("oiChange30m"))
            oi_1h_v = _f(x.get("oiChange1h"))
            p_30 = _f(x.get("priceChange30m"))

            if oi_30 is None:
                return "• OI：30m OI 無法取得，先依價格結構與風控操作。", "• 注意：資料缺口時，倉位建議再降一級。"

            if oi_30 > 0 and (p_30 is None or p_30 >= 0):
                oi_story = f"• OI：30m OI 增加 `{oi_30:+.2f}%`，代表新資金在進場偏多。"
            elif oi_30 > 0 and p_30 < 0:
                oi_story = f"• OI：30m OI 增加 `{oi_30:+.2f}%`，但價格下滑，偏向空方新倉在加碼。"
            elif oi_30 < 0 and (p_30 is None or p_30 > 0):
                oi_story = f"• OI：30m OI 減少 `{oi_30:+.2f}%`，價格走高，偏向空單回補推升。"
            else:
                oi_story = f"• OI：30m OI 減少 `{oi_30:+.2f}%`，偏向多單退場/止損，波動容易放大。"

            if _sig_subtype == "30m衝突":
                oi_risk = "• 注意：30m 與 1H 方向衝突，屬逆勢訊號，請小倉、嚴守止損。"
            elif oi_1h_v is not None and oi_30 * oi_1h_v < 0:
                oi_risk = "• 注意：30m 與 1H OI 方向不一致，可能只是短線反抽/急跌，勿重倉。"
            elif abs(oi_30) >= 6:
                oi_risk = "• 注意：30m OI 變化過大，主力節奏快，建議分批進出。"
            else:
                oi_risk = "• 注意：先看是否守住止損位，再考慮續抱到 TP。"
            return oi_story, oi_risk

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
            _fr_line = f"💸 費率： {_fr_str}% {_fr_desc}"
        else:
            _fr_line = "💸 費率： 無數據"

        # ── 成交值 ────────────────────────────────────────────────────
        vol_m_val    = float(vol_usd) / 1e6 if vol_usd and float(vol_usd) > 0 else 0.0
        _vol_src_tag = x.get("_vol_source", "CoinGlass")
        _src_note    = f" _{_vol_src_tag}_" if _vol_src_tag not in ("CoinGlass", "") else ""
        if vol_m_val >= 50:
            _vol_line = f"📊 成交值 {vol_m_val:.0f}M ✅ 機構級"
        elif vol_m_val >= 20:
            _vol_line = f"📊 成交值 {vol_m_val:.0f}M ✅ 深度充足"
        elif vol_m_val >= 5:
            _vol_line = f"📊 成交值 {vol_m_val:.1f}M{_src_note} ⚠️ 流動性偏低"
        elif vol_m_val > 0:
            _vol_line = f"📊 成交值 {vol_m_val:.1f}M{_src_note} ⚠️ 極低流動性"
        else:
            _vol_line = ""  # 無成交值資料時不顯示此行，避免誤導

        # ══════════════════════════════════════════════════════════
        # 組裝電報訊息：重點／點位前置；錨定滿星＝3 顆 ⭐
        # ══════════════════════════════════════════════════════════
        msg_lines: List[str] = []

        # ─ 標題（回復可複製的舊版樣式）─────────────────────────────────────
        try:
            _score = int(round(float(x.get("score", 0))))
        except (TypeError, ValueError):
            _score = 0
        _zone_now = x.get("zone") or ""
        _tactic_txt, _tactic_emo = _tactic_from_zone(_zone_now, is_bull_sig, _grade, category, x.get("priceChange24h"))
        _regime_txt, _ = _regime_from_grade_zone(_grade, _zone_now)
        if _sig_version == "exhaustion_reversal":
            _type_str, _badge_emo, _ver_short = (
                ("衰竭反轉・抄底" if is_bull_sig else "衰竭反轉・摸頭"),
                "🎯",
                "🔥衰竭",
            )
        elif _sig_version == "confirmed":
            _type_str, _badge_emo, _ver_short = ("確定籌碼・右側突破", "🚀", "✅確定")
        elif _sig_version == "tier2":
            _t2_sub = _sig_subtype or "弱共振"
            _type_str, _badge_emo, _ver_short = (f"觀察名單・{_t2_sub}", "⚠️", "⚠️觀察")
        else:
            _type_str, _badge_emo, _ver_short = (
                ("潛在機會・牛回頭低接" if is_bull_sig else "潛在機會・熊反彈做空"),
                "🧲",
                "🎯回踩",
            )

        try:
            _anch_fit_stars = max(0, min(3, int(round(float(x.get("_anchor_fit_stars") or 0)))))
        except (TypeError, ValueError):
            _anch_fit_stars = 0
        _star_emoji = "⭐" * _anch_fit_stars

        _title_tail = f"{_score} {_grade}"
        if _star_emoji:
            _title_tail = f"{_title_tail} {_star_emoji}"
        msg_lines.append(f"{_dir_emoji} *{_dir_str}* `{sym_base}` {_title_tail}")
        msg_lines.append(f"{_badge_emo} `{_ver_short}` {_type_str}｜{_tactic_emo}{_tactic_txt} · {_regime_txt}")
        msg_lines.append(_grade_brief)
        if x.get("_macro_veto_badge"):
            msg_lines.append(str(x.get("_macro_veto_badge")))
        if _grade == "R":
            msg_lines.append("_⚠️ R級＝逆勢左側，小倉、嚴守止損。_")
        if x.get("cooldown_reverse_recent") and _grade == "S":
            msg_lines.append("🧠 冷卻期內曾反向，本次仍放行。")

        # 2h VWAP + 錨定（供均價區）
        try:
            _vwap_show = (
                float(vwap_2h_val)
                if _vwap_vol_ok
                and vwap_2h_val is not None
                and isinstance(vwap_2h_val, (int, float))
                and float(vwap_2h_val) > 0
                else None
            )
        except (TypeError, ValueError):
            _vwap_show = None

        _vwap_anchor_show: Optional[float] = None
        _vwap_anchor_ts_show = 0
        try:
            _raw_va = x.get("vwap_anchor")
            if _raw_va is not None:
                _vwap_anchor_show = float(_raw_va)
                if _vwap_anchor_show != _vwap_anchor_show or _vwap_anchor_show <= 0:
                    _vwap_anchor_show = None
        except (TypeError, ValueError):
            _vwap_anchor_show = None
        try:
            _vwap_anchor_ts_show = int(x.get("vwap_anchor_ts") or 0)
        except (TypeError, ValueError):
            _vwap_anchor_ts_show = 0
        _entry_now_txt = _fmt_price(_entry_price) if _entry_price is not None else "N/A"
        _sl_txt = _fmt_price(sl) if sl is not None else "N/A"
        _tp1_txt = _fmt_price(tp1) if tp1 is not None else "N/A"
        _tp2_txt = _fmt_price(tp2) if tp2 is not None else None
        # ── 持倉時間過濾（維持 TP1=1:1，但淘汰慢訊號）──────────────────────
        try:
            _tp1_dist_pct = abs(float(tp1) - float(_entry_price)) / float(_entry_price) * 100 if (tp1 and _entry_price) else None
            _pc1h = x.get("priceChange1h")
            _mom_1h = abs(float(_pc1h)) if _pc1h is not None else 0.0
            _mom_need = MIN_1H_MOMENTUM_PCT
            if str(x.get("signal_version") or "") == "tier2":
                _mom_need = min(_mom_need, MIN_1H_MOMENTUM_TIER2_PCT)
            if _mom_1h < _mom_need:
                logger.info(
                    f"[時間過濾⏱️] {sym_base}: 1H動能 {_mom_1h:.2f}% < {_mom_need:.2f}%"
                    f"（{'tier2 門檻' if _mom_need < MIN_1H_MOMENTUM_PCT else '標準門檻'}），略過慢盤訊號"
                )
                continue
            if _tp1_dist_pct is not None:
                _est_hold_h = _tp1_dist_pct / max(_mom_1h, 0.05)
                _hold_cap = MAX_ESTIMATED_HOLD_HOURS
                if str(x.get("signal_version") or "") in ("confirmed", "exhaustion_reversal"):
                    _hold_cap = max(_hold_cap, MAX_ESTIMATED_HOLD_HOURS_CONFIRMED)
                if _est_hold_h > _hold_cap:
                    logger.info(
                        f"[時間過濾⏱️] {sym_base}: 預估到TP1約 {_est_hold_h:.1f}h > {_hold_cap:.1f}h，略過"
                    )
                    continue
        except Exception:
            pass

        # ── 點位（舊版樣式）──────────────────────────────────────────────
        msg_lines.append("**📌 點位**")
        msg_lines.append(f"進場 `{_entry_now_txt}`")
        _atr_zone = float(atr_val) * MARKET_ENTRY_ZONE_ATR if atr_val and isinstance(atr_val, (int, float)) else None
        _tp2_rr_msg = float(_calc_tp1_r_ratio(_entry_price, sl, tp2) or 0.0) if _entry_price and tp2 else 0.0
        if _tp2_rr_msg <= 0:
            _tp2_rr_msg = float(TP2_R_MULTIPLIER)
        _tp2_compact = (
            f" TP2 `{_tp2_txt}`（{_tp2_rr_msg:.1f}R 餘{int(TP2_EXIT_RATIO * 100)}%）"
            if _tp2_txt
            else ""
        )
        msg_lines.append(
            f"TP1 `{_tp1_txt}`（{TP1_R_MULTIPLIER:.1f}R 平{int(TP1_EXIT_RATIO * 100)}% 移SL至進場）"
            f"{_tp2_compact} ｜ SL `{_sl_txt}`"
        )
        if _atr_zone and _atr_zone > 0:
            _ideal_lo = _entry_price - _atr_zone
            _ideal_hi = _entry_price + _atr_zone
            msg_lines.append(
                f"市價帶 `{_fmt_price(_ideal_lo)}`～`{_fmt_price(_ideal_hi)}`（±{MARKET_ENTRY_ZONE_ATR:.2f}ATR）｜超區勿追"
            )
        else:
            msg_lines.append("市價帶 `—`")

        # ── 均價／錨（舊版樣式）────────────────────────────────────────
        msg_lines.append("**📊 均價／錨**")
        if _vwap_show is not None:
            _vwap_part = f"2h VWAP `{_fmt_price(_vwap_show)}`"
        else:
            _vwap_part = "2h VWAP —"
        if _vwap_anchor_show is not None:
            _anchor_time_s = ""
            if _vwap_anchor_ts_show > 0:
                try:
                    _anchor_time_s = " " + datetime.fromtimestamp(
                        float(_vwap_anchor_ts_show), tz=TAIPEI_TZ
                    ).strftime("%m-%d %H:%M")
                except (OSError, ValueError, OverflowError):
                    _anchor_time_s = ""
            _stars_inline = ("⭐" * _anch_fit_stars) if _anch_fit_stars > 0 else ""
            msg_lines.append(
                f"{_vwap_part} ｜ 錨 `{_fmt_price(_vwap_anchor_show)}`{_anchor_time_s}"
                f"{(' ' + _stars_inline) if _stars_inline else ''}"
            )
            _anchor_hint_disp = str(x.get("_anchor_sizing_hint") or "").strip()
            if _anchor_hint_disp:
                msg_lines.append(f"倉位：{_anchor_hint_disp}")
        else:
            msg_lines.append(f"{_vwap_part} ｜ 錨 —")

        # ── 環境（舊版）──────────────────────────────────────────────
        msg_lines.append("**🌍 環境**")
        msg_lines.append(f"4H：{_macro_trend} · {_macro_ema_txt}{_rsi_4h_str if _rsi_4h_str else ''} ｜ 模式 `市價`")
        _oi_story_line, _oi_risk_line = _build_oi_plain_lines()
        msg_lines.append(_oi_story_line)
        if "先看是否守住止損位" not in _oi_risk_line:
            msg_lines.append(_oi_risk_line)
        msg_lines.append(
            _fr_line.replace("💸 費率： ", "💸 **費率：** `").replace("% ", "%` ", 1)
            if _fr_line.startswith("💸 費率： ") and "無數據" not in _fr_line
            else _fr_line.replace("💸 費率： 無數據", "💸 **費率：** 無數據")
        )
        try:
            _btc_pen = float(_btc_1h_pct) if _btc_1h_pct is not None else None
        except (TypeError, ValueError):
            _btc_pen = None
        try:
            _btc_oi_txt_v = float(_btc_oi_1h_pct) if _btc_oi_1h_pct is not None else None
        except (TypeError, ValueError):
            _btc_oi_txt_v = None
        for _macro_ln in format_btc_macro_1h_plain_lines(_btc_pen, _btc_oi_txt_v):
            msg_lines.append(_macro_ln.replace("*BTC 大盤 1h*", "**BTC 大盤 1h（白話）**"))

        def _strategy_comment(cat: str, ver: str) -> str:
            if ver == "confirmed":
                if cat == "long_open":
                    return "主力三層共振建多倉，動能明確，右側追多機會！"
                if cat == "short_open":
                    return "主力三層共振建空倉，空頭動能確認，右側追空機會！"
                if cat in ("short_cover", "short_close"):
                    return "空方三層共振回補，軋空燃料充足，右側做多機會！"
                return "多方三層共振平倉，看空動能聚積，右側做空機會！"
            return "籌碼方向確認中，嚴守止損。"
        msg_lines.append(f"**💡** {_strategy_comment(category, _sig_version)}")
        if _vol_line:
            msg_lines.append(_vol_line.replace("📊 成交值 ", "📊 成交值 `").replace("M ", "M` ", 1))

        # 機讀資料：不貼在 Telegram（避免群組出現 JSON）；寫入 log + item 供後台／審計讀取
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
            "btc_oi_1h": _btc_oi_1h_pct if _btc_oi_1h_pct is not None else 0,
            "fr": _fr_ai,
            "rsi": _rsi_ai,
        }
        x["_sniper_ai_payload"] = ai_data
        logger.info("[SNIPER_AI_PAYLOAD] %s", json.dumps(ai_data, ensure_ascii=False))

        # ─ 儲存供後續使用 ─
        x["sl_price_str"]    = _fmt_price(sl)
        x["tp1_price_str"]   = _fmt_price(tp1)
        x["tp2_price_str"]   = _fmt_price(tp2)
        x["r_tp1"]           = round(_calc_tp1_r_ratio(_entry_price, sl, tp1) or 0.0, 3)
        x["r_tp2"]           = round(_calc_tp1_r_ratio(_entry_price, sl, tp2) or 0.0, 3)
        x["sl_source"]       = (
            f"結構防守 min/max(2H,EMA20,VWAP)+保底{MIN_SL_PERCENT*100:.1f}% "
            f"(TP1={TP1_R_MULTIPLIER}R)"
        )
        x["selected_for_push"] = True
        x["push_sl"] = float(sl) if sl is not None else None
        x["push_entry"] = float(_entry_price) if _entry_price is not None else None
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
            _entry_val = float(price)
        except Exception:
            _entry_val = None
        try:
            _vwap_val = float(vwap_2h_val) if vwap_2h_val is not None else None
        except Exception:
            _vwap_val = None
        _va_card = None
        try:
            if x.get("vwap_anchor") is not None:
                _va_card = float(x["vwap_anchor"])
                if _va_card != _va_card or _va_card <= 0:
                    _va_card = None
        except (TypeError, ValueError):
            _va_card = None
        try:
            _ast_card = int(x.get("_anchor_fit_stars") or 0)
        except (TypeError, ValueError):
            _ast_card = 0
        cards_payload.append(
            {
                "symbol_base": sym_base,
                "caption": _msg_str,
                "direction_is_long": is_bull_sig,
                "signal_version": _sig_version,
                "triggered_from_pending": bool(x.get("_triggered_from_pending")),
                "macro_badge": x.get("_macro_veto_badge"),
                "atr": x.get("atr"),
                "tp1_r": TP1_R_MULTIPLIER,
                "tp2_r": float(x["r_tp2"]) if x.get("r_tp2") is not None else TP2_R_MULTIPLIER,
                "sl": sl,
                "tp1": tp1,
                "tp2": tp2,
                "entry": _entry_val,
                "vwap": _vwap_val,
                "vwap_anchor": _va_card,
                "anchor_fit_stars": max(0, min(3, _ast_card)),
                "anchor_hint": str(x.get("_anchor_sizing_hint") or ""),
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
            f"🔍 *持倉異常狙擊鏡* 本輪無訊號\n"
            f"🕐 {now_str}  條件：1H OI≥動態門檻(主流3%/高流動5%/小幣7%) & 量≥{MTF_VOLUME_MIN_USD/1e6:.1f}M & MTF共振\n"
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
            f"BTC 若急跌時，同向標的常一併承壓，請留意大環境相關性風險。"
        )
    elif bear_count >= 3:
        correlation_warn = (
            f"\n{'─' * 20}\n"
            f"⚠️ *相關性警示：本輪 {bear_count} 個空單同時出現*\n"
            f"BTC 若急漲時，同向標的常一併承壓，請留意大環境相關性風險。"
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
        f"🔍 *持倉異常狙擊鏡*  本輪 {push_count} 個訊號\n"
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
    lines.append(f"🎯 *{stats_str}｜持倉狙擊鏡*{consensus_badge}")
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

                # 計算 SL/TP（OI 起漲點結構防守：Gate K 線為主）
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
                _entry_for_rr = x.get("current_price")
                _rr_real = _calc_tp1_r_ratio(_entry_for_rr, sl_val, tp1_val)
                x["r_tp1"] = round(_rr_real, 3) if _rr_real is not None else r_tp1
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
                if _rr_real is None or _rr_real < MIN_TP1_R_FOR_PUSH:
                    logger.info(
                        f"狙擊鏡跳過 {sym}: 實際TP1風報比 {_rr_real if _rr_real is not None else 'N/A'}R < "
                        f"{MIN_TP1_R_FOR_PUSH}R，不推播"
                    )
                    continue

                had_any_in_sub = True
                has_any = True
                push_count = push_count + 1  # noqa: (defined below at init)
                # 標記：此標的是「實際有推播」的訊號，供後續冷卻/倉位追蹤使用
                x["selected_for_push"] = True
                try:
                    _ps = (
                        float(str(sl_val).replace(",", ""))
                        if sl_val not in (None, "", "-")
                        else None
                    )
                except (TypeError, ValueError):
                    _ps = None
                x["push_sl"] = _ps
                try:
                    x["push_entry"] = float(x.get("current_price")) if x.get("current_price") is not None else None
                except (TypeError, ValueError):
                    x["push_entry"] = None
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
                        f"[推播] {sym} 現價={price_str} ATR={atr_for_log} 止損={sl_val} 止盈={tp1_val}{cap_note} | 數據源={source} (數據源偏差預警：Gate 多次失敗改用 CoinGlass)"
                    )
                else:
                    logger.info(
                        f"[推播] {sym} 現價={price_str} ATR={atr_for_log} 止損={sl_val} 止盈={tp1_val}{cap_note} | 數據源={source or 'Gate'}"
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
                        lines.append(f"✅ TP1({int(TP1_EXIT_RATIO*100)}%)：{_tp1_display} ({t1_note}){r1}")
                    else:
                        lines.append(f"✅ TP1({int(TP1_EXIT_RATIO*100)}%)：{_tp1_display}{r1}")
                    if _r2_val is not None:
                        lines.append(f"🎯 TP2({int(TP2_EXIT_RATIO*100)}%) 理論目標：`{_tp2_str}` (~{_r2_val:.1f}R)")
                else:
                    # 賭鬼：TP1（落袋70%）+ TP2 理論目標
                    lines.append(f"🛑 止損：{_sl_display}{_sl_dist_str}{_cap_tag}")
                    r1 = f" ({r_tp1}R)" if r_tp1 is not None else ""
                    lines.append(f"✅ TP1(落袋{int(TP1_EXIT_RATIO*100)}%)：{_tp1_display}{r1}")
                    if _r2_val is not None:
                        lines.append(f"🎯 TP2({int(TP2_EXIT_RATIO*100)}%) 理論目標：`{_tp2_str}` (~{_r2_val:.1f}R)")

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

        out_d: Dict[str, Any] = {
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
        # 1H OI 僅在原始 item 上；扁平化供 extract_oi_change_1h / Step1 大盤讀取
        _oi1h_snap = extract_oi_change_1h(out_d)
        if _oi1h_snap is not None:
            out_d["oiChange1h"] = _oi1h_snap
            out_d["open_interest_change_percent_1h"] = _oi1h_snap
        return out_d

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
    # Gate/Bitget 有 PLTR、GME、HK50 等代幣化商品，故意排除在外。
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
        _pc_err = "⚠️ 無法取得幣種漲跌資料，請稍後再試。"
        if jackbot_universal_pre_send_gatekeeper("position_change_error", text=_pc_err):
            send_telegram_message(_pc_err, TG_THREAD_IDS['position_change'])
        return
    logger.info(f"📊 [漏斗 1] CoinGlass 全網 {len(all_symbols_data)} 幣種")

    # ── 單次迴圈完成兩件事：BTC/ETH 大盤、24h快取 ──────────────────────────────
    global _btc_30m_pct, _btc_1h_pct, _btc_oi_1h_pct, _eth_30m_pct, _eth_1h_pct
    _btc_30m_pct = None
    _btc_1h_pct = None
    _btc_oi_1h_pct = None
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
            _btc_oi_1h_pct = extract_oi_change_1h(coin)
            logger.info(
                f"📊 [大盤參考] BTC 價格30m {(_btc_30m_pct or 0):+.2f}%  1H {(_btc_1h_pct or 0):+.2f}%"
                f" | BTC OI 1H {(_btc_oi_1h_pct or 0):+.2f}%"
            )

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

    # Gate 可交易白名單：僅保留 Gate USDT 永續存在的標的（降低用戶下單滑點/不可交易風險）
    gate_bases = fetch_gate_usdt_contract_bases()
    if gate_bases:
        _before_gate = len(active_symbols)
        active_symbols = [
            c for c in active_symbols
            if str((normalize_symbol(c) or "")).replace("USDT", "").replace("-", "").replace("_", "").upper() in gate_bases
        ]
        logger.info(
            f"[Gate白名單] 保留 {len(active_symbols)}/{_before_gate} 個可交易標的（Gate USDT 永續）"
        )

    if not coinglass_24h_map:
        coinglass_24h_map = _fetch_coinglass_24h_map()

    # ════════════════════════════════════════════════════════
    # Plan B：Gate 永續合約 24h USDT 成交值（備援，用於 CoinGlass 無資料的幣種）
    # 單一 API call，失敗時靜默回傳空 dict 不影響主流程
    # ════════════════════════════════════════════════════════
    _binance_vol_map: Dict[str, float] = fetch_bingx_futures_24h_vol()

    # ════════════════════════════════════════════════════════
    # 漏斗 Step 4：成交值預篩（三路來源：CoinGlass A → Binance B → 待 K 線估算 C）
    # 規則：
    #   combined_vol ≥ MTF_VOLUME_MIN_USD → 放行（門檻由頂部常數控制，預設 9.9M）
    #   combined_vol = 0                  → A+B 均無資料 → 放行，等 K 線估算（Plan C）
    #   0 < combined_vol < MTF_VOLUME_MIN_USD → 確認流動性不足 → 過濾
    # ════════════════════════════════════════════════════════
    VOLUME_PREFILTER_MIN_USD = MTF_VOLUME_MIN_USD  # 從常數讀取（預設 9.9M，可用 MTF_VOLUME_MIN_USD 覆寫）

    active_above_volume: List[Dict[str, Any]] = []
    vol_cg = 0         # Plan A (CoinGlass) 有資料且 ≥ MTF_VOLUME_MIN_USD
    vol_binance = 0    # Plan B (Gate備援) 補救且 ≥ MTF_VOLUME_MIN_USD
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
                _vol_source = "Gate"

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
        f"（CoinGlass: {vol_cg} | Gate備援: {vol_binance} | 待K線估算: {vol_no_data} | 淘汰[確認<{MTF_VOLUME_MIN_USD/1e6:.1f}M]: {vol_below}）"
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
    logger.info(
        f"【OI門檻】強制分層：主流 {OI_THRESHOLD_MAIN:.0f}% / "
        f"高流動 {OI_THRESHOLD_HIGH_LIQ:.0f}% / 小幣 {OI_THRESHOLD_SMALL:.0f}%"
    )
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
    _top_total = (
        len(top_long_open) + len(top_long_close) + len(top_short_open) + len(top_short_close)
    )
    if _top_total == 0:
        logger.info(
            "[守門員] 本輪不會檢查：1H OI 達門檻後 TOP 候選為 0 筆"
            "（守門員在 enrichment 與版本篩選之後才執行，前面無標的則無守門員日誌）"
        )

    # ════════════════════════════════════════════════════════
    # Enrichment：核心資料（CoinGlass 技術指標 + 資金費率）
    # ════════════════════════════════════════════════════════
    _cg_fr_map: Dict[str, float] = _fetch_funding_rate_map()
    logger.info(f"[FR批次] CoinGlass Funding Rate 預載完成，共 {len(_cg_fr_map)} 個幣種")
    _cg_td15_map: Dict[str, Optional[float]] = fetch_cg_td_map_for_interval("15m")
    logger.info(f"[TD批次] CoinGlass TD list 15m 預載完成，共 {len(_cg_td15_map)} 個幣種")
    _cg_liq_map: Dict[str, Dict[str, float]] = fetch_cg_liq_coin_map()
    logger.info(f"[爆倉批次] CoinGlass liquidation coin-list 預載完成，共 {len(_cg_liq_map)} 個幣種")
    _cg_rsi15_ref_map: Dict[str, Optional[float]] = fetch_cg_rsi_bulk("15m")
    logger.info(f"[RSI批次] CoinGlass rsi/list 15m 預載完成，共 {len(_cg_rsi15_ref_map)} 個幣種（與 K 線 RSI 交叉驗證）")

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

        # 技術指標：主框架維持 1h；防守點另取 15m EMA 回踩位（縮短持倉時間）
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
        # 15m 技術快照（僅用於 SL 防守點：EMA20/EMA100 回踩位）
        tech_15m = calculate_technicals(sym, interval="15m", limit=120)
        _ema20_sl = (tech_15m or {}).get("ema20_close") or tech.get("ema20_close")
        _ema100_sl = (tech_15m or {}).get("ema100_close") or tech.get("ema100_close")
        _ema20_touch_low_sl = (tech_15m or {}).get("ema20_touch_low") or tech.get("ema20_touch_low")
        _ema20_touch_high_sl = (tech_15m or {}).get("ema20_touch_high") or tech.get("ema20_touch_high")
        _ema100_touch_low_sl = (tech_15m or {}).get("ema100_touch_low")
        _ema100_touch_high_sl = (tech_15m or {}).get("ema100_touch_high")
        _atr_sl = (tech_15m or {}).get("atr") or tech.get("atr")

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
        # CoinGlass 有 price 的幣優先用 CoinGlass；Gate-only 幣（price=None）
        # 用 1H K線收盤（tech.current_price）作備援，確保 4H EMA 比對不失效
        _cur_price_prelim = item.get("price") or (tech.get("current_price") if tech else None)
        _is_above_4h_ema  = (
            bool(_cur_price_prelim > _ema20_4h)
            if (_cur_price_prelim and _ema20_4h and _ema20_4h > 0)
            else None
        )

        # 資金費率：CoinGlass 批次表（純 CoinGlass 模式，不再呼叫 Gate fallback）
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
        atr_val = _atr_sl

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

        # ── K 線新鮮度驗證（防止 Gate/Bybit 回傳舊蠟燭導致進場價嚴重偏差）──────────
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

        # Step 2 衝突改為「降級不阻斷」：
        # 30m 與 1H 方向相反時，不再直接淘汰；保留進入後續流程，交由 MTF 分級為逆勢/觀察。
        _is_1h_bull_ctx = cat in ("long_open", "short_cover")
        _is_1h_bear_ctx = cat in ("short_open", "long_close")
        _tf_conflict_soft = False
        if _cat_30m_prelim is not None:
            if (_is_1h_bull_ctx and _cat_30m_prelim == "short_open") or \
               (_is_1h_bear_ctx and _cat_30m_prelim == "long_open"):
                _tf_conflict_soft = True
                logger.info(
                    f"[Step2⚠️方向衝突] {sym}: 30m={_cat_30m_prelim} 與 1H={cat} "
                    f"方向相反，降級為觀察/逆勢候選，續跑 15m+5m 檢查"
                )

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
            "tf_conflict_soft": _tf_conflict_soft,
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
            if (
                tech
                and tech.get("vwap_2h_volume_weighted", True)
                and _vwap
                and _cur_price
                and float(_vwap) > 0
            ):
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
            tech.get("atr") if tech else None,
            bool(tech.get("vwap_2h_volume_weighted", True)),
        )

        _anchor_snap: Optional[Dict[str, Any]] = None
        if SNIPER_ANCHOR_ENABLED and _effective_version in (
            "confirmed",
            "pullback",
            "exhaustion_reversal",
            "tier2",
        ):
            try:
                time.sleep(0.12)
                _anchor_snap = compute_anchored_launch_vwap_snapshot(clean_base)
            except Exception as _ae:
                logger.debug(f"[AnchoredVWAP] {clean_base}: {_ae}")
                _anchor_snap = None

        _td15_val = _cg_td15_map.get(_base_fr)
        if _td15_val is None and str(_base_fr).startswith("1000"):
            _td15_val = _cg_td15_map.get(str(_base_fr)[4:])

        _liq_row = _cg_liq_map.get(_base_fr)
        if _liq_row is None and str(_base_fr).startswith("1000"):
            _liq_row = _cg_liq_map.get(str(_base_fr)[4:])
        _rsi_cg_ref = _cg_rsi15_ref_map.get(_base_fr)
        if _rsi_cg_ref is None and str(_base_fr).startswith("1000"):
            _rsi_cg_ref = _cg_rsi15_ref_map.get(str(_base_fr)[4:])

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
            "ema20": _ema20_sl,
            "ema100": _ema100_sl,
            "ema20_touch_low": _ema20_touch_low_sl,
            "ema20_touch_high": _ema20_touch_high_sl,
            "ema100_touch_low": _ema100_touch_low_sl,
            "ema100_touch_high": _ema100_touch_high_sl,
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
            "vwap_2h_volume_weighted": bool(tech.get("vwap_2h_volume_weighted", True)),
            # 發動錨定 VWAP（5m 爆量；優先 OI 視窗共振，否則僅量能錨；自錨點起 VWAP）
            "vwap_anchor": _anchor_snap.get("vwap_anchor")
            if isinstance(_anchor_snap, dict) and _anchor_snap.get("ok")
            else None,
            "vwap_anchor_ts": _anchor_snap.get("anchor_ts")
            if isinstance(_anchor_snap, dict) and _anchor_snap.get("ok")
            else None,
            "vwap_anchor_vol_ratio": _anchor_snap.get("vol_ratio")
            if isinstance(_anchor_snap, dict) and _anchor_snap.get("ok")
            else None,
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
            # TD Sequential（15m，list 批次）：供 _calc_signal_grade 加分／減分
            "cg_td_15m": _td15_val,
            # CoinGlass 全市場爆倉（coin-list 1h）：守門員硬擋 + 評分加減
            "cg_liq_long_1h_usd": (_liq_row or {}).get("long_1h"),
            "cg_liq_short_1h_usd": (_liq_row or {}).get("short_1h"),
            "cg_liq_total_1h_usd": (_liq_row or {}).get("total_1h"),
            # CoinGlass rsi/list 15m：與 K 線 RSI 交叉驗證（資料來源分歧時降權）
            "cg_rsi_15m_ref": _rsi_cg_ref,
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

    # 品質門撒②：成交值仍未確認（三路均無資料：CoinGlass / Gate / K線估算全失敗）
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

    # 品質門撒③（微調）：OI 續航軟過濾
    # - 以前是硬淘汰，訊號容易被砍光
    # - 現在改成只淘汰「15m+5m 都明顯反向」；其餘降權放行
    # - 目的：維持抗畫門能力，同時避免 0 訊號
    def _oi_flow_consistent(_x: Dict) -> bool:
        _cat = (_x.get("category") or "").strip()
        try:
            _oi15 = float(_x.get("oiChange_15m") or 0.0)
            _oi5 = float(_x.get("oiChange_5m") or 0.0)
        except (TypeError, ValueError):
            # 無 15m/5m 資料不直接砍，交由後續評分處理
            return True
        _is_open = _cat in ("long_open", "short_open")
        _is_close = _cat in ("long_close", "short_close")
        if not (_is_open or _is_close):
            return False
        # 持倉「建倉」理論上 15m/5m 應偏正；若雙週期都明顯反向才淘汰
        if _is_open:
            if _oi15 <= -0.35 and _oi5 <= -0.20:
                return False
            return True
        # 持倉「平倉」理論上 15m/5m 應偏負；若雙週期都明顯反向才淘汰
        if _oi15 >= 0.35 and _oi5 >= 0.20:
            return False
        return True

    _pre_oi_flow = len(all_top)
    all_top = [x for x in all_top if _oi_flow_consistent(x)]
    _drop_oi_flow = _pre_oi_flow - len(all_top)
    if _drop_oi_flow > 0:
        logger.info(
            f"[品質門撒③ OI續航(軟過濾)] 淘汰 {_drop_oi_flow} 個『15m+5m雙週期明顯反向』訊號，"
            f"剩餘 {len(all_top)} 個"
        )

    # 訊號版本門檻（Classic 80%）：
    # - 保留 confirmed / exhaustion_reversal
    # - 放行 tier2（觀察轉實戰）與 pullback（回踩跟隨）
    #   讓節奏更接近 2 月短線狙擊版本，不再只剩極少數訊號
    _ALLOW_PUSH_SIGNAL_VERSIONS = frozenset({"confirmed", "exhaustion_reversal", "tier2", "pullback"})
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

    _gk_pre = len(all_top)
    all_top = [x for x in all_top if sniper_coinglass_gatekeeper_allow(x)]
    _gk_drop = _gk_pre - len(all_top)
    if _gk_drop > 0:
        logger.info(
            f"[守門員] CoinGlass 規則擋下 {_gk_drop} 筆，剩餘 {len(all_top)} 筆"
        )
    elif _gk_pre > 0:
        logger.info(f"[守門員] 檢查 {_gk_pre} 筆，0 筆擋下，全部通過")
    else:
        logger.info(
            "[守門員] 本輪未檢查：進入守門員前列表為 0 筆"
            "（訊號在更早的 MTF／OI／品質門／版本篩選已清空）"
        )

    _confirmed_cnt = sum(1 for x in all_top if x.get("signal_version") == "confirmed")
    _exhaust_cnt   = sum(1 for x in all_top if x.get("signal_version") == "exhaustion_reversal")
    _tier2_cnt     = sum(1 for x in all_top if x.get("signal_version") == "tier2")
    _pullback_cnt = sum(1 for x in all_top if x.get("signal_version") == "pullback")
    logger.info(
        f"[Enrichment 完成] {len(all_top)} 個訊號進入推播流程"
        f"（✅確定籌碼 {_confirmed_cnt} | 🔥衰竭反轉 {_exhaust_cnt}"
        f"{' | ⚠️Tier2 ' + str(_tier2_cnt) if _tier2_cnt else ''}"
        f"{' | ↩️回踩 ' + str(_pullback_cnt) if _pullback_cnt else ''}）"
    )
    if len(all_top) == 0:
        logger.info(f"本輪無符合條件訊號（1H OI≥動態門檻 & 成交值≥{MTF_VOLUME_MIN_USD/1e6:.1f}M USD & MTF共振未達標）")

    # 冷卻規則：同幣同方向 N 小時內不重複推；反向訊號另設「最短間隔」避免短時間連發打架
    # 統一預設 2 小時冷卻（同幣同方向）；需其他值可設 SNIPER_COOLDOWN_HOURS
    _default_cd_hours = 2.0
    COOLDOWN_HOURS = int(max(1, round(_env_float("SNIPER_COOLDOWN_HOURS", _default_cd_hours))))
    # 同幣反向訊號最短間隔（分鐘）：預設 45 分鐘，避免半小時內同標的多空連炸
    COOLDOWN_OPPOSITE_MINUTES = int(max(0, round(_env_float("SNIPER_COOLDOWN_OPPOSITE_MINUTES", 45))))
    cooldown_opp_sec = COOLDOWN_OPPOSITE_MINUTES * 60
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
    pending_signals: List[Dict] = []
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
        pending_signals = _gist_data.get("pending_signals") or []
        _in_window = sum(1 for e in history if isinstance(e, dict) and (now_ts - e.get("ts", 0)) <= cooldown_sec)
        logger.info(f"冷卻檔已讀取(Gist): history {len(history)} 筆，{COOLDOWN_HOURS}h 內 {_in_window} 筆")

    try:
        with _sniper_file_lock():
            if SNIPER_COOLDOWN_FILE.exists() and _gist_data is None:
                raw = json.loads(SNIPER_COOLDOWN_FILE.read_text(encoding="utf-8"))
                history = raw.get("history") or []
                pending_signals = raw.get("pending_signals") or []
                # 相容舊格式：只有 last_round 時轉成 history
                if not history and raw.get("last_round"):
                    last_round = raw.get("last_round") or []
                    if last_round and isinstance(last_round[0], dict):
                        history = [{"symbol": str(p.get("symbol")), "dir": str(p.get("dir")), "ts": int(now_ts) - 3600} for p in last_round if p.get("symbol") and p.get("dir")]
                    else:
                        history = [{"symbol": str(p[0]), "dir": str(p[1]), "ts": int(now_ts) - 3600} for p in last_round if isinstance(p, (list, tuple)) and len(p) >= 2]
                if "pending_signals" not in raw:
                    pending_signals = []
                logger.info(f"冷卻檔已讀取: {_cooldown_path_abs} | 歷史 {len(history)} 筆")
            else:
                if _gist_data is None:
                    logger.info(f"冷卻狀態檔不存在，本輪無冷卻限制: {_cooldown_path_abs}")
    except Exception as e:
        history = []
        pending_signals = []
        logger.warning(f"讀取冷卻狀態檔失敗，本輪無冷卻限制: {e}")

    now_tw = datetime.fromtimestamp(now_ts, tz=TAIPEI_TZ)
    _in_window = sum(1 for e in history if isinstance(e, dict) and (now_ts - e.get("ts", 0)) <= cooldown_sec)
    logger.info(
        f"冷卻狀態: {len(history)} 筆歷史，{COOLDOWN_HOURS}h 內 {_in_window} 筆（同幣同方向才冷卻）"
        f" | pending={len(pending_signals)}"
    )

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

    # ── Macro Veto：大盤與山寨方向對撞時可略過（SNIPER_MACRO_VETO_MODE，預設 relaxed）──
    macro_ctx = _macro_regime_snapshot()
    _macro_filtered: List[Dict] = []
    for x in all_top:
        _base = _cooldown_symbol(x.get("symbol") or "")
        x["_macro_veto_badge"] = macro_ctx.get("badge")
        # BTC/ETH 訊號不做 veto，僅作環境提示
        if _base in ("BTC", "ETH"):
            _macro_filtered.append(x)
            continue
        if _macro_veto_should_skip_alt(_base, x, macro_ctx, _btc_1h_pct):
            continue
        _macro_filtered.append(x)
    all_top = _macro_filtered

    # ── Pending Pool 巡邏：先審查既有待辦（觸發/淘汰/保留）──────────────────────
    def _pending_live_price(symbol: str) -> Optional[float]:
        try:
            snap = _fetch_bingx_ticker_snapshot(symbol)
            if snap and snap.get("price"):
                return float(snap.get("price"))
        except Exception:
            return None
        return None

    pending_kept: List[Dict] = []
    pending_triggered: List[Dict] = []
    for p in pending_signals:
        if not isinstance(p, dict):
            continue
        p_sym = p.get("symbol")
        p_dir = p.get("dir")
        if not p_sym or p_dir not in ("long", "short"):
            continue
        p_now = _pending_live_price(p_sym)
        if p_now is None or p_now <= 0:
            pending_kept.append(p)
            continue
        p_expire = int(p.get("expire_ts") or 0)
        p_sl = float(p.get("sl") or 0)
        p_tp1 = float(p.get("tp1") or 0)
        p_lo = float(p.get("entry_zone_low") or 0)
        p_hi = float(p.get("entry_zone_high") or 0)
        if p_expire and now_ts >= p_expire:
            continue
        if p_dir == "long" and (p_now <= p_sl or p_now >= p_tp1):
            continue
        if p_dir == "short" and (p_now >= p_sl or p_now <= p_tp1):
            continue
        if p_lo <= p_now <= p_hi:
            payload = p.get("signal_payload") or {}
            if isinstance(payload, dict):
                payload["current_price"] = p_now
                payload["_triggered_from_pending"] = True
                payload["_macro_veto_badge"] = macro_ctx.get("badge")
                pending_triggered.append(payload)
            continue
        pending_kept.append(p)

    # ── Pending Pool 寫入：已噴發訊號先放待辦池，不追價 ─────────────────────────
    filtered_for_pending: List[Dict] = []
    _pending_stashed_this_run = 0  # 本輪新進待辦（不併入即時推播）的檔數，供日誌說明
    for x in all_top:
        _atr = x.get("atr")
        _vwap = x.get("vwap_2h")
        _ema = x.get("ema20") or x.get("ema20_close")
        _sig_ver = x.get("signal_version") or ""
        _mtf_ok = int(x.get("mtf_aligned") or 0) >= 3
        try:
            _atr_f = float(_atr) if _atr is not None else None
            _price_f = float(x.get("current_price")) if x.get("current_price") is not None else None
            _vwap_f = float(_vwap) if _vwap is not None else None
            _ema_f = float(_ema) if _ema is not None else None
        except (TypeError, ValueError):
            _atr_f, _price_f, _vwap_f, _ema_f = None, None, None, None
        if not (_sig_ver in ("confirmed", "pullback") and _mtf_ok and _atr_f and _price_f and (_vwap_f or _ema_f)):
            filtered_for_pending.append(x)
            continue
        # 已觸發「動能透支→限價 EMA」的確定籌碼：代表已接受偏離，不再塞待辦池以免整輪無推播
        if bool(x.get("_energy_exhausted")):
            filtered_for_pending.append(x)
            continue
        _anchor = _ema_f if _ema_f and _ema_f > 0 else _vwap_f
        if not _anchor or abs(_price_f - _anchor) <= _atr_f * PENDING_PUMP_ATR_MULTIPLIER:
            filtered_for_pending.append(x)
            continue
        _is_long = (x.get("category") or "") in ("long_open", "short_close")
        _entry_ref = _anchor
        _entry_lo = _entry_ref - (_atr_f * MARKET_ENTRY_ZONE_ATR)
        _entry_hi = _entry_ref + (_atr_f * MARKET_ENTRY_ZONE_ATR)
        _vwap_pending = _vwap_f if x.get("vwap_2h_volume_weighted", True) else None
        _sl, _tp1, _tp2, _one_r_pd, _ = compute_structural_sl_tp(
            _entry_ref,
            _is_long,
            _vwap_pending,
            _ema_f,
            x.get("ema100") or x.get("ema100_close"),
            x.get("recent_low_2h"),
            x.get("recent_high_2h"),
            _atr_f,
            x.get("ema20_touch_low"),
            x.get("ema20_touch_high"),
            x.get("ema100_touch_low"),
            x.get("ema100_touch_high"),
        )
        if _sl is None or _tp1 is None:
            filtered_for_pending.append(x)
            continue
        _bpd = _cooldown_symbol(x.get("symbol") or "")
        _tp2, _ = refine_tp2_box_measured_move(
            _bpd,
            float(_entry_ref),
            _is_long,
            float(_tp1),
            float(_tp2),
            float(_one_r_pd),
            _atr_f,
        )
        _pid = f"{_cooldown_symbol(x.get('symbol') or '')}_{'long' if _is_long else 'short'}_{int(now_ts)}"
        pending_kept.append({
            "id": _pid,
            "symbol": x.get("symbol"),
            "base": _cooldown_symbol(x.get("symbol") or ""),
            "dir": "long" if _is_long else "short",
            "grade": x.get("grade"),
            "source_version": _sig_ver,
            "created_ts": int(now_ts),
            "expire_ts": int(now_ts + PENDING_TTL_HOURS * 3600),
            "entry_ref_price": _entry_ref,
            "entry_zone_low": _entry_lo,
            "entry_zone_high": _entry_hi,
            "entry_anchor": {"ema20": _ema_f, "vwap_2h": _vwap_f, "atr": _atr_f},
            "sl": _sl,
            "tp1": _tp1,
            "tp2": _tp2,
            "rr_plan": {"tp1_r": TP1_R_MULTIPLIER, "tp2_r": TP2_R_MULTIPLIER, "min_push_r": MIN_TP1_R_FOR_PUSH},
            "market_context": {"macro_veto_badge": macro_ctx.get("badge")},
            "signal_payload": x,
        })
        _pending_stashed_this_run += 1
        logger.info(f"[PendingPool] {_cooldown_symbol(x.get('symbol') or '')}: 偏離主力成本過大，寫入待辦池")
    all_top = filtered_for_pending
    if _pending_stashed_this_run:
        logger.info(
            f"[PendingPool📌] 本輪 {_pending_stashed_this_run} 檔因「現價偏離 EMA/VWAP "
            f"> {PENDING_PUMP_ATR_MULTIPLIER:g}×ATR」進**待辦池**，**本輪不發即時推播**"
            f"（拉回進價區後會併入下輪觸發）；即時推播候選剩 {len(all_top)} 檔。"
            f" 若要放寬門檻可設環境變數 SNIPER_PENDING_PUMP_ATR（預設 0.5）。"
        )

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
        # 同幣反向：短時間內阻擋（避免「同標的多空連炸」）
        _opp_last_ts = last_push_ts_by_sym_dir.get((sym_norm, _opp_dir))
        if (
            cooldown_opp_sec > 0
            and _opp_last_ts
            and (now_ts - float(_opp_last_ts)) < cooldown_opp_sec
        ):
            _mins = (now_ts - float(_opp_last_ts)) / 60.0
            logger.info(
                f"冷卻跳過: {sym_norm} ({cur_dir})（距反向訊號僅 {_mins:.1f} 分鐘 < {COOLDOWN_OPPOSITE_MINUTES} 分鐘）"
            )
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

    if pending_triggered:
        cooled_top.extend(pending_triggered)
        logger.info(f"[PendingPool✅] 觸發完美回踩 {len(pending_triggered)} 檔，併入本輪推播候選")

    # 依方向分組後以 |1H OI%| 排序（大者在前）；不設每方向檔數上限
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
    _cooled_sorted: List = []
    for _dkey in ("多", "空"):
        _lst = _by_dir_lists[_dkey]
        _lst.sort(key=_oi_abs_round_cap, reverse=True)
        _cooled_sorted.extend(_lst)
    cooled_top = _cooled_sorted

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
                    _vwap_2h = (
                        _x.get("vwap_2h")
                        if _x.get("vwap_2h_volume_weighted", True)
                        else None
                    )
                    _ema_rt = _x.get("ema20") or _x.get("ema20_close")
                    # 與推播一致：僅市價進場，結構 SL/TP 一律以即時價為進場基準
                    _entry_rt = _live
                    _sl_i, _tp1_i, _tp2_i, _one_ri, _slp_rt = compute_structural_sl_tp(
                        _entry_rt,
                        _is_long_rt,
                        _vwap_2h,
                        _ema_rt,
                        _x.get("ema100") or _x.get("ema100_close"),
                        _x.get("recent_low_2h"),
                        _x.get("recent_high_2h"),
                        _x.get("atr"),
                        _x.get("ema20_touch_low"),
                        _x.get("ema20_touch_high"),
                        _x.get("ema100_touch_low"),
                        _x.get("ema100_touch_high"),
                    )
                    if _sl_i is None or _tp1_i is None:
                        continue

                    _blocked = False
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

            def _gk_trade_payload_for_card_base(base: str) -> Optional[Dict[str, Any]]:
                for _itm in cooled_top or []:
                    if not _itm.get("selected_for_push"):
                        continue
                    if _cooldown_symbol(_itm.get("symbol") or "") != base:
                        continue
                    return sniper_coinglass_trade_payload_from_enriched_item(_itm)
                return None

            for idx, payload in enumerate(cards_payload or []):
                sym_b = payload.get("symbol_base") or ""
                if not sym_b:
                    continue
                caption_txt = payload.get("caption") or ""
                if not caption_txt:
                    continue
                _gk_card = _gk_trade_payload_for_card_base(sym_b)
                if _gk_card is not None:
                    if not jackbot_universal_pre_send_gatekeeper(
                        "position_change",
                        text=caption_txt,
                        coinglass_trade=_gk_card,
                    ):
                        logger.info("[守門員·position_change] 推播前覆核擋下 %s", sym_b)
                        continue
                elif not jackbot_universal_pre_send_gatekeeper("position_change", text=caption_txt):
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
                            vwap_anchor=_posf(payload.get("vwap_anchor")),
                            anchor_fit_stars=int(payload.get("anchor_fit_stars") or 0),
                            anchor_hint=str(payload.get("anchor_hint") or ""),
                            ema20=payload.get("ema20"),
                            ema20_touch_low=payload.get("ema20_touch_low"),
                            ema20_touch_high=payload.get("ema20_touch_high"),
                            ema20_4h=payload.get("ema20_4h"),
                            atr=payload.get("atr"),
                            tp1_r=payload.get("tp1_r"),
                            tp2_r=payload.get("tp2_r"),
                            macro_badge=payload.get("macro_badge"),
                            signal_version=payload.get("signal_version"),
                            triggered_from_pending=bool(payload.get("triggered_from_pending")),
                            out_path=img_path,
                            title_line=f"{sym_b} | 5m×60  K+OI | 青線=2h主力VWAP 金線=錨定發動VWAP",
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
                            # 只在「可判定的格式/長度錯誤」才做 TG 文字備援，避免網路逾時造成 TG 圖+文雙發
                            _fr = (_LAST_TG_PHOTO_FAILURE_REASON or "").lower()
                            _fallback_safe = any(
                                k in _fr for k in (
                                    "can't parse entities",
                                    "caption is too long",
                                    "message caption is too long",
                                    "text is too long",
                                    "bad request",
                                )
                            )
                            if _fallback_safe:
                                send_telegram_message(
                                    caption_txt,
                                    TG_THREAD_IDS['position_change'],
                                    parse_mode="Markdown",
                                    mirror_discord=False,
                                )
                            else:
                                logger.warning(
                                    f"[K線卡片TG備援略過] {sym_b}: 非確定性失敗（{_LAST_TG_PHOTO_FAILURE_REASON}），"
                                    "為避免 TG 雙發，不做文字補發"
                                )
                    except Exception as e:
                        logger.warning(f"[K線卡片渲染/推送失敗] {sym_b}: {e}；改推文字")
                        send_telegram_message(
                            caption_txt,
                            TG_THREAD_IDS['position_change'],
                            parse_mode="Markdown",
                            mirror_discord=False,
                        )
                else:
                    logger.warning(
                        f"[K線卡片跳過] {sym_b}: fetch_ohlc_5m 回傳不足 "
                        f"(ohlc_len={len(ohlc) if ohlc else None})；改推文字"
                    )
                    # 此路徑未嘗試過 sendPhoto，DC 尚未收到內容 → 維持預設同步 DC
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
                f"| OI 門檻 | 分層：主流 {OI_THRESHOLD_MAIN:.0f}% / "
                f"高流動 {OI_THRESHOLD_HIGH_LIQ:.0f}% / 小幣 {OI_THRESHOLD_SMALL:.0f}% |",
                f"| 進入 TOP 候選數 | {len(all_top)} |",
                f"| 最終推播標的數 | {len(cooled_top)} |",
                f"| 推播標的列表 | {pushed_list} |",
                "",
            ]
            with open(step_summary_path, "a", encoding="utf-8") as f:
                f.write("\n".join(summary_lines) + "\n")
        except Exception as e:
            logger.warning(f"寫入 GitHub Step Summary 失敗: {e}")

    # 寫回冷卻狀態（history + pending_signals；向下相容平滑升級）
    try:
        SNIPER_COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
        new_entries = [
            {"symbol": s, "dir": d, "grade": g, "ts": int(now_ts)}
            for (s, d, g) in pairs_this_run
            if s
        ]
        history = history + new_entries
        history = [e for e in history if isinstance(e, dict) and (now_ts - e.get("ts", 0)) <= history_sec]
        state = {
            "version": 2,
            "history": history,
            "pending_signals": pending_kept if isinstance(pending_kept, list) else [],
        }
        _emergency_sniper_state = state
        with _sniper_file_lock():
            save_json_file(SNIPER_COOLDOWN_FILE, state)
        logger.info(
            f"冷卻檔已寫入: 本輪 {len(new_entries)} 筆，歷史共 {len(history)} 筆 (保留 {HISTORY_HOURS}h)"
            f" | pending={len(state['pending_signals'])}"
        )
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


def is_headline_macro_calendar_push(item: Dict) -> bool:
    """
    即時推播用：API 常把 importance=3 標很寬，這裡再收斂到「市場定價錨」級事件。
    - ≥4：一律推
    - =3：須符合關鍵字（利率／就業／物價／央行決策等）
    """
    imp = int(item.get("importance_level") or item.get("importance") or 0)
    if imp < 3:
        return False
    if imp >= 4:
        return True
    name = str(item.get("calendar_name") or item.get("name") or item.get("title") or "")
    blob = name
    u = blob.upper()
    keys = (
        "非農", "NFP", "NON-FARM", "就業人數", "失業率",
        "CPI", "PPI", "物價", "PCE", "通膨", "通脹",
        "GDP",
        "FOMC", "聯邦基金", "利率決議", "INTEREST RATE", "FED", " POWELL", "鮑威爾", "記者會",
        "ISM", "PMI",
        "零售銷售", "RETAIL SALES",
        "初請", "JOBLESS", "JOLTS", "ADP",
        "褐皮書", "BEIGE BOOK",
        "ECB", "BOE", "BOJ", "央行", "升息", "降息", "殖利率", "TREASURY", "債券拍賣",
    )
    for k in keys:
        if k in u or k in blob:
            return True
    return False


def get_why_macro_matters_plain(data: Dict) -> str:
    """白話一句：這類數據通常影響哪一層、為什麼市場會動。"""
    name = str(data.get("calendar_name") or data.get("name") or data.get("title") or "")
    blob = (name + " " + str(data.get("country_name") or data.get("country") or "")).upper()
    pairs = (
        (("CPI", "PPI", "PCE", "物價", "通膨", "通脹"), "物價／通膨數據會改寫市場對「未來利率」的預期；利率預期一動，股債匯與風險資產（含加密）的資金成本與估值錨就跟著重定價。"),
        (("非農", "NFP", "就業", "失業", "JOBLESS", "JOLTS", "ADP"), "就業是景氣溫度計；強就業常支撐「利率偏高更久」的敘事，對高風險資產的流動性偏好通常偏壓抑，反之亦然。"),
        (("GDP",), "GDP 描述整體需求強弱；偏弱時市場容易往「寬鬆／衰退交易」靠，波動與避險情緒常升溫。"),
        (("零售", "RETAIL SALES",), "消費占需求很大塊；零售意外走強或走弱，會牽動「軟著陸 vs 衰退」劇本，短線風險偏好常跟著擺盪。"),
        (("FOMC", "利率", "聯邦基金", "INTEREST RATE", "Fed", "鮑威爾", "Powell", "ECB", "BOE", "BOJ", "央行"), "央行利率路徑是全局定價之母：一點點預期落差，就足以讓美元、公債殖利率與風險資產同步重估。"),
        (("ISM", "PMI",), "PMI／ISM 偏景氣領先指標；擴張或萎縮的意外，常直接打在「景氣循環交易」上，波動常放大。"),
        (("GDP平減", "DEFLATOR",), "平減指數也帶物價成分，市場會拿來交叉驗證通膨黏性。"),
    )
    for keys, text in pairs:
        for k in keys:
            if k in blob or k in name:
                return text
    return "這類日程多半牽動「利率預期、景氣預期、美元與避險情緒」其中至少一項；加密沒有獨立於宏觀的定價，所以短線常跟著風險資產一起反應。"


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
    _why_plain = get_why_macro_matters_plain(data)
    lines.append(f"🧭 *白話為什麼重要*：{_why_plain}")
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
        if not jackbot_universal_pre_send_gatekeeper("economic_data_preview", text=message):
            return
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
        
        # 先取 importance≥3，再以關鍵字收斂（避免 API 標級過寬導致次要事件狂推）
        important_data = [
            x for x in filter_important_data(all_data, min_importance=3)
            if is_headline_macro_calendar_push(x)
        ]
        logger.info(f"過濾後的宏觀頭條級數據: {len(important_data)} 條")
        
        if not important_data:
            logger.info("沒有符合條件的宏觀頭條級數據（importance 或關鍵字未達標）")
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
                if not jackbot_universal_pre_send_gatekeeper("economic_data", text=message):
                    continue
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
        _econ_err = "⚠️ 經濟數據暫時無法取得，請稍後再試。"
        if jackbot_universal_pre_send_gatekeeper("economic_data_error", text=_econ_err):
            send_telegram_message(_econ_err, TG_THREAD_IDS['economic_data'])


# ==================== 5. 新聞快訊推特中文推播 ====================

LAST_NEWS_TIME_FILE = DATA_DIR / "last_news_time.json"
COINGLASS_ARTICLE_IDS_FILE = DATA_DIR / "coinglass_article_ids.json"
COINGLASS_NEWSFLASH_IDS_FILE = DATA_DIR / "coinglass_newsflash_ids.json"


def _news_escape_md_light(s: Optional[str]) -> str:
    """標題／內文若含 * _ ` 等字元會弄壞 Telegram Markdown，先做輕量替換。"""
    if s is None:
        return ""
    x = str(s)
    for a, b in (
        ("\\", "／"),
        ("*", "∗"),
        ("_", "＿"),
        ("`", "′"),
        ("[", "［"),
        ("]", "］"),
    ):
        x = x.replace(a, b)
    return x


def process_and_send(news: Dict, source: str):
    """翻譯並發送 Tree of Alpha 新聞到 Telegram"""
    raw_title = news.get("title") or ""
    translated_title = _news_escape_md_light(translate_text(raw_title))
    url = (news.get("url") or "").strip()

    lines = [
        "📰 *【全球幣圈即時快訊】*",
        "────────────────",
        "📍 *來源* · `Tree of Alpha`",
        "",
        f"🔔 *{translated_title}*" if translated_title else "🔔 *（無標題）*",
    ]
    if raw_title:
        lines.extend(["", "*〔英文原文〕*", _news_escape_md_light(raw_title)[:600]])
    lines.append("")
    if url:
        lines.append(f"🔗 [開啟原文]({url})")

    message = "\n".join(lines)
    if jackbot_universal_pre_send_gatekeeper("news_tree", text=message):
        send_telegram_message(message, TG_THREAD_IDS['news'])


def process_and_send_coinglass(item: Dict, type_str: str):
    """翻譯並發送 CoinGlass 新聞/快訊到 Telegram"""
    is_newsflash = type_str == "newsflash"
    emoji = "⚡" if is_newsflash else "📰"
    type_name = "快訊" if is_newsflash else "新聞"

    translated_title = _news_escape_md_light(translate_text(item.get("title") or item.get("headline") or ""))
    translated_content = _news_escape_md_light(translate_text(item.get("content") or item.get("description") or ""))

    lines = [
        f"{emoji} *【全球幣圈{type_name}】*",
        "────────────────",
        "📍 *來源* · `CoinGlass`",
        "",
    ]
    if translated_title:
        lines.append(f"🔔 *{translated_title}*")
        lines.append("")
    if translated_content:
        tc = translated_content[:520]
        if len(translated_content) > 520:
            tc += "…"
        lines.extend(["*〔摘要〕*", tc, ""])

    time_val = item.get("time") or item.get("timestamp") or item.get("publishTime")
    if time_val:
        if isinstance(time_val, (int, float)):
            if time_val > 1e12:
                date = datetime.fromtimestamp(time_val / 1000, tz=timezone.utc)
            else:
                date = datetime.fromtimestamp(time_val, tz=timezone.utc)
        else:
            date = get_taipei_time()
        date_taipei = get_taipei_time(date)
        lines.append(f"🕐 *時間（台北）* · `{date_taipei.strftime('%Y-%m-%d %H:%M')}`")
        lines.append("")

    link = (item.get("url") or item.get("link") or "").strip()
    if link:
        lines.append(f"🔗 [開啟原文]({link})")

    message = "\n".join(lines)
    if jackbot_universal_pre_send_gatekeeper("news_coinglass", text=message):
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
    
    lines = [
        "📰 *【全球幣圈即時快訊】*",
        "────────────────",
        "_精選標題 · Tree of Alpha／CoinGlass 合輯_",
        "",
    ]
    for idx, item in enumerate(all_news_items[:8], 1):
        title_safe = _news_escape_md_light(item.get("title") or "")
        src = _news_escape_md_light(item.get("source") or "—")
        lines.append(f"*#{idx}* · `{src}`")
        lines.append(title_safe)
        if item.get("url"):
            lines.append(f"🔗 [開啟連結]({item['url']})")
        lines.append("")

    lines.append("────────────────")
    lines.append(f"⏰ *彙整時間（台北）* · `{time_str}`")
    
    message = "\n".join(lines)
    if jackbot_universal_pre_send_gatekeeper("news_digest", text=message):
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
        
        if jackbot_universal_pre_send_gatekeeper("funding_rate", text=message):
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

    fg_str = str(int(fg)) if fg is not None else "—"
    lines = []
    lines.append("⏳ 【長線財富週期】")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📍 當前位置：{color} {status}")
    lines.append("")
    lines.append(f"💰 AHR999 指數：{ahr:.2f}")
    lines.append(f"🌡️ 貪婪恐懼指數：{fg_str}")
    lines.append("")
    lines.append("🧠 盤面碎碎念：")
    lines.append(f"👉 {action}")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(f"⏰ {datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d')}")
    return "\n".join(lines)


def run_long_term_monitor(interval_hours: int = 24):
    """常駐模式：預設每 24 小時推播一次（與「每天一次」排程一致；本機常駐請自行調 interval）"""
    logger.info(f"啟動長線指標監控，每 {interval_hours} 小時更新一次...")
    interval_sec = max(1, int(interval_hours * 3600))
    while True:
        try:
            message = build_long_term_message()
            if message:
                thread_id = TG_THREAD_IDS.get("long_term_index", 0)
                if jackbot_universal_pre_send_gatekeeper("long_term_index", text=message):
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
    if jackbot_universal_pre_send_gatekeeper("long_term_index", text=message):
        send_telegram_message(message, thread_id, parse_mode="Markdown", reply_markup=keyboard)


# ==================== 8. 流動性獵取雷達（極端清算監控） ====================

LIQ_SYMBOLS = [
    "BTC", "ETH", "SOL",  # 只偵測這三個主流幣種
]
LIQ_EXCHANGE_LIST = "Binance"
LIQ_REQUEST_DELAY = 1.2  # 秒


def _liq_threshold_usd_from_env(prefix: str, default_1h: float, default_24h: float) -> tuple:
    """以環境變數覆寫門檻（USD），例如 LIQ_BTC_1H、LIQ_BTC_24H。"""
    def _f(name: str, default: float) -> float:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return default
        try:
            return float(str(raw).strip().replace("_", ""))
        except ValueError:
            return default

    return (
        _f(f"{prefix}_1H", default_1h),
        _f(f"{prefix}_24H", default_24h),
    )


def get_liquidation_threshold(symbol: str, time_window: str = "1h") -> tuple:
    """根據幣種回傳極端爆倉門檻（USD）(1h, 24h)。

    **觸發條件只看 1h 合計是否 ≥ 1h 門檻**；24h 僅供日誌對照。
    BTC／ETH／SOL 使用不同預設；可用環境變數覆寫：
    ``LIQ_BTC_1H``、``LIQ_BTC_24H``、``LIQ_ETH_1H``、``LIQ_ETH_24H``、``LIQ_SOL_1H``、``LIQ_SOL_24H``（單位：美元）。
    """
    base = symbol.replace("USDT", "").replace("-", "").upper()

    # 預設（較舊版調低；三幣種分開）
    if base == "BTC":
        return _liq_threshold_usd_from_env("LIQ_BTC", 35_000.0, 8_000_000.0)
    if base == "ETH":
        return _liq_threshold_usd_from_env("LIQ_ETH", 8_000.0, 5_000_000.0)
    if base == "SOL":
        return _liq_threshold_usd_from_env("LIQ_SOL", 15_000.0, 2_500_000.0)
    if base in ("XRP", "DOGE"):
        return (25_000.0, 3_000_000.0)
    return (20_000.0, 2_000_000.0)


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
    """【主力清算·撿屍雷達】多空結構參考（非投資建議、非跟單）。"""
    now_str = datetime.now(TAIPEI_TZ).strftime("%H:%M")
    lines = []
    lines.append("🩸 *【主力清算 · 撿屍雷達】*")
    lines.append("_多空結構參考｜非投資建議、非跟單、不提供開倉／加倉指令_")
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
            title = "結構上：多側爆倉較重（價格下殺時多單被清算）"
            ref_note = "📎 解讀：短線情緒偏恐慌／籌碼出清，僅作多空情緒參考，請自行判斷。"
            entry_action = "價格參考區間（對照用）"
        else:
            icon = "🔴"
            title = "結構上：空側爆倉較重（價格上沖時空單被清算）"
            ref_note = "📎 解讀：短線情緒偏擁擠／軋空力道，僅作多空情緒參考，請自行判斷。"
            entry_action = "價格參考區間（對照用）"

        lines.append(f"{icon} *{sym}* 💥 爆倉 *${amt:.1f}萬*")
        lines.append(f"💀 {title}")
        if rsi_lbl:
            lines.append(f"📊 {rsi_lbl}")
        if pin_lbl:
            lines.append(f"  {pin_lbl}")
        if confirm:
            lines.append(f"✅ 技術面輔助：{confirm}")
        if entry_low and entry_high and cur_price:
            lines.append(
                f"🎯 *{entry_action}*：`${entry_low}` ~ `${entry_high}`（現價 `${cur_price:.4f}`）"
            )
        lines.append(ref_note)
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(f"⏰ {now_str} | 數據來自 CoinGlass／交易所公開行情；附圖為 Binance 歷史清算柱狀參考。")
    lines.append("_本訊息僅供教育與市場結構討論，不構成任何買賣建議。_")
    return "\n".join(lines)


def _fetch_liq_radar_analysis_1m(symbol: str) -> Dict:
    """為撿屍雷達抓取 1m K 線，計算 RSI 與長下影線（針形態）。
    優先使用 CoinGlass /api/futures/price/history（interval=1m）；
    失敗時備援 Gate 1m K 線。
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

    # ── 備援：Gate 1m K 線 ───────────────────────────────────────────────
    if len(closes) < 16:
        try:
            gate_contract = f"{clean}_USDT"
            r2 = requests.get(
                "https://api.gateio.ws/api/v4/futures/usdt/candlesticks",
                params={"contract": gate_contract, "interval": "1m", "limit": 50},
                timeout=8,
            )
            if r2.status_code == 200:
                candles = r2.json()
                if isinstance(candles, list) and len(candles) >= 16:
                    for c in candles:
                        if isinstance(c, dict):
                            opens.append(float(c.get("o") or c.get("open") or 0))
                            highs.append(float(c.get("h") or c.get("high") or 0))
                            lows.append(float(c.get("l") or c.get("low") or 0))
                            closes.append(float(c.get("c") or c.get("close") or 0))
                        elif isinstance(c, (list, tuple)) and len(c) >= 5:
                            opens.append(float(c[1]))
                            highs.append(float(c[2]))
                            lows.append(float(c[3]))
                            closes.append(float(c[4]))
        except Exception as e:
            logger.debug(f"[撿屍雷達-Gate] {clean} 1m K線異常: {e}")

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


def _render_liquidity_event_fallback_chart(events: List[Dict], out_path: Path) -> Optional[Path]:
    """主圖失敗時，用本輪事件渲染「1h 多/空清算量」對照圖（非價位熱力圖）。

    顯示重點：依爆倉量排序、主導方向、RSI 摘要、合計；避免被誤讀成「下一個清算價」。
    """
    try:
        from PIL import Image, ImageDraw

        if not events:
            return None

        sorted_ev = sorted(
            events,
            key=lambda e: float(e.get("totalVolUsd1h") or 0),
            reverse=True,
        )
        max_cols = 8
        picked = sorted_ev[:max_cols]
        symbols = [str(e.get("symbol") or "") for e in picked]
        long_vals = [float(e.get("buyVolUsd1h") or 0) for e in picked]
        short_vals = [float(e.get("sellVolUsd1h") or 0) for e in picked]
        max_v = max(long_vals + short_vals + [1.0])

        w, h = 1100, 720
        img = Image.new("RGB", (w, h), "#0d1117")
        draw = ImageDraw.Draw(img)
        font_title = _load_cjk_font(17)
        font_sub = _load_cjk_font(12)
        font_col = _load_cjk_font(11)
        font_tiny = _load_cjk_font(10)

        pad_x = 28
        header_y = 18
        draw.text(
            (pad_x, header_y),
            "主力清算 · Fallback（近 1h 多／空清算美元量）",
            fill="#f0f3f6",
            font=font_title,
        )
        draw.text(
            (pad_x, header_y + 26),
            "圖意：對照「本輪通過篩選」的幣種，誰在 1h 內被清算得多、主導是多還是空；",
            fill="#8b949e",
            font=font_sub,
        )
        draw.text(
            (pad_x, header_y + 44),
            "不是價位熱力圖，無法標出「下一個清算價在哪」——僅供多空結構與情緒參考。",
            fill="#8b949e",
            font=font_sub,
        )
        total_shown = sum(float(e.get("totalVolUsd1h") or 0) for e in picked)
        draw.text(
            (pad_x, header_y + 64),
            f"綠柱＝多單被清算（long liq）　紅柱＝空單被清算（short liq）　｜　圖中合計 1h ≈ ${total_shown/10000:.0f} 萬　｜　最多 {len(picked)} 幣",
            fill="#58a6ff",
            font=font_sub,
        )

        base_y = h - 56
        left = 32
        n = len(symbols)
        usable = w - left - 32
        col_w = usable / max(n, 1)
        bar_w = max(22, min(44, int((col_w - 18) / 2)))
        chart_top = header_y + 100
        plot_h = base_y - chart_top - 8
        scale = float(plot_h) / max_v

        draw.line((24, base_y, w - 24, base_y), fill="#6f7781", width=1)

        for i, sym in enumerate(symbols):
            ev = picked[i]
            x0 = left + i * col_w
            x_mid = x0 + col_w / 2
            l_h = int(long_vals[i] * scale)
            s_h = int(short_vals[i] * scale)
            bx0 = x_mid - bar_w - 5
            bx1 = x_mid + 5
            draw.rectangle((bx0, base_y - l_h, bx0 + bar_w, base_y), fill="#45bf87")
            draw.rectangle((bx1, base_y - s_h, bx1 + bar_w, base_y), fill="#d9024b")

            dom = str(ev.get("dominantSide") or "")
            dom_short = "多側主導" if "多" in dom else ("空側主導" if "空" in dom else "—")
            dom_color = "#45bf87" if "多" in dom else "#d9024b"
            rsi = ev.get("rsi_1m")
            rsi_line = f"RSI1m {rsi:.0f}" if rsi is not None else ""

            sym_disp = (f"熱·{sym[:10]}" if ev.get("is_hot") else sym)[:14]
            tb = draw.textbbox((0, 0), sym_disp, font=font_col)
            tw = tb[2] - tb[0]
            draw.text((x_mid - tw / 2, base_y + 6), sym_disp, fill="#f0f3f6", font=font_col)

            tb2 = draw.textbbox((0, 0), dom_short, font=font_tiny)
            t2w = tb2[2] - tb2[0]
            draw.text((x_mid - t2w / 2, base_y + 22), dom_short, fill=dom_color, font=font_tiny)

            if rsi_line:
                tb3 = draw.textbbox((0, 0), rsi_line, font=font_tiny)
                t3w = tb3[2] - tb3[0]
                draw.text((x_mid - t3w / 2, base_y + 36), rsi_line, fill="#8b949e", font=font_tiny)

            def _tag(v: float, bx: float, h_px: int, color: str) -> None:
                label = f"{v/1e4:.1f}萬"
                tb_ = draw.textbbox((0, 0), label, font=font_tiny)
                lw = tb_[2] - tb_[0]
                ly = base_y - h_px - 14 if h_px > 0 else base_y - 16
                draw.text((bx + (bar_w - lw) / 2, ly), label, fill=color, font=font_tiny)

            _tag(long_vals[i], bx0, l_h, "#45bf87")
            _tag(short_vals[i], bx1, s_h, "#d9024b")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(out_path), format="PNG")
        if out_path.is_file():
            return out_path
    except Exception as e:
        logger.warning(f"[撿屍雷達] fallback 圖失敗: {e}")
    return None


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
    if jackbot_universal_pre_send_gatekeeper("liquidity_radar", text=msg):
        send_telegram_message(msg, thread_id, parse_mode="Markdown", reply_markup=keyboard)

    # 附圖：Binance 公開清算快照總覽（改編 liquidations-chart；資料非 CoinGlass 即時 API）
    if os.getenv("LIQ_CHART_DISABLED", "").strip().lower() not in ("1", "true", "yes"):
        try:
            from liquidations_chart import generate_liquidation_chart_png

            chart_path = generate_liquidation_chart_png(
                base_dir=DATA_DIR / "liquidations_chart_data",
                coin="BTCUSDT",
                market="um",
                lookback_days=int(os.getenv("LIQ_CHART_LOOKBACK_DAYS", "180")),
                max_sync_days=int(os.getenv("LIQ_CHART_MAX_SYNC_DAYS", "14")),
            )
            if chart_path and chart_path.is_file():
                cap = (
                    "📊 *BTC 總清算圖*（Binance 歷史快照，近 "
                    f"{os.getenv('LIQ_CHART_LOOKBACK_DAYS', '180')} 日；"
                    "風格參考 Coinglass 總清算圖）\n"
                    "_非投資建議，僅供多空結構參考_"
                )
                send_telegram_photo(
                    str(chart_path),
                    caption=cap,
                    thread_id=thread_id,
                    parse_mode="Markdown",
                    reply_markup=None,
                )
            else:
                fallback_path = _render_liquidity_event_fallback_chart(
                    events,
                    DATA_DIR / "liquidations_chart_data" / "output" / "liquidity_radar_fallback.png",
                )
                if fallback_path and fallback_path.is_file():
                    send_telegram_photo(
                        str(fallback_path),
                        caption=(
                            "📊 *主力清算 · 附圖（Fallback）*\n"
                            "本圖為「本輪通過篩選」各幣 *近 1h 多／空清算美元量* 對照（依量排序），"
                            "*不是*價位清算熱力圖。\n"
                            "_非投資建議，僅供多空結構參考_"
                        ),
                        thread_id=thread_id,
                        parse_mode="Markdown",
                        reply_markup=None,
                    )
                else:
                    logger.info("[撿屍雷達] 清算圖未產生（主圖與 fallback 皆失敗），僅推送文字")
        except Exception as _chart_e:
            logger.warning(f"[撿屍雷達] 附圖略過: {_chart_e}")

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


# ── CoinGlass EMA list 備援（K 線主算；缺 ema20/ema100 時補）────────────────────
# 文件：https://docs.coinglass.com/reference/futures-ema-list
_cg_ema_list_cache: Dict[str, Any] = {"ts": 0.0, "by_base": {}}
_CG_EMA_LIST_TTL = 90.0


def _cg_ema_list_field_key(interval: str) -> str:
    s = (interval or "15m").strip().lower()
    alias = {
        "m1": "1m", "m3": "3m", "m5": "5m", "m15": "15m", "m30": "30m",
        "h1": "1h", "h4": "4h", "d1": "1d", "w1": "1w",
    }
    s = alias.get(s, s)
    if s not in ("1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"):
        s = "15m"
    return f"ema_{s}"


def _refresh_cg_ema_list_cache() -> None:
    global _cg_ema_list_cache
    if not CG_API_KEY:
        return
    now = time.time()
    if (
        now - float(_cg_ema_list_cache.get("ts") or 0) < _CG_EMA_LIST_TTL
        and _cg_ema_list_cache.get("by_base")
    ):
        return
    ep = CG_EP.get("ema_list", "/api/futures/ema/list")
    try:
        _respect_coinglass_rate_limit()
        r = requests.get(
            f"{CG_API_BASE}{ep}",
            headers={"CG-API-KEY": CG_API_KEY, "accept": "application/json"},
            timeout=22,
        )
        if r.status_code in (401, 403):
            logger.debug("[EMA批次] list HTTP %s", r.status_code)
            _cg_ema_list_cache = {"ts": now, "by_base": {}}
            return
        if r.status_code != 200:
            return
        j = r.json()
        if j.get("code") not in (0, "0", 200, "200", None):
            return
        raw = j.get("data") or j.get("list") or []
        by_base: Dict[str, Dict[str, float]] = {}
        for item in (raw if isinstance(raw, list) else []):
            if not isinstance(item, dict):
                continue
            sym = (item.get("symbol") or item.get("coin") or "").upper()
            sym = sym.replace("USDT", "").replace("-", "").replace("_", "").strip()
            if not sym:
                continue
            row: Dict[str, float] = {}
            for k, v in item.items():
                if not isinstance(k, str) or not k.lower().startswith("ema_"):
                    continue
                try:
                    fv = float(v)
                    if fv > 0:
                        row[k] = fv
                except (TypeError, ValueError):
                    continue
            if row:
                by_base[sym] = row
        logger.info("[EMA批次] list 載入 %d 幣種（%s）", len(by_base), ep)
        _cg_ema_list_cache = {"ts": now, "by_base": by_base}
    except Exception as e:
        logger.debug("[EMA批次] list 異常: %s", e)


def _lookup_cg_ema_list_price(base: str, interval: str) -> Optional[float]:
    _refresh_cg_ema_list_cache()
    fk = _cg_ema_list_field_key(interval)
    byb = _cg_ema_list_cache.get("by_base") or {}
    row = byb.get(base) or (byb.get(base[4:]) if base.startswith("1000") else None)
    if not row:
        return None
    if fk in row:
        return float(row[fk])
    fkl = fk.lower()
    for k, v in row.items():
        if isinstance(k, str) and k.lower() == fkl:
            return float(v)
    return None


def _fetch_coinglass_ema_indicator_last(base: str, interval: str, window: int) -> Optional[float]:
    """單幣 /api/futures/indicators/ema 取最新值（備援）。"""
    if not CG_API_KEY:
        return None
    sym = base + "USDT"
    url = f"{CG_API_BASE}/api/futures/indicators/ema"
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}
    for ex in ("Binance", "Bybit", "OKX"):
        try:
            _respect_coinglass_rate_limit()
            time.sleep(0.1)
            r = requests.get(
                url,
                headers=headers,
                params={
                    "exchange": ex,
                    "symbol": sym,
                    "interval": interval,
                    "window": int(window),
                    "limit": 4,
                },
                timeout=10,
            )
            if r.status_code != 200:
                continue
            data = r.json()
            if data.get("code") not in (0, "0", 200, "200", None):
                continue
            raw = data.get("data") or data.get("list") or []
            if not isinstance(raw, list) or not raw:
                continue
            last = raw[-1] if isinstance(raw[-1], dict) else None
            if not last:
                continue
            v = last.get("ema_value") or last.get("value") or last.get("ema")
            if v is not None:
                fv = float(v)
                if fv > 0:
                    return fv
        except Exception:
            continue
    return None


def _apply_coinglass_ema_backfill(tech: Dict[str, Any], base: str, interval: str) -> None:
    """K 線主算為主；僅補齊缺漏的 ema20_close / ema100_close（list → indicators）。"""
    if not tech or not base:
        return
    need20 = tech.get("ema20_close") is None
    need100 = tech.get("ema100_close") is None
    if not need20 and not need100:
        return
    if need20:
        ev = _lookup_cg_ema_list_price(base, interval)
        if ev is None or ev <= 0:
            ev = _fetch_coinglass_ema_indicator_last(base, interval, 20)
        if ev is not None and ev > 0:
            tech["ema20_close"] = float(ev)
            tech["_ema_cg_fallback"] = True
    if need100:
        ev100 = _fetch_coinglass_ema_indicator_last(base, interval, 100)
        if ev100 is not None and ev100 > 0:
            tech["ema100_close"] = float(ev100)
            tech["_ema_cg_fallback"] = True


def _finalize_tech_after_klines(tech: Optional[Dict[str, Any]], base: str, interval: str) -> Optional[Dict[str, Any]]:
    if tech:
        _apply_coinglass_ema_backfill(tech, base, interval)
    return tech


# ── CoinGlass TD list（全市場 15m TD，供狙擊評分加分）──────────────────────────
# 文件：https://docs.coinglass.com/reference/futures-td-list
_cg_td_list_cache: Dict[str, Any] = {"ts": 0.0, "by_base": {}}
_CG_TD_LIST_TTL = 90.0


def _cg_td_list_field_key(interval: str) -> str:
    s = (interval or "15m").strip().lower()
    alias = {"m15": "15m", "h1": "1h", "h4": "4h"}
    s = alias.get(s, s)
    if s not in ("1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"):
        s = "15m"
    return f"td_{s}"


def _refresh_cg_td_list_cache() -> None:
    global _cg_td_list_cache
    if not CG_API_KEY:
        return
    now = time.time()
    if (
        now - float(_cg_td_list_cache.get("ts") or 0) < _CG_TD_LIST_TTL
        and _cg_td_list_cache.get("by_base")
    ):
        return
    ep = CG_EP.get("td_list", "/api/futures/td/list")
    try:
        _respect_coinglass_rate_limit()
        r = requests.get(
            f"{CG_API_BASE}{ep}",
            headers={"CG-API-KEY": CG_API_KEY, "accept": "application/json"},
            timeout=22,
        )
        if r.status_code in (401, 403):
            _cg_td_list_cache = {"ts": now, "by_base": {}}
            return
        if r.status_code != 200:
            return
        j = r.json()
        if j.get("code") not in (0, "0", 200, "200", None):
            return
        raw = j.get("data") or j.get("list") or []
        by_base: Dict[str, Dict[str, float]] = {}
        for item in (raw if isinstance(raw, list) else []):
            if not isinstance(item, dict):
                continue
            sym = (item.get("symbol") or item.get("coin") or "").upper()
            sym = sym.replace("USDT", "").replace("-", "").replace("_", "").strip()
            if not sym:
                continue
            row: Dict[str, float] = {}
            for k, v in item.items():
                if not isinstance(k, str) or not k.lower().startswith("td_"):
                    continue
                try:
                    row[k] = float(v)
                except (TypeError, ValueError):
                    continue
            if row:
                by_base[sym] = row
        logger.info("[TD批次] list 載入 %d 幣種（%s）", len(by_base), ep)
        _cg_td_list_cache = {"ts": now, "by_base": by_base}
    except Exception as e:
        logger.debug("[TD批次] list 異常: %s", e)


def fetch_cg_td_map_for_interval(interval: str = "15m") -> Dict[str, Optional[float]]:
    """回傳 {base: td_value}，供 enrichment 附加到訊號字典。"""
    _refresh_cg_td_list_cache()
    fk = _cg_td_list_field_key(interval)
    out: Dict[str, Optional[float]] = {}
    for symb, row in (_cg_td_list_cache.get("by_base") or {}).items():
        if not isinstance(row, dict):
            continue
        val = row.get(fk)
        if val is None:
            for a, b in row.items():
                if isinstance(a, str) and a.lower() == fk.lower():
                    val = b
                    break
        try:
            out[symb] = float(val) if val is not None else None
        except (TypeError, ValueError):
            out[symb] = None
    return out


# ── CoinGlass liquidation coin-list（Binance 聚合；Skills: futures/liquidation/API.md）──
# Hobbyist 方案文件標為不可用；403 時降級為空 dict，不影響主流程。
_cg_liq_coin_cache: Dict[str, Any] = {"ts": 0.0, "by_base": {}}
_CG_LIQ_COIN_TTL = 95.0
_cg_liq_coin_plan_logged: bool = False


def _cg_liq_pick_float(row: Dict[str, Any], *candidates: str) -> float:
    for k in candidates:
        if k in row and row[k] is not None:
            try:
                v = float(row[k])
                if v == v and v >= 0:
                    return v
            except (TypeError, ValueError):
                continue
    return 0.0


def _cg_liq_row_from_api_item(item: Dict[str, Any]) -> Dict[str, float]:
    """正規化 Skills 文件中的欄位名（蛇形／大小寫容錯）。"""
    long_1h = _cg_liq_pick_float(
        item,
        "long_liquidation_usd_1h",
        "longLiquidationUsd1h",
        "long_liquidation_usd_1H",
    )
    short_1h = _cg_liq_pick_float(
        item,
        "short_liquidation_usd_1h",
        "shortLiquidationUsd1h",
        "short_liquidation_usd_1H",
    )
    long_4h = _cg_liq_pick_float(
        item,
        "long_liquidation_usd_4h",
        "longLiquidationUsd4h",
    )
    short_4h = _cg_liq_pick_float(
        item,
        "short_liquidation_usd_4h",
        "shortLiquidationUsd4h",
    )
    tot_1h = long_1h + short_1h
    if tot_1h <= 0:
        tot_1h = _cg_liq_pick_float(item, "liquidation_usd_1h", "liquidationUsd1h")
    return {
        "long_1h": float(long_1h),
        "short_1h": float(short_1h),
        "total_1h": float(tot_1h),
        "long_4h": float(long_4h),
        "short_4h": float(short_4h),
    }


def _refresh_cg_liq_coin_list_cache() -> None:
    global _cg_liq_coin_cache, _cg_liq_coin_plan_logged
    if not CG_API_KEY:
        return
    now = time.time()
    if (
        now - float(_cg_liq_coin_cache.get("ts") or 0) < _CG_LIQ_COIN_TTL
        and _cg_liq_coin_cache.get("by_base")
    ):
        return
    ep = CG_EP.get("liq_coin_list", "/api/futures/liquidation/coin-list")
    try:
        _respect_coinglass_rate_limit()
        r = requests.get(
            f"{CG_API_BASE}{ep}",
            headers={"CG-API-KEY": CG_API_KEY, "accept": "application/json"},
            params={"exchange": "Binance"},
            timeout=26,
        )
        if r.status_code in (401, 403):
            if not _cg_liq_coin_plan_logged:
                logger.info(
                    "[爆倉批次] coin-list HTTP %s（常見：方案不含此端點），守門員／加分略過",
                    r.status_code,
                )
                _cg_liq_coin_plan_logged = True
            _cg_liq_coin_cache = {"ts": now, "by_base": {}}
            return
        if r.status_code != 200:
            logger.debug("[爆倉批次] coin-list HTTP %s", r.status_code)
            return
        j = r.json()
        if j.get("code") not in (0, "0", 200, "200", None):
            logger.debug("[爆倉批次] coin-list code=%s", j.get("code"))
            return
        raw = j.get("data") or j.get("list") or []
        by_base: Dict[str, Dict[str, float]] = {}
        for item in (raw if isinstance(raw, list) else []):
            if not isinstance(item, dict):
                continue
            sym_raw = item.get("symbol") or item.get("coin") or item.get("base_asset") or ""
            sym = str(sym_raw).replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
            if not sym:
                continue
            row = _cg_liq_row_from_api_item(item)
            if row["total_1h"] > 0 or row["long_1h"] > 0 or row["short_1h"] > 0:
                by_base[sym] = row
        logger.info("[爆倉批次] coin-list 載入 %d 幣種（Binance）", len(by_base))
        _cg_liq_coin_cache = {"ts": now, "by_base": by_base}
    except Exception as e:
        logger.debug("[爆倉批次] coin-list 異常: %s", e)


def fetch_cg_liq_coin_map() -> Dict[str, Dict[str, float]]:
    """{base: {long_1h, short_1h, total_1h, long_4h, short_4h}}，供 enrichment／守門員／評分。"""
    _refresh_cg_liq_coin_list_cache()
    out: Dict[str, Dict[str, float]] = {}
    for k, v in (_cg_liq_coin_cache.get("by_base") or {}).items():
        if isinstance(v, dict):
            out[k] = dict(v)
    return out


def _sniper_gk_symbol_key(s: str) -> str:
    return str(s or "").replace("USDT", "").replace("-", "").replace("_", "").strip().upper()


def sniper_coinglass_trade_payload_from_enriched_item(x: Dict[str, Any]) -> Dict[str, Any]:
    """持倉狙擊 enriched item → `sniper_coinglass_gatekeeper_allow` 所需欄位（推播前即時價覆核用）。"""
    sym = x.get("symbol") or ""
    cp = x.get("current_price")
    if cp is None:
        cp = x.get("price")
    out: Dict[str, Any] = {
        "symbol": sym,
        "category": (x.get("category") or "").strip(),
        "current_price": cp,
        "funding_rate": x.get("funding_rate"),
    }
    p15 = x.get("price_change_percent_15m")
    if p15 is None:
        p15 = x.get("priceChange15m")
    if p15 is not None:
        out["price_change_percent_15m"] = p15
    oi30 = x.get("oiChange30m")
    if oi30 is None:
        oi30 = x.get("oiChange_30m")
    if oi30 is not None:
        out["oiChange30m"] = oi30
    for k in ("cg_liq_long_1h_usd", "cg_liq_short_1h_usd"):
        v = x.get(k)
        if v is not None:
            out[k] = v
    # 推播前結構停損覆核（與 compute_structural_sl_tp 末端一致）
    for k in ("push_sl", "push_entry", "recent_low_2h", "recent_high_2h", "atr"):
        v = x.get(k)
        if v is not None:
            out[k] = v
    return out


def sniper_coinglass_gatekeeper_allow(x: Dict[str, Any]) -> bool:
    """規則式守門員（CoinGlass API Skills 思路內化）：硬擋明顯矛盾／資料無效；與 enrichment FR 封鎖互補。
    持倉狙擊：all_top 篩選後一檢；推播前再以即時價帶入 `jackbot_universal_pre_send_gatekeeper(..., coinglass_trade=...)` 二檢。
    爆擊雷達：ATR 後一檢；送出前 universal 再帶同一套 payload 覆核。
    設 SNIPER_GATEKEEPER_DISABLED=1 可關閉整段守門員。
    爆倉單獨關閉：SNIPER_GK_LIQ_DISABLED=1；門檻：SNIPER_GK_LIQ_MIN_TOTAL_USD、SNIPER_GK_LIQ_DOM_RATIO。
    薄流動（15m 價格暴走但 30m OI 不動）僅在 payload 含 oiChange_30m／oiChange30m 時檢查。
    結構停損：payload 含 push_sl／recent_low_2h／recent_high_2h 時，與 SNIPER_SL_ENFORCE_SWING 對齊做最終覆核。
    """
    raw = os.getenv("SNIPER_GATEKEEPER_DISABLED", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    sym = x.get("symbol") or ""
    base = _sniper_gk_symbol_key(sym)
    cat = (x.get("category") or "").strip()
    try:
        cp = float(x.get("current_price") or 0)
    except (TypeError, ValueError):
        cp = 0.0
    if cp <= 0:
        logger.info("[守門員🚫] %s: 無有效現價", sym or base or "?")
        return False
    fr = x.get("funding_rate")
    try:
        frf = float(fr) if fr is not None else None
    except (TypeError, ValueError):
        frf = None
    if frf is not None:
        _is_short_sig = cat in ("long_close", "short_open")
        _is_long_sig = cat in ("long_open", "short_close")
        if _is_short_sig and frf < -FR_SHORT_SQUEEZE_BLOCK:
            logger.info("[守門員🚫] %s 做空類 費率≤%.2f%%（壅擠）", base, -FR_SHORT_SQUEEZE_BLOCK * 100)
            return False
        if _is_long_sig and frf > FR_LONG_LIQUIDATION_BLOCK:
            logger.info("[守門員🚫] %s 做多類 費率≥%.2f%%（壅擠）", base, FR_LONG_LIQUIDATION_BLOCK * 100)
            return False
    try:
        p15 = float(x.get("priceChange15m") or x.get("price_change_percent_15m") or 0)
    except (TypeError, ValueError):
        p15 = 0.0
    # 僅在「有帶入 30m OI」時檢查薄流動；缺欄位時不推斷為 0（避免誤殺爆擊雷達等輕量 payload）
    _oi30_raw = x.get("oiChange_30m")
    if _oi30_raw is None:
        _oi30_raw = x.get("oiChange30m")
    if _oi30_raw is not None:
        try:
            oi30 = float(_oi30_raw)
        except (TypeError, ValueError):
            oi30 = 0.0
        if abs(p15) > 22 and abs(oi30) < 0.22:
            logger.info(
                "[守門員🚫] %s 15m 價格變化過大(%.1f%%) 但 30m OI 幾乎不動(%.2f%%)（疑似薄流動）",
                base,
                p15,
                oi30,
            )
            return False

    # 1h 爆倉邊（CoinGlass coin-list；Skills 欄位 long/short_liquidation_usd_1h）
    if os.getenv("SNIPER_GK_LIQ_DISABLED", "").strip().lower() not in ("1", "true", "yes", "on"):
        _l1 = x.get("cg_liq_long_1h_usd")
        _s1 = x.get("cg_liq_short_1h_usd")
        if _l1 is not None and _s1 is not None:
            try:
                _lf = float(_l1)
                _sf = float(_s1)
            except (TypeError, ValueError):
                _lf = _sf = -1.0
            if _lf >= 0 and _sf >= 0:
                _tot = _lf + _sf
                _min_l = max(50_000.0, _env_float("SNIPER_GK_LIQ_MIN_TOTAL_USD", 120_000.0))
                _dom = max(1.6, _env_float("SNIPER_GK_LIQ_DOM_RATIO", 2.75))
                _is_bull = cat in ("long_open", "short_close")
                _is_bear = cat in ("short_open", "long_close")
                if _tot >= _min_l and _is_bull and _lf >= _dom * max(_sf, 1.0):
                    logger.info(
                        "[守門員🚫] %s 做多類：1h 多頭爆倉佔優(%.0f vs %.0f USD, 總%.0f)（左側接刀風險）",
                        base,
                        _lf,
                        _sf,
                        _tot,
                    )
                    return False
                if _tot >= _min_l and _is_bear and _sf >= _dom * max(_lf, 1.0):
                    logger.info(
                        "[守門員🚫] %s 做空類：1h 空頭爆倉佔優(%.0f vs %.0f USD)（軋空風險）",
                        base,
                        _sf,
                        _lf,
                    )
                    return False

    # 2H 結構停損複核（與 SNIPER_SL_ENFORCE_SWING／compute_structural_sl_tp 末端對齊；推播前即時價 cp 算緩衝）
    _enf_sw_gk = os.getenv("SNIPER_SL_ENFORCE_SWING", "1").strip().lower() in (
        "1", "true", "yes", "on",
    )
    if _enf_sw_gk:
        _ps = x.get("push_sl")
        _pe = x.get("push_entry")
        _lo2 = x.get("recent_low_2h")
        _hi2 = x.get("recent_high_2h")
        _atr_gk = x.get("atr")
        try:
            sl_f = float(_ps) if _ps is not None else None
        except (TypeError, ValueError):
            sl_f = None
        if sl_f is not None and sl_f > 0:
            try:
                ent = float(_pe) if _pe is not None else float(cp)
            except (TypeError, ValueError):
                ent = float(cp)
            try:
                atr_num = float(_atr_gk) if _atr_gk is not None else None
            except (TypeError, ValueError):
                atr_num = None
            sl_guard_buffer = cp * 0.0012
            if atr_num is not None and atr_num > 0:
                sl_guard_buffer = max(sl_guard_buffer, atr_num * SL_EMA_GUARD_BUFFER_ATR)
            _eps = max(1e-12, abs(float(cp)) * 1e-9)
            _is_long_sig = cat in ("long_open", "short_close")
            _is_short_sig = cat in ("short_open", "long_close")
            if _is_long_sig and _lo2 is not None:
                try:
                    lo2f = float(_lo2)
                except (TypeError, ValueError):
                    lo2f = None
                if lo2f is not None and lo2f < ent:
                    swing_floor = lo2f - sl_guard_buffer
                    if sl_f > swing_floor + _eps:
                        logger.info(
                            "[守門員🚫] %s 多向：SL(%.8g) 未落在 2H 結構低之下（容許底≈%.8g，現價%.8g）",
                            base,
                            sl_f,
                            swing_floor,
                            cp,
                        )
                        return False
            if _is_short_sig and _hi2 is not None:
                try:
                    hi2f = float(_hi2)
                except (TypeError, ValueError):
                    hi2f = None
                if hi2f is not None and hi2f > ent:
                    swing_ceil = hi2f + sl_guard_buffer
                    if sl_f < swing_ceil - _eps:
                        logger.info(
                            "[守門員🚫] %s 空向：SL(%.8g) 未落在 2H 結構高之上（容許頂≈%.8g，現價%.8g）",
                            base,
                            sl_f,
                            swing_ceil,
                            cp,
                        )
                        return False
    return True


def _sniper_llm_parse_json_allow(raw: str) -> Tuple[Optional[bool], str]:
    """解析 LLM JSON：回傳 (是否擋下, 理由)。無法解析時第一個值為 None → 外層應放行。"""
    if not (raw or "").strip():
        return None, ""
    s = raw.strip()
    try:
        p = json.loads(s)
    except json.JSONDecodeError:
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j <= i:
            return None, ""
        try:
            p = json.loads(s[i : j + 1])
        except json.JSONDecodeError:
            return None, ""
    if not isinstance(p, dict):
        return None, ""
    if p.get("allow") is False:
        return False, str(p.get("reason") or "").strip()
    return True, ""


def _sniper_llm_gatekeeper_build_payload(
    channel: str,
    text: Optional[str],
    coinglass_trade: Optional[Dict[str, Any]],
) -> str:
    body_txt = (text or "")[:1200]
    trade_compact = dict(coinglass_trade or {})
    for _k in list(trade_compact.keys()):
        if len(str(trade_compact.get(_k))) > 120:
            trade_compact[_k] = str(trade_compact.get(_k))[:117] + "..."
    return json.dumps(
        {"channel": channel, "trade": trade_compact, "message_excerpt": body_txt},
        ensure_ascii=False,
    )


def _sniper_llm_gatekeeper_system_prompt() -> str:
    return (
        "你是加密合約推播的風控審核。只輸出 JSON："
        '{"allow":true或false,"reason":"一句中文"}。'
        "若訊息與 trade 欄位明顯矛盾（例如做多但停損在結構低之上、費率與方向嚴重衝突），allow=false。"
        "不確定或僅排版問題 → allow=true。"
    )


def _sniper_llm_gatekeeper_allow(
    channel: str,
    text: Optional[str],
    coinglass_trade: Optional[Dict[str, Any]],
) -> bool:
    """選用：LLM 最後覆核。OpenAI 雲端或本機 Ollama（SNIPER_AI_LLM_BACKEND）。失敗時放行。"""
    if os.getenv("SNIPER_AI_LLM_GATEKEEPER", "").strip().lower() not in ("1", "true", "yes", "on"):
        return True
    if channel != "position_change":
        return True

    user_payload = _sniper_llm_gatekeeper_build_payload(channel, text, coinglass_trade)
    sys_prompt = _sniper_llm_gatekeeper_system_prompt()

    backend = (os.getenv("SNIPER_AI_LLM_BACKEND") or "").strip().lower()
    if not backend:
        backend = "openai" if (os.getenv("OPENAI_API_KEY") or "").strip() else "ollama"

    if backend == "ollama":
        ollama_base = (os.getenv("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
        model = (
            (os.getenv("OLLAMA_MODEL") or os.getenv("SNIPER_AI_LLM_MODEL") or "llama3.2").strip()
        )
        timeout_s = max(15.0, _env_float("OLLAMA_TIMEOUT_SEC", 120.0))
        try:
            resp = requests.post(
                f"{ollama_base}/api/chat",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_payload},
                    ],
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0},
                },
                timeout=timeout_s,
            )
            if resp.status_code != 200:
                logger.warning(
                    "[AI守門員] Ollama HTTP %s，放行推播",
                    resp.status_code,
                )
                return True
            data = resp.json() if resp.content else {}
            raw = (data.get("message") or {}).get("content") or ""
            verdict, reason = _sniper_llm_parse_json_allow(raw)
            if verdict is None:
                logger.warning("[AI守門員] Ollama 回傳無法解析 JSON，放行推播")
                return True
            if verdict is False:
                logger.info(
                    "[守門員🚫·AI·Ollama] %s",
                    reason or "LLM 否決",
                )
                return False
        except Exception as ex:
            logger.warning("[AI守門員] Ollama 呼叫失敗（放行）：%s", ex)
            return True
        return True

    if backend == "openai":
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            logger.warning(
                "[AI守門員] SNIPER_AI_LLM_BACKEND=openai 但未設定 OPENAI_API_KEY，略過 LLM"
            )
            return True
        model = (os.getenv("SNIPER_AI_LLM_MODEL") or "gpt-4o-mini").strip()
        try:
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "temperature": 0,
                    "max_tokens": 120,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_payload},
                    ],
                },
                timeout=18,
            )
            if resp.status_code != 200:
                logger.warning("[AI守門員] OpenAI HTTP %s，放行推播", resp.status_code)
                return True
            data = resp.json()
            raw = (((data.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""
            verdict, reason = _sniper_llm_parse_json_allow(raw)
            if verdict is None:
                logger.warning("[AI守門員] OpenAI 回傳無法解析 JSON，放行推播")
                return True
            if verdict is False:
                logger.info(
                    "[守門員🚫·AI] %s",
                    reason or "LLM 否決",
                )
                return False
        except Exception as ex:
            logger.warning("[AI守門員] OpenAI 呼叫失敗（放行）：%s", ex)
            return True
        return True

    logger.warning("[AI守門員] 未知的 SNIPER_AI_LLM_BACKEND=%s，略過 LLM", backend)
    return True


def _sniper_openai_gatekeeper_allow(
    channel: str,
    text: Optional[str],
    coinglass_trade: Optional[Dict[str, Any]],
) -> bool:
    """相容舊名稱，等同 _sniper_llm_gatekeeper_allow。"""
    return _sniper_llm_gatekeeper_allow(channel, text, coinglass_trade)



def jackbot_universal_pre_send_gatekeeper(
    channel: str,
    *,
    text: Optional[str] = None,
    coinglass_trade: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    全頻道推播前守門員（非持倉狙擊的 digest／快訊亦適用）。
    - JACKBOT_GATEKEEPER_ALL_DISABLED=1：略過本函數（維持舊行為）。
    - 空字串訊息不發。
    - 若傳入 coinglass_trade（與 sniper 相同欄位語意），再套用 sniper_coinglass_gatekeeper_allow。
      持倉狙擊／爆擊雷達建議一律傳入，以在「即時價更新後」仍擋掉壅擠費率／薄流動／爆倉邊等矛盾盤。
    - 選用 LLM：SNIPER_AI_LLM_GATEKEEPER=1（僅 position_change）。預設無 OPENAI_API_KEY 時走本機 Ollama；
      或設 SNIPER_AI_LLM_BACKEND=openai／ollama；Ollama 見 OLLAMA_HOST、OLLAMA_MODEL。
    """
    if os.getenv("JACKBOT_GATEKEEPER_ALL_DISABLED", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    if text is not None and not str(text).strip():
        logger.info("[守門員·%s] 空訊息，不發送", channel)
        return False
    if coinglass_trade is not None and not sniper_coinglass_gatekeeper_allow(coinglass_trade):
        logger.info("[守門員·%s] CoinGlass 交易向規則擋下", channel)
        return False
    if not _sniper_llm_gatekeeper_allow(channel, text, coinglass_trade):
        logger.info("[守門員·%s] AI 覆核擋下", channel)
        return False
    return True


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


def _altseason_normalize_base(sym: str) -> str:
    s = str(sym or "").strip().upper()
    for suf in ("USDT", "-PERP", "PERP"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return s.replace("-", "").replace("_", "").strip()


def _altseason_fmt_price_short(p: Optional[float]) -> str:
    if p is None:
        return "—"
    try:
        x = float(p)
    except (TypeError, ValueError):
        return "—"
    if x != x or x <= 0:
        return "—"
    if x >= 1000:
        return f"{x:,.2f}"
    if x >= 1:
        return f"{x:.4f}".rstrip("0").rstrip(".")
    return f"{x:.6g}"


def _altseason_cost_vwap_or_ema(tech: Optional[Dict[str, Any]]) -> Tuple[str, Optional[float]]:
    if not tech:
        return ("成本區", None)
    v = tech.get("vwap_2h")
    try:
        vf = float(v) if v is not None else None
    except (TypeError, ValueError):
        vf = None
    if vf is not None and vf > 0:
        return ("4H 量能成本（VWAP）", vf)
    em = tech.get("ema20_close")
    try:
        ef = float(em) if em is not None else None
    except (TypeError, ValueError):
        ef = None
    if ef is not None and ef > 0:
        return ("4H EMA20（均價）", ef)
    return ("成本區", None)


def _altseason_bull_story_zh(
    br: float,
    rsi: float,
    px: float,
    cost_px: Optional[float],
    fr: Optional[float],
    p24: Optional[float],
) -> str:
    """波段敘事：對齊 4H／日線視角，弱化分鐘級用語。"""
    parts: List[str] = []
    if br >= 56:
        parts.append(f"買方成交占比約 {br:.0f}%（全市場聚合近似）")
    elif br >= 52:
        parts.append("買方成交略優於賣方")
    if rsi >= 72:
        parts.append("4H RSI 偏高、波段動能強（拉回宜分批）")
    elif rsi >= 65:
        parts.append("4H RSI 守在相對強勢區")
    if cost_px is not None and cost_px > 0:
        if px > cost_px * 1.003:
            parts.append("現價高於 4H 成本帶，籌碼成本偏多頭")
        elif px < cost_px * 0.997:
            parts.append("現價在成本帶下方，突破／假突破須再確認")
        else:
            parts.append("價格與 4H 成本帶接近，方向仍在表態")
    if fr is not None and isinstance(fr, (int, float)) and fr == fr:
        if fr < -0.0005:
            parts.append("資金費率偏負，多頭持倉費用較友善")
        elif fr > 0.0025:
            parts.append("費率偏高、多頭略壅擠，波段宜控倉")
    if p24 is not None and isinstance(p24, (int, float)):
        if p24 > 8:
            parts.append(f"24h 漲幅仍偏強（約 {p24:+.1f}%），延續須守回撤")
        elif p24 < -5:
            parts.append(f"24h 仍偏弱（約 {p24:+.1f}%），反彈以短波段看待")
    if not parts:
        return "強勢權值＋資金輪動標的；波段以分批與守損為主。"
    return "；".join(parts[:4]) + "。"


def build_altseason_message() -> Optional[str]:
    """【山寨暴富列車】板塊輪動；以 4H／日線波段為敘事主軸，4H 成本帶與 ATR 為機械參考。"""
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

    _min_rsi_4h = int(max(55, min(85, round(_env_float("ALTSEASON_MIN_RSI_4H", 65)))))
    strong_src = [
        r
        for r in rsi_list
        if (r.get("rsi_4h") or r.get("rsi_base") or 0) >= _min_rsi_4h
    ][:16]
    if strong_src:
        strong_src = attach_buy_ratio(strong_src)
        strong_src.sort(
            key=lambda x: (
                float(x.get("rsi_4h") or x.get("rsi_base") or 0),
                float(x.get("buy_ratio") or 0),
            ),
            reverse=True,
        )

    markets = fetch_coinglass_coins_markets() if CG_API_KEY else []
    mkt_by_base = {str(m.get("symbol") or "").upper(): m for m in markets if m.get("symbol")}
    fr_map: Dict[str, float] = {}
    liq_map: Dict[str, Dict[str, float]] = {}
    try:
        fr_map = _fetch_funding_rate_map() or {}
    except Exception:
        fr_map = {}
    try:
        liq_map = fetch_cg_liq_coin_map()
    except Exception:
        liq_map = {}

    leaders: List[Dict[str, Any]] = []
    if strong_src:
        for cand in strong_src:
            if len(leaders) >= 5:
                break
            base = _altseason_normalize_base(cand.get("symbol") or "")
            if not base:
                continue
            pair = f"{base}USDT"
            mkt_row = mkt_by_base.get(base) or mkt_by_base.get(base[4:] if base.startswith("1000") else "")
            px = _fetch_bingx_current_price(pair, preferred_symbol=None)
            if px is None or float(px) <= 0:
                logger.info("[山寨列車] 無即時價，略過候選 %s", base)
                continue
            px_f = float(px)
            fr = fr_map.get(base)
            if fr is None and base.startswith("1000"):
                fr = fr_map.get(base[4:])
            gk_dict = _crit_radar_gatekeeper_payload(
                mkt_row if isinstance(mkt_row, dict) else {},
                base,
                px_f,
                fr,
                True,
                liq_map,
            )
            if not sniper_coinglass_gatekeeper_allow(gk_dict):
                logger.info("[守門員·山寨列車] 略過候選 %s（與持倉狙擊同源規則）", base)
                continue
            time.sleep(0.15)
            tech = calculate_technicals(pair, interval="4h", limit=72)
            cost_label, cost_px = _altseason_cost_vwap_or_ema(tech)
            atr_v = None
            if tech:
                try:
                    atr_v = float(tech.get("atr")) if tech.get("atr") is not None else None
                except (TypeError, ValueError):
                    atr_v = None
            p24 = None
            if isinstance(mkt_row, dict):
                p24 = mkt_row.get("price_change_percent_24h")
                try:
                    p24 = float(p24) if p24 is not None else None
                except (TypeError, ValueError):
                    p24 = None
            rsi_v = float(cand.get("rsi_4h") or cand.get("rsi_base") or 0)
            br_v = float(cand.get("buy_ratio") or 50.0)
            story = _altseason_bull_story_zh(br_v, rsi_v, px_f, cost_px, fr, p24)
            t1 = t2 = None
            if atr_v is not None and atr_v > 0 and px_f > 0:
                t1 = px_f + 1.5 * atr_v
                t2 = px_f + 3.0 * atr_v
            leaders.append(
                {
                    "base": base,
                    "buy_ratio": br_v,
                    "rsi": rsi_v,
                    "px": px_f,
                    "cost_label": cost_label,
                    "cost_px": cost_px,
                    "story": story,
                    "t1": t1,
                    "t2": t2,
                    "p24": p24,
                }
            )

    lines = []
    lines.append("🎢 *【山寨暴富列車】*")
    lines.append("────────────────")
    lines.append(
        "📐 *時間軸* · 以 *4H／日線波段* 為主（非分鐘級進出）；"
        "下列技術欄位以 *4H K* 計算。"
    )
    season_status = "🛡️ 比特幣吸血中（防守）"
    if index_val is not None and index_val > 70:
        season_status = "🌋 群魔亂舞（山寨季）"
    elif index_val is not None and index_val > 40:
        season_status = "⚖️ 資金輪動（選幣）"
    lines.append(f"🌍 *當前週期* · {season_status}")
    lines.append(
        f"📊 *山寨指數* · `{index_val:.0f}`／100" if index_val is not None else "📊 *山寨指數* · —"
    )
    lines.append("")
    lines.append("*〔領頭羊〕* · 篩選：4H RSI ≥ `%d` · 排序：4H RSI → 買方占比" % _min_rsi_4h)
    lines.append("_每檔已過守門員（費率／流動／爆倉邊）；成本與目標為 4H 機械參考_")
    if not leaders:
        if strong_src:
            lines.append("本輪候選未通過守門員或缺少即時價，暫不列名單。")
        else:
            lines.append("暫無達 4H RSI 門檻之標的，市場偏冷。")
    else:
        for i, L in enumerate(leaders, 1):
            b = L["base"]
            br = L["buy_ratio"]
            rsi = L["rsi"]
            px = L["px"]
            clab = L["cost_label"]
            cpx = L["cost_px"]
            lines.append("")
            lines.append(f"*#{i}* `{b}` · 買方占比約 `{br:.0f}%`")
            lines.append(
                "• 現價 · `{0}`".format(_altseason_fmt_price_short(px))
                + (
                    " · 4H *{0}* · `{1}`".format(clab, _altseason_fmt_price_short(cpx))
                    if cpx is not None
                    else " · 4H 成本資料不足"
                )
            )
            if L.get("p24") is not None and isinstance(L["p24"], (int, float)):
                try:
                    lines.append(f"• 24h 漲跌 · `{float(L['p24']):+.1f}%`（日內背景）")
                except (TypeError, ValueError):
                    pass
            lines.append(f"• 列入理由 · {L['story']}")
            if L.get("t1") is not None and L.get("t2") is not None:
                lines.append(
                    "• 波段參考價（4H ATR×係數，非保證）· `{0}` → `{1}`".format(
                        _altseason_fmt_price_short(L["t1"]),
                        _altseason_fmt_price_short(L["t2"]),
                    )
                )
            lines.append(f"• 4H RSI · `{rsi:.0f}` · 策略偏回檔分批，避免追高")
    lines.append("")
    lines.append("────────────────")
    lines.append(
        "💡 *心法* · 波段跟強勢龍頭；落後補漲波動大、勝率常較差。"
        "目標為機械推算，實際請依個人風控。"
    )
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
    if jackbot_universal_pre_send_gatekeeper("altseason_radar", text=msg):
        send_telegram_message(msg, thread_id or int(CHAT_ID or 0), parse_mode="Markdown", reply_markup=keyboard)
    logger.info("山寨爆發雷達推播完成")


# ==================== 9b. 爆擊雷達（Crit Radar）OI 共振 ====================

CRIT_RADAR_COOLDOWN_FILE = DATA_DIR / "crit_radar_cooldown.json"
# 與持倉狙擊共用 GIST_ID + GITHUB_TOKEN 時，多一個 gist 檔名存爆擊冷卻（跨 GitHub Actions 不丟失）
CRIT_RADAR_GIST_FILENAME = os.getenv("CRIT_RADAR_GIST_FILENAME", "crit_radar_cooldown.json").strip() or "crit_radar_cooldown.json"


def _crit_radar_env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def _crit_radar_cooldown_symbol(sym: str) -> str:
    """冷卻 dict 的 key：統一為基底幣大寫（與 coins-markets 一致），避免 CHIP / CHIPUSDT 各寫一支導致重複推播。"""
    s = str(sym or "").strip().upper().replace("-", "").replace("_", "")
    if s.endswith("USDT"):
        s = s[:-4]
    if s.endswith("PERP"):
        s = s[:-4]
    return s


def _crit_radar_normalize_cooldown_map(mp: Optional[Dict[str, float]]) -> Dict[str, float]:
    """合併別名 key，同一幣只保留最近一次的 epoch。"""
    out: Dict[str, float] = {}
    if not mp:
        return out
    for k, v in mp.items():
        try:
            ts = float(v)
        except (TypeError, ValueError):
            continue
        nk = _crit_radar_cooldown_symbol(str(k))
        if not nk:
            continue
        out[nk] = max(out.get(nk, 0.0), ts)
    return out


def _crit_radar_price_from_item(item: Dict[str, Any]) -> Optional[float]:
    raw = item.get("_raw_cg") or {}
    if isinstance(raw, dict):
        for k in ("indexPrice", "lastPrice", "price", "close", "markPrice", "last"):
            v = raw.get(k)
            if v is None:
                continue
            try:
                p = float(v)
                if p > 0:
                    return p
            except (TypeError, ValueError):
                continue

    # coins-price-change 補入資料常不含 _raw_cg，可直接讀扁平欄位
    for k in ("price", "markPrice", "mark_price", "lastPrice", "close", "current_price"):
        v = item.get(k)
        if v is None:
            continue
        try:
            p = float(v)
            if p > 0:
                return p
        except (TypeError, ValueError):
            continue

    # 最後備援：Gate ticker 即時價（僅在達標候選缺價時才會觸發）
    sym = str(item.get("symbol") or "").strip().upper()
    if sym:
        try:
            p2 = _fetch_bingx_current_price(sym)
            if p2 is not None and float(p2) > 0:
                return float(p2)
        except Exception:
            pass
    return None


def _crit_radar_fmt_price_level(p: Optional[float]) -> str:
    """爆擊雷達訊息用：絕對價字串（方便複製掛單／警報）。"""
    if p is None:
        return "—"
    try:
        x = float(p)
    except (TypeError, ValueError):
        return "—"
    if x != x or x <= 0:
        return "—"
    ax = abs(x)
    if ax >= 1000:
        return f"{x:,.2f}"
    if ax >= 1:
        s = f"{x:.5f}".rstrip("0").rstrip(".")
        return s if s else str(x)
    if ax >= 0.0001:
        s = f"{x:.6f}".rstrip("0").rstrip(".")
        return s if s else str(x)
    return f"{x:.6g}"


def _crit_radar_oi_sort_key(item: Dict[str, Any]) -> float:
    oi15 = item.get("_cg_oi_change_15m")
    oi1h = item.get("oiChange1h") or item.get("open_interest_change_percent_1h")
    try:
        a = abs(float(oi15)) if oi15 is not None else 0.0
    except (TypeError, ValueError):
        a = 0.0
    try:
        b = abs(float(oi1h)) if oi1h is not None else 0.0
    except (TypeError, ValueError):
        b = 0.0
    return max(a, b * 0.65)


def _crit_radar_score_components(
    item: Dict[str, Any],
    funding_rate: Optional[float],
    is_long: bool,
) -> int:
    """0~100：OI 動能、價格 15m、主動買賣、資金費率（單邊）。"""
    oi15 = item.get("_cg_oi_change_15m")
    oi1h = item.get("oiChange1h") or item.get("open_interest_change_percent_1h")
    try:
        oi15f = float(oi15) if oi15 is not None else None
    except (TypeError, ValueError):
        oi15f = None
    try:
        oi1hf = float(oi1h) if oi1h is not None else None
    except (TypeError, ValueError):
        oi1hf = None
    p15 = item.get("price_change_percent_15m")
    if p15 is None:
        p15 = item.get("price_change_percent_30m")
    try:
        p15f = float(p15) if p15 is not None else 0.0
    except (TypeError, ValueError):
        p15f = 0.0
    taker = item.get("_taker_ratio_15m")
    try:
        tk = float(taker) if taker is not None else None
    except (TypeError, ValueError):
        tk = None

    oi_mag = 0.0
    if oi15f is not None:
        oi_mag = abs(oi15f)
    elif oi1hf is not None:
        oi_mag = abs(oi1hf) * 0.85
    else:
        oi_mag = 0.0
    oi_pts = min(38, oi_mag * 9.0)

    if is_long:
        price_pts = min(22, max(0.0, p15f) * 4.2)
    else:
        price_pts = min(22, max(0.0, -p15f) * 4.2)

    if tk is None:
        taker_pts = 8
    elif is_long:
        if tk >= 58:
            taker_pts = 22
        elif tk >= 52:
            taker_pts = 14 + (tk - 52) * 1.3
        else:
            taker_pts = max(0, tk / 52.0 * 8)
    else:
        if tk <= 42:
            taker_pts = 22
        elif tk <= 48:
            taker_pts = 14 + (48 - tk) * 1.3
        else:
            taker_pts = max(0, (100 - tk) / 52.0 * 8)

    if funding_rate is None or (funding_rate != funding_rate):
        fund_pts = 9
    else:
        fr = float(funding_rate)
        if is_long:
            if fr < -0.00003:
                fund_pts = 18
            elif fr < 0:
                fund_pts = 14
            elif fr < 0.00008:
                fund_pts = 8
            else:
                fund_pts = 4
        else:
            if fr > 0.00003:
                fund_pts = 18
            elif fr > 0:
                fund_pts = 14
            elif fr > -0.00008:
                fund_pts = 8
            else:
                fund_pts = 4

    # 早期啟動加權：OI 已擴張、價格尚未大幅走遠時加分（更接近起漲/起跌點）
    timing_bonus = 0.0
    if oi_mag >= 2.2:
        if is_long and 0.15 <= p15f <= 1.6:
            timing_bonus += 7.0
        elif (not is_long) and -1.6 <= p15f <= -0.15:
            timing_bonus += 7.0

    # 過熱追價扣分：15m 已過大幅度時降低分數，避免「噴出後才報」
    if abs(p15f) >= 4.8:
        timing_bonus -= min(10.0, (abs(p15f) - 4.8) * 3.0 + 4.0)

    total = int(round(oi_pts + price_pts + taker_pts + fund_pts + timing_bonus))
    return max(0, min(100, total))


def _crit_radar_load_cooldown_from_gist() -> Dict[str, float]:
    """與狙擊相同 Gist：讀取 CRIT_RADAR_GIST_FILENAME（預設 crit_radar_cooldown.json）。"""
    gist_id = os.getenv("GIST_ID")
    token = os.getenv("GITHUB_TOKEN")
    if not gist_id or not token:
        return {}
    fn = CRIT_RADAR_GIST_FILENAME
    try:
        resp = requests.get(
            f"https://api.github.com/gists/{gist_id}",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"},
            timeout=8,
        )
        if resp.status_code != 200:
            return {}
        files = resp.json().get("files") or {}
        fo = files.get(fn)
        if not fo:
            return {}
        raw = fo.get("content") or "{}"
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        out: Dict[str, float] = {}
        for k, v in data.items():
            try:
                out[str(k).upper()] = float(v)
            except (TypeError, ValueError):
                continue
        if out:
            logger.info("[爆擊雷達·Gist] 已合併冷卻 %d 幣（%s）", len(out), fn)
        return out
    except Exception as e:
        logger.debug("[爆擊雷達·Gist] 讀取冷卻例外: %s", e)
    return {}


def _crit_radar_save_cooldown_to_gist(mp: Dict[str, float]) -> bool:
    gist_id = os.getenv("GIST_ID")
    token = os.getenv("GITHUB_TOKEN")
    if not gist_id or not token:
        return False
    fn = CRIT_RADAR_GIST_FILENAME
    try:
        payload = {
            "files": {
                fn: {
                    "content": json.dumps(mp, ensure_ascii=False, indent=2),
                }
            }
        }
        resp = requests.patch(
            f"https://api.github.com/gists/{gist_id}",
            headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"},
            json=payload,
            timeout=12,
        )
        if resp.status_code == 200:
            logger.info("[爆擊雷達·Gist] 冷卻已寫回 %s（%d 幣）", fn, len(mp))
            return True
        logger.warning("[爆擊雷達·Gist] 寫回失敗 HTTP %s", resp.status_code)
    except Exception as e:
        logger.warning("[爆擊雷達·Gist] 寫回例外: %s", e)
    return False


def _crit_radar_load_cooldown() -> Dict[str, float]:
    path = CRIT_RADAR_COOLDOWN_FILE
    local: Dict[str, float] = {}
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k, v in data.items():
                    try:
                        local[str(k).upper()] = float(v)
                    except (TypeError, ValueError):
                        continue
        except Exception as e:
            logger.warning(f"[爆擊雷達] 讀取本地冷卻失敗: {e}")
    gist_cd = _crit_radar_load_cooldown_from_gist()
    if not gist_cd:
        return _crit_radar_normalize_cooldown_map(local)
    merged = dict(local)
    for k, v in gist_cd.items():
        merged[k] = max(merged.get(k, 0.0), v)
    return _crit_radar_normalize_cooldown_map(merged)


def _crit_radar_save_cooldown(mp: Dict[str, float]) -> None:
    mp = _crit_radar_normalize_cooldown_map(mp)
    path = CRIT_RADAR_COOLDOWN_FILE
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(mp, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except Exception as e:
        logger.warning(f"[爆擊雷達] 寫入冷卻失敗: {e}")
    if not _crit_radar_save_cooldown_to_gist(mp):
        if os.getenv("GITHUB_ACTIONS", "").strip() == "true" and (
            not os.getenv("GIST_ID", "").strip() or not os.getenv("GITHUB_TOKEN", "").strip()
        ):
            logger.warning(
                "[爆擊雷達·冷卻] GitHub Actions 未設定 GIST_ID+GITHUB_TOKEN 時，"
                "每輪 runner 不保留 data/，下輪可能重複推播同幣；請與持倉狙擊共用 Gist 憑證。"
            )


def _crit_radar_extract_oi30_pct(it: Dict[str, Any]) -> Optional[float]:
    """從 coins-markets 原始列讀 30m OI 變化%%（供守門員薄流動檢查；無則略過）。"""
    raw = it.get("_raw_cg") or {}
    if not isinstance(raw, dict):
        return None
    for k in (
        "openInterestChangePercent30m",
        "open_interest_change_percent_30m",
        "oiChangePercent30m",
        "oi_change_percent_30m",
    ):
        v = raw.get(k)
        if v is None:
            continue
        try:
            f = float(v)
            if f == f:
                return f
        except (TypeError, ValueError):
            continue
    return None


def _crit_radar_gatekeeper_payload(
    it: Dict[str, Any],
    sym: str,
    price: float,
    fr: Optional[float],
    is_long: bool,
    liq_map: Dict[str, Dict[str, float]],
) -> Dict[str, Any]:
    """組出與持倉狙擊共用的守門員 dict（category 對應 long_open / short_open）。"""
    base = sym.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
    row = liq_map.get(base)
    if row is None and base.startswith("1000"):
        row = liq_map.get(base[4:])
    oi30 = _crit_radar_extract_oi30_pct(it)
    p15 = it.get("price_change_percent_15m")
    out: Dict[str, Any] = {
        "symbol": sym,
        "category": "long_open" if is_long else "short_open",
        "current_price": price,
        "funding_rate": fr,
        "priceChange15m": p15,
    }
    if oi30 is not None:
        out["oiChange_30m"] = oi30
    if row:
        out["cg_liq_long_1h_usd"] = row.get("long_1h")
        out["cg_liq_short_1h_usd"] = row.get("short_1h")
        out["cg_liq_total_1h_usd"] = row.get("total_1h")
    return out


def run_crit_radar_once() -> None:
    """爆擊雷達：OI 變化排序候選池 → 多空共振分 → 持倉狙擊同款守門員 → 少則精推（ATR SL/TP）。

    預設（大社群適用）：SL 以 max(15m,1h) ATR×係數 再夾％上下限；TP_R 約 2.8R；SL 上限放寬，避免瘋狗幣
    被 7.5% 天花板壓得比「真實波動」還窄。可用 CRIT_RADAR_ATR_BLEND=15m 還原僅 15m ATR。
    """
    logger.info("開始執行爆擊雷達…")
    if not CG_API_KEY:
        logger.warning("[爆擊雷達] 未設定 CG_API_KEY，結束")
        return
    pool_n = max(20, min(200, _crit_radar_env_int("CRIT_RADAR_POOL", 100)))
    # 早期啟動預設：分數門檻下修，配合下方過熱濾網，提早但不追高/追低
    min_score = max(55, min(100, _crit_radar_env_int("CRIT_RADAR_MIN_SCORE", 82)))
    # 大社群：單輪預設少發一檔，降低「同一分鐘連三發」洗版感；要回 3 設 CRIT_RADAR_MAX_ALERTS=3
    max_alerts = max(1, min(8, _crit_radar_env_int("CRIT_RADAR_MAX_ALERTS", 2)))
    cooldown_h = max(1.0, min(72.0, _env_float("CRIT_RADAR_COOLDOWN_HOURS", 4.0)))
    margin = max(0, _crit_radar_env_int("CRIT_RADAR_SIDE_MARGIN", 4))
    # 防翻向：要求 15m 至少有基本動能；可用環境變數覆寫（單位：%）
    min_p15_abs = max(0.0, min(8.0, _env_float("CRIT_RADAR_MIN_P15_ABS", 0.35)))
    # 防追價：15m 漲跌幅過大視為已噴出/已跌破，略過
    max_p15_abs = max(min_p15_abs + 0.5, min(15.0, _env_float("CRIT_RADAR_MAX_P15_ABS", 5.8)))
    # 防翻向：若 1h 與選邊方向明顯對沖，略過（0=關閉）
    require_1h_confirm = (os.getenv("CRIT_RADAR_REQUIRE_1H_CONFIRM", "1").strip().lower() not in ("0", "false", "off", "no"))
    p1h_oppose_abs = max(0.0, min(8.0, _env_float("CRIT_RADAR_1H_MAX_OPPOSE_PCT", 0.9)))
    # 大社群預設：略增 ATR 倍數、放寬 SL%% 上限、提高 TP_R（貼近常見 2.5～3R 風報敘述）
    sl_atr = max(0.6, min(3.0, _env_float("CRIT_RADAR_SL_ATR", 1.5)))
    tp_r = max(1.0, min(4.5, _env_float("CRIT_RADAR_TP_R", 2.8)))
    sl_min_pct = max(0.01, min(0.2, _env_float("CRIT_RADAR_SL_MIN_PCT", 0.032)))
    sl_max_pct = max(sl_min_pct + 0.005, min(0.25, _env_float("CRIT_RADAR_SL_MAX_PCT", 0.11)))
    log_pool_preview = max(5, min(30, _crit_radar_env_int("CRIT_RADAR_LOG_POOL_PREVIEW", 12)))
    _atr_blend_raw = (os.getenv("CRIT_RADAR_ATR_BLEND") or "max15m_1h").strip().lower()
    _use_atr_max_1h = _atr_blend_raw not in ("15m", "legacy", "0", "false", "off", "no")

    logger.info(
        "[爆擊雷達·參數] pool=%d min_score=%d max_alerts=%d cooldown=%.1fh side_margin=%d "
        "min_p15_abs=%.2f%% max_p15_abs=%.2f%% 1h_confirm=%s oppose_cap=%.2f%% "
        "SL_ATR=%.2f TP_R=%.2f SL%%[%.3f~%.3f] atr_blend=%s log_pool_preview=%d",
        pool_n,
        min_score,
        max_alerts,
        cooldown_h,
        margin,
        min_p15_abs,
        max_p15_abs,
        "on" if require_1h_confirm else "off",
        p1h_oppose_abs,
        sl_atr,
        tp_r,
        sl_min_pct,
        sl_max_pct,
        "max15m_1h" if _use_atr_max_1h else "15m",
        log_pool_preview,
    )
    logger.info(
        "[爆擊雷達·提示] 若長期「發送=0」：可下調 Secrets／環境變數 "
        "`CRIT_RADAR_MIN_SCORE`（目前 %d）或 `CRIT_RADAR_SIDE_MARGIN`（目前 %d）以提高達標率",
        min_score,
        margin,
    )

    items = fetch_coinglass_coins_markets()
    if not items:
        logger.warning("[爆擊雷達] 未取得市場列表")
        return

    ranked = sorted(items, key=_crit_radar_oi_sort_key, reverse=True)[:pool_n]
    try:
        fr_map = _fetch_funding_rate_map() or {}
    except Exception as e:
        logger.warning(f"[爆擊雷達] 資金費率表失敗（降級）: {e}")
        fr_map = {}
    liq_map_cr: Dict[str, Dict[str, float]] = fetch_cg_liq_coin_map()
    logger.info("[爆擊雷達] 爆倉 coin-list 預載 %d 幣（與持倉狙擊守門員同源）", len(liq_map_cr))

    pool_lines: List[str] = []
    for it in ranked[:log_pool_preview]:
        sym = str(it.get("symbol") or "").strip().upper()
        if not sym:
            continue
        oik = _crit_radar_oi_sort_key(it)
        oi15 = it.get("_cg_oi_change_15m")
        p15 = it.get("price_change_percent_15m")
        if p15 is None:
            p15 = it.get("price_change_percent_30m")
        try:
            oi15s = f"{float(oi15):+.2f}" if oi15 is not None else "—"
        except (TypeError, ValueError):
            oi15s = "—"
        try:
            p15s = f"{float(p15):+.2f}" if p15 is not None else "—"
        except (TypeError, ValueError):
            p15s = "—"
        pool_lines.append(f"{sym}|oiK={oik:.3f}|oi15%={oi15s}|p15%={p15s}")
    if pool_lines:
        logger.info(
            "[爆擊雷達·OI池] 取前 %d 名（節錄前 %d）：%s",
            pool_n,
            len(pool_lines),
            " / ".join(pool_lines),
        )

    candidates: List[Dict[str, Any]] = []
    n_ambiguous = 0
    n_low_score = 0
    n_low_momentum = 0
    n_overextended = 0
    n_1h_conflict = 0
    sample_ambiguous: List[str] = []
    sample_low: List[str] = []
    sample_low_momentum: List[str] = []
    sample_overextended: List[str] = []
    sample_1h_conflict: List[str] = []

    for it in ranked:
        sym = str(it.get("symbol") or "").strip().upper()
        if not sym:
            continue
        fr = fr_map.get(sym)
        if fr is None:
            fr = fr_map.get(sym.replace("1000", ""))
        lo = _crit_radar_score_components(it, fr, True)
        sh = _crit_radar_score_components(it, fr, False)
        if lo >= sh + margin:
            side, best = "LONG", lo
        elif sh >= lo + margin:
            side, best = "SHORT", sh
        else:
            n_ambiguous += 1
            if len(sample_ambiguous) < 8:
                sample_ambiguous.append(
                    f"{sym}(L{lo}/S{sh},需勝方領先≥{margin}分)"
                )
            continue
        if best < min_score:
            n_low_score += 1
            if len(sample_low) < 10:
                sample_low.append(f"{sym}{side[0]}{best}<{min_score}")
            continue
        p15 = it.get("price_change_percent_15m")
        if p15 is None:
            p15 = it.get("price_change_percent_30m")
        try:
            p15f = float(p15) if p15 is not None else 0.0
        except (TypeError, ValueError):
            p15f = 0.0
        if abs(p15f) < min_p15_abs:
            n_low_momentum += 1
            if len(sample_low_momentum) < 8:
                sample_low_momentum.append(f"{sym}{side[0]}|p15={p15f:+.2f}%<{min_p15_abs:.2f}%")
            continue
        if abs(p15f) > max_p15_abs:
            n_overextended += 1
            if len(sample_overextended) < 8:
                sample_overextended.append(f"{sym}{side[0]}|p15={p15f:+.2f}%>{max_p15_abs:.2f}%")
            continue
        if require_1h_confirm:
            p1h = it.get("price_change_percent_1h")
            try:
                p1hf = float(p1h) if p1h is not None else 0.0
            except (TypeError, ValueError):
                p1hf = 0.0
            if (side == "LONG" and p1hf <= -p1h_oppose_abs) or (side == "SHORT" and p1hf >= p1h_oppose_abs):
                n_1h_conflict += 1
                if len(sample_1h_conflict) < 8:
                    sample_1h_conflict.append(
                        f"{sym}{side[0]}|p15={p15f:+.2f}%|p1h={p1hf:+.2f}% 反向過大"
                    )
                continue
        it2 = dict(it)
        it2["_crit_side"] = side
        it2["_crit_score"] = best
        candidates.append(it2)

    candidates.sort(key=lambda x: int(x.get("_crit_score") or 0), reverse=True)

    logger.info(
        "[爆擊雷達·分數] 池內 %d 幣：達標候選=%d｜多空差不足=%d｜單邊但分數不足=%d｜15m動能不足=%d｜15m過熱=%d｜1h反向衝突=%d",
        len(ranked),
        len(candidates),
        n_ambiguous,
        n_low_score,
        n_low_momentum,
        n_overextended,
        n_1h_conflict,
    )
    if sample_ambiguous:
        logger.info("[爆擊雷達·分數] 多空模糊範例：%s", "；".join(sample_ambiguous))
    if sample_low:
        logger.info("[爆擊雷達·分數] 分數不足範例：%s", "；".join(sample_low))
    if sample_low_momentum:
        logger.info("[爆擊雷達·分數] 15m動能不足範例：%s", "；".join(sample_low_momentum))
    if sample_overextended:
        logger.info("[爆擊雷達·分數] 15m過熱範例：%s", "；".join(sample_overextended))
    if sample_1h_conflict:
        logger.info("[爆擊雷達·分數] 1h反向衝突範例：%s", "；".join(sample_1h_conflict))
    if candidates:
        cap_list = candidates[:25]
        cand_str = " | ".join(
            f"{c.get('symbol')}{str(c.get('_crit_side') or '')[:1]}{int(c.get('_crit_score') or 0)}"
            for c in cap_list
        )
        more = f" …（共{len(candidates)}筆）" if len(candidates) > len(cap_list) else ""
        logger.info("[爆擊雷達·候選] 達標清單（依分數）：%s%s", cand_str, more)

    cd = _crit_radar_load_cooldown()
    now = time.time()
    cool_sec = cooldown_h * 3600.0
    filtered: List[Dict[str, Any]] = []
    n_cool_skip = 0
    cool_log_cap = 15
    cand_evaluated = 0
    queue_cap = max_alerts * 3
    for it in candidates:
        cand_evaluated += 1
        sym = str(it.get("symbol") or "").strip().upper()
        cd_key = _crit_radar_cooldown_symbol(sym)
        last_ts = cd.get(cd_key, 0.0)
        if last_ts and (now - last_ts) < cool_sec:
            n_cool_skip += 1
            if n_cool_skip <= cool_log_cap:
                remain_sec = cool_sec - (now - last_ts)
                logger.info(
                    "[爆擊雷達·冷卻] 略過 %s %s 分=%s｜約 %.2f 小時後可再發（上次 epoch=%.0f）",
                    sym,
                    it.get("_crit_side"),
                    it.get("_crit_score"),
                    remain_sec / 3600.0,
                    last_ts,
                )
            continue
        filtered.append(it)
        if len(filtered) >= queue_cap:
            rest = len(candidates) - cand_evaluated
            if rest > 0:
                logger.info(
                    "[爆擊雷達·隊列] 已集滿 ATR 驗證隊列上限（%d 名），其餘 %d 筆達標候選本輪不排入",
                    queue_cap,
                    rest,
                )
            break

    if n_cool_skip > cool_log_cap:
        logger.info("[爆擊雷達·冷卻] 另略過 %d 筆（僅顯示前 %d 筆詳細）", n_cool_skip - cool_log_cap, cool_log_cap)
    if filtered:
        fq = " | ".join(
            f"{x.get('symbol')}{str(x.get('_crit_side') or '')[:1]}{int(x.get('_crit_score') or 0)}"
            for x in filtered
        )
        logger.info(
            "[爆擊雷達·隊列] 冷卻後待驗 ATR/推播（最多取 %d 名）：%s",
            max_alerts * 3,
            fq,
        )

    thread_id = int(TG_THREAD_IDS.get("crit_radar") or 0)
    if not thread_id:
        logger.warning("[爆擊雷達] 未設定 crit_radar 話題 thread_id")
        return

    sent = 0
    n_no_price = 0
    n_no_atr = 0
    n_tg_fail = 0
    n_gk_skip = 0
    n_cool_reconfirm_skip = 0
    _crit_use_gk = os.getenv("CRIT_RADAR_GATEKEEPER", "1").strip().lower() not in (
        "0", "false", "off", "no",
    )
    _crit_gk_glob_off = os.getenv("SNIPER_GATEKEEPER_DISABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    if _crit_gk_glob_off:
        logger.info(
            "[爆擊雷達·守門員] 全域關閉 SNIPER_GATEKEEPER_DISABLED=1，本輪不套用 CoinGlass 守門員"
        )
    elif not _crit_use_gk:
        logger.info(
            "[爆擊雷達·守門員] 已關閉 CRIT_RADAR_GATEKEEPER=0，本輪不套用守門員"
        )
    elif not filtered:
        logger.info(
            "[爆擊雷達·守門員] 本輪無待驗隊列（達標候選為 0 或皆在冷卻），不執行守門員"
        )
    else:
        logger.info(
            "[爆擊雷達·守門員] 已啟用（與持倉狙擊同源）；僅在現價與 ATR 皆備妥後才檢查，"
            "無現價／無 ATR 略過者不計入「守門員略過」"
        )
    for it in filtered:
        if sent >= max_alerts:
            logger.info(
                "[爆擊雷達·上限] 已達本輪推播上限 max_alerts=%d，其餘留待下輪",
                max_alerts,
            )
            break
        sym = str(it.get("symbol") or "").strip().upper()
        cd_key = _crit_radar_cooldown_symbol(sym)
        side = str(it.get("_crit_side") or "")
        score = int(it.get("_crit_score") or 0)
        fr = fr_map.get(sym) or fr_map.get(sym.replace("1000", ""))
        is_long = side == "LONG"

        price = _crit_radar_price_from_item(it)
        if not price:
            n_no_price += 1
            logger.info(
                "[爆擊雷達·現價] %s %s 分=%s 略過：無可用現價（已嘗試 _raw_cg／平面欄位／備援）",
                sym,
                side,
                score,
            )
            continue
        atr_val = fetch_coinglass_indicator(sym, "atr", "15m")
        if not atr_val or float(atr_val) <= 0:
            n_no_atr += 1
            logger.info(
                "[爆擊雷達·ATR] %s %s 分=%s 略過：CoinGlass 15m ATR 無資料或<=0（atr=%r）",
                sym,
                side,
                score,
                atr_val,
            )
            continue
        atr_f = float(atr_val)
        atr_src_txt = "15m"
        if _use_atr_max_1h:
            time.sleep(0.08)
            atr_1h = fetch_coinglass_indicator(sym, "atr", "1h")
            if atr_1h and float(atr_1h) > 0:
                atr_1h_f = float(atr_1h)
                if atr_1h_f > atr_f:
                    atr_f = atr_1h_f
                atr_src_txt = "15m／1h 取大"
        raw_sl_pct = (atr_f * sl_atr) / price
        raw_sl_pct = max(sl_min_pct, min(sl_max_pct, raw_sl_pct))
        tp_pct = raw_sl_pct * tp_r
        px_f = float(price)
        if is_long:
            sl_px = px_f * (1.0 - raw_sl_pct)
            tp_px = px_f * (1.0 + tp_pct)
        else:
            sl_px = px_f * (1.0 + raw_sl_pct)
            tp_px = px_f * (1.0 - tp_pct)
        ent_s = _crit_radar_fmt_price_level(px_f)
        sl_s = _crit_radar_fmt_price_level(sl_px)
        tp_s = _crit_radar_fmt_price_level(tp_px)
        atr_s = _crit_radar_fmt_price_level(atr_f)

        if _crit_use_gk and not _crit_gk_glob_off:
            _gk_dict = _crit_radar_gatekeeper_payload(
                it, sym, float(price), fr, is_long, liq_map_cr
            )
            if not sniper_coinglass_gatekeeper_allow(_gk_dict):
                n_gk_skip += 1
                logger.info(
                    "[爆擊雷達·守門員] 略過 %s %s 分=%s（與持倉狙擊第二道規則同源）",
                    sym,
                    side,
                    score,
                )
                continue
            logger.info(
                "[爆擊雷達·守門員] 通過 %s %s 分=%s",
                sym,
                side,
                score,
            )

        p15 = it.get("price_change_percent_15m")
        if p15 is None:
            p15 = it.get("price_change_percent_30m")
        try:
            p15s = f"{float(p15):+.2f}%" if p15 is not None else "—"
        except (TypeError, ValueError):
            p15s = "—"
        oi15 = it.get("_cg_oi_change_15m")
        try:
            oi15s = f"{float(oi15):+.2f}%" if oi15 is not None else "—"
        except (TypeError, ValueError):
            oi15s = "—"
        tk = it.get("_taker_ratio_15m")
        try:
            tks = f"{float(tk):.0f}%" if tk is not None else "—"
        except (TypeError, ValueError):
            tks = "—"
        try:
            frs = f"{float(fr) * 100:.4f}%" if fr is not None else "—"
        except (TypeError, ValueError):
            frs = "—"

        dir_zh = "做多 🟢" if is_long else "做空 🔴"
        if fr is None:
            fr_note = "費率未取到（中性處理）"
        elif fr > 0:
            fr_note = "費率偏多（多付空）"
        elif fr < 0:
            fr_note = "費率偏空（空付多）"
        else:
            fr_note = "費率中性"

        _sl_pct_txt = f"{'-' if is_long else '+'}{raw_sl_pct * 100:.2f}%"
        _tp_pct_txt = f"{'+' if is_long else '-'}{tp_pct * 100:.2f}%"
        msg_lines = [
            "💥 *爆擊雷達*",
            "────────────────",
            f"📍 *標的* · `{sym}USDT` · K線 `15m`",
            f"📍 *方向* · {dir_zh}",
            f"📍 *共振分* · `{score}`／100",
            "",
            "*〔籌碼／價格〕*",
            f"• OI 15m：`{oi15s}`",
            f"• 價 15m：`{p15s}`",
            f"• 主動買比：`{tks}`",
            f"• 資金費：`{frs}` · {fr_note}",
            "",
            "*〔風控％〕* · {atr_src_txt}",
            f"• SL 約 `{_sl_pct_txt}`（ATR×{sl_atr:.2f}）",
            f"• TP 約 `{_tp_pct_txt}`（{tp_r:.1f}R）",
            "",
            "*〔參考價〕* · 市價基準",
            f"進場 `{ent_s}` ｜ SL `{sl_s}` ｜ TP `{tp_s}`",
            f"ATR `{atr_s}`（與上列％同源）",
        ]
        msg = "\n".join(msg_lines)
        _gk_pre_send = _crit_radar_gatekeeper_payload(
            it, sym, float(price), fr, is_long, liq_map_cr
        )
        # 發送前再讀冷卻（磁碟／Gist）：避免 Zeabur+GHA 雙排程或兩輪緊貼時，上一輪尚未寫入本機 map 就重複推同幣
        for fk, fv in _crit_radar_load_cooldown().items():
            cd[fk] = max(cd.get(fk, 0.0), fv)
        now_pre = time.time()
        last_push = cd.get(cd_key, 0.0)
        if last_push and (now_pre - last_push) < cool_sec:
            n_cool_reconfirm_skip += 1
            logger.info(
                "[爆擊雷達·冷卻·再確認] 略過 %s %s 分=%s｜約 %.1f 分鐘前已推（剩餘冷卻約 %.2f h，雙排程／併發防重）",
                sym,
                side,
                score,
                (now_pre - last_push) / 60.0,
                (cool_sec - (now_pre - last_push)) / 3600.0,
            )
            continue
        if not jackbot_universal_pre_send_gatekeeper(
            "crit_radar", text=msg, coinglass_trade=_gk_pre_send
        ):
            continue
        ok = send_telegram_message(msg, thread_id, parse_mode="Markdown")
        if ok:
            cd[cd_key] = time.time()
            sent += 1
            _crit_radar_save_cooldown(cd)
            logger.info(
                "[爆擊雷達·推播] 成功 %s %s 分=%s thread_id=%s",
                sym,
                side,
                score,
                thread_id,
            )
        else:
            n_tg_fail += 1
            logger.warning(
                "[爆擊雷達·推播] 失敗 %s %s 分=%s thread_id=%s（TG/DC 其一失敗即 False）",
                sym,
                side,
                score,
                thread_id,
            )

    if sent:
        _crit_radar_save_cooldown(cd)

    logger.info(
        "[爆擊雷達·小結] 發送=%d｜達標候選=%d｜冷卻略過=%d｜冷卻再確認略過=%d｜守門員略過=%d｜無現價=%d｜無ATR=%d｜推播失敗=%d｜"
        "池內模糊=%d｜分數不足=%d｜冷卻檔=%s",
        sent,
        len(candidates),
        n_cool_skip,
        n_cool_reconfirm_skip,
        n_gk_skip,
        n_no_price,
        n_no_atr,
        n_tg_fail,
        n_ambiguous,
        n_low_score,
        CRIT_RADAR_COOLDOWN_FILE,
    )

    if not sent and candidates:
        logger.info(
            "[爆擊雷達·說明] 本輪有達標候選但未發出推播：請對照小結中 冷卻／無現價／無ATR／推播失敗／上限 計數排查"
        )
    elif not candidates:
        logger.info("[爆擊雷達·說明] 本輪無達標候選（門檻與多空差條件偏嚴，屬正常）")


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


def build_hyperliquid_market_digest_message() -> Optional[str]:
    """
    CoinGlass「全市場」補充段（與 whale_wallet_tracker 的指定地址追蹤分源）：
    - /api/hyperliquid/whale-position 榜單節錄
    - /api/hyperliquid/wallet/pnl-distribution 摘要
    - 可選：全域多空帳戶比（若 API 回傳可解析）

    需 CG_API_KEY；另需環境變數 WHALE_CG_MARKET_DIGEST=1 才會送出（避免每輪 cron 洗版）。
    """
    flag = os.getenv("WHALE_CG_MARKET_DIGEST", "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return None
    if not (CG_API_KEY or "").strip():
        logger.info("[HL全市場快照] 略過：未設定 CG_API_KEY")
        return None

    logger.info("[HL全市場快照] 組建 CoinGlass HL 榜單／分布摘要…")
    lines: List[str] = [
        "🧭 *鏈上巨鯨動向｜HL 全市場（CoinGlass）*",
        "_與「指定地址」追蹤為不同資料來源：此為 CoinGlass 匯總榜單／分布_",
        "",
    ]

    top5 = fetch_hyperliquid_whale_position()
    if top5:
        lines.append("*巨鯨持倉榜（whale-position，節錄）*")
        for i, p in enumerate(top5[:5], 1):
            lines.append(f"• {format_whale_position_message(p, i)}")
    else:
        lines.append("*巨鯨持倉榜*：暫無資料（方案權限、或本輪 API 無回應）")

    pnl_raw = fetch_hyperliquid_pnl_distribution()
    pnl_tip = ""
    if isinstance(pnl_raw, dict):
        data_pnl = pnl_raw.get("data") if "data" in pnl_raw else pnl_raw
        if isinstance(data_pnl, dict):
            for k in ("profitablePercent", "winRate", "profitable", "profit_rate"):
                v = data_pnl.get(k)
                if v is not None:
                    try:
                        pv = float(v)
                        if pv > 0:
                            pnl_tip = f"盈利錢包約佔 *{pv:.1f}%*（wallet pnl-distribution）"
                            break
                    except (TypeError, ValueError):
                        pass
    lines.append("")
    if pnl_tip:
        lines.append(f"*全市場盈虧分布*：{pnl_tip}")
    else:
        lines.append("*全市場盈虧分布*：未取得可讀摘要（欄位因 API 版本而異）")

    ls_hint = ""
    try:
        j_ls = _cg_get(CG_EP["hl_global_ls_hist"], {})
        if j_ls:
            raw_ls = j_ls.get("data") or j_ls.get("list") or j_ls
            if isinstance(raw_ls, list) and raw_ls:
                last = raw_ls[-1]
                if isinstance(last, dict):
                    for a, b in [
                        ("longAccount", "shortAccount"),
                        ("long", "short"),
                        ("longRatio", "shortRatio"),
                    ]:
                        if last.get(a) is not None and last.get(b) is not None:
                            try:
                                la = float(last[a])
                                sb = float(last[b])
                                if sb > 0:
                                    ls_hint = f"多空帳戶比（長/短）約 `{la/sb:.3f}`（全域 history 最後一筆）"
                                    break
                            except (TypeError, ValueError, ZeroDivisionError):
                                pass
    except Exception as e_ls:
        logger.debug("[HL全市場快照] global LS history 解析略過: %s", e_ls)

    lines.append("")
    if ls_hint:
        lines.append(f"*帳戶多空*：{ls_hint}")
    else:
        lines.append("*帳戶多空*：本輪無法從 API 解析（可忽略）")

    lines.append("")
    lines.append(f"⏰ {format_datetime(get_taipei_time())}")
    lines.append("_僅供觀察，非投資建議_")
    return "\n".join(lines)


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
    """
    鏈上巨鯨動向（三層，各自獨立）：
    ① whale_wallet_tracker：指定地址 HL API + Etherscan
    ② build_hyperliquid_message：CoinGlass whale-alert（有新事件才推）
    ③ build_hyperliquid_market_digest_message：CoinGlass 榜單／分布（需 WHALE_CG_MARKET_DIGEST=1）

    排程建議（效能／頻率平衡）：
    - 首選每 **30 分鐘** 觸發一次：巨鯨持倉多為較慢節奏，可明顯節省 API 與避免洗版；與現貨回溯窗（WHALE_LOOKBACK_SECONDS 預設 2h）相容。
    - 若最重視 CoinGlass Whale Alert 即時性：可 **15 分鐘**；不建議低於 **5 分鐘**（易重複狀態差分、Etherscan／配額壓力）。
    """
    logger.info(
        "開始執行鏈上巨鯨動向：①地址追蹤 ②CoinGlass Whale Alert ③可選全市場快照（WHALE_CG_MARKET_DIGEST）…"
    )
    thread_id = TG_THREAD_IDS.get("hyperliquid", 252)

    messages = run_whale_wallet_tracker_once(DATA_DIR)
    sent_addr = 0
    if messages:
        for msg in messages[:20]:
            if jackbot_universal_pre_send_gatekeeper("hyperliquid_wallet", text=msg) and send_telegram_message(
                msg, thread_id, parse_mode="Markdown"
            ):
                sent_addr += 1
        logger.info("[鏈上巨鯨] ① 指定地址事件已發送 %s/%s 則", sent_addr, min(len(messages), 20))
    else:
        logger.info("[鏈上巨鯨] ① 指定地址本輪無新事件")

    cg_msg: Optional[str] = None
    try:
        cg_msg = build_hyperliquid_message()
    except Exception as e:
        logger.warning("[鏈上巨鯨] ② CoinGlass 預警訊息構建失敗: %s", e)
    if cg_msg:
        if jackbot_universal_pre_send_gatekeeper("hyperliquid_whale_alert", text=cg_msg) and send_telegram_message(
            cg_msg, thread_id, parse_mode="Markdown"
        ):
            logger.info("[鏈上巨鯨] ② CoinGlass Whale Alert 已發送 1 則")
    else:
        logger.info("[鏈上巨鯨] ② 無新 Whale Alert 或未達內部過濾（見 fetch_hyperliquid_whale_alert 日誌）")

    digest: Optional[str] = None
    try:
        digest = build_hyperliquid_market_digest_message()
    except Exception as e:
        logger.warning("[鏈上巨鯨] ③ 全市場快照構建失敗: %s", e)
    if digest:
        if jackbot_universal_pre_send_gatekeeper("hyperliquid_digest", text=digest) and send_telegram_message(
            digest, thread_id, parse_mode="Markdown"
        ):
            logger.info("[鏈上巨鯨] ③ CoinGlass 全市場快照已發送 1 則")

    if sent_addr == 0 and not cg_msg and not digest:
        logger.info("[鏈上巨鯨] 本輪①②③皆無可推播內容；若需③請設 WHALE_CG_MARKET_DIGEST=1 並確認 CG_API_KEY")


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
        _gmiss = (
            "⚠️ 黃金訊號：找不到 gold_signal_bot 目錄（已嘗試 黃金策略/gold_signal_bot 與 gold_signal_bot），請確認專案結構並部署該資料夾。"
        )
        if jackbot_universal_pre_send_gatekeeper("gold_signal_error", text=_gmiss):
            send_telegram_message(_gmiss, TG_THREAD_IDS.get("gold_signal", 254))
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
        from indicators import add_indicators
        from strategy_orb import compute_signal
        from filters import apply_filters
        from telegram_sender import format_signal_message, format_tp_sl_hit_message, get_gold_chart_keyboard
        logger.info("[黃金訊號] 模組 import 成功")
    except ImportError as e:
        logger.exception("[黃金訊號] 模組 import 失敗: %s", e)
        _gim = f"⚠️ 黃金訊號：依賴缺失（請確認已安裝 yfinance）。{str(e)}"
        if jackbot_universal_pre_send_gatekeeper("gold_signal_error", text=_gim):
            send_telegram_message(_gim, TG_THREAD_IDS.get("gold_signal", 254))
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
    # 先算 ATR/RSI 等，讓波動率與 RSI 濾網在 apply_filters 時有欄位可用
    df_1h = add_indicators(
        df_1h,
        atr_period=cfg.ATR_PERIOD,
        ma_period=cfg.MA_TREND_PERIOD,
        sma_fast=cfg.SMA_FAST,
        sma_slow=cfg.SMA_SLOW,
    )
    logger.info("[黃金訊號] 黃金 1h 數據 OK，共 %s 根 | 時間範圍: %s ~ %s",
                n_rows, df_1h.index.min() if hasattr(df_1h.index, 'min') and len(df_1h) else "N/A", df_1h.index.max() if hasattr(df_1h.index, 'max') and len(df_1h) else "N/A")

    # 狀態檔與模組同目錄（勿用 cwd）：避免從別的工作目錄啟動時 TP/SL 追蹤分裂或遺失
    state_dir = gold_bot_dir / "gold_signal_state"
    state_path = state_dir / "state.json"
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        state_path = gold_bot_dir / "gold_signal_state.json"

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
            if jackbot_universal_pre_send_gatekeeper("gold_signal_tpsl", text=msg_tpsl):
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
    sent = False
    if jackbot_universal_pre_send_gatekeeper("gold_signal", text=msg):
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
        if jackbot_universal_pre_send_gatekeeper("reset_data", text=_msg):
            send_telegram_message(_msg, _thread, parse_mode="Markdown")
            logger.info("【資料重置】Telegram 通知已發送")
        else:
            logger.info("【資料重置】Telegram 通知被守門員略過（空訊息等）")
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
        elif function_name == "crit_radar":
            run_crit_radar_once()
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
            print("  long_term_index       - 長線牛熊導航儀（常駐；預設每 24 小時；排程建議用 long_term_index_once）")
            print("  long_term_index_once  - 長線牛熊導航儀（只執行一次，適合排程）")
            print("  liquidity_radar       - 流動性獵取雷達（極端爆倉彙整）")
            print("  altseason_radar       - 山寨爆發雷達（Altseason + RSI + Buy Ratio）")
            print("  crit_radar            - 爆擊雷達（OI 變化池 + 多空共振 + ATR SL/TP）")
            print("  hyperliquid           - 鏈上巨鯨動向（地址追蹤+CoinGlass；見 WHALE_CG_MARKET_DIGEST）")
            print("  gold_signal           - 黃金 XAUUSD 多空訊號（ORB+MA）")
            print("  api_check             - API 健康檢查（驗證所有端點是否可用）")
            print("  reset_data            - 清除所有冷卻/推播/績效記錄，全新重啟")
    else:
        print("請指定要執行的功能，例如: python jackbot.py sector_ranking")

