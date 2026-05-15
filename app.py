#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
JackBot Flask 入口 — 統一平台版 v3
· APScheduler 管理所有 13 支訊號（前端管理介面可即時修改 + 持久化）
· CRON_SECRET 保護外部 HTTP 觸發端點
· /health 不需驗證（供 api-gateway 存活確認）
"""

from functools import wraps
from flask import Flask, request, jsonify
import os, sys, json, threading, logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
logger = logging.getLogger(__name__)

# ── 背景任務執行緒池 ────────────────────────────────────────────────────────
_task_executor = ThreadPoolExecutor(max_workers=4)

# ── 排程設定持久化路徑（volume 掛載，重啟後保留）──────────────────────────
_SCHEDULES_FILE = Path("/app/data/schedules.json")

def _load_saved_schedules() -> dict:
    try:
        if _SCHEDULES_FILE.exists():
            return json.loads(_SCHEDULES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("load schedules failed: %s", e)
    return {}

def _save_schedule_state(task_id: str, cron_str: str = None, enabled: bool = None):
    """將排程設定寫入持久化檔（cron 和 enabled 狀態各自可選更新）"""
    data = _load_saved_schedules()
    entry = data.get(task_id, {})
    if cron_str is not None:
        entry["cron"] = cron_str
    if enabled is not None:
        entry["enabled"] = enabled
    data[task_id] = entry
    try:
        _SCHEDULES_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SCHEDULES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error("save schedules failed: %s", e)

# ── 認證 ─────────────────────────────────────────────────────────────────────
def _cron_secret_ok() -> bool:
    secret = os.environ.get("CRON_SECRET", "").strip()
    if not secret:
        return False   # Fail-Safe：未設定則拒絕
    auth = request.headers.get("Authorization", "")
    if auth == f"Bearer {secret}":
        return True
    if request.args.get("token") == secret:
        return True
    return False

def require_cron_secret(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not _cron_secret_ok():
            return jsonify({"status": "error", "message": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapped

def _run_task_bg(fn, task_name: str):
    """背景執行任務，立即回傳 202 避免 worker 阻塞"""
    def _wrapper():
        try:
            fn()
        except Exception as e:
            logger.error("[bg-task] %s 失敗: %s", task_name, e)
    _task_executor.submit(_wrapper)
    return jsonify({"status": "accepted", "signal": task_name, "message": "任務已在背景啟動"}), 202

# ── 匯入 jackbot 模組 ─────────────────────────────────────────────────────────
import jackbot as _jackbot_module
from jackbot import (
    fetch_sector_ranking,
    fetch_whale_position,
    buying_power_monitor,
    fetch_position_change,
    fetch_and_push_economic_data,
    send_today_preview,
    fetch_all_news,
    fetch_funding_fortune_list,
    run_long_term_once,
    run_liquidity_radar_once,
    run_altseason_radar_once,
    run_crit_radar_once,
    run_hyperliquid_monitor_once,
    run_gold_signal,
    run_position_screener_board_once,
)

# ── 安裝 tracker_hook ─────────────────────────────────────────────────────────
try:
    import tracker_hook as _tracker_hook
    _tracker_hook.install_hook(_jackbot_module)
except ImportError:
    logger.warning("[app] tracker_hook 未找到，訊號不含品質分析")
except Exception as _hook_err:
    logger.error("[app] tracker_hook 安裝失敗: %s", _hook_err)

# ── 任務對應表 ────────────────────────────────────────────────────────────────
TASK_FUNCTIONS = {
    "crit_radar":           run_crit_radar_once,
    "position_change":      fetch_position_change,
    "hyperliquid":          run_hyperliquid_monitor_once,
    "screener_board":       run_position_screener_board_once,
    "funding_rate":         fetch_funding_fortune_list,
    "buying_power_monitor": buying_power_monitor,
    "liquidity_radar":      run_liquidity_radar_once,
    "altseason_radar":      run_altseason_radar_once,
    "gold_signal":          run_gold_signal,
    "news":                 fetch_all_news,
    "economic_data":        fetch_and_push_economic_data,
    "economic_data_preview":send_today_preview,
    "sector_ranking":       fetch_sector_ranking,
    "long_term_index":      run_long_term_once,
}

DEFAULT_CRONS = {
    "crit_radar":            "*/15 * * * *",
    "position_change":       "*/30 * * * *",
    "hyperliquid":           "5,35 * * * *",
    "screener_board":        "25 * * * *",
    "funding_rate":          "55 0,4,8,12,16,20 * * *",
    "buying_power_monitor":  "20 * * * *",
    "liquidity_radar":       "50 * * * *",
    "altseason_radar":       "35 * * * *",
    "gold_signal":           "0 * * * *",
    "news":                  "*/5 * * * *",
    "economic_data":         "3,13,23,33,43,53 * * * *",
    "economic_data_preview": "10 0 * * *",
    "sector_ranking":        "0 */4 * * *",
    "long_term_index":       "0 1 * * *",
}

# ── APScheduler ───────────────────────────────────────────────────────────────
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    import atexit

    scheduler = BackgroundScheduler(timezone="Asia/Taipei", daemon=True)

    saved_schedules = _load_saved_schedules()

    for task_id, fn in TASK_FUNCTIONS.items():
        saved = saved_schedules.get(task_id, {})
        cron_str = saved.get("cron") or DEFAULT_CRONS.get(task_id, "0 */4 * * *")
        enabled  = saved.get("enabled", True)
        parts    = cron_str.split()
        if len(parts) == 5:
            trigger = CronTrigger(
                minute=parts[0], hour=parts[1], day=parts[2],
                month=parts[3], day_of_week=parts[4],
                timezone="Asia/Taipei",
            )
        else:
            trigger = CronTrigger.from_crontab(DEFAULT_CRONS[task_id], timezone="Asia/Taipei")

        # 包裝成背景執行（避免長任務阻塞排程器執行緒）
        captured_fn   = fn
        captured_name = task_id
        def _make_runner(f, name):
            def _runner():
                try:
                    f()
                except Exception as exc:
                    logger.error("[scheduler] %s 執行失敗: %s", name, exc)
            return _runner

        scheduler.add_job(_make_runner(captured_fn, task_id), trigger,
                          id=task_id, replace_existing=True,
                          max_instances=1, coalesce=True)
        if not enabled:
            scheduler.pause_job(task_id)

    scheduler.start()
    atexit.register(lambda: scheduler.shutdown(wait=False))
    logger.info("[APScheduler] 啟動完成，共 %d 個訊號任務", len(TASK_FUNCTIONS))

except ImportError:
    scheduler = None
    logger.warning("[APScheduler] 未安裝，排程管理介面不可用（pip install apscheduler）")

# ── 匯入 admin_api 並注入 scheduler ──────────────────────────────────────────
try:
    from admin_api import admin_bp, _register_routes as _admin_register
    _admin_register(scheduler, _save_schedule_state)
    _app_admin = admin_bp
except Exception as _adm_err:
    logger.warning("[app] admin_api 載入失敗（管理介面不可用）: %s", _adm_err)
    _app_admin = None

# ── Flask ─────────────────────────────────────────────────────────────────────
app = Flask(__name__)

if _app_admin:
    app.register_blueprint(_app_admin)

# ── /health（不需驗證，供 api-gateway 存活確認）───────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    sched_ok = scheduler is not None and scheduler.running
    return jsonify({
        "status": "ok",
        "scheduler": "running" if sched_ok else "not running",
        "signals": len(TASK_FUNCTIONS),
    }), 200

# ── / 健康檢查 ────────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health_check():
    return jsonify({
        "status": "ok",
        "message": "區塊鏈船長：自動化推播系統運行中",
        "signals": list(TASK_FUNCTIONS.keys()),
    }), 200

# ── 外部 HTTP 觸發端點（需 CRON_SECRET，供 cron / 手動觸發）─────────────────

@app.route("/position_change", methods=["GET", "POST"])
@require_cron_secret
def ep_position_change():
    return _run_task_bg(fetch_position_change, "position_change")

@app.route("/crit_radar", methods=["GET", "POST"])
@require_cron_secret
def ep_crit_radar():
    return _run_task_bg(run_crit_radar_once, "crit_radar")

@app.route("/gold_signal", methods=["GET", "POST"])
@require_cron_secret
def ep_gold_signal():
    return _run_task_bg(run_gold_signal, "gold_signal")

@app.route("/screener_board", methods=["GET", "POST"])
@require_cron_secret
def ep_screener_board():
    return _run_task_bg(run_position_screener_board_once, "screener_board")

@app.route("/sector_ranking", methods=["GET", "POST"])
@require_cron_secret
def ep_sector_ranking():
    return _run_task_bg(fetch_sector_ranking, "sector_ranking")

@app.route("/funding_rate", methods=["GET", "POST"])
@require_cron_secret
def ep_funding_rate():
    return _run_task_bg(fetch_funding_fortune_list, "funding_rate")

@app.route("/long_term_index", methods=["GET", "POST"])
@require_cron_secret
def ep_long_term_index():
    return _run_task_bg(run_long_term_once, "long_term_index")

@app.route("/buying_power_monitor", methods=["GET", "POST"])
@require_cron_secret
def ep_buying_power_monitor():
    return _run_task_bg(buying_power_monitor, "buying_power_monitor")

@app.route("/whale_position", methods=["GET", "POST"])
@require_cron_secret
def ep_whale_position():
    return _run_task_bg(buying_power_monitor, "buying_power_monitor")

@app.route("/economic_data", methods=["GET", "POST"])
@require_cron_secret
def ep_economic_data():
    return _run_task_bg(fetch_and_push_economic_data, "economic_data")

@app.route("/economic_data_preview", methods=["GET", "POST"])
@require_cron_secret
def ep_economic_data_preview():
    return _run_task_bg(send_today_preview, "economic_data_preview")

@app.route("/liquidity_radar", methods=["GET", "POST"])
@require_cron_secret
def ep_liquidity_radar():
    return _run_task_bg(run_liquidity_radar_once, "liquidity_radar")

@app.route("/hyperliquid", methods=["GET", "POST"])
@require_cron_secret
def ep_hyperliquid():
    return _run_task_bg(run_hyperliquid_monitor_once, "hyperliquid")

@app.route("/altseason_radar", methods=["GET", "POST"])
@require_cron_secret
def ep_altseason_radar():
    return _run_task_bg(run_altseason_radar_once, "altseason_radar")

@app.route("/news", methods=["GET", "POST"])
@require_cron_secret
def ep_news():
    return _run_task_bg(fetch_all_news, "news")

@app.route("/run/<task>", methods=["GET", "POST"])
@require_cron_secret
def run_task(task):
    fn = TASK_FUNCTIONS.get(task)
    if not fn:
        return jsonify({"status": "error", "message": f"未知任務: {task}",
                        "available": list(TASK_FUNCTIONS.keys())}), 400
    return _run_task_bg(fn, task)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
