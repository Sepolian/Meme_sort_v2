$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$runtimeRoot = Join-Path $repoRoot ".runtime"
$downloadRoot = Join-Path $runtimeRoot "downloads"
$llamaRoot = Join-Path $runtimeRoot "llama.cpp-b9982-vulkan"
$modelRoot = Join-Path $repoRoot ".models\gguf\qwen3-2b-q4_k_m"

$llamaZip = Join-Path $downloadRoot "llama-b9982-bin-win-vulkan-x64.zip"
$mainModel = Join-Path $modelRoot "Qwen.Qwen3-VL-Embedding-2B.Q4_K_M.gguf"
$mmprojModel = Join-Path $modelRoot "mmproj-Qwen.Qwen3-VL-Embedding-2B.f16.gguf"

$llamaSha256 = "b8c49b3ff732d663dbbf9b1fcefb1153816d072c89bc0579197cea5d19873616"
$mainModelSha256 = "42a4ebc629ecc6514649e12b1529b857f54900273bb854f853c970fb90edd09d"
$mmprojSha256 = "3f89a7768ffa6606935319f71bf56bb71871249ba549bf1080a0caea7a088613"

New-Item -ItemType Directory -Force -Path $downloadRoot, $llamaRoot, $modelRoot | Out-Null

function Assert-Sha256 {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Expected
    )
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Expected.ToLowerInvariant()) {
        throw "SHA256 mismatch for $Path. Expected $Expected, got $actual"
    }
}

function Get-VerifiedFile {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Sha256
    )
    if (Test-Path -LiteralPath $Path) {
        try {
            Assert-Sha256 -Path $Path -Expected $Sha256
            Write-Host "Verified existing file: $Path"
            return
        }
        catch {
            Write-Host "Existing file failed verification; downloading a clean copy."
        }
    }

    $partialPath = "$Path.part"
    curl.exe -L --fail --retry 3 --progress-bar -o $partialPath $Url
    if ($LASTEXITCODE -ne 0) {
        throw "Download failed: $Url"
    }
    Assert-Sha256 -Path $partialPath -Expected $Sha256
    Move-Item -Force -LiteralPath $partialPath -Destination $Path
}

$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($uvCommand) {
    $uv = $uvCommand.Source
}
else {
    $uvRoot = Join-Path $runtimeRoot "uv"
    $uvZip = Join-Path $downloadRoot "uv-x86_64-pc-windows-msvc.zip"
    New-Item -ItemType Directory -Force -Path $uvRoot | Out-Null
    curl.exe -L --fail --retry 3 --progress-bar -o $uvZip "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to download uv from its official GitHub release."
    }
    Expand-Archive -Force -LiteralPath $uvZip -DestinationPath $uvRoot
    $uv = Join-Path $uvRoot "uv.exe"
}

Write-Host "Creating MemeSort Python environments..."
& $uv venv (Join-Path $repoRoot ".venv") --python 3.13 --managed-python
& $uv pip install --python (Join-Path $repoRoot ".venv\Scripts\python.exe") -e $repoRoot
& $uv venv (Join-Path $repoRoot ".venv-ocr") --python 3.12 --managed-python
& $uv pip install --python (Join-Path $repoRoot ".venv-ocr\Scripts\python.exe") --index "https://www.paddlepaddle.org.cn/packages/stable/cpu/" -r (Join-Path $repoRoot "requirements-ocr.txt")

Write-Host "Downloading pinned llama.cpp Vulkan runtime..."
Get-VerifiedFile `
    -Url "https://github.com/ggml-org/llama.cpp/releases/download/b9982/llama-b9982-bin-win-vulkan-x64.zip" `
    -Path $llamaZip `
    -Sha256 $llamaSha256
Expand-Archive -Force -LiteralPath $llamaZip -DestinationPath $llamaRoot

Write-Host "Downloading pinned Qwen3-VL-Embedding 2B GGUF bundle (about 1.93 GB)..."
Get-VerifiedFile `
    -Url "https://huggingface.co/DevQuasar/Qwen.Qwen3-VL-Embedding-2B-GGUF/resolve/main/Qwen.Qwen3-VL-Embedding-2B.Q4_K_M.gguf" `
    -Path $mainModel `
    -Sha256 $mainModelSha256
Get-VerifiedFile `
    -Url "https://huggingface.co/DevQuasar/Qwen.Qwen3-VL-Embedding-2B-GGUF/resolve/main/mmproj-Qwen.Qwen3-VL-Embedding-2B.f16.gguf" `
    -Path $mmprojModel `
    -Sha256 $mmprojSha256

$server = Join-Path $llamaRoot "llama-server.exe"
Write-Host "Setup complete. llama.cpp devices:"
& $server --list-devices
