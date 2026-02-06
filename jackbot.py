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
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any, Tuple, Set
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import random
import pandas as pd
import numpy as np

# 台灣台北時區（UTC+8）
TAIPEI_TZ = timezone(timedelta(hours=8))

# 配置日誌
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
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
    }

# 其他配置
EXCHANGE = "Binance"
TIME_TYPE = "h1"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
# 持倉變化篩選：改為只偵測合約幣種（使用 API 獲取）
MAX_SYMBOLS = 904  # 將由 API 返回的合約幣種數量決定

# 數據存儲目錄
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

# CoinGlass OI 呼叫限速（初創版 80 次/分鐘，global 必須在函數內「最先」宣告再賦值）
_coinglass_oi_rate_limiter = None

# ==================== 工具函數 ====================

def send_telegram_message(text: str, thread_id: int, parse_mode: str = "Markdown") -> bool:
    """發送訊息到 Telegram"""
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "message_thread_id": thread_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    
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
    """從文件加載 JSON 數據"""
    if filepath.exists():
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"讀取文件失敗 {filepath}: {str(e)}")
    return default if default is not None else []


def save_json_file(filepath: Path, data: Any) -> bool:
    """保存數據到 JSON 文件"""
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
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
    "Meme": "Meme 迷因板塊",
    "Artificial Intelligence (AI)": "AI 人工智慧",
    "Real World Assets (RWA)": "RWA 現實資產",
    "Decentralized Finance (DeFi)": "DeFi 去中心化金融",
    "Layer 2": "第二層網路 (L2)",
    "Gaming (GameFi)": "GameFi 電競遊戲",
    "Smart Contract Platform": "智慧合約公鏈",
    "Exchange-based Tokens": "交易所代幣",
    "Stablecoins": "穩定幣"
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
    """發送排行榜到 Telegram"""
    message = "📊 *【全球主流加密板塊排行榜】(1H)* \n\n"
    message += "🔥 *主流板塊強弱一覽：*\n"
    
    for index, sector in enumerate(ranking):
        medal = "🥇" if index == 0 else "🥈" if index == 1 else "🥉" if index == 2 else "🔹"
        change_str = f"{sector['change']:.2f}"
        emoji = "📈" if sector['change'] > 0 else "📉"
        sign = "+" if sector['change'] > 0 else ""
        message += f"{medal} *{sector['displayName']}* `{sign}{change_str}%` {emoji}\n"
    
    message += "\n💡 _由傑克 AI 每小時自動監控資金流向_"
    
    send_telegram_message(message, TG_THREAD_IDS['sector_ranking'])


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
    """購買力監控：監控穩定幣市值和聚合穩定幣保證金持倉"""
    logger.info("開始執行購買力監控...")
    
    # 1. 獲取穩定幣市值歷史
    marketcap_data = fetch_stablecoin_marketcap_history()
    if not marketcap_data:
        logger.warning("無法獲取穩定幣市值數據")
        return
    
    # 2. 計算市值變化率
    mcap_change = calculate_marketcap_change(marketcap_data)
    if not mcap_change:
        logger.warning("無法計算穩定幣市值變化率")
        return
    
    # 3. 獲取穩定幣 OI 歷史
    oi_data = fetch_aggregated_stablecoin_oi_history("BTC", "1h")
    if not oi_data:
        logger.warning("無法獲取穩定幣 OI 數據")
        return
    
    # 4. 計算 OI 變化率
    oi_change = calculate_oi_change(oi_data)
    if not oi_change:
        logger.warning("無法計算穩定幣 OI 變化率")
        return
    
    # 5. 判斷是否需要推播（放寬條件）
    should_alert = False
    alert_type = []
    
    # 市值增加 > 0.05%（放寬從 0.1% 到 0.05%）
    if mcap_change.get('change_1h') is not None and mcap_change['change_1h'] > 0.05:
        should_alert = True
        alert_type.append("資金進場")
    elif mcap_change.get('change_24h') is not None and mcap_change['change_24h'] > 0.05:
        should_alert = True
        alert_type.append("資金進場")
    
    # OI 暴增 > 1%（放寬從 2% 到 1%）
    if oi_change.get('change_1h') is not None and oi_change['change_1h'] > 1.0:
        should_alert = True
        alert_type.append("槓桿堆積")
    elif oi_change.get('change_24h') is not None and oi_change['change_24h'] > 1.0:
        should_alert = True
        alert_type.append("槓桿堆積")
    
    # 如果沒有觸發警報條件，仍然推播數據（但標註為正常狀態）
    # 這樣用戶可以持續監控購買力變化
    if not should_alert:
        logger.info(f"未觸發警報條件，但仍推播當前數據供監控")
        logger.info(f"市值變化: 1h={mcap_change.get('change_1h')}, 24h={mcap_change.get('change_24h')}")
        logger.info(f"OI 變化: 1h={oi_change.get('change_1h')}, 24h={oi_change.get('change_24h')}")
        # 不 return，繼續構建推播訊息
    
    # 6. 構建推播訊息
    now = get_taipei_time()
    time_str = format_datetime(now)
    
    lines = []
    lines.append("💰 *【購買力監控】*")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    
    # 穩定幣市值
    lines.append("📊 *穩定幣市值*：")
    if mcap_change.get('latest_mcap'):
        mcap_b = mcap_change['latest_mcap'] / 1_000_000_000  # 轉換為十億
        lines.append(f"當前市值：*{mcap_b:.2f}B USD*")
    
    if mcap_change.get('change_1h') is not None:
        change_1h = mcap_change['change_1h']
        emoji = "📈" if change_1h > 0 else "📉"
        lines.append(f"{emoji} 1小時變化：*{change_1h:+.2f}%*")
    
    if mcap_change.get('change_24h') is not None:
        change_24h = mcap_change['change_24h']
        emoji = "📈" if change_24h > 0 else "📉"
        lines.append(f"{emoji} 24小時變化：*{change_24h:+.2f}%*")
    
    lines.append("")
    
    # 穩定幣 OI
    lines.append("⚡ *聚合穩定幣保證金持倉*：")
    if oi_change.get('latest_oi'):
        oi_b = oi_change['latest_oi'] / 1_000_000_000  # 轉換為十億
        lines.append(f"當前持倉：*{oi_b:.2f}B USD*")
    
    if oi_change.get('change_1h') is not None:
        change_1h = oi_change['change_1h']
        emoji = "📈" if change_1h > 0 else "📉"
        lines.append(f"{emoji} 1小時變化：*{change_1h:+.2f}%*")
    
    if oi_change.get('change_24h') is not None:
        change_24h = oi_change['change_24h']
        emoji = "📈" if change_24h > 0 else "📉"
        lines.append(f"{emoji} 24小時變化：*{change_24h:+.2f}%*")
    
    lines.append("")
    
    # 警報提示（如果有觸發）
    if alert_type:
        lines.append("🚨 *警報類型*：")
        for alert in alert_type:
            if alert == "資金進場":
                lines.append("✅ 資金進場：場外資金（Fiat）兌換成穩定幣準備買入")
            elif alert == "槓桿堆積":
                lines.append("⚠️ 槓桿堆積：場內資金正在使用穩定幣作為保證金開立做多倉位（看漲）")
        lines.append("")
    
    # 船長解讀
    lines.append("💡 *船長解讀*：")
    if alert_type:
        if "資金進場" in alert_type and "槓桿堆積" in alert_type:
            lines.append("市值上升 + OI 上升 = 雙重利好，市場資金充裕且槓桿活躍，上漲動能強勁。")
        elif "資金進場" in alert_type:
            lines.append("市值上升代表場外資金流入，這是長線利好信號，預示後續買盤支撐。")
        elif "槓桿堆積" in alert_type:
            lines.append("OI 暴增預示波動將至，需注意槓桿風險，可能出現劇烈波動。")
    else:
        # 沒有觸發警報時的提示
        lines.append("目前購買力變化在正常範圍內（市值變化 <= 0.05%，OI 變化 <= 1%）。")
        lines.append("持續監控中，如有異常變化將及時通知。")
    
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"⏰ 更新時間：{time_str}")
    
    message = "\n".join(lines)
    send_telegram_message(message, TG_THREAD_IDS.get('buying_power_monitor', 246), parse_mode="Markdown")
    logger.info("購買力監控推播完成")


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
    - bases_for_price: 每個合約一個 asset（用於向 BingX 取 30m 價格），不重複。
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


