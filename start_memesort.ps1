$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Missing virtual environment Python at $pythonPath"
}

& $pythonPath -m pip show sentencepiece tiktoken *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Missing Qwen tokenizer runtime dependencies in .venv. Install them with: & '$pythonPath' -m pip install -r '$repoRoot\\requirements-qwen.txt'"
}

& $pythonPath -m memesort_worker desktop-shell
