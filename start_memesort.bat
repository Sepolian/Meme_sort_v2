@echo off
setlocal

set "REPO_ROOT=%~dp0"
set "PYTHON_PATH=%REPO_ROOT%.venv\Scripts\python.exe"
set "MANIFEST_PATH=%REPO_ROOT%runtime-manifest.json"

if not exist "%PYTHON_PATH%" (
  echo Missing virtual environment Python at "%PYTHON_PATH%"
  exit /b 1
)

if not exist "%MANIFEST_PATH%" (
  echo Missing runtime manifest at "%MANIFEST_PATH%"
  exit /b 1
)

"%PYTHON_PATH%" -m memesort_worker.runtime_activation validate --manifest "%MANIFEST_PATH%"
if errorlevel 1 (
  echo The pinned Vulkan runtime is not activated for runtime-manifest.json. Run scripts\setup_windows_llama.ps1.
  exit /b 1
)

"%PYTHON_PATH%" -m memesort_worker desktop-shell
