param(
    [string]$PythonExecutable,
    [switch]$ForceInstall
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvDir = Join-Path $projectRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$requirementsPath = Join-Path $projectRoot "requirements-chat.txt"
$markerPath = Join-Path $venvDir ".chat-requirements.sha256"

function Test-Python([string]$Executable, [string[]]$Arguments = @()) {
    if (-not $Executable) { return $false }
    try {
        & $Executable @Arguments -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)" 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    $selectedPython = $null
    $selectedArgs = @()
    if ($PythonExecutable) {
        if (-not (Test-Python $PythonExecutable)) { throw "The selected Python cannot run (Python 3.8+ required)." }
        $selectedPython = $PythonExecutable
    } else {
        $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
        if ($pyLauncher) {
            foreach ($version in @('-3.12', '-3.11', '-3.10')) {
                if (Test-Python $pyLauncher.Source @($version)) {
                    $selectedPython = $pyLauncher.Source
                    $selectedArgs = @($version)
                    break
                }
            }
        }
        if (-not $selectedPython) {
            $systemPython = Get-Command python -ErrorAction SilentlyContinue
            if ($systemPython -and $systemPython.Source -notlike '*\WindowsApps\*' -and (Test-Python $systemPython.Source)) {
                $selectedPython = $systemPython.Source
            }
        }
    }
    if (-not $selectedPython) {
        throw "Python was not found. Install Python 3.11 or 3.12 from https://www.python.org/downloads/ (enable Add Python to PATH), then run start_local.bat again. No system software was installed."
    }
    Write-Host "Creating an isolated .venv (existing system packages are not changed)..."
    & $selectedPython @selectedArgs -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { throw "Cannot create .venv. Check Python includes venv and pip." }
}

$requirementsHash = (Get-FileHash -LiteralPath $requirementsPath -Algorithm SHA256).Hash
$savedHash = if (Test-Path -LiteralPath $markerPath) { (Get-Content -LiteralPath $markerPath -Raw).Trim() } else { '' }
$ready = Test-Python $venvPython
if ($ready) {
    try {
        & $venvPython -c "import streamlit, requests, numpy, dotenv, fastembed" 2>$null | Out-Null
        $ready = $LASTEXITCODE -eq 0
    } catch { $ready = $false }
}
if ($ForceInstall -or -not $ready -or $savedHash -ne $requirementsHash) {
    # ONNX includes deep fixture paths. Avoid a half-installed environment when
    # Windows long-path support is disabled; never change the registry for users.
    $longPathsEnabled = (Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -Name LongPathsEnabled -ErrorAction SilentlyContinue).LongPathsEnabled
    if ($projectRoot.Length -gt 80 -and $longPathsEnabled -ne 1) {
        throw "This project path is too long for ONNX installation on this Windows setup. Move the clone to a shorter path such as C:\AI\PersonaLens, then rerun. No system settings, .env or user documents were changed."
    }
    Write-Host "Installing chat dependencies (first run needs internet; optional collectors are not installed)..."
    & $venvPython -m pip install --disable-pip-version-check --upgrade "pip>=23"
    if ($LASTEXITCODE -ne 0) { throw "Cannot update pip in .venv. Check network and rerun." }
    & $venvPython -m pip install --disable-pip-version-check -r $requirementsPath
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. Check PyPI network access; on Windows use a short project path such as C:\AI\PersonaLens. Rerun to retry; .env and user documents were not overwritten." }
    & $venvPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "Dependency compatibility check failed." }
    Set-Content -LiteralPath $markerPath -Value $requirementsHash -Encoding ascii
} else {
    Write-Host "Chat environment is ready; skipping installation."
}

$envPath = Join-Path $projectRoot ".env"
if (-not (Test-Path -LiteralPath $envPath)) {
    Copy-Item -LiteralPath (Join-Path $projectRoot ".env.example") -Destination $envPath
    Write-Host "Created .env. Fill DEEPSEEK_API_KEY with your own key; do not share this file."
}
New-Item -ItemType Directory -Path (Join-Path $projectRoot "knowledge") -Force | Out-Null
Write-Host "Environment ready. Put your TXT or Markdown files in knowledge/ and start the app."