def fetch_price_change_30m_bingx(symbol: str) -> Optional[float]:
    """
    【BingX 官方數據源】30 分鐘 K 線漲跌幅。
    與 CoinGlass 初創版 OI 顆粒度 >= 30m 一致，推播統一為 30m 版本。
    """
    clean_symbol = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    sym_formatted = f"{clean_symbol}-USDT"
    url = "https://open-api.bingx.com/openApi/swap/v3/quote/klines"
    params = {"symbol": sym_formatted, "interval": "30m", "limit": 3}
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
        # 假設 data 時間正序：data[-1] 為最新 K，data[-2] 為上一根
        latest_k = data[-1]
        # 本根 30m K 線：open=30 分鐘前價格，close=當前價格 → 漲跌幅 = (close - open) / open
        current_price = float(latest_k.get("close") or 0)
        open_price_15m_ago = float(latest_k.get("open") or 0)
        if open_price_15m_ago == 0:
            return None
        change_percent = ((current_price - open_price_15m_ago) / open_price_15m_ago) * 100
        return change_percent
    except Exception:
        return None


def _fetch_coins_price_change_fallback(supported_coins: List[str], max_symbols: int = 99999) -> List[Dict]:
    """初創版 Fallback：使用 BingX 官方 API 獲取 30m 漲跌幅（降速版，避免 429）。"""
    symbols = list(supported_coins)[:max_symbols]
    out = []
    logger.info(f"正在使用 BingX 官方 API 獲取 {len(symbols)} 個幣種的 30m 價格數據 (降速模式)...")

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
    計算單一 symbol 30 分鐘 OI 變化%
    【降速版】大幅增加延遲以適應 CoinGlass 嚴格限流
    """
    global _coinglass_oi_rate_limiter, _coinglass_oi_first_failure_logged

    with _oi_rate_limit_lock:
        if _coinglass_oi_rate_limiter is None:
            _coinglass_oi_rate_limiter = {"last_call": 0.0}
        now = time.time()
        elapsed = now - _coinglass_oi_rate_limiter.get("last_call", 0.0)
        wait_time = 1.2 + random.uniform(0.1, 0.4)
        if elapsed < wait_time:
            time.sleep(wait_time - elapsed)
        _coinglass_oi_rate_limiter["last_call"] = time.time()

    base_symbol = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    url = f"{CG_API_BASE}/api/futures/open-interest/aggregated-history"
    params = {"symbol": base_symbol, "interval": "30m", "limit": 5}
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}

    for attempt in range(2):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            if response.status_code == 200:
                result = response.json()
                if result.get("code") in ("0", 0, 200, "200"):
                    data_list = result.get("data", result.get("list", []))
                    change = _parse_oi_change_from_data_list(data_list)
                    if change is not None:
                        return change
                msg = result.get("msg", "")
                if "Too Many Requests" in msg or result.get("code") == "400":
                    logger.warning(f"CoinGlass 限流 ({base_symbol})，休息 3 秒...")
                    time.sleep(3)
                    continue
            elif response.status_code == 429:
                time.sleep(3)
                continue
        except Exception as e:
            logger.debug(f"OI 請求異常 {base_symbol}: {e}")
            time.sleep(1)
    return None


def normalize_symbol(coin: Dict) -> Optional[str]:
    """從幣種數據中提取 symbol"""
    return coin.get('symbol') or coin.get('pair') or coin.get('name') or coin.get('coin') or coin.get('symbolName')


def extract_price_change_15m(coin: Dict) -> float:
    """提取 15 分鐘價格變化%（其他模組用，持倉篩選已改 30m）"""
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
    """提取 30 分鐘價格變化%（持倉篩選 30m 版：價格與 OI 皆 30m）"""
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


def _fetch_coinglass_rsi(symbol: str) -> Optional[Dict]:
    """CoinGlass V4 RSI：降速版，失敗直接放棄切換本地計算（省 API 額度）。"""
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    symbol_pair = base + "USDT"
    url = f"{CG_API_BASE}/api/futures/indicators/rsi"
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}
    tries = [("Binance", symbol_pair)]

    for exchange, sym_param in tries:
        params = {"exchange": exchange, "interval": "30m", "symbol": sym_param}
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
    """CoinGlass V4 布林帶：先試 Binance/BingX + BTCUSDT，再試 BingX + base。"""
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    symbol_pair = base + "USDT"
    url = f"{CG_API_BASE}/api/futures/indicators/boll"
    headers = {"CG-API-KEY": CG_API_KEY, "accept": "application/json"}
    tries = [("Binance", symbol_pair), ("BingX", symbol_pair), ("BingX", base)]
    last_error = None
    for exchange, sym_param in tries:
        params = {"exchange": exchange, "interval": "30m", "symbol": sym_param}
        try:
            r = requests.get(url, params=params, headers=headers, timeout=8)
            time.sleep(0.2)
            if r.status_code != 200:
                last_error = f"status={r.status_code} body={r.text[:300]}"
                continue
            data = r.json()
            code = data.get("code")
            msg = (data.get("msg") or data.get("message") or "").lower()
            if code not in (0, "0", 200, "200", None) or "instrument" in msg:
                last_error = f"code={code} msg={data.get('msg') or data.get('message')}"
                continue
            raw = data.get("data", data.get("list", []))
            if isinstance(raw, list) and len(raw) > 0:
                return data
            if isinstance(raw, dict) and raw:
                return data
            last_error = "數據為空"
        except Exception as e:
            last_error = str(e)
    logger.warning(f"CoinGlass BOLL {base}: 所有嘗試均失敗，最後: {last_error}")
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
    本地計算 Fallback：用 BingX 30m K 線 + 純 pandas 算 RSI(14)、布林帶(20,2)。
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
            params = {"symbol": sym_param, "interval": "30m", "limit": 60}
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
        logger.warning(f"BingX 本地計算失敗 {clean}: 嘗試了 {try_symbols} 皆無數據")
        return None
    closes = []
    for row in raw:
        c = None
        if isinstance(row, dict):
            c = row.get("close") or row.get("c")
        elif isinstance(row, (list, tuple)) and len(row) >= 5:
            c = row[4]
        if c is not None:
            try:
                closes.append(float(c))
            except (TypeError, ValueError):
                pass
    if len(closes) < 20:
        return None
    series = pd.Series(closes)
    rsi_series = _rsi(series, period=14)
    if rsi_series.empty or pd.isna(rsi_series.iloc[-1]):
        return None
    rsi_val = float(rsi_series.iloc[-1])
    upper_bb, _, lower_bb = _bbands(series, length=20, std_dev=2.0)
    ub_value = float(upper_bb.iloc[-1]) if not pd.isna(upper_bb.iloc[-1]) else None
    lb_value = float(lower_bb.iloc[-1]) if not pd.isna(lower_bb.iloc[-1]) else None
    current_price = float(closes[-1]) if closes else None
    touch_upper = current_price is not None and ub_value is not None and current_price >= ub_value
    touch_lower = current_price is not None and lb_value is not None and current_price <= lb_value
    return {
        "rsi": rsi_val,
        "touch_upper": touch_upper,
        "touch_lower": touch_lower,
        "current_price": current_price,
        "ub_value": ub_value,
        "lb_value": lb_value,
        "source": "BingX",
        "real_symbol": found_symbol,
    }


def calculate_technicals(symbol: str, bingx_symbol_override: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    先試 CoinGlass API；失敗則用 BingX K 線 + 本地 RSI/布林帶計算。
    若傳入 bingx_symbol_override（來自 BingX contracts），BingX 請求時優先使用該 symbol。
    """
    base = symbol.replace("USDT", "").replace("-", "").replace("_", "").upper()
    # 1) 先試 CoinGlass
    rsi_data = _fetch_coinglass_rsi(symbol)
    if rsi_data is None:
        tech = _fetch_bingx_klines_and_calc(symbol, preferred_symbol=bingx_symbol_override)
        if tech:
            return tech
        logger.info(f"RSI 無數據 {base}: CoinGlass 與 BingX 本地計算均失敗（可能限流 429 或該交易對 K 線不可用）")
        return None
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

    return {
        "rsi": rsi_val,
        "touch_upper": touch_upper,
        "touch_lower": touch_lower,
        "current_price": current_price,
        "ub_value": ub_value,
        "lb_value": lb_value,
        "source": "CoinGlass",
    }


