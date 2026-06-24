# MemeSort

MemeSort is a Windows-first local asset library for importing, indexing, and searching still images and GIFs with semantic retrieval.

The current codebase ships as a Python worker plus a local web UI. It manages a durable library copy of imported media, keeps background indexing state in SQLite, and supports text search, image similarity, duplicate review, and optional local OCR enrichment.

## Current Scope

- Windows-first local application flow
- Managed library import with copy-on-import semantics
- Duplicate coalescing through shared assets and multiple source records
- Semantic retrieval with Qwen3-VL embedding backends
- Local web UI for status, library browsing, search, duplicate review, and diagnostics
- Optional PaddleOCR worker integration for OCR text extraction

## Project Layout

- `memesort_worker/`: application code, worker logic, API surface, desktop shell, and static frontend
- `scripts/`: packaging helpers, retrieval evaluation scripts, and OCR worker entrypoint
- `tests/`: unit and integration-style tests for the worker and retrieval flow
- `docs/adr/`: architecture decision records
- `CONTEXT.md`: project domain language and core concepts

## Requirements

- Windows
- Python 3.13
- A project-local virtual environment in `.venv`

Base dependencies are defined in `pyproject.toml`.

Qwen tokenizer/runtime extras are listed in `requirements-qwen.txt`.

Optional OCR support uses a separate `.venv-ocr` environment and `scripts/paddle_ocr_worker.py`.

## Quick Start

Create the main environment:

```powershell
uv venv .venv
.venv\Scripts\python -m pip install -e .
```

Install optional Qwen runtime dependencies when using the real embedding backend:

```powershell
.venv\Scripts\python -m pip install -r requirements-qwen.txt
```

Run tests:

```powershell
.venv\Scripts\python -m unittest tests.test_library
```

Launch the local app:

```powershell
.\start_memesort.ps1
```

Or directly:

```powershell
.venv\Scripts\python -m memesort_worker desktop-shell
```

## OCR

OCR is optional and local-first.

The worker can delegate OCR to `scripts/paddle_ocr_worker.py` through a dedicated Python environment configured by `MEMESORT_OCR_PYTHON`. If no OCR runtime is available, the app can fall back to a debug OCR backend for development.

## Evaluation Scripts

The repository keeps retrieval and OCR evaluation scripts in `scripts/`, but does not include local evaluation datasets or generated evaluation outputs.

If you want to run those scripts, provide your own local dataset directories and label files.

## Notes

- The repository is intended to start clean, without checked-in runtime environments, model caches, build outputs, or generated evaluation artifacts.
- `docs/adr/` and `CONTEXT.md` document the current architecture and domain language.
