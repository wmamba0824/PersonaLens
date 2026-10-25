param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8501,
    [switch]$NoBrowser,
    [string]$Instance,
    [ValidateSet("Local", "Lan")]
    [string]$Mode = "Local"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$runtimeDir = Join-Path $projectRoot ".runtime"
$pidFile = Join-Path $runtimeDir "streamlit.pid"
$stdoutLog = Join-Path $runtimeDir "streamlit.stdout.log"
$stderrLog = Join-Path $runtimeDir "streamlit.stderr.log"
$url = "http://127.0.0.1:$Port"
$listenAddress = if ($Mode -eq "Lan") { "0.0.0.0" } else { "127.0.0.1" }
$accessFile = Join-Path $runtimeDir "lan-access.txt"

function Show-Access {
    Write-Host "PersonaLens is running at $url"
    if ($Mode -eq "Lan") {
        Write-Warning "Trusted LAN only. HTTP is not encrypted. Never enable router port forwarding."
        Write-Host "On your phone use this computer's Wi-Fi IPv4 address and port $Port."
        try {
            Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
                Where-Object { $_.IPAddress -match '^(192\.168\.|10\.|172\.(1[6-9]|2[0-9]|3[01])\.)' } |
                ForEach-Object { Write-Host "LAN URL ($($_.InterfaceAlias)): http://$($_.IPAddress):$Port" }
        } catch { Write-Host "Run ipconfig to find the computer's Wi-Fi IPv4 address." }
        if (Test-Path -LiteralPath $accessFile) {
            Write-Host "Access code (do not share): $((Get-Content -LiteralPath $accessFile -Raw).Trim())"
        }
        Write-Host "If blocked: allow only this Python/port from your trusted local subnet in Windows Firewall. No firewall settings were changed."
    }
}

New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
Set-Location -LiteralPath $projectRoot

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
& (Join-Path $projectRoot "setup_local.ps1")
$pythonExecutable = Join-Path $projectRoot ".venv\Scripts\python.exe"
if ($Instance) { $env:INSTANCE_CONFIG_FILE = $Instance }

function Read-PidRecord {
    if (-not (Test-Path -LiteralPath $pidFile)) {
        return $null
    }
    $raw = Get-Content -LiteralPath $pidFile -Raw
    try {
        $parsed = $raw | ConvertFrom-Json
        if ($parsed -is [int] -or $parsed -is [long] -or $parsed -is [string]) {
            return [PSCustomObject]@{ Pid = [int]$parsed; StartTime = $null; Port = $Port }
        }
        return $parsed
    } catch {
        return [PSCustomObject]@{ Pid = [int]$raw; StartTime = $null; Port = $Port }
    }
}

Write-Host "Checking local environment..."
& $pythonExecutable "scripts/doctor.py" --allow-unconfigured
if ($LASTEXITCODE -ne 0) {
    throw "Readiness check failed. Fix the errors above and run this script again."
}
$configuredInstance = & $pythonExecutable -c "from app.config.settings import INSTANCE_CONFIG_FILE; print(INSTANCE_CONFIG_FILE.as_posix())"
if ($LASTEXITCODE -ne 0) { throw "Cannot resolve the selected instance." }
$configuredInstance = ([string]$configuredInstance).Trim()

