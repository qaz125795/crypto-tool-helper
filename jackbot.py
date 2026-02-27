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
            'altseason_radar': 254,
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
        'altseason_radar': int(os.environ.get('TG_THREAD_ALTSEASON_RADAR', 254)),
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
_bingx_tech_fail_count: int = 0

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
    "tripped": False,
    "trip_time": 0.0,
    "trip_duration": 300.0,  # 5 分鐘保護期
}
_circuit_breaker_lock = threading.Lock()


def _cb_record_429() -> None:
    """記錄一次 429 錯誤；達到 5 次時自動啟動熔斷。"""
    with _circuit_breaker_lock:
        _circuit_breaker["consecutive_429"] += 1
        cnt = _circuit_breaker["consecutive_429"]
        if cnt >= 5 and not _circuit_breaker["tripped"]:
            _circuit_breaker["tripped"] = True
            _circuit_breaker["trip_time"] = time.time()
            logger.warning(
                f"[熔斷器啟動🚨] 連續 {cnt} 次 429，"
                f"自動降為單執行緒並加倍等待，持續 {_circuit_breaker['trip_duration']/60:.0f} 分鐘"
            )


def _cb_record_success() -> None:
    """記錄一次成功請求，重置連續 429 計數。"""
    with _circuit_breaker_lock:
        if _circuit_breaker["consecutive_429"] > 0:
            _circuit_breaker["consecutive_429"] = 0


def _cb_is_tripped() -> bool:
    """判斷熔斷器是否仍在保護期；到期自動恢復。"""
    with _circuit_breaker_lock:
        if not _circuit_breaker["tripped"]:
            return False
        elapsed = time.time() - _circuit_breaker["trip_time"]
        if elapsed >= _circuit_breaker["trip_duration"]:
            _circuit_breaker["tripped"] = False
            _circuit_breaker["consecutive_429"] = 0
            logger.info("[熔斷器恢復✅] 5 分鐘保護期結束，恢復正常並行數與等待時間")
            return False
        return True


def _cb_get_max_workers(default: int = 12) -> int:
    """根據熔斷器狀態返回建議最大執行緒數（標準版預設 12）。"""
    return 1 if _cb_is_tripped() else default


def _cb_get_wait_multiplier() -> float:
    """根據熔斷器狀態返回 wait_time 倍率（熔斷中 → 2×）。"""
    return 2.0 if _cb_is_tripped() else 1.0


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


def save_json_file(filepath: Path, data: Any) -> bool:
    """保存數據到 JSON 文件（帶 fsync）；若是 sniper_cooldown.json 同步寫入備份。"""
    try:
        tmp_path = filepath.with_suffix(filepath.suffix + ".tmp")
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path, filepath)

        # 備份寫入：將 sniper_cooldown.json 同步寫入 data/backup_state.json
        if filepath.name == "sniper_cooldown.json":
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
                backup_tmp = backup_path.with_suffix(".tmp")
                with open(backup_tmp, 'w', encoding='utf-8') as bf:
                    json.dump(backup_all, bf, ensure_ascii=False, indent=2)
                    bf.flush()
                    try:
                        os.fsync(bf.fileno())
                    except OSError:
                        pass
                os.replace(backup_tmp, backup_path)
            except Exception as be:
                logger.warning(f"[備份寫入] backup_state.json 寫入失敗（不影響主要功能）: {be}")

        return True
    except Exception as e:
        logger.error(f"保存文件失敗 {filepath}: {str(e)}")
        return False


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
            logger.error(f"全局帳戶比 API 請求失敗 - {symbol}: {response.status_code}")
            return None
        
        data = response.json()
        if data.get('code') not in ['0', 0, 200, '200']:
            logger.error(f"全局帳戶比 API 返回錯誤 - {symbol}: {data.get('code')}")
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


