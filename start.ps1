$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
foreach ($tool in @('ffmpeg', 'ffprobe')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "缺少 $tool，请先安装 FFmpeg 并加入 PATH。"
    }
}
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $taskPython)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '创建 Python 环境失败。' }
}
& $taskPython -c 'import faster_whisper' 2>$null
if ($LASTEXITCODE -ne 0) {
    & $taskPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw '安装依赖失败。' }
}
& $taskPython server.py @args
