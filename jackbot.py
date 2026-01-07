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
            'long_term_index': 248
        }
else:
    TG_THREAD_IDS = {
        'sector_ranking': int(os.environ.get('TG_THREAD_SECTOR_RANKING', 5)),
        'whale_position': int(os.environ.get('TG_THREAD_WHALE_POSITION', 246)),
        'position_change': int(os.environ.get('TG_THREAD_POSITION_CHANGE', 250)),
        'economic_data': int(os.environ.get('TG_THREAD_ECONOMIC_DATA', 13)),
        'news': int(os.environ.get('TG_THREAD_NEWS', 7)),
        'funding_rate': int(os.environ.get('TG_THREAD_FUNDING_RATE', 244)),
        'long_term_index': int(os.environ.get('TG_THREAD_LONG_TERM_INDEX', 248))
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
    """整合執行函數：同時抓取所有來源的新聞"""
    # 抓取 Tree of Alpha 新聞
    fetch_tree_news()
    
    # 抓取 CoinGlass 新聞（如果遇到速率限制會優雅處理）
    fetch_coinglass_articles()
    
    # 抓取 CoinGlass 快訊（如果遇到速率限制會優雅處理）
    fetch_coinglass_newsflash()
    
    logger.info("所有新聞來源抓取完成")


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


def fetch_rainbow_zone() -> Optional[str]:
    """取得比特幣彩虹圖當前區間描述"""
    result = _coinglass_get("/api/index/bitcoin/rainbow-chart")
    point = _get_latest_from_data(result) if result else None
    if not point:
        return None
    # 確保 point 是 dict
    if not isinstance(point, dict):
        logger.warning(f"彩虹圖資料格式錯誤，預期 dict 但得到 {type(point)}: {point}")
        return None
    # 嘗試多種欄位作為「所在區間」名稱
    for key in ("currentZone", "current_zone", "currentBand", "current_band", "zone", "label", "level"):
        name = point.get(key)
        if isinstance(name, str) and name.strip():
            return name.strip()
    logger.warning(f"彩虹圖結構未知，原始資料: {point}")
    return None


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
    short_ma = point.get("short_ma") or point.get("shortMA") or point.get("fast_ma")
    long_ma = point.get("long_ma") or point.get("longMA") or point.get("slow_ma")
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
    # 確保 point 是 dict
    if not isinstance(point, dict):
        logger.warning(f"恐懼與貪婪指數資料格式錯誤，預期 dict 但得到 {type(point)}: {point}")
        return None
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
        msg_lines.append(f"🌡️ *當前市場情緒*：{fg_mood}（指數 {fg}）")
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
    else:
        print("請指定要執行的功能，例如: python jackbot.py sector_ranking")