def buying_power_monitor():
    """【牛市燃料監控】資金進場=發車，判斷大盤動能"""
    logger.info("開始執行牛市燃料監控...")
    marketcap_data = fetch_stablecoin_marketcap_history()
    mcap_change = calculate_marketcap_change(marketcap_data) if marketcap_data else {}
    oi_data = fetch_aggregated_stablecoin_oi_history("BTC", "1h")
    oi_change = calculate_oi_change(oi_data) if oi_data else {}
    if not mcap_change or not oi_change:
        logger.warning("牛市燃料監控：無法取得市值或 OI 數據，跳過推播")
        return

    mcap_1h = mcap_change.get("change_1h") or 0
    oi_1h = oi_change.get("change_1h") or 0
    trend = "➡️ 震盪蓄力"
    advice = "多看少動，等待方向"
    color = "🟡"
    if mcap_1h > 0.05 and oi_1h > 1.0:
        trend, advice, color = "🚀 火力全開 (雙重利好)", "資金+槓桿雙噴，回調就是買點！", "🟢"
    elif mcap_1h > 0.05:
        trend, advice, color = "💰 資金進場 (現貨買盤)", "場外資金流入，底部墊高，偏多操作。", "🟢"
    elif oi_1h > 1.5:
        trend, advice, color = "⚠️ 槓桿過熱 (高波動預警)", "只有槓桿在堆，小心插針畫門。", "🔴"
    elif mcap_1h < -0.05:
        trend, advice, color = "📉 資金出逃 (獲利了結)", "資金正在撤退，反彈記得減倉。", "🔴"

    lines = []
    lines.append("⛽ *【牛市燃料監控】*")
    lines.append(f"🕐 {datetime.now(TAIPEI_TZ).strftime('%H:%M')} (台灣)")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(f"🌡️ *市場狀態：{color} {trend}*")
    lines.append("")
    mcap_val = (mcap_change.get("latest_mcap") or 0) / 1_000_000_000
    mcap_emoji = "📈" if mcap_1h > 0 else "📉"
    lines.append("💵 *穩定幣 (場外資金)*")
    lines.append(f"• 總量：${mcap_val:.2f}B")
    lines.append(f"• 變動：{mcap_emoji} {mcap_1h:+.3f}% (1H)")
    lines.append("")
    oi_val = (oi_change.get("latest_oi") or 0) / 1_000_000_000
    oi_emoji = "🔥" if oi_1h > 0 else "❄️"
    lines.append("🎰 *合約持倉 (場內槓桿)*")
    lines.append(f"• 總量：${oi_val:.2f}B")
    lines.append(f"• 變動：{oi_emoji} {oi_1h:+.2f}% (1H)")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(f"💡 *船長指令*：\n{advice}")

    msg = "\n".join(lines)
    keyboard = {"inline_keyboard": [[{"text": "💰 查看資金流向圖表", "url": "https://www.coinglass.com/zh-TW/pro/futures/OpenInterest"}]]}
    send_telegram_message(msg, TG_THREAD_IDS.get("buying_power_monitor", 246), parse_mode="Markdown", reply_markup=keyboard)
    logger.info("牛市燃料監控推播完成")


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

    url = f"{CG_API_BASE}/api/futures/open-interest/exchange-list"
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}
    params = {"symbol": base_symbol}

    try:
        _respect_coinglass_rate_limit()
        response = requests.get(url, params=params, headers=headers, timeout=8)
        if response.status_code != 200:
            _consensus_cache[cache_key] = (False, now)
            return False
        result = response.json()
        if result.get("code") not in ("0", 0, 200, "200"):
            _consensus_cache[cache_key] = (False, now)
            return False

        data_list = result.get("data", [])
        if not isinstance(data_list, list) or not data_list:
            _consensus_cache[cache_key] = (False, now)
            return False

        # 統計各大所的 OI 15m 變化方向
        positive_count = 0  # OI 增加
        negative_count = 0  # OI 減少
        found_exchanges = 0

        for entry in data_list:
            exch = (entry.get("exchange") or entry.get("exchangeName") or "").strip()
            if exch not in _CONSENSUS_MAJOR_EXCHANGES:
                continue
            found_exchanges += 1
            # 嘗試各種可能的 OI 變化欄位
            oi_chg = (
                entry.get("openInterestChangePercent15m")
                or entry.get("oiChange15m")
                or entry.get("openInterestChange")
                or entry.get("openInterestChangePercent")
                or entry.get("changePercent")
            )
            if oi_chg is None:
                # fallback: 計算 h 和 o 的差值方向
                oi_now = entry.get("openInterest") or entry.get("oi") or 0
                oi_prev = entry.get("openInterestPrev") or entry.get("oiPrev") or 0
                try:
                    oi_chg = float(oi_now) - float(oi_prev)
                except (TypeError, ValueError):
                    continue
            try:
                chg_val = float(oi_chg)
                if chg_val > 0:
                    positive_count += 1
                elif chg_val < 0:
                    negative_count += 1
            except (TypeError, ValueError):
                pass

        # 3 家以上同向 → 共識確立
        consensus = (positive_count >= 3) or (negative_count >= 3)
        logger.info(
            f"[多所共識] {base_symbol}: 查到 {found_exchanges} 家大所 | "
            f"OI增加 {positive_count} 家 | OI減少 {negative_count} 家 | "
            f"{'✅ 共識確立' if consensus else '❌ 無共識'}"
        )
        _consensus_cache[cache_key] = (consensus, now)
        return consensus

    except Exception as e:
        logger.debug(f"[多所共識] {base_symbol} 查詢異常: {e}")
        _consensus_cache[cache_key] = (False, now)
        return False


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
    一次取得 CoinGlass 全量資金費率（與排行榜同 API、同結構），回傳 symbol(base) -> 費率。
    優先 Binance，無則 BingX。與 fetch_funding_fortune_list 同 URL、同解析邏輯。
    """
    out: Dict[str, float] = {}
    url = f"{CG_API_BASE}/api/futures/funding-rate/exchange-list"
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}
    for attempt in range(2):
        try:
            r = requests.get(url, headers=headers, timeout=12)
            if r.status_code == 429:
                logger.warning("資金費率 API 429 Too Many Requests，2 秒後重試一次")
                time.sleep(2)
                continue
            if r.status_code != 200:
                if len(out) == 0:
                    logger.warning(f"資金費率 API status={r.status_code} body={r.text[:200]}")
                return out
            data = r.json()
            # 與排行榜一致：接受 code 為 0 或 '0'
            if data.get("code") not in (0, "0", 200, "200", None):
                if len(out) == 0:
                    logger.warning(f"資金費率 API code={data.get('code')} msg={data.get('msg')} body={r.text[:200]}")
                return out
            lst = data.get("data", [])
            if not isinstance(lst, list):
                if len(out) == 0:
                    logger.warning(f"資金費率 API data 非列表 type={type(lst)}")
                return out
            break
        except Exception as e:
            if attempt == 0:
                logger.warning(f"資金費率 API 請求異常: {e}，重試一次")
                time.sleep(1)
            else:
                return out
    else:
        return out
    try:
        for item in lst:
            if not isinstance(item, dict):
                continue
            base = item.get("symbol") or item.get("coin") or item.get("base")
            if not base:
                continue
            base = str(base).strip().upper()
            margin_list = item.get("stablecoin_margin_list") or item.get("margin_list") or []
            rate_binance = None
            rate_bingx = None
            for m in margin_list:
                if not isinstance(m, dict):
                    continue
                ex = m.get("exchange")
                if ex not in ("Binance", "BingX"):
                    continue
                try:
                    v = float(m.get("funding_rate"))
                except (TypeError, ValueError):
                    continue
                if ex == "Binance":
                    rate_binance = v
                else:
                    rate_bingx = v
            rate = rate_binance if rate_binance is not None else rate_bingx
            if rate is not None:
                out[base] = rate
        if len(out) == 0 and lst:
            logger.warning(f"資金費率解析後為 0 筆，請檢查 API 回傳格式。首筆 keys={list(lst[0].keys()) if lst and isinstance(lst[0], dict) else 'n/a'}")
        return out
    except Exception as e:
        logger.warning(f"資金費率解析異常: {e}")
        return out


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

    out: Dict[str, Any] = {
        "rsi": rsi_val,
        "touch_upper": touch_upper,
        "touch_lower": touch_lower,
        "current_price": current_price,
        "ub_value": ub_value,
        "lb_value": lb_value,
        "atr": atr_val,
        "source": "BingX",
        # plan_b_used 改為「是否使用 CoinGlass Fallback」，BingX 作為 Plan A 故此處固定 False
        "plan_b_used": False,
        "real_symbol": found_symbol,
        "energy_exhausted": energy_exhausted,
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


def calculate_technicals(symbol: str, bingx_symbol_override: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    技術指標：
    - Plan A = BingX 15m K 線本地計算（RSI/布林/EMA20/VWAP_2h/ATR/MACD/結構高低點）
    - Plan B = CoinGlass API（RSI/BOLL/ATR/EMA），僅在 BingX K 線不可用時作為後備。
    下游 SL/TP 與推播邏輯一律以 BingX 結構為主，確保與實際交易所一致。
    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()

    # Plan A：優先使用 BingX K 線本地計算（適用所有幣種，只要 BingX 有合約）
    logger.info(f"[技術指標] {base}: 優先使用 BingX 15m K 線本地計算技術指標與結構")
    tech = _fetch_bingx_klines_and_calc(symbol, preferred_symbol=bingx_symbol_override)
    if tech:
        # 明確標記為 BingX Plan A（plan_b_used=False 已在 _fetch_bingx_klines_and_calc 設定）
        tech["source"] = "BingX"
        logger.info(
            f"[技術指標] {base}: 使用 BingX 本地計算完成 "
            f"RSI={tech.get('rsi')} 布林上={tech.get('ub_value')} 布林下={tech.get('lb_value')} "
            f"ATR={tech.get('atr')} VWAP_2h={tech.get('vwap_2h')} EMA20={tech.get('ema20_close')}"
        )
        return tech

    # Plan B：僅當 BingX K 線連續失敗多次時，才退回 CoinGlass API
    logger.warning(f"[技術指標] {base}: BingX K 線本地計算失敗，記錄一次 BingX 失敗計數")
    global _bingx_tech_fail_count
    _bingx_tech_fail_count += 1
    if _bingx_tech_fail_count <= 3:  # 連續失敗前三次，寧可放棄該幣種也不直接用 CoinGlass
        logger.warning(f"[技術指標] {base}: BingX 失敗次數={_bingx_tech_fail_count} ≤ 3，本輪放棄技術指標以避免數據源偏差")
        return None

    logger.warning(f"[技術指標] {base}: BingX 連續失敗超過 3 次，啟用 CoinGlass 作為後備 Plan B（可能存在數據源偏差）")
    plan_b_used = True
    logger.info(f"[技術指標] {base}: 查詢 CoinGlass API RSI...")
    backoff = 2.0
    rsi_data = None
    for attempt in range(3):
        _respect_coinglass_rate_limit()
        rsi_data = _fetch_coinglass_rsi(symbol)
        if rsi_data is not None:
            break
        logger.warning(f"[GITHUB_IP_THROTTLED] CoinGlass RSI 取得失敗或限流 ({base})，第 {attempt+1} 次嘗試，休息 {backoff:.1f} 秒後重試")
        time.sleep(backoff + random.uniform(0, 1.0))
        backoff *= 2.0
    if rsi_data is None:
        logger.warning(f"[技術指標] {base}: CoinGlass RSI 亦無數據，技術指標取得失敗")
        return None

    _respect_coinglass_rate_limit()
    boll_data = _fetch_coinglass_boll(symbol)

    rsi_val = None
    data_rsi = rsi_data.get("data", rsi_data.get("list", []))
    if isinstance(data_rsi, list) and data_rsi:
        last = data_rsi[-1] if isinstance(data_rsi[-1], dict) else None
        if last is not None:
            for k in ("rsi", "value", "rsi_value"):
                if last.get(k) is not None:
                    try:
                        rsi_val = float(last[k])
                        break
                    except (TypeError, ValueError):
                        pass
    elif isinstance(data_rsi, dict):
        for k in ("rsi", "value", "rsi_value"):
            if data_rsi.get(k) is not None:
                try:
                    rsi_val = float(data_rsi[k])
                    break
                except (TypeError, ValueError):
                    pass
    if rsi_val is None:
        logger.warning(f"[技術指標] {base}: CoinGlass RSI 結構無有效數值，技術指標取得失敗")
        return None

    ub_value = None
    lb_value = None
    current_price = None
    data_boll = boll_data.get("data", boll_data.get("list", [])) if boll_data else []
    if isinstance(data_boll, list) and data_boll:
        last_b = data_boll[-1] if isinstance(data_boll[-1], dict) else None
        if last_b:
            ub_value = last_b.get("ub_value") or last_b.get("upper") or last_b.get("ub")
            lb_value = last_b.get("lb_value") or last_b.get("lower") or last_b.get("lb")
            current_price = last_b.get("price") or last_b.get("close") or last_b.get("c")
    elif isinstance(data_boll, dict):
        ub_value = data_boll.get("ub_value") or data_boll.get("upper")
        lb_value = data_boll.get("lb_value") or data_boll.get("lower")
        current_price = data_boll.get("price") or data_boll.get("close")
    try:
        ub_value = float(ub_value) if ub_value is not None else None
        lb_value = float(lb_value) if lb_value is not None else None
        current_price = float(current_price) if current_price is not None else None
    except (TypeError, ValueError):
        pass

    touch_upper = touch_lower = False
    if current_price is not None and ub_value is not None:
        touch_upper = current_price >= ub_value
    if current_price is not None and lb_value is not None:
        touch_lower = current_price <= lb_value
    if current_price is None and (ub_value is not None or lb_value is not None):
        current_price = ub_value or lb_value

    _respect_coinglass_rate_limit()
    atr_val = _fetch_coinglass_atr(symbol, "1d")
    vwap_2h = ema20_close = None
    # Plan B 加分項：EMA、MACD（API 失敗不影響 plan_b_used）
    _respect_coinglass_rate_limit()
    _respect_coinglass_rate_limit()
    ema20_api = _fetch_coinglass_ema(symbol, "15m")
    if ema20_api is not None:
        ema20_close = ema20_api
    _respect_coinglass_rate_limit()
    macd_data = _fetch_coinglass_macd(symbol, "15m")
    logger.info(
        f"[技術指標-15m] {base}: 使用 CoinGlass API 數據 RSI={rsi_val} BOLL上={ub_value} "
        f"BOLL下={lb_value} 現價={current_price} ATR={atr_val} EMA20={ema20_close} plan_b_used={plan_b_used}"
    )

    out = {
        "rsi": rsi_val,
        "touch_upper": touch_upper,
        "touch_lower": touch_lower,
        "current_price": current_price,
        "ub_value": ub_value,
        "lb_value": lb_value,
        "atr": atr_val,
        "source": "CoinGlass",
        "plan_b_used": plan_b_used,
        "data_source_warning": True,  # BingX 多次失敗後才啟用 CoinGlass，標記可能存在數據源偏差
    }
    if vwap_2h is not None:
        out["vwap_2h"] = vwap_2h
    if ema20_close is not None:
        out["ema20_close"] = ema20_close
    if macd_data is not None:
        out["macd"] = macd_data
    return out


# 四區塊 + 五星制：zone 為推播區塊名，stars 1=最差 5=最佳
ZONE_DIP = "抄底區"
ZONE_TOP = "摸頭區"
ZONE_BREAKOUT_LONG = "突破追漲區"
ZONE_BREAKOUT_SHORT = "跌破追跌區"

# 資金費率門檻（持倉異常＋費率為主，少依賴 K 線）
FUNDING_POSITIVE = 0.0005   # 0.05%，高於此視為多頭擁擠
FUNDING_NEGATIVE = -0.0005  # -0.05%，低於此視為空頭擁擠（易嘎空）
FUNDING_EXTREME = 0.0003    # v3.0 極端費率 0.03%，用於嘎空/殺多加權標註

# 【持倉異常 = 99% 山寨幣】15m 高頻模式，門檻已針對 15m 週期重新校準
MAIN_COINS = {"BTC", "ETH"}   # 主流幣
OI_MAIN_COIN_MIN = 3.0       # 主流幣須 |OI 15m| >= 3.0% 才進榜（15m 版本降低門檻）
OI_ALTCOIN_MIN = 1.0         # 山寨幣初選門檻（15m 高頻版，後續再用 OI_FOR_4_STAR 篩）

# 星等門檻（基礎值；實際 4/5 星門檻會在 runtime 依 OI 分佈動態調整，以適應不同波動環境）
OI_FOR_5_STAR = 2.2    # 5星：15m 高頻版門檻（基礎值，動態下限）
OI_FOR_4_STAR = 2.0    # 4星（15m 高頻版基礎值，動態下限）
OI_FOR_ELITE = 2.2     # 鑽石 💎：與 5 星對齊（15m 版）

# 狙擊鏡止盈風報門檻：止盈若低於此 R 不推播（避免賠率差、維持勝率品質）
MIN_TP1_R_FOR_PUSH = 0.65

# 抄底/摸頭 15m 門檻（山寨）：略放寬仍算低位/高位，減少誤殺
PRICE_DIP_MAX = 3.0    # 抄底：15m 漲幅 ≤ 3% 才算低位，超過改標追漲
PRICE_TOP_MIN = -3.0   # 摸頭：15m 跌幅 ≥ -3% 才算高位，跌破改標追跌

# 24H 趨勢門檻（保守山寨）：12% 以上才當假抄底/假摸頭，適應日波動
TREND_24H_THRESHOLD = 12.0

# 持倉異常策略（略放寬 5星/鑽石門檻以增加 S/頭等艙訊號，勝率邏輯不變）
# ┌────────┬────────────────────────────┬─────────────┬────────────────────────────────────────┐
# │ 訊號   │ 門檻                        │ 倉位比例    │ 說明                                   │
# ├────────┼────────────────────────────┼─────────────┼────────────────────────────────────────┤
# │ 💎鑽石 │ 摸頭/抄底+5星+OI≥3.5%+量≥5M  │ 滿倉 7%   │ 量≥5M + 鯨魚有數據 + 摸頭RSI≥60/抄底≤40  │
# │ ⭐5星  │ OI≥3.5%+CVD同向              │ 標準倉 5% │ 穩健列車                                 │
# │ ⭐4星  │ OI≥3.3%+方向                │ 減半倉 2.5%│ 賭鬼樂透                                 │
# └────────┴────────────────────────────┴─────────────┴────────────────────────────────────────┘
# 價格位階：抄底 15m≤3%；摸頭 15m≥-3%。24h 假訊號門檻 12%。主流幣 OI≥3.0% 才進榜。

# 鑽石 RSI 輔助：摸頭≥60 / 抄底≤40，無 RSI 不擋
RSI_FILTER_TOP_MIN = 60
RSI_FILTER_DIP_MAX = 40
RSI_FILTER_BREAKOUT_LONG_MIN = 45   # v3.0 追漲時 RSI 不低於 45
RSI_FILTER_BREAKOUT_SHORT_MAX = 55  # v3.0 追跌時 RSI 不高於 55
# 鑽石級 CVD 驗證：CVD 變化量需至少佔 OI 變化的一定比例，避免主力對敲假量
CVD_ELITE_MIN_RATIO = 0.3
# 鑽石級 24h 成交量門檻（略放寬：5M 即給頭等艙）
VOLUME_ELITE_MIN_USD = 5_000_000
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

    # 輔助函數：v3.0 散戶濾網 & 極端費率
    def _apply_retail_funding(tup: Tuple[str, str, int, str, str]) -> Tuple[str, str, int, str, str]:
        label, zone, stars, rsi_desc, reason = tup
        # 做多訊號：突破追漲(long_open) 或 多軍斷頭抄底(long_close)
        is_long = category in ("long_open", "long_close")
        # 做空訊號：跌破追跌(short_open) 或 空軍被軋摸頭(short_close)
        is_short = category in ("short_open", "short_close")

        # 散戶過熱降級
        if retail_ratio is not None and retail_ratio > 1.45:
            if is_long:
                stars = max(4, stars - 1)  # 最低降到 4，因為 3 星已刪除
                reason = reason + " ⚠️ 散戶過熱"
            elif is_short:
                reason = reason + " ✅ 散戶接盤"

        # 極端費率標註
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
    ):
        """
        【OI 起漲點防守法】BingX 15m 結構為主，不再使用「純 ATR 停損」：
        - 做多 SL：recent_low_2h 或「當前 15m K 線 low」往下 0.5% buffer。
        - 做空 SL：recent_high_2h 或「當前 15m K 線 high」往上 0.5% buffer。
        - 列車(S/S+)：SL 與現價距離 >8% 則強制壓縮至 8%。
        - 賭鬼(A)：SL 與現價距離 >6% 則強制壓縮至 6%。
        - 列車 TP：優先主力成本對稱 (VWAP_2h)，無則 1.5R。
        - 賭鬼 TP：TP1 = 1.0R，TP2 = 2.5R~4.0R（此處預設 3R，僅計算不顯示）。
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
        # 容錯機制：列車 8%，賭鬼 6%；高波動標的(vol_pct>3%) 動態放寬至 12% / 10%
        max_pct = 0.08 if is_train else 0.06
        if atr_val is not None and price > 0:
            vol_pct = (atr_val / price) * 100.0
            if vol_pct > 3.0:
                max_pct = 0.12 if is_train else 0.10

        if is_train:
            # 列車：結構 + ATR 雙保底
            basis_price = basis_low if is_long else basis_high
            struct_dist = None
            if basis_price is not None:
                # 結構位做 0.5% buffer 微調後，再算距離
                if is_long:
                    basis_price_adj = basis_price * (1.0 - buffer_pct)
                else:
                    basis_price_adj = basis_price * (1.0 + buffer_pct)
                struct_dist = abs(price - basis_price_adj)
            # ATR 保底距離：1.2×ATR，無 ATR 時用 2% 價格距離
            if atr_val is not None:
                atr_floor = 1.2 * atr_val
            else:
                atr_floor = price * 0.02
            if struct_dist is not None:
                sl_dist = max(struct_dist, atr_floor)
            else:
                sl_dist = atr_floor
            sl_price = price - sl_dist if is_long else price + sl_dist
            dist_pct = abs(price - sl_price) / price if price > 0 else 0
            if dist_pct > max_pct:
                sl_capped = True
                if is_long:
                    sl_price = price * (1.0 - max_pct)
                else:
                    sl_price = price * (1.0 + max_pct)
        else:
            # 賭鬼：保留原本「結構優先，否則 ATR / 百分比」方案
            if is_long and basis_low is not None:
                sl_price = basis_low * (1.0 - buffer_pct)
            elif (not is_long) and basis_high is not None:
                sl_price = basis_high * (1.0 + buffer_pct)

            # 若因資料缺失無法取得結構 SL，最後才用 ATR / 百分比作安全退場（極少數情況）
            if sl_price is None:
                if atr_val is not None:
                    # 結構缺失時，仍盡量用較保守的 ATR 停損
                    fallback_mult = 1.2 if is_gambler else 1.0
                    sl_dist = fallback_mult * atr_val
                else:
                    sl_dist = price * (0.03 if is_gambler else 0.02)
                sl_price = price - sl_dist if is_long else price + sl_dist

            dist_pct = abs(price - sl_price) / price if price > 0 else 0
            if dist_pct > max_pct:
                sl_capped = True
                if is_long:
                    sl_price = price * (1.0 - max_pct)
                else:
                    sl_price = price * (1.0 + max_pct)

        # 風險距離（R 的母數）
        risk_dist = (price - sl_price) if is_long else (sl_price - price)
        if not risk_dist or risk_dist <= 0:
            return _ret(sl_price, None, None, None, None, None, None, sl_capped, bool(cvd_divergence))
        # 避免太小距離導致 R 異常飆高
        min_risk = price * 0.005
        if risk_dist < min_risk:
            risk_dist = min_risk

        # 列車 / 賭鬼 TP 設定
        tp1_price = tp2_price = None
        tp1_label = tp2_label = ""
        r_tp1 = r_tp2 = None

        # 列車 (S/S+)：主力成本對稱優先，其次 1.2R
        if is_train:
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
            r_tp1 = round(((tp1_price - price) / risk_dist) if is_long else ((price - tp1_price) / risk_dist), 1)

        # 賭鬼 (A)：SL 保留現有結構邏輯，TP1 固定為 1.0R，TP2 採用「風報比 2.5~4.0R」區間的理論目標
        else:
            # TP1：固定 1R
            tp1_price = price + 1.0 * risk_dist if is_long else price - 1.0 * risk_dist
            tp1_label = "1.0R"
            r_tp1 = 1.0
            # TP2：依波動度動態選擇 2.5R~4.0R 之間的目標（僅作為績效評估用理論 TP2）
            atr_val = float(atr) if atr is not None and isinstance(atr, (int, float)) and atr > 0 else None
            target_r2 = 3.0
            if atr_val is not None and price > 0:
                vol_pct = (atr_val / price) * 100.0
                if vol_pct >= 3.0:
                    target_r2 = 2.5
                elif vol_pct <= 1.5:
                    target_r2 = 3.5
                else:
                    # 線性內插 1.5%~3.0% → 3.5R~2.5R
                    ratio = (vol_pct - 1.5) / (3.0 - 1.5)
                    target_r2 = 3.5 - ratio * (3.5 - 2.5)
            # 保險起見 clamp 在 2.5~4.0R 之間
            target_r2 = max(2.5, min(4.0, target_r2))
            tp2_price = price + target_r2 * risk_dist if is_long else price - target_r2 * risk_dist
            tp2_label = f"{target_r2:.1f}R"
            r_tp2 = round(target_r2, 1)

        # energy_exhausted / cvd_divergence 保留為附註旗標
        energy_exhausted = bool(cvd_divergence)
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

    # 極品 💎 = 摸頭/抄底 + 5星 + |OI|>=OI_FOR_ELITE + 24h 成交量≥15M + RSI 輔助 + 鯨魚指數有數據（數據完整才給鑽石）
    def _is_elite(x: Dict) -> bool:
        if (x.get("stars") or 0) != 5:
            return False
        z = x.get("zone")
        if z not in (ZONE_TOP, ZONE_DIP):
            return False
        if abs(x.get("oiChange30m") or 0) < OI_FOR_ELITE:
            return False
        if (x.get("volume_usd") or 0) < VOLUME_ELITE_MIN_USD:
            return False  # 鑽石級須流動性足夠，否則只顯示 5 星
        if x.get("whale_index") is None:
            return False  # 鑽石級須鯨魚指數有數據，數據完整才觸發
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
        return (
            _is_elite(x)
            and x.get("has_5m_resonance") is True
            and x.get("is_global_consensus") is True
        )

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
    eligible_items = long_dip + long_break + short_top + short_break
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
    seen_syms = set()  # 同幣只顯示一次，避免 1000PEPE 等重複出現
    for section_title, subs in blocks:
        section_printed = False
        for sub_label, items in subs:
            if not items:
                continue
            if not section_printed:
                lines.append("")
                lines.append(section_title)
                section_printed = True
            items_sorted = sorted(items, key=lambda x: (-(x.get("stars") or 0), -(abs(x.get("oiChange30m") or 0))))
            lines.append(sub_label)
            for x in items_sorted:
                sym = x.get("symbol", "")
                if sym and sym in seen_syms:
                    continue
                if sym:
                    seen_syms.add(sym)
                zone = x.get("zone")
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

                if is_diamond_sig:
                    tier_emoji = "💎"
                    star_display = f"💎【鑽石共振】三重確認‼️ ⭐⭐⭐⭐⭐{resonance_tag}{consensus_tag}"
                    x["tier"] = "diamond"
                elif is_elite_sig:
                    tier_emoji = "✈️"
                    star_display = f"✈️【頭等機艙】(高信心) ⭐⭐⭐⭐⭐{resonance_tag}{consensus_tag}"
                    x["tier"] = "elite"
                elif stars >= 5:
                    tier_emoji = "🚅"
                    star_display = f"🚅【穩健列車】(標準倉) ⭐⭐⭐{resonance_tag}{consensus_tag}"
                    x["tier"] = "train"
                else:
                    tier_emoji = "👻"
                    star_display = f"👻【賭鬼樂透】(高風險)💣{resonance_tag}{consensus_tag}"
                    x["tier"] = "gambler"

                # 策略與風控建議（策略前加分級 emoji）
                atr_val, current_price = x.get("atr"), x.get("current_price")
                is_high_vol = False
                vol_desc = ""
                if atr_val and current_price and (atr_val / current_price) * 100 > 2.0:
                    is_high_vol = True
                    vol_desc = " (波動大⚠️)"

                if is_diamond_sig:  # 💎 三重確認最高等
                    if is_high_vol:
                        strength, pos_rec = "鑽石共振但波動大", "標準倉 5% (動態縮倉)"
                    else:
                        strength, pos_rec = "💎 鑽石共振 S++", "重倉 10% (三重確認)"
                elif is_elite_sig:  # S+
                    if is_high_vol:
                        strength, pos_rec = "頭等艙但波動大", "標準倉 5% (已風控)"
                    else:
                        strength, pos_rec = "頭等機艙 S+", "重倉 7% (信心足)"
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
                )
                # 將 SL/TP 價位與標籤存回，供 24h 出場追蹤（SL 與 TP 同源：主力成本 或 ATR）
                x["sl_price_str"] = sl_val
                x["tp1_price_str"] = tp1_val
                x["tp1_label"] = tp1_label
                x["tp1_real_str"] = tp1_real_str
                x["tp1_real_note"] = tp1_real_note
                x["r_tp1"] = r_tp1
                # 賭鬼虛擬 TP2 目標：僅用於績效評估與 TP2 命中統計，不改變實際出場邏輯
                x["tp2_price_str"] = tp2_val
                x["r_tp2"] = r_tp2
                # 風報比過低不推播：止盈 < 門檻 R 代表賠率差，寧可少出手保勝率
                if r_tp1 is not None and r_tp1 < MIN_TP1_R_FOR_PUSH:
                    logger.info(f"狙擊鏡跳過 {sym}: 止盈 風報比 {r_tp1}R < {MIN_TP1_R_FOR_PUSH}R，不推播")
                    continue

                has_any = True
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
                lines.append(f"{dir_emoji} `{sym_base}`{flip_tag}{vol_desc}")
                # 2. 策略（白話，含分級 emoji）
                lines.append(f"🎲 策略：{strength}｜{pos_rec}")
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
                # 4. 邏輯（持倉變化白話）+ 訂單簿（白話）
                reason = x.get("reason", "籌碼異動")
                if flip:
                    reason = (reason or "") + " 趨勢已改變，舊單失效。"
                # 籌碼背離預警：OI 漲但價格跌幅收斂 → 底部吸籌；OI 跌但價格漲幅收斂 → 頂部出貨
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
                if p30_val is not None and oi30_val is not None:
                    if oi30_val > 0 and p30_val < 0 and abs(p30_val) < abs(oi30_val):
                        reason = (reason or "") + " ⚠️ 底部吸籌跡象"
                    elif oi30_val < 0 and p30_val > 0 and abs(p30_val) < abs(oi30_val):
                        reason = (reason or "") + " ⚠️ 頂部出貨跡象"
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
                lines.append(f"📍 現價：`{price_str}`{p24_str}")
                # 主力均價（成本參考）：優先 VWAP_2h，無則用 EMA20
                vwap_ref = x.get("vwap_2h")
                ema_ref = x.get("ema20_close")
                if vwap_ref is not None and isinstance(vwap_ref, (int, float)):
                    lines.append(f"📍 主力均價(參考)：`{vwap_ref:,.4f}`")
                elif ema_ref is not None and isinstance(ema_ref, (int, float)):
                    lines.append(f"📍 均線參考(EMA20)：`{ema_ref:,.4f}`")
                # 止損與止盈顯示
                t1_note = x.get("tp1_real_note") or x.get("tp1_label") or "ATR"
                if stars >= 5:
                    # 列車：若 SL 距現價 >5%，標注「深空防護」提示跟單者不要輕易移動止損
                    _sl_deep_note = ""
                    try:
                        _sl_price_f = float(sl_val.replace(",", "")) if sl_val and sl_val != "-" else None
                        _cur_p = x.get("current_price")
                        if _sl_price_f and _cur_p and float(_cur_p) > 0:
                            _sl_dist_pct = abs(float(_cur_p) - _sl_price_f) / float(_cur_p) * 100
                            if _sl_dist_pct > 5.0:
                                _sl_deep_note = " 🔐(深空防護)"
                    except (TypeError, ValueError):
                        pass
                    lines.append(f"🛑 止損：`{sl_val}` (標準){_sl_deep_note} {cap_note}")
                    r1 = f" ({r_tp1}R)" if r_tp1 is not None else ""
                    lines.append(f"✅ 止盈：`{tp1_val}` ({t1_note}){r1}")
                else:
                    # 賭鬼：1.0R TP1 + 放飛剩餘 40% + 顯示 TP2 理論目標（供評估與管理）
                    lines.append(f"🛑 止損：`{sl_val}` (防洗盤標準) {cap_note}")
                    lines.append(f"✅ TP1(落袋60%)：`{tp1_val}` (1.0R 保底)")
                    # 若有計算出 TP2，顯示理論目標價與對應 R
                    tp2_str = x.get("tp2_price_str") or "-"
                    r2 = x.get("r_tp2")
                    if tp2_str != "-" and r2 is not None:
                        try:
                            r2_val = float(r2)
                        except (TypeError, ValueError):
                            r2_val = None
                        if r2_val is not None:
                            lines.append(f"🎯 TP2 理論目標：`{tp2_str}` (~{r2_val:.1f}R)")
                    lines.append("🚀 剩餘 40%：不設止盈！推保本後沿著均線移動止損，讓利潤奔跑！")
                # 6. Warnings
                if x.get("low_liquidity_warning"):
                    lines.append("⚠️ 成交量極低 小心滑價")
                lines.append("")

    return "\n".join(lines), has_any


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

    # ── 嘗試 coins-markets（標準版完整端點）────────────────────────────────
    def _try_coins_markets() -> List[Dict]:
        try:
            r = requests.get(
                f"{CG_API_BASE}/api/futures/coins-markets",
                headers=headers, timeout=15
            )
            if r.status_code != 200:
                logger.info(f"coins-markets HTTP {r.status_code}，嘗試備援端點")
                return []
            j = r.json()
            if j.get("code") not in (0, "0", 200, "200", None):
                logger.info(f"coins-markets code={j.get('code')}，嘗試備援端點")
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
                    item.get("coinSymbol") or item.get("base") or ""
                )
                # 標準化為 base 格式（去除 USDT、連字符、底線）
                sym = str(sym_raw).replace("USDT", "").replace("USDT-PERP", "") \
                    .replace("-", "").replace("_", "").strip().upper()
                if not sym or len(sym) > 14:
                    continue
                # 15m / 短週期漲跌幅（多個可能欄位名）
                p15 = (
                    item.get("priceChangePercent15m") or
                    item.get("price_change_percent_15m") or
                    item.get("priceChangePercent30m") or
                    item.get("price_change_percent_30m") or
                    item.get("change15m") or item.get("change_15m")
                )
                # 24h 漲跌幅
                p24 = (
                    item.get("priceChangePercent24h") or
                    item.get("price_change_percent_24h") or
                    item.get("priceChange24h") or item.get("change_24h")
                )
                # 24h 成交量（USD）
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
                out.append({
                    "symbol": sym,
                    "coin": sym,
                    "price_change_percent_30m": p15,  # 15m 作為「30m 槽位」供現有邏輯讀取
                    "price_change_percent_24h": p24,
                    "_cg_volume_usd": vol,
                    "_raw_cg": item,
                })
            return out
        except Exception as e:
            logger.warning(f"coins-markets 異常: {e}")
            return []

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

    result = _try_coins_markets()
    if result:
        logger.info(f"[CoinGlass-First] coins-markets 成功取得 {len(result)} 個幣種")
        return result
    result = _try_coins_price_change()
    if result:
        logger.info(f"[CoinGlass-First] coins-price-change 備援取得 {len(result)} 個幣種")
    return result


