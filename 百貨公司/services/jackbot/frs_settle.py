"""
狙擊訊號結算（cron 每 15 分）。

讀 frs_signals.jsonl 的 open 訊號 → 用 Gate 5m K 線判定 TP1/SL/逾時：
  LONG：先到 low<=SL → 止損(loss)；先到 high>=TP1 → 止盈(win)
  SHORT：先到 high>=SL → 止損(loss)；先到 low<=TP1 → 止盈(win)
  逾 HORIZON_H 未觸發 → 逾時(timeout)

連虧熔斷：2026-08-25 起關閉（乾淨樣本；仍寫 counted，不再寫 paused_until）。
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
DRY = os.environ.get("DRY_RUN", "0") == "1"

TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT = os.environ.get("FRS_TG_CHAT", "-1003611242392")
TG_THREAD = int(os.environ.get("FRS_TG_THREAD", "250"))
DC_TOKEN = os.environ.get("DC_TOKEN", "")
DC_CHANNEL = os.environ.get("FRS_DC_CHANNEL", "1493134120186941470")


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


def settle_notice(rec, status):
    """到價提醒文案（跟推播同一 thread）。"""
    name = rec.get("strategy") or ""
    sym = (rec.get("sym") or "").upper().replace("_", "")
    if not sym.endswith("USDT"):
        base = base_of(sym)
        sym = base + "USDT" if base else sym
    side = (rec.get("side") or "").upper()
    arrow = "🟢 做多" if side == "LONG" else "🔴 做空"
    if status == "win":
        result = "止盈"
        rtxt = "+1.5R"
    elif status == "loss":
        result = "止損"
        rtxt = "−1.0R"
    else:
        result = "逾時平倉"
        rtxt = "0R"
    return "\n".join([
        "🛡 追蹤結案 ·「%s」" % name,
        "%s  `%s`  %s %s" % (arrow, sym, result, rtxt),
        "到價自動提醒，請自行平倉（半自動跟單）。",
    ])


def send_tg(text):
    if DRY or not TG_TOKEN:
        return False
    try:
        r = httpx.post(
            "https://api.telegram.org/bot%s/sendMessage" % TG_TOKEN,
            json={"chat_id": TG_CHAT, "message_thread_id": TG_THREAD,
                  "text": text, "parse_mode": "Markdown",
                  "disable_web_page_preview": True},
            timeout=15,
        )
        return r.json().get("ok", False)
    except Exception as e:
        print("[settle] TG err", str(e)[:80])
        return False


def send_dc(text):
    if DRY or not DC_TOKEN:
        return False
    try:
        r = httpx.post(
            "https://discord.com/api/v10/channels/%s/messages" % DC_CHANNEL,
            headers={"Authorization": "Bot %s" % DC_TOKEN},
            json={"content": text},
            timeout=15,
        )
        return r.status_code in (200, 201)
    except Exception as e:
        print("[settle] DC err", str(e)[:80])
        return False


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
                note = settle_notice(rec, res)
                ok_tg = send_tg(note)
                ok_dc = send_dc(note)
                print("[settle] notify %s %s %s tg=%s dc=%s" % (
                    rec.get("strategy"), rec.get("sym"), res, ok_tg, ok_dc))

    # 標記已結算；2026-08-25 起不再寫熔斷暫停（乾淨樣本）
    for rec in sorted(recs, key=lambda r: r.get("ts", 0)):
        if rec.get("status") in ("win", "loss", "timeout") and not rec.get("counted"):
            strat = rec.get("strategy")
            breaker.setdefault(strat, {"streak": 0, "paused_until": 0})
            rec["counted"] = True
    for st in breaker.values():
        if isinstance(st, dict):
            st["paused_until"] = 0
            st["streak"] = 0

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
