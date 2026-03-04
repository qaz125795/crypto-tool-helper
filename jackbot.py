#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
?憛??寥?????芸???剔頂蝯??游?????賣芋憛?"""

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

# ?啁?啣???嚗TC+8嚗?TAIPEI_TZ = timezone(timedelta(hours=8))

# ?蔭?亥?嚗銵?蝯垢憿舐內 + 撖怠 log 瑼??嫣噶?
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

# ==================== ?蔭閮剖? ====================
# 銝敺??啣?霈?霈???踹??函?撘Ⅳ銝剔′蝺?API ?蝑???閮?
# CoinGecko API
CG_GECKO_API_KEY = os.getenv('CG_GECKO_API_KEY')

# CoinGlass API
CG_API_KEY = os.getenv('CG_API_KEY')
CG_API_BASE = "https://open-api-v4.coinglass.com"

# ??????????????????????????????????????????????????????????????????????????????
# CoinGlass API 蝡舫?摰皜嚗4 璅???
# ?澆?嚗??賡?? "頝臬?"  # 隤芣? [??]
# ???頝臬?隞?_v2 / _alt 敺韌????蝡舫?璅釣 ??# ??????????????????????????????????????????????????????????????????????????????
CG_EP = {
    # ???????????????? 鈭斗?撣 Market ????????????????
    "supported_coins":       "/api/futures/supported-coins",                         # ?舀???撟?車?”
    "supported_pairs":       "/api/futures/supported-exchange-pairs",                # ?舀??漱??
    "pairs_markets":         "/api/futures/pairs-markets",                           # ??鈭斗?撠底??
    "coins_markets":         "/api/futures/coins-markets",                           # ??撟?車撣銵?嚗蜓閬???嚗?
    "price_change_list":     "/futures/price-change-list",                           # 撟?車?寞霈??”
    "price_ohlc_history":    "/api/price/ohlc-history",                             # 鈭斗?撠?嘿蝺風??    # ?? ?暹?頝臬???亙? ??
    "price_history_futures": "/api/futures/price/history",                           # ???寞K蝺??楝敺?
    "price_history_spot":    "/api/spot/price/history",                              # ?曇疏?寞K蝺?    "delisted_pairs":        "/api/futures/delisted-exchange-pairs",                 # 撌脖??嗡漱??

    # ???????????????? ??Open Interest ????????????????
    "oi_history":            "/api/futures/openInterest/ohlc-history",              # ???蝺?
    "oi_agg_history":        "/api/futures/openInterest/ohlc-aggregated-history",   # ???蝺?銝餃?嚗?
    "oi_agg_stable":         "/api/futures/openInterest/ohlc-aggregated-stablecoin",# 蝛拙?撟??霅???
    "oi_agg_coin":           "/api/futures/openInterest/ohlc-aggregated-coin-margin-history", # 撟?雿???
    "oi_exchange_list":      "/api/futures/open-interest/exchange-list",              # ????銵剁???蝣箄? kebab-case嚗?
    "oi_exchange_history":   "/api/futures/open-interest/exchange-history-chart",    # ???風?脣?銵剁???蝣箄? kebab-case嚗?    # ?? ?楝敺??湛??典? API ?航?芣?湔頝臬?嚗??
    "oi_history_old":        "/api/futures/open-interest/history",
    "oi_agg_history_old":    "/api/futures/open-interest/aggregated-history",

    # ???????????????? 鞈?鞎餌? Funding Rate ????????????????
    "fr_history":            "/api/futures/fundingRate/ohlc-history",               # 鞎餌?K蝺?
    "fr_oi_weight":          "/api/futures/fundingRate/oi-weight-ohlc-history",     # OI??鞎餌?嚗?蝎暹?嚗?
    "fr_vol_weight":         "/api/futures/fundingRate/vol-weight-ohlc-history",    # ?漱??甈祥??
    "fr_exchange_list":      "/api/futures/fundingRate/exchange-list",              # ??鞎餌??”
    "fr_accum_exchange":     "/api/futures/fundingRate/accumulated-exchange-list",  # 蝝舐?鞎餌?嚗??勗皜穿?
    "fr_arbitrage":          "/api/futures/fundingRate/arbitrage",                  # 鞎餌?憟璈? ??
    # ?? ?楝敺?????
    "fr_history_old":        "/api/futures/funding-rate/history",
    "fr_oi_weight_old":      "/api/futures/funding-rate/oi-weight-history",
    "fr_vol_weight_old":     "/api/futures/funding-rate/vol-weight-history",
    "fr_exchange_list_old":  "/api/futures/funding-rate/exchange-list",
    "fr_accum_exchange_old": "/api/futures/funding-rate/accumulated-exchange-list",

    # ???????????????? 憭征瘥?Long/Short Ratio ????????????????
    "ls_global_history":     "/api/futures/global-long-short-account-ratio/history",# ?函雯撣單憭征瘥?
    "ls_top_account":        "/api/futures/top-long-short-account-ratio/history",   # 憭扳撣單憭征瘥?
    "ls_top_position":       "/api/futures/top-long-short-position-ratio/history",  # 憭扳??蝛箸?

    # ???????????????? 瘛冽???Net Position ????????????????
    "net_pos_v2":            "/api/futures/v2/net-position/history",                  # 瘛典?/蝛箸??風??v2嚗?雿摰嚗?
    "net_pos_v1":            "/api/futures/net-position/history",                     # 瘛典?/蝛箸??風??v1嚗??湛?

    # ???????????????? ??Liquidation ????????????????
    "liq_history":           "/api/futures/liquidation/history",                    # 鈭斗?撠??風??
    "liq_agg_history":       "/api/futures/liquidation/aggregated-history",         # 撟?車???風?莎?銝餃?嚗?
    "liq_coin_list":         "/api/futures/liquidation/coin-list",                  # 撟?車??銵?
    "liq_exchange_list":     "/api/futures/liquidation/exchange-list",              # 鈭斗????銵?
    "liq_order":             "/api/futures/liquidation/order",                      # ?單?????
    "liq_heatmap_m1":        "/api/futures/liquidation/heatmap/model1",             # ??? Model1 ??
    "liq_heatmap_m2":        "/api/futures/liquidation/heatmap/model2",             # ??? Model2 ??
    "liq_heatmap_m3":        "/api/futures/liquidation/heatmap/model3",             # ??? Model3 ??
    "liq_agg_heatmap_m1":    "/api/futures/liquidation/aggregated-heatmap/model1",  # 撟?車???勗???M1 ??
    "liq_agg_heatmap_m2":    "/api/futures/liquidation/aggregated-heatmap/model2",  # 撟?車???勗???M2 ??
    "liq_agg_heatmap_m3":    "/api/futures/liquidation/aggregated-heatmap/model3",  # 撟?車???勗???M3 ??
    "liq_map":               "/api/futures/liquidation/map",                        # ?????
    "liq_agg_map":           "/api/futures/liquidation/aggregated-map",             # 撟?車?????

    # ???????????????? 閮蝪?Orderbook嚗?蝝? ????????????????
    "ob_ask_bids_history":   "/api/futures/orderbook/ask-bids-history",             # 鈭斗?撠??格楛摨行風??    "ob_agg_ask_bids":       "/api/futures/orderbook/aggregated-ask-bids-history",  # 撟?車??瘛勗漲甇瑕
    "ob_heatmap":            "/api/futures/orderbook/history",                      # 閮蝪輻??
    "ob_large_order":        "/api/futures/orderbook/large-limit-order",            # 憭折??
    "ob_large_order_hist":   "/api/futures/orderbook/large-limit-order-history",    # 憭折??甇瑕

    # ???????????????? 銝餃?鞎瑁都嚗?蝝? ????????????????
    "taker_exchange_list":   "/api/futures/taker-buy-sell-volume/exchange-list",    # ??銝餃?鞎瑁都瘥????range=h1嚗?
    "taker_pair_history":    "/api/futures/v2/taker-buy-sell-volume/history",       # 鈭斗?撠蜓?眺鞈?風??v2嚗?瑼Ⅱ隤?
    "taker_agg_history":     "/api/futures/aggregated-taker-buy-sell-volume/history",# 撟?車??銝餃?鞎瑁都嚗蜓??

    # ???????????????? 閮蝪選??曇疏嚗?????????????????
    "spot_ob_ask_bids":      "/api/spot/orderbook/ask-bids-history",                # ?曇疏鈭斗?撠楛摨???
    "spot_ob_agg_ask_bids":  "/api/spot/orderbook/aggregated-ask-bids-history",     # ?曇疏撟?車??瘛勗漲 ??
    "spot_ob_heatmap":       "/api/spot/orderbook/history",                         # ?曇疏閮蝪輻?? ??
    "spot_ob_large_order":   "/api/spot/orderbook/large-limit-order",               # ?曇疏憭折?? ??
    "spot_ob_large_order_h": "/api/spot/orderbook/large-limit-order-history",       # ?曇疏憭折??甇瑕 ??

    # ???????????????? 銝餃?鞎瑁都嚗鞎剁? ????????????????
    "spot_taker_history":    "/api/spot/taker-buy-sell-volume/history",             # ?曇疏鈭斗?撠蜓?眺鞈???
    "spot_taker_agg":        "/api/spot/aggregated-taker-buy-sell-volume/history",  # ?曇疏撟?車?? ??

    # ???????????????? ?曇疏撣 ????????????????
    "spot_supported_coins":  "/api/spot/supported-coins",                           # ?舀??鞎典馳蝔???
    "spot_supported_pairs":  "/api/spot/supported-exchange-pairs",                  # ?舀??鞎其漱?? ??
    "spot_coins_markets":    "/api/spot/coins-markets",                             # ?曇疏撟?車撣 ??
    "spot_pairs_markets":    "/api/spot/pairs-markets",                             # ?曇疏鈭斗?撠?????

    # ???????????????? ?? Options ????????????????
    "opt_max_pain":          "/api/option/max-pain",                                # ?憭抒?暺 ??
    "opt_info":              "/api/option/info",                                    # ??靽⊥ ??
    "opt_exchange_oi":       "/api/option/exchange-oi-history",                     # ?????風????
    "opt_exchange_vol":      "/api/option/exchange-vol-history",                    # ?????漱?風????

    # ???????????????? ?? On-Chain ????????????????
    "exchange_assets":       "/api/exchange/assets",                                # 鈭斗??鞈??摨???
    "exchange_balance_list": "/api/exchange/balance/list",                          # 鈭斗??擗??” ??
    "exchange_balance_chart":"/api/exchange/balance/chart",                         # 鈭斗??擗??” ??
    "exchange_chain_tx":     "/api/exchange/chain/tx/list",                         # ??頧董閮? ??

    # ???????????????? ETF嚗??孵馳 & 隞亙云?? ????????????????
    "btc_etf_list":          "/api/etf/bitcoin/list",                               # 瘥撟ΒTF?” ??
    "btc_etf_flow":          "/api/etf/bitcoin/flow-history",                       # 瘥撟ΒTF鞈?瘚???
    "btc_etf_net_assets":    "/api/etf/bitcoin/net-assets/history",                 # 瘥撟ΒTF瘛刻?????
    "btc_etf_premium":       "/api/etf/bitcoin/premium-discount/history",           # 瘥撟ΒTF皞Ｗ/? ??
    "btc_etf_history":       "/api/etf/bitcoin/history",                            # 瘥撟ΒTF甇瑕 ??
    "btc_etf_price":         "/api/etf/bitcoin/price/history",                      # 瘥撟ΒTF?寞 ??
    "btc_etf_detail":        "/api/etf/bitcoin/detail",                             # 瘥撟ΒTF閰單? ??
    "hk_btc_etf_flow":       "/api/hk-etf/bitcoin/flow-history",                   # 擐葛BTC ETF瘚? ??
    "eth_etf_net_assets":    "/api/etf/ethereum/net-assets-history",                # 隞亙云?TF瘛刻?????
    "eth_etf_list":          "/api/etf/ethereum/list",                              # 隞亙云?TF?” ??
    "eth_etf_flow":          "/api/etf/ethereum/flow-history",                      # 隞亙云?TF鞈?瘚???
    "grayscale_holdings":    "/api/grayscale/holdings-list",                        # ?啣漲??銵???
    "grayscale_premium":     "/api/grayscale/premium-history",                      # ?啣漲皞Ｗ甇瑕 ??

    # ???????????????? 撣?? Indicators ????????????????
    "rsi_list":              "/api/futures/rsi/list",                               # RSI?”
    "contract_basis":        "/api/futures/basis/history",                          # ???箏榆甇瑕 ??
    "borrow_rate":           "/api/borrow-interest-rate/history",                   # ?硫?拍?甇瑕 ??
    "coinbase_premium":      "/api/coinbase-premium-index",                         # Coinbase皞Ｗ? ??
    "bitfinex_margin_ls":    "/api/bitfinex-margin-long-short",                     # Bitfinex靽???蝛???
    "fear_greed":            "/api/index/fear-greed-history",                       # ?鞎芸帚? ??
    "stablecoin_mcap":       "/api/index/stableCoin-marketCap-history",             # 蝛拙?撟???潭風??    "bull_market_peak":      "/api/bull-market-peak-indicator",                     # ????? ??
    "ahr999":                "/api/index/ahr999",                                   # AHR999?? ??
    "puell_multiple":        "/api/index/puell-multiple",                           # Puell憭??? ??
    "stock_flow":            "/api/index/stock-flow",                               # Stock-to-Flow璅∪? ??
    "pi_cycle":              "/api/index/pi-cycle-indicator",                       # Pi Cycle??? ??
    "golden_ratio":          "/api/index/golden-ratio-multiplier",                  # 暺?瘥?銋 ??
    "btc_profitable_days":   "/api/index/bitcoin/profitable-days",                  # BTC?憭拇 ??
    "btc_rainbow":           "/api/index/bitcoin/rainbow-chart",                    # BTC敶抵????
    "btc_bubble_index":      "/api/index/bitcoin/bubble-index",                     # BTC瘜⊥疵? ??
    "ma_2yr_multiplier":     "/api/index/2-year-ma-multiplier",                     # 2撟游?蝺?????
    "ma_200wk_heatmap":      "/api/index/200-week-moving-average-heatmap",          # 200?勗?蝺?? ??

    # ???????????????? Hyperliquid ????????????????
    "hl_whale_alert":        "/api/hyperliquid/whale-alert",                        # HL攳券??郎
    "hl_whale_position":     "/api/hyperliquid/whale-position",                     # HL攳券???
    "hl_position":           "/api/hyperliquid/position",                           # HL撟?車??
    "hl_wallet_pos_dist":    "/api/hyperliquid/wallet/position-distribution",       # HL?Ｗ???撣?
    "hl_wallet_pnl_dist":    "/api/hyperliquid/wallet/pnl-distribution",            # HL?Ｗ????

    # ???????????????? CVD嚗?頝臬?靽?嚗?????????????????
    "cvd_history":           "/api/futures/cvd/history",
    "cvd_agg_history":       "/api/futures/aggregated-cvd/history",

    # ???????????????? ?單郊????湧??? ???怠?嚗?????????????????
    "footprint":             "/api/futures/volume/footprint-history",               # ?????撣唾?
}

# Tree of Alpha API
TREE_API_KEY = os.getenv('TREE_API_KEY')

# Telegram ?蔭
TG_TOKEN = os.getenv('TG_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

# Telegram Thread IDs (敺憓?????JSON嚗?雿輻?身??
thread_ids_str = os.environ.get('TG_THREAD_IDS', '')
if thread_ids_str:
    try:
        TG_THREAD_IDS = json.loads(thread_ids_str)
    except:
        TG_THREAD_IDS = {
            'sector_ranking': 5,
            'buying_power_monitor': 246,  # ??whale_position嚗歇?踵??箄頃鞎瑕???
            'position_change': 250,
            'economic_data': 13,
            'news': 7,
            'funding_rate': 244,
            'long_term_index': 248,
            'liquidity_radar': 3,
            'altseason_radar': 11044,
            'hyperliquid': 252,
            'gold_signal': 254,  # 暺? XAUUSD 閮?嚗?寧撠 topic ??thread_id嚗?
        }
else:
    TG_THREAD_IDS = {
        'sector_ranking': int(os.environ.get('TG_THREAD_SECTOR_RANKING', 5)),
        'buying_power_monitor': int(os.environ.get('TG_THREAD_WHALE_POSITION', 246)),  # 雿輻??whale_position ??thread ID
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

# ?嗡??蔭
EXCHANGE = "Binance"
TIME_TYPE = "h1"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
# ???祟?賂??寧?芸皜砍?蝝馳蝔殷?雿輻 API ?脣?嚗?MAX_SYMBOLS = 904  # 撠 API 餈???蝝馳蝔格?捱摰?
# ?豢?摮?桅?嚗蝙?刻?祆??函??蝣箔? cron/Zeabur 蝑???cwd 銝楝敺??湛?
DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_DIR.mkdir(exist_ok=True)
# ??撖怠 log 瑼??嫣噶鈭??
_log_file = DATA_DIR / "jackbot.log"
_fh = logging.FileHandler(_log_file, encoding="utf-8")
_fh.setLevel(logging.INFO)
_fh.setFormatter(logging.Formatter(_log_fmt))
logging.getLogger().addHandler(_fh)

# CoinGlass OI ?澆?????80 甈???嚗lobal 敹??典?詨???恐??鞈血潘?
_coinglass_oi_rate_limiter = None

# CoinGlass API ?典??澆閮嚗?皞? 300/min嚗???50 蝺抵?嚗身 250嚗?
_coinglass_api_counter: Dict[str, Any] = {"window_start": 0.0, "count": 0}
_coinglass_api_counter_lock = threading.Lock()
_COINGLASS_MAX_CALLS_PER_MINUTE = 250

# BingX ?銵?璅仃?活?賂?瘥憚?冽?斗?臬? CoinGlass Plan B嚗?# ??蝣箔? ThreadPoolExecutor 銝衣?啣?銝??豢迤蝣?
_bingx_tech_fail_count: int = 0
_bingx_tech_fail_lock = threading.Lock()

# OI API ?敺?甈∪?怎? HTTP ??Ⅳ?隤方??荔?靘?process_single_symbol 閮箸?嚗?
_oi_last_status: Dict[str, int] = {}
_oi_last_error: Dict[str, str] = {}

# ?? OI ?瑼餌絞閮?瘥憚??fetch_position_change ?寞??嗅?璅????湔嚗?# 摰???潮??剁?蝣箔? _classify_signal_and_tier ??fetch_position_change ?甇?Ⅱ摮?
_dynamic_oi_mean_30m: Optional[float] = None
_dynamic_oi_std_30m: Optional[float] = None
_dynamic_oi_4star: Optional[float] = None
_dynamic_oi_5star: Optional[float] = None
_dynamic_oi_sample_size: int = 0

# 憭抒?啣??瞈曄雯嚗????? BTC 30m / 1H 瞍脰?撟?靘???閮餃之?斤??
_btc_30m_pct: Optional[float] = None
_btc_1h_pct: Optional[float] = None   # BTC 1H ?孵?嚗???30m ?斗憭抒撘瑕摹

# 蝺亙??湛?GitHub Action timeout (SIGTERM) ?Ⅱ靽?sniper_cooldown.json ?賢神??蝣?# fetch_position_change ?瑁??????湔甇?dict嚗texit/SIGTERM handler 霈??撖怠
_emergency_sniper_state: Dict[str, Any] = {}
_emergency_sniper_path: Optional[str] = None

# ?? API ???(Circuit Breaker) ??????????????????????????????????????????????
# ?仿???箇 5 甈?429嚗?? MAX_WORKERS ? 1 銝血? wait_time ???? 5 ??
_circuit_breaker: Dict[str, Any] = {
    "consecutive_429": 0,
    "warned": False,       # 3+ 甈?429嚗脣?郎?芋撘?MAX_WORKERS??
    "tripped": False,      # 5+ 甈?429嚗脣???瑯芋撘?MAX_WORKERS??
    "trip_time": 0.0,
    "trip_duration": 300.0,  # 5 ??靽風??tripped ???
    "warn_duration": 120.0,  # 2 ??霅行???warned ???
    "warn_time": 0.0,
}
_circuit_breaker_lock = threading.Lock()


def _cb_record_429() -> None:
    """閮?銝甈?429 ?航炊??    ?? 甈∴??脣霅行?嚗AX_WORKERS??嚗???霅瘀???    ?? 甈∴??脣?嚗AX_WORKERS??嚗??其?霅瘀???    """
    with _circuit_breaker_lock:
        _circuit_breaker["consecutive_429"] += 1
        cnt = _circuit_breaker["consecutive_429"]
        now = time.time()
        # 霅行??挾嚗?-4 甈∴?
        if cnt >= 3 and not _circuit_breaker["warned"] and not _circuit_breaker["tripped"]:
            _circuit_breaker["warned"] = True
            _circuit_breaker["warn_time"] = now
            logger.warning(
                f"[??刻郎??儭 ??? {cnt} 甈?429嚗?"
                f"MAX_WORKERS ? 2嚗?蝥?{_circuit_breaker['warn_duration']:.0f} 蝘?霅?"
            )
        # ??挾嚗?+ 甈∴?
        if cnt >= 5 and not _circuit_breaker["tripped"]:
            _circuit_breaker["tripped"] = True
            _circuit_breaker["trip_time"] = now
            logger.warning(
                f"[??典???沘 ??? {cnt} 甈?429嚗?"
                f"MAX_WORKERS ? 1嚗?蝥?{_circuit_breaker['trip_duration']/60:.0f} ??摰靽風"
            )


def _cb_record_success() -> None:
    """閮?銝甈⊥???瘙??蔭??? 429 閮?郎????"""
    with _circuit_breaker_lock:
        if _circuit_breaker["consecutive_429"] > 0:
            _circuit_breaker["consecutive_429"] = 0
        _circuit_breaker["warned"] = False


def _cb_is_tripped() -> bool:
    """?斗??冽?虫??具??函??瑯?霅瑟?嚗??敺押?"""
    with _circuit_breaker_lock:
        if not _circuit_breaker["tripped"]:
            return False
        elapsed = time.time() - _circuit_breaker["trip_time"]
        if elapsed >= _circuit_breaker["trip_duration"]:
            _circuit_breaker["tripped"] = False
            _circuit_breaker["warned"] = False
            _circuit_breaker["consecutive_429"] = 0
            logger.info("[??冽敺抽?] 5 ??靽風?????Ｗ儔甇?虜銝西??貉?蝑???")
            return False
        return True


def _cb_is_warned() -> bool:
    """?斗??冽?虫??具郎????3-4 甈?429嚗??唳??芸?閫???"""
    with _circuit_breaker_lock:
        if not _circuit_breaker["warned"] or _circuit_breaker["tripped"]:
            return False
        elapsed = time.time() - _circuit_breaker["warn_time"]
        if elapsed >= _circuit_breaker["warn_duration"]:
            _circuit_breaker["warned"] = False
            _circuit_breaker["consecutive_429"] = max(0, _circuit_breaker["consecutive_429"] - 2)
            logger.info("[??刻郎?圾?毋?（ 霅行?????MAX_WORKERS ???喲?閮剖?")
            return False
        return True


def _cb_get_max_workers(default: int = 15) -> int:
    """?寞???函????遣霅唳?憭批銵??詻?    甇?虜 ??default(12)嚗郎??3甈?29) ??2嚗??函???5甈?29) ??1
    """
    if _cb_is_tripped():
        return 1
    if _cb_is_warned():
        return 2
    return default


def _cb_get_wait_multiplier() -> float:
    """?寞???函?????wait_time ????    甇?虜 ??1?嚗郎????1.5?嚗??函?????2?
    """
    if _cb_is_tripped():
        return 2.0
    if _cb_is_warned():
        return 1.5
    return 1.0


def _emergency_save_sniper_state() -> None:
    """蝺亙??游神?伐?atexit ??SIGTERM handler ?梁??    蝣箔? GitHub Action timeout ??憭?甇Ｗ?嚗niper_cooldown.json ?質蝤???    """
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
            f"[蝺亙??弼 sniper_cooldown.json 撌脣??典神??({path})"
        )
    except Exception as ex:
        logging.getLogger(__name__).warning(f"[蝺亙??弼 蝺亙神?亙仃?? {ex}")


import atexit as _atexit
import signal as _signal

_atexit.register(_emergency_save_sniper_state)

def _sigterm_handler(signum, frame):  # type: ignore[type-arg]
    _emergency_save_sniper_state()
    raise SystemExit(0)

try:
    _signal.signal(_signal.SIGTERM, _sigterm_handler)
except (OSError, ValueError):
    # ???啣?嚗indows/?蜓?瑁?蝺?銝??SIGTERM嚗蕭??
    pass


def _respect_coinglass_rate_limit() -> None:
    """蝪∪????嚗Ⅱ靽?CoinGlass API 蝝?<70 甈?????"""
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
                logger.info(f"[CoinGlass ??靽風] ?砍???API 撌脤? {count} 甈∴?隡 {sleep_for:.1f} 蝘?蝜潛?")
                time.sleep(sleep_for)
            window_start = time.time()
            count = 0
        _coinglass_api_counter["window_start"] = window_start
        _coinglass_api_counter["count"] = count + 1

# ==================== 撌亙?賣 ====================

def send_telegram_message(text: str, thread_id: int, parse_mode: str = "Markdown", reply_markup: Optional[Dict] = None) -> bool:
    """?潮??臬 Telegram嚗??Inline Keyboard ??嚗?"""
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
                logger.info("Telegram 閮?潮???")
                return True
            else:
                logger.error(f"Telegram API ?航炊: {result}")
                return False
        else:
            logger.error(f"Telegram HTTP ?航炊: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        logger.error(f"?潮?Telegram 閮憭望?: {str(e)}")
        return False


def load_json_file(filepath: Path, default: Any = None) -> Any:
    """敺?隞嗅?頛?JSON ?豢?嚗銝餅?隞嗆?瘥?蝛綽??芸??岫敺?隞賣敺押?"""
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

    # 銝餅?隞嗆?瘥??摮嚗?閰血?隞?
    backup_path = DATA_DIR / "backup_state.json"
    if backup_path.exists() and backup_path != filepath:
        try:
            backup_all: Dict[str, Any] = json.loads(backup_path.read_text(encoding='utf-8'))
            key = str(filepath.name)
            backed = backup_all.get(key)
            if backed is not None:
                logger.warning(f"[?遢??] 銝餅?隞?{filepath.name} ??/?箇征嚗歇敺?backup_state.json ?Ｗ儔")
                return backed
        except Exception as be:
            logger.debug(f"[?遢??] 霈??隞賢仃?? {be}")

    if filepath.exists():
        logger.error(f"{filepath}")
    return default if default is not None else []


def save_json_file_safe(filepath: Path, data: Any) -> bool:
    """??摰撖怠 JSON 瑼???    撖虫?瘚?嚗itHub Actions 頞?銝剜靽風嚗?
      1. 撖怠 <filepath>.tmp ?怠?瑼???fsync嚗?      2. os.replace() ???孵? ??蝣箔??格?瑼?瘞訾???神?亦???      3. ?亦????嚗niper_cooldown.json / last_summary_date.json嚗?
         ?郊?湔 data/backup_state.json 憭?靽風

    撱箄降???銋???JSON ?賣?冽迨?賣?蹂誨 open()/json.dump()??    """
    try:
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        # sniper_cooldown.json 雿輻?箏??迂 temp_sniper.json ?踹??隞?.tmp 瑼?銵?
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
                pass  # Windows/?? FS 銝??fsync嚗蕭??
                os.replace(tmp_path, filepath)  # ???孵?嚗?銝?葉??瘥??
        # ????嚗?甇亙神??backup_state.json嚗???霅瘀?
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
                logger.warning(f"[?遢撖怠] backup_state.json ?湔憭望?嚗?敶梢銝餅?蝔?: {be}")
        return True
    except Exception as e:
        logger.error(f"[safe撖怠憭望?] {filepath}: {e}")
        return False


def save_json_file(filepath: Path, data: Any) -> bool:
    """???詨捆???剁?撖阡?憪晷蝯?save_json_file_safe??"""
    return save_json_file_safe(filepath, data)


def translate_text(text: str, target_lang: str = 'zh-tw') -> str:
    """蝧餉陌?嚗蝙??googletrans嚗???剁?"""
    try:
        from googletrans import Translator
        translator = Translator()
        result = translator.translate(text, dest=target_lang)
        return result.text
    except ImportError:
        logger.warning("googletrans ?芸?鋆?頝喲?蝧餉陌")
        return text
    except Exception as e:
        logger.warning(f"蝧餉陌憭望?: {str(e)}嚗蝙?典???")
        return text


def get_taipei_time(dt: Optional[datetime] = None) -> datetime:
    """?脣??啁?啣???嚗TC+8嚗?"""
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        # 憒?瘝???鞈?嚗?閮剜 UTC
        dt = dt.replace(tzinfo=timezone.utc)
    # 頧??箏?????
    return dt.astimezone(TAIPEI_TZ)


def format_datetime(dt: datetime) -> str:
    """?澆???????芸?頧??箏?????"""
    # 頧??箏?????
    dt_taipei = get_taipei_time(dt)
    weekdays = ['一', '二', '三', '四', '五', '六', '日']
    weekday = weekdays[dt_taipei.weekday()]
    return dt_taipei.strftime(f"%Y-%m-%d ({weekday}) %H:%M")


# ==================== 1. 銝餅??踹???璁??====================

MAIN_SECTORS = {
    "Artificial Intelligence (AI)": "AI 璈鈭箏鼠??",
    "Meme": "Meme 餈瑕? (撟渲?鈭箸???",
    "Smart Contract Platform": "?箸?? (?箇?撱箄身)",
    "Decentralized Finance (DeFi)": "DeFi (??銵?",
    "Exchange-based Tokens": "鈭斗??隞?馳 (?詨?撟?",
    "Real World Assets (RWA)": "RWA (?輻/暺?銝?)",
    "Gaming (GameFi)": "GameFi (??竟??",
    "Stablecoins": "蝛拙?撟?(蝢?)"
}


def fetch_sector_ranking():
    """??銝餅??踹???璁?"""
    url = f"https://api.coingecko.com/api/v3/coins/categories?x_cg_demo_api_key={CG_GECKO_API_KEY}"
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            logger.error(f"CoinGecko API ?航炊: {response.status_code}")
            return
        
        categories = response.json()
        
        # ?蕪銝虫葉??
        filtered_sectors = []
        for category in categories:
            if category.get('name') in MAIN_SECTORS:
                filtered_sectors.append({
                    'displayName': MAIN_SECTORS[category['name']],
                    'change': category.get('market_cap_change_24h', 0)
                })
        
        # ??
        filtered_sectors.sort(key=lambda x: x['change'], reverse=True)
        
        send_ranking_to_tg(filtered_sectors)
        
    except Exception as e:
        logger.error(f"?豢???憭望?: {str(e)}")


def send_ranking_to_tg(ranking: List[Dict]):
    """?潮?銵???Telegram嚗戭文??? + ?勗?????"""
    message = "?? *??蜓瘚?撖憛?銵???4H)*\n\n"
    message += "? *銝餅??踹?撘瑕摹銝閬踝?*\n"

    for index, sector in enumerate(ranking):
        medal = "??" if index == 0 else "??" if index == 1 else "??" if index == 2 else "?"
        ch = sector.get("change", 0) or 0
        change_str = f"{ch:.2f}"
        sign = "+" if ch >= 0 else ""
        # 閬死??嚗?5% ?怎? / <-5% ?瑕 / -1%~1% ?斗 / ?園? ????
        if ch > 5:
            prefix = "?"
        elif ch < -5:
            prefix = "??"
        elif -1 <= ch <= 1:
            prefix = "?"
        else:
            prefix = "??" if ch > 0 else "??"
        message += f"{medal} {prefix} *{sector['displayName']}* `{sign}{change_str}%`\n"

    message += "\n? _?勗???AI 瘥?撠??芸???鞈?瘚?_"

    keyboard = {
        "inline_keyboard": [
            [{"text": "? ?亦??黎?勗???(暺?)", "url": "https://www.coingecko.com/zh-tw/categories#key-stats"}]
        ]
    }
    send_telegram_message(message, TG_THREAD_IDS["sector_ranking"], reply_markup=keyboard)


# ==================== 2. 撌券祠?之?嗆?????====================


def fetch_stablecoin_marketcap_history() -> Optional[List[Dict]]:
    """?脣?蝛拙?撟???潭風?脫??"""
    url = "https://open-api-v4.coinglass.com/api/index/stableCoin-marketCap-history"
    headers = {
        "CG-API-KEY": CG_API_KEY,
        "accept": "application/json"
    }
    
    try:
        logger.info(f"API: {url}")
        response = requests.get(url, headers=headers, timeout=10)
        logger.info(f"蝛拙?撟????API ?踵???Ⅳ: {response.status_code}")
        
        if response.status_code != 200:
            logger.error(f"蝛拙?撟????API 餈???Ⅳ: {response.status_code}")
            logger.error(f"?踵??批捆: {response.text[:500]}")
            return None
        
        data = response.json()
        logger.info(f"蝛拙?撟????API 餈??豢?蝯?: code={data.get('code')}, msg={data.get('msg')}")
        # 頛詨摰???瑽誑靘輯矽閰?
        logger.info(f"摰?踵?蝯?嚗?2000摮泵嚗? {json.dumps(data, ensure_ascii=False, indent=2)[:2000]}")
        
        # 瑼Ｘ餈?蝣?
        if data.get('code') not in ['0', 0, 200, '200', None]:
            error_msg = data.get('msg') or data.get('message') or '?芰?航炊'
            logger.error(f"蝛拙?撟????API 餈??航炊: {error_msg} (code: {data.get('code')})")
            return None
        
        # 餈??豢??”嚗?祕??API ?踵?蝯?嚗?
        # API 餈?蝯?: { "code": "0", "data": { "data_list": [...] } }
        data_content = data.get('data')
        
        if isinstance(data_content, dict):
            # 瑼Ｘ data_list 摮挾
            data_list = data_content.get('data_list')
            if isinstance(data_list, list) and len(data_list) > 0:
                logger.info(f"???脣?蝛拙?撟???潭?? {len(data_list)} 璇???")
                # 頧??豢??澆?嚗?瘥?{ "USDT": value } 頧??箸?皞撘?
                formatted_list = []
                for idx, item in enumerate(data_list):
                    if isinstance(item, dict):
                        # 閮?蝮賢??潘??蜇??帘摰馳嚗?
                        total_mcap = sum(float(v) for v in item.values() if isinstance(v, (int, float)))
                        # ???USDT嚗??瘙?
                        usdt_mcap = item.get('USDT') or item.get('usdt') or 0
                        
                        # 雿輻蝮賢??潭? USDT 撣潘??芸?雿輻蝮賢??潘?
                        mcap_value = total_mcap if total_mcap > 0 else float(usdt_mcap)
                        
                        # 瑽遣璅??澆????
                        # 瘜冽?嚗PI ?航瘝????喉?雿輻蝝Ｗ?雿????嚗??啁??冽?敺?
                        formatted_item = {
                            'marketCap': mcap_value,
                            'market_cap': mcap_value,
                            'value': mcap_value,
                            'time': None,  # 憒? API 瘝???????                            'timestamp': None,
                            'index': idx  # ?冽??
                        }
                        formatted_list.append(formatted_item)
                
                logger.info(f"?澆?????? {len(formatted_list)} 璇???")
                return formatted_list
        
        # 憒? data ?臬?銵剁??湔餈?嚗??閬撘?嚗?
        if isinstance(data_content, list) and len(data_content) > 0:
            logger.info(f"data ?臬?銵剁??湔餈?: {len(data_content)} 璇???")
            return data_content
        
        # ?岫?嗡??航??畾?
        for key in ['data_list', 'list', 'items', 'history', 'marketCap', 'market_cap', 'values', 'records']:
            if key in data:
                value = data[key]
                if isinstance(value, list) and len(value) > 0:
                    logger.info(f"敺?{key} 摮挾?脣??豢?: {len(value)} 璇???")
                    return value
        
        # 憒???曆??堆?閮?摰???瑽誑靘輯矽閰?
        logger.warning(f"蝛拙?撟????API 餈???撘?蝚血???")
        logger.info(f"?豢?憿?: {type(data_content)}")
        if isinstance(data_content, dict):
            logger.info(f"data 摮?: {list(data_content.keys())}")
        logger.info(f"?豢?蝯?嚗?1000摮泵嚗? {json.dumps(data, ensure_ascii=False, indent=2)[:1000]}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"蝛拙?撟????API 隢?憭望?: {str(e)}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"蝛拙?撟????API ?踵? JSON 閫??憭望?: {str(e)}")
        logger.error(f"?踵??批捆: {response.text[:500] if 'response' in locals() else 'N/A'}")
        return None
    except Exception as e:
        logger.error(f"?脣?蝛拙?撟???潭風?脣仃?? {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return None


def fetch_aggregated_stablecoin_oi_history(symbol: str = "BTC", interval: str = "1h") -> Optional[List[Dict]]:
    """?脣???蝛拙?撟??霅??風?脫??"""
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
            logger.error(f"蝛拙?撟?OI API 餈???Ⅳ: {response.status_code}")
            return None
        
        data = response.json()
        if data.get('code') not in ['0', 0, 200, '200']:
            logger.error(f"蝛拙?撟?OI API 餈??航炊: {data.get('msg')}")
            return None
        
        # 餈??豢??”
        data_list = data.get('data', [])
        if isinstance(data_list, list):
            return data_list
        return None
    except Exception as e:
        logger.error(f"?脣?蝛拙?撟?OI 甇瑕憭望?: {str(e)}")
        return None


def calculate_marketcap_change(data_list: List[Dict]) -> Optional[Dict]:
    """閮?蝛拙?撟???潸???嚗?撠???4撠?嚗?"""
    if not data_list or len(data_list) < 2:
        return None
    
    # ????揣撘?摨???啁??冽?敺?
    def get_sort_key(item):
        time_val = item.get('time') or item.get('timestamp')
        if time_val is not None:
            return time_val
        # 憒?瘝????喉?雿輻蝝Ｗ?
        index_val = item.get('index')
        if index_val is not None:
            return index_val
        # 憒??賣???餈? 0嚗?????嚗?
        return 0
    
    sorted_data = sorted(data_list, key=get_sort_key)
    
    # ?脣???啣?
    latest = sorted_data[-1]
    latest_mcap = latest.get('marketCap') or latest.get('market_cap') or latest.get('value')
    
    if latest_mcap is None:
        return None
    
    # 閮?1撠???4撠?霈?
    # 憒??豢?瘝????喉?雿輻?豢?暺揣撘?隡啁?
    # ?身?豢??舀?撠?銝??嚗??寞?撖阡???隤踵嚗?
    one_hour_data = None
    twenty_four_hours_data = None
    
    if len(sorted_data) >= 2:
        # 憒??豢????嚗蝙?冽??
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
            # 憒?瘝????喉?雿輻蝝Ｗ?靘摯蝞??身?豢??舀?撠?銝??嚗?
            # 1撠???= ?蝚???嚗????店嚗?
            if len(sorted_data) >= 2:
                one_hour_data = sorted_data[-2]
            # 24撠???= ?蝚?5??嚗????店嚗?
            if len(sorted_data) >= 25:
                twenty_four_hours_data = sorted_data[-25]
            elif len(sorted_data) >= 2:
                # 憒??豢?暺?頞?4??雿輻??拍??豢?暺?
                twenty_four_hours_data = sorted_data[0]
    
    result = {
        'latest_mcap': float(latest_mcap),
        'change_1h': None,
        'change_24h': None
    }
    
    # 閮?1撠?霈???
    if one_hour_data:
        one_hour_mcap = one_hour_data.get('marketCap') or one_hour_data.get('market_cap') or one_hour_data.get('value')
        if one_hour_mcap and one_hour_mcap > 0:
            result['change_1h'] = ((latest_mcap - one_hour_mcap) / one_hour_mcap) * 100
    
    # 閮?24撠?霈???
    if twenty_four_hours_data:
        twenty_four_hours_mcap = twenty_four_hours_data.get('marketCap') or twenty_four_hours_data.get('market_cap') or twenty_four_hours_data.get('value')
        if twenty_four_hours_mcap and twenty_four_hours_mcap > 0:
            result['change_24h'] = ((latest_mcap - twenty_four_hours_mcap) / twenty_four_hours_mcap) * 100
    
    return result


def calculate_oi_change(data_list: List[Dict]) -> Optional[Dict]:
    """閮?蝛拙?撟?OI 霈???1撠???4撠?嚗?"""
    if not data_list or len(data_list) < 2:
        return None
    
    # ?????
    sorted_data = sorted(data_list, key=lambda x: x.get('time', 0) or x.get('timestamp', 0))
    
    # ?脣???啣潘?雿輻 close ??value嚗?
    latest = sorted_data[-1]
    latest_oi = latest.get('close') or latest.get('value') or latest.get('openInterest')
    
    if latest_oi is None:
        return None
    
    # 閮?1撠?霈?
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
    
    # 閮?24撠?霈?
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
    
    # 閮?1撠?霈???
    if one_hour_data:
        one_hour_oi = one_hour_data.get('close') or one_hour_data.get('value') or one_hour_data.get('openInterest')
        if one_hour_oi and one_hour_oi > 0:
            result['change_1h'] = ((latest_oi - one_hour_oi) / one_hour_oi) * 100
    
    # 閮?24撠?霈???
    if twenty_four_hours_data:
        twenty_four_hours_oi = twenty_four_hours_data.get('close') or twenty_four_hours_data.get('value') or twenty_four_hours_data.get('openInterest')
        if twenty_four_hours_oi and twenty_four_hours_oi > 0:
            result['change_24h'] = ((latest_oi - twenty_four_hours_oi) / twenty_four_hours_oi) * 100
    
    return result


def _fetch_usdt_premium() -> Optional[float]:
    """?亥岷 USDT/USD 皞Ｗ??甇??皞Ｗ=?祕鞎瑞嚗????=?祉?憟嚗?    雿輻 Binance ?祇? API ?? USDCUSDT ?舐?嚗SDC ??銝?= 1 USD嚗?    ??premium = (1.0 / USDCUSDT - 1.0) * 100嚗?    USDCUSDT < 1.0 隞?” 1 USDC 鞎瑚???1 USDT ??USDT 皞Ｗ嚗?瘙??
    USDCUSDT > 1.0 隞?” 1 USDC > 1 USDT ??USDT ?嚗蝤??拍銝鳴?
    """
    try:
        url = "https://api.binance.com/api/v3/ticker/price"
        resp = requests.get(url, params={"symbol": "USDCUSDT"}, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            usdc_usdt = float(data.get("price", 1.0))
            if usdc_usdt > 0:
                premium_pct = (1.0 / usdc_usdt - 1.0) * 100.0
                logger.info(f"[USDT皞Ｗ] USDCUSDT={usdc_usdt:.6f} ??皞{premium_pct:+.4f}%")
                return round(premium_pct, 4)
    except Exception as e:
        logger.warning(f"[USDT皞Ｗ] ?亥岷憭望?: {e}")
    return None


def _make_fuel_bar(score: int, max_score: int = 5) -> str:
    """?????脣漲璇???????皛踹? 5 ?潘?"""
    filled = max(0, min(score, max_score))
    empty = max_score - filled
    return "??" * filled + "??" * empty


def _fetch_smart_money_oi_split(symbol: str = "BTC") -> Dict[str, Any]:
    """?唳???OI ??嚗帘摰馳靽???撠平鞈?嚗s 撟?雿?霅?嚗?嗆?獢選???    ?寞?A嚗ggregated-stablecoin-history + aggregated-coin-margin-history嚗?蝎暹?嚗?    ?寞?B嚗ggregated-history 蝮賡?嚗??湛??⊥???雿撠??豢?嚗?    ? {"stable_chg": float, "coin_chg": float, "smart_money": bool/None}
    """
    empty = {"stable_chg": None, "coin_chg": None, "smart_money": None, "data_source": "none"}
    base = symbol.upper().replace("USDT", "")
    params = {"symbol": base, "interval": "15m", "limit": 4}

    logger.debug(f"[?唳??㏕I] ?岫??蝛拙?撟?撟?雿I?? symbol={base}")

    stable_bars, coin_bars = None, None
    try:
        j_s = _cg_get(CG_EP["oi_agg_stable"], params)
        rows_s = j_s.get("data") or j_s.get("list") or [] if j_s else []
        stable_bars = _parse_oi_bars_from_rows(rows_s) if rows_s else None
        logger.debug(f"[?唳??㏕I] I: {len(stable_bars) if stable_bars else 0}璉?")
    except Exception as e_s:
        logger.debug(f"[?唳??㏕I] 蝛拙?撟ΜI?啣虜: {e_s}")
    try:
        j_c = _cg_get(CG_EP["oi_agg_coin"], params)
        rows_c = j_c.get("data") or j_c.get("list") or [] if j_c else []
        coin_bars = _parse_oi_bars_from_rows(rows_c) if rows_c else None
        logger.debug(f"[?唳??㏕I] I: {len(coin_bars) if coin_bars else 0}璉?")
    except Exception as e_c:
        logger.debug(f"[?唳??㏕I] 撟?雿I?啣虜: {e_c}")

    stable_chg = coin_chg = None
    if stable_bars and len(stable_bars) >= 2 and stable_bars[-2] != 0:
        stable_chg = round((stable_bars[-1] - stable_bars[-2]) / stable_bars[-2] * 100, 3)
    if coin_bars and len(coin_bars) >= 2 and coin_bars[-2] != 0:
        coin_chg = round((coin_bars[-1] - coin_bars[-2]) / coin_bars[-2] * 100, 3)

    # ?唳??Ｗ?瘀?蝛拙?撟ΜI憓? 銝?撟?雿I銝?????憓?蝛拙?撟???游?嚗?
    # ??撠平璈??典遣??銝???馳瑽▼
    smart_money = None
    if stable_chg is not None and coin_chg is not None:
        if stable_chg > 0.2 and coin_chg <= 0.1:
            smart_money = True   # ?唳??Ｖ蜓撠遣??
        elif coin_chg > 0.5 and stable_chg <= 0.1:
            smart_money = False  # ??瑽▼銝餃?
        else:
            smart_money = None   # 瘛瑕?嚗瘜??
    elif stable_chg is not None:
        smart_money = stable_chg > 0.2

    if smart_money is True:
        logger.info(f"[?唳??㏕I? {base}: 蝛拙?撟{_chg:.3f}% 撟?{coin_chg if coin_chg is not None else 'N/A'} ??撠平鞈?撱箏?")
    elif smart_money is False:
        logger.info(f"[?唳??㏕I??] {base}: 撟?{_chg:.3f}% 蝛拙?撟{stable_chg if stable_chg is not None else 'N/A'} ????瑽▼銝餃?")

    return {"stable_chg": stable_chg, "coin_chg": coin_chg, "smart_money": smart_money, "data_source": "split"}


def _calc_fuel_score(mcap_15m: float, mcap_1h: float, oi_15m: float, oi_1h: float,
                     usdt_premium: Optional[float],
                     smart_money: Optional[bool] = None) -> int:
    """閮???蝛?嚗?-7嚗??啣??唳??Ｙ雁摨佗?
    蝛拙?撟?15m 瘚 (+1)?帘摰馳 1h 瘚 (+1)??    OI 15m ?游撐 (+1)?I 1h ?游撐 (+1)?SDT 皞Ｗ > 0.05% (+1)
    ?唳??㏕I銝餃?嚗帘摰馳>撟?雿?(+1)??撘瑕?蝣箄?(+1)
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
    if smart_money is True and oi_1h > 0.8:  # ?唳????撘菟?蝣箄?
        score += 1
    return score


def buying_power_monitor():
    """??撣???扼??脣=?潸?嚗?瑕之?文??踝?15m 擃??+ ?唳??Ｘ?璅?"""
    logger.info("???瑁???????嚗?5m 擃??+ ?唳??Ｘ???嚗?..")
    marketcap_data = fetch_stablecoin_marketcap_history()
    mcap_change = calculate_marketcap_change(marketcap_data) if marketcap_data else {}

    # ??嚗?????15m ??1h OI
    oi_data_15m = fetch_aggregated_stablecoin_oi_history("BTC", "15m")
    oi_data_1h = fetch_aggregated_stablecoin_oi_history("BTC", "1h")
    oi_change_15m = calculate_oi_change(oi_data_15m) if oi_data_15m else {}
    oi_change_1h = calculate_oi_change(oi_data_1h) if oi_data_1h else {}

    # ?唳??Ｘ???蝛拙?撟ΜI vs 撟?雿I
    smart_money_data = _fetch_smart_money_oi_split("BTC")
    stable_chg = smart_money_data.get("stable_chg")
    coin_chg = smart_money_data.get("coin_chg")
    smart_money = smart_money_data.get("smart_money")
    logger.info(f"[????] ?唳??㏕I??嚗帘摰{stable_chg} 撟?雿?{coin_chg} ?唳???{smart_money}")

    # ?啣?嚗??潸痕憍?+ BTC ETF瘚?+ Coinbase皞Ｗ
    fg_data = fetch_fear_greed_index()
    etf_data = fetch_btc_etf_flow()
    cb_data = fetch_coinbase_premium()
    logger.info(f"[????] ?鞎芸{fg_data.get('value')} ETF瘚?{etf_data.get('direction')} CB皞Ｗ={cb_data.get('premium')}")

    if not mcap_change:
        logger.warning("??????嚗瘜?敺??潭??頝喲??冽")
        return

    # ?? USDT 皞Ｗ??甇???祕鞎瑞嚗????祉?憟嚗?
    usdt_premium = _fetch_usdt_premium()

    mcap_1h = mcap_change.get("change_1h") or 0
    mcap_15m = mcap_change.get("change_15m") or mcap_1h
    oi_15m_chg = (oi_change_15m.get("change_1h") or 0)
    oi_1h_chg = (oi_change_1h.get("change_1h") or 0)

    # ?SDT 皞Ｗ>0.05%??閬?祕鞎瑞
    premium_boost = (usdt_premium is not None and usdt_premium > 0.05)
    if premium_boost:
        logger.info(f"[????] USDT 皞Ｗ {usdt_premium:+.4f}% > 0.05%嚗?甈???蝝?")

    # 蝛?嚗?蝝 7 ?遛嚗??亥?蝬剖漲嚗?
    fuel_score = _calc_fuel_score(mcap_15m, mcap_1h, oi_15m_chg, oi_1h_chg, usdt_premium, smart_money)
    # ??蝬剖漲嚗??潸痕憍?+ ETF瘚?+ Coinbase皞Ｗ嚗?+1???擃??10 ??
    fg_val = fg_data.get("value")
    if fg_val is not None:
        if fg_val >= 60:   # 鞎芸帚??
            fuel_score += 1
        elif fg_val <= 25: # 璆萄漲?=摨璈?
            fuel_score += 1  # 璆萄漲?銋??嚗?摨???
    if etf_data.get("direction") == "inflow":
        fuel_score += 1    # ETF璈?瘚=撘瑕?鞎瑞
    if cb_data.get("signal") == "bullish":
        fuel_score += 1    # Coinbase皞Ｗ=蝢?璈?鞎瑕
    fuel_bar = _make_fuel_bar(fuel_score)

    # ?寞?蝛?瘙箏?銝餅?蝐歹?7 ?嚗?
    if fuel_score >= 6:
        headline = "? 撘瑕????啣?"
        advice = "?唳???鞈?+瑽▼銝?蝣箄?嚗撣鞈??郊?亙嚗蜓?挾敺敺?冽迨韏瑞???"
        bar_label = "??皛輯?"
    elif fuel_score >= 5:
        headline = "?? ?怠??券?嚗?銝餃?嚗?" if smart_money else "?? ?怠??券? (???拙末)"
        advice = "撠平鞈?銝餃?撱箏?蝛拙?撟ΜI?游撐嚗?頝璈??孵?????" if smart_money else "鞈? + 瑽▼?嚗?隤踹停?航眺暺?"
        bar_label = "擃???"
    elif fuel_score >= 4:
        headline = "? 鞈??脣 (?曇疏鞎瑞)"
        advice = "?游?鞈?瘚嚗??典?擃???????"
        bar_label = "銝剔???"
    elif fuel_score >= 2:
        headline = "?∴? ???"
        advice = "憭?撠?嚗?敺?Ⅱ隤??箸???"
        bar_label = "雿???"
    elif oi_1h_chg > 1.5 and smart_money is False:
        headline = "?? ??瑽▼?? (擃郭??霅?"
        advice = "??撟?雿I瞈憓?撠???皜???"
        bar_label = "?梢??"
    elif oi_1h_chg > 1.5:
        headline = "?? 瑽▼? (擃郭??霅?"
        advice = "?芣?瑽▼?典?嚗?敹?????"
        bar_label = "?梢??"
    elif mcap_1h < -0.05:
        headline = "?? 鞈??賡霅血"
        advice = "鞈?甇??日嚗?敶?雓寞?嚗征?剛?Ⅳ??"
        bar_label = "?∠???"
    else:
        headline = "?∴? ???"
        advice = "憭?撠?嚗?敺?Ⅱ隤??箸???"
        bar_label = "雿???"

    lines = []
    lines.append("??*??撣???銵冽??")
    lines.append(f"?? {datetime.now(TAIPEI_TZ).strftime('%H:%M')} (?啁) | ??15M 擃??")
    lines.append("????????????????????")
    lines.append(f"*{headline}*")
    lines.append(f"??{_bar}` {fuel_score}/7 ({bar_label})")
    lines.append("")

    # USDT 皞Ｗ璅惜
    if premium_boost:
        lines.append(f"? *USDT ?祕鞎瑞蝣箄?* (`+{usdt_premium:.3f}%`皞Ｗ)")
    elif usdt_premium is not None and usdt_premium < -0.05:
        lines.append(f"?? USDT ? `{usdt_premium:+.3f}%`嚗?隡潭蝤??抬???撖西眺??")
    elif usdt_premium is not None:
        lines.append(f"? USDT 皞Ｗ{_premium:+.3f}%`嚗葉?改?")

    lines.append("")
    mcap_val = (mcap_change.get("latest_mcap") or 0) / 1_000_000_000
    mcap_emoji = "??" if mcap_1h > 0 else "??"
    lines.append("? *蝛拙?撟???游?鞈?嚗?")
    lines.append(f"??蝮賡?{_val:.2f}B`")
    lines.append(f"??1H {_emoji} `{mcap_1h:+.3f}%`")

    # ?唳???OI ???憛?
    lines.append("")
    lines.append("?? *?唳???OI ??*")
    if stable_chg is not None:
        _s_emoji = "?" if stable_chg > 0.1 else ("?" if stable_chg < -0.1 else "?")
        lines.append(f"??蝛拙?撟??霅?({_emoji} `{stable_chg:+.3f}%`")
    else:
        lines.append("??蝛拙?撟??霅?嚗?豢?銝?灼")
    if coin_chg is not None:
        _c_emoji = "?" if coin_chg > 0.1 else ("?" if coin_chg < -0.1 else "?")
        lines.append(f"??撟?雿?{_emoji} `{coin_chg:+.3f}%`")
    else:
        lines.append("??撟?雿?霅?嚗?豢?銝?灼")
    if smart_money is True:
        lines.append("??? *?唳??Ｖ蜓撠?嚗?瑽??瑟平鈭斗??迤?典遣??蝛拙?撟?撟?雿?")
    elif smart_money is False:
        lines.append("???? *??瑽▼銝餃?*嚗馳?砌?OI?游撐嚗?璈除瘞???瘜冽?皜?")
    else:
        lines.append("????憭征鞈?瘛瑕?嚗?＊?孵?")

    lines.append("")
    oi_val_1h = (oi_change_1h.get("latest_oi") or 0) / 1_000_000_000
    oi_val_15m = (oi_change_15m.get("latest_oi") or 0) / 1_000_000_000 if oi_change_15m else 0
    oi_emoji_15m = "?" if oi_15m_chg > 0 else "??"
    oi_emoji_1h = "?" if oi_1h_chg > 0 else "??"
    lines.append("? *?????游瑽▼嚗?")
    if oi_val_15m > 0:
        lines.append(f"??15m 敹怎{_val_15m:.2f}B` {oi_emoji_15m} `{oi_15m_chg:+.2f}%`")
    lines.append(f"??1H 頞典{_val_1h:.2f}B` {oi_emoji_1h} `{oi_1h_chg:+.2f}%`")

    # ?? 璈?鞈??憛?Fear&Greed + BTC ETF + Coinbase皞Ｗ嚗??????????
    lines.append("")
    lines.append("? *璈?鞈? & 撣??*")
    if fg_val is not None:
        lines.append(f"???鞎芸{_data.get('emoji', '??')} `{fg_val}` {fg_data.get('label','')}")
    if etf_data.get("label"):
        lines.append(f"??BTC ETF嚗{etf_data['label']}")
    if etf_data.get("total_assets_usd"):
        lines.append(f"??ETF蝮質??ｇ?`${etf_data['total_assets_usd']/1e9:.1f}B`")
    if cb_data.get("label"):
        lines.append(f"??{cb_data['label']}")
    if not any([fg_val, etf_data.get("label"), cb_data.get("label")]):
        lines.append("??璈????怎鞈?")

    lines.append("")
    lines.append("????????????????????")
    lines.append(f"? *?寥?誘*{advice}")

    msg = "\n".join(lines)
    keyboard = {"inline_keyboard": [[{"text": "? ?亦?鞈?瘚??”", "url": "https://www.coinglass.com/zh-TW/pro/futures/OpenInterest"}]]}
    send_telegram_message(msg, TG_THREAD_IDS.get("buying_power_monitor", 246), parse_mode="Markdown", reply_markup=keyboard)
    logger.info(f"???????冽摰?{_score}/5嚗?")


# 靽???詨?蝔曹誑???澆捆
def fetch_whale_position():
    """撌脣誥璉?隢蝙??buying_power_monitor()"""
    logger.warning("fetch_whale_position() 撌脣誥璉?隢蝙??buying_power_monitor()")
    buying_power_monitor()




# ==================== 3. ???祟?詨 ====================



# ?? 撟?車?漱????撠銵剁???CoinGlass supported-exchange-pairs 憛怠?嚗??????
# ?澆?嚗"BTC": {"Binance", "OKX", "Bybit", ...}, ...}
# 敹怠??芸遣蝡? get_major_exchanges_for_coin 靽??摰 pool
_cg_full_exchange_map: Dict[str, Set[str]] = {}


def get_major_exchanges_for_coin(base: str, pool: Optional[List[str]] = None) -> List[str]:
    """
    敺?_cg_full_exchange_map 敹怠??亥岷 pool ?批鈭之??舀閰脣馳蝔柴?    敹怠??芸遣蝡???靽??摰 pool嚗馳銝 map ??????    """
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
    """CoinGlass coins-price-change ?蝡舫?嚗oins-markets 憭望????剁?蝝?CoinGlass 璅∪?嚗?"""
    url = f"{CG_API_BASE}/api/futures/coins-price-change"
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.error(f"[?] coins-price-change HTTP {response.status_code}")
            return []
        result = response.json()
        data = result.get('data', result if isinstance(result, list) else [])
        if not data:
            logger.warning(f"[?] coins-price-change ?蝛箄???code={result.get('code')} msg={result.get('msg','')}")
            return []
        logger.info(f"[?] coins-price-change ?? {len(data)} ?馳蝔?")
        return data
    except Exception as e:
        logger.error(f"[?] coins-price-change 憭望?: {e}")
        return []


def _fetch_coinglass_24h_map() -> Dict[str, float]:
    """CoinGlass coins-price-change ??{clean_symbol: 24h_pct}嚗oins-markets 憭望???? 24h 瞍脰?撟?皞???"""
    if not CG_API_KEY:
        return {}
    try:
        r = requests.get(
            f"{CG_API_BASE}/api/futures/coins-price-change",
            headers={"CG-API-KEY": CG_API_KEY, "accept": "application/json"},
            timeout=10
        )
        if r.status_code != 200:
            logger.warning(f"[24h??] coins-price-change HTTP {r.status_code}")
            return {}
        j = r.json()
        if j.get("code") not in (0, "0", 200, "200", None):
            logger.warning(f"[24h??] coins-price-change code={j.get('code')} msg={j.get('msg','')}")
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
        logger.warning(f"[24h??] coins-price-change 憭望?: {e}")
        return {}


def fetch_bingx_futures_24h_vol() -> Dict[str, float]:
    """
    Plan B ?漱?澆??湛?BingX 瘞貊??? 24h quoteVolume嚗SDT嚗甈∪?敺?    ?桐? API call 瘨菔????BingX 銝?撟?車嚗??湔?垢暺? API Key??    ? {base_symbol: vol_usdt_24h}嚗?憒?{"BTC": 2.3e10, "ETH": 5e9}??    憭望???暺??喟征 dict嚗?敶梢銝餅?蝔?    """
    try:
        r = requests.get(
            "https://open-api.bingx.com/openApi/swap/v2/quote/ticker",
            timeout=10
        )
        if r.status_code != 200:
            logger.warning(f"[?B-BingX] HTTP {r.status_code}嚗歲??")
            return {}
        j = r.json()
        # BingX ??澆?嚗"code": 0, "data": [...]} ???list
        data = j.get("data") if isinstance(j, dict) else j
        if not isinstance(data, list):
            return {}
        result: Dict[str, float] = {}
        for item in data:
            sym = item.get("symbol", "")           # ?澆?嚗?BTC-USDT"
            if not sym.endswith("-USDT"):
                continue
            base = sym[:-5]                         # "BTC-USDT" ??"BTC"
            # ?? 1000xxx / 1000000xxx ?賢?嚗ingX ?典蝔梧?CoinGlass ?函葬撖恬?
            # 靘?BingX "1000PEPE" ??CoinGlass "1KPEPE"嚗?甇方?靽? BingX ??靘??改?
            try:
                vol = float(item.get("quoteVolume") or 0)
                if vol > 0:
                    result[base] = vol
                    # ??撱箇?蝮桀神?亙?嚗?000xxx ??1Kxxx嚗?000000xxx ??1Mxxx
                    if base.startswith("1000000"):
                        result.setdefault("1M" + base[7:], vol)
                    elif base.startswith("10000"):
                        result.setdefault("1W" + base[5:], vol)
                    elif base.startswith("1000"):
                        result.setdefault("1K" + base[4:], vol)
            except (TypeError, ValueError):
                pass
        logger.info(f"[?B-BingX? ?? {len(result)} 撟?車 24h USDT ?漱??")
        return result
    except Exception as e:
        logger.warning(f"[?B-BingX] 憭望?: {type(e).__name__}: {e}")
        return {}


def fetch_price_change_24h_coinglass_klines(symbol: str, preferred_symbol: Optional[str] = None) -> Optional[float]:
    """
    CoinGlass 鈭斗?撖遏蝥踹???24h 瞍脰?撟?    ?辣: https://docs.coinglass.com/v4.0-zh/reference/price-ohlc-history
    GET /api/futures/price/history嚗? 1h?25 ?對?擐 open???close 閮? 24h%??    ?菜平??冽迨?亙嚗oins-price-change ?航銝?剁?甇方?雿? fallback??    """
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
    """BingX 24h 瞍脰?撟???1h K 蝺? 24h ???方???唳?方?蝞?CoinGlass ?∟??? fallback嚗?"""
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




# OI 擐活憭望?????甈∴??踹?瘣?
_coinglass_oi_first_failure_logged = False
# 蝺????脫迫憭?蝔??忽????_oi_rate_limit_lock = threading.Lock()


def _parse_oi_change_from_data_list(data_list: list) -> Optional[float]:
    """敺?CoinGlass OI K 蝺?銵刻圾??15m 霈?%嚗???舀 v, c, close, oi嚗?"""
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
    # ???亙撣貉??? t, o, h, l, c嚗?萄?嚗?
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
    """? OI K蝺?潸圾???舀憭車甈??迂嚗?"""
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
    symbol: str, interval: str = "1h", return_ts: bool = False
) -> "Optional[float] | tuple":
    """
    閮??桐? symbol ???? OI 霈?%嚗??1h / 30m / 15m / 5m嚗?    return_ts=True ????(change_pct, candle_start_ts)嚗銝?    candle_start_ts ?箸?餈??孵???K蝺?韏瑕????喉?Unix 蝘???    """
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
                        if return_ts:
                            # ??餈歇?嗥 K蝺?韏瑕???嚗ata_list[-2]嚗?
                            try:
                                _sorted = sorted(
                                    data_list,
                                    key=lambda x: x.get("t") or x.get("time") or x.get("timestamp") or 0
                                )
                                _candle_ts = int(
                                    _sorted[-2].get("t") or _sorted[-2].get("time") or
                                    _sorted[-2].get("timestamp") or 0
                                )
                                # CoinGlass ?典??亙?瘥怎?嚗絞銝頧?
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
                    logger.warning(f"[CG??] {base_symbol} [{interval}] 隡 {sleep_for:.1f}s嚗?閰?{attempt+1}嚗?")
                    time.sleep(sleep_for)
                    backoff *= 2.0
                    continue
            elif response.status_code == 429:
                _cb_record_429()
                sleep_for = backoff + random.uniform(0, 1.0)
                logger.warning(f"[CG 429] {base_symbol} [{interval}] 隡 {sleep_for:.1f}s嚗?閰?{attempt+1}嚗?")
                time.sleep(sleep_for)
                backoff *= 2.0
                continue
        except Exception as e:
            logger.debug(f"OI 隢??啣虜 {base_symbol} [{interval}]: {e}")
            time.sleep(backoff)
            backoff *= 2.0
    return None


def _fetch_oi_multi_tf(symbol: str) -> Dict[str, Optional[float]]:
    """Enrichment 撠嚗? top ?撟?車鋆? CoinGlass 1H OI嚗蝞?1H/4H 霈?%??    ?芸 enrichment 撠?撟?車??恬?銝蔣?蹂蜓?????    ? {"1h": float|None, "4h": float|None}
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


# ?? 璅????5M ??望撽? ???????????????????????????????????????????????
_resonance_cache: Dict[str, Tuple[Optional[bool], float]] = {}
_RESONANCE_CACHE_TTL = 30.0  # 30 蝘翰??瘥霅?哨????祆?祈?嚗?


# ?? ????郎 ?????????????????????????????????????????????????????????????

_liq_heatmap_cache: Dict[str, Tuple[Optional[Dict], float]] = {}
_LIQ_HEATMAP_TTL = 120.0  # 2 ??敹怠?嚗???蝵桐??翰?宏??


def fetch_liq_heatmap_nearby(
    symbol: str,
    current_price: float,
    is_long: bool,
    proximity_pct: float = 3.0,
) -> Optional[Dict[str, Any]]:
    """??????閰ａ脣暺?餈???????
    撠?憭???撠???嫘???嚗?惜嚗?    撠?蝛箄???撠???嫘???嚗?惜嚗?
    ?嚗?        {"pct": float, "side": "憭??|"蝛箏??,
         "label": str, "usd": float}
        ??None嚗PI 憭望? / ???⊥?憿舐???嚗?    """
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
        """敺?API ??”銝剜?箸?餈?憭批?????蝵柴?"""
        best: Optional[Dict] = None
        best_pct = proximity_pct + 1

        for entry in (data_list or []):
            if not isinstance(entry, dict):
                continue
            # ?岫憭車甈???price / liqPrice / liquidationPrice / level / priceLevel
            p_raw = (
                entry.get("price") or entry.get("liqPrice") or
                entry.get("liquidationPrice") or entry.get("level") or
                entry.get("priceLevel")
            )
            # ??嚗ongLiqUsd / shortLiqUsd / value / amount
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

            # ??嚗?瘜具??嫘征?桃??????潘????桃????舀?鋡急?蝛選?
            # ?征嚗?瘜具??嫘??桃?????憯?潘??征?桃????餃?鋡怎??湛?
            total_usd = long_usd + short_usd
            if total_usd < 500_000:  # 雿 50 ??USD 敹賜
                continue

            if pct_dist < best_pct:
                best_pct = pct_dist
                # ?斗?芣???憭?
                if long_usd >= short_usd:
                    side_label = "憭??"
                    dominant_usd = long_usd
                else:
                    side_label = "蝛箏??"
                    dominant_usd = short_usd
                # 撠?憭縑??銝蝛箏?? = ?舀?嚗?蝛綽?嚗??孵??桃???= ?梢
                if is_long:
                    if liq_price < current_price and side_label == "蝛箏??":
                        interp = "? 銝???嚗?蝛箏???"
                    elif liq_price < current_price:
                        interp = "?? 銝憭??嚗迫?黎??"
                    else:
                        interp = "?妤 銝???"
                else:
                    if liq_price > current_price and side_label == "憭??":
                        interp = "? 銝???嚗?憭???"
                    elif liq_price > current_price:
                        interp = "?? 銝蝛箏??嚗迫?黎??"
                    else:
                        interp = "?妤 銝???"
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

    # ?? ?寞?A嚗馳蝔株????風?莎??蝎暹?嚗?撖衣??????????????????
    # aggregated-history ??餈?N ??K 蝺?憭征?蜇??
    logger.debug(f"[??A] {base} endpoint={CG_EP['liq_agg_history']}")
    try:
        j_a = _cg_get(CG_EP["liq_agg_history"],
                       {"symbol": base, "interval": "15m", "limit": 8})
        if j_a:
            rows_a = j_a.get("data") or j_a.get("list") or []
            if isinstance(rows_a, list) and rows_a:
                # ?暹?餈嗾?遏蝺葉??撖???澆???
                # 瘥??虜??longLiqUsd, shortLiqUsd, closePrice/price
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
                            f"[??A? {base}: ??甇瑕 {result['label']} "
                            f"頝?{result['pct']:.2f}% ${result['total_usd']/1e6:.2f}M"
                        )
    except Exception as e_a:
        logger.debug(f"[??A] {base} ??甇瑕?啣虜: {e_a}")

    # ?? ?寞?B嚗?????殷???啁?撖阡??嚗移皞?閮嚗??????
    if not result:
        logger.debug(f"[??B] {base} endpoint={CG_EP['liq_order']}")
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
                            logger.info(f"[??B? {base}: ?單?閮 {result['label']} 頝?{result['pct']:.2f}%")
        except Exception as e_b:
            logger.debug(f"[??B] {base} ?單?閮?啣虜: {e_b}")

    # ?? ?寞?C嚗??? model1/2/3 + ?????蝎暹??撟喳?????嚗??
    # ? CoinGlass ?撘瑞???皜祆芋??model1=靽?隡啁? model2=銝剜?model3=瞈??
    if not result:
        logger.debug(f"[??C] {base} ?岫??? aggregated-heatmap model1~3")
# ?芰????鈭斗?撠???⊥????芯???aggregated ?嚗?
        heatmap_eps = [
            (CG_EP["liq_agg_heatmap_m2"], "agg_m2"),  # ?? M2嚗葉?扳?皞???????
            (CG_EP["liq_agg_heatmap_m1"], "agg_m1"),  # ?? M1嚗?摰???????
                        (CG_EP["liq_agg_heatmap_m3"], "agg_m3"),  # ?? M3嚗??莎???????
            # liq_heatmap_m1/m2/m3 = 鈭斗?撠?嚗? ?⊥???撌脩宏??
        ]
        for ep_hm, src_label in heatmap_eps:
            try:
                j_hm = _cg_get(ep_hm, {"symbol": base, "exchange": "Binance",
                                         "interval": "8h"})
                if not j_hm:
                    continue
                raw_hm = j_hm.get("data") or j_hm.get("list") or []
                # ?勗??虜? price levels with ?摯??
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
                            logger.info(f"[??C? {base}: heatmap {src_label} "
                                        f"{result['label']} 頝?{result['pct']:.2f}%")
                            break
                elif isinstance(raw_hm, dict):
                    # ?典? model ? {longs: [...], shorts: [...]}
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
                            logger.info(f"[??C? {base}: heatmap(dict) {src_label} 頝?{result['pct']:.2f}%")
                            break
            except Exception as e_hm:
                logger.debug(f"[??C] {base} heatmap {src_label} ?啣虜: {e_hm}")
                continue

    # ?? ?寞?D嚗??摯蝞垢暺??敺??湛???????????????????????????????
    if not result:
        logger.debug(f"[??D] {base} ?岫?? estimated-levels 蝡舫?")
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
                logger.debug(f"[??D] {base} {endpoint_d} ?啣虜: {e_d}")
                continue

    if result:
        logger.info(
            f"[???-A? {base}: {result['label']} 頝 {result['pct']:.2f}% "
            f"閬芋 ${result['total_usd']/1e6:.2f}M"
        )
        _liq_heatmap_cache[cache_key] = (result, now)
        return result

    # ?? ?寞?B嚗??桃倏?勗???orderbook history = ?雿??桀?摨佗?隞????嚗??
    # ??嚗之???桅虜撠望?????嚗雿????誨??璅?
    logger.debug(f"[???-B] {base} ?岫 orderbook heatmap endpoint={CG_EP['ob_heatmap']}")
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
                            ob_side = "銝" if p_b > current_price else "銝"
                            ob_result = {
                                "pct": round(pct_b, 2),
                                "price": round(p_b, 6),
                                "side": "憭??" if p_b < current_price else "蝛箏??",
                                "usd": usd_b,
                                "total_usd": usd_b,
                                "label": f"? {ob_side}?撖??嚗誨????嚗?",
                                "source": "orderbook_proxy",
                            }
                    except (TypeError, ValueError, IndexError):
                        continue
            if ob_result:
                logger.info(
                    f"[???-B? {base}: 閮蝪蹂誨??{ob_result['label']} "
                    f"頝 {ob_result['pct']:.2f}% ? ${ob_result['total_usd']/1e6:.2f}M"
                )
                _liq_heatmap_cache[cache_key] = (ob_result, now)
                return ob_result
    except Exception as e_b:
        logger.debug(f"[???-B] {base} orderbook heatmap ?啣虜: {e_b}")

    logger.debug(f"[???-?典仃? {base}: ??垢暺??⊿?餈??????")
    _liq_heatmap_cache[cache_key] = (None, now)
    return None


# ??????????????????????????????????????????????????????????????????????????????
# 閮瘚???(Order Flow Analysis) ??銝餃?鞎瑁都?楊憭甇亙??雿?# ???? TP/SL 蝎暹????詨?嚗?其蝙??CoinGlass 璅???API
# ??????????????????????????????????????????????????????????????????????????????

_flow_cache: Dict[str, Tuple[Any, float]] = {}   # {cache_key: (data, ts)}
_FLOW_TTL = 90.0   # 90 蝘翰??閮瘚??圈??嚗?_FOOTPRINT_TTL = 120.0  # ?單郊?翰??2 ??


def _cg_interval(interval: str) -> str:
    """撠?皞??撘?? CoinGlass API 閬??撘?    ??蝣箄?嚗aker/net-pos/L-S ratio 蝑垢暺蝙??h1/m15 ?澆?嚗? 1h/15m??    OI history 蝑垢暺??亙? 15m嚗迨 helper 蝯曹????拚??澆???    """
    _map = {
        "1m": "m1",  "3m": "m3",  "5m": "m5",  "15m": "m15", "30m": "m30",
        "1h": "h1",  "2h": "h2",  "4h": "h4",  "6h": "h6",
        "8h": "h8",  "12h": "h12","1d": "d1",  "1w": "w1",
        # 撌脩??舀迤蝣箸撘?嚗?璅????
        "m1": "m1",  "m3": "m3",  "m5": "m5",  "m15": "m15", "m30": "m30",
        "h1": "h1",  "h2": "h2",  "h4": "h4",  "h6": "h6",
        "h8": "h8",  "h12": "h12","d1": "d1",  "w1": "w1",
    }
    return _map.get(interval, interval)


def _cg_get(path: str, params: Dict) -> Optional[Dict]:
    """頛? CoinGlass GET wrapper嚗葆????絞銝?航炊????"""
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
    """撠迫??皞??閰梯牧??蝪∠嚗??亥店嚗?"""
    # 靘??皞?銝??牧??
    if "footprint_history" in fp_data_source:
        src_note = "嚗甇亙?撖阡????漱撽?嚗?"
    elif "ob_depth_agg" in fp_data_source:
        src_note = "嚗蝬脰????桃倏?撖?雿?"
    elif "ob_depth_binance" in fp_data_source:
        src_note = "嚗inance 閮蝪踵??桀???嚗?"
    elif "taker_concentration" in fp_data_source:
        src_note = "嚗??蜓?眺?寞?鈭斗??葉????"
    elif "ob_depth" in fp_data_source:
        src_note = "嚗??桃倏?撖?雿摯蝞?"
    else:
        src_note = ""

    if "?單郊??" in sl_source or "閮蝪踵??" in sl_source:
        return f"??撖??眺?{_note}嚗??港誨銵刻眺?孵?ａ??"
    if "taker?舀?" in sl_source:
        return f"餈?銝餃?鞎瑕??{_note}嚗ㄐ?舐?撖行?鈭文耦???舀?"
    if "蝯?雿?" in sl_source or "蝯?擃?" in sl_source:
        return f"餈?撠?K蝺{'?雿?' if is_long else '?擃?'}暺??對??港??{'憭?' if is_long else '蝛?'}?寧?瑽援瞏?"
    if "K蝺?瑽?" in sl_source:
        return f"?嗅?15m K蝺{'雿?' if is_long else '擃?'}暺??K蝺?雿???"
    return "ATR??閮?嚗????撖行郭??摨西?身摰??刻???"


def tp_plain_desc(tp_label: str, is_long: bool, fp_data_source: str = "") -> str:
    """撠迫??蝐方??閰梯牧??蝪∠嚗??亥店嚗?"""
    if not tp_label:
        return """
    if "footprint_history" in fp_data_source:
        src_note = "嚗甇亙??漱撖??都?文???嚗?"
    elif "ob_depth_agg" in fp_data_source or "ob_depth_binance" in fp_data_source:
        src_note = "嚗??桃倏???鞈???葉?嚗?"
    elif "taker_concentration" in fp_data_source:
        src_note = "嚗蜓?都?箸??葉????"
    else:
        src_note = """

    if "?單郊???" in tp_label or "閮蝪輸??" in tp_label:
        return f"??撖??都?{_note}嚗?ㄐ鞈?憯?憭批?"
    if "taker?餃?" in tp_label:
        return f"餈?銝餃?鞈???{_note}嚗撣?祈??鞎典???"
    if "銝餃??" in tp_label:
        return "2h??VWAP??蝔曹?嚗蜓???砍?銝嚗虜?臬??Ｙ?箸?暺?"
    if "1.2R" in tp_label or "1.0R" in tp_label:
        r = tp_label.replace("R", "").strip()
        return f"{r}?璅??唾竟{r}?迫???Ｙ??拇膜"
    if "R" in tp_label:
        r_val = tp_label.replace("R", "").strip().split("(")[-1] if "(" in tp_label else tp_label.replace("R", "").strip()
        return f"{r_val}?◢?望??格?"
    return ""


# ?? 憭折??????????????????????????????????????????????????????????????????

_orderbook_wall_cache: Dict[str, Tuple[Optional[Dict], float]] = {}
_OB_WALL_TTL = 45.0       # 45 蝘翰???????敹恬?
_OB_WALL_MIN_USD = 800_000  # 80 ??USD 隞乩????楊????

def normalize_symbol(coin: Dict) -> Optional[str]:
    """敺馳蝔格?葉?? symbol"""
    return coin.get('symbol') or coin.get('pair') or coin.get('name') or coin.get('coin') or coin.get('symbolName')


def extract_price_change_30m(coin: Dict) -> float:
    """?? 15 ???寞霈?%嚗??祟??15m 擃???寞??OI ??15m嚗?"""
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
    """?? 24 撠??寞霈?%嚗?澆???/??剝?瞈橘?24h 憭扳撞銝?????4h 憭扯?銝??賊嚗?"""
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
    ? CoinGlass ?銵?璅?API嚗??ATR嚗像??撖行郭撟??OLL嚗??葆嚗?    - indicator_name: 'atr' | 'boll'
    - ?嚗tr ?箸??唬?蝑?ATR ?詨?(float)嚗oll ?箏???API ?? (dict嚗 data/list)??    """
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
    CoinGlass V4 攳券??甇瑕?豢???    GET /api/futures/whale-index/history?exchange=Binance&symbol=BTCUSDT&interval=1d
    ?摰 API ?? (??data/list)嚗仃????None??    """
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
    ??攳券??孵???嚗?閰?CoinGlass 攳券?? API嚗?豢???fallback 憭扳??蝛箸???    ??潛絞銝??0~100 璁艙嚗?50 ????50 ?征嚗?靘??Ｘ蕪蝬脖蝙?具?    ?辣: https://docs.coinglass.com/v4.0-zh/reference/斢賊掉?
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
        logger.info(f"攳券?? {base}: ?⊥??攳券?? API ??fallback 憭扳????嚗?")
        return None
    point = get_latest_data_point(fallback_data)
    if not point or not isinstance(point, dict):
        logger.info(f"攳券?? {base}: ?⊥??fallback 憭扳瘥??唬?蝑?")
        return None
    ratio = point.get("top_position_long_short_ratio")
    if ratio is None:
        logger.info(f"攳券?? {base}: ?⊥??fallback 憭扳瘥 top_position_long_short_ratio嚗?")
        return None
    try:
        r = float(ratio)
        if r <= 0:
            return None
        normalized = 50.0 * r
        logger.info(f"攳券?? {base}: 雿輻 fallback 憭扳?? ratio={r:.3f} ??璅???{normalized:.1f}")
        return normalized
    except (TypeError, ValueError):
        return None


def _fetch_bingx_funding_rate(symbol: str, preferred_symbol: Optional[str] = None) -> Optional[float]:
    """
    ?湔敺?BingX API ??閰脣馳蝔株??祥??喳 preferred_symbol嚗???contracts嚗??芸?雿輻??    """
    clean = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    try_symbols = [preferred_symbol] if preferred_symbol else []
    if preferred_symbol and "USDC" in preferred_symbol.upper():
        try_symbols.append(preferred_symbol.upper().replace("-USDC", "-USDT"))
    try_symbols += [f"{clean}-USDT", f"1000{clean}-USDT"]
    try_symbols = list(dict.fromkeys(try_symbols))  # ?駁?銝???摨?
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
    """敺?BingX swap ticker ???單???啣嚗摰寡??澆嚗??寞嚗?"""
    snap = _fetch_bingx_ticker_snapshot(symbol, preferred_symbol)
    return snap.get("price") if snap else None


def _fetch_bingx_ticker_snapshot(symbol: str, preferred_symbol: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    敺?BingX swap v2 ticker 銝甈∪?敺???啣 + 24h ?漱憿?USDT)??    ? {"price": float, "volume_usd": float or None}嚗仃????None??    """
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
            # ?交?鈭日???0 ?撩憭梧??典?憪?symbol ?岫銝甈∴??踹? 1000PEPE 蝑炊?歹?
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
    銝甈∪?敺?CoinGlass ?券?鞈?鞎餌?嚗???symbol(base) -> 鞎餌???    ?寞?A嚗I??鞎餌?甇瑕嚗?蝎暹?撣??嚗??蜓???穿?
    ?寞?B嚗xchange-list嚗??鞎餌?嚗? Binance ?芸?嚗?    """
    out: Dict[str, float] = {}
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}

    # ?? ?寞?A嚗I ??鞎餌?嚗蜓?遣???祆??亥???璅??????????????
    # ?冽??唬???K 蝺? OI ??鞎餌?雿??撖血??渲祥??
    logger.debug(f"[鞈?鞎餌?-A] ?岫 OI??鞎餌? endpoint={CG_EP['fr_oi_weight']}")
    try:
        _respect_coinglass_rate_limit()
        r_oi = requests.get(
            f"{CG_API_BASE}{CG_EP['fr_oi_weight']}",
            headers=headers,
            params={"symbol": "BTC", "interval": "8h", "limit": 1},  # ?桐??亥岷皜祈岫蝯?
            timeout=10
        )
        if r_oi.status_code == 200:
            j_oi = r_oi.json()
            if j_oi.get("code") in (0, "0", 200, "200", None):
                logger.debug(f"[鞈?鞎餌?-A] OI??鞎餌? API ?舐嚗?甇斤垢暺?桀馳蝔格閰ｇ?敺???隤輻")
    except Exception:
        pass

    # ?? ?寞?B嚗xchange-list嚗???臭?甈∪?敺??馳蝔殷??????????????
    # ?芸?雿輻??fetch_funding_fortune_list嚗祥??銵?嚗??函?? kebab-case 蝡舫?嚗歇撽???嚗?
    # camelCase ??曉???404嚗??箸活閬???
    fr_ep_candidates = [CG_EP["fr_exchange_list_old"], CG_EP["fr_exchange_list"]]
    lst = []
    url_used = ""
    for fr_ep_path in fr_ep_candidates:
        url = f"{CG_API_BASE}{fr_ep_path}"
        logger.debug(f"[鞈?鞎餌?-B] ?岫?券?鞎餌??” endpoint={fr_ep_path}")
        succeeded = False
        for attempt in range(2):
            try:
                _respect_coinglass_rate_limit()
                r = requests.get(url, headers=headers, timeout=12)
                if r.status_code == 429:
                    logger.warning("鞈?鞎餌? API 429 Too Many Requests嚗? 蝘??岫銝甈?")
                    time.sleep(2)
                    continue
                if r.status_code == 404:
                    logger.info(f"[鞈?鞎餌?-B] {fr_ep_path} 404嚗????渲楝敺?")
                    break  # ?岫銝???endpoint
                if r.status_code != 200:
                    logger.warning(f"鞈?鞎餌?-B status={r.status_code} body={r.text[:200]}")
                    break
                data = r.json()
                if data.get("code") not in (0, "0", 200, "200", None):
                    logger.warning(f"鞈?鞎餌?-B code={data.get('code')} msg={data.get('msg')}")
                    break
                candidate = data.get("data", [])
                if isinstance(candidate, list) and candidate:
                    lst = candidate
                    url_used = fr_ep_path
                    succeeded = True
                    logger.info(f"[鞈?鞎餌?-B? 雿輻 {fr_ep_path} ?? {len(lst)} 蝑祥????")
                    break
            except Exception as e:
                if attempt == 0:
                    logger.warning(f"鞈?鞎餌?-B 隢??啣虜: {e}嚗?閰虫?甈?")
                    time.sleep(1)
                else:
                    logger.warning(f"鞈?鞎餌?-B ?券憭望?: {e}")
        if succeeded:
            break
    if not lst:
        logger.warning(f"[鞈?鞎餌?-B? ?啗?頝臬??瘜?敺祥????餈?蝛箄”")

    # ?? 閫???摩嚗? fetch_funding_fortune_list 摰撠?嚗馳摰?stablecoin_margin_list ?芸?嚗??
    try:
        for coin_data in (lst if isinstance(lst, list) else []):
            if not isinstance(coin_data, dict):
                continue
            base = coin_data.get("symbol") or coin_data.get("coin") or coin_data.get("base")
            if not base:
                continue
            base = str(base).strip().upper()

            rate_found: Optional[float] = None
            # 鈭斗???芸???嚗inance > Bybit > OKX > BingX > Bitget嚗?憭扳??找蔔????
            _EXCHANGE_PRIORITY = ["Binance", "Bybit", "OKX", "BingX", "Bitget"]

            def _parse_rate_from_list(ex_list: list, priority: list) -> Optional[float]:
                """敺?exchange list 銝剜??芸????曄洵銝???祥??                CoinGlass exchange-list ??funding_rate ?舐???澆?
                嚗? 0.007343 = 0.007343%嚗??支誑 100 頧撠靘?蝥?蝞?                """
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

            # ?芸?嚗SDT 瘞貊?嚗tablecoin_margin_list嚗?
            stablecoin_list = coin_data.get("stablecoin_margin_list") or []
            rate_found = _parse_rate_from_list(stablecoin_list, _EXCHANGE_PRIORITY)

            # ?嚗馳?砌?瘞貊?嚗oken_margin_list嚗?
            if rate_found is None:
                token_list = coin_data.get("token_margin_list") or []
                rate_found = _parse_rate_from_list(token_list, _EXCHANGE_PRIORITY)

            if rate_found is not None:
                out[base] = rate_found

        if out:
            logger.info(f"[鞈?鞎餌?? ??閫?? {len(out)} 撟?車嚗oinGlass exchange-list嚗inance>Bybit>OKX>BingX>Bitget ?芸?嚗?")
        elif lst:
            _sample = list(lst[0].keys()) if lst and isinstance(lst[0], dict) else "n/a"
            logger.warning(f"[鞈?鞎餌???] 閫?? 0 蝑?擐?蝯? keys={_sample}")
    except Exception as e:
        logger.warning(f"鞈?鞎餌?閫???啣虜: {e}")
    return out


def fetch_oi_weighted_funding_rate(symbol: str, interval: str = "8h", limit: int = 3) -> Optional[float]:
    """???桐?撟?車??OI ??鞈?鞎餌?嚗??賢??蜓??獢踵??祉???嚗?    ?寞?A嚗r_oi_weight嚗I??嚗?    ?寞?B嚗r_vol_weight嚗?鈭日???嚗?    ?寞?C嚗r_history嚗?憪祥??Binance ?芸?嚗?    敹怠? 15 ??嚗祥??8 撠?蝯?銝甈∴??剜?霈?銝之嚗?    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    cache_key = f"fr_oi_w:{base}:{interval}"
    now = time.time()
    if cache_key in _flow_cache:
        val, ts = _flow_cache[cache_key]
        if now - ts < 900:  # 15 ??敹怠?
            return val

    logger.debug(f"[OI??鞎餌?] {base} endpoint={CG_EP['fr_oi_weight']} interval={interval}")
    for ep_key in ["fr_oi_weight", "fr_vol_weight", "fr_history"]:
        try:
            j = _cg_get(CG_EP[ep_key], {"symbol": base, "interval": interval, "limit": limit,
                                          "exchange": "Binance"})
            if not j:
                continue
            rows = j.get("data") or j.get("list") or []
            if not isinstance(rows, list) or not rows:
                logger.debug(f"[OI??鞎{_key}] {base}: 蝛箸??")
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
            logger.info(f"[OI??鞎餌?? {base} {ep_key}: {rate_f*100:.4f}%")
            _flow_cache[cache_key] = (rate_f, now)
            return rate_f
        except Exception as e:
            logger.debug(f"[OI??鞎{_key}] {base} ?啣虜: {e}")
            continue

    _flow_cache[cache_key] = (None, now)
    return None


def fetch_funding_rate_trend(symbol: str, interval: str = "8h", limit: int = 2) -> Tuple[Optional[float], Optional[float]]:
    """??鞈?鞎餌???銝?望???????(fr_trend = current - previous)??    ?亥祥????扳亙?銝?嚗r_trend < -0.02%嚗? OI 銝?嚗璅??箸??刻?蝛箄???    ?嚗?current_rate, fr_trend)嚗?豢?? (None, None)??    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    cache_key = f"fr_trend:{base}:{interval}"
    now = time.time()
    if cache_key in _flow_cache:
        val, ts = _flow_cache[cache_key]
        if now - ts < 900:  # 15 ??敹怠?
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
            logger.debug(f"[鞎餌???] {base} current={current_f*100:.4f}% prev={prev_f*100:.4f}% fr_trend={fr_trend*100:.4f}%")
            return out
        except Exception as e:
            logger.debug(f"[鞎{_key}] {base} ?啣虜: {e}")
            continue

    _flow_cache[cache_key] = (None, now)
    return (None, None)


def fetch_accumulated_funding_score(symbol: str) -> Dict[str, Any]:
    """蝝舐?鞈?鞎餌?璆萇垢?澆皜研?    ?券??斗撣?臬撌脯祥???晞??祥?扔摨西??潘??征瞏?嚗?    ?寞?A嚗ccumulated-exchange-list嚗敞蝛祥??7 ??30 ?伐?
    ?嚗
      "accumulated_7d": float,      # 7?亦敞蝛祥??      "accumulated_30d": float,     # 30?亦敞蝛祥??      "squeeze_risk": str,          # "long_squeeze" / "short_squeeze" / "neutral"
      "squeeze_label": str,         # ?冽??
    }
    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    cache_key = f"accum_fr:{base}"
    now = time.time()
    if cache_key in _flow_cache:
        val, ts = _flow_cache[cache_key]
        if now - ts < 1800:  # 30 ??敹怠?嚗敞蝛祥?楨?Ｚ???
            return val if val else {"accumulated_7d": None, "accumulated_30d": None,
                                    "squeeze_risk": "unknown", "squeeze_label": ""}

    empty = {"accumulated_7d": None, "accumulated_30d": None, "squeeze_risk": "unknown", "squeeze_label": ""}
    logger.debug(f"[蝝舐?鞎餌?] {base} endpoint={CG_EP['fr_accum_exchange']}")
    try:
        j = _cg_get(CG_EP["fr_accum_exchange"], {"symbol": base})
        if not j:
            _flow_cache[cache_key] = (empty, now)
            return empty
        data = j.get("data") or j.get("list") or []

        accum_7d = accum_30d = None
        # ?岫敺??銝剖? Binance ????
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

        # ?斗??憸券
        # 蝝舐?鞎餌? > 2% (7d) 隞?”憭??隞祥 = 頠征??嚗??剝??梧?蝛粹?征璈?憭改?
        # 蝝舐?鞎餌? < -1% (7d) 隞?”蝛粹??隞祥 = ?征??嚗征?剝??梧?憭?征璈?憭改?
        squeeze_risk = "neutral"
        squeeze_label = ""
        val_for_check = accum_7d
        if val_for_check is not None:
            if val_for_check > 0.02:   # > 2% 7d 蝝舐? = 憭璆萄漲?
                squeeze_risk = "long_squeeze"
                squeeze_label = f"?? 7?亦敞蝛祥??`{val_for_check*100:.2f}%`嚗??剛祥?券?擃??征憸券??"
                logger.info(f"[蝝舐?鞎餌???] {base}: 憭璆萄漲? 7d蝝舐?={val_for_check*100:.2f}%")
            elif val_for_check < -0.01:  # < -1% 7d 蝝舐? = 蝛粹璆萄漲?
                squeeze_risk = "short_squeeze"
                squeeze_label = f"? 7?亦敞蝛祥??`{val_for_check*100:.2f}%`嚗征?剛祥?券?擃??征瞏?撌典之嚗?"
                logger.info(f"[蝝舐?鞎餌??] {base}: 蝛粹璆萄漲? 7d蝝舐?={val_for_check*100:.2f}%")
            else:
                squeeze_label = f"? 7?亦敞蝛祥??`{val_for_check*100:.3f}%`嚗迤撣賂?"
        else:
            logger.debug(f"[蝝舐?鞎餌?] {base}: ?⊥?閫??蝝舐?鞎餌??豢? data={str(data)[:100]}")

        result = {"accumulated_7d": accum_7d, "accumulated_30d": accum_30d,
                  "squeeze_risk": squeeze_risk, "squeeze_label": squeeze_label}
        _flow_cache[cache_key] = (result, now)
        return result
    except Exception as e:
        logger.debug(f"[蝝舐?鞎餌?] {base} ?啣虜: {e}")
        _flow_cache[cache_key] = (empty, now)
        return empty


# ??????????????????????????????????????????????????????????????????????????????
# 撣摰???嚗ear&Greed?TC ETF?oinbase皞Ｗ??蝝撌柴?甈?憭抒?暺?
# ??????????????????????????????????????????????????????????????????????????????

def fetch_fear_greed_index() -> Dict[str, Any]:
    """?鞎芸帚?嚗???湔?蝺??雿喳銝??嚗?    endpoint: /api/index/fear-greed-history
    敹怠? 30 ??嚗??交?唬?甈∴??剜?敹怠??脤?銴?瘙???    ?: {"value": int, "label": str, "emoji": str, "signal": str}
    """
    cache_key = "fear_greed_index"
    now = time.time()
    if cache_key in _flow_cache:
        val, ts = _flow_cache[cache_key]
        if now - ts < 1800:
            return val if val else {}

    logger.debug(f"[?鞎芸帚] endpoint={CG_EP['fear_greed']}")
    empty = {"value": None, "label": "N/A", "emoji": "??", "signal": "neutral"}
    try:
        j = _cg_get(CG_EP["fear_greed"], {"limit": 1})
        if not j:
            _flow_cache[cache_key] = (empty, now)
            return empty
        data = j.get("data") or j.get("list") or j
        if isinstance(data, list) and data:
            data = data[-1]  # ??唬?蝑?
            if not isinstance(data, dict):
                _flow_cache[cache_key] = (empty, now)
                return empty

        val_raw = data.get("value") or data.get("score") or data.get("index")
        label_raw = data.get("value_classification") or data.get("label") or data.get("classification") or ""
        if val_raw is None:
            _flow_cache[cache_key] = (empty, now)
            return empty

        fg_val = int(float(val_raw))
        # 璅???蝐?
        if fg_val >= 80:
            emoji, label, signal = "?", "璆萄漲鞎芸帚", "overbought"
        elif fg_val >= 60:
            emoji, label, signal = "?", "鞎芸帚", "bullish"
        elif fg_val >= 40:
            emoji, label, signal = "?", "銝剜?", "neutral"
        elif fg_val >= 20:
            emoji, label, signal = "?", "?", "bearish"
        else:
            emoji, label, signal = "??", "璆萄漲?", "oversold"

        result = {"value": fg_val, "label": label_raw or label, "emoji": emoji,
                  "signal": signal, "score": fg_val}
        logger.info(f"[?鞎芸帚? ?{_val} {emoji} {label}")
        _flow_cache[cache_key] = (result, now)
        return result
    except Exception as e:
        logger.debug(f"[?鞎芸帚] ?啣虜: {e}")
        _flow_cache[cache_key] = (empty, now)
        return empty


def fetch_btc_etf_flow(limit: int = 3) -> Dict[str, Any]:
    """瘥撟ΒTF鞈?瘚?嚗?瑽??脣?湔??湔靽∟?嚗?    endpoint: /api/etf/bitcoin/flow-history
    ?寞?B: /api/etf/bitcoin/net-assets/history
    敹怠? 1 撠???    ?: {"net_flow_usd": float, "direction": "inflow"/"outflow"/"neutral",
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

        if net_f > 50_000_000:       # > 5? USD 瘛冽???
            direction = "inflow"
            label = f"? BTC ETF瘛冽???`${net_f/1e6:.0f}M`嚗?瑽?璆菔眺?伐?"
        elif net_f < -50_000_000:    # 5?隞乩?瘛冽???
            direction = "outflow"
            label = f"? BTC ETF瘛冽???`${abs(net_f)/1e6:.0f}M`嚗?瑽??郎??"
        else:
            direction = "neutral"
            label = f"? BTC ETF鞈?瘚?`${net_f/1e6:+.0f}M`嚗葉?改?" if net_f != 0 else ""

        result = {"net_flow_usd": net_f, "direction": direction, "label": label,
                  "total_assets_usd": total_a if total_a > 0 else None}
        logger.info(f"[BTC ETF? 瘛冽? ${net_f/1e6:+.0f}M 蝮質???${total_a/1e9:.1f}B")
        _flow_cache[cache_key] = (result, now)
        return result
    except Exception as e:
        logger.debug(f"[BTC ETF] ?啣虜: {e}")
        _flow_cache[cache_key] = (empty, now)
        return empty


def fetch_coinbase_premium() -> Dict[str, Any]:
    """Coinbase 皞Ｗ?嚗???瑽眺?文撥摨行??湔??嚗?    Coinbase 皞Ｗ > 0 = 蝢?鞈?甇?鞎瑕嚗?瑽?閮?嚗?    Coinbase 皞Ｗ < 0 = 蝢?鞈?甇?鞈?嚗?瑽?閮?嚗?    endpoint: /api/coinbase-premium-index
    敹怠? 15 ????    """
    cache_key = "coinbase_premium"
    now = time.time()
    if cache_key in _flow_cache:
        val, ts = _flow_cache[cache_key]
        if now - ts < 900:
            return val if val else {}

    logger.debug(f"[Coinbase皞Ｗ] endpoint={CG_EP['coinbase_premium']}")
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
            signal, label = "bullish", f"? Coinbase皞Ｗ `+{prem_f:.3f}%`嚗???瑽蜓?眺?伐?"
        elif prem_f < -0.1:
            signal, label = "bearish", f"? Coinbase? `{prem_f:.3f}%`嚗???瑽蜓?都?綽?"
        else:
            signal, label = "neutral", f"? Coinbase皞Ｗ `{prem_f:+.3f}%`嚗葉?改?"

        result = {"premium": prem_f, "label": label, "signal": signal}
        logger.info(f"[Coinbase皞Ｗ? {prem_f:+.3f}% {signal}")
        _flow_cache[cache_key] = (result, now)
        return result
    except Exception as e:
        logger.debug(f"[Coinbase皞Ｗ] ?啣虜: {e}")
        _flow_cache[cache_key] = (empty, now)
        return empty


def fetch_options_max_pain(symbol: str = "BTC") -> Dict[str, Any]:
    """???憭抒?暺?潘?撣?寞???葉敹??唳??亙?撣詨?甇豢迨?對???    ?寞?A: /api/option/max-pain
    ?寞?B: /api/option/info嚗???靽⊥銝剜???
    敹怠? 1 撠?嚗??交?唬?甈∴???    ?: {"max_pain_price": float, "expiry": str, "label": str, "distance_pct": float}
    """
    base = symbol.replace("USDT", "").upper()
    cache_key = f"opt_max_pain:{base}"
    now = time.time()
    if cache_key in _flow_cache:
        val, ts = _flow_cache[cache_key]
        if now - ts < 3600:
            return val if val else {}

    logger.debug(f"[?憭抒?暺 {base} endpoint={CG_EP['opt_max_pain']}")
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
            result["label"] = f"? ???憭抒?{_f:,.0f}` USD嚗? {expiry}嚗?"
            logger.info(f"[?憭抒?暺?] {base}: {mp_f:,.0f} ?唳?={expiry}")
            _flow_cache[cache_key] = (result, now)
            return result
    except Exception as e:
        logger.debug(f"[?憭抒?暺 {base} ?啣虜: {e}")
    _flow_cache[cache_key] = (empty, now)
    return empty


def fetch_contract_basis(symbol: str = "BTC", interval: str = "1h", limit: int = 4) -> Dict[str, Any]:
    """???箏榆嚗?鞎典??- ?曇疏?寞嚗?    甇?撌殷??疏皞Ｗ嚗? 撣????
    鞎撌殷??疏?嚗? 撣?征???鞎冽撘?    endpoint: /api/futures/basis/history
    敹怠? 15 ????    ?: {"basis_pct": float, "trend": "widening"/"narrowing"/"stable",
           "label": str, "signal": str}
    """
    base = symbol.replace("USDT", "").upper()
    cache_key = f"basis:{base}:{interval}"
    now = time.time()
    if cache_key in _flow_cache:
        val, ts = _flow_cache[cache_key]
        if now - ts < 900:
            return val if val else {}

    logger.debug(f"[???箏榆] {base} endpoint={CG_EP['contract_basis']}")
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

        # 閫???餈嗾??K 蝺??箏榆??
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
        # ?斗頞典
        if abs(latest_basis) > abs(prev_basis) * 1.1:
            trend = "widening"
        elif abs(latest_basis) < abs(prev_basis) * 0.9:
            trend = "narrowing"
        else:
            trend = "stable"

        # ??靽∟?
        if latest_basis > 0.005:     # > 0.5%
            signal = "bullish"
            label = f"?? ?疏皞Ｗ `+{latest_basis*100:.3f}%`嚗??渡?憭??疏>?曇疏嚗?"
        elif latest_basis < -0.003:  # < -0.3%
            signal = "bearish"
            label = f"?? ?疏? `-{abs(latest_basis)*100:.3f}%`嚗鞎冽撘瘀?鞈????曇疏嚗?"
        else:
            signal = "neutral"
            label = f"?堆? ?箏榆銝剜?`{latest_basis*100:+.3f}%`"

        if trend == "widening" and signal == "bullish":
            label += "嚗撌格憭改??????澈嚗?"
        elif trend == "narrowing" and signal == "bullish":
            label += "嚗撌格蝒?憭?望??澈嚗?"

        result = {"basis_pct": latest_basis * 100, "trend": trend, "label": label, "signal": signal}
        logger.info(f"[???箏榆? {base}: {latest_basis*100:+.4f}% trend={trend}")
        _flow_cache[cache_key] = (result, now)
        return result
    except Exception as e:
        logger.debug(f"[???箏榆] {base} ?啣虜: {e}")
        _flow_cache[cache_key] = (empty, now)
        return empty


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI(period)嚗? pandas 撖虫?嚗?撣貉?鈭斗??銝?氬?"""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _bbands(close: pd.Series, length: int = 20, std_dev: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """撣?撣塚?middle=SMA(close), upper=middle+std*std(close), lower=middle-std*std(close)????(upper, middle, lower)??"""
    middle = close.rolling(window=length).mean()
    std = close.rolling(window=length).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


def _calc_indicators_from_ohlcv(
    opens: list, highs: list, lows: list, closes: list, volumes: list,
    clean: str, source_label: str, real_symbol: str,
) -> Optional[Dict[str, Any]]:
    """?梁??閮??詨?嚗撓??OHLCV list嚗撓?箄? _fetch_bingx_klines_and_calc ?詨??澆???dict??    鋡?_fetch_cg_klines_and_calc ??_fetch_bingx_klines_and_calc ?梁嚗??銴?頛胯?    """
    if len(closes) < 20:
        logger.warning(f"[??閮?] {clean}: ?? K 蝺??{len(closes)} < 20嚗瘜?蝞?")
        return None

    # EMA20嚗????摨?嚗??MA20 ?萱蝯?雿?蝞?
    ema20_close = None
    ema20_series: list = []   # index 撠? closes[period:]
    period = 20
    alpha = 2.0 / (period + 1)
    ema = float(np.mean(closes[:period]))
    for i in range(period, len(closes)):
        ema = alpha * float(closes[i]) + (1.0 - alpha) * ema
        ema20_series.append(ema)
    ema20_close = ema
    # ???? closes 蝑???游?????period ?孵‵ None嚗?
    ema20_full = [None] * period + ema20_series

    # VWAP_2h嚗?餈?8 ??15m K 蝺???文?詨? VWAP ??皞榆嚗? TP2 頠??剁?
    vwap_2h = None
    vwap_std = None
    if len(closes) >= 8 and len(volumes) >= 8:
        uc, uh, ul, uv = closes[-8:], highs[-8:], lows[-8:], volumes[-8:]
        typical = [(uh[i] + ul[i] + uc[i]) / 3.0 for i in range(len(uc))]
        total_vol = sum(uv)
        if total_vol > 0:
            vwap_2h = sum(typical[i] * uv[i] for i in range(len(typical))) / total_vol
            logger.info(f"[??閮?] {clean}: VWAP_2h 雿輻?餈?8 ??K 蝺?鈭日??? (?詨???H+L+C/3)")
        else:
            vwap_2h = sum(typical) / len(typical)
            logger.info(f"[??閮?] {clean}: VWAP_2h ??volume嚗?函?甈???(TWAP 餈撮)")
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
        logger.warning(f"[??閮?] {clean}: RSI(14) 閮??⊥?")
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

    # MACD(12,26,9) ?賡??
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

    # ???菜葫
    vol_spike_ratio: Optional[float] = None
    if len(volumes) >= 10:
        sample = volumes[:-1][-min(96, len(volumes) - 1):]
        avg_vol = float(np.mean(sample)) if sample else 0.0
        if avg_vol > 0 and volumes[-1] > 0:
            vol_spike_ratio = volumes[-1] / avg_vol
            if vol_spike_ratio >= 1.5:
                logger.info(f"[??閮?] {clean}: ???? ???{volumes[-1]:.2f} ??{avg_vol:.2f} {vol_spike_ratio:.2f}?")

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

    # ?? EMA20 ?萱蝯?雿?擃?敺??憭? 30 ?對??暹?餈?甈?K 蝺?暺孛蝣?EMA20 ??蝵?
    # ???湧?霅? EMA20 摰???雿??? 瘥???EMA20-pad ?渡移皞? SL ?券?
    _scan_end = len(closes) - 1           # ?閮? K 蝺頨恬??敺??對?
    _scan_start = max(period, _scan_end - 30)
    ema20_touch_low = None   # 靘?憭?SL ??
    ema20_touch_high = None  # 靘?蝛?SL ??
    for _i in range(_scan_end - 1, _scan_start - 1, -1):
        _ev = ema20_full[_i]
        if _ev is None:
            continue
        _ev = float(_ev)
        # ???孵?嚗 K 蝺?暺閫貊１/?乩???EMA20嚗?撌?1.5%嚗?銝?文 EMA20 ??????
        if ema20_touch_low is None:
            if float(lows[_i]) <= _ev * 1.015:
                ema20_touch_low = float(lows[_i])
        # ?征?孵?嚗 K 蝺?暺閫貊１/?仿???EMA20
        if ema20_touch_high is None:
            if float(highs[_i]) >= _ev * 0.985:
                ema20_touch_high = float(highs[_i])
        if ema20_touch_low is not None and ema20_touch_high is not None:
            break
    if ema20_touch_low is not None:
        out["ema20_touch_low"] = ema20_touch_low
    if ema20_touch_high is not None:
        out["ema20_touch_high"] = ema20_touch_high

    # ?? Plan C嚗? K 蝺摯蝞?24h USD ?漱?潘?close ? volume ?蜇敺?瘥??其摯??24h嚗?
    # ?冽??CoinGlass ??Binance ???漱?潸?????敺蝺?
    try:
        n_candles = len(closes)
        if n_candles >= 2 and len(volumes) == n_candles:
            # 雿輻?餈?96 ??(=24h ?亦 15m K) ??函??K 蝺?
            n_use = min(n_candles, 96)
            kline_vol_usd = sum(
                float(closes[-(n_use - i)]) * float(volumes[-(n_use - i)])
                for i in range(n_use)
                if closes[-(n_use - i)] and volumes[-(n_use - i)]
            )
            # 憒? K 蝺??頞?96 ?對???靘??刻 24h
            if n_use < 96 and kline_vol_usd > 0:
                kline_vol_usd = kline_vol_usd * (96 / n_use)
            if kline_vol_usd > 0:
                out["kline_vol_usd_24h"] = kline_vol_usd
    except Exception:
        pass  # 隡啁?憭望?銝蔣?蹂蜓瘚?

    logger.info(
        f"[{source_label}??] {clean}: RSI={rsi_val:.2f} BB{_value} BB銝?{lb_value} "
        f"?{_price} ATR={atr_val} VWAP_2h={vwap_2h} EMA20={ema20_close} "
        f"EMA20?萱{_low} "
        f"2h擃?=({out.get('recent_high_2h')}, {out.get('recent_low_2h')}) "
        f"?便=({out.get('last_kline_open_30m')},{out.get('last_kline_high_30m')},"
        f"{out.get('last_kline_low_30m')},{out.get('last_kline_close_30m')})"
    )
    return out


def _parse_kline_rows(raw: list) -> Tuple[list, list, list, list, list]:
    """閫?? OHLCV K 蝺?銵剁??詨捆 dict ?澆???[ts,o,h,l,c,v] ?澆?嚗?"""
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
    ?湔??Binance ?疏?祇? K 蝺?API嚗? API Key嚗???撣?volume ????OHLCV??    閫?捱 CoinGlass price/history ?芸???OHLC ? volume 撠 VWAP ???????    """
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
            # Binance ?疏 K 蝺撘?
            # [ts, open, high, low, close, vol, close_ts, quote_vol, trades, taker_buy_base, taker_buy_quote, ignore]
            opens, highs, lows, closes, volumes = [], [], [], [], []
            for bar in raw:
                try:
                    opens.append(float(bar[1]))
                    highs.append(float(bar[2]))
                    lows.append(float(bar[3]))
                    closes.append(float(bar[4]))
                    volumes.append(float(bar[5]))  # base volume嚗馳?砌??漱??
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
                    f"[BinanceDirect? {clean}: {sym_pair} {interval} {len(closes)} ?對???volume嚗?"
                )
                return result
        except Exception as e:
            logger.debug(f"[BinanceDirect] {clean}/{sym_pair} ?啣虜: {e}")
    return None


def _try_bybit_futures_klines_direct(
    symbol_base: str, interval: str = "15m", limit: int = 60
) -> Optional[Dict[str, Any]]:
    """
    ?湔??Bybit V5 蝺扳偶蝥?K 蝺?API嚗? API Key嚗???撣?volume ????OHLCV??    閬? Bybit-only 撟?車嚗? XION, WHITEWHALE, PLAYSOUT 蝑???Binance 銝?撟????    Bybit interval ?澆?嚗?/3/5/15/30/60/120/240/D/W/M嚗??誑?詨?銵函內嚗?    瘜冽?嚗ybit list ?箸?啗?嚗???敺?蝞?璅?    """
    clean = symbol_base.replace("USDT", "").replace("-", "").replace("_", "").upper()
    # Binance "15m" ??Bybit "15"嚗?1h" ??"60"嚗?4h" ??"240"
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
            # Bybit ?澆?嚗timestamp, open, high, low, close, volume, turnover]嚗??嚗???
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
                    f"[BybitDirect? {clean}: {sym_pair} {interval} {len(closes)} ?對???volume嚗?"
                )
                return result
        except Exception as e:
            logger.debug(f"[BybitDirect] {clean}/{sym_pair} ?啣虜: {e}")
    return None


def _try_bingx_spot_klines_direct(
    symbol_base: str, interval: str = "15m", limit: int = 60
) -> Optional[Dict[str, Any]]:
    """
    BingX ?曇疏?祇? K 蝺??偷??嚗??箸?敺?fallback??    閬??芸 BingX 銝??隞之??芯??控撖典馳嚗?璅?葆 volume??    ?澆?嚗ts, open, high, low, close, volume, close_ts, quote_vol]
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
                f"[BingX-Spot? {clean}: {sym_pair} {interval} {len(closes)} ?對???volume嚗?"
            )
            return result
    except Exception as e:
        logger.debug(f"[BingX-Spot] {clean}/{sym_pair} ?啣虜: {e}")
    return None


def _fetch_cg_klines_and_calc(symbol: str, interval: str = "15m", limit: int = 60) -> Optional[Dict[str, Any]]:
    """
    K 蝺?撅日?蝝??伐??芸???volume ????皞?嚗?      1. Binance ?疏?湧????Key嚗? volume嚗? 閬? Binance 銝??馳蝔?      2. Bybit 瘞貊??湧????Key嚗? volume嚗? ??閬? Bybit-only 撟?車嚗? XION, WHITEWHALE嚗?      3. CoinGlass 隞?? OKX/BingX/Bitget     ????volume嚗?閬??拚??琿?撟?車
      4. BingX ?曇疏?湧????Key嚗? volume嚗? ???蝯?fallback
    """
    clean = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()

    # ?? Step 1: Binance ?疏?湧????芸?嚗? volume嚗???????????????????????
    _direct = _try_binance_futures_klines_direct(clean, interval, limit)
    if _direct:
        return _direct

    # ?? Step 2: Bybit 瘞貊??湧??閬? Bybit-only 撟?車嚗? volume嚗??????????
    _bybit = _try_bybit_futures_klines_direct(clean, interval, limit)
    if _bybit:
        return _bybit

    # ?? Step 3: CoinGlass 隞??嚗??擗馳蝔殷???volume嚗??????????????????
    # 靽? Bybit 雿 CoinGlass 隞???嚗甇?Bybit ?湧??曇???憭望????函鞈?
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
                    logger.warning(f"[CG K蝺 {clean} 429 ??嚗?怎?敺?")
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
                    f"[CG K蝺 {clean}: {exchange} {sym_pair} {interval} {len(raw)} ?對?"
                    f"??閮? RSI/BB/ATR/EMA/VWAP"
                )
                result = _calc_indicators_from_ohlcv(
                    opens, highs, lows, closes, volumes,
                    clean, f"CoinGlass/{exchange}", sym_pair,
                )
                if result:
                    result["source"] = "CoinGlass"
                    return result
            except Exception as e:
                logger.debug(f"[CG K蝺 {clean}/{exchange}/{sym_pair} ?啣虜: {e}")
                continue

    # ?? Step 4: BingX ?曇疏?湧???蝯?fallback嚗? volume嚗????????????????
    _bingx = _try_bingx_spot_klines_direct(clean, interval, limit)
    if _bingx:
        return _bingx

    logger.warning(
        f"[CG K蝺 {clean}: ???皞??⊥???頞喳? K 蝺?"
        f"嚗inance?湧?+ Bybit?湧?+ CoinGlass/{exchanges_to_try} + BingX?曇疏嚗?"
        f" ???航??Hyperliquid/Gate.io 撠惇撟?車"
    )
    return None


def calculate_technicals(
    symbol: str,
    bingx_symbol_override: Optional[str] = None,
    interval: str = "1h",
    limit: int = 48,
) -> Optional[Dict[str, Any]]:
    """
    ?銵?璅?蝞???1H K 蝺?身嚗?葉?郭畾萇??伐???    interval="1h" limit=48 ???? 2 憭?1H ?嚗?蝞?RSI/ATR/EMA20/VWAP 蝑葉??璅?    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    logger.info(f"[?銵?璅 {base}: {interval} K 蝺?蝞?銵?璅?")
    tech = _fetch_cg_klines_and_calc(symbol, interval=interval, limit=limit)
    if tech:
        return tech
    logger.warning(f"[?銵?璅 {base}: K 蝺仃???銵?璅瘜?蝞?")
    return None


# ??憛?+ 鈭??塚?zone ?箸?剖?憛?嚗tars 1=?撌?5=?雿?ZONE_DIP = "???"
ZONE_TOP = "?賊?"
ZONE_BREAKOUT_LONG = "蝒餈賣撞?"
ZONE_BREAKOUT_SHORT = "頝餈質??"

# 鞈?鞎餌??瑼?FUNDING_EXTREME = 0.0003
# 璆萇垢鞎餌? 0.03%嚗?潭?閮?
# ?? 鞈?鞎餌?憭征憯??蕪?瑼??????????????????????????????????????????????
# 鞎餌??箏??賂?0.001 = 0.1%嚗?# 蝛粹憯?嚗祥??鞎?嚗?蝛箄???閫貊霅行?/撠?
FR_SHORT_SQUEEZE_RISK  = 0.001   # -0.1%嚗征?剝?憪??????征閮???
FR_SHORT_SQUEEZE_BLOCK = 0.003   # -0.3%嚗征?剖?????征憸券擃????征閮?撠?
# 憭憯?嚗祥??甇??嚗?憭???閫貊霅行?/撠?
FR_LONG_LIQUIDATION_RISK  = 0.002  # +0.2%嚗??剝?憪???????閮???
FR_LONG_LIQUIDATION_BLOCK = 0.005  # +0.5%嚗??剖?????◢?芷? ????閮?撠?

# ??????????????????????????????????????????????????????????????????????
# 1H MTF ?惜瞍?蝑?瑼鳴??葉蝞∠?嚗矽???寥ㄐ嚗?# ??????????????????????????????????????????????????????????????????????
MAIN_COINS = {"BTC", "ETH"}

# ?? 瘚??折?瑼鳴?24h ?漱?潘?雿甇斗楛摨虫?頞喉???????????????????????????
MTF_VOLUME_MIN_USD  = 7_000_000     # 7M USD嚗?026-03-03 隤輸?嚗?瞈曆?瘚??批控撖剁?

# ?? 1H OI ?單??瑼鳴?Stage 1 銝餅?獢???????????????????????????????????
OI_THRESHOLD_1H    = 3.0            # 3.0%嚗?026-03-03 隤輸?嚗?雿????
PRICE_THRESHOLD_1H = 1.5            # 1H ?寞?單??瑼?
# ?? RSI ?/??餅嚗Ⅱ摰?蝣潸蕭擃?餈賭?靽風嚗???????????????????????
MTF_RSI_OVERBOUGHT = 85             # ????蝺?>85 ? Tier2 閫撖??桅???摰孵? RSI ??嚗?MTF_RSI_OVERSOLD   = 15             # ?征??蝺?<15 ? Tier2 閫撖??桅???摰孵? RSI ??嚗?
# ?? 銵?/???詨捆?亙?嚗??嗡??賣撘嚗??????????????????????????????
OI_MAIN_COIN_MIN    = OI_THRESHOLD_1H
OI_ALTCOIN_MIN      = OI_THRESHOLD_1H
OI_FOR_5_STAR       = OI_THRESHOLD_1H
OI_FOR_4_STAR       = OI_THRESHOLD_1H
OI_FOR_ELITE        = OI_THRESHOLD_1H
OI_THRESHOLD_30M    = OI_THRESHOLD_1H
PRICE_THRESHOLD_30M = PRICE_THRESHOLD_1H

# ?? 暺??殷?瘞訾?蝳迫?冽?????舫?憓?蝘駁嚗????????????????????????????????
# ??嚗風?脰”?曉榆???找?頞喋?◤??馳蝔?
SYMBOL_BLACKLIST: set = {
    # ?? 撌脩??撟???萵/瘚????⊥?蝢?璆萄?撣潘???
    "BULLA", "FIO", "ORBS", "NEIROCTO", "DENT",
    "RTX", "IKA", "POND", "1000NEIROCTO",
    "ULTIMA", "REAL", "CRCLX", "TFUEL",
    "WHITEWHALE", "PYR",
    "MANYU",      # 璆萄?撣?meme 撟???寞 ~7e-9 USD嚗鈭斗??儔
    "CITY",       # ?冽???暺???
    "REQ", "STEEM", "ROAM",  # ?冽???暺??殷?2026-03-02嚗?
    "CELR", "ATA", "ICX", "AGT", "ALU", "CAMP",  # ?冽???暺??殷?2026-03-02/03嚗?
    "BOBA", "AIO", "BTR",  # ?冽???暺??殷?2026-03-03嚗?
    "BSU", "AVL",  # ?冽???暺??殷?2026-03-04嚗?
    "MASTOCK",    # 隞?馳?蟡剁?OI ?豢??啣虜嚗閫貊 621% 璆萇垢?潘?
    "PLTRSTOCK",  # Palantir 隞?馳?蟡剁?STOCK 敺韌?澆?嚗?
    # ?? ?嗡???撖疏撟??鞎???
    "XTI",        # WTI ?硃?疏嚗TI/USD嚗?
    "XBR",        # Brent ?硃?疏
    "KO",         # Coca-Cola ?∠巨
    # ?? 隞?馳?蟡剁?Bybit/BingX/Bitget ??嚗???鞎典馳嚗??
    # Bybit 隞乩?撣?STOCK 敺韌?撘??塚???Ⅱ?
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
    "PLTR",                 # Palantir嚗ybit ??STOCK 敺韌?澆?嚗?    "AMD", "AMDX",          # Advanced Micro Devices
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
    # ?? ?喟絞???疏 ??
    "COPPER", "SILVER", "GOLD", "XAU", "XAG",
    "XBR", "OIL", "BRENT", "WTI", "USOIL",
    # ?? 瘜Ｗ?????/ ?喟絞? ??
    "VIX", "VIXINDEX",
    "DXY", "SPX", "NDX", "ES",
    "US2000", "US30", "US500", "NAS100",  # 蝢?
    # ?? 鈭散?∠巨??疏嚗ingX/Bitget ?漱????
    "HK50", "HKTECH",                     # ??? / ??蝘?
    "JP225", "NIKKEI", "NIKKEI225",       # ?亦??
    "CN50", "CHINA50", "CSI300",          # 銝剖?A50 / 皛祆楛300
    "AU200", "SG30",                      # 瞉單散/?啣??⊥???
    "UK100", "DE40", "FR40", "EU50",      # 甇散?
}


def _check_manipulation_risk(
    item: Dict,
    tech: Optional[Dict],
    atr_val: Optional[float],
    category: str = "",
) -> str:
    """
    ???脰風嚗nti-Manipulation Gate嚗?
    ?振?冽?嚗 $50?祉???琿?撅勗祠撟??銝?楊憭批?嚗? 15m OI ?游? 5%??潭???2%嚗?    璈鈭箄孛?潭?哨?銝???15m ?蝡?貊?箄疏嚗蕭?脰◤憟撅梢?嚗?蝔晞?????
    ????????????????????????????????????????????????????????????????????????    ?? 憿?撌桃嚗??菔身閮?                                                 ??    ?? long_open / short_open  = ?蝒??摰寞?鋡怎?嚗?璇辣?典??剁?     ??    ?? long_close / short_close = ??賊?摨?嚗之??閮??祈澈嚗?     ??    ??   ??璇辣1嚗??剖之撠???隞?嚗?H OI??嚗????儔嚗歲??       ??    ??   ??璇辣2嚗?撟?之OI嚗?憟嚗??曉潭撖祈銝蔣?踵迤撣豢??            ??    ????????????????????????????????????????????????????????????????????????
    ?撠???摮葡嚗?蝛?= 撠?嚗?蝛箏?銝?= ?曇???    """
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

    # ?閮?嚗??嚗?long_close = ??征?hort_close = 摨??
    is_reversal = category in ("long_close", "short_close")

    abs_oi = abs(oi30)

    # ?? 璇辣 1嚗孛?潸??剖祕擃?憭改??蝒???拍嚗????????????????????????
    # ?賊?摨頨怠停?臬憭扯??剖?曉??亙嚗之?賣?敺?蝛箝之?唳?敺?憭?嚗?
    # ?隞仿?歲?迨璇辣嚗撠?long_open / short_open 憟??
    if not is_reversal and tech and atr_val and atr_val > 0:
        _ko = tech.get("last_kline_open_30m")
        _kc = tech.get("last_kline_close_30m")
        if _ko and _kc:
            candle_body = abs(float(_kc) - float(_ko))
            body_atr = candle_body / atr_val
            if body_atr >= 2.5:
                return (
                    f"閫貊K撖阡? {body_atr:.1f}x ATR嚗?"
                    f"?格??賡?撌脰嚗?摰嗥?敺虜銝?喟??"
                )

    # ?? 璇辣 2嚗?瘚???+ 憭批? OI ?游?嚗???/ ????剁??曉潔???????
    # ????賊?摨??曉祝?曉潘?甇?虜?萱頦?頠征 ?典?撟???航??4~5% OI嚗?
    # ?芸???憿舐撣貊?璆萇垢??嚗ol < 2M + OI ??8%嚗?
    if is_reversal:
        if 0 < vol_m < 2.0 and abs_oi >= 8.0:
            return (
                f"璆菔?撟???I嚗?鈭{_m:.1f}M 雿?OI 瘜Ｗ? {abs_oi:.1f}%嚗?"
                f"?桐犖鞈??喳?賡萱頦?頠征閮?"
            )
    else:
        # ?蝒??頛?潘?撠?鞈??喳?賡???
        if 0 < vol_m < 3.0 and abs_oi >= 4.0:
            return (
                f"?馳憭別I嚗?鈭{_m:.1f}M 雿?OI ?游? {abs_oi:.1f}%嚗?"
                f"撠?鞈??喳?賡迨蝒閮?"
            )
        if 0 < vol_m < 5.0 and abs_oi >= 7.0:
            return (
                f"雿??批??郭???{_m:.1f}M & OI {abs_oi:.1f}%嚗?"
                f"?恍?憸券璆菟?"
            )

    # ?? 璇辣 3嚗?H OI ?? + 雿??改??蝒???拍嚗??????????????????
    # ??賊?摨??靘停?? 15m ??1H OI ?孵?銝??湛??閮??祈澈??頛荔?嚗?
    # 憟甇斗?隞嗆??航炊?啣??嗾銋????嚗?甇日?歲??
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
                f"1H OI??{oi_1h_pct:+.2f}%嚗? ?漱?澆? {vol_m:.1f}M嚗?"
                f"15m 摮斤??萵憳?擃?1H 憭折望??芰Ⅱ隤?"
            )

    return ""  # ?曇?


def _classify_mtf_signal(item: Dict) -> Optional[Dict[str, Any]]:
    """
    MTF ?惜閮????剁??湔??v3 ??撖抒撩?踵翰嚗?
    ?情??蝣潛???蝢抬?
      ? long_open:   OI??+ Price????憭撱箏?      ? short_open:  OI??+ Price????蝛箸撱箏?      ? long_close:  OI??+ Price????憭撟喳?      ? short_cover: OI??+ Price????蝛箸??

    ???芸?閮勗蝔格?哨??園?銝敺?return None嚗?霅瑕董?嗅??歹?嚗?      ??A. confirmed (蝣箏?蝐Ⅳ)嚗?H/30m/15m/5m ?惜?孵?摰銝??                                   + RSI ?芷?餈賡?/餈賜征璆萇垢嚗?憭75嚗?蝛算25嚗?      ? B. pullback  (摰??萱)嚗?H/30m ?? + 15m/5m ??剔???撟喳???                                   嚗之頞典?矽雿暺?

    ??隞乩???銝敺?return None嚗?      - Step 2 銵?嚗?0m 銝餃??孵???1H ?詨?
      - RSI ?/?嚗Ⅱ摰?蝣潭?隞嗅歇?? RSI 璆萇垢
      - ???嚗?H ?急挾?孵噩嚗???頛臭?憸券擃??嚗?      - 撘勗?荔??芣? 1H ??嚗隞???孵???
    """
    cat_1h = item.get("category") or ""
    # 璅???1H ???Ｙ???"short_close" ???_get_cat() ??"short_cover" ?臬?銝蝐Ⅳ???
    # 嚗I??+ Price??= 蝛箸??嚗?蝯曹?頧 short_cover 靘?蝥??撠?
    if cat_1h == "short_close":
        cat_1h = "short_cover"
    oi_1h  = item.get("oiChange1h") or item.get("oiChange30m") or 0
    p_1h   = item.get("priceChange1h") or 0
    oi_30m = item.get("oiChange_30m")
    p_30m  = item.get("priceChange30m")
    oi_15m = item.get("oiChange_15m")
    oi_5m  = item.get("oiChange_5m")
    rsi    = item.get("rsi")

    # ?? ?情??憿???????????????????????????????????????????????????????????
    def _get_cat(oi_val: Optional[float], price_val: Optional[float]) -> Optional[str]:
        """?寞? OI ?孵? + ?寞?孵?瘙箏?蝐Ⅳ?情????"""
        if oi_val is None:
            return None
        if oi_val > 0:
            return "long_open" if (price_val is None or price_val >= 0) else "short_open"
        else:
            return "short_cover" if (price_val is None or price_val > 0) else "long_close"

    # 30m嚗? priceChange30m ???豢?
    cat_30m = _get_cat(oi_30m, p_30m)
    # 15m/5m嚗?函? price嚗誑 1H price ?孵?雿餈撮嚗?頞典?其摯嚗?
    cat_15m = _get_cat(oi_15m, p_1h)
    cat_5m  = _get_cat(oi_5m,  p_1h)

    # ?? Step 2 銵??文? ????????????????????????????????????????????????????????
    is_1h_bull = cat_1h in ("long_open", "short_cover")
    is_1h_bear = cat_1h in ("short_open", "long_close")
    step2_conflict = (
        (is_1h_bull and cat_30m == "short_open") or
        (is_1h_bear and cat_30m == "long_open")
    )

    # ?? RSI 璆萇垢?文? ??????????????????????????????????????????????????????????
    rsi_f          = float(rsi) if rsi is not None and isinstance(rsi, (int, float)) else None
    rsi_overbought = rsi_f is not None and rsi_f > MTF_RSI_OVERBOUGHT
    rsi_oversold   = rsi_f is not None and rsi_f < MTF_RSI_OVERSOLD

    # ?? 憿舐內璅惜摰儔 ??????????????????????????????????????????????????????????
    _cat_emoji = {
        "long_open": "?", "short_open": "?",
        "long_close": "?", "short_cover": "?", None: "??",
    }
    _cat_name = {
        "long_open": "憭撱箏?", "short_open": "蝛箸撱箏?",
        "long_close": "憭撟喳?", "short_cover": "蝛箸??", None: "?⊥??",
    }
    oi_1h_s  = f"{oi_1h:+.1f}%"
    oi_30m_s = f"{oi_30m:+.1f}%" if oi_30m is not None else "??"
    oi_15m_s = f"{oi_15m:+.1f}%" if oi_15m is not None else "??"
    oi_5m_s  = f"{oi_5m:+.1f}%"  if oi_5m  is not None else "??"
    rsi_tag  = f", RSI: {rsi_f:.0f}" if rsi_f is not None else ""
    warn_30m = " ??銵?" if step2_conflict else ""

    # 靘??舫＊蝷箇? MTF 瞍???嚗?撅支?銵?
    mtf_funnel = (
        f"1H{_cat_emoji.get(cat_1h,'??')} {_cat_name.get(cat_1h,'??')} "
        f"(OI {oi_1h_s}{rsi_tag})\n"
        f"30m{_cat_emoji.get(cat_30m,'??')} {_cat_name.get(cat_30m,'??')}{warn_30m}\n"
        f"15m{_cat_emoji.get(cat_15m,'??')} {_cat_name.get(cat_15m,'??')}\n"
        f"5m{_cat_emoji.get(cat_5m,'??')} {_cat_name.get(cat_5m,'??')}"
    )
    mtf_oi_line = (
        f"? OI: 1H`{oi_1h_s}` 30m`{oi_30m_s}` 15m`{oi_15m_s}` 5m`{oi_5m_s}`"
    )
    base = {
        "mtf_desc": mtf_funnel, "mtf_oi_line": mtf_oi_line,
        "cat_30m": cat_30m, "cat_15m": cat_15m, "cat_5m": cat_5m,
        "step2_conflict": step2_conflict,
    }

    # ??????????????????????????????????????????????????????????
    # 銝惜瘙箇?璅?v4嚗銝? + 5m ??摰孵? + Tier2 閫撖??殷?
    #
    # ??A. confirmed嚗Ⅱ摰?蝣潘?嚗?H = 30m = 15m 銝惜摰銝?湛?5m ?迂??
    #
    # RSI 璆萇垢嚗?85 / <15嚗? ? Tier2嚗??湔銝?
    # ? B. pullback嚗?蝢?頦抬?嚗?H/30m ?? + 15m/5m ?蝺???
    # ?? C. tier2嚗?撖??殷?嚗?H/30m ????雿?15m ????RSI 璆萇垢
    #
    # ?冽????儭?閫撖??柴?蝐歹???頛?
    # ??D. ?園?嚗tep2 銵? / 1H&30m 憭扳???函????return None 銝?
    # ??????????????????????????????????????????????????????????

    # Step 2 銵? ???湔銝?Lazy Fetching 憭惜撌脫????迎?甇斤蝚砌??蝺?
    if step2_conflict:
        return None

    # ???????????? ?蔭嚗??摰??????????????????????????????????????
    is_30m_bull = cat_30m in ("long_open", "short_cover")
    is_30m_bear = cat_30m in ("short_open", "long_close")

    # ?? A. 蝣箏?蝐Ⅳ嚗銝?嚗?1H = 30m = 15m 蝎曄Ⅱ銝?湛?5m 銝撥????????????
    iron_triangle = (
        cat_1h is not None
        and cat_1h == cat_30m
        and (cat_15m == cat_1h or cat_15m is None)
    )
    if iron_triangle:
        if (is_1h_bull and rsi_overbought) or (is_1h_bear and rsi_oversold):
            # RSI 璆萇垢嚗隅?Ｖ??Ⅱ嚗?餈賡?/餈賜征憸券擃???????Tier2 閫撖?
            return {**base, "version": "tier2", "subtype": "RSI璆萇垢",
                    "aligned_count": 3,
                    "reversal_hint": f"?? RSI={rsi_f:.0f} 撌脤?璆萇垢嚗銝???雿遣霅啗???撖?"}
        return {**base, "version": "confirmed", "subtype": "",
                "aligned_count": 3, "reversal_hint": ""}

    # ?? B. 摰??萱嚗ullback嚗?1H/30m ?? + 15m/5m ?剔??? ????????????
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

# ?? C. 甈∠?閮?嚗ier2嚗?1H/30m 憭扳?????嚗? 15m ?? ????????????
# 瘥?confirmed/pullback 撘梧?雿???批????閫撖??格??
    big_picture_aligned = (
        (is_1h_bull and is_30m_bull) or
        (is_1h_bear and is_30m_bear)
    )
    if big_picture_aligned:
        _t2_dir  = "憭?" if is_1h_bull else "蝛?"
        _t2_hint = (
            f"1H/30m ?{_t2_dir}?孵?嚗?"
            f"{_name.get(cat_15m,'N/A')} / 5m={_cat_name.get(cat_5m,'N/A')} 撠蝣箄?"
        )
        return {**base, "version": "tier2", "subtype": "撘勗??",
                "aligned_count": 2,
                "reversal_hint": f"?? {_t2_hint}嚗遣霅啁?敺?15m ?孵?蝣箄??脣"}

    # ?? D. ?嗡?嚗之?孵??嚗? 銝? ????????????????????????????????????????
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
    ?情????憿?1H MTF ?惜瞍?蝑嚗?H ?單? + 24H 頞典瞈曄雯嚗?
    ?單?璇辣嚗?H嚗?
      |OI 1H| >= 1.5%  銝? |Price 1H| >= 1.5%

    # 頞典瞈曄雯嚗?4H嚗?
      憭閮?嚗ong_open / short_close嚗?price_24h > 0 隞?”憭扳??憸?      蝛粹閮?嚗hort_open / long_close嚗?price_24h < 0 隞?”憭扳??憸?      ?? ?◢嚗銵???閮?銝剜?瘜Ｘ挾?迂?雿?嚗?    """
    oi = item.get("oiChange1h") or item.get("oiChange30m") or 0
    price_chg_1h_main = item.get("priceChange1h") or item.get("priceChange30m")
    if price_chg_1h_main is not None and not isinstance(price_chg_1h_main, (int, float)):
        price_chg_1h_main = None

    # ?單?璇辣嚗?H OI 蝯???>= ?瑼鳴?蝝?OI 撽?嚗??典?潛?嚗?
    if abs(oi) < OI_THRESHOLD_1H:
        return None

    # 1H 頞典瞈曄雯嚗??剛??? 1h > 0嚗征?剛??? 1h < 0
    is_bull_signal = category in ("long_open", "short_close")
    is_bear_signal = category in ("short_open", "long_close")

    # 24H 憭扯隅?Ｘ蕪蝬莎?1H ?澆?隞?24H ?箝之?孵???琿??◢嚗?
    mtf_trend_ok = True
    mtf_note = ""
    _p24h = price_chg_24h
    if _p24h is not None and isinstance(_p24h, (int, float)):
        if is_bull_signal and _p24h <= -5.0:
            mtf_trend_ok = False
            mtf_note = f" ??24H銝?頞{_p24h:+.1f}%)"
        elif is_bear_signal and _p24h >= 5.0:
            mtf_trend_ok = False
            mtf_note = f" ??24H銝撞頞{_p24h:+.1f}%)"
        else:
            mtf_note = f" 24H{_p24h:+.1f}%"
    else:
        mtf_note = ""

    # ?? 4H OI 蝝舐???嚗郭畾菜畾菔郎蝷綽???????????????????????????????????????
    oi_4h_pct = item.get("oi_change_4h_pct")
    _oi_mtf_note = ""
    oi_1h_confirmed = True   # 1H 撌脫銝餅?獢??身蝣箄?
    if isinstance(oi_4h_pct, (int, float)):
        _abs_4h = abs(oi_4h_pct)
        if _abs_4h >= 5.0:
            _oi_mtf_note = f" ??4H OI{_abs_4h:.1f}%嚗郭畾菜畾蛛?蝮桃璅?"
        elif _abs_4h >= 2.5:
            _oi_mtf_note = f" ??4H OI{_abs_4h:.1f}%嚗葉畾蛛?雓寞?嚗?"
        else:
            _oi_mtf_note = " ?4H OI????瘜Ｘ挾??嚗征?雲嚗?"

    # RSI ?膩
    rsi = tech.get("rsi") if tech else None
    if rsi is not None:
        if rsi > 70:
            rsi_desc = f"RSI {rsi:.0f}(頞眺)"
        elif rsi < 30:
            rsi_desc = f"RSI {rsi:.0f}(頞都)"
        else:
            rsi_desc = f"RSI {rsi:.0f}"
    else:
        rsi_desc = "RSI ??"

    # 鞈?鞎餌?璅酉
    fr_note = ""
    if funding_rate is not None and isinstance(funding_rate, (int, float)):
        if funding_rate > FUNDING_EXTREME:
            fr_note = " ?質祥??甇?"
        elif funding_rate < -FUNDING_EXTREME:
            fr_note = " ?鞎餌???"

    # ?情??憿?靘?category 瘙箏?閮?璅惜??zone嚗?
    _counter_hint = ""
    if not mtf_trend_ok:
        if category in ("long_open", "short_close"):
            _counter_hint = "嚗?4H???嚗?撌血雿?嚗?批?嚗?"
        elif category in ("short_open", "long_close"):
            _counter_hint = "嚗?4H??征嚗?撌血雿?嚗?批?嚗?"

    if category == "long_open":
        label = "?? 憭?亙"
        zone = ZONE_BREAKOUT_LONG
        _trend = "?撌血雿?" if not mtf_trend_ok else "??喳餈賢?"
        reason = f"1H OI??Price??銝餃?蝛扔撱{_note}"
    elif category == "short_open":
        label = "? 蝛粹?亙"
        zone = ZONE_BREAKOUT_SHORT
        _trend = "?撌血雿?" if not mtf_trend_ok else "??喳餈賜征"
        reason = f"1H OI??Price??蝛粹蝛扔撱{_note}"
    elif category == "long_close":
        label = "? 憭撟喳?"
        zone = ZONE_DIP
        _trend = "??詨?璈?" if not mtf_trend_ok else "1H銝???"
        reason = f"1H OI??Price??憭?琿?箏嚗征?孵像??蝛箸?蝑?敶?{_note}"
    elif category == "short_close":
        label = "? 蝛粹撟喳?"
        zone = ZONE_TOP
        _trend = "??賊?璈?" if not mtf_trend_ok else "1H銝???"
        reason = f"1H OI??Price??蝛粹?剛?蝛箏?鋆?憭撟喳?蝛箸?餈{_note}"
    else:
        return None

    # oi_1h_confirmed 摮? item 靘?build_report_message_tiered ???? ???摩雿輻
    item["_oi_1h_confirmed"] = oi_1h_confirmed

    return (label, zone, 5, rsi_desc, reason)


def _calc_signal_grade(x: dict, is_bull_sig: bool) -> tuple:
    """
    閮?閮?蝬?閰?嚗 / A / B / R嚗?    餈? (grade_str, score_int, brief_reason_str, already_moving_bool, motion_note_str)

    ?? R 蝝??芸??斗嚗??????????????????????????????????????????????????
      R = ?撌血?餅?剜摨?          ??雿?孵 4H EMA20 銝 ???詨?
          ?征雿?孵 4H EMA20 銝 ???賊

    ?? ?閮?閰?嚗遛??100嚗?????????????????????????????????????????
      1. 閮??撘瑕漲    (max 40) ?? confirmed=40 / tier2=20 / potential=10
      2. MTF 憭??嗅?朣? (max 25) ?? 4獢?25 / 3獢?18 / 2獢?10 / 1獢?3
      3. 4H 摰?憭拙?    (max 15) ?? ?+15 / ?芰 0
      4. RSI ?銵?       (??~+10) ?? ????10 / 銝剜?5 / ?梢 ??
      5. 1H OI 撘瑕漲      (max 10) ?? >8%=10 / 5-8%=7 / 3-5%=5 / <3%=2
      6. 頞典??        (??5~+15) ?? 銝?畾萄?憭?銝撞畾萄?蝛?+15嚗?瞍脫挾??/銝?畾萄?蝛???0
      7. 頝券??乩?蝣箄?    (max 20) ?? ?憚?箇鈭?閮?嚗?蝛?憭? / ?箄疏+蝛粹?嚗???      8. 鞈?鞎餌??◢    (max 10) ?? 鞎餌?????+5/+10嚗祥??甇??蝛?5/+10

    ?? 頠歇?澆??菜葫嚗?H ?寞撌脣之瞍?憭扯?嚗???????????????????????????????
      ?? + 1H 瞍脣? > +5%嚗???銵???擃?A 蝝??冽?郎蝷?      ?征 + 1H 頝? < ??%嚗???銵???擃?A 蝝??冽?郎蝷?
    ?? 蝑??瑼鳴??閮?嚗?????????????????????????????????????????????
      S ??80  閮?璆萄撥?颱?撅文?荔???摰?嚗?蝣箄?
      A ??60  閮?撘瑯銝餉?璇辣撠?
      B < 60  閮?銝剔??餅??蝡?雓寞???
    """
    # ??????????????????????????????????????????????????????????????
    # 蝚砌?甇伐??斗?撌血 ??R 蝝?
    # ??????????????????????????????????????????????????????????????
    is_above_4h = x.get("is_above_4h_ema")
    _is_counter_trend = (
        (is_above_4h is True  and not is_bull_sig) or
        (is_above_4h is False and     is_bull_sig)
    )
    if _is_counter_trend:
        _dir_label = "?賊?駁?征" if not is_bull_sig else "?詨??駁??"
        brief = f"??*R 蝝? ?{_dir_label}嚗?批?嚗?"
        return "R", 0, brief, False, ""

    # ??????????????????????????????????????????????????????????????
    # 蝚砌?甇伐?頠歇?澆??菜葫嚗??歇??嚗?
    # ?摩嚗?憭???曆? 1H 撌脫撞 >5% ??餈賡?憸券憭改???擃?A 蝝?    #        ?征閮??箇雿?1H 撌脰? >5% ??餈賭?憸券憭改???擃?A 蝝?    # ??????????????????????????????????????????????????????????????
    _price_1h = x.get("priceChange1h") or 0
    _price_30m = x.get("priceChange30m") or x.get("priceChange_30m") or 0
    _already_moving = False
    _motion_note = ""
    try:
        _p1h = float(_price_1h)
        _p30m = float(_price_30m)
        if is_bull_sig and _p1h > 5.0:
            _already_moving = True
            _motion_note = f"?? 頠歇?澆?嚗?H 撌脫撞 {_p1h:+.1f}%嚗釣?蕭擃◢?迎?銵??航?脣?急挾"
        elif is_bull_sig and _p30m > 3.0:
            _already_moving = True
            _motion_note = f"?? 頠??澆?嚗?0m 銝撞 {_p30m:+.1f}%嚗遣霅啁??葫??"
        elif not is_bull_sig and _p1h < -5.0:
            _already_moving = True
            _motion_note = f"?? 頠歇?澆?嚗?H 撌脰? {_p1h:+.1f}%嚗釣?蕭蝛粹◢?迎??征憸券??"
        elif not is_bull_sig and _p30m < -3.0:
            _already_moving = True
            _motion_note = f"?? 頠??澆?嚗?0m 銝? {_p30m:+.1f}%嚗遣霅啁?????"
    except (TypeError, ValueError):
        pass

    # ??????????????????????????????????????????????????????????????
    # 蝚砌?甇伐??閮?閰?嚗 / A / B嚗?
    # ??????????????????????????????????????????????????????????????
    score = 0
    reasons = []

    # ?? 1. 閮??撘瑕漲 ??????????????????????????????????????????
    version = x.get("signal_version") or "potential"
    if version == "confirmed":
        score += 40
        reasons.append("銝惜?望")
    elif version == "tier2":
        score += 20
        reasons.append("?典??望")
    else:
        score += 10
        reasons.append("瞏閮?")

    # ?? 2. MTF 憭??嗅?朣???????????????????????????????????????
    mtf_aligned = x.get("mtf_aligned") or 1
    if mtf_aligned >= 4:
        score += 25
        reasons.append("4獢??")
    elif mtf_aligned >= 3:
        score += 18
        reasons.append("3獢??")
    elif mtf_aligned >= 2:
        score += 10
        reasons.append("2獢??")
    else:
        score += 3

    # ?? 3. 4H 摰?憭拙?????????????????????????????????????????
    if (is_above_4h is True and is_bull_sig) or (is_above_4h is False and not is_bull_sig):
        score += 15
        reasons.append("4H?")

    # ?? 4. RSI ?銵? ????????????????????????????????????????
    rsi_v = x.get("rsi")
    if rsi_v is not None:
        try:
            rsi_v = float(rsi_v)
            if is_bull_sig:
                if 30 <= rsi_v <= 55:
                    score += 10
                    reasons.append(f"RSI{rsi_v:.0f}?")
                elif 55 < rsi_v <= 70:
                    score += 5
                elif rsi_v > 75:
                    score -= 5
            else:
                if 45 <= rsi_v <= 70:
                    score += 10
                    reasons.append(f"RSI{rsi_v:.0f}?")
                elif 25 <= rsi_v < 45:
                    score += 5
                elif rsi_v < 25:
                    score -= 5
        except (TypeError, ValueError):
            pass

    # ?? 5. 1H OI 撘瑕漲 ????????????????????????????????????????
    oi_1h = abs(x.get("oiChange1h") or 0)
    if oi_1h >= 8.0:
        score += 10
        reasons.append(f"OI{oi_1h:.1f}%撘?")
    elif oi_1h >= 5.0:
        score += 7
    elif oi_1h >= 3.0:
        score += 5
    else:
        score += 2

    # ?? 6. 頞典??閰?嚗敹??伐??整?撅銝准??歇?澆?????????
    # 蝑?摩嚗?
    #   ??閮? + 24h 銝? ???其?頝挾銝剖遣憭?= ?航鞎瑕韏瑟撞暺?????
    #   ??閮? + 24h 憭扳撞 ???其?瞍脫畾菔蕭憭?= 餈賡?/鋡怠鞎券◢???????
    #   ?征閮? + 24h 銝撞 ???其?瞍脫挾銝剖遣蝛箏?= ?航?賊 ????
    #   ?征閮? + 24h 憭扯? ???其?頝畾菔蕭蝛?= 餈賭?/?征憸券 ?????
    cat = x.get("category", "")
    p24h = x.get("priceChange24h") or 0
    try:
        p24h = float(p24h)
        if cat == "long_open":
            if p24h < -3.0:
                score += 15
                reasons.append("銝?畾萄?撅")   # 銝?畾萄遣憭?= 鞎瑕?
            elif p24h > 10.0:
                score -= 10                     # 憭扳撞敺遣憭?= 餈賡?
        elif cat in ("short_cover", "short_close"):
            # 蝛箸?? = ??嚗銝?畾萄?暹憟?
            if p24h < -3.0:
                score += 10
                reasons.append("頝?頠征")
        elif cat == "short_open":
            if p24h > 3.0:
                score += 15
                reasons.append("銝撞畾菜??")   #  銝撞畾萄遣蝛?= ?賊
            elif p24h < -10.0:
                score -= 10                     # 憭扯?敺遣蝛?= 餈賭?
        elif cat == "long_close":
            # 憭撟喳?= ?征嚗銝撞畾萄?暹憟踝??箄疏嚗?
            if p24h > 3.0:
                score += 10
                reasons.append("瞍脣??箄疏")
    except (TypeError, ValueError):
        pass

    # ?? 7. 頝券??乩?蝣箄?? ??????????????????????????????????????
    # ?憚?冽銝哨?憒?鈭?閮??賢?橘?隞?”?抵????蝣箄????孵?
    # long_open + short_close = 憭撱箏?+ 蝛箸?? = ????蝣箄?嚗?蝢??殷?
    # long_close + short_open = 憭?箄疏 + 蝛箸撱箏?= ???征蝣箄?嚗?蝢征?殷?
    if x.get("_cross_confirm"):
        score += 20
        reasons.append("??鈭Ⅱ隤?")

    # ?? 8. 鞈?鞎餌??◢?? ???????????????????????????????????????
    # ??嚗祥??鞎?= 蝛粹?臭?鞎餌?蝯血???= 蝛粹憯? = ???◢
    #
    # 鞎餌??迤 = 憭?臭?鞎餌?蝯衣征??= 憭憯? = ?征?◢
    # 甇文??閮???憸具?鞎餌?撠????抬?嚗??冽迨??????撌脣?蕪撅方???
    _fr = x.get("funding_rate")
    if _fr is not None and isinstance(_fr, (int, float)):
        if is_bull_sig and _fr < -FR_SHORT_SQUEEZE_RISK:
            # ?? + 鞎餌???嚗征?孵????憭拍?典?????
            if _fr < -FR_SHORT_SQUEEZE_BLOCK:
                score += 10
                reasons.append("鞎餌?璆萄?鞎?征?◢")
            else:
                score += 5
                reasons.append("鞎餌????餃?憭?憸?")
        elif not is_bull_sig and _fr > FR_LONG_LIQUIDATION_RISK:
            # ?征 + 鞎餌??迤嚗??剖????憭拍憯????
            if _fr > FR_LONG_LIQUIDATION_BLOCK:
                score += 10
                reasons.append("鞎餌?璆萄?甇???憸?")
            else:
                score += 5
                reasons.append("鞎餌??迤?餃?蝛粹?憸?")

    # ?? 閰?嚗 / A / B嚗?撌脩????銝? A嚗????????????????????
    score = max(0, min(100, score))
    if _already_moving:
        score = min(score, 74)   # 頠歇?澆?嚗′銝? 74 ??= ?擃?A 蝝?
    if score >= 80:
        grade = "S"
        grade_badge = "?? *S 蝝?"
        grade_desc = "閮?璆萄撥"
    elif score >= 60:
        grade = "A"
        grade_badge = "?? *A 蝝?"
        grade_desc = "閮?撘?"
    else:
        grade = "B"
        grade_badge = "?? *B 蝝?"
        grade_desc = "閮?銝剔?"

    brief = f"{grade_badge} {grade_desc}{'??'.join(reasons[:3])}嚗?"
    return grade, score, brief, _already_moving, _motion_note


def build_report_message_tiered(
    enriched_items: List[Dict],
    processed_count: int = 0,
    oi_success_count: int = 0,
) -> str:
    """
    ????1H MTF ?惜瞍?閮??冽??    蝣箏?蝐Ⅳ嚗?撅文?荔?+ 瞏璈?嚗??Ｗ?頦???賊?摨????研?    ?銵?璅皞?1H K 蝺?銝剜?瘜Ｘ挾閬?嚗?    """
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
        ema20_touch_low: Optional[float] = None,   # ?餈?EMA20 ?萱雿?嚗?蝎暹?蝯?雿?
        ema20_touch_high: Optional[float] = None,  # ?餈?EMA20 ?萱擃?嚗?蝛箇?瑽?嚗?
        ):
        """
        SL ?賊??摩嚗?憭靘?嚗?          EMA20 ???游?蝯?雿??亦??箏 SL嚗??抵葉頛票餈?對?頛?嚗????          憒??芣??嗡葉銝????撠梁??瘝????2H 雿???
          ATR 雿蝺抵? pad嚗遣?? 0.5x嚗?蝛箏? 0.2x嚗?鞎澆?詨???瑽?憭??
        ?: (sl_price, tp1_price, tp2_price, sl_pct, warn_pct, sl_basis_label)
          sl_basis_label: 隤芣? SL 靘?嚗MA20 / 蝯?雿?/ 2H雿?/ ?∪皞?
        """
        if not price or price <= 0:
            return None, None, None, None, None, "??"

        is_squeeze = (signal_type == "squeeze")
        atr_val = float(atr) if atr and isinstance(atr, (int, float)) and atr > 0 else None
        atr_mult = 0.2 if is_squeeze else 0.5
        pad = atr_mult * atr_val if atr_val else price * (0.005 if is_squeeze else 0.01)

        def _valid_below(v):  # 撠?憭?????? = ?函?嫣誑銝?
            return v and isinstance(v, (int, float)) and 0 < float(v) < price
        def _valid_above(v):  # 撠?蝛綽??????? = ?函?嫣誑銝?
            return v and isinstance(v, (int, float)) and float(v) > price

        # pad_tight嚗MA20 ?萱蝯?雿璆萄?蝺抵?嚗?脫???銝憭扯??ｇ?
        pad_tight = atr_val * 0.15 if atr_val else price * 0.003

        candidates = []  # [(sl_price, label)]

        if is_squeeze:
            # ?? 頠征 / 頠?嚗摨??賊嚗?????????????????????????????????????????
            # ?摩嚗澆??賢?閮?嚗MA20 ?虜??曉銝?脣??儔??
            # SL 隞乓??質絲瞍脩?瑽???箸?嚗?            #   頠征嚗?憭???頠征?? K 銋???暺仃摰?= ?瘨仃 ???箏
            #   頠?嚗?蝛綽???頠??? K 銋???暺???= ?瘨仃 ???箏
            if is_long:
                if _valid_below(pre_breakout_low):
                    candidates.append((float(pre_breakout_low) - pad, "頠征韏瑟撞雿?)"))
                if _valid_below(recent_low_2h):
                    candidates.append((float(recent_low_2h) - pad, "2H雿?"))
            else:
                if _valid_above(pre_breakout_high):
                    candidates.append((float(pre_breakout_high) + pad, "頠?韏瑟撞擃?)"))
                if _valid_above(recent_high_2h):
                    candidates.append((float(recent_high_2h) + pad, "2H擃?"))
        else:
            # ?? 撱箏?嚗?憭???/ ?征蝒嚗?????????????????????????????????????
            # ?摩嚗MA20 ?舀敹摰???
            # ?芸??具?餈?甈?EMA20 ?萱?祕??暺?撣撽???嚗?            # ?嗆活?? EMA20 雿蔭嚗?敺??2H 蝯???
            if is_long:
                # ?芸?嚗MA20 ?萱雿?撣撽????祕蝯?雿?
                if _valid_below(ema20_touch_low):
                    candidates.append((float(ema20_touch_low) - pad_tight, "EMA20?萱雿?)"))
                # 甈⊿嚗MA20 ??雿蔭
                if _valid_below(ema20):
                    candidates.append((float(ema20) - pad, "EMA20?脣?"))
                # ?嚗??游? 3 ?寧?瑽?
                if _valid_below(pre_breakout_low):
                    candidates.append((float(pre_breakout_low) - pad, "蝯?雿摰?)"))
                # ?蝯??湛?2H ?湧?雿?
                if _valid_below(recent_low_2h):
                    candidates.append((float(recent_low_2h) - pad, "2H雿?"))
            else:
                # ?芸?嚗MA20 ?萱擃?撣撽????祕蝯?雿?
                if _valid_above(ema20_touch_high):
                    candidates.append((float(ema20_touch_high) + pad_tight, "EMA20?萱擃?)"))
                # 甈⊿嚗MA20 ??雿蔭
                if _valid_above(ema20):
                    candidates.append((float(ema20) + pad, "EMA20?脣?"))
                # ?嚗??游? 3 ?寧?瑽?
                if _valid_above(pre_breakout_high):
                    candidates.append((float(pre_breakout_high) + pad, "蝯?擃摰?"))
                # ?蝯??湛?2H ?湧?擃?
                if _valid_above(recent_high_2h):
                    candidates.append((float(recent_high_2h) + pad, "2H擃?"))

        if candidates:
            if is_long:
                # ????擃??鞎潸??曉 = ?蝺???甇Ｘ?嚗?
                sl_price, sl_label = max(candidates, key=lambda c: c[0])
            else:
                # ?征??雿?
                sl_price, sl_label = min(candidates, key=lambda c: c[0])
            # 蝣箔? SL ?孵?甇?Ⅱ嚗??賜忽頞?對?
            if is_long and sl_price >= price:
                sl_price = price * 0.97
                sl_label = "?3%"
            elif not is_long and sl_price <= price:
                sl_price = price * 1.03
                sl_label = "?3%"
        else:
            sl_price = price * 0.97 if is_long else price * 1.03
            sl_label = "?3%"

        # ?? SL 瘛函征靽風嚗L Clear Zone嚗??????????????????????????????????????????
        # ?詨???嚗L 敹???頞? 2h ???潸??綽??血?閮??典敺??餃停?質◤??閫詨???
        #   ?征嚗l > recent_2h_high + 0.5%嚗L 敹?蝡 2h ?擃?銋?嚗?        #   ??嚗l < recent_2h_low  - 0.5%嚗L 敹?蝡 2h ?雿?銋?嚗?        # TP ?銝?芸?隞交 risk ??嚗 瘥?銝???
        _sl_clear_buffer = 0.005  # 0.5%嚗雲憭?頞?閮?+ 皛嚗????漲?游之??嚗?
        if not is_long and recent_high_2h and recent_high_2h > 0:
            _min_sl_short = recent_high_2h * (1.0 + _sl_clear_buffer)
            if sl_price < _min_sl_short:
                logger.debug(
                    f"[SL瘛函征] ?征 SL {sl_price:.6f} ??2h{_high_2h:.6f}銋?嚗?"
                    f"隤{_short:.6f}嚗?0.5% 瘛函征蝺抵?嚗?"
                )
                sl_price = _min_sl_short
                sl_label += " 瘛函征"
        elif is_long and recent_low_2h and recent_low_2h > 0:
            _max_sl_long = recent_low_2h * (1.0 - _sl_clear_buffer)
            if sl_price > _max_sl_long:
                logger.debug(
                    f"[SL瘛函征] ?? SL {sl_price:.6f} ??2h{_low_2h:.6f}銋?嚗?"
                    f"隤{_long:.6f}嚗?0.5% 瘛函征蝺抵?嚗?"
                )
                sl_price = _max_sl_long
                sl_label += " 瘛函征"

        risk = (price - sl_price) if is_long else (sl_price - price)
        if risk <= 0:
            risk = price * (0.005 if is_squeeze else 0.015)

        # ?? TP ??嚗SI ?/??葬?剔璅?敹恍鋡?
        # ?? RSI>75 = 頞眺餈賡?嚗P 靽?嚗?憭?RSI<40 = 雿?憭嚗征??頞?        # ?征 RSI<25 = 頞都餈賜征嚗P 靽?嚗?蝛?RSI>60 = 擃?蝛粹嚗征??頞?
        rsi_val_f = float(rsi) if rsi and isinstance(rsi, (int, float)) else None
        if is_squeeze:
            r1, r2 = 1.0, 2.0
            tp_mode = "squeeze"
        elif rsi_val_f is not None and ((is_long and rsi_val_f >= 75) or (not is_long and rsi_val_f <= 25)):
            r1, r2 = 1.0, 2.0   # RSI ?/?嚗葬??TP嚗翰?鋡?
            tp_mode = "rsi_hot"
        else:
            r1, r2 = 1.5, 3.0   # 甇?虜頞典嚗?皞?TP
            tp_mode = "normal"

        tp1_price = price + r1 * risk if is_long else price - r1 * risk
        tp2_price = price + r2 * risk if is_long else price - r2 * risk

        sl_pct = abs(price - sl_price) / price * 100 if price > 0 else 0
        warn_pct = sl_pct if sl_pct > 8.0 else None
        return sl_price, tp1_price, tp2_price, sl_pct, warn_pct, sl_label, tp_mode, r1, r2

    def _is_bull(x: Dict) -> bool:
        cat = x.get("category", "")
        return cat in ("long_open", "short_close")

    def _pass_rsi_filter(x: Dict, z: str) -> bool:
        return True  # ?啁?銝?瞈?RSI嚗I+Price 蝯?璇辣撌脰雲憭?

    # ?剔????? = ?賊/?? + 5??+ |OI|>=OI_FOR_ELITE + ?漱?1M + ?喳?銝???格??豢? + RSI 頛
    # 攳券??銝撥?塚?撅勗祠撟??閬?嚗??漱??瑼餅撖穿?撅勗祠撟??蝝?雿?
    def _is_elite(x: Dict) -> bool:
        return (x.get("stars") or 0) >= 5  # ?啁?嚗???璇辣????????    # ?? 隞乩??箸?葡??頛航絲憪? ??????????????????????????????????????????????

    def _fmt_price(p: Optional[float]) -> str:
        if p is None or (isinstance(p, float) and p != p) or p <= 0:
            return "??"
        # 璆萄??寞嚗? meme 撟?7.18e-9嚗???閮??閬嗾雿??賡＊蝷?4 雿??摮?
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
            return "?賊?征"
        if zone == ZONE_DIP:
            return "????"
        if zone == ZONE_BREAKOUT_LONG:
            return "餈賢?"
        if zone == ZONE_BREAKOUT_SHORT:
            return "餈賜征"
        return "??" if is_bull_flag else "?征"

    def _reason_plain(reason: str) -> str:
        return (reason or "蝐Ⅳ???).strip(")
        # ?? 隞乩????摩撌脫??剁??啁? reason 撌脩?亙 _classify_signal_and_tier ?Ｙ?嚗???
        if not reason:
            return "蝐Ⅳ???"
        r = (reason or "").strip()

        # 1) 撟喳?/ 皜?嚗??剖像??/ 蝛粹撟喳?
        if "?之皜?(蝛粹撟喳??詨?)" in r or ("蝛粹撟喳?" in r and "?詨?" in r):
            r = r.replace(
                "?之皜?(蝛粹撟喳??詨?)",
                "?征?犖甇??貊???嚗征?孵????湛??ㄐ?????蝺?暺?"
            )
        if "?之皜?(憭撟喳??賊)" in r or ("憭撟喳?" in r and "?賊" in r):
            r = r.replace(
                "?之皜?(憭撟喳??賊)",
                "???犖??銝?嫣??寡都?綽??剔?瞍脣?摰寞???嚗??剔征"
            )

        # 2) ?之憓?+ 鞎餌?嚗圾?銝餃??Ⅳ / 頠征 / 畾箏?
        if "?之憓?鞎餌?鞎?" in r:
            r = r.replace(
                "?之憓?鞎餌?鞎?(?征瞏?)",
                "銝餃???鞎瑕嚗?蝛箇?鈭箏???隞嚗征?桅??質◤頠?嚗?瞍脣??賡?撣詨撥"
            )
        if "?之憓?(餈賢?)" in r:
            r = r.replace(
                "?之憓?(餈賢?)",
                "憭憭扯??Ⅳ嚗??湔迤?券??Ｗ?銝粥"
            )
        if "鞎餌?/??憭?" in r:
            r = r.replace(
                "鞎餌?/??憭?",
                "憭鈭箸???祇??嚗蝺???瞍莎?雿???銝?血?頧?畾箏?"
            )
        if "鞎餌?甇?憭撟喳?(?賊)" in r:
            r = r.replace(
                "鞎餌?甇?憭撟喳?(?賊)",
                "???犖?券?雿?Ｗ鞎剁???隞?鞎餌????ㄐ摰寞?霈??剔?擃?"
            )
        if "鞎餌?鞎?蝛粹撟喳?(?詨?)" in r:
            r = r.replace(
                "鞎餌?鞎?蝛粹撟喳?(?詨?)",
                "?征?犖?其?雿?鋆?蝯?憭???鞎餌??芸嚗ㄐ摰寞?霈??剔?雿?"
            )

        # 3) 撘瑕?征 / 憭扳撞?膩嚗?閮港蝙?刻??頝臬????
        if "撘瑕?征 (24h瞍?" in r:
            # 靘? "? 撘瑕?征 (24h瞍?25.0%)" ??"24h 銝撞 25.0%嚗征?桐?頝航◤頠?銵??虜撘瑕"
            r = r.replace("? 撘瑕?征 (24h瞍?", "24h 銝撞 ")
            if "%)" in r and "蝛箏銝頝航◤頠?" not in r:
                r = r.replace("%)", "%嚗征?桐?頝航◤頠?銵??虜撘瑕", 1)

        # 4) ?嗅?頛?蝖祉??膩嚗???狐?典鞎?/ 隤啣?貊???
        if "?之皜?(憭撟喳?" in r and "?賊" in r:
            r = r.replace(
                "???之皜?(憭撟喳??賊)",
                "????犖甇?鞈??Ｗ嚗ㄐ撅祆擃?頧摹?嚗?蝛粹?"
            )
        if "?? (?征)" in r:
            r = r.replace(
                "?? (?征)",
                "???其??函葬撠??湧?蝐Ⅳ?亙?蝛?"
            )

        # 5) CVD / 憭批蝣箄?嚗?銝之?桀祕?祕???質店隤芣?
        if " (CVD蝣箄?)" in r:
            r = r.replace(
                " (CVD蝣箄?)",
                " ??憭批撖行?撖行?鈭歹?銝阡????嚗蜓?????券脣"
            )

        return r if r.strip() else "蝐Ⅳ???"

    # ??????????????????????????????????????????????????
    # ?啁?皜脫??摩嚗?0m ?情?扔蝪⊥撘?
    # ??????????????????????????????????????????????????

    # ?? 撠??璅?撠?嚗??憭??征?誘嚗????????????????????????????
    _signal_title = {
        "long_open":   "? ?撥?Ｗ?憭?Long??",
        "short_close": "? ?敺拙?敶?(??)??",
        "short_open":  "? ???Ｗ?蝛?Short??",
        "long_close":  "? ???援頝?(?征)??",
    }
    # ?? ?質店?脣?摩嚗??亥店蝘?嚗????????????????????????????????????
    _signal_reason = {
        "long_open":   "?振甇???亦???鞎琿莎?銝之頞典??嚗??Ｚ?銝?",
        "short_close": "?征?犖甇?鋡怠?蝛箏撥?嗅像??撘??銝撞嚗?剖?嚗?",
        "short_open":  "憭扳甇?憭扯?撱箏?蝛綽?銝之頞典?征嚗??Ｙ?頝?",
        "long_close":  "???犖甇????頦抵?嚗??潮??頝嚗?剔征嚗?",
    }

    now_str = datetime.now(TAIPEI_TZ).strftime("%m/%d %H:%M")
    messages_out: List[str] = []
    grade_per_msg: List[str] = []   # ??messages_out ?郊嚗???????閰?
    s_grade_msgs: List[str] = []    # S 蝝??蝡??靘?憭??
    push_count = 0
    has_any = False
    seen_syms: set = set()

    # ?? 頝券??乩?蝣箄???蝞?????????????????????????????????????????????????
    # 蝑?詨?嚗?∪????Ⅱ隤?銝?孵? = ?撘瑁???
    #   憭摰?蝯?嚗ong_open嚗蜓?遣憭?嚗?short_close嚗征?寡◤餈怠?鋆?= ???典?
    #   蝛箏摰?蝯?嚗ong_close嚗蜓?鞎剁?嚗?short_open嚗征?嫣蜓?遣??= ??憯
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

    for x in enriched_items:
        sym = x.get("symbol", "")
        if not sym or sym in seen_syms:
            continue
        seen_syms.add(sym)
        category = x.get("category", "")
        title = _signal_title.get(category)
        if not title:
            continue

        sym_base = sym.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
        is_bull_sig = category in ("long_open", "short_close")
        price = x.get("current_price")
        if not price or not isinstance(price, (int, float)) or price <= 0:
            continue

        oi30 = x.get("oiChange30m")
        p30 = x.get("priceChange30m")
        p1h = x.get("priceChange1h")
        vol_usd = x.get("volume_usd") or x.get("_cg_volume_usd") or 0
        funding_rate = x.get("funding_rate")
        atr_val = x.get("atr")
        taker_ratio_15m = x.get("_taker_ratio_15m")  # 銝餃?鞎瑞%嚗oins-markets top-100 ??嚗?
        rsi_val = x.get("rsi")
        detected_ts = x.get("_detected_ts")
        vwap_2h_val = x.get("vwap_2h")
        _now_ts = time.time()

        # ??????????????????????????????????????????????????????????
        # ATR 憸冽閮?嚗oogle 撱箄降蝝?ATR ?砍?嚗isk = 1.5 ? 1H_ATR嚗?
        # ??嚗L = ?曉 - Risk | TP1 = ?曉 + Risk?1.5 | TP2 = ?曉 + Risk?3.0
        # ?征嚗L = ?曉 + Risk | TP1 = ?曉 - Risk?1.5 | TP2 = ?曉 - Risk?3.0
        # ??????????????????????????????????????????????????????????
        sl, tp1, tp2 = None, None, None
        _r1, _r2 = 1.5, 3.0
        sl_pct_val = None
        if atr_val and atr_val > 0 and price and price > 0:
            _risk = 1.8 * atr_val
            if is_bull_sig:
                sl  = price - _risk
                tp1 = price + _risk * 1.5
                tp2 = price + _risk * 3.0
            else:
                sl  = price + _risk
                tp1 = price - _risk * 1.5
                tp2 = price - _risk * 3.0
            sl_pct_val = abs(price - sl) / price * 100
        else:
            # ??ATR ?嚗誑?箏?瘥?閮?嚗?.5% = 1R嚗?
            _risk = price * 0.015 if price and price > 0 else None
            if _risk:
                sl  = price - _risk if is_bull_sig else price + _risk
                tp1 = price + _risk * 1.5 if is_bull_sig else price - _risk * 1.5
                tp2 = price + _risk * 3.0 if is_bull_sig else price - _risk * 3.0
                sl_pct_val = 1.5

        # ??????????????????????????????????????????????????????????
        # 閮?? / 璅惜 / 蝑?剛?
        # ??????????????????????????????????????????????????????????
        _sig_version   = x.get("signal_version") or "potential"
        _sig_subtype   = x.get("signal_subtype") or ""
        _mtf_desc      = x.get("mtf_desc") or ""
        _reversal_hint = x.get("reversal_hint") or ""

        # 璅?璅惜嚗?撅方???confirmed / pullback / tier2嚗?
        _dir_str   = "??" if is_bull_sig else "?征"
        _dir_emoji = "?"   if is_bull_sig else "?"
        if _sig_version == "confirmed":
            _type_str  = "蝣箏?蝐Ⅳ?餃?渡???"
            _badge_emo = "??"
            _ver_label = "??*蝣箏?蝐Ⅳ*嚗銝??望 1H/30m/15m 銝?湛?"
            sig_emoji  = "??"
        elif _sig_version == "tier2":
            _t2_sub    = _sig_subtype or "撘勗??"
            _type_str  = f"閫撖??{_sub}"
            _badge_emo = "??"
            _ver_label = f"?? *閫{_sub}嚗遣霅啗???"
            sig_emoji  = "??"
        else:  # pullback
            _type_str  = "瞏璈??餌??雿" if is_bull_sig else "瞏璈??餌????征"
            _badge_emo = "?妓"
            _ver_label = "? *瞏璈?*嚗?蝢?頦抬?"
            sig_emoji  = "??儭?"

        x["_sig_emoji"] = sig_emoji  # 靘?header 敶蜇?

        # ?? 蝑?剛?嚗???????????????????????????????????????????
        def _gen_comment(cat: str, ver: str, sub: str, hint: str, rsi_v) -> str:
            # short_close嚗?憪潘???short_cover嚗?冽?皞??潘?隤??詨?嚗I??Price??= 蝛箸??
            _is_bull_cat = cat in ("long_open", "short_cover", "short_close")
            _is_bear_cat = cat in ("short_open", "long_close")
            if ver == "confirmed":
                if cat == "long_open":
                    return "銝餃?銝惜?望撱箏?????Ⅱ嚗?渲蕭憭???"
                if cat == "short_open":
                    return "銝餃?銝惜?望撱箇征??蝛粹?蝣箄?嚗?渲蕭蝛箸???"
                if cat in ("short_cover", "short_close"): return "蝛箸銝惜?望??嚗?蝛箇???頞喉??喳??璈?嚗?"
                return "憭銝惜?望撟喳??征???嚗?游?蝛箸???"
            if sub == "pullback":
                if _is_bull_cat:
                    return "憭扳?獢??剛隅?ＹⅡ蝡?撠望??剜?矽嚗雿?脣????璈?"
                return "憭扳?獢征?剛隅?ＹⅡ蝡?撠望??剜??嚗?ａ??征????璈?"
            if ver == "tier2":
                if sub == "RSI璆萇垢":
                    rsi_str = f"RSI={rsi_v:.0f}" if rsi_v else "RSI??"
                    if _is_bull_cat:
                        return f"?萎?閫?蝡? {rsi_str} 撌脣??梧??桅?銵??航????湔甇Ｘ???"
                    return f"?萎?閫?蝡? {rsi_str} 撌脣??瘀????航????湔甇Ｘ???"
                return "1H/30m ??雿?15m 撠蝣箄?嚗?敺??望??孵??嗆?敺??脣??"
            return "蝐Ⅳ?孵?蝣箄?銝哨??游?甇Ｘ???"

        _strategy_comment = _gen_comment(category, _sig_version, _sig_subtype, _reversal_hint, rsi_val)

        # ?? 4H 摰?憭拙?????????????????????????????????????????????????
        _ema20_4h_val    = x.get("ema20_4h")
        _rsi_4h_val      = x.get("rsi_4h")
        _is_above_4h_ema = x.get("is_above_4h_ema")
        if _is_above_4h_ema is True:
            _macro_trend   = "?" if is_bull_sig else "?"
            _macro_ema_txt = "蝡? 4H EMA20"
        elif _is_above_4h_ema is False:
            _macro_trend   = "?" if is_bull_sig else "?"
            _macro_ema_txt = "頝 4H EMA20"
        else:
            _macro_trend   = "??"
            _macro_ema_txt = "4H EMA20 ?⊥??"
        _rsi_4h_str  = f" 繚 RSI {_rsi_4h_val:.0f}" if _rsi_4h_val is not None else ""
        _macro_line  = f"?? *4H憭拙?* {_macro_trend} 繚 {_macro_ema_txt}{_rsi_4h_str}"

        # ?? 鞈?鞎餌?嚗憭征憯??方?嚗????????????????????????????????????????
        # 鞎餌??? = 蝛粹?臭?鞎餌?嚗征?剖????????舫?憸剁??征憸券擃??征嚗?
        # 鞎餌??迤 = 憭?臭?鞎餌?嚗??剖??????征?舫?憸剁???憸券擃???
        if funding_rate is not None and isinstance(funding_rate, (int, float)):
            _fr_val = funding_rate * 100
            # 銝剜批???
            if abs(funding_rate) <= 0.0001:
                _fr_desc = "銝剜?"
            # ?湧?憯?嚗歇鋡?FR 撠??銝??圈??ㄐ嚗?憿舐內撅支??璅酉嚗?
            elif funding_rate <= -FR_SHORT_SQUEEZE_BLOCK:
                _fr_desc = "? 蝛粹?湧?憯?嚗?蝛粹◢?芣扔擃?" if is_bull_sig else "? 蝛粹?湧?憯?嚗??輯蕭蝛綽?"
            elif funding_rate >= FR_LONG_LIQUIDATION_BLOCK:
                _fr_desc = "? 憭?湧?憯?嚗??輯蕭憭?" if is_bull_sig else "? 憭?湧?憯?嚗征?寥?憸剁?"
            # 憯?霅行?嚗?蝝???賢?曉甇歹?
            elif funding_rate < -FR_SHORT_SQUEEZE_RISK:
                _fr_desc = "?? 蝛粹憯?嚗?憭?憸?" if is_bull_sig else "?? 蝛粹憯?嚗?蝛粹◢?芸?擃?"
            elif funding_rate > FR_LONG_LIQUIDATION_RISK:
                _fr_desc = "?? 憭憯?嚗?蝛粹?憸?" if not is_bull_sig else "?? 憭憯?嚗??◢?芸?擃?"
            # 頛凝??
            elif funding_rate < -0.0005:
                _fr_desc = "?亙?蝛綽????交?鞎餌???嚗?" if is_bull_sig else "?亙?蝛綽?蝛粹蝔?嚗?"
            elif funding_rate > 0.0005:
                _fr_desc = "?亙?憭??征?交?鞎餌???嚗?" if not is_bull_sig else "?亙?憭?憭蝔?嚗?"
            elif funding_rate > 0:
                _fr_desc = "?亙?憭?"
            else:
                _fr_desc = "?亙?蝛?"
            # ?芸??餃偏?塚?0.0100% ??0.01%嚗?0.0500% ??-0.05%
            _fr_str = f"{_fr_val:+.4f}".rstrip('0').rstrip('.')
            _fr_line = f"? *鞎餌?嚗? `{_fr_str}%` {_fr_desc}"
        else:
            _fr_line = "? *鞎餌?嚗? ?⊥??"

        # ?? ?漱??????????????????????????????????????????????????????
        vol_m_val    = float(vol_usd) / 1e6 if vol_usd and float(vol_usd) > 0 else 0.0
        _vol_src_tag = x.get("_vol_source", "CoinGlass")
        _src_note    = f" _{_vol_src_tag}_" if _vol_src_tag not in ("CoinGlass", "") else ""
        if vol_m_val >= 50:
            _vol_line = f"?? ?{_val:.0f}M` ??璈?蝝?"
        elif vol_m_val >= 20:
            _vol_line = f"?? ?{_val:.0f}M` ??瘛勗漲?雲"
        elif vol_m_val >= 5:
            _vol_line = f"?? ?{_note} ?? 瘚??批?雿?"
        elif vol_m_val > 0:
            _vol_line = f"?? ?{_note} ?? 璆萎?瘚???"
        else:
            _vol_line = ""  # ?⊥?鈭文潸???銝＊蝷箸迨銵??踹?隤文?

        # ??????????????????????????????????????????????????????????
        # 閮?閰?嚗 / A / B / R嚗?
        # ??????????????????????????????????????????????????????????
        _grade, _grade_score, _grade_brief, _already_moving, _motion_note = _calc_signal_grade(x, is_bull_sig)

        # ??????????????????????????????????????????????????????????
        # 蝯??餃閮嚗?璈???餅??蝯?皜嚗?
        # ??????????????????????????????????????????????????????????
        msg_lines: List[str] = []

        # ? 璅?銵??嚗＊蝷箏蝷馳????USDT 敺韌嚗copy_sym 靘?雿??怠?雿輻嚗?
        _copy_sym = sym if sym.endswith("USDT") else f"{sym_base}USDT"
        msg_lines.append(f"{_dir_emoji} *{_dir_str}* `{sym_base}` {_badge_emo}")
        msg_lines.append(_grade_brief)
        msg_lines.append(_ver_label)
        msg_lines.append("")

        # ? 摰?憭拙?繚 鞎餌? 繚 ?漱?潘?????憿舐內?漱?潸?嚗?
        msg_lines.append(_macro_line)
        msg_lines.append(_fr_line)
        if _vol_line:
            msg_lines.append(_vol_line)
        msg_lines.append("")

        # ? 蝐Ⅳ?望瞍? ?
        msg_lines.append("?? *蝐Ⅳ瞍?嚗?")
        if _mtf_desc:
            msg_lines.append(_mtf_desc)
        else:
            msg_lines.append("  ????MTF ?豢?")
        # 4H OI 蝝舐??內
        oi_4h_val = x.get("oi_change_4h_pct")
        if isinstance(oi_4h_val, (int, float)):
            _abs_4h = abs(oi_4h_val)
            if _abs_4h >= 5.0:
                msg_lines.append(f"_4H OI {oi_4h_val:+.1f}% ?? ?畾蛛?蝮桃?格?_")
            elif _abs_4h >= 2.5:
                pass  # TODO: 4H OI 2.5-5.0% range
        push_count += 1
        has_any = True
        logger.info(
            f"[?冽] {sym_base} {title} ?曉=${_fmt_price(price)} "
            f"SL=${_fmt_price(sl)} TP1=${_fmt_price(tp1)} TP2=${_fmt_price(tp2)}"
        )

    if not has_any:
        no_sig_msg = (
            f"?? *???撣貊??* ?祈憚?∟??n"
            f"?? {now_str}  璇辣嚗?H OI?{OI_THRESHOLD_1H}% & ?{MTF_VOLUME_MIN_USD/1e6:.0f}M & MTF?望\n"
            f"蝜潛???銝?.."
        )
        return no_sig_msg, False, 0, []

    # ?園????? emoji嚗???header 敶蜇??
    pushed_items = [
        x for x in enriched_items
        if x.get("selected_for_push") and x.get("symbol") in seen_syms
    ]
    emoji_summary = " ".join(x.get("_sig_emoji", "??儭") for x in pushed_items)

    # 憭?賊??扯郎蝷綽??憚 ?? ??????
    bull_count = sum(1 for x in pushed_items if x.get("category") in ("long_open", "short_close"))
    bear_count = sum(1 for x in pushed_items if x.get("category") in ("short_open", "long_close"))
    correlation_warn = ""
    if bull_count >= 3:
        correlation_warn = (
            f"\n{'?' * 20}\n"
            f"?? *?賊??扯郎蝷綽??祈憚 {bull_count} ???桀????\n"
            f"BTC ?交亥??航?郊閫豢?嚗??批蝮賢?嚗?典??"
        )
    elif bear_count >= 3:
        correlation_warn = (
            f"\n{'?' * 20}\n"
            f"?? *?賊??扯郎蝷綽??祈憚 {bear_count} ?征?桀????\n"
            f"BTC ?交交撞?航?郊閫豢?嚗??批蝮賢?嚗?典??"
        )

    # ?? 閰?蝯梯?嚗/A/B/R嚗??????????????????????????????????????????
    _grade_counts = {"S": 0, "A": 0, "B": 0, "R": 0}
    for _g in grade_per_msg:
        if _g in _grade_counts:
            _grade_counts[_g] += 1

    _grade_parts = []
    for _g, _badge in [("S", "??S"), ("A", "??A"), ("B", "??B"), ("R", "?﹕")]:
        if _grade_counts[_g] > 0:
            _grade_parts.append(f"{_badge}?{_grade_counts[_g]}")
    _grade_tag = "  ".join(_grade_parts) if _grade_parts else "?"

    header = (
        f"?? *???撣貊??*  ?祈憚 {push_count} ???n"
        f"?? {now_str} ?啣?  |  {_grade_tag}\n"
        f"{'?' * 20}\n"
    )
    sep = f"\n{'?' * 20}\n"
    body = sep.join(messages_out) + correlation_warn

    # ?? 隞乩??箄??葡????撌脫??剁??湔 return 頝喲?嚗??????????????
    return header + body, has_any, push_count, s_grade_msgs

def process_single_symbol(coin: Dict) -> Optional[Dict]:
    """
    ???桀馳蝔殷?1H MTF 瞍? Stage 1嚗?H OI/Price 憭扳撅??嚗?    ?情??憿?頛荔?
      price??+ OI??= long_open  (銝餃?蝛扔撱箏???
      price??+ OI??= short_close (蝛箸鋡怨翰??)
      price??+ OI??= short_open (銝餃?蝛扔撱箇征??
      price??+ OI??= long_close  (憭??撟喳?
    """
    symbol = normalize_symbol(coin)
    if not symbol:
        return None

    try:
        # 1H ?寞霈?嚗???Stage 1 銝餅?獢?
        price_change_1h = coin.get("price_change_percent_1h")
        try:
            price_change_1h = float(price_change_1h) if price_change_1h is not None else None
        except (TypeError, ValueError):
            price_change_1h = None

        if price_change_1h is None:
            return {'status': 'no_category', 'symbol': symbol}

        # 1H OI 霈?嚗tage 1 ?詨???嚗?
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
                'priceChange30m': price_change_30m,  # 靽?靘＊蝷箇
                'oiChange1h': oi_change_1h,
                'oiChange30m': oi_change_1h,          # ???詨捆
                'priceChange24h': price_change_24h,
                'price_change_percent_1h': price_change_1h,
                '_cg_volume_usd': coin.get("_volume_usd") or coin.get("_cg_volume_usd"),
                '_scan_ts': time.time(),  # 1H OI ?啣?擐活?菜葫??
            }
        else:
            return {'status': 'no_category', 'symbol': symbol}

    except Exception as e:
        logger.error(f"?? {symbol} ??隤? {str(e)}")
        return {'status': 'error', 'symbol': symbol, 'error': str(e)}


def _gist_load_cooldown() -> Optional[Dict]:
    """敺?GitHub Gist 霈??餌????閮剖? GIST_ID + GITHUB_TOKEN ?啣?霈嚗?    ?閫??敺? dict嚗? None嚗閮剖? / 霈?仃????    """
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
                logger.info(f"[Gist?瑕? 敺?GitHub Gist 霈????history={len(data.get('history',[]))} 蝑?")
                return data
        else:
            logger.warning(f"[Gist?瑕] 霈?仃??HTTP {resp.status_code}")
    except Exception as e:
        logger.warning(f"[Gist?瑕] 霈??憭? {e}")
    return None


def _gist_save_cooldown(data: Dict) -> bool:
    """撠?餌??神??GitHub Gist???單?行???"""
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
            logger.info(f"[Gist?瑕? 撖怠? GitHub Gist ??嚗istory={len(data.get('history',[]))} 蝑?")
            return True
        else:
            logger.warning(f"[Gist?瑕] 撖怠?憭望? HTTP {resp.status_code}")
    except Exception as e:
        logger.warning(f"[Gist?瑕] 撖怠?靘?: {e}")
    return False




def fetch_coinglass_coins_markets() -> List[Dict]:
    """??皞? CoinGlass-First????CoinGlass ?典??游馳蝔桀翰?扼?
    ?芸??澆 /api/futures/coins-markets嚗?漱????獢?潸???嚗?    ?亥府蝡舫?銝?剁????/api/futures/coins-price-change??
    ?蝯曹??澆??”嚗???item 靽??恬?
        symbol                 : str  (base嚗? "BTC"??1000PEPE")
        coin                   : str  (??symbol嚗??? normalize_symbol 霈??
        price_change_percent_30m: float|None  (15m / ?餈?函??剝望?瞍脰?撟?
        price_change_percent_24h: float|None
        _cg_volume_usd         : float|None  (24h ?漱憿?USD嚗??漱??蝭拐蝙??
    """
    if not CG_API_KEY:
        return []

    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}

    # ?? ?岫 coins-markets嚗?皞?摰蝡舫?嚗葆?????典??湛????????????????
    def _parse_cg_item(item: Dict) -> Optional[Dict]:
        """閫???桐? CoinGlass coins-markets item ?箇絞銝?澆?嚗仃????None??"""
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
        # ?漱??雿?CoinGlass ?賢?瘛瑚?嚗?狙????賜? key
        # ?漱?潘?CoinGlass coins-markets ?? long_volume_usd_24h + short_volume_usd_24h
        # ?抵??= ?典???24h ???漱憿?USD嚗?
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
        # CoinGlass coins-markets 撌脣 15m OI 霈????湔靽?嚗?敺??I敹恍??
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

        # CoinGlass coins-markets ? 15m/1h 銝餃?鞎瑁都???祥??CVD 鞈?嚗?
        # long_volume_usd_15m = 銝餃?鞎瑕嚗aker buy嚗? short_volume_usd_15m = 銝餃?鞈?嚗aker sell嚗?
        try:
            taker_buy_15m = float(item.get("long_volume_usd_15m") or 0)
            taker_sell_15m = float(item.get("short_volume_usd_15m") or 0)
            taker_total_15m = taker_buy_15m + taker_sell_15m
            # 銝餃?鞎瑞雿? 0~100嚗one 隞?”甇文馳??15m taker 鞈?嚗? coins-markets top-100嚗?
            taker_ratio_15m = round(taker_buy_15m / taker_total_15m * 100, 1) if taker_total_15m > 0 else None
        except (TypeError, ValueError):
            taker_ratio_15m = None

        return {
            "symbol": sym,
            "coin": sym,
            "price_change_percent_30m": p15,
            "price_change_percent_1h": p1h,
            "price_change_percent_24h": p24,
            "_cg_volume_usd": vol,
            "_cg_oi_change_15m": cg_oi_15m,  # CoinGlass 撌脩?憟賜? 15m OI 霈???coins-markets ??嚗?            "_taker_ratio_15m": taker_ratio_15m,  # 銝餃?鞎瑞%嚗one=?∟???>50=鞎瑕?>鞈??
            "_raw_cg": item,
        }

    def _fetch_one_coins_markets_page(sort_field: str = "", sort_type: str = "0",
                                       seen: Optional[set] = None) -> List[Dict]:
        """??銝??coins-markets嚗??唾圾????銵剁??駁???seen set嚗?"""
        if seen is None:
            seen = set()
        out_page: List[Dict] = []
        try:
            _respect_coinglass_rate_limit()
            params: Dict = {"pageSize": 100}
            if sort_field:
                params["sortField"] = sort_field
                params["sortType"] = sort_type  # "0"=?? "1"=??
            r = requests.get(
                f"{CG_API_BASE}/api/futures/coins-markets",
                headers=headers,
                params=params,
                timeout=15,
            )
            if r.status_code != 200:
                logger.debug(f"[CoinGlass憭?摨 sortField={sort_field} HTTP {r.status_code}")
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
            logger.info(f"[CoinGlass憭?摨 sortField={sort_field or '(default)'} ?啣? {added} ??/ ?祆活??{len(raw)} 蝑?")
        except Exception as e:
            logger.debug(f"[CoinGlass憭?摨 sortField={sort_field} ?啣虜: {e}")
        return out_page

    def _try_coins_markets() -> List[Dict]:
        """?? CoinGlass coins-markets Top-100嚗? OI ??嚗?        CoinGlass ??pageNum / sortField ?◤ API 敹賜嚗?甈∪?怠??喟??100 蝑?
        ????甈⊿?閮剜?摨?蝭???? API ????        """
        seen_syms: set = set()
        out = _fetch_one_coins_markets_page("", "0", seen_syms)
        logger.info(f"[CoinGlass-First] coins-markets ?? {len(out)} ?馳蝔殷?OI ?? Top-100嚗?")
        return out

    # ?? ?岫 coins-price-change嚗??渡垢暺???????????????????????????????????
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
                    "price_change_percent_30m": p15,
                    "price_change_percent_1h": p1h,
                    "price_change_percent_24h": p24,
                    "_cg_volume_usd": vol,
                })
            return out
        except Exception as e:
            logger.warning(f"coins-price-change ?啣虜: {e}")
            return []

    # ?? Step 0嚗漱???賢??殷???銵?雿??撟?車?蕪??嚗????????????????
    # ?芸? Binance / Bybit / OKX 銝之蝝?撖漱???偶蝥?蝝蝷??Ｕ?
    # ???蝯?銝?隞?馳?蟡?/ ?⊥? / ???疏嚗?甇文?嗅???撖疏撟????    # BingX/Bitget ??PLTR?ME?K50 蝑誨撟????嚗????文憭?    _supported_whitelist: set = set()

    def _fetch_supported_whitelist() -> set:
        # 蝝?撖漱??嚗inance / Bybit / OKX ??銝隞?馳?蟡冽??
        _TARGET_EXCHANGES = {"Binance", "Bybit", "OKX"}
        try:
            _respect_coinglass_rate_limit()
            r = requests.get(
                f"{CG_API_BASE}/api/futures/supported-exchange-pairs",
                headers={"CG-API-KEY": CG_API_KEY, "accept": "application/json"},
                timeout=15
            )
            if r.status_code != 200:
                logger.warning(f"[exchange-pairs??] HTTP {r.status_code}嚗??嚗?券??靽風")
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
                f"[exchange-pairs? 鈭斗?撠?頛 {len(wl)} ?馳蝔?"
                f"{ex_summary}嚗?"
            )
            return wl
        except Exception as e:
            logger.warning(f"[exchange-pairs??] ?啣虜: {e}嚗??嚗?券??靽風")
            return set()

    _supported_whitelist = _fetch_supported_whitelist()

    # ?? Step 1嚗oins-markets嚗op 100嚗葆摰 OI/Price ?豢?嚗??????????????
    result_markets = _try_coins_markets()

    # ?? Step 2嚗oins-price-change嚗?馳蝔桀?潸???閮箸 + 鋆?嚗??????????
    result_pc = _try_coins_price_change()
    if result_pc:
        _pc_sample_keys = list(result_pc[0].keys()) if isinstance(result_pc[0], dict) else []
        logger.info(f"[CoinGlass-First] coins-price-change ? {len(result_pc)} ?馳蝔?| 擐?{_keys}")
    else:
        logger.warning("[CoinGlass-First] coins-price-change ?∪??單??")

    # ?? Step 3嚗?頝臬?雿蛛????皞?????賢??桅??嚗??????????????????????
    seen_syms: set = set()
    result: List[Dict] = []
    mkt_filtered = 0  # markets top-100 鋡怎??蕪???
    pc_filtered = 0   # price-change 鋡怎??蕪???
    def _wl_pass(sym: str) -> bool:
        """?賢??格炎?伐?sym_base 敹???Binance/Bybit/OKX ?偶蝥?蝝??桀??"""
        if not _supported_whitelist:
            return True  # ?賢??株??亙仃???曇?嚗???靽風
        base = sym.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
        return base in _supported_whitelist

    # markets top-100 ?見憟?嚗LTR/HK50 ?航??Hyperliquid OI ????嚗?
    for item in result_markets:
        sym = item.get("symbol", "")
        if not sym or sym in seen_syms:
            continue
        if not _wl_pass(sym):
            mkt_filtered += 1
            continue
        seen_syms.add(sym)
        result.append(item)

    # price-change ???馳蝔殷??見憟?
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
        logger.info(f"[?賢??氣?侷 ?{_filtered} ????鞎典馳撟?車嚗arkets={mkt_filtered} | pc={pc_filtered}嚗yperliquid?∠巨/?/??蝑?")

    # supported-coins 銝剖??芰??亦???撟?車嚗?? stub嚗Ⅱ靽閬?嚗?
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
        logger.info(f"[CoinGlass-First] supported-coins 鋆? {stub_added} ??stub 撟?車嚗 price ?豢?嚗?仿?OI ??嚗?")

    markets_passed = len(result_markets) - mkt_filtered
    logger.info(
        f"[CoinGlass-First] 銝楝?蔥摰? ??蝮質? {len(result)} ?銝撟?車"
        f"{_passed} | pc鋆?={pc_added} | stub={stub_added} | ?賢??桅?瞈?{wl_filtered}嚗?"
    )
    return result


def fetch_position_change():
    """
    ??H MTF ?惜瞍?蝑?葉?郭畾菜????蜓瘚???    瞍?嚗?H OI/Price 憭扳撅?? ??30m OI 蝣箄?撱嗥?????15m OI ?剜?蝯? ??5m OI 蝎暹??脣暺?    閮?嚗? 蝣箏?蝐Ⅳ嚗?撅文?荔?嚚??瞏璈?嚗??Ｗ?頦?/ ??賊?摨???    """
    global _coinglass_oi_first_failure_logged
    _coinglass_oi_first_failure_logged = False

    # ??函????瘥憚????綽?靘踵 GitHub Actions ?亥?閮箸嚗?
    _cb_cnt = _circuit_breaker.get("consecutive_429", 0)
    if _cb_is_tripped():
        logger.warning(f"[??劾?沘 ?祈憚隞?MAX_WORKERS=1 ?桀銵?璅∪???{_cnt}嚗?")
    elif _cb_is_warned():
        logger.warning(f"[??兩?儭 ?祈憚隞?MAX_WORKERS=2 霅行?璅∪???{_cnt}嚗?")
    else:
        logger.info(f"[??兩?] 甇?虜璅∪?{_cnt}嚗?")

    logger.info("?? 撅勗祠撟??摰嗥?? ?? | 蝝?CoinGlass 璅∪? | 1H MTF ?惜瞍???")

    # ????????????????????????????????????????????????????????
    # 瞍? Step 0嚗???????蝝?CoinGlass 璅∪?嚗?
    # ????????????????????????????????????????????????????????
    logger.info("?? [??瞍?] Step 0嚗? CoinGlass 璅∪?嚗?????漱??K蝺?OI嚗?靘 CoinGlass API")

    # ????????????????????????????????????????????????????????
    # 瞍? Step 1嚗oinGlass ?典??湔??撣嗅????? 300~500 ?馳蝔殷?
    # ????????????????????????????????????????????????????????
    all_symbols_data = fetch_coinglass_coins_markets()
    if not all_symbols_data:
        logger.warning("[瞍?] coins-markets 憭望?嚗?閰?coins-price-change ?")
        all_symbols_data = fetch_coins_price_change()
        if all_symbols_data:
            logger.info(f"[?? coins-price-change ?? {len(all_symbols_data)} ?馳蝔?")
    if not all_symbols_data:
        send_telegram_message("?? ?⊥???撟?車瞍脰?鞈?嚗?蝔??岫??, TG_THREAD_IDS['position_change']")
        return
    logger.info(f"?? [瞍? 1] CoinGlass ?函雯 {len(all_symbols_data)} 撟?車")

    # ?? ?格活餈游?摰??拐辣鈭?BTC憭抒??4h敹怠? ?????????????????????????????????
    global _btc_30m_pct, _btc_1h_pct
    _btc_30m_pct = None
    _btc_1h_pct = None
    coinglass_24h_map: Dict[str, float] = {}
    active_symbols: List[Dict] = []
    for coin in all_symbols_data:
        sym_raw = normalize_symbol(coin) or ""
        clean_sym = sym_raw.replace("USDT", "").replace("-", "").replace("_", "").upper()

        # ??BTC 憭抒?啣?
        if clean_sym == "BTC" and _btc_30m_pct is None:
            _btc_30m_pct = extract_price_change_30m(coin)
            _btc_1h_pct_raw = coin.get("price_change_percent_1h")
            try:
                _btc_1h_pct = float(_btc_1h_pct_raw) if _btc_1h_pct_raw is not None else None
            except (TypeError, ValueError):
                _btc_1h_pct = None
            logger.info(f"?? [憭抒瞈曄雯] BTC 30m {(_btc_30m_pct or 0):+.2f}%  1H {(_btc_1h_pct or 0):+.2f}%")

        # ??24h 瞍脰?撟翰??
        pct24 = extract_price_change_24h(coin)
        if pct24 is not None and clean_sym:
            coinglass_24h_map[clean_sym] = pct24

        active_symbols.append(coin)

    if not coinglass_24h_map:
        coinglass_24h_map = _fetch_coinglass_24h_map()

    # ????????????????????????????????????????????????????????
    # Plan B嚗ingX 瘞貊??? 24h USDT ?漱?潘??嚗??CoinGlass ?∟???撟?車嚗?
    # ?桐? API call嚗仃?????蝛?dict 銝蔣?蹂蜓瘚?
    # ????????????????????????????????????????????????????????
    _binance_vol_map: Dict[str, float] = fetch_bingx_futures_24h_vol()

    # ????????????????????????????????????????????????????????
    # 瞍? Step 4嚗?鈭文潮?蝭抬?銝楝靘?嚗oinGlass A ??Binance B ??敺?K 蝺摯蝞?C嚗?
    # 閬?嚗?    #   combined_vol ??MTF_VOLUME_MIN_USD ???曇?嚗?瑼餌?撣豢?批嚗?閮?5M嚗?    #
    combined_vol = 0
    # ??A+B ?鞈? ???曇?嚗? K 蝺摯蝞?Plan C嚗?    #   0 < combined_vol < MTF_VOLUME_MIN_USD ??蝣箄?瘚??找?頞????蕪
    # ????????????????????????????????????????????????????????
    VOLUME_PREFILTER_MIN_USD = MTF_VOLUME_MIN_USD  # 敺虜?貉????身 5M嚗?券??典虜?詨?隤踵嚗?
    active_above_volume: List[Dict[str, Any]] = []
    vol_cg = 0         # Plan A (CoinGlass) ???? ??MTF_VOLUME_MIN_USD
    vol_binance = 0    # Plan B (BingX?) 鋆?銝???MTF_VOLUME_MIN_USD
    vol_no_data = 0    # A+B ?鞈? ???曇?蝑?Plan C
    vol_below = 0      # 蝣箄?銝雲?瑼????蕪

    for coin in active_symbols:
        # ?? Plan A嚗oinGlass ?漱???????????????????????????????
        cg_vol = coin.get("_cg_volume_usd")
        try:
            cg_vol = float(cg_vol) if cg_vol is not None else 0.0
        except (TypeError, ValueError):
            cg_vol = 0.0

        # ?? Plan B嚗inance ?嚗oinGlass ?∟???雿輻嚗??????????
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
            # A+B ?鞈? ???曇?嚗? enrichment ?挾 Plan C嚗 蝺摯蝞?鋆?
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
        f"?? [瞍? 4] ?漱?潛祟???{MTF_VOLUME_MIN_USD/1e6:.1f}M: ?? {len(active_above_volume)} ??"
        f"CoinGlass: {vol_cg} | BingX?: {vol_binance} | 敺蝺摯蝞? {vol_no_data} | 瘛掠[蝣箄?<{MTF_VOLUME_MIN_USD/1e6:.1f}M]: {vol_below}嚗?"
    )

    # ?? Step 5嚗?摨?+ ??賊?嚗? 50 ?箏?嚗擗璈?憭見?改??????????????????
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
            f"?漱??蝭拙???{len(active_above_volume)} ???祈憚???? {MAX_OI_SYMBOLS} ?誑蝣箔?皞??冽 "
            f"(??50 靘?鈭日??箏?嚗擗璈璅?"
        )
    
    long_open = []
    long_close = []
    short_open = []
    short_close = []
    
    processed_count = 0
    oi_success_count = 0
    oi_fail_count = 0
    
    # 銝西????蔭嚗?皞?擃璅∪?嚗?閮?12 ?瑁?蝺???典????芸?? 1
    MAX_WORKERS = _cb_get_max_workers(default=15)
    _cb_tripped = _circuit_breaker.get("tripped", False)
    logger.info(f"[???啣?] CG_API_KEY={'撌脰身摰?'+CG_API_KEY[:6]+'...)' if CG_API_KEY else '?閮剖?'}"
                f" | MAX_WORKERS={MAX_WORKERS} | ???{'???芋撘?' if _cb_tripped else '?迤撣?'}")
    if MAX_WORKERS == 1:
        logger.warning("[??其??其葉] MAX_WORKERS 撌脤???1嚗頛芣?桀銵?靽風璅∪?")
    start_time = time.time()
    MAX_EXECUTION_TIME = 16 * 60  # 撘瑕蝯?銝? 16 ??嚗???霅瑞嚗?    
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    broke_early = False
    try:
        future_to_coin = {executor.submit(process_single_symbol, coin): coin for coin in target_symbols}
        completed = 0
        for future in as_completed(future_to_coin):
            elapsed_time = time.time() - start_time
            if elapsed_time > MAX_EXECUTION_TIME:
                logger.warning(f"撌脤? {MAX_EXECUTION_TIME/60:.0f} ??銝?嚗????蒂?冽嚗歇?? {processed_count} ??")
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
                logger.info(f"???脣漲: {completed}/{len(target_symbols)} | 撌脩?? {elapsed_time/60:.1f} ??")
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
                    'oiChange30m': oi_change,    # ???詨捆
                    'priceChange24h': price_change_24h,
                    'price_change_percent_1h': price_change_1h,
                    '_cg_volume_usd': result.get('_cg_volume_usd'),
                }
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
        executor.shutdown(wait=not broke_early)  # ??蝯???蝑??芸??遙??隞亙皞??冽
    
    total_time = time.time() - start_time
    in_four = len(long_open) + len(long_close) + len(short_open) + len(short_close)
    below_oi_threshold = oi_success_count - in_four
    logger.info(
        f"?? [Step1 1H OI??] ??{processed_count} 撟?| ?? {oi_success_count} 憭望? {oi_fail_count} | ?冽? {total_time/60:.1f}min | "
        f"?仿: 憭? {len(long_open)} 憭像 {len(long_close)} 蝛粹? {len(short_open)} 蝛箏像 {len(short_close)} "
        f"嚗??{_four} / OI?? {oi_success_count}嚗?"
    )

    # ?? OI ?瑼餉?蝞?隞交頛芸?憿見?祉? |OI 30m| ??閮?撟喳???皞榆嚗?
    # 4 ?祕??瑼?= max(?箏? 4 ??瑼? mean + 1?)
    # 5 ?祕??瑼?= max(?箏? 5 ??瑼? mean + 2?)
    global _dynamic_oi_mean_30m, _dynamic_oi_std_30m, _dynamic_oi_4star, _dynamic_oi_5star, _dynamic_oi_sample_size
    oi_samples: List[float] = []
    for _lst in (long_open, long_close, short_open, short_close):
        for _x in _lst:
            try:
                v = float(_x.get("oiChange1h") or _x.get("oiChange30m") or 0.0)
            except (TypeError, ValueError):
                continue
            if v == v:
                oi_samples.append(abs(v))
    _dynamic_oi_sample_size = len(oi_samples)
    if _dynamic_oi_sample_size >= 10:
        arr = np.array(oi_samples, dtype=float)
        # ?餅扔蝡臬潘??芣??95th ?曉?雿??脫迫?桐??啣虜撟??憒?OI +621%嚗憌?瑼?
        cap_95 = float(np.percentile(arr, 95))
        arr_clean = np.clip(arr, 0, cap_95)
        _dynamic_oi_mean_30m = float(arr_clean.mean())
        _dynamic_oi_std_30m = float(arr_clean.std())
        _dynamic_oi_4star = max(OI_FOR_4_STAR, _dynamic_oi_mean_30m + 1.0 * _dynamic_oi_std_30m)
        _dynamic_oi_5star = max(OI_FOR_5_STAR, _dynamic_oi_mean_30m + 2.0 * _dynamic_oi_std_30m)
        _outlier_count = int(np.sum(arr > cap_95))
        logger.info(
            f"???I?瑼颯{_size} ???芣 {_outlier_count} ?扔蝡臬潑{cap_95:.1f}%嚗?"
            f" | {_mean_30m:.2f}% ?={_dynamic_oi_std_30m:.2f}% "
            f"??4?{_dynamic_oi_4star:.2f}% 5?{_dynamic_oi_5star:.2f}%"
        )
    else:
        # 璅??訾?頞喉?鋆? CoinGlass Top-20 OI ?豢?
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
        except Exception as _e:
            logger.warning(f"???I?瑼颯op-20 鋆?憭望?: {_e}")

        if len(oi_samples) >= 10:
            arr = np.array(oi_samples, dtype=float)
            cap_95 = float(np.percentile(arr, 95))
            arr_clean = np.clip(arr, 0, cap_95)
            _dynamic_oi_mean_30m = float(arr_clean.mean())
            _dynamic_oi_std_30m = float(arr_clean.std())
            _dynamic_oi_4star = max(OI_FOR_4_STAR, _dynamic_oi_mean_30m + 1.0 * _dynamic_oi_std_30m)
            _dynamic_oi_5star = max(OI_FOR_5_STAR, _dynamic_oi_mean_30m + 2.0 * _dynamic_oi_std_30m)
            _dynamic_oi_sample_size = len(oi_samples)
            logger.info(
                f"???I?瑼?鋆?敺??{_size} ??| 弮={_dynamic_oi_mean_30m:.2f}% ?={_dynamic_oi_std_30m:.2f}% "
                f"??4?{_dynamic_oi_4star:.2f}% 5?{_dynamic_oi_5star:.2f}%"
            )
        else:
            _dynamic_oi_mean_30m = None
            _dynamic_oi_std_30m = None
            _dynamic_oi_4star = None
            _dynamic_oi_5star = None
            logger.info(
                f"???I?瑼颯見?砌?頞???{len(oi_samples)} ??嚗窒?典摰?{_STAR}% 5?{OI_FOR_5_STAR}%"
            )

    # ?芰絞閮?閮? 4 ?誑銝?|OI| < 撖阡? 4 ??瑼?????top??頝?蝥?蝞?
    oi_threshold_4 = _dynamic_oi_4star if (_dynamic_oi_4star is not None and _dynamic_oi_sample_size >= 10) else OI_FOR_4_STAR
    long_open = [x for x in long_open if abs(x.get('oiChange30m') or 0) >= oi_threshold_4]
    long_close = [x for x in long_close if abs(x.get('oiChange30m') or 0) >= oi_threshold_4]
    short_open = [x for x in short_open if abs(x.get('oiChange30m') or 0) >= oi_threshold_4]
    short_close = [x for x in short_close if abs(x.get('oiChange30m') or 0) >= oi_threshold_4]
    # ?? ??1H OI 蝯??潭?????3??OI頞之=銝餃???頞?蝣綽?????????????????
    # ?桃?嚗??????????撟??銝?冽??見
    long_open.sort(key=lambda x: abs(x.get('oiChange1h') or x.get('oiChange30m') or 0), reverse=True)
    long_close.sort(key=lambda x: abs(x.get('oiChange1h') or x.get('oiChange30m') or 0), reverse=True)
    short_open.sort(key=lambda x: abs(x.get('oiChange1h') or x.get('oiChange30m') or 0), reverse=True)
    short_close.sort(key=lambda x: abs(x.get('oiChange1h') or x.get('oiChange30m') or 0), reverse=True)
    top_long_open  = long_open[:3]
    top_long_close = long_close[:3]
    top_short_open = short_open[:3]
    top_short_close = short_close[:3]
    # 閮????交???1=OI?憭改?嚗?敺?閰?雿輻
    for _cat_list in (top_long_open, top_long_close, top_short_open, top_short_close):
        for _rank_i, _item in enumerate(_cat_list):
            _item["_oi_rank"] = _rank_i + 1
    logger.info(
        f"?? [TOP?] 憭? {len(top_long_open)} 憭像 {len(top_long_close)} 蝛粹? {len(top_short_open)} 蝛箏像 {len(top_short_close)}嚗???3嚗? ?? enrichment"
    )

    # ????????????????????????????????????????????????????????
    # Enrichment嚗敹???CoinGlass ?銵?璅?+ 鞈?鞎餌?嚗?
    # ????????????????????????????????????????????????????????
    _cg_fr_map: Dict[str, float] = _fetch_funding_rate_map()
    logger.info(f"[FR?寞活] CoinGlass Funding Rate ??摰?嚗 {len(_cg_fr_map)} ?馳蝔?")

    all_top = []
    for item, cat in [(x, "long_open") for x in top_long_open] + [(x, "long_close") for x in top_long_close] + [(x, "short_open") for x in top_short_open] + [(x, "short_close") for x in top_short_close]:
        sym = item.get("symbol", "")

        # ?? 暺??桀?蝵桅?瞈橘???K 蝺????嚗???API 甈⊥嚗??????????????????????
        _sym_base = sym.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
        # 隞?馳?蟡刻???迎?PLTRSTOCK / MASTOCK / NVDASTOCK 蝑誑 STOCK 蝯偏?撘?
        _is_tokenized_stock = _sym_base.endswith("STOCK") or _sym_base.endswith("TOKEN")
        if _sym_base in SYMBOL_BLACKLIST or _is_tokenized_stock:
            logger.info(f"[暺??氣?侷 {sym} ??enrichment ?撠?嚗歲??K 蝺???")
            continue

        # ?銵?璅?CoinGlass K 蝺?蝞?RSI / ATR / 蝯?擃?暺?
        # 嚗fetch_cg_klines_and_calc ?折撌脫? _respect_coinglass_rate_limit ???⊿?憿? sleep嚗?
        tech = calculate_technicals(sym)

        # ?? Plan C嚗 蝺摯蝞?鈭文潘?鋆? CoinGlass + Binance ?鞈??馳蝔殷???????
        if item.get("_vol_need_planc") and tech:
            kline_vol_est = tech.get("kline_vol_usd_24h")
            if kline_vol_est and kline_vol_est > 0:
                item["_volume_usd"] = kline_vol_est
                item["_cg_volume_usd"] = kline_vol_est
                item["_vol_source"] = "K蝺摯蝞?"
                item.pop("_vol_need_planc", None)
                logger.debug(f"[Plan C] {sym}: K蝺摯蝞?24h ?漱??{kline_vol_est/1e6:.2f}M USD")

        # 鋆? 1H/4H OI嚗nrichment ?剁??? top 撠??撟?車?澆嚗?
        _oi_tf = _fetch_oi_multi_tf(sym)
        item["oi_change_1h_pct"] = _oi_tf.get("1h")
        item["oi_change_4h_pct"] = _oi_tf.get("4h")

        # 4H 摰?憭拙?EMA20 + RSI嚗oogle 撱箄降?啣?嚗?雿??抵?閮?雿蕪蝬脤?瘀?
        _tech_4h = _fetch_cg_klines_and_calc(sym, interval="4h", limit=20)
        _ema20_4h = _tech_4h.get("ema20_close") if _tech_4h else None
        _rsi_4h   = _tech_4h.get("rsi")        if _tech_4h else None
        # ?斗?曉?臬蝡? 4H EMA20嚗?/?憭拙?
        # CoinGlass ??price ?馳?芸???CoinGlass嚗ingX-only 撟??price=None嚗?
        # ??1H K蝺?歹?tech.current_price嚗??嚗Ⅱ靽?4H EMA 瘥?銝仃??
        _cur_price_prelim = item.get("price") or (tech.get("current_price") if tech else None)
        _is_above_4h_ema  = (
            bool(_cur_price_prelim > _ema20_4h)
            if (_cur_price_prelim and _ema20_4h and _ema20_4h > 0)
            else None
        )

        # 鞈?鞎餌?嚗oinGlass ?寞活銵剁?蝝?CoinGlass 璅∪?嚗????BingX嚗?
        _base_fr = sym.replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
        funding_rate = _cg_fr_map.get(_base_fr)

        # 24h 瞍脰?撟?
        clean_base = sym.replace("USDT", "").replace("-", "").upper()
        price_24h = item.get("priceChange24h") if isinstance(item.get("priceChange24h"), (int, float)) else None
        if price_24h is None:
            price_24h = coinglass_24h_map.get(clean_base)

        # 1H 頞典?孵?嚗TF 瞈曄雯嚗?
        price_1h = item.get("priceChange1h")
        try:
            price_1h = float(price_1h) if price_1h is not None else None
        except (TypeError, ValueError):
            price_1h = None

        # ?情??憿?15m ?單? + 1h 頞典瞈曄雯嚗?
        classified = _classify_signal_and_tier(
            item, cat, tech, funding_rate,
            price_chg_24h=price_24h,
            price_chg_1h=price_1h,
        )
        if classified is None:
            logger.debug(f"[MTF] 頝喲? {sym}: OI<{OI_THRESHOLD_30M}% ??Price<{PRICE_THRESHOLD_30M}%")
            continue
        signal_label, zone, stars, rsi_desc, reason = classified
        rsi_val = tech.get("rsi") if tech else None
        atr_val = tech.get("atr") if tech else None

        # ?? ???脰風嚗nti-Manipulation Gate嚗????????????????????????????
        # ?曉??敺?撌脩?舐?撖西??嚗?剖?嚗???摰嗅?蝒/?恍??孵噩
        _manip_reason = _check_manipulation_risk(item, tech, atr_val, category=cat)
        if _manip_reason:
            logger.info(
                f"[???] {sym}{cat}嚗???{_reason}"
            )
            continue

        # ?? K 蝺擙桀漲撽?嚗甇?BingX/Bybit ????剖??湧脣?孵??撌殷???????????
        # ??K 蝺??唳?方? CoinGlass ?單??曉?榆 > 3%嚗誨銵?K 蝺歇??嚗?憒馳蝔桀??游
        # 雿?API 隞??喳???嗥嚗??渡??銵?璅?典仃???湔頝喲?甇方???
        _cg_price = item.get("price")  # CoinGlass ?單??曉嚗??望???嚗??單?嚗?
        _kline_close = tech.get("current_price") if tech else None
        if _cg_price and _kline_close and _cg_price > 0 and _kline_close > 0:
            _kline_divergence = abs(_kline_close - _cg_price) / _cg_price
            if _kline_divergence > 0.03:
                logger.warning(
                    f"[K蝺???儭 {sym}: K{_close:.6f} ??CoinGlass?曉 "
                    f"{_cg_price:.6f} ?榆 {_kline_divergence:.1%}嚗?3%嚗?K蝺???頝喲?甇方???"
                )
                continue

        # ?曉嚗???CoinGlass ?單??曉嚗 蝺?支??
        _cur_price = _cg_price if (_cg_price and _cg_price > 0) else _kline_close

        # ????????????????????????????????????????????????????????????????????
        # 瞍?撘辣??API 隢?嚗azy Fetching嚗?鞎澆? 300甈??? ?平璅???
        # ???批嚗?蝑?瘙? sleep(0.2) = 5甈?蝘?摰銝孛??429
        # 蝑嚗?蝚血?璇辣撠梁???continue嚗?瘚芾祥敺? API 憿漲
        # ????????????????????????????????????????????????????????????????????

        # ?? Step 2嚗? 30m OI嚗??喳??孵?銵??祟 ????????????????????????????
        time.sleep(0.2)
        _oi_30m = fetch_oi_change_tf(sym, "30m")
        _p_30m  = item.get("priceChange30m")

        # 30m ?情??憿?銵閮?嚗?靘陷憭?賣嚗?
        if _oi_30m is not None:
            if _oi_30m > 0:
                _cat_30m_prelim = "long_open"  if (_p_30m is None or _p_30m >= 0) else "short_open"
            else:
                _cat_30m_prelim = "short_cover" if (_p_30m is not None and _p_30m > 0) else "long_close"
        else:
            _cat_30m_prelim = None

        logger.info(
            f"[Step2 30m OI] {sym}: OI={(_oi_30m or 0):+.2f}% ??{_cat_30m_prelim or 'N/A'}"
            f"  (1H={cat})"
        )

        # Step 2 銵??餅嚗蜓??????蝭??API嚗?交璉?
        _is_1h_bull_ctx = cat in ("long_open", "short_cover")
        _is_1h_bear_ctx = cat in ("short_open", "long_close")
        if _cat_30m_prelim is not None:
            if (_is_1h_bull_ctx and _cat_30m_prelim == "short_open") or \
               (_is_1h_bear_ctx and _cat_30m_prelim == "long_open"):
                logger.info(
                    f"[Step2????愍 {sym}: 30m={_cat_30m_prelim} ??1H={cat} "
                    f"?孵?銵?嚗???15m+5m API嚗璉?"
                )
                continue

        # ?? Step 3 & 4嚗?5m + 5m OI嚗????? Step 2 ?扔撠撟?車嚗??????????
        time.sleep(0.2)
        _oi_15m_result = fetch_oi_change_tf(sym, "15m", return_ts=True)
        if isinstance(_oi_15m_result, tuple):
            _oi_15m, _oi_15m_candle_ts = _oi_15m_result
        else:
            _oi_15m, _oi_15m_candle_ts = _oi_15m_result, 0
        logger.info(f"[Step3 15m OI] {sym}: OI={(_oi_15m or 0):+.2f}%")
        time.sleep(0.2)
        _oi_5m  = fetch_oi_change_tf(sym, "5m")
        logger.info(f"[Step4  5m OI] {sym}: OI={(_oi_5m or 0):+.2f}%")

        # ?? MTF 閮???嚗?潛?嚗?蝚血? A/B ??None ??continue嚗??????????????
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

        # ?湔閮??蕪嚗one = 撘梯????孵???嚗祐蝻箏瞈怎?交璉?
        if _mtf_result is None:
            logger.info(
                f"[?湔?蕪? {sym}: 銝泵?Ⅱ摰?蝣?摰??萱璇辣"
                f"嚗?H={cat}, 30m={_cat_30m_prelim}, OI15m={_oi_15m}, OI5m={_oi_5m}嚗??暹?"
            )
            continue

        # ?? 鞈?鞎餌?憭征憯??蕪 ??????????????????????????????????????????????
        # ??嚗祥??鞎?= 蝛粹?臭?鞎餌?蝯血???= 蝛粹?其?憯?
        #
        # ???征?◢?芷?嚗?蝛綽?嚗?憭??舫?憸剁?蝛粹鋆??
        #
        # 鞎餌??迤 = 憭?臭?鞎餌?蝯衣征??= 憭?其?憯?
        #
        # ?????◢?芷?嚗??剔????殷?嚗?蝛箸??舫?憸?
        _effective_version = _mtf_result.get("version", "potential")
        _fr_crowding_note = ""
        if funding_rate is not None and isinstance(funding_rate, (int, float)):
            _fr_abs = abs(funding_rate)
            _is_short_sig = cat in ("long_close", "short_open")
            _is_long_sig  = cat in ("long_open", "short_close")
            _fr_pct_str   = f"{funding_rate * 100:+.4f}%"

            if _is_short_sig and funding_rate < -FR_SHORT_SQUEEZE_BLOCK:
                # 鞎餌? < -0.3%嚗征?剖?????征憸券璆菟?嚗???蝛箄???
                logger.info(
                    f"[FR撠??] {sym}: ?征閮? 鞎{_str}"
                    f"嚗征?剖??????-{FR_SHORT_SQUEEZE_BLOCK*100}%嚗?撠?"
                )
                continue
            elif _is_short_sig and funding_rate < -FR_SHORT_SQUEEZE_RISK:
                # 鞎餌? -0.1%~-0.3%嚗征?剖??郎???征閮???
                _effective_version = "tier2"
                _fr_crowding_note = f"蝛粹憯?霅衣內嚗{_str}嚗?蝛粹◢?芸?擃?"
                logger.info(
                    f"[FR????] {sym}: ?征閮? 鞎{_str} 蝛粹憯? ???閫撖???"
                )
            elif _is_long_sig and funding_rate > FR_LONG_LIQUIDATION_BLOCK:
                # 鞎餌? > +0.5%嚗??剖?????◢?芷?嚗???憭???
                logger.info(
                    f"[FR撠??] {sym}: ??閮? 鞎{_str}"
                    f"嚗??剖??????+{FR_LONG_LIQUIDATION_BLOCK*100}%嚗?撠?"
                )
                continue
            elif _is_long_sig and funding_rate > FR_LONG_LIQUIDATION_RISK:
                # 鞎餌? +0.2%~+0.5%嚗??剖??郎????閮???
                _effective_version = "tier2"
                _fr_crowding_note = f"憭憯?霅衣內嚗{_str}嚗??◢?芸?擃?"
                logger.info(
                    f"[FR????] {sym}: ??閮? 鞎{_str} 憭憯? ???閫撖???"
                )

        all_top.append({
            **item,
            "priceChange24h": price_24h,
            "priceChange1h": price_1h,
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
            "vwap_2h": tech.get("vwap_2h") if tech else None,
            # _scan_ts = 1H OI 擐活?菜葫??嚗rocess_single_symbol ??嚗?靽?????
            # ??item ?⊥迨甈?嚗?頝臬?嚗?隞亦????頞?
            "_detected_ts": item.get("_scan_ts") or time.time(),
            # 15m OI K蝺絲憪???CoinGlass 鞈??祈澈???嚗誨銵冽???????蝒?
            "_oi_15m_candle_ts": locals().get("_oi_15m_candle_ts") or 0,
            # MTF ?惜?豢?
            "oiChange_30m": _oi_30m,
            "oiChange_15m": _oi_15m,
            "oiChange_5m":  _oi_5m,
            # MTF 閮??嚗歇憟 FR 憯??蕪嚗effective_version ?航??嚗?
            "signal_version":  _effective_version,
            "signal_subtype":  _mtf_result.get("subtype", "") or _fr_crowding_note,
            "mtf_desc":        _mtf_result.get("mtf_desc", ""),
            "mtf_oi_line":     _mtf_result.get("mtf_oi_line", ""),
            "mtf_aligned":     _mtf_result.get("aligned_count", 1),
            "reversal_hint":   _mtf_result.get("reversal_hint", ""),
            # 4H 摰?憭拙?頛鞈?嚗?
            "ema20_4h":        _ema20_4h,
            "rsi_4h":          _rsi_4h,
            "is_above_4h_ema": _is_above_4h_ema,
        })
        _ver_tag = (
            "?Ⅱ摰?蝣潘??萎?閫?" if _effective_version == "confirmed"
            else f"??閫撖???{_fr_crowding_note or _mtf_result.get('subtype','')})" if _effective_version == "tier2"
            else f"?瞏{_result.get('subtype','')})"
        )
        logger.info(f"[Enrichment] {sym} 撌脣???all_top{_val} ATR={atr_val} ?曉={_cur_price} | {_ver_tag} | {reason}")

    # ?釭???嚗TR=None ??K 蝺?豢?嚗L/TP/RSI ?瘜?蝞?銝??
    pre_quality = len(all_top)
    all_top = [x for x in all_top if x.get("atr") is not None]
    skipped_no_kline = pre_quality - len(all_top)
    if skipped_no_kline > 0:
        logger.info(f"[?釭???] 瘛掠 {skipped_no_kline} ??ATR=None嚗蝺?豢?撠馳嚗??拚? {len(all_top)} ????")

    # ?釭??嚗?鈭文潔??芰Ⅱ隤?銝楝?鞈?嚗oinGlass / BingX / K蝺摯蝞憭望?嚗?
    # ??撟??冽???隞乓?K蝺摯蝞?蝢拇銵?嚗? Plan C 銋?隡啣靘?    # ???⊥?蝣箄?瘚??折?璅?銝?哨??踹??典??鈭文??⊥??閮?
    pre_vol = len(all_top)
    all_top = [x for x in all_top if not x.get("_vol_need_planc")]
    skipped_no_vol = pre_vol - len(all_top)
    if skipped_no_vol > 0:
        logger.info(f"[?釭??] 瘛掠 {skipped_no_vol} ??鈭文潭蝣箄?嚗?頝臬??∟???嚗擗?{len(all_top)} ????")

    # ?漱憿?甇伐?敺?_cg_volume_usd 撖怠靘?凋蝙?剁?
    for x in all_top:
        x["volume_usd"] = x.get("_volume_usd") or x.get("_cg_volume_usd") or 0

    _confirmed_cnt = sum(1 for x in all_top if x.get("signal_version") == "confirmed")
    _tier2_cnt     = sum(1 for x in all_top if x.get("signal_version") == "tier2")
    _potential_cnt = len(all_top) - _confirmed_cnt - _tier2_cnt
    logger.info(
        f"[Enrichment 摰?] {len(all_top)} ???脣?冽瘚?"
        f"嚗?蝣箏?蝐Ⅳ {_confirmed_cnt} | ?瞏璈? {_potential_cnt} | ??閫{_cnt}嚗?"
    )
    if len(all_top) == 0:
        logger.info(f"?祈憚?∠泵??隞嗉???1H OI?{OI_THRESHOLD_1H}% & ?漱?潑{MTF_VOLUME_MIN_USD/1e6:.0f}M USD & MTF?望?芷?璅?")

    # ?瑕閬?嚗?銝撟?2h ?批??孵?銝?銴嚗?H ?澆?嚗?餅??????瘀?
    COOLDOWN_HOURS = 4   # ?馳???4h ?瑕嚗oogle 撱箄降嚗?H 瘜Ｘ挾蝑?雿喲???
    HISTORY_HOURS = 24   # ?瑕甇瑕靽? 24h嚗??亥????

    def _item_direction(x: Dict) -> str:
        """?芸???憭?蝛箝? build_report 撌脰身摰? dir 甈?嚗甈∠ category嚗?敺?閫?? signal_label??"""
        # 1. ??舫?嚗uild_report_message_tiered ?冽???剝??桐??湔閮剖???dir
        d = (x.get("dir") or "").strip()
        if d in ("憭?", "蝛?"):
            return d
        # 2. 敺?category ?斗嚗ong_open / short_close = ??閮?嚗?
        cat = (x.get("category") or x.get("entry_category") or "").strip()
        if cat in ("long_open", "short_close"):
            return "憭?"
        if cat in ("short_open", "long_close"):
            return "蝛?"
        # 3. fallback嚗?閰西圾??signal_label嚗??萄??游?嚗?
        sig = x.get("signal_label") or ""
        bull_kws = ("??", "餈賢?", "?征", "??", "憭?亙", "蝛粹撟喳?", "撘瑕??", "Long")
        return "憭?" if any(kw in sig for kw in bull_kws) else "蝛?"

    def _cooldown_symbol(s: str) -> str:
        """?瑕 key 蝯曹??具馳蝔桀摨?撠??踹? BNLIFE / BNLIFEUSDT / BNLIFE-USDT 鋡怎銝?撟??銴??"""
        if not s:
            return ""
        return str(s).replace("USDT", "").replace("-", "").replace("_", "").strip().upper()

    # ?祈憚??蝐Ⅳ??嚗銵剁?靘?湔?蝷箸?撠?嚗?憭??交頛芾? short_open/long_close ?喳?頧??嗅??函征??祈憚霈?long_open/short_close ?喳?頧?
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

    # ?瑕瑼楝敺?cron/?脩垢?啣???data/ 銝?銋??航身 SNIPER_COOLDOWN_DIR ?????桅?嚗?撠楝敺?
    _cooldown_dir = os.getenv("SNIPER_COOLDOWN_DIR")
    if _cooldown_dir:
        _cooldown_dir = Path(_cooldown_dir).resolve()
        _cooldown_dir.mkdir(parents=True, exist_ok=True)
        SNIPER_COOLDOWN_FILE = _cooldown_dir / "sniper_cooldown.json"
    else:
        SNIPER_COOLDOWN_FILE = (DATA_DIR / "sniper_cooldown.json").resolve()
    _cooldown_path_abs = str(SNIPER_COOLDOWN_FILE)
    # ?瑕 + ?冽蝝??箝銝 JSON??雿菔?撖恬??踹? CI cache ???瑼?銝?湛??瑕??剔???0 蝑?
    logger.info(f"?????頝臬?嚗???冽蝝??: {_cooldown_path_abs}")
    # 閮餃?蝺亙??渲楝敺?蝣箔? GitHub Action timeout (SIGTERM / atexit) ?撖怠?蝤?
    global _emergency_sniper_path, _emergency_sniper_state
    _emergency_sniper_path = _cooldown_path_abs
    now_ts = time.time()
    cooldown_sec = COOLDOWN_HOURS * 3600
    history_sec = HISTORY_HOURS * 3600
    history: List[Dict] = []
    push_log_signals: List[Dict] = []
    # 瑼????踹? CI ???脩???撖怠撠 JSON ??
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
                    logger.warning("????????????暹????湔霈撖恬??航摮蝡嗥憸券嚗?")
                    yield
                    break
                time.sleep(poll_interval + random.uniform(0, poll_interval))

    # ?? Gist ?芸?霈??餌???憭望???fallback ?唳??JSON ??????????
    _gist_data = _gist_load_cooldown()
    if _gist_data is not None:
        history = _gist_data.get("history") or []
        _in_window = sum(1 for e in history if isinstance(e, dict) and (now_ts - e.get("ts", 0)) <= cooldown_sec)
        logger.info(f"?瑕瑼歇霈??Gist): history {len(history)} {COOLDOWN_HOURS}h ??{_in_window} 蝑?")

    try:
        with _sniper_file_lock():
            if SNIPER_COOLDOWN_FILE.exists() and _gist_data is None:
                raw = json.loads(SNIPER_COOLDOWN_FILE.read_text(encoding="utf-8"))
                history = raw.get("history") or []
                # ?詨捆?撘??芣? last_round ????history
                if not history and raw.get("last_round"):
                    last_round = raw.get("last_round") or []
                    if last_round and isinstance(last_round[0], dict):
                        history = [{"symbol": str(p.get("symbol")), "dir": str(p.get("dir")), "ts": int(now_ts) - 3600} for p in last_round if p.get("symbol") and p.get("dir")]
                    else:
                        history = [{"symbol": str(p[0]), "dir": str(p[1]), "ts": int(now_ts) - 3600} for p in last_round if isinstance(p, (list, tuple)) and len(p) >= 2]
                logger.info(f"?瑕瑼歇霈?? {_cooldown_path_abs} | 甇瑕 {len(history)} 蝑?")
            else:
                if _gist_data is None:
                    logger.info(f"?瑕???銝??剁??祈憚?∪?駁??? {_cooldown_path_abs}")
    except Exception as e:
        history = []
        logger.warning(f"霈??餌???憭望?嚗頛芰?瑕?: {e}")

    now_tw = datetime.fromtimestamp(now_ts, tz=TAIPEI_TZ)
    _in_window = sum(1 for e in history if isinstance(e, dict) and (now_ts - e.get("ts", 0)) <= cooldown_sec)
    logger.info(f"?瑕??? {len(history)} 蝑風?{COOLDOWN_HOURS}h ??{_in_window} 蝑??馳????瑕嚗?")

    # ?瑕??嚗?撟???孵???COOLDOWN_HOURS ?批歇?券????
    cooldown_symbol_dir_4h: Set[Tuple[str, str]] = set()
    last_round_by_sym: Dict[str, str] = {}
    for e in history:
        if not isinstance(e, dict) or not e.get("symbol") or not e.get("dir"):
            continue
        s = _cooldown_symbol(str(e["symbol"]))
        d = str(e["dir"])
        if (now_ts - e.get("ts", 0)) <= cooldown_sec:
            cooldown_symbol_dir_4h.add((s, d))
        if s not in last_round_by_sym:
            last_round_by_sym[s] = d
    latest_signal_by_sym: Dict[str, Dict[str, Any]] = {}

    # ?? 暺??桐??蝺?enrichment ?歇??甈∴?甇方?蝣箔??⊥?蝬脖?擳?????????????????
    _before_bl = len(all_top)
    all_top = [
        x for x in all_top
        if _cooldown_symbol(x.get("symbol") or "").upper() not in SYMBOL_BLACKLIST
    ]
    _bl_removed = _before_bl - len(all_top)
    if _bl_removed > 0:
        logger.info(f"[暺??氣?侷 鈭??脩?? {_bl_removed} ????")

    cooled_top = []
    for x in all_top:
        sym = x.get("symbol") or ""
        if not sym:
            continue
        sym_norm = _cooldown_symbol(sym)
        cur_dir = _item_direction(x)

        # ?馳???COOLDOWN_HOURS ?折????
        if (sym_norm, cur_dir) in cooldown_symbol_dir_4h:
            logger.info(f"?瑕頝喲?: {sym_norm} ({cur_dir}) ({COOLDOWN_HOURS}h ?批?撟???孵?撌脣??")
            continue

        # ?馳???璅?憭?蝛?蝛箄?憭???
        if sym_norm in last_round_by_sym and last_round_by_sym[sym_norm] != cur_dir:
            x["direction_flip"] = last_round_by_sym[sym_norm] + "頧? + cur_dir"
        else:
            x["direction_flip"] = None
        cooled_top.append(x)

    _skipped = len(all_top) - len(cooled_top)
    if _skipped > 0:
        logger.info(f"?祈憚?瑕頝喲? {_skipped} 瑼??{COOLDOWN_HOURS}h ?找??嚗?")

    # ?? 憭??梯?撌脩宏?歹???fetch_exchange_oi_consensus API ?鞈???15m ??蝒銝泵嚗炊?文?嚗????
    # is_global_consensus 甈?靽?雿摰 False嚗s_premium 撌脖?靘陷甇斗?雿?
    if cooled_top:
        for _item in cooled_top:
            _item["is_global_consensus"] = False
            _item["volume_oi_warn"] = False

    # ?? ?冽???孵翰?改??脣?寡?憸典瘥???澆?嚗??????????????????????????
    # 雿輻???啗???隞乓??嫘??殷??隞?SL/TP 敹?敺??寧?韏瘀??? K 蝺?扎?
    # ??靽? signal_price嚗 蝺孛?潭?歹?靘??舫＊蝷箝孛?潮? vs ?曉??    # 閬?嚗??TP1 R 瘥?< 0.8 隞?”銵?撌脤??◢?望?銝?嚗?交璉??具?
    if cooled_top:
        _drop_low_r: List = []
        for _x in cooled_top:
            _sym_rt = _x.get("symbol") or ""
            _sig_price = _x.get("current_price")   # K 蝺?歹?閮?閫貊暺?
            _atr_rt = _x.get("atr")
            _x["signal_price"] = _sig_price         # 瘞賊?靽?閫貊暺?憿舐內
            if not _sig_price or not _atr_rt or _atr_rt <= 0:
                continue
            try:
                _snap = _fetch_bingx_ticker_snapshot(_sym_rt)
                if _snap and _snap.get("price") and float(_snap["price"]) > 0:
                    _live = float(_snap["price"])
                    _drift = abs(_live - _sig_price) / _sig_price
                    # ?∟??榆憭批??賣?堆??單??寞??舐?嗅祕?脣?對?
                    _x["current_price"] = _live
                    if _drift >= 0.003:   # ??.3% ??log嚗????
                        logger.info(
                            f"[?單??勗??] {_sym_rt}: 閫貊 {_sig_price:.6f} ???單? {_live:.6f}"
                            f"嚗?{_drift:.1%}嚗?"
                        )
                    # ?? TP1 憸典瘥祟?賂??箸?單??對???????????????????????????????????
                    # 敹恍摯蝞?SL 頝瘝輻閮?閫貊暺?蝯?嚗ecent_2h_high/low + 0.5%嚗?
                    # ?亙??TP1 R 瘥?< 0.8 隞?”銵?撌脣之撟宏??銝澆??
                    _is_long_rt = (_x.get("category") or "") in ("long_open", "short_close")
                    _r2h_high = _x.get("recent_high_2h")
                    _r2h_low = _x.get("recent_low_2h")
                    if _is_long_rt and _r2h_low and _r2h_low > 0:
                        _sl_est = _r2h_low * 0.995
                        _risk_est = _live - _sl_est
                    elif not _is_long_rt and _r2h_high and _r2h_high > 0:
                        _sl_est = _r2h_high * 1.005
                        _risk_est = _sl_est - _live
                    else:
                        _risk_est = _atr_rt * 1.8   # ?隡啁?
                    if _risk_est > 0:
                        _tp1_est = (_live + _risk_est) if _is_long_rt else (_live - _risk_est)
                        # 撖阡?銝◢?望??晞?脣?啣?憪?TP1 ?格??捱摰?
                        _orig_tp1_est = (_sig_price + _risk_est) if _is_long_rt else (_sig_price - _risk_est)
                        _rt_reward = (_orig_tp1_est - _live) if _is_long_rt else (_live - _orig_tp1_est)
                        _rt_r_ratio = _rt_reward / _risk_est if _risk_est > 0 else 0
                        if _rt_r_ratio < 0.8:
                            logger.info(
                                f"[雿瘥歲??儭 {_sym_rt}: ?單? TP1 R={_rt_r_ratio:.2f} < 0.8"
                                f"嚗??{_sig_price:.6f} ?單? {_live:.6f}嚗?銵?撌脤?嚗???"
                            )
                            _drop_low_r.append(_x)
            except Exception as _e:
                logger.debug(f"[?單??勗] {_sym_rt} 敹怎憭望?嚗窒??K 蝺?? {_e}")
        # 蝘駁憸典瘥?雿?閮?
        for _drop in _drop_low_r:
            if _drop in cooled_top:
                cooled_top.remove(_drop)

    # ??祕???喳?銝?????銝餃銵剁??∟????刻◤憸典瘥祟????銝嚗???
    has_any = False
    if cooled_top:
        msg, has_any, push_count, s_grade_msgs = build_report_message_tiered(cooled_top, processed_count, oi_success_count)
        if has_any:
            logger.info(
                f"??剔蜇蝯頛芣?{_count} 瑼?"
                f"嚗?餃?? {len(cooled_top)} ??RSI+憸典瘥祟?詨?撖行 {push_count} ??"
                f"嚗??馳{_count} ??OI ?? {oi_success_count} ??"
            )
            # ?? S 蝝嚗蝡?哨??芸??潔蜓?梯”嚗????????????????????????
            if s_grade_msgs:
                _s_sep = f"\n{'?' * 20}\n"
                _s_header = (
                    f"? *S 蝝*  ?祈憚 {len(s_grade_msgs)} ?扔撘瑁??n"
                    f"{'?' * 20}\n"
                )
                _s_body = _s_sep.join(s_grade_msgs)
                send_telegram_message(
                    _s_header + _s_body,
                    TG_THREAD_IDS['position_change'],
                    parse_mode="Markdown"
                )
                logger.info(f"[S蝝] 撌脫??{len(s_grade_msgs)} ??S 蝝????函?閮嚗?")
            # ?? 銝餃銵剁??怠?刻?????????????????????????????????????????
            send_telegram_message(msg, TG_THREAD_IDS['position_change'], parse_mode="Markdown")
        else:
            logger.info(f"??冽???頛?{len(cooled_top)} 蝑??瑕嚗? RSI/憸典瘥祟?詨? 0 蝑?冽嚗??潮蜓?梯”")
    else:
        if len(all_top) == 0:
            logger.info(f"??冽???頛芰??OI ?瑼颱?璅?嚗?憿? 0 蝑?嚗??潮蜓?梯”")
        else:
            logger.info(f"??冽???頛?{len(all_top)} 蝑?◤?瑕嚗?h ?批?撟???孵?撌脫??嚗??潮蜓?梯”")

    # ?瑕?剁??頛芸祕???冽??璅??神??history嚗elected_for_push ??build_report_message_tiered ?扯身摰?
    pairs_this_run = [
        (_cooldown_symbol(x.get("symbol")), _item_direction(x))
        for x in cooled_top
        if x.get("symbol") and x.get("selected_for_push")
    ]

    # GitHub Step Summary嚗??GitHub Actions ?啣?銝哨?頛詨?祈憚?蝯梯???
    step_summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if step_summary_path:
        try:
            pushed_symbols = sorted({_cooldown_symbol(x.get("symbol") or "") for x in cooled_top if x.get("symbol")}) if cooled_top else []
            pushed_list = ", ".join(pushed_symbols) if pushed_symbols else "??"
            # ?? OI ?瑼鳴??交頛芣?閮??＊蝷綽??血?憿舐內?箏??瑼鳴?
            oi_4 = _dynamic_oi_4star if (_dynamic_oi_4star is not None and _dynamic_oi_sample_size >= 10) else OI_FOR_4_STAR
            oi_5 = _dynamic_oi_5star if (_dynamic_oi_5star is not None and _dynamic_oi_sample_size >= 10) else OI_FOR_5_STAR

            summary_lines = [
                "## ???祟?豢?閬?",
                "",
                "| ?? | ?詨?|",
                "| --- | --- |",
                f"| ??撟?車蝮賣 | {processed_count} |",
                f"| OI ????| {oi_success_count} |",
                f"| OI 憭望???| {oi_fail_count} |",
                f"| ?? OI ?瑼?(4??5?? | {oi_4:.2f}% / {oi_5:.2f}% |",
                f"| ?脣 TOP ???| {len(all_top)} |",
                f"| ?蝯?剜?? | {len(cooled_top)} |",
                f"| ?冽璅??” | {pushed_list} |",
                "",
            ]
            with open(step_summary_path, "a", encoding="utf-8") as f:
                f.write("\n".join(summary_lines) + "\n")
        except Exception as e:
            logger.warning(f"撖怠 GitHub Step Summary 憭望?: {e}")

    # 撖怠??瑕????芯???history嚗宏?文?餈質馱嚗?
    try:
        SNIPER_COOLDOWN_FILE.parent.mkdir(parents=True, exist_ok=True)
        new_entries = [{"symbol": s, "dir": d, "ts": int(now_ts)} for (s, d) in pairs_this_run if s]
        history = history + new_entries
        history = [e for e in history if isinstance(e, dict) and (now_ts - e.get("ts", 0)) <= history_sec]
        state = {"history": history}
        _emergency_sniper_state = state
        with _sniper_file_lock():
            save_json_file(SNIPER_COOLDOWN_FILE, state)
        logger.info(f"?瑕瑼歇撖怠: ?祈憚 {len(new_entries)} 蝑?甇瑕??{len(history)} 蝑?(靽? {HISTORY_HOURS}h)")
        _gist_save_cooldown(state)
    except Exception as e:
        logger.warning(f"撖怠?瑕???憭望?: {e}")

    logger.info("???祟?詨銵??蒂撌脫??")


# ==================== 4. ??蝬??豢??冽 ====================

SENT_DATA_FILE = DATA_DIR / "sent_economic_data_ids.json"


def fetch_economic_data() -> List[Dict]:
    """敺?CoinGlass API ??蝬??豢?"""
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
            # 璅??豢?靘?
            for item in data_list:
                item['_source'] = 'economic_data'
            return data_list
        else:
            logger.error(f"Economic Data API 餈??航炊: {result.get('msg')} (?航炊蝣? {result.get('code')})")
            return []
    except Exception as e:
        logger.error(f"?脣?蝬??豢?憭望?: {str(e)}")
        return []


def fetch_financial_events() -> List[Dict]:
    """敺?CoinGlass API ??鞎∠?鈭辣"""
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
            # 璅??豢?靘?
            for item in data_list:
                item['_source'] = 'financial_events'
            return data_list
        else:
            logger.warning(f"Financial Events API 餈??航炊: {result.get('msg')} (?航炊蝣? {result.get('code')})")
            return []
    except Exception as e:
        logger.warning(f"?脣?鞎∠?鈭辣憭望?: {str(e)}")
        return []


def fetch_central_bank_activities() -> List[Dict]:
    """敺?CoinGlass API ??憭株?瘣餃?"""
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
            # 璅??豢?靘?
            for item in data_list:
                item['_source'] = 'central_bank'
            return data_list
        else:
            logger.warning(f"Central Bank API 餈??航炊: {result.get('msg')} (?航炊蝣? {result.get('code')})")
            return []
    except Exception as e:
        logger.warning(f"?脣?憭株?瘣餃?憭望?: {str(e)}")
        return []


def parse_publish_time(item: Dict) -> Optional[datetime]:
    """閫???澆???嚗???UTC datetime嚗?蝥?頧??箏?????"""
    publish_timestamp = item.get('publish_timestamp') or item.get('publish_time') or item.get('time')
    if not publish_timestamp:
        return None
    
    try:
        if isinstance(publish_timestamp, (int, float)):
            if publish_timestamp > 1e12:  # 瘥怎?????
                dt = datetime.fromtimestamp(publish_timestamp / 1000, tz=timezone.utc)
            else:  # 蝘??
                dt = datetime.fromtimestamp(publish_timestamp, tz=timezone.utc)
            return dt
        else:
            # ?岫 ISO ?澆?
            time_str = str(publish_timestamp).replace('Z', '+00:00')
            dt = datetime.fromisoformat(time_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
    except Exception as e:
        logger.debug(f"??閫??憭望?: {publish_timestamp}, ?航炊: {str(e)}")
        return None


def filter_important_data(data_array: List[Dict], min_importance: int = 2) -> List[Dict]:
    """?蕪??蝬??豢?嚗???雿?閬改?"""
    now = get_taipei_time()
    one_week_later = now + timedelta(days=7)
    two_hours_ago = now - timedelta(hours=2)  # ?迂撌脩撣?撠??抒??豢?
    
    filtered = []
    for item in data_array:
        importance = item.get('importance_level') or item.get('importance') or 0
        
        # 閫???澆???
        publish_time = parse_publish_time(item)
        if not publish_time:
            continue
        
        # 瑼Ｘ?臬撌脩撣??祕?撣潘?
        is_published = item.get('published_value') not in [None, '']
        
        # ??蝭?嚗???撠??唳靘?憭?
        time_valid = two_hours_ago <= publish_time <= one_week_later
        
        # ?寞??雿?閬折?瞈?
        if importance >= min_importance and time_valid:
            filtered.append(item)
    
    return filtered


def filter_today_events(data_array: List[Dict], min_importance: int = 4) -> List[Dict]:
    """?蕪隞鈭辣嚗?潭銝?暺???"""
    now = get_taipei_time()
    today_start = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=TAIPEI_TZ)
    today_end = datetime(now.year, now.month, now.day, 23, 59, 59, tzinfo=TAIPEI_TZ)
    
    filtered = []
    for item in data_array:
        importance = item.get('importance_level') or item.get('importance') or 0
        
        # 閫???澆???
        publish_time = parse_publish_time(item)
        if not publish_time:
            continue
        
        # ?芸?隞銝?澆???隞?
        is_published = item.get('published_value') not in [None, '']
        is_today = today_start <= publish_time <= today_end
        
        if importance >= min_importance and is_today and not is_published:
            filtered.append(item)
    
    return filtered


def generate_data_id(item: Dict) -> str:
    """???臭????ID嚗?澆??"""
    # ?芸?雿輻 API ???銝 ID
    if item.get('id'):
        return str(item['id'])
    if item.get('calendar_id'):
        return str(item['calendar_id'])
    
    # 憒?瘝??臭? ID嚗蝙?函??嚗?皞?+ ?迂 + ???喉?
    source = item.get('_source', 'unknown')
    name = item.get('calendar_name') or item.get('name') or item.get('title') or 'unknown'
    timestamp = item.get('publish_timestamp') or item.get('publish_time') or item.get('time') or '0'
    
    return f"{source}_{name}_{timestamp}"


def get_unsent_data(data_array: List[Dict]) -> List[Dict]:
    """?脣?撠?券??豢?嚗?脩?嚗?澆????祕?潘?"""
    sent_ids = load_json_file(SENT_DATA_FILE, [])
    unsent = []
    now = get_taipei_time()
    
    for item in data_array:
        data_id = generate_data_id(item)
        
        # 瑼Ｘ?臬?典歇?券?銵其葉
        if data_id in sent_ids:
            continue
        
        # 憿?瑼Ｘ嚗???歇?澆?頞? 2 撠?嚗?撌脫?撖阡??潘??歲??
        # ?隞仿甇Ｗ GitHub Actions ?啣?銝剝?銴??
        publish_time = parse_publish_time(item)
        if publish_time:
            time_diff = (now - publish_time).total_seconds()
            published_value = item.get('published_value') or item.get('actual')
            
            # 憒?撌脩撣???2 撠?銝?撖阡??潘?閬撌脰???嚗??銴?
            if time_diff > 7200 and published_value:  # 2撠? = 7200蝘?
                logger.debug(f"頝喲?撌脩撣???撠???? {data_id}")
                # 璅??箏歇?券??踹?銝活?炎??
                mark_as_sent(data_id)
                continue
        
        unsent.append(item)
    
    return unsent


def mark_as_sent(data_id: str):
    """璅??豢??箏歇?券?"""
    sent_ids = load_json_file(SENT_DATA_FILE, [])
    if data_id not in sent_ids:
        sent_ids.append(data_id)
        # ?芯???餈?1000 璇???
        if len(sent_ids) > 1000:
            sent_ids = sent_ids[-1000:]
        save_json_file(SENT_DATA_FILE, sent_ids)


def get_time_status(publish_time: datetime) -> tuple:
    """閮??????餈? (???摮? ?臬撌脩撣? ??撌桃???"""
    # 蝣箔??拙???典?銝??嚗?????
    now = get_taipei_time()
    publish_time_taipei = get_taipei_time(publish_time)
    diff_seconds = (publish_time_taipei - now).total_seconds()
    
    is_past = diff_seconds < 0
    abs_diff = abs(diff_seconds)
    
    if is_past:
        # 撌脩撣???
        if abs_diff < 3600:  # 1撠???
            minutes = int(abs_diff // 60)
            return (f"撌脩撣?{minutes} ????, True, diff_seconds")
        elif abs_diff < 86400:  # 24撠???
            hours = int(abs_diff // 3600)
            return (f"撌脩撣?{hours} 撠???, True, diff_seconds")
        else:
            days = int(abs_diff // 86400)
            return (f"撌脩撣?{days} 憭拙?", True, diff_seconds)
    else:
        # ?芰撣???
        if abs_diff < 3600:  # 1撠???
            minutes = int(abs_diff // 60)
            return (f"{minutes} ??敺撣?, False, diff_seconds")
        elif abs_diff < 86400:  # 24撠???
            hours = int(abs_diff // 3600)
            minutes = int((abs_diff % 3600) // 60)
            if minutes > 0:
                return (f"{hours} 撠? {minutes} ??敺?, False, diff_seconds")
            else:
                return (f"{hours} 撠?敺?, False, diff_seconds")
        else:
            days = int(abs_diff // 86400)
            hours = int((abs_diff % 86400) // 3600)
            if hours > 0:
                return (f"{days} 憭?{hours} 撠?敺?, False, diff_seconds")
            else:
                return (f"{days} 憭拙?", False, diff_seconds)


def get_country_flag(country_name: str) -> str:
    """?脣??振?? emoji"""
    flag_map = {
        '蝢?': '??', '蝢??': '??', 'US': '??', 'United States': '??', 'USA': '??',        '銝剖?': '??', '銝剛鈭箸??勗???': '??', 'CN': '??', 'China': '??',
        '甇??': '??', '甇?': '??', 'EU': '??', 'Eurozone': '??', 'Euro Area': '??',
        '?勗?': '??', '憭找???': '??', 'UK': '??', 'United Kingdom': '??', 'GB': '??',
        '?交': '??', 'JP': '??', 'Japan': '??',
        '?啁': '??', '?箇': '??', 'TW': '??', 'Taiwan': '??',
    }
    
    if country_name in flag_map:
        return flag_map[country_name]
    
    for key, flag in flag_map.items():
        if key in country_name or country_name in key:
            return flag
    
    return '??'


def get_effect_text(effect: str) -> str:
    """?脣?撣敶梢?葉??餈?"""
    effect_map = {
        'Minor Impact': '頛凝敶梢',
        'Moderate Impact': '銝剔?敶梢',
        'High Impact': '?之敶梢',
        'Major Impact': '璆萄之敶梢',
        '?拙?': '???拙?', 'Bullish': '???拙?',
        '?拍征': '???拍征', 'Bearish': '???拍征',
        '銝剜?': '銝剜批蔣??',  'Neutral': '銝剜批蔣??',
    }
    
    for key, value in effect_map.items():
        if key in effect or effect in key:
            return value
    
    return effect or '敺?撖?'


def get_effect_emoji(effect: str) -> str:
    """?脣?撣敶梢 emoji"""
    effect_map = {
        '?拙?': '??', 'Bullish': '??',
        '?拍征': '??', 'Bearish': '??',
        '銝剜?': '?∴?', 'Neutral': '?∴?'
    }
    return effect_map.get(effect, '??')


def get_category_info(data: Dict) -> tuple:
    """?脣??豢?憿鞈?嚗???(憿?迂, 憿emoji)"""
    source = data.get('_source', 'economic_data')
    category_map = {
        'economic_data': ('蝬??豢?', '??'),
        'financial_events': ('鞎∠?鈭辣', '?'),
        'central_bank': ('憭株?瘣餃?', '?')
    }
    return category_map.get(source, ('蝬?鈭辣', '??'))


def format_economic_data_message(data: Dict) -> str:
    """?澆???瞈???荔??冽閮剛?嚗?"""
    publish_time = parse_publish_time(data)
    if not publish_time:
        publish_time = get_taipei_time()
    
    time_str = format_datetime(publish_time)
    time_status, is_published, _ = get_time_status(publish_time)
    
    # ????
    importance_level = data.get('importance_level') or data.get('importance') or 0
    if importance_level >= 3:
        importance_emoji = '?'
        importance_text = '璆菟?'
        importance_badge = '?? 璆菟?????'
    elif importance_level >= 2:
        importance_emoji = '?'
        importance_text = '擃?'
        importance_badge = '??擃?閬?'
    else:
        importance_emoji = '?'
        importance_text = '銝?'
        importance_badge = '?? 銝剝?閬?'
    
    # 憿鞈?
    category_name, category_emoji = get_category_info(data)
    
    # ?振鞈?
    country_flag = get_country_flag(data.get('country_name') or data.get('country') or '')
    country_name = data.get('country_name') or data.get('country') or '?芰?啣?'
    
    # 鈭辣?迂
    event_name = data.get('calendar_name') or data.get('name') or data.get('title') or '蝬???'
    
    # 撣敶梢
    effect_emoji = get_effect_emoji(data.get('data_effect') or data.get('effect') or '')
    effect_text = get_effect_text(data.get('data_effect') or data.get('effect') or '')
    
    # ?葫?潸???
    forecast_value = data.get('forecast_value') or data.get('forecast')
    previous_value = data.get('previous_value') or data.get('previous')
    published_value = data.get('published_value') or data.get('actual')
    
    # 瑽遣閮
    lines = []
    
    # 璅????
    lines.append(f"{category_emoji} *{category_name}?冽??")
    lines.append("????????????????????????")
    lines.append("")
    
    # 鈭辣璅?
    lines.append(f"{importance_emoji} *{event_name}*")
    lines.append(f"{country_flag} {country_name}")
    lines.append("")
    
    # ??鞈?
    lines.append("?? *?澆???*")
    if is_published:
        lines.append(f"??{time_str}")
        lines.append(f"??{time_status}")
    else:
        lines.append(f"?? {time_str}")
        lines.append(f"??{time_status}")
    lines.append("")
    
    # ?豢?撠?嚗??歇?澆?嚗＊蝷箏祕?潘??芰撣＊蝷粹?皜砍潘?
    has_data = False
    if published_value:
        lines.append("?? *撖阡??澆???")
        lines.append(f"`{published_value}`")
        has_data = True
        if forecast_value:
            lines.append(f"?葫?{_value}`")
        if previous_value:
            lines.append(f"?{_value}`")
    elif forecast_value or previous_value:
        lines.append("?? *撣??*")
        if forecast_value:
            lines.append(f"?葫?{_value}`")
        if previous_value:
            lines.append(f"?{_value}`")
        has_data = True
    
    if has_data:
        lines.append("")
    
    # ???扯?敶梢
    lines.append(f"{importance_badge}")
    if effect_text and effect_text != '敺?撖?:':
        lines.append(f"{effect_emoji} 撣敶{_text}")
    lines.append("")
    
    # 鋆?隤芣?
    remark = data.get('remark') or data.get('note') or data.get('description')
    if remark:
        lines.append(f"? *?寥閫??*")
        # ?隤芣??瑕漲
        if len(remark) > 200:
            remark = remark[:200] + "..."
        lines.append(f"{remark}")
        lines.append("")
    
    # 摨鞈?
    lines.append("????????????????????????")
    lines.append(f"?? ?憛??寥嚚format_datetime(get_taipei_time())")
    
    return "\n".join(lines)


def format_today_preview_message(events: List[Dict]) -> str:
    """?澆????仿????荔??寥脩?嚗?瘨?蝝??寧擃?閬批?璆菟????改?"""
    now = get_taipei_time()
    time_str = format_datetime(now)
    
    lines = []
    lines.append("?? *???仿?閬?瞈????")
    lines.append("????????????????????????")
    lines.append("")
    
    # ??嚗扔擃?閬改?>= 3嚗?擃?閬改?>= 2 銝?< 3嚗?
    very_high = [e for e in events if (e.get('importance_level') or e.get('importance') or 0) >= 3]
    high = [e for e in events if 2 <= (e.get('importance_level') or e.get('importance') or 0) < 3]
    
    # ????摨?雿輻?芯???雿 fallback嚗?
    future_time = datetime(2099, 12, 31, 23, 59, 59, tzinfo=TAIPEI_TZ)
    very_high.sort(key=lambda x: parse_publish_time(x) or future_time)
    high.sort(key=lambda x: parse_publish_time(x) or future_time)
    
    if very_high:
        lines.append("? *璆菟????改?撠???哨?*嚗?")
        lines.append("")
        for event in very_high:
            publish_time = parse_publish_time(event)
            if publish_time:
                # 頧??箏????蒂?澆???
                publish_time_taipei = get_taipei_time(publish_time)
                time_display = publish_time_taipei.strftime("%H:%M")
                event_name = event.get('calendar_name') or event.get('name') or event.get('title') or '蝬???'
                country_flag = get_country_flag(event.get('country_name') or event.get('country') or '')
                lines.append(f"  ??{time_display} | {country_flag} {event_name}")
        lines.append("")
    
    if high:
        lines.append("? *擃?閬改????箸??殷?*嚗?")
        lines.append("")
        for event in high:
            publish_time = parse_publish_time(event)
            if publish_time:
                # 頧??箏????蒂?澆???
                publish_time_taipei = get_taipei_time(publish_time)
                time_display = publish_time_taipei.strftime("%H:%M")
                event_name = event.get('calendar_name') or event.get('name') or event.get('title') or '蝬???'
                country_flag = get_country_flag(event.get('country_name') or event.get('country') or '')
                lines.append(f"  ??{time_display} | {country_flag} {event_name}")
        lines.append("")
    
    if not very_high and not high:
        lines.append("隞?⊿?閬?瞈??隞?")
        lines.append("")
    
    lines.append("????????????????????????")
    lines.append(f"??????{time_str}")
    
    return "\n".join(lines)


def send_today_preview():
    """?拐?8暺???仿????擃?閬找誑銝?鈭辣嚗?"""
    try:
        all_data = []
        
        # ??????
        logger.info("甇???蝬??豢?嚗??芋撘?...")
        economic_data = fetch_economic_data()
        all_data.extend(economic_data)
        
        financial_events = fetch_financial_events()
        all_data.extend(financial_events)
        
        central_bank = fetch_central_bank_activities()
        all_data.extend(central_bank)
        
        if not all_data:
            logger.info("瘝??脣??唬遙雿??")
            return
        
        # ?蕪隞擃?閬找誑銝?鈭辣嚗?= 2嚗?
        today_events = filter_today_events(all_data, min_importance=2)
        logger.info(f"隞擃?閬找誑銝?隞? {len(today_events)} 璇?")
        
        if not today_events:
            logger.info("隞?⊿?閬?隞?")
            return
        
        # ?潮???
        message = format_today_preview_message(today_events)
        send_telegram_message(message, TG_THREAD_IDS['economic_data'], parse_mode="Markdown")
        logger.info("隞???潮???")
        
    except Exception as e:
        logger.error(f"?潮??仿??隤? {str(e)}")


def fetch_and_push_economic_data():
    """銝餃?賂???銝行??瞈???芣?剜扔擃?閬找?隞塚??其?隞嗥??嚗?"""
    try:
        all_data = []
        
        # 1. ??蝬??豢?
        logger.info("甇???蝬??豢?...")
        economic_data = fetch_economic_data()
        all_data.extend(economic_data)
        logger.info(f"蝬??豢?{len(economic_data)} 璇?")
        
        # 2. ??鞎∠?鈭辣
        logger.info("甇???鞎∠?鈭辣...")
        financial_events = fetch_financial_events()
        all_data.extend(financial_events)
        logger.info(f"鞎∠?鈭辣{len(financial_events)} 璇?")
        
        # 3. ??憭株?瘣餃?
        logger.info("甇???憭株?瘣餃?...")
        central_bank = fetch_central_bank_activities()
        all_data.extend(central_bank)
        logger.info(f"憭株?瘣餃?{len(central_bank)} 璇?")
        
        if not all_data:
            logger.info("瘝??脣??唬遙雿??")
            return
        
        logger.info(f"蝮賢?脣? {len(all_data)} 璇??蝬??豢?: {len(economic_data)}, 鞎∠?鈭辣: {len(financial_events)}, 憭株?瘣餃?: {len(central_bank)}嚗?")
        
        # ?芷?瞈暹扔擃?閬扳??>= 3嚗?擃?閬改?>= 2 銝?< 3嚗??冽
        important_data = filter_important_data(all_data, min_importance=3)
        logger.info(f"?蕪敺?璆菟????扳?? {len(important_data)} 璇?")
        
        if not important_data:
            logger.info("瘝?蝚血?璇辣?扔擃?閬扳??")
            return
        
        # ?撣???摨??芸??券撠撣?嚗?
        future_time = datetime(2099, 12, 31, 23, 59, 59, tzinfo=TAIPEI_TZ)
        important_data.sort(key=lambda x: parse_publish_time(x) or future_time)
        
        # 瑼Ｘ?芯?撠?券?
        new_data = get_unsent_data(important_data)
        logger.info(f"撠?券?璆菟????扳?? {len(new_data)} 璇?")
        
        if not new_data:
            logger.info("??扔擃?閬扳??撌脫??")
            return
        
        # ?寥??券??踹???餌?嚗?
        success_count = 0
        for idx, data in enumerate(new_data):
            try:
                message = format_economic_data_message(data)
                send_telegram_message(message, TG_THREAD_IDS['economic_data'], parse_mode="Markdown")
                
                data_id = generate_data_id(data)
                mark_as_sent(data_id)
                success_count += 1
                
                # 瘥?閮?? 1 蝘??踹?閫貊???
                if idx < len(new_data) - 1:
                    time.sleep(1)
                    
            except Exception as e:
                logger.error(f"?券璇?仃?? {str(e)}")
        
        logger.info(f"???{_count}/{len(new_data)} 璇扔擃?閬抒?瞈??")
        
    except Exception as e:
        logger.error(f"蝬??豢??冽?瑁??航炊: {str(e)}")
        send_telegram_message("?? 蝬??豢??急??⊥???嚗?蝔??岫??, TG_THREAD_IDS['economic_data']")


# ==================== 5. ?啗?敹怨??函銝剜??冽 ====================

LAST_NEWS_TIME_FILE = DATA_DIR / "last_news_time.json"
COINGLASS_ARTICLE_IDS_FILE = DATA_DIR / "coinglass_article_ids.json"
COINGLASS_NEWSFLASH_IDS_FILE = DATA_DIR / "coinglass_newsflash_ids.json"


def process_and_send(news: Dict, source: str):
    """蝧餉陌銝衣??Tree of Alpha ?啗???Telegram"""
    translated_title = translate_text(news.get('title', ''))
    
    message = "? *??馳??翰閮?\n\n"
    message += f"?? *{translated_title}*\n\n"
    message += f"?? ??{news.get('title', '')}\n"
    message += f"?? [暺??亦???]({news.get('url', '')})"
    
    send_telegram_message(message, TG_THREAD_IDS['news'])


def process_and_send_coinglass(item: Dict, type_str: str):
    """蝧餉陌銝衣??CoinGlass ?啗?/敹怨???Telegram"""
    is_newsflash = type_str == "newsflash"
    emoji = "??" if is_newsflash else "?"
    type_name = "敹怨?" if is_newsflash else "?啗?"
    
    translated_title = translate_text(item.get('title') or item.get('headline') or "")
    translated_content = translate_text(item.get('content') or item.get('description') or "")
    
    message = f"{emoji} *??{_name}??\n\n"
    
    if translated_title:
        message += f"?? *{translated_title}*\n\n"
    
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
        # 頧??箏?????
        date_taipei = get_taipei_time(date)
        message += f"?? ??{_taipei.strftime('%Y-%m-%d %H:%M:%S')}\n"
    
    if item.get('url') or item.get('link'):
        message += f"?? [暺??亦???]({item.get('url') or item.get('link')})"
    
    send_telegram_message(message, TG_THREAD_IDS['news'])


def fetch_all_news():
    """?游??瑁??賣嚗?????蒂瞈葬???陛?剛??荔?瘥?撠??冽銝甈∴?"""
    all_news_items = []
    
    # ?? Tree of Alpha ?啗?
    try:
        url = "https://news.treeofalpha.com/api/news"
        params = {"limit": 5}  # ?芸????璇?
        headers = {"Authorization": TREE_API_KEY}
        response = requests.get(url, params=params, headers=headers, timeout=10)
        news_list = response.json()
        for news in news_list[:5]:  # ?芸???璇?
            title = translate_text(news.get('title', ''))
            if title:
                all_news_items.append({
                    'title': title,
                    'source': 'Tree of Alpha',
                    'url': news.get('url', '')
                })
    except Exception as e:
        logger.warning(f"Tree of Alpha ?啗???憭望?: {str(e)}")
    
    # ?? CoinGlass ?啗?嚗????璇?
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
                article_list = result.get('data', [])[:3]  # ?芸???璇?
                for article in article_list:
                    title = translate_text(article.get('title') or article.get('headline') or "")
                    if title:
                        all_news_items.append({
                            'title': title,
                            'source': 'CoinGlass',
                            'url': article.get('url') or article.get('link') or ''
                        })
        except Exception as e:
            logger.warning(f"CoinGlass ?啗???憭望?: {str(e)}")
    
    # 憒?瘝??啗?嚗??冽
    if not all_news_items:
        logger.info("?祆活???⊥?啗?嚗歲???")
        return
    
    # 瞈葬???陛?剛???
    now = get_taipei_time()
    time_str = format_datetime(now)
    
    lines = []
    lines.append("? *??馳??翰閮?")
    lines.append("????????????????????")
    lines.append("")
    
    # ?芷＊蝷箸?憿?蝪∠?澆?
    for idx, item in enumerate(all_news_items[:8], 1):  # ?憭?璇?
        lines.append(f"{idx}. {item['title']}")
        if item.get('url'):
            lines.append(f"   ?? [?亦?閰單?]({item['url']})")
        lines.append("")
    
    lines.append("????????????????????")
    lines.append(f"???{_str}")
    
    message = "\n".join(lines)
    send_telegram_message(message, TG_THREAD_IDS['news'], parse_mode="Markdown")
    logger.info(f"?啗?敹怨??冽摰?嚗 {len(all_news_items)} 璇??")


# ==================== 6. 鞈?鞎餌? ====================

def fetch_funding_fortune_list():
    """??鞈?鞎餌???璁?"""
    url = "https://open-api-v4.coinglass.com/api/futures/funding-rate/exchange-list"
    headers = {
        "accept": "application/json",
        "CG-API-KEY": CG_API_KEY
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        logger.info(f"API ????Ⅳ: {response.status_code}")
        
        result = response.json()
        if result.get('code') not in ['0', 0]:
            logger.error(f"API ???航炊: {result}")
            return
        
        data_list = result.get('data', [])
        if not isinstance(data_list, list):
            logger.error("API ?豢??澆??航炊")
            return
        
        binance_funding_rates = []
        for coin_data in data_list:
            symbol = coin_data.get('symbol')
            
            # ?芸??? USDT 瘞貊???
            stablecoin_list = coin_data.get('stablecoin_margin_list', [])
            for item in stablecoin_list:
                if item.get('exchange') == 'Binance' and item.get('funding_rate') is not None:
                    binance_funding_rates.append({
                        'symbol': symbol,
                        'exchange': item.get('exchange'),
                        'fundingRate': float(item.get('funding_rate', 0)),
                        'marginType': 'USDT瘞貊?',
                        'fundingRateInterval': item.get('funding_rate_interval', 8)
                    })
            
            # 憒? USDT 瘞貊?瘝?撟??????炎?亙馳?砌?瘞貊?
            token_list = coin_data.get('token_margin_list', [])
            for item in token_list:
                if item.get('exchange') == 'Binance' and item.get('funding_rate') is not None:
                    has_usdt = any(r['symbol'] == symbol and r['marginType'] == 'USDT瘞貊?' 
                                   for r in binance_funding_rates)
                    if not has_usdt:
                        binance_funding_rates.append({
                            'symbol': symbol,
                            'exchange': item.get('exchange'),
                            'fundingRate': float(item.get('funding_rate', 0)),
                            'marginType': '撟?雿偶蝥?',
                            'fundingRateInterval': item.get('funding_rate_interval', 8)
                        })
        
        logger.info(f"撟??瘞貊????豢?璇: {len(binance_funding_rates)}")
        
        # ?寞?鞎餌?蝯??潭?摨??? 5 ??
        sorted_data = sorted(
            [item for item in binance_funding_rates if item['fundingRate'] != 0],
            key=lambda x: abs(x['fundingRate']),
            reverse=True
        )[:5]
        
        if not sorted_data:
            logger.warning("?芣?啣馳摰偶蝥?蝝???鞈?鞎餌??豢?")
            return
        
        # 瑽遣閮
        message = "? *??砌?鞈?鞎餌???璁?\n"
        message += "????????????????????\n"
        message += "*隞交???10,000 USDT ?箔?嚗? 4 撠?蝯?銝甈∴?*\n\n"
        
        for index, item in enumerate(sorted_data):
            symbol = item['symbol']
            rate = item['fundingRate']
            
            rate_percent = f"{abs(rate):.6f}"
            rate_display = f"+{rate_percent}%" if rate >= 0 else f"-{rate_percent}%"
            
            rate_for_calculation = abs(rate) / 100
            single_pay = f"{10000 * 0.4 * rate_for_calculation:.2f}"
            
            message += f"{index + 1}. ? *{symbol}USDT 瘞貊?*\n"
            message += f"   ?? 鞈?鞎餌?{_display}`\n"
            message += f"   ? ?格活??{_pay}` USDT\n"
            message += "????????????????????\n"
        
        message += "\n? *憟蝑*嚗?牧????=?撞鞎瑕嚗?蝛???鞈?嚗?\n"
        message += "*甇?祥??+嚗?嚗?蝛箸偶蝥?蝝???嚗? ???曇疏嚗? 4 撠???鞈?鞎餌??n"
        message += "*鞎祥??-嚗?嚗?憭偶蝥?蝝??撞嚗? 鞈??曇疏嚗??瘜冽?頠征憸券?n\n"
        now_taipei = get_taipei_time()
        message += f"???湔??{_taipei.strftime('%Y-%m-%d %H:%M:%S')}"
        
        send_telegram_message(message, TG_THREAD_IDS['funding_rate'])
        
    except Exception as e:
        logger.error(f"鞈祥璁銵仃?? {str(e)}")


# ==================== 7. ?瑞???嚗????芸? ====================

def _coinglass_get(path: str, params: Optional[Dict] = None) -> Optional[Dict]:
    """???CoinGlass GET 隢?撌亙"""
    if not CG_API_KEY:
        logger.error("CG_API_KEY ?芾身摰??⊥??澆 CoinGlass API")
        return None
    url = f"{CG_API_BASE}{path}"
    headers = {
        "accept": "application/json",
        "CG-API-KEY": CG_API_KEY,
    }
    try:
        resp = requests.get(url, headers=headers, params=params or {}, timeout=10)
        if resp.status_code != 200:
            logger.error(f"CoinGlass API HTTP ?航炊 {path}: {resp.status_code} - {resp.text[:200]}")
            return None
        data = resp.json()
        # 憭 CoinGlass 隞 code ??'0' 隞?”??
        code = data.get("code", 0)
        if code not in [0, "0", 200, "200"]:
            logger.error(f"CoinGlass API 餈??航炊 {path}: {data}")
            return None
        return data
    except Exception as e:
        logger.error(f"CoinGlass API 隢?憭望? {path}: {str(e)}")
        return None


def _get_latest_from_data(result: Dict) -> Optional[Dict]:
    """敺?CoinGlass ??銝剖??箸??唬?蝑?data嚗Ⅱ靽???dict"""
    if not result:
        return None
    data = result.get("data", result)
    if isinstance(data, list):
        if not data:
            return None
        # ??敺???蝝?雿Ⅱ靽???dict
        last_item = data[-1]
        if isinstance(last_item, dict):
            return last_item
        # 憒??敺???蝝???dict嚗?閰血??
        for item in reversed(data):
            if isinstance(item, dict):
                return item
        logger.warning(f"?”銝剜????dict 憿????? {data}")
        return None
    if isinstance(data, dict):
        return data
    logger.warning(f"?芰???撘? {type(data)} - {data}")
    return None


def fetch_ahr999_index() -> Optional[float]:
    """??瘥撟?Ahr999 ???詨?"""
    result = _coinglass_get("/api/index/ahr999")
    point = _get_latest_from_data(result) if result else None
    if not point:
        return None
    # 蝣箔? point ??dict嚗???list
    if not isinstance(point, dict):
        logger.warning(f"Ahr999 鞈??澆??航炊嚗???dict 雿???{type(point)}: {point}")
        return None
    # ?岫憭虜閬?雿?蝔梧??撖阡? API ???ahr999_value嚗?
    for key in ("ahr999_value", "ahr999", "ahr999_index", "ahrIndex", "ahr_value"):
        val = point.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    logger.warning(f"Ahr999 蝯??芰嚗?憪??? {point}")
    return None


def get_rainbow_stage(price: Optional[float], levels: Optional[List[float]]) -> str:
    """
    ?寞??嗅??寞?蔗?孵??寞?曉潘?????膩??    levels: ?曹??圈???潮?澆?銵剁??虜 9 ????    """
    if price is None or not levels or len(levels) < 3:
        return "鞈?銝雲嚗?⊥??斗"

    # 蝣箔????
    levels = sorted(levels)

    # ?湧?雿摯
    if price < levels[0]:
        return "?箸銝?怎憭抒?對?璆萄漲雿摯?嚗?"

    # ?湧?擃摯
    if price > levels[-1]:
        return "?憭扳部瘝怠?嚗遣霅啣??寥???雿?獢?"

    # ?賢??葉嚗?啣???畾?
    idx = 0
    for i in range(len(levels) - 1):
        if levels[i] <= price < levels[i + 1]:
            idx = i
            break

    # 靘??典?畾萇????雿?/ 銝凋? / 擃???
    n = len(levels) - 1  # ??n ????
    low_border = n // 3
    high_border = (2 * n) // 3

    if idx <= low_border:
        return "?寞雿敶抵??雿?嚗?蝺敞蝛????"
    elif idx <= high_border:
        return "?寞雿敶抵?葉??嚗惇?澆?????????/閫??"
    else:
        return "?寞雿敶抵??雿?嚗??游? FOMO/瘜⊥疵嚗?雓寞??抒恣憸券"


def fetch_rainbow_zone() -> Optional[str]:
    """??瘥撟?蔗?孵??嗅????餈堆?頧?撠????嚗?"""
    result = _coinglass_get("/api/index/bitcoin/rainbow-chart")
    if not result:
        return None

    # ?岫敺??葉???嗅? BTC ?寞
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
        # ?詨?蝯?嚗?? [v1, v2, ..., vN, timestamp] ??[level1..level9]
        if isinstance(last_row, list) and len(last_row) >= 4:
            # ?岫閬?敺???蝝???喉??園??箏?潮??
            numeric_parts = [x for x in last_row if isinstance(x, (int, float))]
            if len(numeric_parts) >= 4:
                # ?亙??芸?敺?潘?雿輻?憭批潛??潔??箄?隡?
                if price is None:
                    price = max(numeric_parts)
                # ??嗅??寞憭?撠????雿?惜蝝??踹??扔蝡舀?憭批潛雿???
                # ?ㄐ蝪∪??箏??葉??憭批潘??園?閬敶抵撅斤?
                max_val = max(numeric_parts)
                levels = [v for v in numeric_parts if v != max_val] or numeric_parts

    return get_rainbow_stage(price, levels)


def fetch_pi_cycle_signal() -> bool:
    """?? Pi 敺芰????臬閫貊嚗?蝺漱??"""
    result = _coinglass_get("/api/index/pi-cycle-indicator")
    point = _get_latest_from_data(result) if result else None
    if not point:
        return False
    # 蝣箔? point ??dict
    if not isinstance(point, dict):
        logger.warning(f"Pi 敺芰??鞈??澆??航炊嚗???dict 雿???{type(point)}: {point}")
        return False

    # 1) ?湔????雿?
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

    # 2) 憒??璇?蝺?潘??臭誑蝎?斗?臬?漱??
    # 雿??亥?憿舐內蝯??? {'ma_110': ..., 'ma_350_mu_2': ..., 'price': ..., 'timestamp': ...}
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
            # ?芾??剖?蝺??潮??嚗??箸??憸券
            return short_ma >= long_ma
        except (TypeError, ValueError):
            pass

    logger.warning(f"Pi 敺芰??蝯??芰嚗?憪??? {point}")
    return False


def fetch_latest_fear_greed() -> Optional[int]:
    """????唬?蝑??潸?鞎芸帚?"""
    result = _coinglass_get("/api/index/fear-greed-history")
    point = _get_latest_from_data(result) if result else None
    if not point:
        return None

    # 1) ?啁?蝯?嚗'data_list': [ ... ?湔?” ... ]}
    if isinstance(point, dict) and "data_list" in point:
        data_list = point.get("data_list")
        if isinstance(data_list, list) and data_list:
            try:
                return int(float(data_list[-1]))
            except (TypeError, ValueError):
                logger.warning(f"?⊥?閫????痕憍?data_list ?敺?蝑?? {data_list[-1]}")
                return None

    # 2) ?喟絞蝯?嚗?蝑銝??dict嚗 value / score 蝑?雿?
    if isinstance(point, dict):
        for key in ("value", "fear_greed", "score", "index"):
            val = point.get(key)
            if val is not None:
                try:
                    return int(float(val))
                except (TypeError, ValueError):
                    continue

    logger.warning(f"??痕憍芣??貊?瑽?伐???鞈?: {point}")
    return None


def _classify_fear_greed(value: Optional[int]) -> str:
    if value is None:
        return "?芰"
    if value <= 20:
        return "璆萄漲?"
    if value <= 40:
        return "?"
    if value < 60:
        return "銝剜?"
    if value <= 80:
        return "鞎芸帚"
    return "璆萄漲鞎芸帚"


def _describe_fear_greed(value: Optional[int]) -> str:
    """撠??潸?鞎芸帚?頧??湔??恍??餈唳?摮?"""
    if value is None:
        return "???怎撩嚗???撖?Ahr999 ??潔?蝵柴?"
    if value < 25:
        return "? 憭批振?賢?嚗?蝺扔摨行??潘?敺敺?舫蝺?鞈犖?Ｘ?蹂噶摰????"
    if 45 <= value <= 55:
        return "?? 撣???亥?銝剜改??拙??銝???蝭憟?敺?雿?胯?"
    if value > 75:
        return "? 撣璆萄漲鞎芸帚嚗???蝺???隢鼠憟賢??典葆銝阡??????"
    return "??撠?唳扔蝡臬???撱箄降?剝? Ahr999 ?蔗?孵?銝韏瑞???瑯?"


def _interpret_rainbow_zone(zone: Optional[str]) -> str:
    """?蔗?孵?????蕃???賢???餈?"""
    if not zone:
        return "鞈?銝雲嚗?⊥??斗"
    z = zone.lower()
    if any(k in z for k in ["buy", "cheap", "accumulate", "bargain", "btfd"]):
        return f"{zone}嚗??典???嚗蝺?靘踹?嚗?"
    if any(k in z for k in ["hodl", "hold"]):
        return f"{zone}嚗蝺???嚗??梁?嚗?"
    if any(k in z for k in ["fomo", "sell", "bubble", "maximum", "overvalued"]):
        return f"{zone}嚗?瘜⊥疵/擃摯?嚗???◢?芣蝞∴?"
    return zone


def build_long_term_message() -> Optional[str]:
    """?蝺瓷撖望???瑕之蝝鞎瑁都暺??曉?臬????"""
    ahr = fetch_ahr999_index()
    fg = fetch_latest_fear_greed()
    if ahr is None:
        return None

    status = "?? 撠瑕鬲? (??)"
    action = "憭?撠?嚗雿鞎?"
    color = "?"
    if ahr < 0.45:
        status, action, color = "?? ?賜摨?(憭扳?摨?", "?賊?鞈?鞎琿脣嚗撟游?雿????芸楛嚗?", "?"
    elif ahr < 1.2:
        status, action, color = "? 摰?? (蝝舐?)", "?芣偌?潔?撠梯眺嚗?閬恣?寞??", "?"
    elif ahr > 5.0:
        status, action, color = "?? 銝?? (?)", "皜??服PP嚗??嚗?", "?"
    elif ahr > 1.2 and fg is not None and fg > 80:
        status, action, color = "? 瘜⊥疵? (皜?", "鈭箄曌硫??湛??鞈???", "??"

    lines = []
    lines.append("??*?蝺瓷撖望???")
    lines.append("????????????????????")
    lines.append(f"?? *?嗅?雿蔭{color} {status}*")
    lines.append("")
    lines.append(f"? *AHR999 ?{ahr:.2f}*")
    lines.append(f"?儭?*鞎芸帚??{fg}*" if fg is not None else "?儭?*鞎芸帚??嚗?")
    lines.append("")
    lines.append("?? *???寥蝣?敹?嚗?")
    lines.append(f"?? {action}")
    if fg is not None and fg < 20:
        lines.append("?? ?曉撣璆萄漲?嚗??虜?臬?鈭箄??湔??Ｙ???")
    if fg is not None and fg > 80:
        lines.append("?? ?曉撣璆萄漲鞎芸帚嚗?咱?賢?馳嚗?閰脣?敹???")
    lines.append("")
    lines.append("????????????????????")
    lines.append(f"??{datetime.now(TAIPEI_TZ).strftime('%Y-%m-%d')}")
    return "\n".join(lines)


def run_long_term_monitor(interval_hours: int = 4):
    """24 撠?撣賊?嚗? interval_hours 撠???銝行?凋?甈?"""
    logger.info(f"???瑞?????嚗? {interval_hours} 撠??湔銝甈?..")
    interval_sec = max(1, int(interval_hours * 3600))
    while True:
        try:
            message = build_long_term_message()
            if message:
                thread_id = TG_THREAD_IDS.get("long_term_index", 0)
                send_telegram_message(message, thread_id, parse_mode="Markdown")
            else:
                logger.warning("?祈憚?瑞?????憭望?嚗?潮??")
        except Exception as e:
            logger.error(f"?瑞??????瑁??航炊: {str(e)}")
        # 隡 interval
        time.sleep(interval_sec)


def run_long_term_once():
    """?瑞?鞎∪??望??冽嚗??嚗?"""
    logger.info("?瑁??格活?瑞????冽...")
    message = build_long_term_message()
    if not message:
        logger.warning("?祆活?瑞?????憭望?嚗?潮??")
        return
    thread_id = TG_THREAD_IDS.get("long_term_index", 248)
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "?? ?亦?瘥撟?蔗?孵?", "url": "https://www.coinglass.com/zh-TW/pro/i/bitcoin-rainbow-chart"},
                {"text": "? ?亦? AHR999", "url": "https://www.coinglass.com/zh-TW/pro/i/ahr999"}
            ]
        ]
    }
    send_telegram_message(message, thread_id, parse_mode="Markdown", reply_markup=keyboard)


# ==================== 8. 瘚??抒???璆萇垢皜???嚗?====================

LIQ_SYMBOLS = [
    "BTC", "ETH", "SOL",  # ?芸皜祇??蜓瘚馳蝔?
]
LIQ_EXCHANGE_LIST = "Binance"
LIQ_REQUEST_DELAY = 1.2  # 蝘?

def get_liquidation_threshold(symbol: str, time_window: str = "1h") -> tuple:
    """?寞?撟?車?璆萇垢??瑼鳴?USD嚗?    餈? (1h?? 24h?? ??蝯?    瘜冽?嚗?撠??瑼餃歇憭批???嚗誑靘踵??憭扔蝡舐???隞?    """
    if symbol in ("BTC", "ETH"):
        return (100_000.0, 15_000_000.0)  # 1h: 10?穿?憭批???嚗? 24h: 1500??
    elif symbol in ("SOL", "XRP", "DOGE"):
        return (50_000.0, 5_000_000.0)  # 1h: 5?穿?憭批???嚗? 24h: 500??
    return (30_000.0, 3_000_000.0)  # 1h: 3?穿?憭批???嚗? 24h: 300??

def fetch_liquidation_data(symbol: str) -> Optional[List[Dict]]:
    """敺?CoinGlass ???桐?撟?車??蝞?蝮賣風?莎??寥脩?嚗溶?矽閰虫縑?荔?"""
    if not CG_API_KEY:
        logger.error("CG_API_KEY ?芾身摰??⊥??澆皜? API")
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
            logger.warning(f"{symbol} 皜? API 隢?憭望?嚗??Ⅳ: {resp.status_code}")
            return None

        data = resp.json()
        if not (data.get("success") is True or data.get("code") in (0, "0")):
            logger.warning(
                f"{symbol} 皜? API code: {data.get('code')}, msg: {data.get('msg')}"
            )
            return None

        data_array = data.get("data") or data.get("list") or []
        if not isinstance(data_array, list):
            logger.warning(f"{symbol} 皜??豢??澆??啣虜: {type(data_array)}")
            return None
        
        # 隤輯岫嚗炎?交??瑽??芸??嗾?馳蝔殷?
        if symbol in ["BTC", "ETH", "SOL"] and data_array:
            sample = data_array[-1] if data_array else {}
            logger.debug(f"{symbol} API餈? - ?豢?蝑: {len(data_array)}, ??唬?蝑??: {sample.get('time')}, 甈?: {list(sample.keys())[:8]}")
        
        return data_array
    except Exception as e:
        logger.error(f"?脣? {symbol} 皜??豢???撣? {str(e)}")
        return None


def process_liquidation_data(symbol: str, data_array: List[Dict]) -> Optional[Dict]:
    """??皜??豢?嚗?瑟?阡??唳扔蝡舐???瑼鳴?餈?鈭辣?膩嚗?脩?嚗耨敺拇????嚗?"""
    try:
        if not data_array:
            logger.debug(f"{symbol} 皜??豢??箇征")
            return None

        now_ms = int(time.time() * 1000)
        twenty_four_hours_ago = now_ms - 24 * 60 * 60 * 1000
        one_hour_ago = now_ms - 60 * 60 * 1000

        buy_vol_usd_24h = 0.0
        sell_vol_usd_24h = 0.0
        buy_vol_usd_1h = 0.0
        sell_vol_usd_1h = 0.0

        # 隤輯岫嚗炎?交??瑽??芸??嗾?馳蝔殷?
        if symbol in ["BTC", "ETH", "SOL"] and data_array:
            sample_item = data_array[-1] if data_array else {}
            logger.debug(f"{symbol} ?豢?璅? - ???? {sample_item.get('time')}, 甈?: {list(sample_item.keys())[:5]}")

        # 敺?敺??甇瘀?蝝臬??餈?24 撠???1 撠???蝞?
        items_in_24h = 0
        items_in_1h = 0
        
        for item in reversed(data_array):
            try:
                item_time_raw = item.get("time") or item.get("timestamp") or 0
                
                # ?????喉??航?舀神蝘?蝘?
                if isinstance(item_time_raw, str):
                    item_time = int(float(item_time_raw))
                else:
                    item_time = int(item_time_raw)
                
                # 憒????喟?韏瑚??舐?嚗???1e12嚗?頧??箸神蝘?
                if item_time < 1e12:
                    item_time = item_time * 1000
                
            except (TypeError, ValueError) as e:
                logger.debug(f"{symbol} ???唾圾?仃?? {item_time_raw}, ?航炊: {str(e)}")
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

        # 隤輯岫?亥?嚗撠?撟曉馳蝔格??嗆?撣豢?嚗?
        if symbol in ["BTC", "ETH", "SOL"] or (items_in_1h == 0 and items_in_24h > 0):
            logger.debug(f"{symbol} ??蝭?蝯梯? - 24h?? {items_in_24h} 蝑? 1h?? {items_in_1h} 蝑? 蝮賣?? {len(data_array)} 蝑?")

        # 憒? 24h 瘝???冽??唬?蝑?銝???摩嚗?
        if buy_vol_usd_24h == 0 and sell_vol_usd_24h == 0 and data_array:
            latest = data_array[-1]
            buy_vol_usd_24h = float(latest.get("aggregated_long_liquidation_usd") or latest.get("long_liquidation_usd") or latest.get("long") or 0)
            sell_vol_usd_24h = float(latest.get("aggregated_short_liquidation_usd") or latest.get("short_liquidation_usd") or latest.get("short") or 0)
            buy_vol_usd_1h = buy_vol_usd_24h
            sell_vol_usd_1h = sell_vol_usd_24h

            logger.debug(f"{symbol} ?芣??24 撠??扳???寧??唬?蝑?蝞???")

        total_vol_usd_24h = buy_vol_usd_24h + sell_vol_usd_24h
        total_vol_usd_1h = buy_vol_usd_1h + sell_vol_usd_1h
        threshold_1h, threshold_24h = get_liquidation_threshold(symbol)

        # 閮?撖阡?皜??豢?靘矽閰?
        logger.info(
            f"{symbol} 1h: ${total_vol_usd_1h/10000:.2f}??(?瑼? ${threshold_1h/10000:.2f}??, "
            f"24h: ${total_vol_usd_24h/10000:.2f}??(?瑼? ${threshold_24h/10000:.2f}??"
        )

        # ?芣炎??撠??瑼鳴??芣??1撠???瑼餅????
        triggered_by_1h = total_vol_usd_1h >= threshold_1h
        
        if not triggered_by_1h:
            logger.debug(
                f"{symbol} ?1h: {total_vol_usd_1h/10000:.2f}??< {threshold_1h/10000:.2f}??"
            )
            return None

        # ?斗銝餃?皜??孵?嚗??撠??豢?嚗?
        is_long_dom = buy_vol_usd_1h > sell_vol_usd_1h
        dominant_side = "憭嚗?憭??撞??嚗?" if is_long_dom else "蝛箏嚗?蝛綽?????嚗?"
        dominant_amount_1h = buy_vol_usd_1h if is_long_dom else sell_vol_usd_1h

        logger.info(
            f"{symbol} ?? 1h: ${(buy_vol_usd_1h + sell_vol_usd_1h)/10000:.2f}??"
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
        logger.error(f"?? {symbol} 皜??豢???隤? {str(e)}")
        return None


# 蝘駁 generate_liq_symbol_analysis ?賣嚗???閬那?瑟?摮?


def format_liquidity_consolidated_message(events: List[Dict]) -> str:
    """?蜓??蝞瑟撅?鈭箸??潭?鞎芸帚嚗葆銵蝐Ⅳ?擐?"""
    now_str = datetime.now(TAIPEI_TZ).strftime("%H:%M")
    lines = []
    lines.append("?弩 *?蜓??蝞?繚 ?踹??琿???")
    lines.append("????????????????????")
    total_vol = sum(e.get("totalVolUsd1h", 0) for e in events)
    lines.append(f"?? ?1撠?嚗?撟?車??*${total_vol / 10000:.0f}??")
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

        if "憭?" in side:
            icon = "?"
            title = "憭???滿 ??撣嗉?蝐Ⅳ?箇"
            advice = "?? ?雿???嚗迫?身?雿?銝 1%"
            entry_action = "???脣?"
        else:
            icon = "?"
            title = "蝛箄???滿 ??頠征銵?韏瑞?"
            advice = "?? ?葫蝣箄?銝雿?嚗餈賜征??敺?頧?"
            entry_action = "?賊??脣?"

        lines.append(f"{icon} *{sym}* ? ??*${amt:.1f}??")
        lines.append(f"?? {title}")
        if rsi_lbl:
            lines.append(f"?? {rsi_lbl}")
        if pin_lbl:
            lines.append(f"  {pin_lbl}")
        if confirm:
            lines.append(f"??蝣箄?靽∟?{confirm}")
        # 撱箄降?脣???
        if entry_low and entry_high and cur_price:
            lines.append(f"? *{entry_action}*{_low}` ~ `${entry_high}`嚗??`${cur_price:.4f}`嚗?")
        lines.append(f"? 蝑{advice}")
        lines.append("")

    lines.append("????????????????????")
    lines.append(f"??{now_str} | ?乩犖??痕憍迎?撣嗉?蝐Ⅳ?擐?")
    return "\n".join(lines)


def _fetch_liq_radar_analysis_1m(symbol: str) -> Dict:
    """?箸撅????1m K 蝺?閮? RSI ?銝蔣蝺??耦????    ?芸?雿輻 CoinGlass /api/futures/price/history嚗nterval=1m嚗?
    憭望?????BingX 1m K 蝺?    餈?嚗"rsi": float|None, "has_pin": bool, "lower_shadow_ratio": float,
           "cur_price": float|None, "entry_zone_low": float|None, "entry_zone_high": float|None}
    """
    result: Dict = {"rsi": None, "has_pin": False, "lower_shadow_ratio": 0.0,
                    "cur_price": None, "entry_zone_low": None, "entry_zone_high": None}
    clean = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()

    opens, highs, lows, closes = [], [], [], []

    # ?? ?芸?嚗oinGlass futures/price/history 1m ??????????????????????????
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
        logger.debug(f"[?踹??琿?-CG] {clean} 1m K蝺撣? {e}")

    # ?? ?嚗ingX 1m K 蝺????????????????????????????????????????????????
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
            logger.debug(f"[?踹??琿?-BX] {clean} 1m K蝺撣? {e}")

    if len(closes) < 15:
        return result

    try:
        # RSI 14 ??
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

        # ??唬???K 蝺??瑚?敶梁?嚗???敶Ｘ??斗
        o, h, l, c_last = opens[-1], highs[-1], lows[-1], closes[-1]
        if h > l:
            body = abs(c_last - o)
            lower_shadow = min(o, c_last) - l  # 銝蔣蝺摨?
            total_range = h - l
            upper_shadow = h - max(o, c_last)
            # 銝蔣蝺?蝮賣撟?40% 隞乩? + 銝蔣蝺?> 撖阡???2 ??
            lower_shadow_ratio = lower_shadow / total_range if total_range > 0 else 0.0
            result["lower_shadow_ratio"] = round(lower_shadow_ratio, 3)
            result["has_pin"] = (
                lower_shadow_ratio >= 0.40
                and (body == 0 or lower_shadow >= body * 2.0)
                and lower_shadow >= upper_shadow  # 銝蔣蝺?銝蔣蝺
            )

        # ?曉?遣霅圈脣????箸?餈?5 ??K 蝺?雿? + 2% 蝺抵?嚗?
        result["cur_price"] = closes[-1]
        recent_low = min(lows[-5:]) if len(lows) >= 5 else lows[-1]
        recent_high = max(highs[-5:]) if len(highs) >= 5 else highs[-1]
        result["entry_zone_low"] = round(recent_low * 0.995, 6)   # 雿?銝 0.5%
        result["entry_zone_high"] = round(min(c_last * 1.003, recent_high * 0.998), 6)  # ?曉銝 0.3%

        logger.info(
            f"[?踹???] {symbol} RSI={result['rsi']} has_pin={result['has_pin']} "
            f"lower_shadow_ratio={result['lower_shadow_ratio']:.2f} cur={closes[-1]}"
        )
    except Exception as e:
        logger.warning(f"[?踹?RSI] {symbol} 1m ??憭望?: {e}")
    return result


def _fetch_liq_coin_list_snapshot() -> Dict[str, Dict]:
    """???典??游馳蝔桃??翰?改?liq_coin_list嚗?? base -> {long_usd, short_usd, total_usd}??    ?冽?琿????翰??箝迤?函???撟?車嚗?蝑??馳頛芾岷??    """
    out: Dict[str, Dict] = {}
    logger.debug(f"[?翰?吞 endpoint={CG_EP['liq_coin_list']}")
    try:
        j = _cg_get(CG_EP["liq_coin_list"], {"timeType": "0"})  # timeType=0=?1撠?
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
        logger.debug(f"[?翰?吞 閫????{len(out)} 撟?車???")
    except Exception as e:
        logger.debug(f"[?翰?吞 ?啣虜: {e}")
    return out


def run_liquidity_radar_once():
    """銝餅?蝔?瘚??抒????瑁?銝甈∴??拙?????HTTP 閫貊嚗?    ?????Ⅱ隤蕪蝬莎?
      1. liq_coin_list ?翰?改???箸迤?函???撟?車嚗?馳頛芾岷嚗?      2. RSI 1m 璆萇垢???<20 頞都 / >80 頞眺嚗? ?瑚?/銝蔣蝺?敶Ｘ?
      3. 蝝舐?鞎餌?頛蝣箄?嚗征?剝???+ 憭??= ?征璈?憭改?
    """
    logger.info(f"???瑁?瘚??抒???銝?蝣箄?????嚗 {len(LIQ_SYMBOLS)} ?馳蝔?..")

    # ?芸???liq_coin_list 敹怎?曉?歇?函???撟?車
    liq_snapshot = _fetch_liq_coin_list_snapshot()
    hot_symbols = set()
    if liq_snapshot:
        # ?瑼鳴??1撠??? > 50??USD
        for base_s, data_s in liq_snapshot.items():
            if data_s["total_usd"] >= 500_000:
                hot_symbols.add(base_s)
        logger.info(f"[?踹??琿?] 敹怎?曉 {len(hot_symbols)} ?暺馳蝔殷???50?柿SD嚗?{sorted(hot_symbols)[:10]}")

    # ?蔥 LIQ_SYMBOLS ?翰?抒暺??梢??芸?嚗?
    scan_symbols = []
    for sym_s in LIQ_SYMBOLS:
        base_s = sym_s.replace("USDT", "").replace("-", "").upper()
        is_hot = base_s in hot_symbols
        scan_symbols.append((sym_s, is_hot))
    # ?梢??芸???
    scan_symbols.sort(key=lambda x: (0 if x[1] else 1))

    events: List[Dict] = []

    for idx, (symbol, is_hot_sym) in enumerate(scan_symbols):
        base_sym = symbol.replace("USDT", "").replace("-", "").upper()
        try:
            # 敹怎?蔭?蕪嚗??梢?撟?車銝翰?扳?鞈????? OI ?瑼?
            snap_data = liq_snapshot.get(base_sym)
            if snap_data:
                logger.debug(f"[?踹??琿?] {symbol} 敹怎?? ${snap_data['total_usd']/1e6:.2f}M"
                             f" 憭?{snap_data['long_usd']/1e6:.2f}M 蝛?{snap_data['short_usd']/1e6:.2f}M")

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

            # 銝?蝣箄?嚗SI 璆萇垢???+ ?耦??+ 蝝舐?鞎餌?
            analysis = _fetch_liq_radar_analysis_1m(symbol)
            rsi_1m = analysis.get("rsi")
            has_pin = analysis.get("has_pin", False)
            lower_shadow_ratio = analysis.get("lower_shadow_ratio", 0.0)
            cur_price = analysis.get("cur_price")
            entry_low = analysis.get("entry_zone_low")
            entry_high = analysis.get("entry_zone_high")

            dominant_side = event.get("dominantSide", "")
            is_long_liq = "憭? in dominant_side  # 憭?????寞?亥?"

            rsi_ok = False
            rsi_label = ""
            pin_label = ""
            confirm_reason = []

            if rsi_1m is None:
                rsi_ok = True
                rsi_label = "RSI ?芰Ⅱ隤?鞈?銝雲嚗?"
                logger.warning(f"[?踹??琿?] {symbol} ?⊥??? 1m RSI嚗銵?璅??芰Ⅱ隤?")
            else:
                if is_long_liq:
                    if rsi_1m < 20:
                        rsi_ok = True
                        rsi_label = f"? RSI 1m={rsi_1m:.0f} 璆萄漲頞都嚗??※蝡哨?"
                        confirm_reason.append("RSI璆萇垢頞都")
                    elif rsi_1m < 25:
                        rsi_ok = True
                        rsi_label = f"? RSI 1m={rsi_1m:.0f} 瘛勗漲頞都"
                        confirm_reason.append("RSI瘛勗漲頞都")
                else:
                    if rsi_1m > 80:
                        rsi_ok = True
                        rsi_label = f"? RSI 1m={rsi_1m:.0f} 璆萄漲頞眺嚗?蝛箄※蝡哨?"
                        confirm_reason.append("RSI璆萇垢頞眺")
                    elif rsi_1m > 75:
                        rsi_ok = True
                        rsi_label = f"? RSI 1m={rsi_1m:.0f} 瘛勗漲頞眺"
                        confirm_reason.append("RSI瘛勗漲頞眺")

            # ?耦?蝡Ⅱ隤?
            if has_pin and is_long_liq:
                rsi_ok = True
                pin_label = f"?? ?瑚?敶梁???銝{_ratio:.0%}嚗???銵啁垠敶Ｘ?"
                confirm_reason.append("?耦??")
            elif has_pin and not is_long_liq:
                pin_label = f"?? ?瑚?敶梁???銝蔣敶Ｘ?嚗?頠征銵啁垠"
                confirm_reason.append("?耦??")

            # 蝝舐?鞎餌?頛蝣箄?嚗洵銝?嚗?
            accum_fr_label_liq = ""
            try:
                accum_data_liq = fetch_accumulated_funding_score(symbol)
                sq_risk = accum_data_liq.get("squeeze_risk") or "neutral"
                if is_long_liq and sq_risk == "short_squeeze":
                    # 憭??+ 蝛粹鞎餌?? = ?征???敺??歹???璈?璆萄之
                    accum_fr_label_liq = f"? 蝛粹鞎餌??({accum_data_liq.get('accumulated_7d',0)*100:.2f}%蝝舐?)嚗?蝛箸??楊憭?"
                    if not rsi_ok:  # 鞎餌?璇辣?臭誑?典?鋆撥
                        confirm_reason.append("鞎餌??征")
                        rsi_ok = True
                        rsi_label = rsi_label or "鞎餌??征鋆撥"
                elif not is_long_liq and sq_risk == "long_squeeze":
                    accum_fr_label_liq = f"??憭鞎餌??({accum_data_liq.get('accumulated_7d',0)*100:.2f}%蝝舐?)嚗捏憭??之"
            except Exception:
                pass

            if not rsi_ok:
                logger.info(f"[?踹??琿?] {symbol} ???芷?銝?蝣箄? RSI={rsi_1m} pin={has_pin}嚗歲??")
                if idx < len(scan_symbols) - 1:
                    time.sleep(LIQ_REQUEST_DELAY)
                continue

            event["rsi_1m"] = rsi_1m
            event["rsi_label"] = rsi_label
            event["pin_label"] = pin_label
            event["confirm_reason"] = "??".join(confirm_reason) if confirm_reason else "璇辣?曇?"
            event["accum_fr_label"] = accum_fr_label_liq
            event["is_hot"] = is_hot_sym
            event["snap_total_usd"] = snap_data["total_usd"] if snap_data else 0
            event["cur_price"] = cur_price
            event["entry_zone_low"] = entry_low
            event["entry_zone_high"] = entry_high
            events.append(event)
            logger.info(f"[?踹??琿?] {symbol} ??蝣箄?{event['confirm_reason']}嚗???冽"
                        + (" ??梢?" if is_hot_sym else ""))

            if idx < len(scan_symbols) - 1:
                time.sleep(LIQ_REQUEST_DELAY)
        except Exception as e:
            logger.error(f"?? {symbol} 瘚??扳???潛??航炊: {str(e)}")

    if not events:
        logger.info("?祆活???∪馳蝔桅??唳扔蝡舐???瑼鳴????芷???蝣箄?嚗SI<20 / ?耦??")
        return

    msg = format_liquidity_consolidated_message(events)
    thread_id = TG_THREAD_IDS.get("liquidity_radar", 3)
    keyboard = {
        "inline_keyboard": [[{"text": "?? ?亦?閰喟敦???", "url": "https://www.coinglass.com/zh-TW/LiquidationData"}]]
    }
    send_telegram_message(msg, thread_id, parse_mode="Markdown", reply_markup=keyboard)
    logger.info(f"瘚??抒??????券?{len(events)} ?馳蝔殷???蝣箄???嚗?")


# ==================== 9. 撅勗祠??琿?嚗ltcoin Season + RSI + Buy Ratio嚗?====================

def _coinglass_simple_get(path: str, params: Optional[Dict] = None) -> Optional[Dict]:
    """蝪∪???GET嚗蜓閬策 Altseason / RSI ???格活?亥岷??"""
    if not CG_API_KEY:
        logger.error("CG_API_KEY ?芾身摰??⊥??澆 CoinGlass API")
        return None
    url = f"{CG_API_BASE}{path}"
    headers = {
        "accept": "application/json",
        "CG-API-KEY": CG_API_KEY,
    }
    try:
        resp = requests.get(url, headers=headers, params=params or {}, timeout=10)
        if resp.status_code != 200:
            logger.error(f"CoinGlass API HTTP ?航炊 {path}: {resp.status_code} - {resp.text[:200]}")
            return None
        data = resp.json()
        if data.get("code") not in (0, "0", 200, "200", None) and not data.get("success", True):
            logger.error(f"CoinGlass API 餈??航炊 {path}: {data}")
            return None
        return data
    except Exception as e:
        logger.error(f"CoinGlass API 隢?憭望? {path}: {str(e)}")
        return None


def fetch_altseason_index() -> Optional[float]:
    """??撅勗祠摮????(0-100)"""
    data = _coinglass_simple_get("/api/index/altcoin-season")
    if not data:
        logger.warning("Altseason API ??箇征")
        return None

    # 閮????豢?蝯?隞乩噶隤輯岫
    logger.debug(f"Altseason API ???: {json.dumps(data, ensure_ascii=False)[:500]}")

    # ?岫憭車?航???瑽?
    val = None
    
    # 1) 憒? data ??dict
    if isinstance(data.get("data"), dict):
        inner = data["data"]
        # ?岫?游??航??雿?蝔?
        for key in ("value", "index", "altcoinSeasonIndex", "altcoin_season_index", 
                    "seasonIndex", "season_index", "altcoinIndex", "altcoin_index",
                    "score", "ratio", "percentage"):
            if inner.get(key) is not None:
                val = inner.get(key)
                logger.debug(f"敺?data[dict] 銝剜?唳?雿?{key}: {val}")
                break
    
    # 2) 憒? data ??list
    elif isinstance(data.get("data"), list) and data["data"]:
        # ??敺?蝑???啁?嚗?
        inner = data["data"][-1]
        if isinstance(inner, dict):
            for key in ("value", "index", "altcoinSeasonIndex", "altcoin_season_index",
                        "seasonIndex", "season_index", "altcoinIndex", "altcoin_index",
                        "score", "ratio", "percentage"):
                if inner.get(key) is not None:
                    val = inner.get(key)
                    logger.debug(f"敺?data[list][-1] 銝剜?唳?雿?{key}: {val}")
                    break
    
    # 3) ?湔?券?撅斗
    if val is None:
        for key in ("value", "index", "altcoinSeasonIndex", "altcoin_season_index",
                    "seasonIndex", "season_index", "altcoinIndex", "altcoin_index",
                    "score", "ratio", "percentage"):
            if data.get(key) is not None:
                val = data.get(key)
                logger.debug(f"敺?撅斗?唳?雿?{key}: {val}")
                break
    
    # 4) 憒???曆??堆??岫?風???潭?雿?
    if val is None:
        def find_numeric_value(obj, depth=0):
            if depth > 3:  # ?踹??艘憭芣楛
                return None
            if isinstance(obj, (int, float)):
                if 0 <= obj <= 100:  # 撅勗祠摮???豢?閰脣 0-100 銋?
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
            logger.debug(f"??瘛勗漲???曉?詨? {val}")

    # 頧???float
    if val is not None:
        try:
            result = float(val)
            # 撽?蝭?
            if 0 <= result <= 100:
                logger.info(f"???? Altseason ?: {result}")
                return result
            else:
                logger.warning(f"Altseason ?頞蝭? (0-100): {result}")
        except (TypeError, ValueError) as e:
            logger.warning(f"Altseason ?頧?憭望?: {val} - {str(e)}")
    
    logger.warning(f"?⊥?敺?Altseason API ?銝剜????賂????豢?: {json.dumps(data, ensure_ascii=False)[:500]}")
    return None


def describe_altseason(index_val: Optional[float]) -> str:
    if index_val is None:
        return "鞈??怎撩嚗?瘜?蝣箏?瑟撅勗祠摮???舀??孵馳摮??"
    if index_val > 75:
        return "?? 撅勗祠摮??甇∴?鞈?憭批?瘚?撅勗祠撟??瘜Ｗ??◢?芸?甇交憭改?撠馳?湔撞?渲?璈?璆菟???"
    if index_val < 25:
        return "? 瘥撟?迤嚗??蜓閬?蝜?BTC 蝑蜓瘚??ｇ?撅勗祠?格撞?航??閬?蝑???"
    return "??鞈??冽??孵馳?控撖其??撠?銵∴??蝢銵函?湧?閬?"


def fetch_rsi_list() -> List[Dict]:
    """?? RSI ?”銝西???皞???dict list嚗?靘陷 pandas"""
    data = _coinglass_simple_get("/api/futures/rsi/list")
    if not data:
        return []

    raw = data.get("data") or data.get("list") or []
    if not isinstance(raw, list) or not raw:
        logger.warning("RSI ?”?箇征?撘撣?")
        return []

    # 璅???雿?蝔?
    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        
        # ??symbol 甈?
        symbol = None
        for key in ["symbol", "pair", "coin", "symbolName"]:
            if key in item:
                symbol = str(item[key])
                break
        if not symbol:
            continue

        # ??RSI 甈?
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

        # ?暹?鈭日?甈?
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


# ?? ?典???RSI ?寞活敹怠?嚗????冽???甈⊥折?頛?enrichment ?湔?亥”嚗?????????????
_cg_rsi_bulk_cache: Dict[str, Any] = {"ts": 0.0, "data": {}}  # {base: {rsi_15m, rsi_1h, ...}}
_CG_RSI_BULK_TTL = 120.0  # 2 ?? TTL嚗???15m ?望?


def fetch_cg_rsi_bulk(interval: str = "15m") -> Dict[str, Optional[float]]:
    """?寞活???典???RSI嚗oinGlass /api/futures/rsi/list嚗?    ? {base_symbol: rsi_float} dict嚗翰??2 ????    ??fetch_position_change ??????思?甈∴?enrichment ?挾?湔 dict ?亥”嚗?    銝?閬?瘥馳蝔桀?典??API??    """
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
            logger.debug(f"[RSI?寞活] HTTP {r.status_code}")
            return _cg_rsi_bulk_cache.get("data", {})
        j = r.json()
        if j.get("code") not in (0, "0", 200, "200", None):
            logger.debug(f"[RSI?寞活] code={j.get('code')}")
            return _cg_rsi_bulk_cache.get("data", {})

        raw = j.get("data") or j.get("list") or (j if isinstance(j, list) else [])
        out: Dict[str, Optional[float]] = {}
        for item in (raw if isinstance(raw, list) else []):
            if not isinstance(item, dict):
                continue
            # symbol 甈?
            sym = (item.get("symbol") or item.get("pair") or item.get("coin") or "").upper()
            sym = sym.replace("USDT", "").replace("-", "").replace("_", "").strip()
            if not sym:
                continue
            # RSI ?潘??芸??? interval嚗?閰血?蝔格?雿?
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
            # ?交?曉嚗?閰衣?亦? rsi 甈?
            if rsi_val is None:
                for k in ("rsi", "RSI", "rsiValue", "rsi_value", "value"):
                    if k in item and item[k] is not None:
                        try:
                            rsi_val = float(item[k])
                            break
                        except (TypeError, ValueError):
                            pass
            out[sym] = rsi_val

        logger.info(f"[RSI?寞活] ?? {len(out)} ?馳蝔?{interval} RSI嚗oinGlass rsi/list嚗?")
        _cg_rsi_bulk_cache = {"ts": now, "data": out}
        return out
    except Exception as e:
        logger.debug(f"[RSI?寞活] ?啣虜: {e}")
        return _cg_rsi_bulk_cache.get("data", {})


def fetch_buy_ratio(symbol: str) -> Optional[float]:
    """
    餈撮閮??馳蝔桃? Buy Ratio嚗???瘛勗漲餈撮嚗ids / (bids + asks)嚗?    雿輻 /api/futures/orderbook/aggregated-ask-bids-history
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
        # ?岫憭車甈??迂
        bid_keys = [k for k in last.keys() if "bid" in k.lower()]
        ask_keys = [k for k in last.keys() if "ask" in k.lower()]
        bid_val = float(last.get(bid_keys[0]) or 0) if bid_keys else 0.0
        ask_val = float(last.get(ask_keys[0]) or 0) if ask_keys else 0.0
    elif isinstance(last, list):
        # ?身蝯? [bids, asks, time] ??[asks, bids, time]嚗??捆??
        numeric = [x for x in last if isinstance(x, (int, float))]
        if len(numeric) >= 2:
            # ?身蝚砌?? bids嚗洵鈭 asks
            bid_val, ask_val = float(numeric[0]), float(numeric[1])
        else:
            return None
    else:
        return None

    total = bid_val + ask_val
    if total <= 0:
        return None
    return bid_val / total * 100.0  # 頧??曉?瘥?

def fetch_price_history(symbol: str, interval: str = "1h") -> Optional[List[Dict]]:
    """?脣??寞甇瑕?豢?嚗HLC嚗?    瘜冽?嚗oinGlass API v4 ?航瘝??湔??price/history 蝡舫?
    ?ㄐ雿輻 OI history 蝡舫?嚗??箏??虜? markPrice 蝑?潔縑??    """
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
        logger.debug(f"?岫?脣??寞甇瑕 {symbol}嚗蝙??OI history 蝡舫?")
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('code') in ['0', 0, 200, '200']:
                data_list = data.get('data', [])
                if isinstance(data_list, list) and len(data_list) > 0:
                    # 瑼Ｘ?豢?蝯?嚗??臬??澆?畾?
                    sample = data_list[0]
                    sample_keys = list(sample.keys()) if isinstance(sample, dict) else []
                    logger.debug(f"?寞甇瑕?豢?璅? {symbol}: 摮挾 {sample_keys[:15]}")
                    logger.debug(f"?寞甇瑕?豢?璅? {symbol}: ?批捆 {json.dumps(sample, ensure_ascii=False)[:200]}")
                    
                    # {logger.debug(f"敺?OI 蝡舫??脣??唳??{symbol}: {len(data_list)} 璇?)
                    # 頛詨?豢?璅?隞乩噶隤輯岫
                    if isinstance(sample, dict):
                        logger.debug(f"?豢?璅?摮挾: {list(sample.keys())[:20]}")
                    return data_list
        
        logger.debug(f"?⊥?敺?OI 蝡舫??脣??寞?豢? for {symbol} (??Ⅳ: {response.status_code})")
        return None
    except Exception as e:
        logger.warning(f"?脣??寞甇瑕憭望? {symbol}: {str(e)}")
        import traceback
        logger.debug(traceback.format_exc())
        return None


def fetch_aggregated_cvd_history(symbol: str, interval: str = "1h") -> Optional[List[Dict]]:
    """?脣???蝝航??漱?榆?潘?CVD嚗風?脫??    ?芸??岫 /api/futures/aggregated-cvd/history嚗楊鈭斗????嚗?
    憭望?????/api/futures/cvd/history嚗銝鈭斗??嚗?閮?Binance嚗?    蝯曹??璅???list[dict]嚗 time/cvd 甈?嚗?    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    headers_cg = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}

    def _parse_cvd_list(data_list: list) -> Optional[List[Dict]]:
        if not isinstance(data_list, list) or not data_list:
            return None
        # 璅???雿?蝯曹???{"time": ts, "cvd": value}
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

    # ?? ?芸?嚗???CVD嚗?? aggregated嚗????????????????????????????????????
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
                    logger.debug(f"[CVD-??] {base} {interval}: {len(result)} 璇?")
                    return result
    except Exception as e:
        logger.debug(f"[CVD-??] {base} ?啣虜: {e}")

    # ?? ?嚗? CVD嚗inance嚗????????????????????????????????????????????
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
                    logger.debug(f"[CVD-?格?] {base} {interval}: {len(result2)} 璇?")
                    return result2
    except Exception as e:
        logger.debug(f"[CVD-?格?] {base} ?啣虜: {e}")

    logger.debug(f"[CVD] {base} {interval} ?拙垢暺??⊥??")
    return None


def _cvd_change_last2(symbol: str, interval: str = "1h") -> Optional[float]:
    """??餈?2 ??K ??CVD 霈???(Current - Prev)嚗?潮?瞈暸??寡??Ｕ?    fetch_aggregated_cvd_history 撌脣??單?皞? {"time", "cvd"} ?澆?嚗?乩蝙?具?    """
    data = fetch_aggregated_cvd_history(symbol, interval)
    if not data or len(data) < 2:
        return None
    sorted_data = sorted(data, key=lambda x: x.get("time", 0))
    last_two = sorted_data[-2:]
    cvd_vals = []
    for item in last_two:
        # ?芸???皞?甈? "cvd"嚗?閰西?撘?雿?嚗摰寡?鞈?嚗?
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
    """瑼Ｘ葫 CVD ?嚗?瞍???嚗?    餈?: 'bullish' (?撞?), 'bearish' (???), None (?∟???
    
    ?芸??嚗?    - ?游之瘥?蝒??20 ??K 蝺?蝝?24 撠??豢?嚗?    - 撠??嗅??寞????20 ??K 蝺?擃?暺?    - 撠??嗅? CVD ????潮?雿??? CVD ??    """
    try:
        # ?脣??餈?24 撠???1h ?豢?
        logger.info(f"CVD ?瑼Ｘ葫 {symbol}: ??瑼Ｘ葫...")
        price_data = fetch_price_history(symbol + "USDT", "1h")
        base_symbol = symbol.replace("USDT", "")
        cvd_data = fetch_aggregated_cvd_history(base_symbol, "1h")
        
        logger.info(f"CVD ?瑼Ｘ葫 {symbol}: ?脣??啣?潭??{len(price_data) if price_data else 0} 璇? CVD ?豢? {len(cvd_data) if cvd_data else 0} 璇?")
        
        if not price_data or not cvd_data:
            logger.info(f"CVD ?瑼Ｘ葫 {symbol}: ?豢?銝雲嚗?? {len(price_data) if price_data else 0}, CVD: {len(cvd_data) if cvd_data else 0}嚗?")
            return None
        
        if len(price_data) < 20 or len(cvd_data) < 20:
            logger.info(f"CVD ?瑼Ｘ葫 {symbol}: ?豢?暺?頞喉??閬撠?20 ???寞: {len(price_data)}, CVD: {len(cvd_data)}嚗?")
            return None
        
        # 摰儔???萄?賂??? None ?潘?
        def get_sort_key(x):
            time_val = x.get('time') or x.get('timestamp') or x.get('t') or 0
            if isinstance(time_val, str):
                try:
                    return int(time_val)
                except:
                    return 0
            return int(time_val) if time_val else 0
        
        # 蝣箔??豢?????摨?
        price_sorted = sorted(price_data, key=get_sort_key)
        cvd_sorted = sorted(cvd_data, key=get_sort_key)
        
        # ??餈?20 ??K 蝺?
        p_slice = price_sorted[-20:]
        c_slice = cvd_sorted[-20:]
        
        # ???寞???拙?賂??岫憭車摮挾嚗?
        def extract_price(item: Dict, field: str) -> Optional[float]:
            """敺??銝剜???澆?畾?"""
            if not isinstance(item, dict):
                return None
            if field in item:
                val = item[field]
                if isinstance(val, (int, float)) and val > 0:
                    return float(val)
            return None
        
        # ???嗅? K 蝺? high ??low
        curr_item = p_slice[-1]
        curr_p_high = extract_price(curr_item, 'high') or extract_price(curr_item, 'markPrice') or extract_price(curr_item, 'mark_price') or extract_price(curr_item, 'close') or extract_price(curr_item, 'price') or extract_price(curr_item, 'value')
        curr_p_low = extract_price(curr_item, 'low') or extract_price(curr_item, 'markPrice') or extract_price(curr_item, 'mark_price') or extract_price(curr_item, 'close') or extract_price(curr_item, 'price') or extract_price(curr_item, 'value')
        
        if not curr_p_high or not curr_p_low:
            logger.info(f"CVD ?瑼Ｘ葫 {symbol}: ?high: {curr_p_high}, low: {curr_p_low}嚗??豢?璅?摮挾: {list(curr_item.keys())[:10]}")
            return None
        
        # ???嗅? K 蝺? CVD
        curr_cvd_item = c_slice[-1]
        curr_cvd = None
        # 瘛餃?撖阡???畾萄?蝔梧?cum_vol_delta嚗敞閮?鈭日?撌桀潘?
        for key in ['cum_vol_delta', 'cvd', 'value', 'close', 'cvdValue', 'cumulativeVolumeDelta', 'volumeDelta', 'agg_taker_buy_vol', 'agg_taker_sell_vol']:
            if key in curr_cvd_item:
                val = curr_cvd_item[key]
                if isinstance(val, (int, float)) and val != 0:
                    curr_cvd = float(val)
                    logger.debug(f"CVD ?瑼Ｘ葫 {symbol}: 敺?畾?'{key}' ???CVD: {curr_cvd}")
                    break
        
        if curr_cvd is None:
            logger.info(f"CVD ?瑼Ｘ葫 {symbol}: ?⊥????嗅? CVD ?潘?CVD ?豢?璅?摮挾: {list(curr_cvd_item.keys())[:10]}")
            return None
        
        # ?曉? 19 ??K 蝺??擃??雿
        prev_prices_high = []
        prev_prices_low = []
        
        # 頛詨蝚砌?????K 蝺?摮挾隞乩噶隤輯岫
        if len(p_slice) > 1:
            sample_prev_item = p_slice[0]
            logger.debug(f"CVD ?瑼Ｘ葫 {symbol}: ? K 蝺見?砍?畾? {list(sample_prev_item.keys())[:15]}")
        
        for idx, item in enumerate(p_slice[:-1]):  # ? 19 ??
            if not isinstance(item, dict):
                continue
                
            # ?岫?? high嚗?蝙??high嚗?????雿輻?嗡?摮挾嚗?
            high = extract_price(item, 'high')
            if not high:
                # 憒?瘝? high嚗?閰虫蝙?典隞?澆?畾?
                high = extract_price(item, 'markPrice') or extract_price(item, 'mark_price') or extract_price(item, 'close') or extract_price(item, 'price') or extract_price(item, 'value')
            
            # ?岫?? low嚗?蝙??low嚗?????雿輻?嗡?摮挾嚗?
            low = extract_price(item, 'low')
            if not low:
                # 憒?瘝? low嚗?閰虫蝙?典隞?澆?畾?
                low = extract_price(item, 'markPrice') or extract_price(item, 'mark_price') or extract_price(item, 'close') or extract_price(item, 'price') or extract_price(item, 'value')
            
            # 憒??瘝?嚗?閰行???澆?畾?
            if not high or not low:
                for key, val in item.items():
                    if isinstance(val, (int, float)) and val > 0:
                        key_lower = key.lower()
                        # 頝喲??＊銝?寞??畾?
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
            logger.info(f"CVD ?瑼Ｘ葫 {symbol}: ?high: {len(prev_prices_high)}, low: {len(prev_prices_low)}嚗??嗅? K 蝺?畾? {list(p_slice[-1].keys())[:15] if p_slice else []}")
            return None
        
        prev_p_high = max(prev_prices_high)
        prev_p_low = min(prev_prices_low)
        
        # ?脣??擃??雿撠???CVD ??
        # ?曉?擃撠??揣撘?雿輻?游祝擛??寥?嚗?唳??亥??潘?
        high_idx = None
        min_diff = float('inf')
        for idx, item in enumerate(p_slice[:-1]):
            high = extract_price(item, 'high') or extract_price(item, 'markPrice') or extract_price(item, 'mark_price') or extract_price(item, 'close') or extract_price(item, 'price') or extract_price(item, 'value')
            if high:
                diff = abs(high - prev_p_high)
                if diff < min_diff:
                    min_diff = diff
                    high_idx = idx
                    if diff < 0.01:  # 憒??曉?虜?亥??潘??湔雿輻
                        break
        
        # ?曉?雿撠??揣撘?雿輻?游祝擛??寥?嚗?唳??亥??潘?
        low_idx = None
        min_diff = float('inf')
        for idx, item in enumerate(p_slice[:-1]):
            low = extract_price(item, 'low') or extract_price(item, 'markPrice') or extract_price(item, 'mark_price') or extract_price(item, 'close') or extract_price(item, 'price') or extract_price(item, 'value')
            if low:
                diff = abs(low - prev_p_low)
                if diff < min_diff:
                    min_diff = diff
                    low_idx = idx
                    if diff < 0.01:  # 憒??曉?虜?亥??潘??湔雿輻
                        break
        
        if high_idx is None or low_idx is None:
            logger.info(f"CVD ?瑼Ｘ葫 {symbol}: ?⊥??曉撠???潛揣_idx: {high_idx}, low_idx: {low_idx}, ??擃: {prev_p_high:.4f}, ??雿: {prev_p_low:.4f}嚗?")
            return None
        
        # ??撠?蝝Ｗ???CVD ??
        cvd_at_p_high = None
        cvd_at_p_low = None
        
        if high_idx < len(c_slice[:-1]):
            high_cvd_item = c_slice[high_idx]
            # 瘛餃?撖阡???畾萄?蝔梧?cum_vol_delta嚗敞閮?鈭日?撌桀潘?
            for key in ['cum_vol_delta', 'cvd', 'value', 'close', 'cvdValue', 'cumulativeVolumeDelta', 'volumeDelta', 'agg_taker_buy_vol', 'agg_taker_sell_vol']:
                if key in high_cvd_item:
                    val = high_cvd_item[key]
                    if isinstance(val, (int, float)) and val != 0:
                        cvd_at_p_high = float(val)
                        break
        
        if low_idx < len(c_slice[:-1]):
            low_cvd_item = c_slice[low_idx]
            # 瘛餃?撖阡???畾萄?蝔梧?cum_vol_delta嚗敞閮?鈭日?撌桀潘?
            for key in ['cum_vol_delta', 'cvd', 'value', 'close', 'cvdValue', 'cumulativeVolumeDelta', 'volumeDelta', 'agg_taker_buy_vol', 'agg_taker_sell_vol']:
                if key in low_cvd_item:
                    val = low_cvd_item[key]
                    if isinstance(val, (int, float)) and val != 0:
                        cvd_at_p_low = float(val)
                        break
        
        if cvd_at_p_high is None or cvd_at_p_low is None:
            logger.info(f"CVD ?瑼Ｘ葫 {symbol}: ?⊥???撠???CVD ?_idx: {high_idx}, low_idx: {low_idx}, cvd_at_p_high: {cvd_at_p_high}, cvd_at_p_low: {cvd_at_p_low}嚗?")
            return None
        
        # ???嚗?澆擃?雿?CVD 雿?嗆?擃???CVD
        if curr_p_high > prev_p_high and curr_cvd < cvd_at_p_high:
            logger.info(f"CVD ?瑼Ｘ葫 {symbol}: ????? (?寞: {curr_p_high:.4f} > {prev_p_high:.4f}, CVD: {curr_cvd:.2f} < {cvd_at_p_high:.2f})")
            return 'bearish'
        
        # ?撞?嚗?澆雿?雿?CVD 擃?嗆?雿???CVD
        if curr_p_low < prev_p_low and curr_cvd > cvd_at_p_low:
            logger.info(f"CVD ?瑼Ｘ葫 {symbol}: ???撞? (?寞: {curr_p_low:.4f} < {prev_p_low:.4f}, CVD: {curr_cvd:.2f} > {cvd_at_p_low:.2f})")
            return 'bullish'
        
        logger.info(f"CVD ?瑼Ｘ葫 {symbol}: ?∟??Ｖ縑??(?嗅??寞: {curr_p_high:.4f}/{curr_p_low:.4f}, ?擃?: {prev_p_high:.4f}/{prev_p_low:.4f}, ?CVD: {curr_cvd:.2f})")
        return None
        
    except Exception as e:
        logger.error(f"CVD ?瑼Ｘ葫?粹 {symbol}: {str(e)}")
        import traceback
        logger.debug(traceback.format_exc())
        return None


def build_altseason_message() -> Optional[str]:
    """?控撖冽撖?頠??踹?頛芸?嚗撥??撘瑯?"""
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
    lines.append("? *?控撖冽撖?頠?")
    lines.append("????????????????????")
    season_status = "?儭?瘥撟?銵銝?(?脣?)"
    if index_val is not None and index_val > 70:
        season_status = "?? 蝢日?鈭? (撅勗祠摮?"
    elif index_val is not None and index_val > 40:
        season_status = "?? 鞈?頛芸?銝?(?詨馳)"
    lines.append(f"?? *?嗅??{_status}")
    lines.append(f"?? *撅勗祠?*{_val:.0f}` / 100" if index_val is not None else "?? *撅勗祠?*嚗?")
    lines.append("")
    lines.append("? *撘瑕?蝢?(鞈?甇???*")
    if not strong:
        lines.append("?怎撘瑕撟?車嚗??港?餈瑯?")
    else:
        for i, item in enumerate(strong[:5], 1):
            sym = item.get("symbol", "")
            br = item.get("buy_ratio", 50)
            rsi = item.get("rsi_4h") or item.get("rsi_base", 50)
            lines.append(f"{i}. *{sym}* (鞎瑞 {br:.0f}%)")
            lines.append(f"   ?? RSI {rsi:.0f} 嚚??撘瑕?嚗?隤踹??")
    lines.append("")
    lines.append("????????????????????")
    lines.append("? *??敹?*嚗撥??撘瑯撅勗祠摮??銝?鞎瑁敺?瞍脩??嚗?鞎瑕停鞎琿??哨?")
    return "\n".join(lines)


def run_altseason_radar_once():
    """撅勗祠?游???銝餅?蝔??急???"""
    logger.info("???瑁?撅勗祠??琿?...")
    msg = build_altseason_message()
    if not msg:
        logger.warning("?祆活撅勗祠??琿??芾?Ｙ???閮")
        return
    thread_id = TG_THREAD_IDS.get("altseason_radar", 0)
    keyboard = {"inline_keyboard": [[{"text": "? ?亦?撅勗祠摮????", "url": "https://www.blockchaincenter.net/en/altcoin-season-index/"}]]}
    send_telegram_message(msg, thread_id or int(CHAT_ID or 0), parse_mode="Markdown", reply_markup=keyboard)
    logger.info("撅勗祠??琿??冽摰?")


# ==================== 10. Hyperliquid ?唳??Ｙ??====================

HYPERLIQUID_SENT_ALERTS_FILE = DATA_DIR / "hyperliquid_sent_alerts.json"
WHALE_ALERT_THRESHOLD = 200_000  # 靽?靘隞?孵??剁?撖阡??摩?寧???瑼?
SMART_MONEY_PNL_MIN = 50_000  # $50k USD嚗撖穿?
MONEY_PRINTER_PNL_MIN = 500_000  # $50??USD嚗撖穿?

# ???瑼駁?蝵?
_WHALE_MAINSTREAM_COINS = {"BTC", "ETH", "SOL"}
_WHALE_THRESHOLD_MAINSTREAM = 500_000   # 銝餅?撟?$50??_WHALE_THRESHOLD_ALTCOIN_RATIO = 0.005  # 撅勗祠撟??24h ?漱?? 0.5%
_WHALE_THRESHOLD_ALTCOIN_DEFAULT = 50_000  # 撅勗祠撟???湧?瑼?$5??

def _get_whale_threshold(symbol: str, alert: Dict) -> float:
    """?寞?撟?車閮???攳券??瑼颯?    銝餅?撟?(BTC/ETH/SOL) ??$50?砍摰?撅勗祠撟???24h ?漱??? 0.5%嚗鞈???$5?穿???    """
    base = symbol.replace("USDT", "").replace("-PERP", "").replace("PERP", "").strip().upper()
    if base in _WHALE_MAINSTREAM_COINS:
        return _WHALE_THRESHOLD_MAINSTREAM

    # ?岫敺?alert 銝剜???24h ?漱??
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
        return max(dynamic, 10_000)  # ?雿?$1 ?砌?霅?
    return _WHALE_THRESHOLD_ALTCOIN_DEFAULT


def fetch_hyperliquid_whale_alert() -> List[Dict]:
    """?脣? Hyperliquid 攳券???嚗???瑼餌?嚗蜓瘚馳 $50?穿?撅勗祠撟?24h ??0.5%嚗?"""
    url = f"{CG_API_BASE}/api/hyperliquid/whale-alert"
    headers = {
        "CG-API-KEY": CG_API_KEY,
        "accept": "application/json"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.error(f"Hyperliquid Whale Alert API ?航炊: {response.status_code}")
            return []

        result = response.json()
        if result.get('code') not in ['0', 0, 200, '200']:
            logger.error(f"Hyperliquid Whale Alert API 餈??航炊: {result}")
            return []

        data_list = result.get('data', [])
        if not isinstance(data_list, list):
            logger.warning(f"Hyperliquid Whale Alert ?豢??澆??啣虜: {type(data_list)}")
            return []

        # {logger.info(f"Hyperliquid Whale Alert ???豢?: {len(data_list)} 璇?)
        if data_list:
            sample = data_list[0]
            logger.info(f"?豢?璅?甈?: {list(sample.keys())}")
            logger.info(f"?豢?璅?摰?批捆: {json.dumps(sample, ensure_ascii=False, indent=2)}")

        filtered_alerts = []
        value_stats = []  # 隤輯岫??
        for idx, alert in enumerate(data_list):
            value = None
            value_key = None

            # ???摨?閰血?蝔桀?畾萄?蝔梧??芸?雿輻 position_value_usd嚗?
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
                logger.warning(f"Alert #{idx} ?⊥??曉?詨澆?畾蛛????畾? {list(alert.keys())}")
                continue

            try:
                if isinstance(value, str):
                    value_clean = value.replace(',', '').replace('$', '').replace(' ', '').replace('USD', '').replace('usd', '')
                    value_float = float(value_clean)
                else:
                    value_float = float(value)

                sym_raw = alert.get('symbol') or alert.get('coin') or alert.get('asset') or '?芰'
                # 閮????瑼?
                threshold = _get_whale_threshold(str(sym_raw), alert)
                base_sym = str(sym_raw).replace("USDT", "").replace("-PERP", "").replace("PERP", "").strip().upper()
                threshold_label = (
                    f"銝餅?撟?${threshold/10000:.0f}??"
                    if base_sym in _WHALE_MAINSTREAM_COINS
                    else f"撅勗祠?? ${threshold/10000:.1f}??"
                )

                if idx < 10:
                    value_stats.append({
                        'symbol': sym_raw,
                        'key': value_key,
                        'value': value_float,
                        'threshold': threshold,
                        'formatted': f"${value_float/10000:.2f}??(?{_label})"
                    })

                if value_float >= threshold:
                    filtered_alerts.append(alert)
                    logger.info(f"???唳??ａ脣: {sym_raw} - ${value_float/10000:.2f}????{threshold_label} (摮挾: {value_key})")
                else:
                    if idx < 5:
                        logger.info(f"???芷????瑼? {sym_raw} - ${value_float/10000:.2f}??< {threshold_label} (摮挾: {value_key})")
            except (TypeError, ValueError) as e:
                logger.warning(f"Alert #{idx} ?詨潸圾?仃?? 摮{_key}, ??{value}, ?航炊: {str(e)}")
                continue

        if value_stats:
            logger.info("??0璇???詨潛絞閮?")
            for stat in value_stats:
                logger.info(f"  {stat['symbol']}: {stat['formatted']} (摮挾: {stat['key']})")

        logger.info(f"蝚血????瑼餌? Whale Alert: {len(filtered_alerts)} 璇?銝餅?撟?${_WHALE_THRESHOLD_MAINSTREAM/10000:.0f}??| 撅勗祠撟????.5%??")
        return filtered_alerts
    except Exception as e:
        logger.error(f"?脣? Hyperliquid Whale Alert 憭望?: {str(e)}")
        return []


def fetch_hyperliquid_coin_position(symbol: str) -> Optional[Dict]:
    """???敺摰馳蝔桀 Hyperliquid ????撣?憭征?孵???獢踴?璅∴???    ?券??斗 HL 銝??祕???頛攳券???????    endpoint: /api/hyperliquid/position
    """
    base = symbol.replace("USDT", "").replace("-PERP", "").replace("PERP", "").upper()
    logger.debug(f"[HL? {base} endpoint={CG_EP['hl_position']}")
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
            f"[HL? {base}: 憭?${long_pos/1e6:.2f}M 蝛箏?${short_pos/1e6:.2f}M "
            f"憭征{_ratio:.2f}" if ls_ratio else
            f"[HL? {base}: 憭?${long_pos/1e6:.2f}M 蝛箏?${short_pos/1e6:.2f}M"
        )
        return {"long_usd": long_pos, "short_usd": short_pos, "total_usd": total,
                "ls_ratio": ls_ratio, "symbol": base}
    except Exception as e:
        logger.debug(f"[HL? {base} ?啣虜: {e}")
        return None


def fetch_hyperliquid_smart_money_score(symbol: str) -> Dict[str, Any]:
    """???閰?嚗??HL ??撣?+ ?Ｗ????嚗?瑟??靽∪???    閰??摩嚗?      - HL 憭征瘥?> 1.2嚗??凋蜓撠?+ ??Ｗ?雿??????唳??Ｗ?憭?score > 0
      - HL 憭征瘥?< 0.8嚗征?凋蜓撠?+ ??Ｗ?雿??????唳??Ｗ?蝛綽?score < 0
      - ??? + ???????靽∪???
    ?嚗"score": int, "direction": "long"/"short"/"neutral", "label": str, "hl_ls": float}
    """
    base = symbol.replace("USDT", "").replace("-PERP", "").upper()
    cache_key = f"hl_smart:{base}"
    now = time.time()
    if cache_key in _flow_cache:
        val, ts = _flow_cache[cache_key]
        if now - ts < 600:  # 10 ??敹怠?
            return val if val else {"score": 0, "direction": "neutral", "label": "", "hl_ls": None}

    empty = {"score": 0, "direction": "neutral", "label": "", "hl_ls": None}

    # ?? ??HL 撟?車??????????????????????????????????????????????????
    hl_pos = fetch_hyperliquid_coin_position(symbol)
    hl_ls = hl_pos.get("ls_ratio") if hl_pos else None

    # ?? ??HL ?Ｗ????嚗撣嚗??擃????????????????
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
                # ??Ｗ? > 60% = ?唳??Ｙ憓?憭批??賊?刻竟
                if profit_pct > 60:
                    pnl_score = 1
                    pnl_label_part = f"(HL??{_pct:.0f}%)"
                elif profit_pct < 40:
                    pnl_score = -1
                    pnl_label_part = f"(HL?扳??Ｗ?{_pct:.0f}%)"
                logger.debug(f"[HL?唳??兡 {base} ?典??渡??{_pct:.1f}%")
    except Exception as e_pnl:
        logger.debug(f"[HL?唳??兡 {base} ????啣虜: {e_pnl}")

    # ?? ??HL ?Ｗ???撣??斗憭折??vs 撠???????????????????
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
                    pos_dist_label = "憭折??憭?"
                    pnl_score += 1
                elif large_short > large_long * 1.3:
                    pos_dist_label = "憭折??蝛?"
                    pnl_score -= 1
                logger.debug(f"[HL?唳??兡 {base} 憭{_long:.0f} 蝛?{large_short:.0f}")
    except Exception as e_dist:
        logger.debug(f"[HL?唳??兡 {base} ??撣撣? {e_dist}")

    # ?? 蝬?閰? ?????????????????????????????????????????????????????
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
        label = f"? HL?唳??Ｗ?憭?{_score:+d} L/S={hl_ls_str}{pnl_label_part}{' '+pos_dist_label if pos_dist_label else ''})"
    elif direction == "short":
        label = f"? HL?唳??Ｗ?蝛?{_score:+d} L/S={hl_ls_str}{pnl_label_part}{' '+pos_dist_label if pos_dist_label else ''})"
    else:
        label = f"? HL?唳??Ｖ{_str})" if hl_ls else ""

    result = {"score": final_score, "direction": direction, "label": label, "hl_ls": hl_ls}
    logger.info(f"[HL?唳??Ｔ?] {base}: {label}")
    _flow_cache[cache_key] = (result, now)
    return result


def fetch_hyperliquid_pnl_distribution() -> Optional[Dict]:
    """?脣? Hyperliquid ?Ｗ????嚗撣嚗?"""
    logger.debug(f"[HL?] endpoint={CG_EP['hl_wallet_pnl_dist']}")
    try:
        j = _cg_get(CG_EP["hl_wallet_pnl_dist"], {})
        return j.get("data", j) if j else None
    except Exception as e:
        logger.error(f"[HL?] ?啣虜: {e}")
        return None


def fetch_hyperliquid_whale_position() -> List[Dict]:
    """?脣? Hyperliquid 攳券????孵?> $100k嚗?"""
    url = f"{CG_API_BASE}/api/hyperliquid/whale-position"
    headers = {
        "CG-API-KEY": CG_API_KEY,
        "accept": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.error(f"Hyperliquid Whale Position API ?航炊: {response.status_code}")
            return []
        
        result = response.json()
        if result.get('code') not in ['0', 0, 200, '200']:
            logger.error(f"Hyperliquid Whale Position API 餈??航炊: {result}")
            return []
        
        data_list = result.get('data', [])
        if not isinstance(data_list, list):
            return []
        
        # 閮?蝚砌???蝵桃??豢?蝯?隞乩噶隤輯岫嚗?冽??豢???
        if data_list:
            first_item = data_list[0]
            logger.info(f"Hyperliquid Whale Position ?豢?蝯?蝷箔?嚗? 3 ??雿?: {list(first_item.keys())[:10]}")
            logger.info(f"摰?豢?蝯?: {json.dumps(first_item, ensure_ascii=False, indent=2)[:1000]}")
        
        # ?岫????潛?憭車?航甈?
        def get_position_value(item: Dict) -> float:
            # ?岫?湔?潭?雿?
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
            
            # 憒??湔?潔?摮嚗?閰衣 size * price 閮?
            if value == 0 or (isinstance(value, (int, float)) and value == 0):
                size = float(item.get('size') or item.get('position_size') or item.get('positionSize') or 0)
                price = float(item.get('price') or item.get('mark_price') or item.get('markPrice') or 0)
                if size > 0 and price > 0:
                    value = abs(size * price)
            
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0
        
        # ??銝血???5 ??????潘?
        sorted_positions = sorted(
            data_list,
            key=get_position_value,
            reverse=True
        )[:5]
        
        return sorted_positions
    except Exception as e:
        logger.error(f"?脣? Hyperliquid Whale Position 憭望?: {str(e)}")
        return []


def process_smart_money_pnl(pnl_data: Dict) -> Dict:
    """???唳???PNL ???豢?"""
    if not pnl_data or not isinstance(pnl_data, dict):
        return {}
    
    smart_money_info = {
        'money_printers': [],  # > $1M ?脣
        'smart_money': [],     # $100k - $1M ?脣
        'top_symbols': {}
    }
    
    # ?岫閫???惜?豢?
    # ?航??瑽??惜?”??亙??急??
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
            
            # ?脣? PNL 蝭?
            pnl_min = float(item.get('pnl_min') or item.get('pnlMin') or item.get('min_pnl') or 0)
            pnl_max = float(item.get('pnl_max') or item.get('pnlMax') or item.get('max_pnl') or float('inf'))
            address_count = int(item.get('address_count') or item.get('addressCount') or item.get('count') or 0)
            
            # ?斗撅斤?
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
    
    # ?岫?脣???雿??馳蝔殷?
    position_dist = pnl_data.get('position_distribution') or pnl_data.get('top_symbols') or {}
    if isinstance(position_dist, dict):
        # ??銝血???3 ?馳蝔?
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
    """?澆????Whale Alert 閮"""
    symbol = alert.get('symbol') or alert.get('coin') or '?芰'
    direction = alert.get('side') or alert.get('direction') or alert.get('type') or '?芰'
    value = float(
        alert.get('notional_value') or 
        alert.get('notionalValue') or 
        alert.get('value') or 
        0
    )
    
    # ?斗?孵? emoji嚗?憭??撞嚗?蝛???嚗?
    direction_emoji = "?" if str(direction).lower() in ['long', 'buy', '憭?', 'long'] else "?"
    direction_text = "憭折???嚗?瞍莎?" if str(direction).lower() in ['long', 'buy', '憭?', 'long'] else "憭折??征嚗?頝?"
    
    return f"?嚗{symbol}`\n?{_emoji} {direction_text}\n閬芋嚗?{value:,.0f} USD (??孵?"


def format_whale_position_message(position: Dict, index: int) -> str:
    """?澆???祠擳?????"""
    address = position.get('address') or position.get('user') or position.get('user_address') or '?芰'
    symbol = position.get('symbol') or position.get('coin') or position.get('asset') or '?芰'
    side = position.get('side') or position.get('direction') or position.get('position_side') or '?芰'
    
    # ?岫憭車?孵??脣????
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
    
    # 憒??湔?潔?摮嚗?閰衣 size * price 閮?
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
    
    # 蝪∪??啣?憿舐內嚗憿舐內敺?4 雿?
    address_short = address[-4:] if len(address) > 4 else address
    
    # ?斗憭征?孵?嚗閰望?嚗?憭??撞嚗?蝛???嚗?
    side_lower = str(side).lower()
    side_text = "??嚗?瞍莎?" if side_lower in ['long', 'buy', '憭?', 'l'] else "?征嚗?頝?"
    
    # ?澆???憿＊蝷?
    if size_float >= 1_000_000:
        size_display = f"${size_float/1_000_000:.2f}M"
    elif size_float >= 1_000:
        size_display = f"${size_float/1_000:.2f}K"
    else:
        size_display = f"${size_float:.2f}"
    
    return f"{index}. ?啣? `...{address_short}` | ??{size_display} [{symbol} {side_text}] | {leverage:.1f}x"


def build_hyperliquid_message() -> Optional[str]:
    """蝯? Hyperliquid ?唳??Ｙ?扯??荔?????Whale Alert ??哨?"""
    logger.info("??瑽遣 Hyperliquid ?唳??Ｙ?扯???..")
    
    # 1. ?脣? Whale Alert
    alerts = fetch_hyperliquid_whale_alert()
    logger.info(f"?脣???{len(alerts)} ??Whale Alert")
    
    # 瑼Ｘ?臬???Alert嚗??銴?哨?
    sent_alert_ids = load_json_file(HYPERLIQUID_SENT_ALERTS_FILE, [])
    new_alerts = []
    new_alert_ids = []
    
    for alert in alerts:
        # ???臭? ID嚗蝙?冽?? + symbol + value嚗?
        alert_id = f"{alert.get('time') or alert.get('timestamp')}_{alert.get('symbol')}_{alert.get('notional_value') or alert.get('notionalValue')}"
        if alert_id not in sent_alert_ids:
            new_alerts.append(alert)
            new_alert_ids.append(alert_id)
    
    # ?? ??嚗?冽??啁? Whale Alert ???冽嚗????
    if not new_alerts:
        logger.info("?祆活?????⊥?之憿漱????> $1M嚗?頝喲??冽")
        return None
    
    # 2. ?脣? PNL Distribution嚗?雿鋆?鞈?嚗?
    pnl_data = fetch_hyperliquid_pnl_distribution()
    smart_money_info = process_smart_money_pnl(pnl_data) if pnl_data else {}
    
    # 3. ?脣? Whale Position嚗?雿鋆?鞈?嚗?
    whale_positions = fetch_hyperliquid_whale_position()
    logger.info(f"?脣???{len(whale_positions)} ?祠擳???")
    
    # 瑽遣閮嚗??冽??啁? Alert ??瑽遣嚗?
    lines = []
    lines.append("? *??憛??寥 - Hyperliquid 攳券?餈質馱??")
    lines.append("????????????????????")
    lines.append("")
    
    # Whale Alert ?典?嚗蜓閬摰對?????????
    lines.append("? *撌券祠?單??郎 (Whale Alert)*嚗?")
    for alert in new_alerts[:5]:  # ?憭＊蝷?5 ??
        symbol = alert.get('symbol') or alert.get('coin') or '?芰'
        
        # ?脣?USD?孵潘??芸?雿輻 position_value_usd嚗?
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
        
        # ?脣?????create_time ?舀神蝘??嚗?
        alert_time = alert.get('create_time') or alert.get('time') or alert.get('timestamp') or alert.get('open_time')
        time_str = "???芰"
        if alert_time:
            try:
                if isinstance(alert_time, (int, float)):
                    # create_time ?舀神蝘??嚗?憒?1768536078000嚗?
                    if alert_time > 1e12:
                        dt = datetime.fromtimestamp(alert_time / 1000, tz=timezone.utc)
                    else:
                        dt = datetime.fromtimestamp(alert_time, tz=timezone.utc)
                    # 頧??箏?????
                    dt_taipei = get_taipei_time(dt)
                    time_str = dt_taipei.strftime("%Y-%m-%d %H:%M")
                else:
                    time_str = str(alert_time)
            except Exception as e:
                logger.debug(f"??閫??憭望?: {alert_time}, ?航炊: {str(e)}")
                time_str = "???芰"
        
        # ?斗?孵?嚗??position_size 甇????position_action嚗?
        position_size = alert.get('position_size') or alert.get('positionSize') or 0
        position_action = alert.get('position_action') or alert.get('positionAction')
        side = alert.get('side') or alert.get('direction') or alert.get('type')
        
        # ?斗?孵??摩嚗?
        # 1. 憒???side/direction/type 摮挾嚗?乩蝙??        # 2. 憒? position_size > 0嚗?賣??嚗? 0 ?航?臬?蝛?        # 3. position_action: 1=??, 2=?征, 3=撟喳?, 4=撟喟征
        if side:
            direction_text = "??嚗?瞍莎?" if str(side).lower() in ['long', 'buy', '憭?', 'l', '1'] else "?征嚗?頝?"
        elif position_action is not None:
            # position_action: 1=??, 2=?征
            if position_action == 1:
                direction_text = "??嚗?瞍莎?"
            elif position_action == 2:
                direction_text = "?征嚗?頝?"
            else:
                direction_text = "?芰"
        elif isinstance(position_size, (int, float)):
            # ?寞? position_size 甇???斗嚗迤?詨?賣??嚗??詨?賣?征嚗?
            direction_text = "??嚗?瞍莎?" if position_size > 0 else "?征嚗?頝?"
        else:
            direction_text = "?芰"
        
        direction_emoji = "?" if "??" in direction_text else "?"
        
        # ?澆???潮＊蝷?
        if value >= 1_000_000:
            value_display = f"${value/1_000_000:.2f}M"
        elif value >= 1_000:
            value_display = f"${value/1_000:.2f}K"
        else:
            value_display = f"${value:,.0f}"
        
        # ???脣?對??冽 VWAP ?瘥?嚗?
        entry_price = alert.get('entry_price') or alert.get('entryPrice') or alert.get('avg_price') or alert.get('avgPrice')
        mark_price = alert.get('mark_price') or alert.get('markPrice') or alert.get('price')
        liq_price = alert.get('liq_price') or alert.get('liquidationPrice') or alert.get('liquidation_price')
        leverage = alert.get('leverage') or alert.get('leverageRatio') or alert.get('leverage_ratio')

        # ?岫???曉嚗? Binance ?祇? API嚗?鞎鳴?
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

        # VWAP ? vs ?曉??
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
            if "??" in direction_text:
                if deviation_pct > 2.0:
                    cost_analysis = f"?? 餈賡?憸券嚗?孵歇瘥之?嗆??祇? `+{deviation_pct:.1f}%`嚗??桅?雓寞?"
                elif deviation_pct < -1.0:
                    cost_analysis = f"?儭?撘瑕??舀?雿??曉?葫?喳之?{_ref:.4f}`嚗?撌?`{deviation_pct:.1f}%`嚗??舀???"
                else:
                    cost_analysis = f"??鞎潸?憭扳? `{cost_ref:.4f}`嚗?{_pct:+.1f}%`嚗?頝憸券雿?"
            else:  # ?征
                if deviation_pct < -2.0:
                    cost_analysis = f"?? 餈賜征憸券嚗?孵歇瘥之?嗆??砌? `{deviation_pct:.1f}%`嚗??桅?雓寞?"
                elif deviation_pct > 1.0:
                    cost_analysis = f"?儭?憯??嚗?寥??澆之?嗥征?{_ref:.4f}`嚗?撌?`+{deviation_pct:.1f}%`嚗?憯??＊"
                else:
                    cost_analysis = f"???曉鞎潸?憭扳蝛箏?嚗?{_pct:+.1f}%`嚗?頝征憸券雿?"

        # 攳券?????
        lev_float = None
        if leverage:
            try:
                lev_float = float(leverage)
            except (TypeError, ValueError):
                pass
        if lev_float is not None:
            if lev_float >= 10:
                whale_intent = f"? *頞典撱箏?嚗?{_float:.0f}x 擃?獢選?撘瑟?扳瘜剁?"
            elif lev_float >= 3:
                whale_intent = f"?? *銝剜批遣??嚗?{_float:.0f}x嚗隅?Ｗ遣??瘜Ｘ挾雿?嚗?"
            else:
                whale_intent = f"?儭?*撠?/靽?*嚗?{_float:.0f}x 雿?獢選??撮撠?靽潘?"
        else:
            whale_intent = "?? ??敺?撖?瑽▼?芰嚗?"

        # ?? HL 撟?車??+ ?唳??Ｚ????啣?嚗????????????????????????
        hl_smart = {}
        try:
            hl_smart = fetch_hyperliquid_smart_money_score(str(symbol))
        except Exception:
            pass
        hl_pos_data = fetch_hyperliquid_coin_position(str(symbol))
        hl_ls_val = hl_pos_data.get("ls_ratio") if hl_pos_data else None

        lines.append(f"????{time_str}")
        lines.append(f"璅?嚗{symbol}`")
        lines.append(f"?{_emoji} {direction_text}")
        lines.append(f"閬{_display} USD")
        if cost_ref:
            lines.append(f"?{_ref:.4f}`" + (f" | ?曉嚗{current_market_price:.4f}`" if current_market_price else ""))
        if cost_analysis:
            lines.append(cost_analysis)
        if liq_price:
            try:
                lines.append(f"?嚗{float(liq_price):.4f}`")
            except (TypeError, ValueError):
                pass
        lines.append(f"?? 攳{_intent}")
        # HL ?湧????璈?蝝??
        if hl_ls_val is not None:
            _hl_emoji = "?" if hl_ls_val > 1.1 else ("?" if hl_ls_val < 0.9 else "?")
            lines.append(f"{_hl_emoji} HL?湧?憭征{_val:.2f}`嚗?1?? <1?征嚗?")
        if hl_smart.get("label"):
            lines.append(f"? {hl_smart['label']}")
        lines.append("")
    
    # ?湔撌脩??ID ?”
    sent_alert_ids.extend(new_alert_ids)
    # ?芯???餈?500 璇?
    if len(sent_alert_ids) > 500:
        sent_alert_ids = sent_alert_ids[-500:]
    save_json_file(HYPERLIQUID_SENT_ALERTS_FILE, sent_alert_ids)
    
    # ?唳???PNL ???典?嚗???閮?
    has_smart_money_data = (
        smart_money_info.get('money_printers') or 
        smart_money_info.get('smart_money') or 
        smart_money_info.get('top_symbols')
    )
    
    if has_smart_money_data:
        lines.append("? *?唳???PNL ??閫撖?嚗?")
        
        # 憿舐內撅斤?蝯梯?
        if smart_money_info.get('money_printers'):
            printer_count = sum(mp.get('address_count', 0) for mp in smart_money_info['money_printers'])
            if printer_count > 0:
                lines.append(f"Money Printer (> $1M ?{_count} ???")
        
        if smart_money_info.get('smart_money'):
            smart_count = sum(sm.get('address_count', 0) for sm in smart_money_info['smart_money'])
            if smart_count > 0:
                lines.append(f"Smart Money ($100k - $1M ?{_count} ???")
        
        # 憿舐內??銝剖漲
        top_symbols = smart_money_info.get('top_symbols', {})
        if top_symbols:
            symbol_list = []
            for symbol, info in list(top_symbols.items())[:3]:
                bias = info.get('bias', 0)
                symbol_list.append(f"`{symbol}`")
                if bias > 0:
                    lines.append(f"?嗡葉 {symbol} ??瞍脫?蝺?(Bias) ??{bias:.1f}%")
            
            if symbol_list:
                lines.append(f"?桀??脣 > $100k ???銝餉???{', '.join(symbol_list)}")
        
        lines.append("")
    
    # ?寥?內
    if new_alerts:
        top_symbol = new_alerts[0].get('symbol', '?孵?璅?')
        lines.append(f"? *?寥?內*嚗?甇??釣 {top_symbol}嚗?瘜冽?閰脣馳蝔桃?瘚??扯???")
        lines.append("")
    
    lines.append("????????????????????")
    lines.append(f"???湔??嚗format_datetime(get_taipei_time())")
    
    return "\n".join(lines)


def run_hyperliquid_monitor_once():
    """?瑁?銝甈?Hyperliquid ?唳??Ｙ?改??拙???閫貊嚗?"""
    logger.info("???瑁? Hyperliquid ?唳??Ｙ??..")
    
    message = build_hyperliquid_message()
    if not message:
        logger.info("?祆活 Hyperliquid ???⊥?????芰???")
        return
    
    thread_id = TG_THREAD_IDS.get("hyperliquid", 252)
    send_telegram_message(message, thread_id, parse_mode="Markdown")
    logger.info("Hyperliquid ?唳??Ｙ?扳?剖???")


def run_gold_signal():
    """暺? XAUUSD 憭征閮?嚗RB+MA嚗??冽?啣?銝??Telegram 璈鈭箝?摰?topic??"""
    import sys
    base = Path(__file__).resolve().parent
    cwd = Path.cwd()
    logger.info("[暺?閮?] ???瑁? | jackbot ??函??%s | ?嗅?撌乩??桅?=%s", base, cwd)
    # 靘??岫嚗?撅?gold_signal_bot嚗epo ?寧????????gold_signal_bot??極雿??
    candidates = [
        base / "gold_signal_bot",
        base / "暺?蝑" / "gold_signal_bot",
        cwd / "gold_signal_bot",
    ]
    gold_bot_dir = None
    for p in candidates:
        if p.is_dir():
            gold_bot_dir = p
            logger.info("[暺?閮?] 雿輻璅∠?頝臬?: %s", gold_bot_dir)
            break
        logger.info("[暺?閮?] 頝臬?銝??剁?頝喲?: %s", p)
    if gold_bot_dir is None:
        logger.error("[暺?閮?] ??頝臬???摮: %s", candidates)
        send_telegram_message(
            "?? 暺?閮?嚗銝 gold_signal_bot ?桅?嚗歇?岫 暺?蝑/gold_signal_bot ??gold_signal_bot嚗?隢Ⅱ隤?獢?瑽蒂?函蔡閰脰??冗??",
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
        logger.info("[暺?閮?] 璅∠? import ??")
    except ImportError as e:
        logger.exception("[暺?閮?] 璅∠? import 憭望?: %s", e)
        send_telegram_message(
            f"?? 暺?閮?嚗?鞈渡撩憭梧?隢Ⅱ隤歇摰? yfinance{str(e)}",
            TG_THREAD_IDS.get("gold_signal", 254),
        )
        return
    cfg = get_config()
    data_src = getattr(cfg, "DATA_SOURCE", "yfinance")
    symbol = getattr(cfg, "SYMBOL_GOLD", "GC=F")
    logger.info("[暺?閮?] ?豢?皞?%s 蝚西?=%s ???? 1h K 蝺?, data_src, symbol")
    df_1h = fetch_ohlc(cfg.SYMBOL_GOLD, interval="1h", period="5d", config=cfg)
    if df_1h is None or df_1h.empty:
        logger.warning("[暺?閮?] 暺? 1h ?豢??箇征 (df is None=%s, empty=%s)嚗頛芯??冽",
                      df_1h is None, df_1h.empty if df_1h is not None else "N/A")
        return
    n_rows = len(df_1h)
    if n_rows < 24:
        logger.warning("[暺?閮?] 暺? 1h ?豢?銝雲 24 ??(?桀? %s ??嚗頛芯??冽", n_rows)
        return
    logger.info("[暺?閮?] 暺? 1h ?豢? OK嚗 %s ??| ??蝭?: %s ~ %s",
                n_rows, df_1h.index.min() if hasattr(df_1h.index, 'min') and len(df_1h) else "N/A", df_1h.index.max() if hasattr(df_1h.index, 'max') and len(df_1h) else "N/A")

    # ???頝臬?嚗? gold_signal_bot ?惜嚗靘?repo ?扳 gold_signal_bot/gold_signal_state/state.json
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
            logger.warning("[暺?閮?] 霈????憭望?: %s", e)
        return {}

    def _save_gold_state(s):
        try:
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(s, f, ensure_ascii=False, indent=0)
        except Exception as e:
            logger.warning("[暺?閮?] 撖怠???憭望?: %s", e)

    state = _load_gold_state()
    now_utc = datetime.now(timezone.utc)
    # 隞鈭斗??交??隞?SESSION_START_HOUR_UTC=1 ?箏皞?
    orb_hour = getattr(cfg, "SESSION_START_HOUR_UTC", 1)
    if now_utc.hour < orb_hour:
        today_trade_date = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        today_trade_date = now_utc.strftime("%Y-%m-%d")

    # 頝冽?芸?皜嚗RB ?箸?抒??伐???鈭斗??交閫詨? TP/SL ????銝?撠??唬?憭拙皜?
    _active_trade_date = state.get("trade_date")
    if state.get("last_direction") and _active_trade_date and _active_trade_date != today_trade_date:
        logger.info(
            "[暺?閮?] ??鈭斗??伐?%s嚗?s ??TP/SL ?芾孛??頝冽?芸?皜?????隞嚗?s嚗??啣皜?",
            _active_trade_date, state.get("last_direction"), today_trade_date,
        )
        state = {}
        _save_gold_state({})

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
            logger.info("[暺?閮?] 撌脫??%s 閫詨?嚗頛芰???隞??????啣?", "甇Ｙ?" if hit == "tp" else "甇Ｘ?")
            # 閮????亙歇蝯?????脫迫??鈭斗??仿?銴????
            _save_gold_state({
                "closed_direction": last_dir,
                "closed_trade_date": today_trade_date,
            })
            return  # ?祈憚?湔蝯?嚗???啣

    df_dxy = None
    if cfg.USE_DXY_FILTER:
        df_dxy = fetch_ohlc(cfg.SYMBOL_DXY, interval="1h", period="5d", config=None)
        logger.info("[暺?閮?] DXY 瞈曄雯?冽?? %s ??", len(df_dxy) if df_dxy is not None and not df_dxy.empty else 0)
    signal = compute_signal(df_1h, cfg)
    if signal is None:
        logger.info("[暺?閮?] ?祈憚?∠泵??隞嗥? ORB+MA 閮?嚗歲???")
        return
    logger.info("[暺?閮?] ??閮?: ?孵?=%s ?脣=%s", signal.direction, signal.entry)
    # ?豢???嚗?憒望隡?嚗?銝?哨??踹???憭拇???餅?啗???
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
        logger.info("[暺?閮?] ?豢???嚗?敺?K 蝺歇??24h嚗?賭?撣?嚗歲???")
        return
    # ???葉嚗?????
    if state.get("last_direction") == signal.direction:
        logger.info("[暺?閮?] ??閮???嚗????%s ??嚗歲???, signal.direction")
        return
    # ???葉嚗??銝蝑??芰?獢???嚗???????踹?閮??銝???TP/SL 餈質馱嚗?
    active_direction = state.get("last_direction")
    opposite = {"long": "short", "short": "long"}.get(active_direction)
    if active_direction and signal.direction == opposite:
        logger.info(
            "[暺?閮?] ?桀?隞? %s ???芰?獢?TP/SL ?芾孛??嚗蕭?亙???%s 閮?嚗???蕭頩斤???",
            active_direction,
            signal.direction,
        )
        return
    # ???歇閫詨? TP/SL嚗鈭斗??乩???????
    if (
        state.get("closed_direction") == signal.direction
        and state.get("closed_trade_date") == today_trade_date
    ):
        logger.info("[暺?閮?] 隞 %s ?孵?撌脰孛??TP/SL嚗?鈭斗??乩???????, signal.direction")
        return
    ok, reason = apply_filters(
        signal.direction, cfg, df_1h, df_dxy=df_dxy, now=now_utc
    )
    if not ok:
        logger.info("[暺?閮?] 閮?鋡急蕪蝬脫?蝯? %s", reason)
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
    logger.info("[暺?閮?] ?冽摰? | thread_id=%s ?潮???%s", thread_id, sent)


# ??????????????????????????????????????????????????????????????????????????????
# API ?亙熒瑼Ｘ嚗????芸?撽????閬垢暺??豢??舐?改?
# ??????????????????????????????????????????????????????????????????????????????

def run_api_health_check(symbol: str = "BTC") -> None:
    """????皜祈岫???閬?API 蝡舫?嚗OG 皜?璅? ??????    ?澆?孵?嚗ython jackbot.py api_check
    銋 position_change ????銵?甈∴??憛???    """
    base = symbol.upper().replace("USDT", "")

    # 瘥葫閰阡??殷?(憿舐內?? ep_key, 皜祈岫 params, 敹???
    # 敹??改??=?詨?  ?=??  ?=??
    checks = [
        # ?? OI ??
        ("???蝺?",          "oi_agg_history",       {"symbol": base, "interval": "15m", "limit": 2}, "?"),
        ("蝛拙?撟??霅?OI",       "oi_agg_stable",        {"symbol": base, "interval": "15m", "limit": 2}, "?"),
        ("撟?雿I",             "oi_agg_coin",          {"symbol": base, "interval": "15m", "limit": 2}, "?"),
        ("????銵?",         "oi_exchange_list",     {"symbol": base}, "?"),
        ("???風??",         "oi_exchange_history",  {"symbol": base + "USDT", "exchange": "Binance", "interval": "15m", "limit": 2}, "?"),
        # ?? 鞈?鞎餌? ??
        ("鞎餌??”(??)",       "fr_exchange_list",     {}, "?"),
        ("OI??鞎餌?K蝺?",        "fr_oi_weight",         {"symbol": base, "interval": "8h", "limit": 2, "exchange": "Binance"}, "?"),
        ("蝝舐?鞎餌?",             "fr_accum_exchange",    {"symbol": base}, "?"),
        ("鞎餌?憟璈?",         "fr_arbitrage",         {}, "?"),
        # ?? 憭征瘥???
        ("?函雯撣單憭征瘥?",       "ls_global_history",    {"symbol": base, "exchange": "Binance", "interval": "1h", "limit": 2}, "?"),
        ("憭扳撣單憭征瘥?",       "ls_top_account",       {"symbol": base, "exchange": "Binance", "interval": "1h", "limit": 2}, "?"),
        ("憭扳??蝛箸?",       "ls_top_position",      {"symbol": base, "exchange": "Binance", "interval": "1h", "limit": 2}, "?"),
        # ?? 銝餃?鞎瑁都 ??
        ("撟?車??銝餃?鞎瑁都",     "taker_agg_history",    {"symbol": base, "interval": "15m", "limit": 3}, "?"),
        ("鈭斗?撠蜓?眺鞈?",       "taker_pair_history",   {"symbol": base + "USDT", "exchange": "Binance", "interval": "15m", "limit": 3}, "?"),
        ("??銝餃?鞎瑁都瘥?",       "taker_exchange_list",  {"symbol": base}, "?"),
        # ?? ????
        ("撟?車???風??",     "liq_agg_history",      {"symbol": base, "interval": "15m", "limit": 4}, "?"),
        ("?單?????",         "liq_order",            {"symbol": base, "limit": 5}, "?"),
        ("撟?車??銵?",         "liq_coin_list",        {"timeType": "0"}, "?"),
        ("?????M2",     "liq_agg_heatmap_m2",   {"symbol": base, "exchange": "Binance"}, "?"),
        ("?????M1",     "liq_agg_heatmap_m1",   {"symbol": base, "exchange": "Binance"}, "?"),
        ("?????)",       "liq_agg_map",          {"symbol": base}, "?"),
        # ?? 閮蝪???
        ("??閮蝪踵楛摨行風??",   "ob_agg_ask_bids",      {"symbol": base, "interval": "15m", "limit": 3, "range": "5"}, "?"),
        ("憭折??",             "ob_large_order",       {"symbol": base, "side": "asks"}, "?"),
        ("憭折??甇瑕",         "ob_large_order_hist",  {"symbol": base, "interval": "15m", "limit": 2}, "?"),
        # ?? ??撣 ??
        ("??撟?車撣銵?",     "coins_markets",        {"page": "1", "size": "10"}, "?"),
        # ?? ?曇疏銝餃?鞎瑁都 ??
        ("?曇疏撟?車??銝餃?鞎瑁都", "spot_taker_agg",       {"symbol": base, "interval": "15m", "limit": 2}, "?"),
        # ?? ?? ??
        ("???憭抒?暺?",         "opt_max_pain",         {"symbol": base}, "?"),
        ("???風??",         "opt_exchange_oi",      {"symbol": base, "limit": 2}, "?"),
        # ?? ?? ??
        ("?鞎芸帚?",         "fear_greed",           {"limit": 1}, "?"),
        ("Coinbase皞Ｗ?",     "coinbase_premium",     {"limit": 1}, "?"),
        ("???箏榆甇瑕",         "contract_basis",       {"symbol": base, "exchange": "Binance", "interval": "1h", "limit": 2}, "?"),
        ("BTC ETF鞈?瘚?",        "btc_etf_flow",         {"limit": 1}, "?"),
        ("BTC ETF瘛刻???",        "btc_etf_net_assets",   {"limit": 1}, "?"),
        # ?? ETF & ?? ??
        ("鈭斗??擗??”",       "exchange_balance_list",{}, "?"),
        ("?啣漲??",             "grayscale_holdings",   {}, "?"),
        # ?? Hyperliquid ??
        ("HL攳券??郎",           "hl_whale_alert",       {}, "?"),
        ("HL撟?車??",           "hl_position",          {"symbol": base}, "?"),
        ("HL?Ｗ????",       "hl_wallet_pnl_dist",   {}, "?"),
    ]

    logger.info("=" * 70)
    logger.info("?? [API?亙熒瑼Ｘ] ????撽????閬垢暺?..")
    logger.info(f"   皜祈岫撟?車{base}USDT  |  CG_API_KEY: {'撌脰身摰?' if CG_API_KEY else '?閮剖?'}")
    logger.info("=" * 70)

    results = {"??": 0, "??": 0, "??": 0}

    for name, ep_key, params, priority in checks:
        ep = CG_EP.get(ep_key, "")
        if not ep:
            logger.warning(f"  [{priority}] {name:30s} ??  CG_EP 銝剜銝 key={ep_key}")
            results["??"] += 1
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
                    logger.info(f"  [{priority}] {name:30s} ?? HTTP200 ?豢?蝑={len(data) if isinstance(data, list) else '??'}")
                    results["??"] += 1
                elif code in (0, "0", 200, "200", None):
                    logger.warning(f"  [{priority}] {name:30s} ??  HTTP200 雿ata?箇征 code={code}")
                    results["??"] += 1
                else:
                    msg = j.get("msg") or j.get("message") or ""
                    logger.warning(f"  [{priority}] {name:30s} ?? HTTP200 雿PI?航炊 code={code} msg={msg[:60]}")
                    results["??"] += 1
            elif status == 401:
                logger.error(f"  [{priority}] {name:30s} ?? HTTP401 API Key ?⊥??閮剖?")
                results["??"] += 1
            elif status == 403:
                logger.warning(f"  [{priority}] {name:30s} ?? HTTP403 甈?銝雲嚗???撣唾?嚗?")
                results["??"] += 1
            elif status == 404:
                logger.warning(f"  [{priority}] {name:30s} ??  HTTP404 蝡舫?銝??剁?頝臬??航?炊嚗?")
                results["??"] += 1
            elif status == 429:
                logger.warning(f"  [{priority}] {name:30s} ??  HTTP429 ???嚗PI Key甇?Ⅱ雿??擃?")
                results["??"] += 1
            else:
                logger.warning(f"  [{priority}] {name:30s} ?? HTTP{status}")
                results["??"] += 1
        except Exception as e_hc:
            logger.warning(f"  [{priority}] {name:30s} ??  隢??啣虜: {str(e_hc)[:50]}")
            results["??"] += 1
        time.sleep(0.3)  # ?踹??亙熒瑼Ｘ?祈澈閫貊429

    # 敶?勗?
    total = sum(results.values())
    logger.info("=" * 70)
    logger.info(f"?? [API?亙熒瑼Ｘ摰?] ?望葫閰?{total} ?垢暺?")
    logger.info(f"   ??甇?虜{results['??']}   ??憭望?/?⊥???{results['??']}   ?? 蝛箸???啣虜{results['??']}")
    if results["??"] > 0:
        logger.warning("   ?? ?????隢? CoinGlass 蝣箄?撣唾?甈?嚗?瑼Ｘ CG_API_KEY ?啣?霈")
    logger.info("=" * 70)


# ==================== 鞈??蔭撌亙 ====================

def run_reset_data() -> None:
    """
    皜???颯?剔??蜀???霈頂蝯勗?圈???    ?澆?孵?嚗ython jackbot.py reset_data

    皜蝭?嚗?      ??sniper_cooldown.json   - ?瑕甇瑕 + ?冽蝝???怠?餈質馱嚗?      ??performance_history.json - 瘥蝮暹?蝝舐?嚗???R ?潘?
      ??last_summary_date.json  - 瘥蝮暹?蝮賜??潮??
      ??backup_state.json       - ????隞?    """
    logger.info("=" * 60)
    logger.info("????蝵柴?憪??斗???餉?蝮暹?閮?...")

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
            logger.info(f"  ??撌脫??? {fname}")
            cleared.append(fname)
        except Exception as e:
            logger.warning(f"  ?? 皜憭望? {fname}: {e}")

    logger.info(f"????蝵柴????望???{len(cleared)} ??獢?{cleared}")
    logger.info("=" * 60)

    # ?潮?Telegram ?
    from datetime import datetime as _dt
    _now_str = _dt.now(TAIPEI_TZ).strftime("%m/%d %H:%M")
    _msg = (
        f"?? *?頂蝯梢?蝵柴??歇?券皜*\n"
        f"?? {_now_str} (?啁)\n"
        f"??????????????\n"
        f"???瑕甇瑕 & ?冽蝝??撌脫?蝛暝n"
        f"????餈質馱閮?嚗歇皜征\n"
        f"??蝮暹?甇瑕嚗???R ?潘?嚗歇皜征\n"
        f"??瘥蝮賜??潮?嚗歇皜征\n"
        f"??????????????\n"
        f"?? 銝?頛芣???敺??閮?嚗?瑕???"
    )
    try:
        _thread = TG_THREAD_IDS.get("sniper", 0) or int(CHAT_ID or 0)
        send_telegram_message(_msg, _thread, parse_mode="Markdown")
        logger.info("????蝵柴elegram ?撌脩??")
    except Exception as e:
        logger.warning(f"????蝵柴elegram ?憭望?嚗?敶梢?蔭蝯?嚗? {e}")


# ==================== 銝餌?摨?====================

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        function_name = sys.argv[1]
        
        if function_name == "sector_ranking":
            fetch_sector_ranking()
        elif function_name == "buying_power_monitor":
            buying_power_monitor()
        elif function_name == "whale_position":
            # ???澆捆嚗??迂隞雿輻
            logger.info("雿輻??詨?蝔?whale_position嚗遣霅唳??buying_power_monitor")
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
            print("?舐????")
            print("  sector_ranking   - 銝餅??踹???璁??")
            print("  buying_power_monitor - 鞈潸眺??改?蝛拙?撟????+ OI ??嚗?")
            print("  whale_position       - 撌脣誥璉?隢蝙??buying_power_monitor")
            print("  position_change  - ???祟??")
            print("  economic_data    - ??蝬??豢??冽")
            print("  news             - ?啗?敹怨??冽")
            print("  funding_rate     - 鞈?鞎餌???璁?")
            print("  long_term_index       - ?瑞???撠?嚗?4 撠?瘥?4 撠??湔嚗?")
            print("  long_term_index_once  - ?瑞???撠?嚗?瑁?銝甈∴??拙???嚗?")
            print("  liquidity_radar       - 瘚??抒???璆萇垢???湛?")
            print("  altseason_radar       - 撅勗祠??琿?嚗ltseason + RSI + Buy Ratio嚗?")
            print("  hyperliquid           - Hyperliquid ?唳??Ｙ??")
            print("  gold_signal           - 暺? XAUUSD 憭征閮?嚗RB+MA嚗?")
            print("  api_check             - API ?亙熒瑼Ｘ嚗?霅??垢暺?血?剁?")
            print("  reset_data            - 皜?????冽/蝮暹?閮?嚗?圈???")
    else:
        print("隢?摰??瑁????踝?靘?: python jackbot.py sector_ranking")

