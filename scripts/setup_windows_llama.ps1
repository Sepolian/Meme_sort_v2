param(
    [switch]$Offline
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = [IO.Path]::GetFullPath(
    (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
)
$manifestPath = Join-Path $repoRoot "runtime-manifest.json"
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json

function Resolve-RepoPath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)
    if ([IO.Path]::IsPathRooted($RelativePath)) {
        throw "Manifest path must be repository-relative: $RelativePath"
    }
    $fullPath = [IO.Path]::GetFullPath(
        (Join-Path $repoRoot ($RelativePath -replace '/', [IO.Path]::DirectorySeparatorChar))
    )
    $rootPrefix = $repoRoot.TrimEnd('\') + '\'
    if (-not $fullPath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Manifest path escapes the repository: $RelativePath"
    }
    return $fullPath
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
            ($_.ExecutablePath -and
                [IO.Path]::GetFullPath($_.ExecutablePath).Equals(
                    $PinnedServer,
                    [StringComparison]::OrdinalIgnoreCase
                )) -or
            ($_.Name -match '^(MemeSort|python|pythonw)(\.exe)?$' -and
                $_.CommandLine -and
                $_.CommandLine.IndexOf($repoRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0)
        }
    }
    catch {
        $blocking = Get-Process -Name "llama-server", "MemeSort" -ErrorAction SilentlyContinue
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
        catch {
            Write-Warning $_
        }
    }
    if ($Offline) {
        throw "Offline setup cannot repair missing or invalid artifact: $Path"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    $partialPath = "$Path.part"
    Write-Host "Downloading pinned artifact: $Url"
    & curl.exe -L --fail --retry 3 --continue-at - --output $partialPath $Url
    if ($LASTEXITCODE -ne 0) {
        throw "Download failed: $Url"
    }
    Assert-VerifiedFile -Path $partialPath -Sha256 $Sha256 -SizeBytes $SizeBytes
    Move-Item -Force -LiteralPath $partialPath -Destination $Path
}

function Replace-DirectoryAtomically {
    param(
        [Parameter(Mandatory = $true)][string]$StagedPath,
        [Parameter(Mandatory = $true)][string]$TargetPath
    )
    $rootPrefix = $repoRoot.TrimEnd('\') + '\'
    foreach ($path in @($StagedPath, $TargetPath)) {
        $resolved = [IO.Path]::GetFullPath($path)
        if (-not $resolved.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to replace directory outside repository: $resolved"
        }
    }
    $backupPath = "$TargetPath.backup-$([guid]::NewGuid().ToString('N'))"
    $hadTarget = Test-Path -LiteralPath $TargetPath
    try {
        if ($hadTarget) {
            Move-Item -LiteralPath $TargetPath -Destination $backupPath
        }
        Move-Item -LiteralPath $StagedPath -Destination $TargetPath
        if ($hadTarget) {
            Remove-Item -Recurse -Force -LiteralPath $backupPath
        }
    }
    catch {
        if ((-not (Test-Path -LiteralPath $TargetPath)) -and (Test-Path -LiteralPath $backupPath)) {
            Move-Item -LiteralPath $backupPath -Destination $TargetPath
        }
        throw
    }
}

function Get-ArtifactPath {
    param($Artifact)
    return Join-Path $downloadRoot ([string]$Artifact.filename)
}

Assert-WindowsX64

$downloadRoot = Resolve-RepoPath ([string]$manifest.paths.download_dir)
$activationRecord = Resolve-RepoPath ([string]$manifest.paths.activation_record)
$uvRoot = Resolve-RepoPath ([string]$manifest.toolchain.uv.install_dir)
$llamaRoot = Resolve-RepoPath ([string]$manifest.llama_cpp.install_dir)
$modelRoot = Resolve-RepoPath ([string]$manifest.model.install_dir)
$llamaServer = Join-Path $llamaRoot ([string]$manifest.llama_cpp.executable)
$mainModel = Join-Path $modelRoot ([string]$manifest.model.main.filename)
$projectorModel = Join-Path $modelRoot ([string]$manifest.model.projector.filename)
$mainVenv = Join-Path $repoRoot ".venv"
$ocrVenv = Join-Path $repoRoot ".venv-ocr"

Assert-NoManagedProcess -PinnedServer $llamaServer
New-Item -ItemType Directory -Force -Path $downloadRoot, $modelRoot | Out-Null
Remove-Item -Force -LiteralPath $activationRecord -ErrorAction SilentlyContinue

$uvArchive = Get-ArtifactPath $manifest.toolchain.uv.archive
Get-VerifiedFile `
    -Url ([string]$manifest.toolchain.uv.archive.url) `
    -Path $uvArchive `
    -Sha256 ([string]$manifest.toolchain.uv.archive.sha256) `
    -SizeBytes ([long]$manifest.toolchain.uv.archive.size_bytes)

$stagingId = [guid]::NewGuid().ToString('N')
$uvStaging = "$uvRoot.staging-$stagingId"
New-Item -ItemType Directory -Force -Path $uvStaging | Out-Null
Expand-Archive -LiteralPath $uvArchive -DestinationPath $uvStaging
$stagedUv = Join-Path $uvStaging "uv.exe"
if (-not (Test-Path -LiteralPath $stagedUv -PathType Leaf)) {
    throw "Pinned uv archive did not contain uv.exe"
}
Replace-DirectoryAtomically -StagedPath $uvStaging -TargetPath $uvRoot
$uv = Join-Path $uvRoot "uv.exe"
$uvVersion = (& $uv --version)
if ($uvVersion -notmatch [regex]::Escape([string]$manifest.toolchain.uv.version)) {
    throw "Pinned uv version mismatch. Expected $($manifest.toolchain.uv.version), got $uvVersion"
}

$llamaArchive = Get-ArtifactPath $manifest.llama_cpp.archive
Get-VerifiedFile `
    -Url ([string]$manifest.llama_cpp.archive.url) `
    -Path $llamaArchive `
    -Sha256 ([string]$manifest.llama_cpp.archive.sha256) `
    -SizeBytes ([long]$manifest.llama_cpp.archive.size_bytes)
$llamaStaging = "$llamaRoot.staging-$stagingId"
New-Item -ItemType Directory -Force -Path $llamaStaging | Out-Null
Expand-Archive -LiteralPath $llamaArchive -DestinationPath $llamaStaging
if (-not (Test-Path -LiteralPath (Join-Path $llamaStaging ([string]$manifest.llama_cpp.executable)))) {
    throw "Pinned llama.cpp archive did not contain $($manifest.llama_cpp.executable)"
}
Replace-DirectoryAtomically -StagedPath $llamaStaging -TargetPath $llamaRoot

Get-VerifiedFile `
    -Url ([string]$manifest.model.main.url) `
    -Path $mainModel `
    -Sha256 ([string]$manifest.model.main.sha256) `
    -SizeBytes ([long]$manifest.model.main.size_bytes)
Get-VerifiedFile `
    -Url ([string]$manifest.model.projector.url) `
    -Path $projectorModel `
    -Sha256 ([string]$manifest.model.projector.sha256) `
    -SizeBytes ([long]$manifest.model.projector.size_bytes)

$uvOffline = @()
if ($Offline) {
    $uvOffline = @("--offline")
}
& $uv python install ([string]$manifest.toolchain.python.main_version) @uvOffline
& $uv python install ([string]$manifest.toolchain.python.ocr_version) @uvOffline

$mainStaging = "$mainVenv.staging-$stagingId"
$ocrStaging = "$ocrVenv.staging-$stagingId"
& $uv venv $mainStaging --python ([string]$manifest.toolchain.python.main_version) --managed-python
$mainPython = Join-Path $mainStaging "Scripts\python.exe"
$priorProjectEnvironment = $env:UV_PROJECT_ENVIRONMENT
try {
    $env:UV_PROJECT_ENVIRONMENT = $mainStaging
    & $uv sync --frozen --project $repoRoot --python $mainPython @uvOffline
}
finally {
    if ($null -eq $priorProjectEnvironment) {
        Remove-Item Env:UV_PROJECT_ENVIRONMENT -ErrorAction SilentlyContinue
    }
    else {
        $env:UV_PROJECT_ENVIRONMENT = $priorProjectEnvironment
    }
}
& $uv venv $ocrStaging --python ([string]$manifest.toolchain.python.ocr_version) --managed-python
$ocrPython = Join-Path $ocrStaging "Scripts\python.exe"
# PaddlePaddle provides its Windows CPU wheel through its own package index.
# PaddleOCR itself (and its Python dependencies) comes from PyPI.  Installing
# both from the Paddle index can leave a successfully-created but empty OCR
# virtual environment because PowerShell does not turn a native-command exit
# code into an exception by itself.
& $uv pip install --python $ocrPython --index "https://www.paddlepaddle.org.cn/packages/stable/cpu/" "paddlepaddle==3.2.2" @uvOffline
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install pinned PaddlePaddle CPU runtime."
}
& $uv pip install --python $ocrPython -r (Join-Path $repoRoot "requirements-ocr.txt") @uvOffline
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install pinned PaddleOCR worker dependencies."
}
& $ocrPython -c "import paddle, paddleocr; assert paddle.__version__ == '3.2.2'; assert paddleocr.__version__ == '3.6.0'; print('Pinned PaddleOCR environment verified.')"
if ($LASTEXITCODE -ne 0) {
    throw "Pinned PaddleOCR environment verification failed."
}

Replace-DirectoryAtomically -StagedPath $mainStaging -TargetPath $mainVenv
Replace-DirectoryAtomically -StagedPath $ocrStaging -TargetPath $ocrVenv
$mainPython = Join-Path $mainVenv "Scripts\python.exe"

& $mainPython -m memesort_worker.runtime_activation write --manifest $manifestPath
& $mainPython -m memesort_worker.runtime_activation validate --manifest $manifestPath

Write-Host "Setup complete. Pinned Vulkan devices:"
& $llamaServer --list-devices
if ($LASTEXITCODE -ne 0) {
    throw "Pinned llama-server failed to enumerate devices."
}
