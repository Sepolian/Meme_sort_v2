$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $repoRoot ".venv\Scripts\python.exe"
$bundledServer = Join-Path $repoRoot ".runtime\llama.cpp-b9982-vulkan\llama-server.exe"
$bundledModel = Join-Path $repoRoot ".models\gguf\qwen3-2b-q4_k_m"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Missing virtual environment Python at $pythonPath"
}

if (-not $env:MEMESORT_LLAMA_SERVER) {
    if (-not (Test-Path -LiteralPath $bundledServer)) {
        throw "Missing llama.cpp Vulkan runtime at $bundledServer. Follow README.md setup steps."
    }
    $env:MEMESORT_LLAMA_SERVER = $bundledServer
}

if (-not (Test-Path -LiteralPath $bundledModel)) {
    throw "Missing GGUF model bundle at $bundledModel. Follow README.md setup steps."
}

& $pythonPath -m memesort_worker desktop-shell
