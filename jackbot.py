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
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import os
from pathlib import Path

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
            'whale_position': 246,
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
        'whale_position': int(os.environ.get('TG_THREAD_WHALE_POSITION', 246)),
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
# 持倉變化篩選：抓取全部 904 個幣種
MAX_SYMBOLS = 904

# 數據存儲目錄
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

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


def format_datetime(dt: datetime) -> str:
    """格式化日期時間"""
    weekdays = ['一', '二', '三', '四', '五', '六', '日']
    weekday = weekdays[dt.weekday()]
    return dt.strftime(f"%Y-%m-%d (週{weekday}) %H:%M")


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
    
    message += "\n🔗 [查看完整即時數據](https://www.coingecko.com/zh-tw/categories#key-stats) \n"
    message += "\n💡 _數據源：CoinGecko API_ \n"
    message += "_由傑克 AI 每小時自動監控資金流向_"
    
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
    """分析數據並判斷市場狀況"""
    global_point = get_latest_data_point(all_data.get('global'))
    global_ratio = global_point.get('global_account_long_short_ratio') if global_point else None
    
    top_account_point = get_latest_data_point(all_data.get('topAccount'))
    top_account_ratio = top_account_point.get('top_account_long_short_ratio') if top_account_point else None
    
    top_position_point = get_latest_data_point(all_data.get('topPosition'))
    top_position_ratio = top_position_point.get('top_position_long_short_ratio') if top_position_point else None
    
    if global_ratio is None and top_position_ratio is None:
        logger.warning("無法提取必要的數據指標")
        return None
    
    diagnosis = "勢力均衡"
    diagnosis_detail = ""
    
    if global_ratio is not None and top_position_ratio is not None:
        if global_ratio > 1.8 and top_position_ratio < 1.0:
            diagnosis = "巨鯨出貨中"
            diagnosis_detail = "散戶瘋狂做多，但巨鯨正在減倉，警惕回調風險"
        elif global_ratio < 0.8 and top_position_ratio > 1.2:
            diagnosis = "巨鯨強勢掃貨"
            diagnosis_detail = "散戶恐慌割肉，巨鯨大舉建倉，可能是底部信號"
        elif top_position_ratio < 1 and global_ratio > 1.5:
            diagnosis = "巨鯨誘多"
            diagnosis_detail = "大戶開空，散戶瘋狂做多，價格可能迎來暴跌"
        elif top_position_ratio > 1 and global_ratio < 0.8:
            diagnosis = "巨鯨抄底"
            diagnosis_detail = "大戶買進，散戶恐慌割肉，可能是抄底機會"
    elif global_ratio is not None:
        diagnosis = "散戶看多" if global_ratio > 1.5 else ("散戶看空" if global_ratio < 0.8 else "勢力均衡")
    elif top_position_ratio is not None:
        diagnosis = "巨鯨看多" if top_position_ratio > 1 else ("巨鯨看空" if top_position_ratio < 1 else "勢力均衡")
    
    return {
        'globalRatio': global_ratio,
        'topAccountRatio': top_account_ratio,
        'topPositionRatio': top_position_ratio,
        'diagnosis': diagnosis,
        'diagnosisDetail': diagnosis_detail
    }


def format_symbol_message(symbol: str, analysis: Dict) -> str:
    """格式化單個幣種的訊息片段"""
    coin_symbol = symbol.replace("USDT", "")
    message = f"\n🐋 【{coin_symbol}】\n"
    message += "━━━━━━━━━━━━━━━━━━━\n"
    
    if analysis.get('globalRatio') is not None:
        message += f"👤 散戶情緒 (全局帳戶比)：{analysis['globalRatio']:.4f}\n"
    
    if analysis.get('topAccountRatio') is not None:
        message += f"📊 大戶帳戶數比：{analysis['topAccountRatio']:.4f}\n"
    
    if analysis.get('topPositionRatio') is not None:
        message += f"🐳 巨鯨部位 (大戶持倉比)：{analysis['topPositionRatio']:.4f}\n"
    
    message += f"\n🚩 深度診斷：{analysis['diagnosis']}\n"
    
    if analysis.get('diagnosisDetail'):
        message += f"📝 {analysis['diagnosisDetail']}\n"
    
    return message


def fetch_whale_position():
    """主執行函數：巨鯨持倉監控"""
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
    
    # 格式化合併訊息
    now = datetime.now()
    time_str = format_datetime(now)
    
    message = "🐋 【巨鯨持倉異動監控】\n"
    message += "━━━━━━━━━━━━━━━━━━━\n"
    
    for i, symbol in enumerate(SYMBOLS):
        if all_analyses[i] is not None:
            message += format_symbol_message(symbol, all_analyses[i])
            if i < len(SYMBOLS) - 1:
                message += "\n"
    
    message += "\n💡 船長提示：\n"
    message += "散戶看多而巨鯨看空時，價格往往會迎來暴跌收割。\n"
    message += "請留意「多空比」與價格的背離現象。\n"
    message += "━━━━━━━━━━━━━━━━━━━\n"
    message += f"⏰ 更新：{time_str}"
    
    send_telegram_message(message, TG_THREAD_IDS['whale_position'])


