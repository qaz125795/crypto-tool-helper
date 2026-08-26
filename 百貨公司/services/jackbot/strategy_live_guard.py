"""
新策略上線前哨兵（cron 每日跑一次）。

背景（2026-08-26）：TKUP／BTCR／WHAL 上線時只有「本季擂台」回測數字（62~65% 勝率），
沒有樣本量門檻就直接開真倉；實盤跑到 1~5 筆時已經 0% 勝率，卻是虧了錢才發現。
本腳本把「用真錢驗證回測數字」這件事自動化、定期跑，虧錢之前先示警，不要再無聲無息。

規則：
  1. 樣本不足：帳號已啟用實盤（gate-quant enabled=true），但本地累積已結案樣本
     < MIN_SAMPLE_FOR_FULL_SIZE → 標記「樣本不足，建議降倉觀察」。
  2. 顯著劣於宣傳數字：已有 >= MIN_SAMPLE_FOR_JUDGE 筆已結案樣本，且
     live_WR < backtest_WR * DIVERGENCE_RATIO 或 live_avgR <= 0（回測 avgR > 0）
     → 標記「顯著劣於宣傳數字，建議人工複核」。
  3. 兩者皆不觸發 → OK。

不自動下架／不自動改帳號設定；只示警，讓人決定要不要動手（呼應「不要用 AI 做確定性判斷」，
判斷交給人，AI 只做分類/整理示警文字）。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta

import httpx

DIR = os.environ.get("ARENA_DATA_DIR", "/app/data/crit_collector")
if not os.path.isdir(DIR):
    DIR = os.path.dirname(os.path.abspath(__file__))

FRS_LOG = os.path.join(DIR, "frs_signals.jsonl")
SMCP_LOG = os.path.join(DIR, "smcp_signals.jsonl")
OUT = os.path.join(DIR, "strategy_live_guard.json")

TW = timezone(timedelta(hours=8))
GATE_QUANT_URL = os.environ.get("GATE_QUANT_URL", "http://gate-quant:8001")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT = os.environ.get("FRS_TG_CHAT", "-1003611242392")
TG_THREAD = int(os.environ.get("FRS_TG_THREAD", "250"))

MIN_SAMPLE_FOR_FULL_SIZE = int(os.environ.get("GUARD_MIN_SAMPLE_FULL", "20"))
MIN_SAMPLE_FOR_JUDGE = int(os.environ.get("GUARD_MIN_SAMPLE_JUDGE", "5"))
DIVERGENCE_RATIO = float(os.environ.get("GUARD_DIVERGENCE_RATIO", "0.5"))

# 帳號白名單 → 對應本地訊號檔 + 宣傳回測數字（wr%, avgR）。人工更新，不用每次都準。
STRATEGY_REF = {
    "小盤妖股": {"log": SMCP_LOG, "backtest_wr": 45.5, "backtest_avgR": 0.996},
    "資費反殺": {"log": FRS_LOG, "backtest_wr": 52.6, "backtest_avgR": 0.32},
    "突破手·品質": {"log": FRS_LOG, "backtest_wr": 60.3, "backtest_avgR": 0.719},
    "主買狂潮": {"log": FRS_LOG, "backtest_wr": 64.5, "backtest_avgR": 0.884},
    "BTC閘門動能": {"log": FRS_LOG, "backtest_wr": 65.5, "backtest_avgR": 1.189},
    "鯨魚雙吸": {"log": FRS_LOG, "backtest_wr": 62.6, "backtest_avgR": 1.132},
}


def _load_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    return out


def _live_stats(records: list[dict], strategy: str) -> dict:
    closed = [r for r in records if r.get("strategy") == strategy and r.get("status") in ("win", "loss", "timeout")]
    n = len(closed)
    wins = sum(1 for r in closed if r["status"] == "win")
    losses = sum(1 for r in closed if r["status"] == "loss")
    rs = []
    for r in closed:
        entry, sl, tp1 = r.get("entry"), r.get("sl"), r.get("tp1")
        if not (entry and sl):
            continue
        risk = abs(entry - sl)
        if risk <= 0:
            continue
        if r["status"] == "loss":
            rs.append(-1.0)
        elif r["status"] == "win" and tp1:
            rs.append(abs(tp1 - entry) / risk)
        elif r["status"] == "timeout":
            rs.append(0.0)
    avg_r = sum(rs) / len(rs) if rs else 0.0
    wr = wins / n * 100 if n else 0.0
    return {"n": n, "wins": wins, "losses": losses, "wr": wr, "avg_r": avg_r, "total_r": sum(rs)}


def _gate_accounts() -> list[dict]:
    if not ADMIN_TOKEN:
        return []
    try:
        r = httpx.get(f"{GATE_QUANT_URL}/accounts", headers={"X-Admin-Token": ADMIN_TOKEN}, timeout=15)
        data = r.json()
        return data if isinstance(data, list) else data.get("accounts", [])
    except Exception as e:
        print("[guard] 讀取 gate-quant 帳號失敗（不影響其他檢查）:", str(e)[:120])
        return []


def send_tg(text: str) -> bool:
    if not TG_TOKEN:
        print("[guard] TG_TOKEN 未設定，只印出結果:\n" + text)
        return False
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={
                "chat_id": TG_CHAT, "message_thread_id": TG_THREAD,
                "text": text, "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        return bool(r.json().get("ok"))
    except Exception as e:
        print("[guard] TG 發送失敗:", str(e)[:120])
        return False


def classify_severity(stats: dict, ref: dict, enabled: bool) -> tuple[str, str]:
    """回傳 (severity, reason)。純函式，方便測試「為什麼」會示警。

    優先序：明顯劣於宣傳數字（fail）> 樣本不足還不能判斷（warn）> 正常（ok）。
    即使樣本才 5 筆，只要已經是 0% 勝率這種一眼看出的異常，也不該被「樣本不足」
    這種較溫和的提示蓋掉——這正是 TKUP/BTCR/WHAL 這次的真實狀況。
    """
    if not enabled:
        return "ok", ""
    if stats["n"] >= MIN_SAMPLE_FOR_JUDGE:
        wr_bad = stats["wr"] < ref["backtest_wr"] * DIVERGENCE_RATIO
        avgr_bad = stats["avg_r"] <= 0 and ref["backtest_avgR"] > 0
        if wr_bad or avgr_bad:
            return "fail", (
                f"實盤 WR={stats['wr']:.1f}% avgR={stats['avg_r']:+.2f}"
                f" 明顯劣於宣傳 WR={ref['backtest_wr']}% avgR={ref['backtest_avgR']:+.2f}"
                "，建議人工複核是否暫停"
            )
    if stats["n"] < MIN_SAMPLE_FOR_FULL_SIZE:
        return "warn", f"樣本不足（{stats['n']}<{MIN_SAMPLE_FOR_FULL_SIZE}），建議降倉觀察，別滿倉信回測數字"
    return "ok", ""


def main() -> None:
    accounts_by_whitelist = {a.get("strategy_whitelist"): a for a in _gate_accounts()}
    now_str = datetime.now(TW).strftime("%Y-%m-%d %H:%M")

    frs_recs = _load_jsonl(FRS_LOG)
    smcp_recs = _load_jsonl(SMCP_LOG)

    rows = []
    alerts = []
    for strat, ref in STRATEGY_REF.items():
        records = smcp_recs if ref["log"] == SMCP_LOG else frs_recs
        stats = _live_stats(records, strat)
        acct = accounts_by_whitelist.get(strat)
        enabled = bool(acct and acct.get("enabled"))
        severity, reason = classify_severity(stats, ref, enabled)

        row = {
            "strategy": strat, "enabled": enabled, "severity": severity, "reason": reason,
            "live_n": stats["n"], "live_wins": stats["wins"], "live_losses": stats["losses"],
            "live_wr": round(stats["wr"], 1), "live_avg_r": round(stats["avg_r"], 3),
            "live_total_r": round(stats["total_r"], 2),
            "backtest_wr": ref["backtest_wr"], "backtest_avg_r": ref["backtest_avgR"],
        }
        rows.append(row)
        if severity != "ok":
            alerts.append(row)

    out = {"ts": now_str, "rows": rows}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(json.dumps(out, ensure_ascii=False, indent=2))

    if alerts:
        lines = [f"🛡 *策略上線前哨兵*（{now_str}）", "偵測到與宣傳數字不符或樣本不足的實盤策略：", ""]
        for a in alerts:
            icon = "🔴" if a["severity"] == "fail" else "🟡"
            lines.append(
                f"{icon} *{a['strategy']}*：{a['reason']}\n"
                f"    實盤 n={a['live_n']} W={a['live_wins']} L={a['live_losses']} "
                f"WR={a['live_wr']}% avgR={a['live_avg_r']:+.2f} totalR={a['live_total_r']:+.2f}"
            )
        lines.append("\n此訊息只示警，不會自動暫停或改帳號設定。")
        send_tg("\n".join(lines))
    else:
        print("[guard] 所有已啟用策略皆在正常範圍內，不發送警示")


if __name__ == "__main__":
    main()
