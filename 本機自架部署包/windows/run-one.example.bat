@echo off
REM 複製此檔為 run-position_change.bat 等，並修改路徑與 TASK
setlocal
cd /d "C:\Path\To\加密貨幣推播工具"

REM 若不用系統環境變數，可在此設定（勿提交含真金鑰的檔案到 Git）
REM set TG_TOKEN=...
REM set CHAT_ID=...
REM set CG_API_KEY=...

set TASK=position_change
REM set TASK=hyperliquid
REM set TASK=crit_radar

".venv\Scripts\python.exe" jackbot.py %TASK% >> data\win_task_%TASK%.log 2>&1
endlocal
