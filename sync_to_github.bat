@echo off
REM 自動同步到 GitHub 的腳本（Windows）
echo 正在同步到 GitHub...

git add .
git commit -m "Auto sync: %date% %time%"
git push

echo 同步完成！