# 四區塊 + 五星制：zone 為推播區塊名，stars 1=最差 5=最佳
ZONE_DIP = "抄底區"
ZONE_TOP = "摸頭區"
ZONE_BREAKOUT_LONG = "突破追漲區"
ZONE_BREAKOUT_SHORT = "跌破追跌區"


def _classify_signal_and_tier(
    item: Dict,
    category: str,
    tech: Optional[Dict],
    funding_rate: Optional[float] = None,
) -> Tuple[str, str, int, str, str]:
    """
    分級邏輯：回傳 (signal_label, zone, stars, rsi_desc, reason)。
    zone 為四區塊之一；stars 1～5，5 最佳；reason 為戰術理由。
    """
    oi = item.get("oiChange30m") or 0
    rsi = tech.get("rsi") if tech else None
    touch_upper = tech.get("touch_upper", False) if tech else False
    touch_lower = tech.get("touch_lower", False) if tech else False
    EXTREME_FUNDING = 0.0005  # 0.05%
    rsi_desc = "RSI —"
    if tech is None:
        label_by_cat = {"long_open": "📈 多方開倉", "long_close": "📉 多方平倉", "short_open": "📉 空方開倉", "short_close": "📈 空方平倉"}
        if funding_rate is not None:
            if funding_rate > EXTREME_FUNDING:
                rsi_desc = "費率過熱🔥"
            elif funding_rate < -EXTREME_FUNDING:
                rsi_desc = "費率過冷❄️"
        label = label_by_cat.get(category, "📊 異動")
        reason = "數據異動"
        if category == "short_open":
            return (label, ZONE_BREAKOUT_SHORT, 2, rsi_desc, reason)
        if category == "long_open":
            return (label, ZONE_BREAKOUT_LONG, 2, rsi_desc, reason)
        if category == "short_close":
            return (label, ZONE_BREAKOUT_LONG, 2, rsi_desc, reason)
        if category == "long_close":
            return (label, ZONE_BREAKOUT_SHORT, 2, rsi_desc, reason)
        return (label, ZONE_DIP, 1, rsi_desc, reason)
    if rsi is not None:
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
    elif funding_rate is not None:
        if funding_rate > EXTREME_FUNDING:
            rsi_desc = "費率過熱🔥"
        elif funding_rate < -EXTREME_FUNDING:
            rsi_desc = "費率過冷❄️"
    # 摸頭區：做空、過熱
    bearish_cond = touch_upper or (rsi is not None and rsi > 70)
    if rsi is None and funding_rate is not None and funding_rate > EXTREME_FUNDING:
        bearish_cond = True
    if oi < -2.0 and bearish_cond:
        return ("🔴 摸頭做空", ZONE_TOP, 5, rsi_desc, "⛽ 空軍投降 (嘎空結束)")
    # 突破追漲區：嘎空、順勢做多
    if oi > 2.0 and funding_rate is not None and funding_rate < -0.001:
        return ("🟢 潛在嘎空", ZONE_BREAKOUT_LONG, 5, rsi_desc, "🚀 資金湧入 (強勢突破)")
    # 抄底區：做多、超賣
    bullish_cond = touch_lower or (rsi is not None and rsi < 30)
    if rsi is None and funding_rate is not None and funding_rate < -EXTREME_FUNDING:
        bullish_cond = True
    if oi < -2.0 and bullish_cond:
        return ("🟢 抄底做多", ZONE_DIP, 5, rsi_desc, "🩸 賣壓竭盡 (跌深反彈)")
    # 順勢：OI > 2% 未過熱
    if oi > 2.0 and (rsi is None or (30 < rsi < 70)):
        zone = ZONE_BREAKOUT_LONG if category in ("long_open", "long_close") else ZONE_BREAKOUT_SHORT
        reason = "🚀 資金湧入 (強勢突破)" if zone == ZONE_BREAKOUT_LONG else "📉 空方加碼 (順勢追跌)"
        return ("🟡 順勢觀察", zone, 4, rsi_desc, reason)
    # 偏空過熱 -> 摸頭區
    if rsi is not None and (rsi > 70 or touch_upper):
        return ("🔴 偏空過熱", ZONE_TOP, 4, rsi_desc, "🔨 主力佈空 (漲多過熱)")
    # 偏多超賣 -> 抄底區
    if rsi is not None and (rsi < 30 or touch_lower):
        return ("🟢 偏多超賣", ZONE_DIP, 4, rsi_desc, "🛒 主力吸籌 (跌深撿便宜)")
    # 其餘依 category 歸入追漲/追跌
    if category == "short_open":
        return ("📊 異動", ZONE_BREAKOUT_SHORT, 2, rsi_desc, "數據異動")
    if category == "long_open":
        return ("📊 異動", ZONE_BREAKOUT_LONG, 2, rsi_desc, "數據異動")
    return ("📊 異動", ZONE_DIP if category in ("long_close", "long_open") else ZONE_TOP, 1, rsi_desc, "數據異動")


