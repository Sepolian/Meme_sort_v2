param(
    [string]$PortableRoot = $PSScriptRoot,
    [switch]$Offline
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-ContainedPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Description
    )
    $resolvedPath = [IO.Path]::GetFullPath($Path)
    $resolvedRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    if (
        -not $resolvedPath.Equals($resolvedRoot, [StringComparison]::OrdinalIgnoreCase) -and
        -not $resolvedPath.StartsWith($resolvedRoot + '\', [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Refusing $Description outside portable root: $resolvedPath"
    }
    return $resolvedPath
}

$portableRoot = [IO.Path]::GetFullPath($PortableRoot)
$dataRoot = Join-Path $portableRoot "MemeSortData"
$manifestPath = Join-Path $portableRoot "runtime-manifest.json"
$sidecarPath = Join-Path $portableRoot "sidecar\memesort-sidecar-x86_64-pc-windows-msvc.exe"
$ocrRequirements = Join-Path $portableRoot "requirements-ocr.txt"
$ocrWorker = Join-Path $portableRoot "scripts\paddle_ocr_worker.py"

foreach ($path in @($dataRoot, $manifestPath, $sidecarPath, $ocrRequirements, $ocrWorker)) {
    Assert-ContainedPath -Path $path -Root $portableRoot -Description "portable setup target" | Out-Null
}
if (-not (Test-Path -LiteralPath (Join-Path $portableRoot "MemeSort.exe") -PathType Leaf)) {
    throw "Portable root does not contain MemeSort.exe: $portableRoot"
}
foreach ($required in @($manifestPath, $sidecarPath, $ocrRequirements, $ocrWorker)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Portable package is incomplete; missing $required"
    }
}

$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json

function Convert-ManifestDataPath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)
    if ([IO.Path]::IsPathRooted($RelativePath) -or $RelativePath -match '^[A-Za-z]:') {
        throw "Portable manifest path must be relative: $RelativePath"
    }
    $parts = @($RelativePath.Replace('/', '\').Split('\', [StringSplitOptions]::RemoveEmptyEntries))
    if ($parts.Count -lt 2 -or $parts[0] -notin @('.runtime', '.models')) {
        throw "Portable manifest path must start with .runtime or .models: $RelativePath"
    }
    if ($parts | Where-Object { $_ -in @('.', '..') }) {
        throw "Portable manifest path contains traversal: $RelativePath"
    }
    $managedRoot = if ($parts[0] -eq '.runtime') {
        Join-Path $dataRoot "runtime"
    }
    else {
        Join-Path $dataRoot "models"
    }
    $tail = ($parts | Select-Object -Skip 1) -join '\'
    $candidate = [IO.Path]::GetFullPath((Join-Path $managedRoot $tail))
    Assert-ContainedPath -Path $candidate -Root $managedRoot -Description "manifest path" | Out-Null
    Assert-ContainedPath -Path $candidate -Root $dataRoot -Description "manifest path" | Out-Null
    return $candidate
}

function Assert-WindowsX64 {
    if (
        [Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
        $env:PROCESSOR_ARCHITECTURE -ne "AMD64"
    ) {
        throw "MemeSort's pinned Vulkan runtime requires Windows x64."
    }
    if ($manifest.platform.os -ne "windows" -or $manifest.platform.architecture -ne "x86_64") {
        throw "runtime-manifest.json does not target Windows x64."
    }
    if ($manifest.platform.device -ne "Vulkan0") {
        throw "runtime-manifest.json must select Vulkan0."
    }
}

function Assert-NoManagedProcess {
    param([Parameter(Mandatory = $true)][string]$PinnedServer)
    try {
        $blocking = Get-CimInstance Win32_Process | Where-Object {
            ($_.ExecutablePath -and [IO.Path]::GetFullPath($_.ExecutablePath).Equals(
                $PinnedServer, [StringComparison]::OrdinalIgnoreCase
            )) -or (
                $_.CommandLine -and $_.CommandLine.IndexOf($portableRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
                $_.Name -match '^(MemeSort|memesort-sidecar|python|pythonw)(\.exe)?$'
            )
        }
    }
    catch {
        $blocking = Get-Process -Name "llama-server", "MemeSort", "memesort-sidecar" -ErrorAction SilentlyContinue
    }
    if ($blocking) {
        $details = ($blocking | ForEach-Object {
            $pidValue = if ($_ -is [System.Diagnostics.Process]) { $_.Id } else { $_.ProcessId }
            "$($_.Name) (PID $pidValue)"
        }) -join ", "
        throw "Close MemeSort and its managed llama-server before setup: $details"
    }
}

function Assert-VerifiedFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Sha256,
        [Parameter(Mandatory = $true)][long]$SizeBytes
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Pinned artifact is missing: $Path"
    }
    $actualSize = (Get-Item -LiteralPath $Path).Length
    if ($actualSize -ne $SizeBytes) {
        throw "Size mismatch for $Path. Expected $SizeBytes bytes, got $actualSize"
    }
    $actualHash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $Sha256.ToLowerInvariant()) {
        throw "SHA256 mismatch for $Path. Expected $Sha256, got $actualHash"
    }
}

