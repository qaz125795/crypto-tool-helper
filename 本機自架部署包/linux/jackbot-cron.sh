#!/usr/bin/env bash
# 由 cron 呼叫：請改成你的專案與 Python 路徑
set -euo pipefail

PROJECT_ROOT="/ABS/PATH/TO/加密貨幣推播工具"
VENV_PYTHON="${PROJECT_ROOT}/.venv/bin/python"

cd "${PROJECT_ROOT}"

# 若使用 .env（需安裝 export 或可手動改成 source）
if [[ -f "${PROJECT_ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${PROJECT_ROOT}/.env"
  set +a
fi

TASK="${1:-}"
LOG_DIR="${PROJECT_ROOT}/data"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/cron_${TASK}_$(date -u +%Y%m%d).log"

VALID_TASKS="position_change|crit_radar|hyperliquid|buying_power_monitor|screener_board|economic_data|economic_data_preview|news|funding_rate|long_term_index_once|liquidity_radar|altseason_radar|gold_signal|sector_ranking|reset_data"

case "${TASK}" in
  position_change|crit_radar|hyperliquid|buying_power_monitor|screener_board|\
  economic_data|economic_data_preview|news|funding_rate|long_term_index_once|\
  liquidity_radar|altseason_radar|gold_signal|sector_ranking|reset_data)
    exec >>"${LOG_FILE}" 2>&1
    echo "==== $(date -Is) START ${TASK} ===="
    "${VENV_PYTHON}" "${PROJECT_ROOT}/jackbot.py" "${TASK}"
    echo "==== $(date -Is) END ${TASK} ===="
    ;;
  *)
    echo "用法: jackbot-cron.sh <TASK>" >&2
    echo "有效 TASK：${VALID_TASKS}" >&2
    exit 64
    ;;
esac
