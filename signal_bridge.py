"""
signal_bridge.py
JackBot 訊號橋接器 - 雙路徑：
  1. 轉發到 signal-tracker（追蹤、TP/SL、推播帶編號）
  2. 仍轉發到 gate-quant（保留兼容性）
內建指數退避重試（最多 3 次），避免因量化引擎短暫重啟而永久遺失訊號。
"""
import os
import time
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

TRACKER_URL = os.environ.get("SIGNAL_TRACKER_URL", "http://signal-tracker:8004")
GATE_QUANT_URL = os.environ.get(
    "GATE_QUANT_WEBHOOK_URL", "http://gate-quant:8001/webhook/tg-signal"
)
WEBHOOK_TOKEN = os.environ.get("WEBHOOK_TOKEN", "")

DEFAULT_SIGNAL_CHAT = os.environ.get("CHAT_ID", "")
TG_THREAD_MAP = {
    "crit_radar":      int(os.environ.get("TG_THREAD_CRIT_RADAR", "11040") or 11040),
    "position_change": int(os.environ.get("TG_THREAD_POSITION_CHANGE", "250") or 250),
    "gold_signal":     int(os.environ.get("TG_THREAD_GOLD_SIGNAL", "254") or 254),
}

_MAX_RETRIES = 3
_RETRY_BASE_SEC = 1.0  # 指數退避起始秒數：1, 2, 4


def _post_with_retry(url: str, payload: dict, label: str) -> requests.Response | None:
    """帶指數退避的 POST，失敗最多重試 _MAX_RETRIES 次。"""
    for attempt in range(_MAX_RETRIES):
        try:
            resp = requests.post(url, json=payload, timeout=10)
            if resp.status_code == 200:
                return resp
            # 422 通常是 Pydantic schema mismatch（欄位格式不符），重試不會改善
            if resp.status_code == 422:
                logger.error("[bridge] %s HTTP 422 Pydantic 校驗失敗，訊號格式不符（不重試）: %s",
                             label, resp.text[:400])
                return None  # 不重試，直接放棄此次請求
            logger.warning("[bridge] %s HTTP %s (attempt %d/%d): %s",
                           label, resp.status_code, attempt + 1, _MAX_RETRIES, resp.text[:200])
        except requests.exceptions.RequestException as e:
            logger.warning("[bridge] %s 連線失敗 (attempt %d/%d): %s",
                           label, attempt + 1, _MAX_RETRIES, e)
        if attempt < _MAX_RETRIES - 1:
            time.sleep(_RETRY_BASE_SEC * (2 ** attempt))
    logger.error("[bridge] %s 已超過最大重試次數 (%d)，訊號遺失", label, _MAX_RETRIES)
    return None


def forward_signal(signal: dict, source: str) -> bool:
    """
    將訊號送到 signal-tracker（主路徑）並同時轉發到 gate-quant（兼容路徑）。
    signal 格式：
      {
        "symbol": "BTCUSDT",
        "side": "long" | "short",
        "entry": 65000.0,
        "sl": 63000.0,
        "tp": 68000.0,  # 或 tp1, tp2, tp3, tp4
        "tp1": ..., "tp2": ...,
        "leverage": 10,
        "tg_message_id": 12345,
      }
    """
    chat_id = signal.get("tg_chat_id")
    if not chat_id and DEFAULT_SIGNAL_CHAT:
        try:
            chat_id = int(DEFAULT_SIGNAL_CHAT)
        except (TypeError, ValueError):
            chat_id = None

    tracker_payload = {
        "source": source,
        "symbol": signal.get("symbol", ""),
        "side": signal.get("side", "long"),
        "entry_price": float(signal.get("entry") or signal.get("entry_price") or 0),
        "sl_price": _safe_float(signal.get("sl") or signal.get("sl_price")),
        "tp1_price": _safe_float(signal.get("tp1") or signal.get("tp") or signal.get("tp1_price")),
        "tp2_price": _safe_float(signal.get("tp2") or signal.get("tp2_price")),
        "tp3_price": _safe_float(signal.get("tp3") or signal.get("tp3_price")),
        "tp4_price": _safe_float(signal.get("tp4") or signal.get("tp4_price")),
        "leverage": int(signal.get("leverage", 10)),
        "tg_message_id": signal.get("tg_message_id"),
        "tg_chat_id": chat_id,
        "payload": signal,
    }

    # 1. 送到 signal-tracker（帶重試）
    resp = _post_with_retry(f"{TRACKER_URL}/signals", tracker_payload, "tracker")
    if resp:
        data = resp.json()
        logger.info("[tracker] #%s %s %s %s -> %s",
                    data.get("id"), source, signal.get("symbol"),
                    signal.get("side"), data.get("action"))

    # 2. 同時轉發到 gate-quant（帶重試）
    quant_payload = {
        "token": WEBHOOK_TOKEN,
        "source": source,
        "signal": signal,
        "timestamp": datetime.utcnow().isoformat(),
    }
    resp2 = _post_with_retry(GATE_QUANT_URL, quant_payload, "gate-quant")
    if resp2:
        logger.info("[bridge] %s 已轉發 Gate 量化", source)

    return resp is not None or resp2 is not None


def _safe_float(v):
    if v is None or v == "":
        return None
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None