function Get-VerifiedFile {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Sha256,
        [Parameter(Mandatory = $true)][long]$SizeBytes
    )
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        try {
            Assert-VerifiedFile -Path $Path -Sha256 $Sha256 -SizeBytes $SizeBytes
            Write-Host "Verified existing artifact: $Path"
            return
        }
        catch { Write-Warning $_ }
    }
    if ($Offline) {
        throw "Offline setup cannot repair missing or invalid artifact: $Path"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    $partialPath = "$Path.part"
    Write-Host "Downloading pinned artifact: $Url"
    & curl.exe -L --fail --retry 3 --continue-at - --output $partialPath $Url
    if ($LASTEXITCODE -ne 0) { throw "Download failed: $Url" }
    Assert-VerifiedFile -Path $partialPath -Sha256 $Sha256 -SizeBytes $SizeBytes
    Move-Item -Force -LiteralPath $partialPath -Destination $Path
}

function Replace-DirectoryAtomically {
    param(
        [Parameter(Mandatory = $true)][string]$StagedPath,
        [Parameter(Mandatory = $true)][string]$TargetPath
    )
    foreach ($path in @($StagedPath, $TargetPath)) {
        Assert-ContainedPath -Path $path -Root $dataRoot -Description "runtime replacement" | Out-Null
    }
    $backupPath = "$TargetPath.backup-$([guid]::NewGuid().ToString('N'))"
    $hadTarget = Test-Path -LiteralPath $TargetPath
    try {
        if ($hadTarget) { Move-Item -LiteralPath $TargetPath -Destination $backupPath }
        Move-Item -LiteralPath $StagedPath -Destination $TargetPath
        if ($hadTarget) { Remove-Item -Recurse -Force -LiteralPath $backupPath }
    }
    catch {
        if ((-not (Test-Path -LiteralPath $TargetPath)) -and (Test-Path -LiteralPath $backupPath)) {
            Move-Item -LiteralPath $backupPath -Destination $TargetPath
        }
        throw
    }
}

Assert-WindowsX64
$downloadRoot = Convert-ManifestDataPath ([string]$manifest.paths.download_dir)
$activationRecord = Convert-ManifestDataPath ([string]$manifest.paths.activation_record)
$uvRoot = Convert-ManifestDataPath ([string]$manifest.toolchain.uv.install_dir)
$llamaRoot = Convert-ManifestDataPath ([string]$manifest.llama_cpp.install_dir)
$modelRoot = Convert-ManifestDataPath ([string]$manifest.model.install_dir)
$ocrVenv = Join-Path $dataRoot "runtime\ocr-venv"
Assert-ContainedPath -Path $ocrVenv -Root $dataRoot -Description "OCR environment" | Out-Null
$llamaServer = Join-Path $llamaRoot ([string]$manifest.llama_cpp.executable)
$mainModel = Join-Path $modelRoot ([string]$manifest.model.main.filename)
$projectorModel = Join-Path $modelRoot ([string]$manifest.model.projector.filename)

Assert-NoManagedProcess -PinnedServer $llamaServer
New-Item -ItemType Directory -Force -Path $downloadRoot, $modelRoot | Out-Null
Remove-Item -Force -LiteralPath $activationRecord -ErrorAction SilentlyContinue

$uvArchive = Join-Path $downloadRoot ([string]$manifest.toolchain.uv.archive.filename)
Get-VerifiedFile -Url ([string]$manifest.toolchain.uv.archive.url) -Path $uvArchive -Sha256 ([string]$manifest.toolchain.uv.archive.sha256) -SizeBytes ([long]$manifest.toolchain.uv.archive.size_bytes)
$stagingId = [guid]::NewGuid().ToString('N')
$uvStaging = "$uvRoot.staging-$stagingId"
New-Item -ItemType Directory -Force -Path $uvStaging | Out-Null
Expand-Archive -LiteralPath $uvArchive -DestinationPath $uvStaging
if (-not (Test-Path -LiteralPath (Join-Path $uvStaging "uv.exe") -PathType Leaf)) { throw "Pinned uv archive did not contain uv.exe" }
Replace-DirectoryAtomically -StagedPath $uvStaging -TargetPath $uvRoot
$uv = Join-Path $uvRoot "uv.exe"
if ((& $uv --version) -notmatch [regex]::Escape([string]$manifest.toolchain.uv.version)) { throw "Pinned uv version mismatch." }