# ==================== 3. 持倉變化篩選器 ====================

def fetch_coins_price_change() -> List[Dict]:
    """獲取幣種漲跌幅列表"""
    url = f"{CG_API_BASE}/api/futures/coins-price-change"
    headers = {
        "CG-API-KEY": CG_API_KEY,
        "accept": "application/json"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            logger.error(f"coins-price-change error: {response.status_code}")
            return []
        
        result = response.json()
        return result.get('data', result if isinstance(result, list) else [])
    except Exception as e:
        logger.error(f"獲取幣種價格變化失敗: {str(e)}")
        return []


def fetch_oi_change_15m(symbol: str) -> Optional[float]:
    """計算單一 symbol 15 分鐘 OI 變化%"""
    # 直接使用 symbol+USDT 格式，只嘗試 m15 區間（根據實際測試，這樣成功率最高）
    sym = symbol + "USDT"
    url = f"{CG_API_BASE}/api/futures/open-interest/history"
    params = {
        "exchange": EXCHANGE,
        "symbol": sym,
        "interval": "m15"  # 使用 15 分鐘區間
    }
    headers = {
        "CG-API-KEY": CG_API_KEY,
        "accept": "application/json"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        if response.status_code != 200:
            # 只對前幾個幣種記錄錯誤，避免日誌過多
            if symbol in ["BTC", "ETH"]:
                logger.warning(f"[{symbol}] OI API 錯誤: {response.status_code}")
            return None
        
        result = response.json()
        data_list = result.get('data', result.get('list', []))
        
        if not isinstance(data_list, list) or len(data_list) < 2:
            return None
        
        last = data_list[-1]
        prev = data_list[-2]
        
        # 實際欄位名稱：time, open, high, low, close（用 close 代表 OI 數值）
        last_oi = last.get('close') or last.get('open')
        prev_oi = prev.get('close') or prev.get('open')
        
        # 確保轉換為數字（處理字符串情況）
        try:
            last_oi = float(last_oi) if last_oi is not None else None
            prev_oi = float(prev_oi) if prev_oi is not None else None
        except (ValueError, TypeError):
            return None
        
        if not last_oi or not prev_oi or prev_oi == 0:
            return None
        
        change = ((last_oi - prev_oi) / prev_oi) * 100
        return change
    except Exception as e:
        logger.error(f"獲取 {symbol} OI 變化失敗: {str(e)}")
        return None


def normalize_symbol(coin: Dict) -> Optional[str]:
    """從幣種數據中提取 symbol"""
    return coin.get('symbol') or coin.get('pair') or coin.get('name') or coin.get('coin') or coin.get('symbolName')


def extract_price_change_15m(coin: Dict) -> float:
    """提取 15 分鐘價格變化%"""
    # 實際欄位名稱（根據日誌）
    change = coin.get('price_change_percent_15m')
    if isinstance(change, (int, float)):
        return float(change)
    if isinstance(change, str) and change:
        try:
            parsed = float(change)
            if not (parsed != parsed):  # 檢查 NaN
                return parsed
        except ValueError:
            pass
    
    # 備用：其他時間區間
    change = coin.get('price_change_percent_1h')
    if isinstance(change, (int, float)):
        return float(change)
    
    change = coin.get('price_change_percent_24h')
    if isinstance(change, (int, float)):
        return float(change)
    
    return 0.0


def build_report_message(top_long_open: List, top_long_close: List, top_short_open: List, top_short_close: List) -> str:
    """組合推播文字"""
    lines = ["💰 持倉異常偵測報告（最近 15 分鐘）", ""]
    
    def fmt(num):
        if num is None or (isinstance(num, float) and (num != num)):  # NaN check
            return "0.00%"
        return f"{'+' if num >= 0 else ''}{num:.2f}%"
    
    # 多方開倉 TOP 3
    lines.append("📈 多方開倉 TOP 3")
    if not top_long_open:
        lines.append("  無明顯多方開倉標的")
    else:
        for idx, item in enumerate(top_long_open):
            lines.append(
                f"{idx + 1}) {item['symbol']}｜價格 {fmt(item['priceChange15m'])}｜持倉 {fmt(item['oiChange15m'])}"
            )
    lines.append("")
    
    # 多方平倉 TOP 3
    lines.append("📉 多方平倉 TOP 3")
    if not top_long_close:
        lines.append("  無明顯多方平倉標的")
    else:
        for idx, item in enumerate(top_long_close):
            lines.append(
                f"{idx + 1}) {item['symbol']}｜價格 {fmt(item['priceChange15m'])}｜持倉 {fmt(item['oiChange15m'])}"
            )
    lines.append("")
    
    # 空方開倉 TOP 3
    lines.append("📉 空方開倉 TOP 3")
    if not top_short_open:
        lines.append("  無明顯空方開倉標的")
    else:
        for idx, item in enumerate(top_short_open):
            lines.append(
                f"{idx + 1}) {item['symbol']}｜價格 {fmt(item['priceChange15m'])}｜持倉 {fmt(item['oiChange15m'])}"
            )
    lines.append("")
    
    # 空方平倉 TOP 3
    lines.append("📉 空方平倉 TOP 3")
    if not top_short_close:
        lines.append("  無明顯空方平倉標的")
    else:
        for idx, item in enumerate(top_short_close):
            lines.append(
                f"{idx + 1}) {item['symbol']}｜價格 {fmt(item['priceChange15m'])}｜持倉 {fmt(item['oiChange15m'])}"
            )
    
    return "\n".join(lines)


def fetch_position_change():
    """主流程：持倉變化篩選（抓取全部 904 個幣種）"""
    logger.info("開始執行持倉變化篩選，抓取全部 904 個幣種...")
    
    all_symbols_data = fetch_coins_price_change()
    if not all_symbols_data:
        send_telegram_message("⚠️ 無法從 Coinglass 取得幣種漲跌資料，請稍後再試。", TG_THREAD_IDS['position_change'])
        return
    
    logger.info(f"從 Coinglass API 取得 {len(all_symbols_data)} 個幣種，將處理前 {MAX_SYMBOLS} 個")
    
    # 確保抓取全部 904 個幣種
    target_symbols = all_symbols_data[:MAX_SYMBOLS]
    
    long_open = []
    long_close = []
    short_open = []
    short_close = []
    
    processed_count = 0
    oi_success_count = 0
    oi_fail_count = 0
    
    # 每處理 100 個幣種記錄一次進度
    progress_interval = 100
    
    for coin in target_symbols:
        symbol = normalize_symbol(coin)
        if not symbol:
            continue
        
        processed_count += 1
        
        # 進度日誌
        if processed_count % progress_interval == 0:
            logger.info(f"處理進度: {processed_count}/{MAX_SYMBOLS} 個幣種 ({processed_count*100//MAX_SYMBOLS}%)")
        
        price_change_15m = extract_price_change_15m(coin)
        oi_change_15m = fetch_oi_change_15m(symbol)
        
        if oi_change_15m is None:
            oi_fail_count += 1
            continue
        
        oi_success_count += 1
        
        # 4 類分類邏輯
        if price_change_15m > 0:
            # 價格上漲
            if oi_change_15m > 0:
                long_open.append({'symbol': symbol, 'priceChange15m': price_change_15m, 'oiChange15m': oi_change_15m})  # 多方開倉
            elif oi_change_15m < 0:
                long_close.append({'symbol': symbol, 'priceChange15m': price_change_15m, 'oiChange15m': oi_change_15m})  # 多方平倉
        elif price_change_15m < 0:
            # 價格下跌
            if oi_change_15m > 0:
                short_open.append({'symbol': symbol, 'priceChange15m': price_change_15m, 'oiChange15m': oi_change_15m})  # 空方開倉
            elif oi_change_15m < 0:
                short_close.append({'symbol': symbol, 'priceChange15m': price_change_15m, 'oiChange15m': oi_change_15m})  # 空方平倉
    
    logger.info(f"處理統計: 總共 {processed_count} 個幣種, OI 成功 {oi_success_count} 個, OI 失敗 {oi_fail_count} 個")
    logger.info(f"分類結果: 多方開倉 {len(long_open)}, 多方平倉 {len(long_close)}, 空方開倉 {len(short_open)}, 空方平倉 {len(short_close)}")
    
    # 排序與取前 3 名
    long_open.sort(key=lambda x: x['oiChange15m'], reverse=True)      # OI 增加越多越好
    long_close.sort(key=lambda x: x['oiChange15m'])                   # OI 減少越多越好（越負越好）
    short_open.sort(key=lambda x: x['oiChange15m'], reverse=True)     # OI 增加越多越好
    short_close.sort(key=lambda x: x['oiChange15m'])                  # OI 減少越多越好（越負越好）
    
    top_long_open = long_open[:3]
    top_long_close = long_close[:3]
    top_short_open = short_open[:3]
    top_short_close = short_close[:3]
    
    msg = build_report_message(top_long_open, top_long_close, top_short_open, top_short_close)
    send_telegram_message(msg, TG_THREAD_IDS['position_change'], parse_mode="HTML")
    
    logger.info("持倉變化篩選執行完成")


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
            return result.get('data', [])
        else:
            logger.error(f"API 返回錯誤: {result.get('msg')} (錯誤碼: {result.get('code')})")
            return []
    except Exception as e:
        logger.error(f"獲取經濟數據失敗: {str(e)}")
        return []


def filter_important_data(data_array: List[Dict]) -> List[Dict]:
    """過濾重要經濟數據"""
    now = datetime.now()
    one_week_later = now + timedelta(days=7)
    one_day_ago = now - timedelta(days=1)
    
    filtered = []
    for item in data_array:
        importance = item.get('importance_level') or item.get('importance') or 0
        
        # 解析發布時間
        publish_timestamp = item.get('publish_timestamp') or item.get('publish_time')
        if not publish_timestamp:
            continue
        
        if isinstance(publish_timestamp, (int, float)):
            if publish_timestamp > 1e12:  # 毫秒時間戳
                publish_time = datetime.fromtimestamp(publish_timestamp / 1000)
            else:  # 秒時間戳
                publish_time = datetime.fromtimestamp(publish_timestamp)
        else:
            try:
                publish_time = datetime.fromisoformat(str(publish_timestamp).replace('Z', '+00:00'))
            except:
                continue
        
        # 檢查是否已發布
        is_published = item.get('published_value') not in [None, '']
        
        time_valid = one_day_ago <= publish_time <= one_week_later
        
        if importance >= 2 and time_valid and not is_published:
            filtered.append(item)
    
    return filtered


def get_unsent_data(data_array: List[Dict]) -> List[Dict]:
    """獲取尚未推送的數據"""
    sent_ids = load_json_file(SENT_DATA_FILE, [])
    unsent = []
    
    for item in data_array:
        data_id = item.get('id') or item.get('calendar_id') or f"{item.get('calendar_name')}_{item.get('publish_timestamp')}"
        if data_id not in sent_ids:
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


def get_time_until(publish_time: datetime) -> str:
    """計算距離發布時間還有多久"""
    now = datetime.now()
    diff = (publish_time - now).total_seconds()
    
    if diff < 0:
        return '已經發布過了'
    
    days = int(diff // 86400)
    hours = int((diff % 86400) // 3600)
    minutes = int((diff % 3600) // 60)
    
    if days > 7:
        return f"還有 {days} 天"
    elif days > 0:
        if hours > 0:
            return f"還有 {days} 天 {hours} 小時"
        else:
            return f"還有 {days} 天"
    elif hours > 0:
        if minutes > 0:
            return f"還有 {hours} 小時 {minutes} 分鐘"
        else:
            return f"還有 {hours} 小時"
    elif minutes > 0:
        return f"還有 {minutes} 分鐘"
    else:
        return '即將發布'


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


def format_economic_data_message(data: Dict) -> str:
    """格式化經濟數據訊息"""
    publish_timestamp = data.get('publish_timestamp') or data.get('publish_time')
    if isinstance(publish_timestamp, (int, float)):
        if publish_timestamp > 1e12:
            publish_time = datetime.fromtimestamp(publish_timestamp / 1000)
        else:
            publish_time = datetime.fromtimestamp(publish_timestamp)
    else:
        publish_time = datetime.now()
    
    time_str = format_datetime(publish_time)
    time_until = get_time_until(publish_time)
    
    importance_level = data.get('importance_level') or data.get('importance') or 0
    importance_emoji = '🔴' if importance_level >= 3 else '🟡' if importance_level >= 2 else '🟢'
    importance_text = '超高' if importance_level >= 3 else '高' if importance_level >= 2 else '中'
    
    country_flag = get_country_flag(data.get('country_name') or data.get('country') or '')
    effect_emoji = get_effect_emoji(data.get('data_effect') or data.get('effect') or '')
    effect_text = get_effect_text(data.get('data_effect') or data.get('effect') or '')
    
    message = "區塊鏈船長傑克通知您\n\n"
    message += "📊 *重要經濟數據來囉！*\n"
    message += "━━━━━━━━━━━━━━━━━━━━\n\n"
    
    calendar_name = data.get('calendar_name') or data.get('name') or '經濟指標'
    country_name = data.get('country_name') or data.get('country') or '未知國家'
    
    message += f"{importance_emoji} *{calendar_name}*\n"
    message += f"{country_flag} {country_name} 即將發布\n\n"
    
    message += "⏰ *什麼時候發布？*\n"
    message += f"📅 {time_str}\n"
    message += f"⏳ {time_until}就要發布了\n\n" if '還有' in time_until else f"⏳ {time_until}\n\n"
    
    message += "📈 *市場怎麼看？*\n"
    if data.get('forecast_value'):
        message += f"專家預測: *{data['forecast_value']}*\n"
    if data.get('previous_value'):
        message += f"上次結果: {data['previous_value']}\n"
    message += "\n"
    
    message += f"⚡ *重要程度*: {importance_text}重要性\n"
    if effect_text:
        message += f"{effect_emoji} *對市場影響*: {effect_text}\n"
    message += "\n"
    
    if data.get('remark') or data.get('note'):
        message += f"📝 *補充說明*\n{data.get('remark') or data.get('note')}\n\n"
    
    message += "━━━━━━━━━━━━━━━━━━━━\n"
    message += f"🤖 自動推播 | {format_datetime(datetime.now())}"
    
    return message


def fetch_and_push_economic_data():
    """主函數：抓取並推送經濟數據"""
    try:
        economic_data = fetch_economic_data()
        if not economic_data:
            logger.info("沒有新的經濟數據")
            return
        
        logger.info(f"總共獲取 {len(economic_data)} 條經濟數據")
        
        important_data = filter_important_data(economic_data)
        logger.info(f"過濾後的重要數據: {len(important_data)} 條")
        
        if not important_data:
            return
        
        new_data = get_unsent_data(important_data)
        logger.info(f"尚未推送的重要數據: {len(new_data)} 條")
        
        if not new_data:
            return
        
        for data in new_data:
            message = format_economic_data_message(data)
            send_telegram_message(message, TG_THREAD_IDS['economic_data'])
            
            data_id = data.get('id') or data.get('calendar_id') or f"{data.get('calendar_name')}_{data.get('publish_timestamp')}"
            mark_as_sent(data_id)
        
        logger.info(f"成功推送 {len(new_data)} 條重要經濟數據")
        
    except Exception as e:
        logger.error(f"錯誤: {str(e)}")
        send_telegram_message(f"⚠️ *經濟數據抓取錯誤*\n\n{str(e)}", TG_THREAD_IDS['economic_data'])


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
    message += f"🔍 來源：{news.get('source', '')}\n"
    message += f"🔗 [點擊查看原文]({news.get('url', 'https://tree.news')})"
    
    send_telegram_message(message, TG_THREAD_IDS['news'])


def process_and_send_coinglass(item: Dict, type_str: str):
    """翻譯並發送 CoinGlass 新聞/快訊到 Telegram"""
    is_newsflash = type_str == "newsflash"
    emoji = "⚡" if is_newsflash else "📰"
    type_name = "快訊" if is_newsflash else "新聞"
    
    translated_title = translate_text(item.get('title') or item.get('headline') or "")
    translated_content = translate_text(item.get('content') or item.get('description') or "")
    
    message = f"{emoji} *【CoinGlass {type_name}】*\n\n"
    
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
                date = datetime.fromtimestamp(time_val / 1000)
            else:
                date = datetime.fromtimestamp(time_val)
        else:
            date = datetime.now()
        message += f"🕐 時間：{date.strftime('%Y-%m-%d %H:%M:%S')}\n"
    
    if item.get('source'):
        message += f"🔍 來源：{item.get('source')}\n"
    
    if item.get('url') or item.get('link'):
        message += f"🔗 [點擊查看原文]({item.get('url') or item.get('link')})"
    
    send_telegram_message(message, TG_THREAD_IDS['news'])


def fetch_all_news():
    """整合執行函數：只抓取 Tree of Alpha 新聞"""
    # 抓取 Tree of Alpha 新聞
    fetch_tree_news()
    
    logger.info("Tree of Alpha 新聞抓取完成")


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
        
        message += "\n💡 *套利策略*：\n"
        message += "*正費率（+）*：做空永續 + 持有現貨，每 4 小時領取資金費率。\n"
        message += "*負費率（-）*：做多永續 + 賣出現貨，但需注意軋空風險。\n\n"
        message += "📊 數據來源：[幣安U本位](https://www.binance.com/zh-TC/futures/funding-history/perpetual/real-time-funding-rate)\n"
        message += f"⏰ 更新時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
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

    now_str = format_datetime(datetime.now())

    msg_lines = []
    msg_lines.append("📊 *【區塊鏈船長 - 牛熊導航儀】*")
    msg_lines.append("━━━━━━━━━━━━━━━━━━━━")
    msg_lines.append("")

    # 市場情緒
    if fg is not None:
        mood_desc = _describe_fear_greed(fg)
        msg_lines.append(f"🌡️ *當前市場情緒*：{fg_mood}（指數 {fg}）")
        msg_lines.append(f"   {mood_desc}")
    else:
        msg_lines.append("🌡️ *當前市場情緒*：資料暫缺")

    # Ahr999
    if ahr is not None:
        msg_lines.append(f"💰 *Ahr999 指標*：{ahr:.4f}（狀態：{ahr_status}/{ahr_state}）")
    else:
        msg_lines.append("💰 *Ahr999 指標*：資料暫缺")

    # 彩虹圖
    msg_lines.append(f"🌈 *彩虹圖位置*：{rainbow_desc}")

    # 風險提示
    msg_lines.append("")
    msg_lines.append(f"🚨 *風險提示*：{risk_text}")

    # 額外提醒
    alert_parts = []
    if ahr is not None and ahr < 0.45:
        alert_parts.append("🔔 Ahr999 < 0.45：觸發「抄底警報」")
    elif ahr is not None and ahr < 1.2:
        alert_parts.append("📩 Ahr999 < 1.2：處於「適合定投」區間")
    if fg is not None and (fg < 20 or fg > 80):
        alert_parts.append(f"📊 恐懼與貪婪極端區：{fg_mood}（{fg}）")
    if pi_trigger:
        alert_parts.append("⏰ Pi 循環頂部指標：*均線交叉，逃頂預警啟動*")

    if alert_parts:
        msg_lines.append("")
        msg_lines.append("⚡ *警報狀態一覽*：")
        for line in alert_parts:
            msg_lines.append(f"- {line}")

    # 船長建議
    msg_lines.append("")
    msg_lines.append(f"💡 *船長建議*：{advice_text}")
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
    "BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "TRX", "AVAX", "DOT",
    "LINK", "NEAR", "MATIC", "SUI", "APT",
]
LIQ_EXCHANGE_LIST = "Binance"
LIQ_REQUEST_DELAY = 1.2  # 秒


def get_liquidation_threshold(symbol: str, time_window: str = "1h") -> tuple:
    """根據幣種回傳極端爆倉門檻（USD）
    返回 (1h阈值, 24h阈值) 的元組
    """
    if symbol in ("BTC", "ETH"):
        return (2_000_000.0, 15_000_000.0)  # 1h: 200萬, 24h: 1500萬
    if symbol in ("SOL", "XRP", "DOGE"):
        return (800_000.0, 5_000_000.0)  # 1h: 80萬, 24h: 500萬
    return (400_000.0, 3_000_000.0)  # 1h: 40萬, 24h: 300萬


def fetch_liquidation_data(symbol: str) -> Optional[List[Dict]]:
    """從 CoinGlass 抓取單一幣種的清算彙總歷史"""
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

        data_array = data.get("data") or []
        if not isinstance(data_array, list):
            logger.warning(f"{symbol} 清算數據格式異常: {type(data_array)}")
            return None
        return data_array
    except Exception as e:
        logger.error(f"獲取 {symbol} 清算數據時發生異常: {str(e)}")
        return None


def process_liquidation_data(symbol: str, data_array: List[Dict]) -> Optional[Dict]:
    """處理清算數據，判斷是否達到極端爆倉門檻，返回事件描述"""
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

        # 從後往前遍歷，累加最近 24 小時與 1 小時的清算
        for item in reversed(data_array):
            try:
                item_time = int(item.get("time") or 0)
            except (TypeError, ValueError):
                continue

            long_liq = float(item.get("aggregated_long_liquidation_usd") or 0)
            short_liq = float(item.get("aggregated_short_liquidation_usd") or 0)

            if item_time >= twenty_four_hours_ago:
                buy_vol_usd_24h += long_liq
                sell_vol_usd_24h += short_liq

                if item_time >= one_hour_ago:
                    buy_vol_usd_1h += long_liq
                    sell_vol_usd_1h += short_liq
            else:
                break

        # 如果 24h 沒數據，用最新一筆頂上
        if buy_vol_usd_24h == 0 and sell_vol_usd_24h == 0 and data_array:
            latest = data_array[-1]
            buy_vol_usd_24h = float(latest.get("aggregated_long_liquidation_usd") or 0)
            sell_vol_usd_24h = float(latest.get("aggregated_short_liquidation_usd") or 0)
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

        # 改進判斷邏輯：1小時達到門檻 OR 24小時達到門檻（更寬鬆）
        triggered_by_1h = total_vol_usd_1h >= threshold_1h
        triggered_by_24h = total_vol_usd_24h >= threshold_24h
        
        if not (triggered_by_1h or triggered_by_24h):
            logger.debug(
                f"{symbol} 未達門檻 - 1h: {total_vol_usd_1h/10000:.2f}萬 < {threshold_1h/10000:.2f}萬, "
                f"24h: {total_vol_usd_24h/10000:.2f}萬 < {threshold_24h/10000:.2f}萬"
            )
            return None

        # 判斷主導清算方向：如果1小時達標則用1小時，否則用24小時
        if triggered_by_1h:
            is_long_dom = buy_vol_usd_1h > sell_vol_usd_1h
            dominant_side = "多單" if is_long_dom else "空單"
            dominant_amount_1h = buy_vol_usd_1h if is_long_dom else sell_vol_usd_1h
            trigger_reason = "1小時極端爆倉"
        else:
            # 24小時達標但1小時未達標，用24小時數據判斷
            is_long_dom = buy_vol_usd_24h > sell_vol_usd_24h
            dominant_side = "多單" if is_long_dom else "空單"
            # 24小時觸發時，顯示24小時的總量（但標註為24小時累積）
            dominant_amount_1h = buy_vol_usd_24h if is_long_dom else sell_vol_usd_24h
            trigger_reason = "24小時累積爆倉"

        logger.info(
            f"{symbol} ⚠️ 觸發警報 ({trigger_reason}) - 過去1h: ${(buy_vol_usd_1h + sell_vol_usd_1h)/10000:.2f}萬 | "
            f"24h: ${total_vol_usd_24h/10000:.2f}萬"
        )

        return {
            "symbol": symbol,
            "dominantSide": dominant_side,
            "dominantAmount1h": dominant_amount_1h,
            "totalVolUsd24h": total_vol_usd_24h,
            "totalVolUsd1h": total_vol_usd_1h,
            "buyVolUsd24h": buy_vol_usd_24h,
            "sellVolUsd24h": sell_vol_usd_24h,
            "buyVolUsd1h": buy_vol_usd_1h,
            "sellVolUsd1h": sell_vol_usd_1h,
            "triggerReason": trigger_reason,
        }
    except Exception as e:
        logger.error(f"處理 {symbol} 清算數據時發生錯誤: {str(e)}")
        return None


def generate_liq_symbol_analysis(event: Dict) -> str:
    """根據 24h 多空清算對比產出一句分析"""
    is_long_dominant_24h = event.get("buyVolUsd24h", 0) > event.get("sellVolUsd24h", 0)
    if is_long_dominant_24h:
        return "多頭已被大幅清洗，留意技術性反彈與短線抄底機會。"
    return "空頭已被大幅清洗，留意反向回落與高位補跌風險。"


def format_liquidity_consolidated_message(events: List[Dict]) -> str:
    """將多個清算事件整理成一則 Telegram 推播文字"""
    now = datetime.now()
    time_str = now.strftime("%Y-%m-%d %H:%M:%S")

    lines: List[str] = []
    lines.append("🎯 *【巨鯨獵殺告警 - 極端爆倉彙整】*")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📊 本次監控共有 *{len(events)}* 個幣種達到極端爆倉門檻\n")

    # 依觸發總量排序：如果是1小時觸發用1小時數據，如果是24小時觸發用24小時數據
    def get_sort_key(e):
        trigger_reason = e.get("triggerReason", "1小時極端爆倉")
        if trigger_reason == "1小時極端爆倉":
            return e.get("totalVolUsd1h", 0)
        else:
            return e.get("totalVolUsd24h", 0)
    
    events_sorted = sorted(events, key=get_sort_key, reverse=True)

    for ev in events_sorted:
        total_24h = ev["totalVolUsd24h"] / 10_000
        total_1h = ev.get("totalVolUsd1h", 0.0) / 10_000
        amount_1h = ev["dominantAmount1h"] / 10_000
        analysis = generate_liq_symbol_analysis(ev)

        lines.append(f"🥊 *【{ev['symbol']}】*")

        # 顯示觸發原因和清算數據
        trigger_reason = ev.get("triggerReason", "極端爆倉")
        if trigger_reason == "1小時極端爆倉":
            if total_1h < 10:  # 小於 10 萬 USD 視為訊號偏弱
                lines.append(
                    "過去 1 小時內爆倉金額不顯著，主要清算壓力來自較早前的波動。"
                )
            else:
                lines.append(
                    f"🚨 *過去 1 小時內*約有 *${amount_1h:.2f} 萬* 美元的 *{ev['dominantSide']}* 被強制平倉（爆倉）。"
                )
            lines.append(f"過去 24 小時內總清算金額：約 *${total_24h:.2f} 萬* 美元。")
        else:
            # 24小時累積觸發，amount_1h 實際上是 24h 的主導清算量
            lines.append(
                f"⚠️ *過去 24 小時內*累積約有 *${amount_1h:.2f} 萬* 美元的 *{ev['dominantSide']}* 被強制平倉。"
            )
            lines.append(f"其中過去 1 小時內清算：約 *${total_1h:.2f} 萬* 美元。")
        lines.append(f"💡 {analysis}\n")

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


def build_altseason_message() -> Optional[str]:
    """組合山寨爆發雷達訊息（不依賴 pandas）"""
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

    # 強勢突破：RSI >= 70
    strong_list = [r for r in rsi_list if r.get("rsi_base", 0) >= 70]
    # 超賣反彈：RSI <= 30
    oversold_list = [r for r in rsi_list if r.get("rsi_base", 100) <= 30]

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

    # 強勢突破：買入比 >= 55%
    if strong_list:
        strong_list = attach_buy_ratio(strong_list)
        strong_list = [r for r in strong_list if r.get("buy_ratio", 0) >= 55.0]
        strong_list.sort(key=lambda x: (x.get("rsi_base", 0), x.get("buy_ratio", 0)), reverse=True)
        strong_list = strong_list[:5]

    # 超賣反彈：買入比 >= 52%
    if oversold_list:
        oversold_list = attach_buy_ratio(oversold_list)
        oversold_list = [r for r in oversold_list if r.get("buy_ratio", 0) >= 52.0]
        oversold_list.sort(key=lambda x: (x.get("rsi_base", 100), -x.get("buy_ratio", 0)))
        oversold_list = oversold_list[:5]

    now_str = format_datetime(datetime.now())

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

    # 強勢突破區
    lines.append("🔥 *潛力領頭羊（強勢突破）*：")
    if not strong_list:
        lines.append("目前沒有符合條件的強勢突破山寨幣。")
    else:
        for idx, item in enumerate(strong_list, 1):
            s = str(item.get("symbol", ""))
            rsi_v = float(item.get("rsi_base", 0))
            br = float(item.get("buy_ratio", 0))
            lines.append(f"{idx}. `{s}` - RSI: *{rsi_v:.1f}* ｜ 買入比: *{br:.1f}%*")
    lines.append("")

    # 超賣反彈區
    lines.append("💎 *超賣反彈機會（抄底參考）*：")
    if not oversold_list:
        lines.append("目前沒有明顯的超賣反彈候選。")
    else:
        for idx, item in enumerate(oversold_list, 1):
            s = str(item.get("symbol", ""))
            rsi_v = float(item.get("rsi_base", 0))
            br = float(item.get("buy_ratio", 0))
            lines.append(f"{idx}. `{s}` - RSI: *{rsi_v:.1f}* ｜ 買入比: *{br:.1f}%*")
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
WHALE_ALERT_THRESHOLD = 1_000_000  # $1M USD
SMART_MONEY_PNL_MIN = 100_000  # $100k USD
MONEY_PRINTER_PNL_MIN = 1_000_000  # $1M USD


def fetch_hyperliquid_whale_alert() -> List[Dict]:
    """獲取 Hyperliquid 鯨魚提醒（大額交易）"""
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
            return []
        
        # 篩選名目價值 > $1M 的提醒
        filtered_alerts = []
        for alert in data_list:
            # 嘗試多種可能的欄位名稱
            value = (
                alert.get('notional_value') or 
                alert.get('notionalValue') or 
                alert.get('value') or 
                alert.get('size') or 
                alert.get('amount') or
                0
            )
            
            try:
                value_float = float(value)
                if value_float >= WHALE_ALERT_THRESHOLD:
                    filtered_alerts.append(alert)
            except (TypeError, ValueError):
                continue
        
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
    
    # 判斷方向 emoji
    direction_emoji = "🟢" if str(direction).lower() in ['long', 'buy', '多', 'long'] else "🔴"
    direction_text = "大額開多" if str(direction).lower() in ['long', 'buy', '多', 'long'] else "大額開空"
    
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
    
    # 判斷多空方向（白話文中文）
    side_lower = str(side).lower()
    side_text = "做多" if side_lower in ['long', 'buy', '多', 'l'] else "做空"
    
    # 格式化金額顯示
    if size_float >= 1_000_000:
        size_display = f"${size_float/1_000_000:.2f}M"
    elif size_float >= 1_000:
        size_display = f"${size_float/1_000:.2f}K"
    else:
        size_display = f"${size_float:.2f}"
    
    return f"{index}. 地址 `...{address_short}` | 倉位：{size_display} [{symbol} {side_text}] | 槓桿：{leverage:.1f}x"


def build_hyperliquid_message() -> Optional[str]:
    """組合 Hyperliquid 聰明錢監控訊息"""
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
    
    # 2. 獲取 PNL Distribution
    pnl_data = fetch_hyperliquid_pnl_distribution()
    smart_money_info = process_smart_money_pnl(pnl_data) if pnl_data else {}
    
    # 3. 獲取 Whale Position
    whale_positions = fetch_hyperliquid_whale_position()
    logger.info(f"獲取到 {len(whale_positions)} 個鯨魚持倉")
    
    # 如果完全沒有數據，不發送推播（但至少要有 whale positions 或其他信息）
    has_smart_money_info = (
        smart_money_info.get('money_printers') or 
        smart_money_info.get('smart_money') or 
        smart_money_info.get('top_symbols')
    )
    
    if not new_alerts and not has_smart_money_info and not whale_positions:
        logger.info("本次監控無有效數據，跳過推播")
        return None
    
    # 構建訊息
    lines = []
    lines.append("🐳 *【區塊鏈船長 - Hyperliquid 鯨魚追蹤】*")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("")
    
    # Whale Alert 部分
    if new_alerts:
        lines.append("🚨 *巨鯨即時預警 (Whale Alert)*：")
        for alert in new_alerts[:3]:  # 最多顯示 3 個
            lines.append(format_alert_message(alert))
            lines.append("")
        
        # 更新已發送 ID 列表
        sent_alert_ids.extend(new_alert_ids)
        # 只保留最近 500 條
        if len(sent_alert_ids) > 500:
            sent_alert_ids = sent_alert_ids[-500:]
        save_json_file(HYPERLIQUID_SENT_ALERTS_FILE, sent_alert_ids)
    else:
        lines.append("🚨 *巨鯨即時預警 (Whale Alert)*：")
        lines.append("本次監控期間無新的大額交易提醒（> $1M）")
        lines.append("")
    
    # 聰明錢 PNL 分佈部分
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
    
    # 頂級鯨魚倉位部分
    if whale_positions:
        lines.append("📊 *頂級鯨魚倉位 (Top Positions)*：")
        for idx, position in enumerate(whale_positions, 1):
            lines.append(format_whale_position_message(position, idx))
        lines.append("")
    
    # 船長提示
    if new_alerts or smart_money_info.get('top_symbols'):
        top_symbol = list(smart_money_info.get('top_symbols', {}).keys())[0] if smart_money_info.get('top_symbols') else new_alerts[0].get('symbol', '特定標的') if new_alerts else '特定標的'
        lines.append(f"💡 *船長提示*：聰明錢正在關注 {top_symbol}，請注意該幣種的流動性變化！")
        lines.append("")
    
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"⏰ 更新時間：{format_datetime(datetime.now())}")
    
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
        elif function_name == "whale_position":
            fetch_whale_position()
        elif function_name == "position_change":
            fetch_position_change()
        elif function_name == "economic_data":
            fetch_and_push_economic_data()
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
            print("  whale_position   - 巨鯨持倉動向")
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

