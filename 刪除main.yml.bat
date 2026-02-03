@echo off
REM 刪除 GitHub 上的 main.yml 文件

echo 正在刪除 GitHub 上的 main.yml 文件...
echo.

cd /d "%~dp0"

REM 檢查文件是否存在
if exist ".github\workflows\main.yml" (
    echo 發現本地的 main.yml 文件，正在刪除...
    git rm .github\workflows\main.yml
    git commit -m "Remove invalid main.yml file"
    git push
    echo.
    echo 已刪除並推送到 GitHub！
) else (
    echo 本地沒有 main.yml 文件。
    echo 如果是遠程文件，請在 GitHub 網站手動刪除。
    echo.
    echo 請前往：https://github.com/qaz125795/crypto-tool-helper/tree/main/.github/workflows
    echo 找到 main.yml 文件並刪除。
)

echo.
pause


