param(
    [string]$OutputDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) "dist")
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$pythonPath = Join-Path $repoRoot ".venv\Scripts\python.exe"
$desktopRoot = Join-Path $repoRoot "desktop"
$tauriRoot = Join-Path $desktopRoot "src-tauri"
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
$stageRoot = Join-Path $outputRoot "MemeSort-portable"
$archivePath = Join-Path $outputRoot "MemeSort-portable.zip"
$sidecarName = "memesort-sidecar-x86_64-pc-windows-msvc"
$sidecarBuildRoot = Join-Path $repoRoot "build\portable-sidecar"
$sidecarDistRoot = Join-Path $repoRoot "dist\portable-sidecar"
$hostBinary = Join-Path $tauriRoot "target\release\memesort-desktop.exe"
$cargoCommand = Get-Command cargo -ErrorAction SilentlyContinue
$cargoPath = if ($cargoCommand) {
    $cargoCommand.Source
}
else {
    Join-Path $env:USERPROFILE ".cargo\bin\cargo.exe"
}

function Assert-PortableStagePath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $resolved = [IO.Path]::GetFullPath($Path)
    if (-not $resolved.StartsWith($outputRoot.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify a portable stage outside the requested output directory: $resolved"
    }
}

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Missing build Python at $pythonPath. Run uv sync first."
}
if (-not (Test-Path -LiteralPath $cargoPath -PathType Leaf)) {
    throw "Cargo is required to build the Tauri host. Install Rust with rustup and ensure cargo is available."
}

& $pythonPath -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is missing from the locked build environment. Run uv sync --group build."
}

Assert-PortableStagePath -Path $stageRoot
New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
if (Test-Path -LiteralPath $stageRoot) {
    Remove-Item -Recurse -Force -LiteralPath $stageRoot
}
if (Test-Path -LiteralPath $archivePath) {
    Remove-Item -Force -LiteralPath $archivePath
}

& $pythonPath -m PyInstaller --noconfirm --clean `
    --workpath $sidecarBuildRoot `
    --distpath $sidecarDistRoot `
    (Join-Path $repoRoot "memesort_sidecar.spec")
if ($LASTEXITCODE -ne 0) {
    throw "Headless sidecar build failed."
}

Push-Location $desktopRoot
try {
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) {
        throw "Tauri frontend build failed."
    }
}
finally {
    Pop-Location
}

Push-Location $tauriRoot
try {
    & $cargoPath build --release
    if ($LASTEXITCODE -ne 0) {
        throw "Tauri host build failed."
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $hostBinary -PathType Leaf)) {
    throw "Tauri host executable was not produced at $hostBinary"
}

$sidecarSource = Join-Path $sidecarDistRoot $sidecarName
if (-not (Test-Path -LiteralPath $sidecarSource -PathType Container)) {
    throw "PyInstaller sidecar directory was not produced at $sidecarSource"
}

New-Item -ItemType Directory -Force -Path $stageRoot | Out-Null
Copy-Item -LiteralPath $hostBinary -Destination (Join-Path $stageRoot "MemeSort.exe")
Copy-Item -Recurse -LiteralPath $sidecarSource -Destination (Join-Path $stageRoot "sidecar")
Copy-Item -LiteralPath (Join-Path $repoRoot "runtime-manifest.json") -Destination $stageRoot
Copy-Item -LiteralPath (Join-Path $repoRoot "requirements-ocr.txt") -Destination $stageRoot
Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\setup_portable_runtime.ps1") -Destination $stageRoot
Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\setup_portable_runtime.bat") -Destination $stageRoot
New-Item -ItemType Directory -Force -Path (Join-Path $stageRoot "scripts") | Out-Null
Copy-Item -LiteralPath (Join-Path $repoRoot "scripts\paddle_ocr_worker.py") -Destination (Join-Path $stageRoot "scripts")

$dataRoot = Join-Path $stageRoot "MemeSortData"
foreach ($directory in @("library", "models", "runtime")) {
    $reserved = Join-Path $dataRoot $directory
    New-Item -ItemType Directory -Force -Path $reserved | Out-Null
    Set-Content -LiteralPath (Join-Path $reserved "README.txt") -Encoding utf8 -Value @"
Reserved MemeSort portable $directory directory.
This release deliberately does not include user Library data, GGUF models, the multimodal projector, or the pinned Vulkan runtime.
Keep this directory beside MemeSort.exe; the application resolves it from its executable location, never from the current working directory.
"@
}

Set-Content -LiteralPath (Join-Path $stageRoot "PORTABLE-README.txt") -Encoding utf8 -Value @"
MemeSort portable layout

MemeSort.exe
sidecar\
MemeSortData\library\
MemeSortData\models\
MemeSortData\runtime\

Do not move MemeSort.exe independently from sidecar or MemeSortData. This package contains no OS installer and does not include model or Vulkan runtime artifacts.
Run setup_portable_runtime.bat after extraction to download and verify the pinned runtime, GGUF models, and OCR environment into MemeSortData.
"@

Compress-Archive -LiteralPath $stageRoot -DestinationPath $archivePath
Write-Host "Portable package built: $archivePath"
