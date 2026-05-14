# 在「本機自架部署包」資料夾內執行此腳本即可（會自動壓縮上一層整個專案）
# 產生可帶到另一台電腦的 zip（不含 venv、.git、data、__pycache__）
$ErrorActionPreference = "Stop"
# 預設：本腳本位於 「專案\本機自架部署包\打包帶走.ps1」，專案根 = 上一層
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $root "jackbot.py"))) {
    Write-Error "請在專案根目錄執行，或確認 jackbot.py 存在。目前 root=$root"
}
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$zipName = "JackBot-本機部署-$stamp.zip"
$zipPath = Join-Path $root $zipName
$items = Get-ChildItem -LiteralPath $root -Force | Where-Object {
    $_.Name -notin @('venv', '.venv', '.git', 'data', '__pycache__') -and
    $_.Name -notlike '*.zip'
}
Compress-Archive -Path ($items.FullName) -DestinationPath $zipPath -Force
Write-Host "已建立: $zipPath"
Write-Host "請在目標電腦解壓後依 本機自架部署包\README.md 建立 .venv 與 .env"
