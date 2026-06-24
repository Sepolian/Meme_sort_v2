@echo off
setlocal

set "REPO_ROOT=%~dp0"
set "PYTHON_PATH=%REPO_ROOT%.venv\Scripts\python.exe"

if not exist "%PYTHON_PATH%" (
  echo Missing virtual environment Python at "%PYTHON_PATH%"
  exit /b 1
)

call "%PYTHON_PATH%" -m pip show sentencepiece tiktoken >nul 2>&1
if errorlevel 1 (
  echo Missing Qwen tokenizer runtime dependencies in ".venv". Run:
  echo   "%PYTHON_PATH%" -m pip install -r "%REPO_ROOT%requirements-qwen.txt"
  exit /b 1
)

"%PYTHON_PATH%" -m memesort_worker desktop-shell
