#!/bin/bash
# 自動同步到 GitHub 的腳本（Linux/Mac）
echo "正在同步到 GitHub..."

git add .
git commit -m "Auto sync: $(date '+%Y-%m-%d %H:%M:%S')"
git push

echo "同步完成！"