def build_report_message_tiered(
    enriched_items: List[Dict],
    processed_count: int = 0,
    oi_success_count: int = 0,
) -> str:
    """新版推播：強調進場理由與戰術 + 費率狀態提示"""

    def star_str(n: int) -> str:
        return "⭐" * (n or 0) + "☆" * (5 - (n or 0))

    def fmt_pct(num):
        if num is None or (isinstance(num, float) and (num != num)):
            return "0.00%"
        return f"{'+' if num >= 0 else ''}{num:.2f}%"

    zone_order = [ZONE_TOP, ZONE_DIP, ZONE_BREAKOUT_LONG, ZONE_BREAKOUT_SHORT]
    zone_map = {
        ZONE_TOP: "🏔️ 【摸頭區】 (高風險高報酬)",
        ZONE_DIP: "🌊 【抄底區】 (高風險高報酬)",
        ZONE_BREAKOUT_LONG: "🚀 【突破追漲】 (順勢交易)",
        ZONE_BREAKOUT_SHORT: "📉 【跌破追跌】 (順勢交易)",
    }

    lines = []
    lines.append("💰 *【傑克短線戰術雷達】(30分)*")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
    now_str = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M")
    lines.append(f"🕐 {now_str} (台灣)")
    lines.append("")

    has_any = False

    for zone in zone_order:
        items = [x for x in enriched_items if x.get("zone") == zone and (x.get("stars") or 0) >= 3]
        lines.append(f"*{zone_map[zone]}*")

        if not items:
            lines.append("_此輪無水標_")
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")
            continue

        has_any = True
        items_sorted = sorted(items, key=lambda x: (-(x.get("stars") or 0), -(abs(x.get("oiChange30m") or 0))))

        for x in items_sorted:
            sym = x.get("symbol", "")
            stars = x.get("stars", 1)
            signal = x.get("signal_label", "觀望")
            reason = x.get("reason", "數據異動")
            price = x.get("current_price")
            price_str = f"${price:,.4g}" if price is not None and isinstance(price, (int, float)) else "—"

            rsi_part = x.get("rsi_desc", "RSI —")
            oi_val = x.get("oiChange30m", 0)
            oi_str = fmt_pct(oi_val)

            funding = x.get("funding_rate")
            fee_str = ""
            if funding is not None:
                fee_val = funding * 100
                if fee_val >= 0.05:
                    fee_state = "🔥多頭過熱"
                elif fee_val >= 0.02:
                    fee_state = "⚡多方擁擠"
                elif fee_val <= -0.05:
                    fee_state = "❄️空頭過熱"
                elif fee_val <= -0.02:
                    fee_state = "🥶空方擁擠"
                else:
                    fee_state = "⚖️多空平衡"
                fee_str = f"｜費率 {fee_val:.3f}% ({fee_state})"

            lines.append(f"{signal[:1]} *{sym}* ({star_str(stars)})")
            lines.append(f"   💡 *戰術：{signal[2:]}*")
            lines.append(f"   📝 *理由：{reason}*")
            lines.append(f"   📊 數據：價 {price_str}｜{rsi_part}｜持倉 {oi_str}{fee_str}")
            lines.append("")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━")

    if not has_any:
        lines.append("😴 目前市場平靜，無明顯交易訊號。")
        lines.append("")

    lines.append("📌 *戰術速查*")
    lines.append("• ⛽ *嘎空結束*：空單停損離場，上漲動能耗盡 ⮕ 做空")
    lines.append("• 🔨 *主力佈空*：價格漲多，大戶加碼空單賭回調 ⮕ 做空")
    lines.append("• 🩸 *賣壓竭盡*：多單停損離場，下跌動能耗盡 ⮕ 做多")
    lines.append("• 🛒 *主力吸籌*：價格跌深，大戶進場撿便宜 ⮕ 做多")
    lines.append("")
    lines.append("⚠️ 系統僅供輔助，請嚴格設定停損。")

    return "\n".join(lines)


