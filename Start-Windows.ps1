$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$trackerPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $trackerPython)) {
    Write-Host 'Preparing LearnHub Tracker...'
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.9 or newer and reopen this launcher.' }
}
$trackerStamp = Join-Path $PSScriptRoot '.venv\.installed'
$trackerRequirements = Join-Path $PSScriptRoot 'requirements.txt'
if (-not (Test-Path -LiteralPath $trackerStamp) -or (Get-Item -LiteralPath $trackerRequirements).LastWriteTime -gt (Get-Item -LiteralPath $trackerStamp).LastWriteTime) {
    & $trackerPython -m ensurepip
    if ($LASTEXITCODE -ne 0) { throw 'Could not prepare pip.' }
    & $trackerPython -m pip install -r $trackerRequirements
    if ($LASTEXITCODE -ne 0) { throw 'Installation failed. Check your internet connection and retry.' }
    New-Item -ItemType File -Force -Path $trackerStamp | Out-Null
}
# Keeping this terminal open matches the Mac launcher and makes failures visible.
& $trackerPython app.py
if ($LASTEXITCODE -ne 0) { throw 'LearnHub Tracker stopped with an error.' }
