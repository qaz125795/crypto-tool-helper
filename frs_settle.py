"""
狙擊訊號結算 + 連虧熔斷（cron 每 15 分）。

讀 frs_signals.jsonl 的 open 訊號 → 用 Gate 5m K 線判定 TP1/SL/逾時：
  LONG：先到 low<=SL → 止損(loss)；先到 high>=TP1 → 止盈(win)
  SHORT：先到 high>=SL → 止損(loss)；先到 low<=TP1 → 止盈(win)
  逾 HORIZON_H 未觸發 → 逾時(timeout，不計入連虧)

連虧熔斷：每策略連續 LOSS_STREAK 筆止損 → 暫停推播 PAUSE_H 小時（寫 frs_breaker.json）。
"""
import json
import os
import time

import httpx

DIR = os.environ.get("ARENA_DATA_DIR", "/app/data/crit_collector")
if not os.path.isdir(DIR):
    DIR = os.path.dirname(os.path.abspath(__file__))
SIGNALS_LOG = os.path.join(DIR, "frs_signals.jsonl")
BREAKER = os.path.join(DIR, "frs_breaker.json")

HORIZON_H = float(os.environ.get("FRS_HORIZON_H", "48"))
PAUSE_H = float(os.environ.get("FRS_PAUSE_H", "12"))
LOSS_STREAK = int(os.environ.get("FRS_LOSS_STREAK", "3"))


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def base_of(sym):
    return (sym or "").upper().replace("USDT", "").replace("_", "")


def gate_klines(base, frm, to):
    for contract in ("%s_USDT" % base, "1000%s_USDT" % base):
        try:
            r = httpx.get(
                "https://api.gateio.ws/api/v4/futures/usdt/candlesticks",
                params={"contract": contract, "interval": "5m", "from": int(frm), "to": int(to)},
                timeout=15,
            )
            j = r.json()
            if isinstance(j, list) and j:
                out = []
                for b in j:
                    if isinstance(b, dict):
                        t = fnum(b.get("t"))
                        h = fnum(b.get("h")); l = fnum(b.get("l"))
                        if t and h and l:
                            out.append((t, h, l))
                if out:
                    out.sort(key=lambda x: x[0])
                    return out
        except Exception:
            continue
    return None


def settle_one(rec):
    """回傳 win / loss / timeout / open"""
    entry_ts = fnum(rec.get("ts")) or 0
    side = (rec.get("side") or "").upper()
    sl = fnum(rec.get("sl")); tp1 = fnum(rec.get("tp1"))
    if not entry_ts or sl is None or tp1 is None:
        return "open"
    now = time.time()
    kl = gate_klines(base_of(rec.get("sym")), entry_ts, now)
    if kl:
        for t, h, l in kl:
            if t < entry_ts:
                continue
            if side == "LONG":
                if l <= sl:
                    return "loss"
                if h >= tp1:
                    return "win"
            else:
                if h >= sl:
                    return "loss"
                if l <= tp1:
                    return "win"
    if now - entry_ts > HORIZON_H * 3600:
        return "timeout"
    return "open"


def load_breaker():
    try:
        with open(BREAKER, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def main():
    if not os.path.exists(SIGNALS_LOG):
        print("[settle] no signals log")
        return
    recs = []
    with open(SIGNALS_LOG, encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                recs.append(json.loads(ln))
            except json.JSONDecodeError:
                continue

    breaker = load_breaker()
    newly = 0
    for rec in recs:
        if rec.get("status") == "open":
            res = settle_one(rec)
            if res != "open":
                rec["status"] = res
                newly += 1

    # 依策略、時間序，從已結算且尚未計入熔斷的訊號更新連虧
    for rec in sorted(recs, key=lambda r: r.get("ts", 0)):
        if rec.get("status") in ("win", "loss", "timeout") and not rec.get("counted"):
            strat = rec.get("strategy")
            st = breaker.setdefault(strat, {"streak": 0, "paused_until": 0})
            if rec["status"] == "loss":
                st["streak"] = int(st.get("streak", 0)) + 1
            else:
                st["streak"] = 0
            if st["streak"] >= LOSS_STREAK:
                st["paused_until"] = time.time() + PAUSE_H * 3600
                st["streak"] = 0
                st["last_pause_at"] = int(time.time())
                print("[settle] 🔴 %s 連虧%d → 熔斷暫停 %.0fh" % (strat, LOSS_STREAK, PAUSE_H))
            rec["counted"] = True

    with open(SIGNALS_LOG, "w", encoding="utf-8") as f:
        for rec in recs:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with open(BREAKER, "w", encoding="utf-8") as f:
        json.dump(breaker, f, ensure_ascii=False, indent=2)

    opens = sum(1 for r in recs if r.get("status") == "open")
    wins = sum(1 for r in recs if r.get("status") == "win")
    losses = sum(1 for r in recs if r.get("status") == "loss")
    print("[settle] 本輪新結算=%d | 累計 win=%d loss=%d open=%d | breaker=%s"
          % (newly, wins, losses, opens, json.dumps(breaker, ensure_ascii=False)))


if __name__ == "__main__":
    main()
