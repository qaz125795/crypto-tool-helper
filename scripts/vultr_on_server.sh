#!/bin/bash
# 在 Vultr 上執行（root@108.160.139.47 或 Vultr Web Console）
#
# 用法：
#   export GATE_TESTNET_KEY='你的key'
#   export GATE_TESTNET_SECRET='你的secret'
#   # 若 arena_web / fr 腳本不在 unified-platform 根目錄，可指定：
#   # export EXTRA_SRC=/root/crypto-tool-helper
#   bash scripts/vultr_on_server.sh
set -euo pipefail

UNIFIED="${UNIFIED:-/root/unified-platform}"
EXTRA_SRC="${EXTRA_SRC:-}"
REPO="${REPO:-$UNIFIED}"
WR_RELEASE="${WR_RELEASE:-/data/partner-apps/p-6e6dee8f/releases/current}"

log() { echo "[deploy] $*"; }
warn() { echo "[warn] $*" >&2; }

src_file() {
  local name="$1"
  for base in "$EXTRA_SRC" "$REPO" "/root/deploy-staging" "/root/crypto-tool-helper"; do
    [ -n "$base" ] && [ -f "$base/$name" ] && { echo "$base/$name"; return 0; }
  done
  return 1
}

src_dir() {
  local name="$1"
  for base in "$EXTRA_SRC" "$REPO" "/root/deploy-staging" "/root/crypto-tool-helper"; do
    [ -n "$base" ] && [ -d "$base/$name" ] && { echo "$base/$name"; return 0; }
  done
  return 1
}

cd "$UNIFIED"
log "工作目錄 $UNIFIED"

# ── 前端（不需 WR_TOKEN，直接覆寫 war-room release）──
ARENA="$(src_dir arena_web || true)"
if [ -n "$ARENA" ]; then
  log "前端 $ARENA → $WR_RELEASE"
  mkdir -p "$WR_RELEASE/js"
  for f in fund.html volume.html index.html vip.html; do
    [ -f "$ARENA/$f" ] && cp "$ARENA/$f" "$WR_RELEASE/$f"
  done
  for f in js/fund.js js/volume_live.js js/access_guard.js; do
    [ -f "$ARENA/$f" ] && cp "$ARENA/$f" "$WR_RELEASE/$f"
  done
else
  warn "找不到 arena_web，跳過前端"
fi

# ── frs 訊號（volume mount + 映像檔雙寫）──
CRIT="$UNIFIED/services/jackbot/data/crit_collector"
mkdir -p "$CRIT"
for fn in fr_sniper_push.py frs_settle.py; do
  if fp="$(src_file "$fn")"; then
    cp "$fp" "$UNIFIED/services/jackbot/$fn"
    cp "$fp" "$CRIT/$fn"
    log "frs: $fn"
  else
    warn "找不到 $fn"
  fi
done

# ── gate-quant volume_runner ──
VR_LOCAL="$(src_dir 百貨公司/services/gate-quant/backend/volume_runner || src_dir services/gate-quant/backend/volume_runner || true)"
VR_REMOTE="$UNIFIED/services/gate-quant/backend/volume_runner"
if [ -n "$VR_LOCAL" ]; then
  log "volume_runner $VR_LOCAL → $VR_REMOTE"
  mkdir -p "$VR_REMOTE"
  cp "$VR_LOCAL"/*.py "$VR_REMOTE/"
  APP_SRC="$(src_file 百貨公司/services/gate-quant/backend/app.py || src_file services/gate-quant/backend/app.py || true)"
  if [ -n "$APP_SRC" ]; then
    cp "$APP_SRC" "$UNIFIED/services/gate-quant/backend/app.py"
    log "app.py 已更新"
  fi
else
  warn "找不到 volume_runner 原始碼，僅 rebuild 現有 gate-quant"
fi

# ── Testnet API + Volume Runner env ──
ENV="$UNIFIED/.env"
patch_env() {
  local k="$1" v="$2"
  if grep -q "^${k}=" "$ENV" 2>/dev/null; then
    sed -i "s|^${k}=.*|${k}=${v}|" "$ENV"
  else
    echo "${k}=${v}" >> "$ENV"
  fi
}

if [ -n "${GATE_TESTNET_KEY:-}" ] && [ -n "${GATE_TESTNET_SECRET:-}" ]; then
  log "寫入 Testnet API（slot 1）"
  patch_env GATE_BASE_URL "https://api-testnet.gateapi.io/api/v4"
  patch_env GATE_KEY "$GATE_TESTNET_KEY"
  patch_env GATE_SECRET "$GATE_TESTNET_SECRET"
  patch_env VOLUME_RUNNER_ENABLED "1"
  patch_env VOLUME_RUNNER_DRY_RUN "1"
  patch_env VOLUME_RUNNER_ALLOW_MAINNET "0"
  patch_env VOLUME_RUNNER_STRATEGY "mm"
  patch_env VOLUME_RUNNER_SYMBOLS "BTC_USDT,ETH_USDT"
  patch_env VOLUME_RUNNER_MARGIN_USDT "100"
  patch_env VOLUME_RUNNER_LEVERAGE "10"
  patch_env VOLUME_RUNNER_ACCOUNT_SLOT "1"
else
  warn "未設 GATE_TESTNET_KEY/SECRET，跳過 .env API 補丁"
fi

# ── frs cron（sniper 收集後 1 分鐘推播；每 15 分結算）──
CRON_MARK="# frs-sniper-deploy"
if ! crontab -l 2>/dev/null | grep -qF "$CRON_MARK"; then
  log "追加 crontab frs 推播/結算"
  (crontab -l 2>/dev/null || true; echo "$CRON_MARK
8,23,38,53 * * * * docker exec platform-jackbot python3 /app/data/crit_collector/fr_sniper_push.py >> /var/log/frs_push.log 2>&1
*/15 * * * * docker exec platform-jackbot python3 /app/data/crit_collector/frs_settle.py >> /var/log/frs_settle.log 2>&1") | crontab -
else
  log "crontab frs 已存在，跳過"
fi

# ── rebuild ──
log "docker compose build gate-quant jackbot…"
cd "$UNIFIED"
docker compose build gate-quant jackbot
docker compose up -d gate-quant jackbot
sleep 8
docker logs platform-gate-quant --tail 20
docker logs platform-jackbot --tail 10

# ── frs 統計 ──
log "=== frs 訊號統計 ==="
docker exec platform-jackbot python3 /app/data/crit_collector/frs_settle.py 2>/dev/null || true
SIG="/app/data/crit_collector/frs_signals.jsonl"
docker exec platform-jackbot sh -c "test -f $SIG && tail -30 $SIG || echo '(尚無 frs_signals.jsonl)'"

ADMIN=$(grep -E '^ADMIN_TOKEN=' "$ENV" | cut -d= -f2- | tr -d '"')
if [ -n "$ADMIN" ]; then
  curl -s -H "X-Admin-Token: $ADMIN" http://127.0.0.1:8001/volume/status | head -c 1200
  echo
fi

log "完成。"
log "Testnet 第一週：1 組 API + mm 策略即可；4 策略正式帶單再考慮多 slot。"
log "戰情室前端：https://108.160.139.47/war-room/apps/p-6e6dee8f/"
