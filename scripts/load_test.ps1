# Runs the Locust load test twice against a private copy of the app on port 8041:
#   A. per-address limits raised, so the global caps (queue, upload slots) are what is tested
#   B. the real per-address limits, to show overload turning into clean 429s
# Usage (venv active, from the project folder):  ./scripts/load_test.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$out = Join-Path $root '.cache\load'
New-Item -ItemType Directory -Force $out | Out-Null
$python = Join-Path $root '.venv\Scripts\python.exe'

function Invoke-Scenario($name, $seconds, $limits) {
    foreach ($key in 'UPLOADS_PER_HOUR', 'ANALYSES_PER_HOUR', 'POLLS_PER_MINUTE', 'REQUESTS_PER_MINUTE') {
        if ($limits) { Set-Item "env:SL_RATE_$key" '100000000' } else { Remove-Item "env:SL_RATE_$key" -ErrorAction SilentlyContinue }
    }
    $server = Start-Process $python -ArgumentList '-m', 'uvicorn', 'app.main:app', '--port', '8041', '--log-level', 'warning' `
        -PassThru -WindowStyle Hidden -RedirectStandardError "$out\$name-server.log"
    try {
        for ($i = 0; $i -lt 100; $i++) { try { Invoke-RestMethod http://127.0.0.1:8041/healthz -TimeoutSec 1 | Out-Null; break } catch { Start-Sleep -Milliseconds 200 } }
        # Start-Process, not "&": Windows PowerShell treats Locust's log lines on stderr as errors.
        Start-Process $python -Wait -WindowStyle Hidden -RedirectStandardError "$out\$name-locust.log" -ArgumentList `
            '-m', 'locust', '-f', 'tests/load/locustfile.py', '--headless', '-u', '20', '-r', '5', '-t', "${seconds}s", `
            '--host', 'http://127.0.0.1:8041', '--csv', "$out\$name", '--only-summary'
        $listener = (Get-NetTCPConnection -LocalPort 8041 -State Listen).OwningProcess | Select-Object -First 1
        $process = Get-Process -Id $listener
        "$name  server alive: $(-not $process.HasExited)  peak memory: $([math]::Round($process.PeakWorkingSet64 / 1MB)) MB"
    }
    finally {
        Get-NetTCPConnection -LocalPort 8041 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
}

Invoke-Scenario 'raised-limits' 60 $true
Invoke-Scenario 'real-limits' 30 $false
& $python scripts/load_summary.py "$out\raised-limits" "$out\real-limits"
