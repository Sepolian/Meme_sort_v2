# MemeSort

MemeSort is a local Windows library for image and GIF assets. It keeps a managed library copy of imported media, creates semantic embeddings and OCR locally, and supports text search, image search, similar-asset retrieval, and duplicate review.

Semantic inference has one supported runtime: the pinned `llama.cpp` Vulkan build and pinned Qwen3-VL-Embedding GGUF bundle declared by [runtime-manifest.json](runtime-manifest.json). It always uses `Vulkan0`; there is no CPU, CUDA, Transformers, custom-model, or external-server fallback. PaddleOCR remains an isolated CPU service for OCR.

## Supported environment

- Windows 10/11 x64
- A Vulkan-capable AMD (`0x1002`), Intel (`0x8086`), or NVIDIA (`0x10de`) GPU selected as `Vulkan0`
- Python 3.13.14 for the application and Python 3.12.13 for isolated OCR
- llama.cpp `b9982` Windows Vulkan build
- Qwen3-VL-Embedding 2B `Q4_K_M` GGUF with its F16 multimodal projector
- PaddlePaddle 3.2.2 CPU with PaddleOCR 3.6.0 / PP-OCRv5 mobile

The runtime health check validates the manifest activation, verifies the pinned artifacts, admits one of the supported GPU vendors at `Vulkan0`, and makes both a text and an image embedding. The verified Radeon 780M smoke test produced 2048-dimensional vectors for both paths.

## Setup

Reserve at least 5 GB of disk space and install a display driver with working Vulkan support. From the repository root:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup_windows_llama.ps1
```

Setup is the only supported installer for the semantic runtime. It reads `runtime-manifest.json`, then downloads and verifies the project-local uv tool, llama.cpp Vulkan archive, GGUF model and projector; creates `.venv` and `.venv-ocr`; writes the activation record; and displays `llama-server --list-devices`.

The current semantic bundle is approximately 1.93 GB. Its archive names, sizes, and SHA256 hashes are all pinned in the manifest, including:

| File | SHA256 |
| --- | --- |
| `llama-b9982-bin-win-vulkan-x64.zip` | `b8c49b3ff732d663dbbf9b1fcefb1153816d072c89bc0579197cea5d19873616` |
| `Qwen.Qwen3-VL-Embedding-2B.Q4_K_M.gguf` | `42a4ebc629ecc6514649e12b1529b857f54900273bb854f853c970fb90edd09d` |
| `mmproj-Qwen.Qwen3-VL-Embedding-2B.f16.gguf` | `3f89a7768ffa6606935319f71bf56bb71871249ba549bf1080a0caea7a088613` |

Sources are the llama.cpp [b9982 release](https://github.com/ggml-org/llama.cpp/releases/tag/b9982) and the [DevQuasar GGUF repository](https://huggingface.co/DevQuasar/Qwen.Qwen3-VL-Embedding-2B-GGUF).

## Portable desktop package

The desktop distribution is portable-only: it has no MSI or NSIS installer. From a prepared development checkout, build it with:

```powershell
.\scripts\build_portable.ps1
```

The resulting `dist\MemeSort-portable.zip` expands to:

```text
MemeSort-portable/
  MemeSort.exe
  sidecar/
  MemeSortData/
    library/
    models/
    runtime/
```

The package does not include the managed Library, GGUF main model, multimodal projector, or llama.cpp Vulkan runtime. `MemeSortData` must remain beside `MemeSort.exe`; it is calculated from the executable location, not the current working directory or `%APPDATA%`.

After extracting the ZIP, install the separately downloaded runtime, semantic models, and CPU OCR environment in that same folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup_portable_runtime.ps1
```

The script reads the packaged manifest, verifies every downloaded Vulkan/GGUF artifact by size and SHA256, and writes only below `MemeSortData`. It creates `MemeSortData\runtime\ocr-venv` and provisions the fixed PP-OCRv5 mobile cache at `MemeSortData\models\paddleocr`; neither is present in the ZIP. Use `-Offline` only when the required verified downloads are already present in `MemeSortData\runtime\downloads`.