def build_report_message(top_long_open: List, top_long_close: List, top_short_open: List, top_short_close: List, processed_count: int = 0, oi_success_count: int = 0) -> str:
    """組合推播文字（30m 版：價格與持倉皆為 30 分鐘）"""
    lines = []
    lines.append("💰 *【傑克短線持倉異動排行榜】(30分)*")
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
    lines.append("請先判斷 *30分K價格走勢趨勢* 去換位思考主力動機")
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
        if price_change_30m > 0:
            if oi_change_30m > 0:
                category = 'long_open'
            elif oi_change_30m < 0:
                category = 'long_close'
        elif price_change_30m < 0:
            if oi_change_30m > 0:
                category = 'short_open'
            elif oi_change_30m < 0:
                category = 'short_close'
        if category:
            return {
                'status': 'success',
                'category': category,
                'symbol': symbol,
                'priceChange30m': price_change_30m,
                'oiChange30m': oi_change_30m
            }
        else:
            return {'status': 'no_category', 'symbol': symbol}
            
    except Exception as e:
        logger.error(f"處理 {symbol} 時發生錯誤: {str(e)}")
        return {'status': 'error', 'symbol': symbol, 'error': str(e)}


def fetch_position_change():
    """主流程：持倉變化篩選（原本的邏輯，只是改成只偵測 BingX 的 554 個交易對）"""
    global _coinglass_oi_first_failure_logged
    _coinglass_oi_first_failure_logged = False  # 本輪只記錄第一次 OI 失敗，方便診斷
    logger.info("開始執行持倉變化篩選，只偵測 BingX 合約幣種...")

    # 步驟1：先抓 BingX 有支援的合約交易對；失敗則用 CoinGlass 名單
    allowed_bases, base_to_symbol, bases_for_price = fetch_bingx_contracts()
    if allowed_bases:
        bingx_symbols_upper = allowed_bases
        logger.info(f"從 BingX contracts 取得 {len(allowed_bases)} 個合約交易對")
    else:
        bingx_symbols = fetch_supported_futures_coins()
        if not bingx_symbols:
            send_telegram_message("⚠️ 無法取得合約幣種名單，請稍後再試。", TG_THREAD_IDS['position_change'])
            return
        bingx_symbols_upper = {s.upper() for s in bingx_symbols}
        base_to_symbol = {}
        bases_for_price = []
        logger.info(f"獲取到 {len(bingx_symbols)} 個 BingX 合約幣種（CoinGlass 名單）")

    # 步驟2：依「BingX 交易對」取得 30m 價格（有 contracts 則直接用 BingX 取價，不再依賴 CoinGlass 漲跌幅）
    if bases_for_price:
        all_symbols_data = _fetch_coins_price_change_fallback(bases_for_price)
        logger.info(f"依 BingX 交易對取得 {len(all_symbols_data)} 個幣種的 30m 價格數據")
    else:
        all_symbols_data = fetch_coins_price_change()
        logger.info(f"從 Coinglass API 取得 {len(all_symbols_data)} 個幣種的價格數據")
    if not all_symbols_data:
        send_telegram_message("⚠️ 無法取得幣種漲跌資料，請稍後再試。", TG_THREAD_IDS['position_change'])
        return

    # 步驟3：只保留 BingX 名單中的幣種
    target_symbols_data = []
    for coin in all_symbols_data:
        symbol = normalize_symbol(coin)
        if symbol and symbol.upper() in bingx_symbols_upper:
            target_symbols_data.append(coin)
    
    logger.info(f"過濾後剩餘 {len(target_symbols_data)} 個合約幣種")
    
    # 【智慧過濾 Smart Filter - 30m 版】放寬初選以確保足夠候選進入技術分析。
    PRICE_GATEKEEPER = 1.0  # 30m 價格波動門檻 %（>=1% 即進入 OI 檢查）
    OI_THRESHOLD = 1.5      # 30m 持倉異動門檻 %
    active_symbols = []
    for coin in target_symbols_data:
        p_change = extract_price_change_30m(coin)
        if abs(p_change) >= PRICE_GATEKEEPER:
            active_symbols.append(coin)
    logger.info(
        f"🔍 智慧過濾: 從 {len(target_symbols_data)} 個幣種中篩選出 {len(active_symbols)} 個活躍標的 "
        f"(價格 30m >= {PRICE_GATEKEEPER}%) 進行 30m OI 檢查..."
    )
    # 為在 16 分鐘內完成，OI 階段僅處理前 320 個（約 8 分鐘內跑完）
    MAX_OI_SYMBOLS = 320
    target_symbols = active_symbols[:MAX_OI_SYMBOLS]
    if len(active_symbols) > MAX_OI_SYMBOLS:
        logger.info(f"活躍標的共 {len(active_symbols)} 個，本輪僅處理前 {MAX_OI_SYMBOLS} 個以確保準時推播")
    
    long_open = []
    long_close = []
    short_open = []
    short_close = []
    
    processed_count = 0
    oi_success_count = 0
    oi_fail_count = 0
    
    # 並行處理配置：CoinGlass 專用慢速模式，避免瞬間請求過多
    MAX_WORKERS = 4
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
                item = {'symbol': symbol, 'priceChange30m': price_change, 'oiChange30m': oi_change}
                if abs(oi_change) >= OI_THRESHOLD:
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
    logger.info(f"處理統計: 總共 {processed_count} 個幣種, OI 成功 {oi_success_count} 個, OI 失敗 {oi_fail_count} 個 | 總用時: {total_time/60:.1f} 分鐘")
    logger.info(f"分類結果: 多方開倉 {len(long_open)}, 多方平倉 {len(long_close)}, 空方開倉 {len(short_open)}, 空方平倉 {len(short_close)}")
    
    # 排序與取前 3 名
    long_open.sort(key=lambda x: x['oiChange30m'], reverse=True)
    long_close.sort(key=lambda x: x['oiChange30m'])
    short_open.sort(key=lambda x: x['oiChange30m'], reverse=True)
    short_close.sort(key=lambda x: x['oiChange30m'])
    
    top_long_open = long_open[:3]
    top_long_close = long_close[:3]
    top_short_open = short_open[:3]
    top_short_close = short_close[:3]

    # 對 top 標的取 RSI/布林帶（先休息 2 秒再打 CoinGlass，降低 429 機率）
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
        signal_label, zone, stars, rsi_desc, reason = _classify_signal_and_tier(item, cat, tech, funding_rate)
        all_top.append({
            **item,
            "category": cat,
            "current_price": tech.get("current_price") if tech else None,
            "signal_label": signal_label,
            "zone": zone,
            "stars": stars,
            "rsi_desc": rsi_desc,
            "reason": reason,
            "funding_rate": funding_rate,
        })
    msg = build_report_message_tiered(all_top, processed_count, oi_success_count)
    send_telegram_message(msg, TG_THREAD_IDS['position_change'], parse_mode="Markdown")
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
    """抓取並分析長線指標，組成 Telegram Markdown 推播內容"""
    ahr = fetch_ahr999_index()
    rainbow_zone = fetch_rainbow_zone()
    pi_trigger = fetch_pi_cycle_signal()
    fg = fetch_latest_fear_greed()

    if ahr is None and fg is None and not rainbow_zone:
        logger.error("長線指標資料皆取得失敗，放棄推播")
        return None

    # Ahr999 區間判斷
    ahr_status = "未知"
    ahr_state = "資料不足"
    if ahr is not None:
        if ahr < 0.45:
            ahr_status = "特價抄底期"
            ahr_state = "抄底中"
        elif ahr <= 1.2:
            ahr_status = "定投區"
            ahr_state = "定投中"
        else:
            ahr_status = "高估區"
            ahr_state = "謹慎觀望"

    # 恐懼貪婪
    fg_mood = _classify_fear_greed(fg)

    # 彩虹圖中文說明
    rainbow_desc = _interpret_rainbow_zone(rainbow_zone)

    # 泡沫風險判斷：恐懼貪婪 > 80 且 Pi 觸發
    bubble_risk = bool(fg is not None and fg > 80 and pi_trigger)

    # 風險提示 / 船長建議
    risk_text = "資料不足，暫無法評估風險。"
    advice_text = "請先確認指標資料是否正常取得，再做決策。"

    if ahr is not None:
        if ahr < 0.45:
            risk_text = "目前長線風險偏低，屬於「特價抄底期」，但仍需分批布局、嚴守風險。"
            advice_text = "這裡屬於長線黃金區間，可以考慮分批逢低佈局，比特幣為主、山寨為輔。"
        elif ahr <= 1.2:
            risk_text = "目前估值合理偏便宜，「適合定投」區間，風險與報酬相對均衡。"
            advice_text = "建議啟動/維持固定週期定投策略，不為短期波動情緒化。"
        else:
            risk_text = "目前估值偏貴，屬於高估區，若再疊加情緒過熱，需謹慎面對回撤風險。"
            advice_text = "不建議重倉追高，可考慮只小額試單，或等待更友善的估值再進場。"

    # 疊加情緒與 Pi 頂部信號調整建議
    if fg is not None:
        if fg <= 20:
            risk_text += " 另外，市場處於「極度恐懼」，短線可能還有殺價，但長線通常是機會大於風險。"
        elif fg >= 80:
            risk_text += " 同時，市場處於「極度貪婪」，資金情緒過熱，追高風險極大。"

    if bubble_risk:
        risk_text = "⚠️ 市場進入「泡沫風險期」：情緒極度貪婪且 Pi 循環頂部指標觸發，需嚴防大幅回調。"
        advice_text = "建議逐步減倉、鎖定獲利，避免高槓桿追高；保留現金與穩定幣，等待更好的風險回報區間。"
    elif pi_trigger:
        risk_text += " 另外，Pi 循環頂部指標已觸發，歷史上常對應中長期高位區。"
        advice_text = "可以考慮調降整體倉位，將高風險山寨幣逐步換回主流或穩定幣。"

    now_str = format_datetime(get_taipei_time())

    msg_lines = []
    msg_lines.append("📊 *【牛熊導航儀】*")
    msg_lines.append("━━━━━━━━━━━━━━━━━━━━")
    msg_lines.append("")

    # 市場情緒（白話）
    if fg is not None:
        msg_lines.append(f"🌡️ *市場情緒*：{fg_mood}（{fg}分）")
    else:
        msg_lines.append("🌡️ *市場情緒*：資料暫缺")

    # Ahr999（白話）
    if ahr is not None:
        msg_lines.append(f"💰 *Ahr999*：{ahr_status}")
    else:
        msg_lines.append("💰 *Ahr999*：資料暫缺")

    # 彩虹圖（白話）
    msg_lines.append(f"🌈 *彩虹圖*：{rainbow_desc}")

    # 今天操作方向建議（新增）
    msg_lines.append("")
    msg_lines.append("🎯 *今天操作方向建議*：")
    
    # 根據指標綜合判斷操作方向（做多=看漲買入，做空=看跌賣出）
    if ahr is not None and fg is not None:
        if ahr < 0.45 and fg < 30:
            msg_lines.append("✅ 建議：分批做多（看漲、買入），適合抄底")
        elif ahr < 1.2 and fg < 60:
            msg_lines.append("✅ 建議：可以考慮做多（看漲），但需謹慎")
        elif ahr > 1.2 and fg > 70:
            msg_lines.append("⚠️ 建議：謹慎做空（看跌、賣出），注意風險")
        elif pi_trigger and fg > 75:
            msg_lines.append("⚠️ 建議：減倉觀望，等待回調")
        else:
            msg_lines.append("➡️ 建議：保持觀望，等待明確信號")
    elif ahr is not None:
        if ahr < 0.45:
            msg_lines.append("✅ 建議：可以考慮做多（看漲）")
        elif ahr > 1.2:
            msg_lines.append("⚠️ 建議：謹慎做空（看跌）")
        else:
            msg_lines.append("➡️ 建議：保持觀望")
    else:
        msg_lines.append("➡️ 建議：資料不足，保持觀望")

    # 簡化的風險提示
    msg_lines.append("")
    msg_lines.append(f"🚨 *風險提示*：{risk_text}")

    # 簡化的船長建議
    msg_lines.append("")
    msg_lines.append(f"💡 *操作建議*：{advice_text}")
    msg_lines.append("")
    msg_lines.append(f"⏰ 更新時間：{now_str}")

    return "\n".join(msg_lines)


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
    """只執行一次長線指標分析與推播（適合排程觸發）"""
    logger.info("執行單次長線指標推播...")
    message = build_long_term_message()
    if not message:
        logger.warning("本次長線指標分析失敗，未發送推播")
        return
    thread_id = TG_THREAD_IDS.get("long_term_index", 248)
    send_telegram_message(message, thread_id, parse_mode="Markdown")


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
    """將多個清算事件整理成一則 Telegram 推播文字（只顯示過去1小時數據，白話+操作建議）"""
    now = get_taipei_time()
    time_str = now.strftime("%Y-%m-%d %H:%M:%S")

    lines: List[str] = []
    lines.append("🎯 *【清算爆倉雷達】*")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📊 本次監控共有 *{len(events)}* 個幣種達到極端爆倉門檻\n")

    # 依1小時總量排序
    events_sorted = sorted(events, key=lambda e: e.get("totalVolUsd1h", 0), reverse=True)

    for ev in events_sorted:
        total_1h = ev.get("totalVolUsd1h", 0.0) / 10_000
        amount_1h = ev["dominantAmount1h"] / 10_000
        dominant_side = ev['dominantSide']

        lines.append(f"🥊 *【{ev['symbol']}】*")
        lines.append(f"⚠️ 過去1小時內約有 *${amount_1h:.2f} 萬* 美元的 *{dominant_side}* 被強制平倉。\n")
        
        # 操作建議（白話，做多=看漲、做空=看跌）
        if "多單" in dominant_side or dominant_side.startswith("多"):
            lines.append("💡 *操作建議*：大量多單（做多倉位）被爆倉，代表價格下跌壓力大。")
            lines.append("   • 價格還在跌：可考慮做空（看跌）摸頭，但要設好止損")
            lines.append("   • 價格已跌很多：可考慮做多（看漲）摸底，但要分批進場")
        else:  # 空單
            lines.append("💡 *操作建議*：大量空單（做空倉位）被爆倉，代表價格上漲動能強。")
            lines.append("   • 價格還在漲：可考慮做空（看跌）摸頭，但要設好止損")
            lines.append("   • 價格已漲很多：可考慮做多（看漲）摸底，但要分批進場")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"⏰ 更新時間：{time_str}")

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
    send_telegram_message(msg, thread_id, parse_mode="Markdown")

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
    """組合山寨爆發雷達訊息（不依賴 pandas，加入 CVD 背離判斷）"""
    index_val = fetch_altseason_index()
    rsi_list = fetch_rsi_list()
    if not rsi_list:
        logger.error("無法取得 RSI 列表，放棄推播")
        return None

    # 只看成交額前 50 大，避免垃圾幣
    rsi_with_vol = [r for r in rsi_list if r.get("volume") is not None]
    if rsi_with_vol:
        rsi_with_vol.sort(key=lambda x: x.get("volume") or 0, reverse=True)
        rsi_list = rsi_with_vol[:50] + [r for r in rsi_list if r.get("volume") is None]

    # 標準化 RSI：優先使用 4h，沒有才用 1h
    for item in rsi_list:
        rsi_base = item.get("rsi_4h")
        if rsi_base is None:
            rsi_base = item.get("rsi_1h")
        item["rsi_base"] = rsi_base

    # 過濾掉沒有 RSI 的項目
    rsi_list = [r for r in rsi_list if r.get("rsi_base") is not None]

    # 強勢突破（做多）：RSI >= 70
    strong_list = [r for r in rsi_list if r.get("rsi_base", 0) >= 70]
    # 超賣反彈（做多）：RSI <= 30
    oversold_list = [r for r in rsi_list if r.get("rsi_base", 100) <= 30]
    # 超買回調（做空）：RSI >= 70（與強勢突破相同，但買入比條件不同）
    overbought_list = [r for r in rsi_list if r.get("rsi_base", 0) >= 70]

    # 加入 Buy Ratio 過濾
    def attach_buy_ratio(items: List[Dict]) -> List[Dict]:
        result = []
        for item in items:
            sym = item.get("symbol", "")
            base = sym.replace("USDT", "")
            ratio = fetch_buy_ratio(base)
            if ratio is None:
                ratio = fetch_buy_ratio(sym)
            item["buy_ratio"] = ratio
            if ratio is not None:
                result.append(item)
            time.sleep(0.8)
        return result

    # 強勢突破（做多）：買入比 >= 55%
    if strong_list:
        strong_list = attach_buy_ratio(strong_list)
        strong_list = [r for r in strong_list if r.get("buy_ratio", 0) >= 55.0]
        strong_list.sort(key=lambda x: (x.get("rsi_base", 0), x.get("buy_ratio", 0)), reverse=True)
        strong_list = strong_list[:5]

    # 超賣反彈（做多）：買入比 >= 52%
    if oversold_list:
        oversold_list = attach_buy_ratio(oversold_list)
        oversold_list = [r for r in oversold_list if r.get("buy_ratio", 0) >= 52.0]
        oversold_list.sort(key=lambda x: (x.get("rsi_base", 100), -x.get("buy_ratio", 0)))
        oversold_list = oversold_list[:5]

    # 超買回調（做空）：RSI >= 70 且買入比 < 45%（買盤力道不足，可能回調）
    if overbought_list:
        overbought_list = attach_buy_ratio(overbought_list)
        overbought_list = [r for r in overbought_list if r.get("buy_ratio") is not None and r.get("buy_ratio", 0) < 45.0]
        overbought_list.sort(key=lambda x: (x.get("rsi_base", 0), x.get("buy_ratio", 0) or 0), reverse=True)
        overbought_list = overbought_list[:5]

    now_str = format_datetime(get_taipei_time())

    lines: List[str] = []
    lines.append("🛰️ *【區塊鏈船長 - 山寨爆發雷達】*")
    lines.append("━━━━━━━━━━━━━━━━━━━━")

    # 山寨季指數
    if index_val is not None:
        season = "山寨季" if index_val > 50 else "比特幣季"
        lines.append(f"📅 *當前週期*：{season}")
        lines.append(f"📈 *山寨季指數*：{index_val:.2f}（0-100）")
    else:
        lines.append("📅 *當前週期*：資料暫缺")
        lines.append("📈 *山寨季指數*：暫無法取得")

    lines.append("")
    lines.append(describe_altseason(index_val))
    lines.append("")

    # 強勢突破區（做多 = 看漲、買入）
    lines.append("🔥 *潛力領頭羊（強勢突破 - 做多／看漲）*：")
    if not strong_list:
        lines.append("目前沒有符合條件的強勢突破山寨幣。")
    else:
        for idx, item in enumerate(strong_list, 1):
            s = str(item.get("symbol", ""))
            rsi_v = float(item.get("rsi_base", 0) or 0)
            br_val = item.get("buy_ratio")
            br = float(br_val) if br_val is not None else 0.0
            
            lines.append(f"{idx}. `{s}` - RSI: *{rsi_v:.1f}* ｜ 買入比: *{br:.1f}%*")
            
            # 避免請求過於頻繁
            if idx < len(strong_list):
                time.sleep(0.5)
    lines.append("")
    
    # 超買回調區（做空 = 看跌、賣出）
    lines.append("⚠️ *超買回調風險（做空參考／看跌）*：")
    if not overbought_list:
        lines.append("目前沒有明顯的超買回調候選。")
    else:
        for idx, item in enumerate(overbought_list, 1):
            s = str(item.get("symbol", ""))
            rsi_v = float(item.get("rsi_base", 0) or 0)
            br_val = item.get("buy_ratio")
            br = float(br_val) if br_val is not None else 0.0
            
            lines.append(f"{idx}. `{s}` - RSI: *{rsi_v:.1f}* ｜ 買入比: *{br:.1f}%*")
            
            # 避免請求過於頻繁
            if idx < len(overbought_list):
                time.sleep(0.5)
    lines.append("")
    
    # 超賣反彈區（做多 = 看漲、抄底）
    lines.append("💎 *超賣反彈機會（抄底參考 - 做多／看漲）*：")
    if not oversold_list:
        lines.append("目前沒有明顯的超賣反彈候選。")
    else:
        for idx, item in enumerate(oversold_list, 1):
            s = str(item.get("symbol", ""))
            rsi_v = float(item.get("rsi_base", 0) or 0)
            br_val = item.get("buy_ratio")
            br = float(br_val) if br_val is not None else 0.0
            
            lines.append(f"{idx}. `{s}` - RSI: *{rsi_v:.1f}* ｜ 買入比: *{br:.1f}%*")
            
            # 避免請求過於頻繁
            if idx < len(oversold_list):
                time.sleep(0.5)
    lines.append("")

    # 提示
    lines.append("💡 *船長提示*：")
    if index_val is not None and index_val > 60:
        lines.append("山寨季指數正在抬升，資金開始加速流向小幣，建議重點關注領頭羊二測與放量突破。")
    elif index_val is not None and index_val < 40:
        lines.append("目前仍偏向比特幣季，山寨波動相對受限，建議以主流幣與現貨為主，耐心等待資金輪動。")
    else:
        lines.append("資金尚未明顯偏向任何一方，選擇山寨時更要搭配成交量與買入比率，避免追在假突破上。")

    lines.append("")
    lines.append(f"⏰ 更新時間：{now_str}")

    return "\n".join(lines)