def fetch_position_change():
    """【CoinGlass-First 架構】15M 高頻持倉狙擊主流程。
    標準版重構：以 CoinGlass 全市場數據為主軸掃描；BingX 僅在最終推播前做標的支援驗證。
    """
    global _coinglass_oi_first_failure_logged
    _coinglass_oi_first_failure_logged = False
    logger.info("【CoinGlass-First】開始執行 15M 持倉狙擊掃描...")

    # ── Step 1：CoinGlass 全市場數據（不預先受限於 BingX 幣種）────────────────
    # base_to_symbol / allowed_bases 延遲至 enrichment 前再載入（節省首輪時間）
    base_to_symbol: Dict[str, str] = {}
    allowed_bases: Set[str] = set()

    all_symbols_data = fetch_coinglass_coins_markets()
    if not all_symbols_data:
        # 備援：舊式 BingX-first 流程
        logger.warning("[CoinGlass-First] 主流端點失敗，啟用 BingX 備援流程")
        _ab, base_to_symbol, _bfp = fetch_bingx_contracts()
        allowed_bases = _ab
        if _bfp:
            all_symbols_data = _fetch_coins_price_change_fallback(_bfp)
            logger.info(f"[備援] BingX 取得 {len(all_symbols_data)} 個幣種 15m 價格數據")
        else:
            all_symbols_data = fetch_coins_price_change()
            logger.info(f"[備援] CoinGlass coins-price-change 取得 {len(all_symbols_data)} 個幣種")
        if not all_symbols_data:
            send_telegram_message("⚠️ 無法取得幣種漲跌資料，請稍後再試。", TG_THREAD_IDS['position_change'])
            return
    logger.info(f"[CoinGlass-First] 取得 {len(all_symbols_data)} 個幣種市場數據，開始篩選流程")

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

    # ── Step 2：價格門檻過濾（CoinGlass 15m/1h 漲跌幅）──────────────────────
    PRICE_GATEKEEPER = 0.6
    active_symbols = []
    for coin in all_symbols_data:
        p_change = extract_price_change_30m(coin)
        if abs(p_change) >= PRICE_GATEKEEPER:
            active_symbols.append(coin)
    logger.info(
        f"🔍 [CoinGlass] 價格門檻篩選: {len(all_symbols_data)} → {len(active_symbols)} 個活躍標的 "
        f"(|漲跌| >= {PRICE_GATEKEEPER}%) 進入 OI 檢查"
    )

    # ── Step 3：成交量預篩（直接讀 CoinGlass _cg_volume_usd，免去大量 BingX ticker 呼叫）──
    VOLUME_PREFILTER_MIN_USD = 3_000_000
    active_above_volume: List[Dict[str, Any]] = []
    vol_no_data = 0    # CoinGlass 無成交量欄位（保守放行）
    vol_below = 0      # 低於門檻剔除
    for coin in active_symbols:
        cg_vol = coin.get("_cg_volume_usd")
        if cg_vol is None:
            # CoinGlass 沒有成交量資訊 → 保守放行（避免漏掉小市值爆發訊號）
            vol_no_data += 1
            coin["_volume_usd"] = float(VOLUME_PREFILTER_MIN_USD)
            active_above_volume.append(coin)
        else:
            try:
                vol = float(cg_vol)
            except (TypeError, ValueError):
                vol = 0.0
            if vol < VOLUME_PREFILTER_MIN_USD:
                vol_below += 1
            else:
                coin["_volume_usd"] = vol
                active_above_volume.append(coin)
    logger.info(
        f"📊 [CoinGlass] 成交量預篩: 門檻 {VOLUME_PREFILTER_MIN_USD/1e6:.0f}M USD | "
        f"通過 {len(active_above_volume)} 個 (無量資料放行 {vol_no_data} 個) | "
        f"低於門檻刷除 {vol_below} 個 → 進入 OI 檢查"
    )

    # ── Step 4：排序 + 限制數量（前 50 固定，其餘隨機保多樣性）─────────────────
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
    MAX_WORKERS = _cb_get_max_workers(default=12)
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
        _dynamic_oi_mean_30m = None
        _dynamic_oi_std_30m = None
        _dynamic_oi_4star = None
        _dynamic_oi_5star = None
        logger.info(
            f"【動態 OI 門檻】樣本數 {_dynamic_oi_sample_size} < 10，沿用固定門檻 4星 {OI_FOR_4_STAR}% / 5星 {OI_FOR_5_STAR}%"
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

    # ── Step 7：延遲取得 BingX 支援名單（OI 篩選後再取，節省無效 API 呼叫）────────
    # 主流程（CoinGlass-First）時 base_to_symbol 為空，此時才補取；備援流程時已有值
    if not base_to_symbol:
        _ab2, base_to_symbol, _ = fetch_bingx_contracts()
        if _ab2:
            allowed_bases = _ab2
            logger.info(f"[BingX延遲載入] 取得 {len(allowed_bases)} 個支援交易對，用於 enrichment 與最終驗證")
        else:
            logger.warning("[BingX延遲載入] 取得失敗，enrichment 將使用通用格式推導，最終驗證將略過")

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
        symbol_param = clean_base + "USDT"
        global_data = fetch_global_account_ratio(symbol_param, "1h")
        time.sleep(0.5)
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
        ub_val = tech.get("ub_value") if tech else None
        lb_val = tech.get("lb_value") if tech else None
        atr_val = tech.get("atr") if tech else None

        # ── 5M 動能共振驗證（標準版核心濾網）────────────────────────────────
        resonance_5m = fetch_oi_resonance_5m(sym, cat)
        # 5M 方向明確相反 → 動能已竭，5 星降為 4 星（保留推播但降低信心）
        if resonance_5m is False and stars == 5:
            stars = 4
            reason = f"{reason} ⚠️[5M動能已竭:已降星]"
            logger.info(f"[5M共振] {sym} 5M OI 與 15M 反向，5星降為4星")

        all_top.append({
            **item,
            "priceChange24h": price_24h,
            "category": cat,
            "current_price": tech.get("current_price") if tech else None,
            "rsi": rsi_val,
            "atr": atr_val,
            "ub_value": tech.get("ub_value") if tech else None,
            "lb_value": tech.get("lb_value") if tech else None,
            "vwap_2h": tech.get("vwap_2h") if tech else None,
            "ema20_close": tech.get("ema20_close") if tech else None,
            # BingX 結構高低點（OI 起漲點防守用）
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
            "is_global_consensus": False,  # 預設 False，由 cooled_top 迴圈覆寫
        })
        resonance_str = "🔥共振" if resonance_5m is True else ("⚠️已竭" if resonance_5m is False else "❓未知")
        logger.info(
            f"Top 入選 {sym}: 星{stars} 區={zone} RSI={rsi_val} 布林上={ub_val} 布林下={lb_val} ATR={atr_val} 鯨魚指數={whale_idx} 5M={resonance_str} | {reason}"
        )

    # ── BingX 支援驗證守門（CoinGlass-First 架構：最終推播前確認 BingX 有此標的）────
    if allowed_bases:
        _before_bingx_check = len(all_top)
        _verified = []
        for _x in all_top:
            _cb = (_x.get("symbol") or "").replace("USDT", "").replace("-", "").replace("_", "").strip().upper()
            if _cb in allowed_bases:
                _verified.append(_x)
            else:
                logger.info(
                    f"[BingX驗證] {_cb} 不在 BingX 支援名單，移除"
                    f"（CoinGlass 有此訊號但 BingX 無對應合約，無法取 K 線/費率）"
                )
        _removed = _before_bingx_check - len(_verified)
        if _removed > 0:
            logger.info(f"[BingX驗證] 移除 {_removed} 個不支援標的，剩餘 {len(_verified)} 個進入推播流程")
        all_top = _verified
    else:
        logger.info("[BingX驗證] BingX 名單未取得（備援），跳過此守門步驟")

    # 用 BingX ticker 取現價 + 24h 成交額（僅標示低流動性與 5 星降星，不再做成交量門檻過濾）
    VOLUME_SOFT_MIN_USD = 5_000_000   # <5M 標示「成交量極低 小心滑價」
    VOLUME_5STAR_MIN_USD = 5_000_000   # 5 星僅允許 >5M，≤ 降為 4 星
    for x in all_top:
        sym = x.get("symbol", "")
        clean_base = sym.replace("USDT", "").replace("-", "").upper()
        preferred = base_to_symbol.get(clean_base) if base_to_symbol else None
        if not preferred:
            preferred = base_to_symbol.get(sym.upper()) if base_to_symbol else None
        snap = _fetch_bingx_ticker_snapshot(sym, preferred_symbol=preferred)
        if snap:
            if snap.get("price") is not None:
                x["current_price"] = snap["price"]
            vol = snap.get("volume_usd")
            if vol is not None:
                x["low_liquidity_warning"] = vol < VOLUME_SOFT_MIN_USD
                x["volume_usd"] = float(vol)
                if (x.get("stars") or 0) == 5 and vol <= VOLUME_5STAR_MIN_USD:
                    x["stars"] = 4
            else:
                x["low_liquidity_warning"] = False
                x["volume_usd"] = 0
                if (x.get("stars") or 0) == 5:
                    x["stars"] = 4
        else:
            x["low_liquidity_warning"] = False
            x["volume_usd"] = 0
            if (x.get("stars") or 0) == 5:
                x["stars"] = 4
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
            n_reversal = sum(1 for e in closed_today if e.get("exit_reason") == "reversal")
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
                f"✅ 已結案：{n_closed} 單（止盈 {n_tp1}｜止損 {n_sl}｜超時撤退 {n_timeout}｜籌碼反轉 {n_reversal}）",
                f"🎯 R 統計：贏 {sum_r_win:.2f}R｜輸 {sum_r_loss:.2f}R｜淨 {sum_r:.2f}R",
                f"※ 只要碰到 TP1 即計入贏局；止盈算贏、止損算輸（timeout / reversal 視為平局）→ {n_win} 贏 / {n_sl} 輸",
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
            logger.info(f"每日績效總結已發送: {summary_date} 推播 {n_pushed} 結案 {n_closed} 止盈 {n_tp1} 止損 {n_sl} 超時 {n_timeout} 反轉 {n_reversal} 勝率 {win_rate}% | 週R={sum_r_week:.2f} 月R={sum_r_month:.2f}")
    # 倉位追蹤依賴「上一輪（及之前）寫入的推播紀錄」；若 data 目錄在排程間未持久化（如 CI 無 cache），此處會一直是 0 筆
    in_window = [
        e for e in push_log_signals
        if isinstance(e, dict) and not e.get("notified_exit") and (e.get("symbol") or "").strip()
        and (now_ts - (e.get("ts") or 0)) <= EXIT_CHECK_WINDOW_HOURS * 3600
    ]
    logger.info(f"推播紀錄: 共 {len(push_log_signals)} 筆，48h 內且未結案 {len(in_window)} 筆待追蹤 (倉位追蹤需 data 目錄在排程間持久化)")
    if len(push_log_signals) == 0 and len(history) > 0:
        logger.warning("推播紀錄為 0 筆但冷卻有歷史 → 若曾推播過，請確認 workflow 的 data 目錄已正確 cache/還原，否則每輪從空檔開始、無法倉位追蹤")
    logger.info(f"【倉位追蹤】本輪待追蹤 {len(in_window)} 筆歷史訊號 (48h 內未結案)，開始檢查 SL/TP、籌碼反轉、進場理由…")
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

        if sl_level is not None or tp1_level is not None:
            full_sym = entry.get("full_symbol") or f"{sym_base}USDT"
            # 優先使用 BingX 30m K 線本地計算（可取得當前 K 線的 high/low，避免瞬間插針被漏判）
            kline_tech = _fetch_bingx_klines_and_calc(full_sym, preferred_symbol=None)
            cur_price = None
            kline_high = None
            kline_low = None
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
                logger.info(
                    f"【倉位追蹤K線】{sym_base} full_symbol={full_sym} "
                    f"cur_price={cur_price} last_high_30m={kline_high} last_low_30m={kline_low}"
                )
            # 若 K 線不可用，退回到 ticker 快照（確保不會整體失效）
            if kline_tech is None:
                logger.warning(f"倉位追蹤 K 線取得失敗，改用 ticker 快照取價: {sym_base} full_symbol={full_sym}")
                snap = _fetch_bingx_ticker_snapshot(full_sym, preferred_symbol=None)
                if snap and snap.get("price") is not None:
                    try:
                        cur_price = float(snap.get("price"))
                    except (TypeError, ValueError):
                        cur_price = None
                if cur_price is None and snap is None:
                    logger.warning(f"倉位追蹤取價失敗(無快照): {sym_base} full_symbol={full_sym}")
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
                            exit_msg = (
                                f"⚠️ *【剩餘倉位出場・整體獲利結案】*\n"
                                f"台灣時間 *{pushed_at_tw}* 推的 *{dir_label}* 標的 `{sym_base}` 價格回落觸及防守價。\n"
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
                            exit_msg = (
                                f"⚠️ *【已觸發止損・本倉結案】*\n"
                                f"台灣時間 *{pushed_at_tw}* 推的 *{dir_label}* 標的 `{sym_base}` 價格已觸及止損 `{sl_level}`。\n"
                                f"原因：價格觸及防守價，進場理由失效。\n\n{sl_copy}"
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
                            tp_msg = (
                                f"🛡️ *【盾牌啟動・此單已零風險】*\n"
                                f"✅ TP1 達標 | 台灣時間 *{pushed_at_tw}* 推的賭鬼 *{dir_label}* 標的 `{sym_base}`\n"
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
                            exit_msg = (
                                f"✅ *【已達止盈・本倉完結】*\n"
                                f"台灣時間 *{pushed_at_tw}* 推的 *{dir_label}* 標的 `{sym_base}` 已達止盈({tp1_lbl}) `{tp1_level}`。\n"
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
                        exit_msg = (
                            f"🎯 *【TP2 滿貫結案】*\n"
                            f"台灣時間 *{pushed_at_tw}* 推的賭鬼 *{dir_label}* 標的 `{sym_base}` 價格已觸及 TP2 `{tp2_level}`。\n"
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
            # 紀錄缺 sl/tp 欄位（舊格式或寫入時無價位），本輪僅做進場理由與籌碼追蹤
            logger.info(f"比對價格: {sym_base} 無止損/止盈價位可比對（紀錄缺 sl/tp），僅做進場理由與籌碼追蹤")

        # 1-3) 時間衰竭：持倉超過 24 小時仍未達標，建議保本/小虧出場
        if (not entry.get("closed")) and pushed_ts and (now_ts - pushed_ts) >= 24 * 3600:
            timeout_msg = (
                f"⏳ *【動能衰竭・建議保本平倉】*\n"
                f"標的 `{sym_base}` 已持倉超過 24 小時未達目標，主力動能減弱，"
                f"建議原價或小虧平倉，本倉結案。"
            )
            send_telegram_message(timeout_msg, TG_THREAD_IDS["position_change"], parse_mode="Markdown")
            entry["notified_exit"] = True
            entry["closed"] = True
            entry["exit_reason"] = "timeout"
            entry["realized_R"] = 0.0
            entry["closed_ts"] = int(now_ts)
            exit_notified_set.add(sym_base)
            logger.info(f"倉位追蹤已發送: {sym_base} 動能衰竭超過24小時，建議保本/小虧出場 (本倉結案)")

        # 價格已結案（SL / TP / timeout），不再做籌碼反轉檢查
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

        # 2) 籌碼變化追蹤：反轉→提早下車；同向加強→可考慮加碼
        if not cur_cat:
            continue
        # 2a) 籌碼反轉 → 提早出場
        reversal = False
        if pushed_dir == "多" and cur_cat in ("short_open", "long_close"):
            reversal = True
        elif pushed_dir == "空" and cur_cat in ("long_open", "short_close"):
            reversal = True
        if reversal:
            exit_msg = (
                f"🚨 *【籌碼反轉・很可惜】*\n"
                f"台灣時間 *{pushed_at_tw}* 推的 *{dir_label}* 標的 `{sym_base}` 籌碼已出現反轉，"
                f"建議減碼或再觀察，等待下一輪機會。\n"
                f"_（僅供參考，若未即時看到推播也可繼續持有，依自身狀況判斷。)_"
            )
            send_telegram_message(exit_msg, TG_THREAD_IDS["position_change"], parse_mode="Markdown")
            entry["notified_exit"] = True
            entry["closed"] = True
            entry["exit_reason"] = "reversal"
            entry["realized_R"] = 0.0
            entry["closed_ts"] = int(now_ts)
            exit_notified_set.add(sym_base)
            logger.info(f"出場提示已發送: {sym_base} (當初{pushed_dir}，本輪{cur_cat} 籌碼反轉，realized_R={entry.get('realized_R')})")
            continue
        # 2b) 籌碼同向：主力/倉位還在，可持續持有（不建議加碼，僅提醒遵守停損停利）
        same_side_strength = False
        if pushed_dir == "多" and cur_cat in ("long_open", "short_close"):
            same_side_strength = True
        elif pushed_dir == "空" and cur_cat in ("short_open", "long_close"):
            same_side_strength = True
        if same_side_strength and not entry.get("add_notified"):
            add_msg = (
                f"📈 *【籌碼同向・倉位仍在】*\n"
                f"台灣時間 *{pushed_at_tw}* 推的 *{dir_label}* 標的 `{sym_base}` 本輪主力/倉位方向仍在，"
                f"可持續持有，一樣遵守停損停利。"
            )
            send_telegram_message(add_msg, TG_THREAD_IDS["position_change"], parse_mode="Markdown")
            entry["add_notified"] = True
            exit_notified_set.add(sym_base)
            logger.info(f"加碼提示已發送: {sym_base} (當初{pushed_dir}，本輪{cur_cat} 同向加強)")
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
        logger.info(f"【倉位追蹤】本輪檢查完成，無觸發 (待追蹤 {len(in_window)} 筆均未達 SL/TP 或籌碼反轉/加碼)")
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
    # 寫入冷卻檔時也用正規化 symbol，確保下次讀取比對一致
    pairs_this_run = [(_cooldown_symbol(x.get("symbol")), _item_direction(x)) for x in cooled_top if x.get("symbol")]

    # ── 標準版：多所共識檢查（只對最終入選訊號查詢，節省 API 用量）────────────
    if cooled_top:
        for _item in cooled_top:
            _sym = _item.get("symbol", "")
            if _sym:
                _item["is_global_consensus"] = fetch_exchange_oi_consensus(_sym)

    # 僅在「實際有至少一則訊號」時才推主報表；無訊號或全被風報比篩掉 → 不推，安靜
    has_any = False
    if cooled_top:
        msg, has_any = build_report_message_tiered(cooled_top, processed_count, oi_success_count)
        if has_any:
            logger.info(
                f"【推播總結】本輪最終推播 {len(cooled_top)} 檔，處理幣種 {processed_count} 個，OI 成功 {oi_success_count} 個"
            )
            send_telegram_message(msg, TG_THREAD_IDS['position_change'], parse_mode="Markdown")
        else:
            logger.info(f"【未推播原因】本輪 {len(cooled_top)} 筆通過冷卻，但風報比篩選後 0 筆可推播（止盈風報比 < {MIN_TP1_R_FOR_PUSH}R），不發送主報表")
    else:
        if len(all_top) == 0:
            logger.info(f"【未推播原因】本輪無達 OI 門檻之標的（四類皆 0 筆），不發送主報表")
        else:
            logger.info(f"【未推播原因】本輪 {len(all_top)} 筆候選皆被冷卻（4h 內同幣同方向已推過），不發送主報表")

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
        if "多" in side:
            icon, title, advice = "🟢", "多軍陣亡 (可嘗試摸底)", "👉 價格若止跌，分批接多 (Buy the Dip)"
        else:
            icon, title, advice = "🔴", "空軍陣亡 (可嘗試摸頭)", "👉 價格若漲不動，嘗試做空 (Short the Top)"
        lines.append(f"{icon} *{sym}* 💥 爆倉 *${amt:.1f}萬*")
        lines.append(f"💀 慘況：{title}")
        lines.append(f"💡 策略：{advice}")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(f"⏰ {now_str} | 別人恐懼我貪婪，帶血籌碼最香。")
    return "\n".join(lines)


def run_liquidity_radar_once():
    """主流程：流動性獵取雷達（執行一次，適合排程或 HTTP 觸發）"""
    logger.info(f"開始執行流動性獵取雷達，共 {len(LIQ_SYMBOLS)} 個幣種...")

    events: List[Dict] = []

    for idx, symbol in enumerate(LIQ_SYMBOLS):
        try:
            data_array = fetch_liquidation_data(symbol)
            if data_array is None:
                continue
            event = process_liquidation_data(symbol, data_array)
            if event:
                events.append(event)
            # 控制請求節奏，避免觸發頻率限制
            if idx < len(LIQ_SYMBOLS) - 1:
                time.sleep(LIQ_REQUEST_DELAY)
        except Exception as e:
            logger.error(f"處理 {symbol} 流動性數據時發生錯誤: {str(e)}")

    if not events:
        logger.info("本次監控無幣種達到極端爆倉門檻")
        return

    msg = format_liquidity_consolidated_message(events)
    thread_id = TG_THREAD_IDS.get("liquidity_radar", 3)
    keyboard = {
        "inline_keyboard": [[{"text": "💀 查看詳細爆倉數據", "url": "https://www.coinglass.com/zh-TW/LiquidationData"}]]
    }
    send_telegram_message(msg, thread_id, parse_mode="Markdown", reply_markup=keyboard)
    logger.info(f"流動性獵取雷達完成，推送 {len(events)} 個幣種的極端爆倉事件")


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
    """獲取聚合累計成交量差值（CVD）歷史數據"""
    url = "https://open-api-v4.coinglass.com/api/futures/aggregated-cvd/history"
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
        logger.debug(f"嘗試獲取 CVD 歷史 {symbol}")
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.debug(f"聚合 CVD API 返回狀態碼: {response.status_code} for {symbol}")
            return None
        
        data = response.json()
        if data.get('code') not in ['0', 0, 200, '200']:
            error_msg = data.get('msg') or data.get('message') or '未知錯誤'
            logger.debug(f"聚合 CVD API 返回錯誤: {error_msg} (code: {data.get('code')}) for {symbol}")
            return None
        
        data_list = data.get('data', [])
        if isinstance(data_list, list) and len(data_list) > 0:
            logger.debug(f"成功獲取 CVD 歷史 {symbol}: {len(data_list)} 條")
            # 輸出數據樣本以便調試
            if len(data_list) > 0:
                sample = data_list[0]
                logger.debug(f"CVD 數據樣本 {symbol}: 字段 {list(sample.keys())[:10]}")
            return data_list
        else:
            logger.debug(f"聚合 CVD API 返回空數據 for {symbol}")
            return None
    except Exception as e:
        logger.debug(f"獲取聚合 CVD 歷史失敗 {symbol}: {str(e)}")
        import traceback
        logger.debug(traceback.format_exc())
        return None


def _cvd_change_last2(symbol: str, interval: str = "1h") -> Optional[float]:
    """取 1h CVD 最近 2 根 K 的變化值 (Current_CVD - Prev_CVD)。用於過濾量價背離。"""
    data = fetch_aggregated_cvd_history(symbol, interval)
    if not data or len(data) < 2:
        return None
    sort_key = lambda x: int(x.get("time") or x.get("timestamp") or x.get("t") or 0)
    sorted_data = sorted(data, key=sort_key)
    last_two = sorted_data[-2:]
    cvd_vals = []
    for item in last_two:
        v = None
        for key in ("cum_vol_delta", "cvd", "value", "cvdValue", "cumulativeVolumeDelta", "volumeDelta"):
            if item.get(key) is not None:
                try:
                    v = float(item[key])
                    break
                except (TypeError, ValueError):
                    pass
        if v is not None:
            cvd_vals.append(v)
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


def fetch_hyperliquid_pnl_distribution() -> Optional[Dict]:
    """獲取 Hyperliquid 錢包盈虧分佈"""
    url = f"{CG_API_BASE}/api/hyperliquid/wallet/pnl-distribution"
    headers = {
        "CG-API-KEY": CG_API_KEY,
        "accept": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.error(f"Hyperliquid PNL Distribution API 錯誤: {response.status_code}")
            return None
        
        result = response.json()
        if result.get('code') not in ['0', 0, 200, '200']:
            logger.error(f"Hyperliquid PNL Distribution API 返回錯誤: {result}")
            return None
        
        return result.get('data', result)
    except Exception as e:
        logger.error(f"獲取 Hyperliquid PNL Distribution 失敗: {str(e)}")
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
        
        lines.append(f"⏰ 時間：{time_str}")
        lines.append(f"標的：`{symbol}`")
        lines.append(f"方向：{direction_emoji} {direction_text}")
        lines.append(f"規模：{value_display} USD")
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
            logger.info("[黃金訊號] 已推播 %s 觸及", "止盈" if hit == "tp" else "止損")
            state = {}
            _save_gold_state(state)

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
    now_utc = datetime.now(timezone.utc)
    try:
        age_sec = (pd.Timestamp(now_utc) - pd.Timestamp(last_bar_utc)).total_seconds()
    except Exception:
        age_sec = 0
    if age_sec > 24 * 3600:
        logger.info("[黃金訊號] 數據過舊（最後 K 線已逾 24h，可能休市），跳過推播")
        return
    ok, reason = apply_filters(
        signal.direction, cfg, df_1h, df_dxy=df_dxy, now=now_utc
    )
    if not ok:
        logger.info("[黃金訊號] 訊號被濾網拒絕: %s", reason)
        return
    # 同向訊號過濾：該倉未結束或尚未出現反向訊號前，不再推同向
    if state.get("last_direction") == signal.direction:
        logger.info("[黃金訊號] 同向訊號重疊（目前仍有 %s 倉），跳過推播", signal.direction)
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
            "last_time_utc": datetime.now(timezone.utc).isoformat(),
        })
    logger.info("[黃金訊號] 推播完成 | thread_id=%s 發送結果=%s", thread_id, sent)


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
    else:
        print("請指定要執行的功能，例如: python jackbot.py sector_ranking")

