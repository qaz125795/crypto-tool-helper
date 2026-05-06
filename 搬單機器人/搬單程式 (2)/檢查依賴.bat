@echo off
chcp 65001 >nul
echo ========================================
echo 檢查並安裝依賴套件
echo ========================================
echo.

cd /d "%~dp0"

echo 正在檢查 Python...
python --version
if errorlevel 1 (
    echo [錯誤] 找不到 Python！
    echo 請先安裝 Python 3.6 或更新版本
    pause
    exit /b 1
)

echo.
echo 正在檢查 telethon...
python -c "import telethon" 2>nul
if errorlevel 1 (
    echo telethon 未安裝，正在安裝...
    pip install telethon
    if errorlevel 1 (
        echo [錯誤] 安裝失敗！請檢查網路連線或使用管理員權限執行
        pause
        exit /b 1
    )
    echo telethon 安裝成功 ✓
) else (
    echo telethon 已安裝 ✓
)

echo.
echo 正在檢查 tkinter...
python -c "import tkinter" 2>nul
if errorlevel 1 (
    echo [警告] tkinter 未安裝
    echo Windows 通常已內建，如未安裝請手動安裝
) else (
    echo tkinter 已安裝 ✓
)

echo.
echo ========================================
echo 所有依賴檢查完成！
echo 現在可以執行 啟動GUI.bat 來啟動程式
echo ========================================
echo.
pause