def run_altseason_radar_once():
    """每小時執行一次的山寨爆發雷達主流程"""
    logger.info("開始執行山寨爆發雷達...")
    msg = build_altseason_message()
    if not msg:
        logger.warning("本次山寨爆發雷達未能產生有效訊息")
        return
    thread_id = TG_THREAD_IDS.get("altseason_radar", 0)
    if not thread_id:
        logger.warning("未設定 TG_THREAD_ALTSEASON_RADAR，將發送到預設聊天而非特定話題")
    send_telegram_message(msg, thread_id or int(CHAT_ID or 0), parse_mode="Markdown")
    logger.info("山寨爆發雷達推播完成")


# ==================== 10. Hyperliquid 聰明錢監控 ====================

HYPERLIQUID_SENT_ALERTS_FILE = DATA_DIR / "hyperliquid_sent_alerts.json"
WHALE_ALERT_THRESHOLD = 200_000  # $20萬 USD（放寬門檻，捕捉更多大額交易）
SMART_MONEY_PNL_MIN = 50_000  # $50k USD（放寬）
MONEY_PRINTER_PNL_MIN = 500_000  # $50萬 USD（放寬）


def fetch_hyperliquid_whale_alert() -> List[Dict]:
    """獲取 Hyperliquid 鯨魚提醒（大額交易，改進版：降低門檻並添加調試）"""
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
        
        # 篩選名目價值 >= 門檻的提醒（門檻已降低）
        filtered_alerts = []
        value_stats = []  # 記錄所有數值用於調試
        
        for idx, alert in enumerate(data_list):
            # 嘗試多種可能的欄位名稱（擴展更多可能性）
            value = None
            value_key = None
            
            # 按優先順序嘗試各種字段名稱（優先使用 position_value_usd，這是正確的USD價值）
            possible_keys = [
                'position_value_usd', 'positionValueUsd', 'position_value', 'positionValue',  # 最優先：持倉USD價值
                'notional_value', 'notionalValue', 'notional', 'notional_usd',
                'value', 'value_usd', 'usd_value', 'usdValue',
                'size_usd', 'sizeUSD', 'size',  # size 可能是數量，不是價值
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
            
            # 如果還是找不到，嘗試遍歷所有數值字段（排除明顯不是價值的字段）
            if value is None:
                excluded_keys = ['entry_price', 'liq_price', 'mark_price', 'leverage', 'position_size', 'create_time', 'update_time']
                for key, val in alert.items():
                    if key.lower() in excluded_keys:
                        continue  # 跳過明顯不是價值的字段
                    if isinstance(val, (int, float)) and val > 0:
                        # 可能是數值字段，但需要判斷是否合理（通常交易金額 > 1000）
                        if val >= 1000:
                            value = val
                            value_key = key
                            break
            
            if value is None:
                logger.warning(f"Alert #{idx} 無法找到數值字段，所有字段: {list(alert.keys())}")
                continue
            
            try:
                # 處理字符串格式（可能包含逗號或單位）
                if isinstance(value, str):
                    # 移除逗號、空格、$符號等
                    value_clean = value.replace(',', '').replace('$', '').replace(' ', '').replace('USD', '').replace('usd', '')
                    value_float = float(value_clean)
                else:
                    value_float = float(value)
                
                # 記錄統計信息（前10條）
                if idx < 10:
                    symbol = alert.get('symbol') or alert.get('coin') or alert.get('asset') or '未知'
                    value_stats.append({
                        'symbol': symbol,
                        'key': value_key,
                        'value': value_float,
                        'formatted': f"${value_float/10000:.2f}萬"
                    })
                
                if value_float >= WHALE_ALERT_THRESHOLD:
                    filtered_alerts.append(alert)
                    symbol = alert.get('symbol') or alert.get('coin') or alert.get('asset') or '未知'
                    logger.info(f"✅ 符合門檻的 Alert: {symbol} - ${value_float/10000:.2f}萬 (字段: {value_key})")
                else:
                    if idx < 5:  # 只記錄前5條未達門檻的
                        symbol = alert.get('symbol') or alert.get('coin') or alert.get('asset') or '未知'
                        logger.info(f"❌ 未達門檻: {symbol} - ${value_float/10000:.2f}萬 < ${WHALE_ALERT_THRESHOLD/10000:.2f}萬 (字段: {value_key})")
            except (TypeError, ValueError) as e:
                logger.warning(f"Alert #{idx} 數值解析失敗: 字段={value_key}, 值={value}, 錯誤: {str(e)}")
                continue
        
        # 輸出統計信息
        if value_stats:
            logger.info(f"前10條數據的數值統計:")
            for stat in value_stats:
                logger.info(f"  {stat['symbol']}: {stat['formatted']} (字段: {stat['key']})")
        
        logger.info(f"符合門檻的 Whale Alert: {len(filtered_alerts)} 條（門檻: ${WHALE_ALERT_THRESHOLD/10000:.2f}萬）")
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
    else:
        print("請指定要執行的功能，例如: python jackbot.py sector_ranking")

