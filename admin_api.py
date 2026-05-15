"""
admin_api.py - 訊號管理後台 API（APScheduler 版）
· pause / resume / update_cron → 即時生效 + 持久化（重啟保留）
· update_params → 寫入 .env + 即時 os.environ 更新
"""
from flask import Blueprint, jsonify, request
from datetime import datetime, timezone, timedelta
import os
import json

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")

SIGNAL_NAMES = {
    "crit_radar":           "⚡ 爆擊雷達",
    "position_change":      "🎯 持倉狙擊",
    "liquidity_radar":      "💧 流動性雷達",
    "hyperliquid":          "🐋 鯨魚追蹤",
    "gold_signal":          "🥇 黃金訊號",
    "funding_rate":         "💰 資金費率",
    "news":                 "📰 新聞快訊",
    "economic_data":        "📊 經濟數據",
    "economic_data_preview":"📅 經濟預告",
    "sector_ranking":       "🏆 板塊排行",
    "long_term_index":      "🧭 長線導航",
    "altseason_radar":      "🚀 山寨爆發",
    "buying_power_monitor": "💪 購買力監控",
    "screener_board":       "📋 市場地圖",
}

SIGNAL_PARAMS = {
    "crit_radar": [
        ("CRIT_RADAR_POOL",              "掃描幣種池大小",   "int",   "100"),
        ("CRIT_RADAR_MIN_SCORE",         "最低分數門檻",     "int",   "84"),
        ("CRIT_RADAR_MAX_ALERTS",        "每次最多推播",     "int",   "1"),
        ("CRIT_RADAR_COOLDOWN_HOURS",    "冷卻小時數",       "float", "8"),
        ("CRIT_RADAR_SL_ATR",            "SL ATR 倍數",      "float", "1.5"),
        ("CRIT_RADAR_TP_R",              "TP R 倍率",        "float", "2.0"),
        ("CRIT_RADAR_REQUIRE_1H_CONFIRM","需 1H 確認",       "bool",  "true"),
    ],
    "position_change": [
        ("SNIPER_FAST_MODE",      "短打模式",     "bool",  "false"),
        ("SNIPER_TP1_R",          "TP1 R 倍率",   "float", "1.5"),
        ("SNIPER_TP2_R",          "TP2 R 倍率",   "float", "3.2"),
        ("SNIPER_MIN_SL_PCT",     "最小 SL %",    "float", "0.03"),
        ("SNIPER_COOLDOWN_HOURS", "冷卻小時數",   "float", "3"),
    ],
    "gold_signal": [
        ("GOLD_ORB_MINUTES",       "ORB 分鐘數",        "int",   "60"),
        ("GOLD_ATR_PERIOD",        "ATR 週期",          "int",   "14"),
        ("GOLD_TP_R",              "TP R 倍率",         "float", "2.0"),
        ("GOLD_RSI_LONG_MAX",      "做多 RSI 上限",     "float", "72"),
        ("GOLD_RSI_SHORT_MIN",     "做空 RSI 下限",     "float", "35"),
    ],
    "hyperliquid": [
        ("WHALE_SPOT_MIN_USD",           "現貨最小 USD",    "int", "500000"),
        ("WHALE_HL_MIN_DELTA_USD",       "HL 最小變動",     "int", "100000"),
        ("WHALE_EVENT_COOLDOWN_SECONDS", "事件冷卻秒",      "int", "600"),
        ("WHALE_MAX_MESSAGES_PER_RUN",   "每次最多訊息",    "int", "5"),
        ("WHALE_LOOKBACK_SECONDS",       "回看秒數",        "int", "300"),
    ],
    "liquidity_radar": [
        ("LIQ_CHART_LOOKBACK_DAYS", "回看天數",     "int", "10"),
        ("LIQ_CHART_MAX_SYNC_DAYS", "最大同步天數", "int", "180"),
    ],
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


def _register_routes(scheduler_ref, save_fn=None):
    """
    scheduler_ref : APScheduler BackgroundScheduler（或 None）
    save_fn       : callable(task_id, cron_str=None, enabled=None) 持久化函數
    """

    def _save(task_id, cron_str=None, enabled=None):
        if save_fn:
            try:
                save_fn(task_id, cron_str=cron_str, enabled=enabled)
            except Exception:
                pass

    def _next_run_tw(job) -> str | None:
        if not job or not job.next_run_time:
            return None
        try:
            dt = job.next_run_time
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone(timedelta(hours=8))).strftime("%m/%d %H:%M")
        except Exception:
            return None

    def _format_cron(job) -> str:
        try:
            fields = {f.name: str(f) for f in job.trigger.fields}
            return "{minute} {hour} {day} {month} {day_of_week}".format(
                minute=fields.get("minute", "*"),
                hour=fields.get("hour", "*"),
                day=fields.get("day", "*"),
                month=fields.get("month", "*"),
                day_of_week=fields.get("day_of_week", "*"),
            )
        except Exception:
            return ""

    @admin_bp.route("/jobs", methods=["GET"])
    def list_jobs():
        result = []
        for task_id in DEFAULT_CRONS:
            job  = scheduler_ref.get_job(task_id) if scheduler_ref else None
            enabled = bool(job and job.next_run_time is not None)
            result.append({
                "id":         task_id,
                "name":       SIGNAL_NAMES.get(task_id, task_id),
                "enabled":    enabled,
                "cron":       _format_cron(job) if job else DEFAULT_CRONS.get(task_id, ""),
                "next_run_tw": _next_run_tw(job),
                "has_params": task_id in SIGNAL_PARAMS,
            })
        return jsonify(result)

    @admin_bp.route("/jobs/<task_id>/pause", methods=["POST"])
    def pause_job(task_id):
        if not scheduler_ref:
            return jsonify({"ok": False, "error": "scheduler not running"}), 503
        try:
            scheduler_ref.pause_job(task_id)
            _save(task_id, enabled=False)
            return jsonify({"ok": True, "task": task_id, "status": "paused"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @admin_bp.route("/jobs/<task_id>/resume", methods=["POST"])
    def resume_job(task_id):
        if not scheduler_ref:
            return jsonify({"ok": False, "error": "scheduler not running"}), 503
        try:
            scheduler_ref.resume_job(task_id)
            _save(task_id, enabled=True)
            return jsonify({"ok": True, "task": task_id, "status": "running"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @admin_bp.route("/jobs/<task_id>/cron", methods=["PUT"])
    def update_cron(task_id):
        if not scheduler_ref:
            return jsonify({"ok": False, "error": "scheduler not running"}), 503
        data     = request.get_json() or {}
        cron_str = data.get("cron", "").strip()
        if not cron_str:
            return jsonify({"ok": False, "error": "cron required"}), 400
        try:
            parts = cron_str.split()
            if len(parts) != 5:
                return jsonify({"ok": False, "error": "cron must have 5 parts"}), 400
            from apscheduler.triggers.cron import CronTrigger
            trigger = CronTrigger(
                minute=parts[0], hour=parts[1], day=parts[2],
                month=parts[3], day_of_week=parts[4],
                timezone="Asia/Taipei",
            )
            scheduler_ref.reschedule_job(task_id, trigger=trigger)
            _save(task_id, cron_str=cron_str)   # ← 持久化
            return jsonify({"ok": True, "task": task_id, "cron": cron_str,
                            "note": "排程已更新，重啟後自動恢復此設定"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @admin_bp.route("/params/<task_id>", methods=["GET"])
    def get_params(task_id):
        if task_id not in SIGNAL_PARAMS:
            return jsonify({"items": []})
        items = []
        for key, name, vtype, default in SIGNAL_PARAMS[task_id]:
            items.append({
                "key": key, "name": name, "type": vtype,
                "default": default,
                "current": os.environ.get(key, default),
            })
        return jsonify({"task": task_id, "items": items})

    @admin_bp.route("/params/<task_id>", methods=["PUT"])
    def update_params(task_id):
        if task_id not in SIGNAL_PARAMS:
            return jsonify({"ok": False, "error": "task not found"}), 404
        data     = request.get_json() or {}
        updates  = data.get("updates", {})
        allowed  = {item[0] for item in SIGNAL_PARAMS[task_id]}
        safe_upd = {k: v for k, v in updates.items() if k in allowed}
        if not safe_upd:
            return jsonify({"ok": False, "error": "no valid keys"}), 400

        # 原子寫入 .env（.tmp → os.replace 防止截斷損毀）
        env_path = "/root/.env"
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            updated_keys = set()
            new_lines    = []
            for line in lines:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    new_lines.append(line)
                    continue
                key = stripped.split("=", 1)[0].strip()
                if key in safe_upd:
                    new_lines.append(f"{key}={safe_upd[key]}\n")
                    updated_keys.add(key)
                else:
                    new_lines.append(line)
            for k, v in safe_upd.items():
                if k not in updated_keys:
                    new_lines.append(f"{k}={v}\n")
            # 原子寫入：先寫暫存檔，再替換
            tmp_path = env_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            os.replace(tmp_path, env_path)

        # 即時生效（當前 process；注意：此為單 worker 設計，多 worker 需重啟）
        for k, v in safe_upd.items():
            os.environ[k] = str(v)

        return jsonify({
            "ok": True, "task": task_id,
            "updated": list(safe_upd.keys()),
            "note": "已即時生效（當前 worker），並原子寫入 .env（重啟後保留）",
        })

    @admin_bp.route("/signal-types", methods=["GET"])
    def signal_types():
        return jsonify({
            "names":         SIGNAL_NAMES,
            "params":        {k: [{"key":x[0],"name":x[1],"type":x[2],"default":x[3]} for x in v]
                              for k, v in SIGNAL_PARAMS.items()},
            "default_crons": DEFAULT_CRONS,
        })