## Launch and use

```powershell
.\start_memesort.ps1
```

The launcher validates that the repository-local runtime matches `runtime-manifest.json`; it does not accept an external executable. In the application:

1. Run the Vulkan health check and confirm the reported GPU vendor, `Vulkan0`, and 2048d text and image smoke tests.
2. Import a local folder. Files are copied into the library; repeated content adds a source record rather than another asset.
3. Start indexing. One managed llama-server and one serialized inference queue serve all searches and background indexing; search jobs have priority but do not interrupt a running indexing call.

Changing the manifest is a developer upgrade, not an in-app setting. Update all relevant artifact and model fields in `runtime-manifest.json`, rerun setup, run the health check, then rebuild the active index. A changed recipe fingerprint (including a changed embedding dimension) transactionally resets incompatible semantic vectors and queues the library for the new recipe.

## OCR

Semantic embeddings use Vulkan. OCR intentionally uses CPU in `.venv-ocr` for the repository workflow and `MemeSortData\runtime\ocr-venv` in the portable package, so it remains independent of the GPU vendor and the Vulkan inference path.

The default OCR stack is fixed to PaddleOCR PP-OCRv5 mobile (`PP-OCRv5_mobile_det` and `PP-OCRv5_mobile_rec`) on CPU, with document orientation, unwarping, and text-line orientation disabled. A missing environment is an explicit setup error, never a debug OCR fallback. Its cache is `.models\paddleocr` in the repository workflow and `MemeSortData\models\paddleocr` in a portable package; the worker protocol is UTF-8 on Windows.

## Validation and evaluation

Run the complete automated suite:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

Evaluate still-image retrieval with local `example/` and `labels/labels.json` directories:

```powershell
.venv\Scripts\python.exe scripts\evaluate_still_image_search.py `
  --dataset-dir example `
  --labels-path labels\labels.json `
  --output .tmp_eval\llama_cpp_vulkan_still_eval.json
```

Evaluate GIF retrieval:

```powershell
.venv\Scripts\python.exe scripts\evaluate_gif_search.py `
  --dataset-dir gif_example `
  --labels-path labels\gif_labels.json `
  --output .tmp_eval\llama_cpp_vulkan_gif_eval.json
```

Evaluate OCR:

```powershell
.venv-ocr\Scripts\python.exe scripts\evaluate_paddle_ocr.py `
  --example-dir example `
  --labels-path labels\labels.json `
  --skip-gif `
  --device cpu `
  --output-path .tmp_eval\paddle_ocr_mobile_eval.json `
  --generated-labels-path .tmp_eval\paddle_ocr_mobile_generated_labels.json
```

## Troubleshooting

Check device discovery using the executable declared by the active manifest:

```powershell
.\.runtime\llama.cpp-b9982-vulkan\llama-server.exe --list-devices
```

If `Vulkan0` is absent or its vendor is not AMD, Intel, or NVIDIA, update the display driver or use a supported GPU. If activation or a GGUF hash check fails, rerun the setup script; do not replace an artifact manually. If OCR setup fails, remove an incomplete `.models\paddleocr` directory and retry its job with network access.

## Repository layout

- `runtime-manifest.json`: the sole developer-controlled semantic runtime and model definition
- `memesort_worker/`: application, indexing, retrieval, OCR coordination, Web API, and UI
- `scripts/setup_windows_llama.ps1`: manifest-driven Windows runtime setup
- `scripts/evaluate_*.py`: still-image, GIF, and OCR evaluation tools
- `tests/`: Vulkan runtime, worker, OCR, and UI regression tests
- `CONTEXT.md`: domain terminology and architecture boundaries
- `.models/`, `.runtime/`, and `.venv*`: repository-local artifacts excluded from Git
