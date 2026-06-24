$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoRoot ".venv\Scripts\python.exe"
$specPath = Join-Path $repoRoot "memesort_desktop.spec"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "Missing virtual environment Python at $pythonPath"
}

& $pythonPath -m pip show pyinstaller *> $null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed in .venv. Install it with: & '$pythonPath' -m pip install pyinstaller"
}

Push-Location $repoRoot
try {
    & $pythonPath -m PyInstaller --noconfirm --clean $specPath
}
finally {
    Pop-Location
}

Write-Host "Bundle built under dist\\MemeSort"
