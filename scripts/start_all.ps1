$ErrorActionPreference = "Stop"

# Resolve project root (one level above this script directory)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $ScriptDir
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "Python venv not found at $Python"
}

function Start-Progenitor {
    Write-Host "Starting Progenitor..." -ForegroundColor Cyan
    $out = Join-Path $LogsDir "progenitor.out.log"
    $err = Join-Path $LogsDir "progenitor.err.log"
    Start-Process -FilePath $Python `
        -ArgumentList '-m','app.progenitor.main' `
        -WorkingDirectory $Root `
        -RedirectStandardOutput $out `
        -RedirectStandardError $err `
        -WindowStyle Hidden | Out-Null
}

function Start-ChatServer {
    Write-Host "Starting Chat Server (uvicorn @ 0.0.0.0:8000)..." -ForegroundColor Cyan
    $out = Join-Path $LogsDir "chatserver.out.log"
    $err = Join-Path $LogsDir "chatserver.err.log"
    $envMap = @{ CHAT_SERVER_PYTHON = $Python }
    Start-Process -FilePath $Python `
        -ArgumentList '-m','uvicorn','app.chat_server.main:app','--host','0.0.0.0','--port','8000','--log-level','debug' `
        -WorkingDirectory $Root `
        -Environment $envMap `
        -RedirectStandardOutput $out `
        -RedirectStandardError $err `
        -WindowStyle Hidden | Out-Null
}

# Ensure logs directory exists
$LogsDir = Join-Path $Root "logs"
if (-not (Test-Path $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir | Out-Null }

Start-Progenitor
Start-ChatServer

Write-Host "Processes launched. Check logs/ for activity (e.g., logs/decisions.jsonl, progenitor.out.log, chatserver.out.log)." -ForegroundColor Green
