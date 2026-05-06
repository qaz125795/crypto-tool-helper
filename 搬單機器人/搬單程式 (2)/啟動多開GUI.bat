@echo off
chcp 65001 >nul
echo ========================================
echo Telegram 搬單機器人 - 多開版 GUI 啟動器
echo ========================================
echo.

cd /d "%~dp0"

echo 正在檢查 Python 安裝...
python --version >nul 2>&1
if errorlevel 1 (
    echo [錯誤] 找不到 Python！
    echo 請確認已安裝 Python 並加入系統路徑
    echo.
    pause
    exit /b 1
)

echo Python 已安裝 ✓
echo.

echo 正在啟動多開版圖形介面...
echo.

python run_gui_multi.py

if errorlevel 1 (
    echo.
    echo [錯誤] 程式執行時發生錯誤！
    echo 請檢查：
    echo 1. 是否已安裝 telethon 套件：pip install telethon
    echo 2. 是否缺少其他依賴套件
    echo.
    pause
)

