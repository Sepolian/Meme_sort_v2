@echo off
setlocal

set "REPO_ROOT=%~dp0"
set "PYTHON_PATH=%REPO_ROOT%.venv\Scripts\python.exe"
set "BUNDLED_SERVER=%REPO_ROOT%.runtime\llama.cpp-b9982-vulkan\llama-server.exe"
set "BUNDLED_MODEL=%REPO_ROOT%.models\gguf\qwen3-2b-q4_k_m"

if not exist "%PYTHON_PATH%" (
  echo Missing virtual environment Python at "%PYTHON_PATH%"
  exit /b 1
)

if not defined MEMESORT_LLAMA_SERVER set "MEMESORT_LLAMA_SERVER=%BUNDLED_SERVER%"

if not exist "%MEMESORT_LLAMA_SERVER%" (
  echo Missing llama.cpp Vulkan runtime at "%MEMESORT_LLAMA_SERVER%". Follow README.md setup steps.
  exit /b 1
)

if not exist "%BUNDLED_MODEL%" (
  echo Missing GGUF model bundle at "%BUNDLED_MODEL%". Follow README.md setup steps.
  exit /b 1
)

"%PYTHON_PATH%" -m memesort_worker desktop-shell
