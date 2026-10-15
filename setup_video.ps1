param([switch]$DownloadModel, [ValidateSet('small', 'base', 'tiny')][string]$Model = 'small')
$ErrorActionPreference = 'Stop'
$projectDirectory = $PSScriptRoot
& (Join-Path $projectDirectory 'setup_local.ps1')
if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'Local setup failed.' }
$videoPython = Join-Path $projectDirectory '.venv\Scripts\python.exe'
& $videoPython -m pip install --only-binary=:all: -r (Join-Path $projectDirectory 'requirements-video.txt')
if ($LASTEXITCODE -ne 0) { throw 'Video dependency installation failed. Chat/subtitles remain available.' }
if ($DownloadModel) {
    & $videoPython (Join-Path $projectDirectory 'scripts\prepare_video_model.py') --download --model $Model
    if ($LASTEXITCODE -ne 0) { throw 'Model preparation failed; no automatic retry.' }
} else {
    Write-Host 'ASR dependencies ready. To explicitly download the local model: .\setup_video.ps1 -DownloadModel'
}
