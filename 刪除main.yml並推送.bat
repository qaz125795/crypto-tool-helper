@echo off
chcp 65001 >nul
echo ========================================
echo 刪除 GitHub 上的 main.yml 文件
echo ========================================
echo.

cd /d "%~dp0"

REM 檢查是否為 Git 倉庫
if not exist ".git" (
    echo [錯誤] 當前目錄不是 Git 倉庫！
    echo 請確認您在正確的專案目錄中。
    pause
    exit /b 1
)

echo [1/3] 檢查 main.yml 文件...
if exist ".github\workflows\main.yml" (
    echo 發現本地文件，將刪除並推送...
    git rm .github\workflows\main.yml
    if errorlevel 1 (
        echo [錯誤] 刪除失敗！
        pause
        exit /b 1
    )
) else (
    echo 本地沒有 main.yml 文件。
    echo 嘗試強制刪除遠程文件...
    git rm --cached .github/workflows/main.yml 2>nul
)

echo.
echo [2/3] 提交更改...
git commit -m "Remove invalid main.yml file"
if errorlevel 1 (
    echo [警告] 提交失敗，可能沒有變更需要提交。
)

echo.
echo [3/3] 推送到 GitHub...
git push
if errorlevel 1 (
    echo [錯誤] 推送失敗！
    echo 請檢查網絡連接和 GitHub 認證。
    pause
    exit /b 1
)

echo.
echo ========================================
echo ✅ 完成！main.yml 已刪除並推送到 GitHub
echo ========================================
echo.
echo 下一步：
echo 1. 前往 GitHub 網站確認文件已刪除
echo 2. 設置 GitHub Secrets（API 金鑰）
echo 3. 測試執行 workflow
echo.
pause