if (Test-Path -LiteralPath $pidFile) {
    $pidRecord = Read-PidRecord
    $recordedPid = [int]$pidRecord.Pid
    $existingProcess = Get-Process -Id $recordedPid -ErrorAction SilentlyContinue
    if ($existingProcess) {
        $sameStartTime = $false
        if ($pidRecord.StartTime) {
            $actualStartTime = $existingProcess.StartTime.ToUniversalTime()
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
        $expectedExecutable = [IO.Path]::GetFullPath($pythonExecutable)
        if (-not $sameStartTime -or $existingProcess.ProcessName -notlike "python*" -or $existingProcess.Path -ne $expectedExecutable) {
            Write-Warning "The PID record is stale; remove .runtime\streamlit.pid after checking the process."
            exit 1
        }
        if ($pidRecord.Port -and [int]$pidRecord.Port -ne $Port) {
            Write-Warning "The recorded app uses port $($pidRecord.Port). Stop it with stop_local.bat before changing ports."
            exit 1
        }
        if ($pidRecord.Instance -ne $configuredInstance) {
            Write-Warning "Stop the running app with stop_local.bat before switching instances."
            exit 1
        }
        $recordedAddress = if ($pidRecord.ListenAddress) { $pidRecord.ListenAddress } else { "127.0.0.1" }
        if ($recordedAddress -ne $listenAddress) {
            Write-Warning "Stop the running app with stop_local.bat before changing Local/Lan mode."
            exit 1
        }
        try {
            $health = Invoke-WebRequest -UseBasicParsing -Uri "$url/_stcore/health" -TimeoutSec 2
            if ($health.StatusCode -eq 200) {
                Write-Host "PersonaLens is already running at $url"
                Show-Access
                if (-not $NoBrowser) {
                    Start-Process $url
                }
                exit 0
            }
        } catch {
            Write-Warning "A recorded process exists but is not healthy. Use .\stop_local.ps1 first."
            exit 1
        }
    }
}

# Refuse to launch over an unrecorded listener (or mistake it for our service).
$probe = New-Object Net.Sockets.TcpClient
try {
    $probe.Connect("127.0.0.1", $Port)
    throw "Port $Port is already occupied by an unrecorded service. Check and stop the correct service first."
} catch [Net.Sockets.SocketException] {
    # Expected: this exact local port is free.
} finally { $probe.Dispose() }

$env:MD_NETWORK_MODE = $Mode.ToLowerInvariant()
if ($Mode -eq "Lan") {
    $randomBytes = New-Object byte[] 12
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($randomBytes) } finally { $generator.Dispose() }
    $env:MD_LAN_ACCESS_CODE = ([BitConverter]::ToString($randomBytes)).Replace("-", "").ToLowerInvariant()
    Set-Content -LiteralPath $accessFile -Value $env:MD_LAN_ACCESS_CODE -Encoding ascii
} else {
    Remove-Item Env:MD_LAN_ACCESS_CODE -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $accessFile -Force -ErrorAction SilentlyContinue
}

$arguments = @(
    "-m", "streamlit", "run", "chat_app.py",
    "--server.address", $listenAddress,
    "--server.port", $Port,
    "--server.headless", "true",
    "--browser.gatherUsageStats", "false"
)

Write-Host "Starting PersonaLens in the background..."
$process = Start-Process `
    -FilePath $pythonExecutable `
    -ArgumentList $arguments `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

$pidRecord = [PSCustomObject]@{
    Pid = $process.Id
    StartTime = $process.StartTime.ToUniversalTime().ToString("o")
    Port = $Port
    Instance = $configuredInstance
    ListenAddress = $listenAddress
}
$pidRecord | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding utf8

$ready = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 500
    if ($process.HasExited) {
        break
    }
    try {
        $health = Invoke-WebRequest -UseBasicParsing -Uri "$url/_stcore/health" -TimeoutSec 2
        if ($health.StatusCode -eq 200) {
            $ready = $true
            break
        }
    } catch {
        # The process is still starting.
    }
}

if (-not $ready) {
    if (-not $process.HasExited) {
        & taskkill.exe /PID $process.Id /T /F | Out-Null
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-Host "Startup failed. Recent error log:"
    if (Test-Path -LiteralPath $stderrLog) {
        Get-Content -LiteralPath $stderrLog -Tail 30
    }
    throw "PersonaLens did not become healthy within 20 seconds."
}

Show-Access
Write-Host "Logs: $runtimeDir"
Write-Host "Stop it with: .\stop_local.ps1"

if (-not $NoBrowser) {
    Start-Process $url
}
