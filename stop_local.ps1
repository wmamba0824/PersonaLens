param()

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pidFile = Join-Path (Join-Path $projectRoot ".runtime") "streamlit.pid"

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host "PersonaLens is not recorded as running."
    exit 0
}

$rawRecord = Get-Content -LiteralPath $pidFile -Raw
try {
    $parsedRecord = $rawRecord | ConvertFrom-Json
    if ($parsedRecord -is [int] -or $parsedRecord -is [long] -or $parsedRecord -is [string]) {
        $pidRecord = [PSCustomObject]@{ Pid = [int]$parsedRecord; StartTime = $null }
    } else {
        $pidRecord = $parsedRecord
    }
} catch {
    $pidRecord = [PSCustomObject]@{ Pid = [int]$rawRecord; StartTime = $null }
}

$recordedPid = [int]$pidRecord.Pid
$process = Get-Process -Id $recordedPid -ErrorAction SilentlyContinue
$removePidFile = $false
if ($process) {
    $sameStartTime = $false
    if ($pidRecord.StartTime) {
        $actualStartTime = $process.StartTime.ToUniversalTime()
        if ($pidRecord.StartTime -is [datetime]) {
            $recordedStartTime = $pidRecord.StartTime.ToUniversalTime()
        } else {
            $recordedStartTime = [DateTimeOffset]::Parse(
                [string]$pidRecord.StartTime,
                [Globalization.CultureInfo]::InvariantCulture,
                [Globalization.DateTimeStyles]::RoundtripKind
            ).UtcDateTime
        }
        $sameStartTime = [Math]::Abs(
            ($actualStartTime - $recordedStartTime).TotalSeconds
        ) -lt 1
    }
    $isPython = $process.ProcessName -like "python*"
    $expectedExecutable = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if ($sameStartTime -and $isPython -and $process.Path -eq $expectedExecutable) {
        # Windows venv launchers may spawn the actual Python listener as a child.
        # Stop the validated app tree, not only the bootstrap parent.
        & taskkill.exe /PID $recordedPid /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not stop the recorded app tree; PID record was preserved." }
        Write-Host "Stopped PersonaLens process $recordedPid."
        $removePidFile = $true
    } else {
        Write-Warning "PID $recordedPid no longer matches the recorded PersonaLens process; it was not stopped."
    }
} else {
    Write-Host "The recorded PersonaLens process is no longer running."
    $removePidFile = $true
}

if ($removePidFile) {
    Remove-Item -LiteralPath $pidFile -Force
    Remove-Item -LiteralPath (Join-Path $projectRoot ".runtime\lan-access.txt") -Force -ErrorAction SilentlyContinue
}