$llamaArchive = Join-Path $downloadRoot ([string]$manifest.llama_cpp.archive.filename)
Get-VerifiedFile -Url ([string]$manifest.llama_cpp.archive.url) -Path $llamaArchive -Sha256 ([string]$manifest.llama_cpp.archive.sha256) -SizeBytes ([long]$manifest.llama_cpp.archive.size_bytes)
$llamaStaging = "$llamaRoot.staging-$stagingId"
New-Item -ItemType Directory -Force -Path $llamaStaging | Out-Null
Expand-Archive -LiteralPath $llamaArchive -DestinationPath $llamaStaging
if (-not (Test-Path -LiteralPath (Join-Path $llamaStaging ([string]$manifest.llama_cpp.executable)))) { throw "Pinned llama.cpp archive did not contain $($manifest.llama_cpp.executable)" }
Replace-DirectoryAtomically -StagedPath $llamaStaging -TargetPath $llamaRoot

Get-VerifiedFile -Url ([string]$manifest.model.main.url) -Path $mainModel -Sha256 ([string]$manifest.model.main.sha256) -SizeBytes ([long]$manifest.model.main.size_bytes)
Get-VerifiedFile -Url ([string]$manifest.model.projector.url) -Path $projectorModel -Sha256 ([string]$manifest.model.projector.sha256) -SizeBytes ([long]$manifest.model.projector.size_bytes)

$uvOffline = if ($Offline) { @("--offline") } else { @() }
& $uv python install ([string]$manifest.toolchain.python.ocr_version) @uvOffline
if ($LASTEXITCODE -ne 0) { throw "Failed to install pinned OCR Python." }
$ocrStaging = "$ocrVenv.staging-$stagingId"
& $uv venv $ocrStaging --python ([string]$manifest.toolchain.python.ocr_version) --managed-python @uvOffline
if ($LASTEXITCODE -ne 0) { throw "Failed to create pinned OCR environment." }
$ocrPython = Join-Path $ocrStaging "Scripts\python.exe"
& $uv pip install --python $ocrPython --index "https://www.paddlepaddle.org.cn/packages/stable/cpu/" "paddlepaddle==3.2.2" @uvOffline
if ($LASTEXITCODE -ne 0) { throw "Failed to install pinned PaddlePaddle CPU runtime." }
& $uv pip install --python $ocrPython -r $ocrRequirements @uvOffline
if ($LASTEXITCODE -ne 0) { throw "Failed to install pinned PaddleOCR worker dependencies." }
& $ocrPython -c "import paddle, paddleocr; assert paddle.__version__ == '3.2.2'; assert paddleocr.__version__ == '3.6.0'; print('Pinned PaddleOCR environment verified.')"
if ($LASTEXITCODE -ne 0) { throw "Pinned PaddleOCR environment verification failed." }
Replace-DirectoryAtomically -StagedPath $ocrStaging -TargetPath $ocrVenv

# Initializing the worker once downloads the fixed PP-OCRv5 mobile models into
# the portable cache.  Never inherit a user profile cache: a later app launch
# must observe exactly the models provisioned under MemeSortData.
$priorPaddleCache = $env:PADDLE_PDX_CACHE_HOME
$priorPaddleSourceCheck = $env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK
try {
    $env:PADDLE_PDX_CACHE_HOME = Join-Path $dataRoot "models\paddleocr"
    $env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK = "True"
    New-Item -ItemType Directory -Force -Path $env:PADDLE_PDX_CACHE_HOME | Out-Null
    $null | & (Join-Path $ocrVenv "Scripts\python.exe") $ocrWorker --lang ch --device cpu
    if ($LASTEXITCODE -ne 0) { throw "Failed to provision the pinned PaddleOCR models." }
    if (-not (Get-ChildItem -LiteralPath $env:PADDLE_PDX_CACHE_HOME -Recurse -File | Select-Object -First 1)) {
        throw "Pinned PaddleOCR model provisioning did not populate the portable cache."
    }
}
finally {
    if ($null -eq $priorPaddleCache) { Remove-Item Env:PADDLE_PDX_CACHE_HOME -ErrorAction SilentlyContinue }
    else { $env:PADDLE_PDX_CACHE_HOME = $priorPaddleCache }
    if ($null -eq $priorPaddleSourceCheck) { Remove-Item Env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK -ErrorAction SilentlyContinue }
    else { $env:PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK = $priorPaddleSourceCheck }
}

& $sidecarPath --portable-root $portableRoot --write-runtime-activation
if ($LASTEXITCODE -ne 0) { throw "Failed to activate the verified portable runtime." }
Write-Host "Setup complete. Pinned Vulkan devices:"
& $llamaServer --list-devices
if ($LASTEXITCODE -ne 0) { throw "Pinned llama-server failed to enumerate devices." }
