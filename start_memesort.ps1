$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $repoRoot ".venv\Scripts\python.exe"
$manifestPath = Join-Path $repoRoot "runtime-manifest.json"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Missing virtual environment Python at $pythonPath"
}

if (-not (Test-Path -LiteralPath $manifestPath)) {
    throw "Missing runtime manifest at $manifestPath"
}

& $pythonPath -m memesort_worker.runtime_activation validate --manifest $manifestPath
if ($LASTEXITCODE -ne 0) {
    throw "The pinned Vulkan runtime is not activated for runtime-manifest.json. Run .\scripts\setup_windows_llama.ps1."
}

& $pythonPath -m memesort_worker desktop-shell
