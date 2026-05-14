@echo off
REM ================================================================
REM 複製此檔為 run-position_change.bat / run-hyperliquid.bat 等
REM 修改 TASK 與路徑，工作排程器呼叫此 bat 即可
REM ⚠️ 含真金鑰的 bat 請勿提交 Git 或對外分享
REM ================================================================
setlocal
cd /d "C:\Path\To\加密貨幣推播工具"

REM ── 若不使用系統環境變數，在此 set（擇一，不要重複設）──
REM set TG_TOKEN=
REM set CHAT_ID=
REM set CG_API_KEY=
REM set ETHERSCAN_API_KEY=        <- hyperliquid 需要
REM set CHAT_ID_2=                <- 第二頻道（選用）
REM set TG_THREAD_IDS_2={"position_change":12345}   <- 第二頻道（選用）

REM ── 修改成要執行的模組 ──
REM 可選：position_change / hyperliquid / crit_radar / buying_power_monitor /
REM        screener_board / economic_data / economic_data_preview / news /
REM        funding_rate / long_term_index_once / liquidity_radar /
REM        altseason_radar / gold_signal / sector_ranking / reset_data
set TASK=position_change

".venv\Scripts\python.exe" jackbot.py %TASK% >> data\win_task_%TASK%.log 2>&1
endlocal
