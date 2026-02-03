@echo off
REM 推送 workflow 文件到 GitHub

echo 正在推送 workflow 文件到 GitHub...

cd /d "%~dp0"

REM 添加 .github 目錄
git add .github/

REM 提交
git commit -m "Fix: Remove invalid main.yml, keep individual workflow files"

REM 推送到 GitHub
git push

echo.
echo 完成！請檢查 GitHub Actions 是否正常。
pause


