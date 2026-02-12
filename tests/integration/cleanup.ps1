#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Cleanup script for integration tests - stops Progenitor and related services

.DESCRIPTION
    This script stops all Python processes using the test ports (9500, 8000)
    and cleans up any orphaned test processes.

.EXAMPLE
    .\cleanup.ps1
#>

Write-Host "`n================================================================" -ForegroundColor Cyan
Write-Host "   Integration Test Cleanup" -ForegroundColor Cyan
Write-Host "================================================================`n" -ForegroundColor Cyan

# Find processes on test ports
Write-Host "Checking for processes on test ports..." -ForegroundColor Yellow

$port9500 = Get-NetTCPConnection -State Listen -LocalPort 9500 -ErrorAction SilentlyContinue
$port8000 = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue

$processesToStop = @()

if ($port9500) {
    Write-Host "  Found process on port 9500 (Progenitor IPC)" -ForegroundColor White
    $processesToStop += $port9500.OwningProcess
}

if ($port8000) {
    Write-Host "  Found process on port 8000 (Backend)" -ForegroundColor White
    $processesToStop += $port8000.OwningProcess
}

if ($processesToStop.Count -eq 0) {
    Write-Host "`n✓ No test processes running" -ForegroundColor Green
    exit 0
}

# Get unique PIDs
$processesToStop = $processesToStop | Select-Object -Unique

Write-Host "`nStopping processes..." -ForegroundColor Yellow
foreach ($procId in $processesToStop) {
    try {
        $proc = Get-Process -Id $procId -ErrorAction Stop
        Write-Host "  Stopping PID $procId ($($proc.ProcessName))..." -ForegroundColor White
        Stop-Process -Id $procId -Force -ErrorAction Stop
        Write-Host "    ✓ Stopped" -ForegroundColor Green
    }
    catch {
        Write-Host "    ⚠️ Could not stop: $_" -ForegroundColor Yellow
    }
}

# Wait and verify
Start-Sleep -Seconds 2

Write-Host "`nVerifying cleanup..." -ForegroundColor Cyan
$port9500After = Get-NetTCPConnection -State Listen -LocalPort 9500 -ErrorAction SilentlyContinue
$port8000After = Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue

if (-not $port9500After -and -not $port8000After) {
    Write-Host "✓ All test ports are now free" -ForegroundColor Green
}
else {
    Write-Host "⚠️ Some ports are still in use:" -ForegroundColor Yellow
    if ($port9500After) { Write-Host "  - Port 9500" -ForegroundColor White }
    if ($port8000After) { Write-Host "  - Port 8000" -ForegroundColor White }
}

Write-Host "`n================================================================`n" -ForegroundColor Cyan
