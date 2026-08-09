param(
    [string]$PortableRoot = (Join-Path (Split-Path -Parent $PSScriptRoot) "dist\MemeSort-portable")
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$portableRoot = [IO.Path]::GetFullPath($PortableRoot)
$sidecar = Join-Path $portableRoot "sidecar\memesort-sidecar-x86_64-pc-windows-msvc.exe"
foreach ($required in @(
    "MemeSort.exe",
    "runtime-manifest.json",
    "requirements-ocr.txt",
    "setup_portable_runtime.ps1",
    "scripts\paddle_ocr_worker.py",
    "MemeSortData\library",
    "MemeSortData\models",
    "MemeSortData\runtime"
)) {
    $path = Join-Path $portableRoot $required
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Portable smoke test is missing $required at $path"
    }
}
if (-not (Test-Path -LiteralPath $sidecar -PathType Leaf)) {
    throw "Portable smoke test is missing the sidecar at $sidecar"
}

$unmanagedArtifacts = Get-ChildItem -LiteralPath $portableRoot -Recurse -File | Where-Object {
    $_.Name -match '\.gguf$' -or $_.FullName -match 'llama\.cpp'
}
if ($unmanagedArtifacts) {
    throw "Portable package must not contain model or Vulkan runtime artifacts: $($unmanagedArtifacts[0].FullName)"
}

# Starting with no semantic runtime must still produce the authenticated host
# handshake, and EOF after the explicit shutdown control message must leave no
# sidecar process behind.
$shutdownMessage = '{"command":"shutdown"}'
$handshakeLines = @($shutdownMessage | & $sidecar --portable-root $portableRoot)
if ($LASTEXITCODE -ne 0) {
    throw "Packaged sidecar smoke test failed with exit code $LASTEXITCODE"
}
if ($handshakeLines.Count -ne 1) {
    throw "Packaged sidecar must emit exactly one handshake line; got $($handshakeLines.Count)"
}
$handshake = $handshakeLines[0] | ConvertFrom-Json
if ($handshake.protocol_version -ne 1 -or -not ([string]$handshake.origin).StartsWith("http://127.0.0.1:")) {
    throw "Packaged sidecar emitted an invalid handshake."
}
$expectedLibrary = Join-Path $portableRoot "MemeSortData\library"
if ([IO.Path]::GetFullPath([string]$handshake.library_root) -ne $expectedLibrary) {
    throw "Packaged sidecar resolved the wrong Library Root: $($handshake.library_root)"
}

Write-Host "Portable smoke test passed: $portableRoot"
