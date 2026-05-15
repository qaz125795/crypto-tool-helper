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
import hmac, os, sys, json, threading, logging, shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# filelock：跨 process（Gunicorn multi-worker）安全的檔案鎖
try:
    from filelock import FileLock
    _HAS_FILELOCK = True
except ImportError:
    _HAS_FILELOCK = False
    logger_tmp = logging.getLogger(__name__)
    logger_tmp.warning("[app] filelock 未安裝，fallback 為 threading.Lock（單 worker 模式）")

sys.path.insert(0, str(Path(__file__).parent))
logger = logging.getLogger(__name__)

# ── 背景任務執行緒池 ────────────────────────────────────────────────────────
import atexit as _atexit
_task_executor = ThreadPoolExecutor(max_workers=4)
# 注意：不在此立即 atexit！需在 tracker_hook import 之後統一注冊一個協調關閉函數
# 確保順序：_task_executor 先完成（含最後一批 tracker submit）→ _tracker_executor 再關閉

# ── 執行日誌（記錄每支訊號最近一次執行結果）────────────────────────────────
import datetime as _dt
_exec_log: dict = {}           # task_id → TaskLog dict
_exec_log_lock = threading.Lock()

def _record_exec(task_id: str, status: str, msg: str = ""):
    now = _dt.datetime.now(_dt.timezone(  # 台北時間
        _dt.timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    with _exec_log_lock:
        entry = _exec_log.get(task_id, {"task": task_id, "status": "pending",
                                         "last_run": None, "logs": []})
        entry["status"]   = status
        entry["last_run"] = now
        log_line = f"[{now}] {status}" + (f" - {msg}" if msg else "")
        logs = entry.get("logs") or []
        logs.append(log_line)
        entry["logs"] = logs[-20:]     # 只保留最近 20 筆
        _exec_log[task_id] = entry

# ── 排程設定持久化路徑（volume 掛載，重啟後保留）──────────────────────────
_SCHEDULES_FILE = Path("/app/data/schedules.json")

_SCHEDULES_LOCKFILE = Path(str(_SCHEDULES_FILE) + ".lock")

def _schedules_lock():
    """回傳跨 process 安全的 filelock（或 fallback threading.Lock）。"""
    if _HAS_FILELOCK:
        return FileLock(str(_SCHEDULES_LOCKFILE), timeout=5)
    return threading.Lock()

def _load_saved_schedules() -> dict:
    """讀取排程設定，主檔失敗自動從 .bak 還原。"""
    for path in (_SCHEDULES_FILE, Path(str(_SCHEDULES_FILE) + ".bak")):
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logger.warning("load schedules failed (%s): %s", path.name, e)
    return {}

def _save_schedule_state(task_id: str, cron_str: str = None, enabled: bool = None):
    """跨 process 原子寫入（filelock + .tmp → os.replace + .bak 備份）。"""
    with _schedules_lock():
        data  = _load_saved_schedules()
        entry = data.get(task_id, {})
        if cron_str is not None:
            entry["cron"] = cron_str
        if enabled is not None:
            entry["enabled"] = enabled
        data[task_id] = entry
        try:
            _SCHEDULES_FILE.parent.mkdir(parents=True, exist_ok=True)
            content  = json.dumps(data, ensure_ascii=False, indent=2)
            tmp_file = Path(str(_SCHEDULES_FILE) + ".tmp")
            tmp_file.write_text(content, encoding="utf-8")
            if _SCHEDULES_FILE.exists():
                shutil.copy2(_SCHEDULES_FILE, str(_SCHEDULES_FILE) + ".bak")
            os.replace(tmp_file, _SCHEDULES_FILE)
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
_tracker_hook_module = None
try:
    import tracker_hook as _tracker_hook_module
    _tracker_hook_module.install_hook(_jackbot_module)
except ImportError:
    logger.warning("[app] tracker_hook 未找到，訊號不含品質分析")
except Exception as _hook_err:
    logger.error("[app] tracker_hook 安裝失敗: %s", _hook_err)

# ── 協調關閉（atexit LIFO 修復）────────────────────────────────────────────────
# atexit 是 LIFO：最後 register 的最先執行。
# 正確順序：task_executor 先完成（含最後 tracker submit）→ tracker_executor 再關閉
def _coordinated_shutdown():
    logger.info("[shutdown] 等待 _task_executor 完成...")
    _task_executor.shutdown(wait=True)
    if _tracker_hook_module and hasattr(_tracker_hook_module, "_tracker_executor"):
        logger.info("[shutdown] 等待 _tracker_executor 完成...")
        _tracker_hook_module._tracker_executor.shutdown(wait=True)
    logger.info("[shutdown] 所有 executor 已安全關閉")

_atexit.register(_coordinated_shutdown)

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
                _record_exec(name, "running")
                try:
                    f()
                    _record_exec(name, "success")
                except Exception as exc:
                    logger.error("[scheduler] %s 執行失敗: %s", name, exc)
                    _record_exec(name, "error", str(exc)[:200])
            return _runner

        scheduler.add_job(_make_runner(captured_fn, task_id), trigger,
                          id=task_id, replace_existing=True,
                          max_instances=1, coalesce=True)
        if not enabled:
            scheduler.pause_job(task_id)

    scheduler.start()
    atexit.register(lambda: scheduler.shutdown(wait=False))
    # 初始化 _exec_log：啟動時所有 task 設為 pending，前端不顯示空白
    with _exec_log_lock:
        for _tid in TASK_FUNCTIONS:
            if _tid not in _exec_log:
                _exec_log[_tid] = {"task": _tid, "status": "pending",
                                   "last_run": None, "logs": []}
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

if _app_admin and _app_admin.name not in app.blueprints:
    app.register_blueprint(_app_admin)

# ── /api/schedule（Dashboard 判斷 JackBot 是否在線 + 無排程顯示）──────────────
# 格式：[{id, next_run}]，前端用 Array.isArray() 判斷在線
@app.route("/api/schedule", methods=["GET"])
def api_schedule():
    result = []
    if scheduler:
        for task_id in DEFAULT_CRONS:
            job = scheduler.get_job(task_id)
            next_run = ""
            if job and job.next_run_time:
                try:
                    import datetime as _dt2
                    tw = job.next_run_time.astimezone(
                        _dt2.timezone(_dt2.timedelta(hours=8)))
                    next_run = tw.strftime("%m/%d %H:%M")
                except Exception:
                    next_run = str(job.next_run_time)[:16]
            result.append({"id": task_id, "next_run": next_run})
    return jsonify(result)

# ── /health（不需驗證，供 api-gateway 存活確認）───────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    sched_ok = scheduler is not None and scheduler.running
    return jsonify({
        "status": "ok",
        "scheduler": "running" if sched_ok else "not running",
        "signals": len(TASK_FUNCTIONS),
    }), 200

# ── /api/logs（前端管理介面需要；api-gateway 已有 ADMIN_TOKEN 保護）────────
def _internal_ok() -> bool:
    """接受 CRON_SECRET 或 ADMIN_TOKEN；使用 hmac.compare_digest 防計時攻擊。"""
    cron   = os.environ.get("CRON_SECRET", "").strip()
    admin  = os.environ.get("ADMIN_TOKEN", "").strip()
    auth   = request.headers.get("Authorization", "")
    token  = request.args.get("token", "")
    if cron:
        if hmac.compare_digest(auth, f"Bearer {cron}"): return True
        if hmac.compare_digest(token, cron):            return True
    if admin:
        if hmac.compare_digest(auth, f"Bearer {admin}"): return True
        if hmac.compare_digest(token, admin):            return True
    return False

@app.route("/api/logs", methods=["GET"])
def api_logs_all():
    if not _internal_ok():
        return jsonify({"status": "error", "message": "unauthorized"}), 401
    with _exec_log_lock:
        return jsonify(dict(_exec_log))

@app.route("/api/logs/<task_id>", methods=["GET"])
def api_logs_one(task_id):
    if not _internal_ok():
        return jsonify({"status": "error", "message": "unauthorized"}), 401
    with _exec_log_lock:
        entry = _exec_log.get(task_id)
    if not entry:
        return jsonify({"task": task_id, "status": "pending",
                        "last_run": None, "logs": []}), 200
    return jsonify(entry)

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
def run_task(task):
    # Fail-Safe：CRON_SECRET 未設定時回 500，強制部署者明確設定
    if not _internal_ok():
        secret = os.environ.get("CRON_SECRET", "").strip()
        if not secret:
            return jsonify({"status": "error",
                            "message": "CRON_SECRET is not configured"}), 500
        return jsonify({"status": "error", "message": "unauthorized"}), 401
    fn = TASK_FUNCTIONS.get(task)
    if not fn:
        return jsonify({"status": "error", "message": f"未知任務: {task}",
                        "available": list(TASK_FUNCTIONS.keys())}), 400
    return _run_task_bg(fn, task)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
