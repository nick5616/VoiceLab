# Builds dist\VoiceLab\VoiceLab.exe with PyInstaller and zips it to dist\VoiceLab-windows.zip.
param([string]$Python = ".\venv\Scripts\python.exe")

# Windows PowerShell 5.1 turns native stderr output (pip notices, PyInstaller logs) into errors under "Stop",
# so check exit codes instead.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

& $Python -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

& $Python -m PyInstaller packaging\voicelab.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$zip = Join-Path $root "dist\VoiceLab-windows.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -ErrorAction Stop -Path (Join-Path $root "dist\VoiceLab") -DestinationPath $zip
Write-Host "Built $zip"
