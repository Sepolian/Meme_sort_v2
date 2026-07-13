# MemeSort

MemeSort is a local Windows image and GIF library. It imports media, generates embeddings and OCR locally, and supports text search, image search, similar-asset retrieval, and duplicate review.

The recommended and default inference stack is:

- `llama.cpp` with Vulkan, running the Qwen3-VL-Embedding 2B GGUF on AMD, Intel, or NVIDIA GPUs;
- PaddleOCR on CPU in a separate Python environment, using the PP-OCRv5 mobile Chinese detection and recognition models;
- a local FastAPI application and Web UI, with media, vectors, and OCR data stored in the user-selected library.

The previous Transformers/Safetensors CPU and CUDA profiles remain available for existing settings and index recipes. They are no longer the recommended installation path, and a new llama.cpp environment does not require PyTorch or `requirements-qwen.txt`.

## Validated environment

This repository currently pins and validates the following combination:

- Windows x64
- AMD Radeon 780M Graphics
- Python 3.13.14 for the main environment
- Python 3.12.13 for the isolated OCR environment
- llama.cpp `b9982` Windows Vulkan build
- Qwen3-VL-Embedding 2B `Q4_K_M` with an F16 multimodal projector
- PaddlePaddle 3.2.2 CPU with PaddleOCR 3.6.0

llama.cpp detects the 780M as `Vulkan0: AMD Radeon 780M Graphics`. Both text and image inputs produce 2048-dimensional embeddings.

## Quick setup

Prepare at least 5 GB of free disk space and make sure the AMD, Intel, or NVIDIA display driver provides a working Vulkan runtime. Run the following commands from the repository root:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup_windows_llama.ps1
```

The setup script:

1. uses an existing `uv`, or downloads a project-local copy from the official uv GitHub release;
2. creates `.venv` with Python 3.13 and installs MemeSort;
3. creates `.venv-ocr` with Python 3.12 and installs pinned CPU OCR dependencies;
4. downloads and verifies the llama.cpp `b9982` Windows Vulkan package;
5. downloads and verifies the pinned main GGUF and multimodal projector;
6. runs `llama-server --list-devices` to display the detected Vulkan GPU.

The model bundle is approximately 1.93 GB. Because the GGUF files are a community conversion, the setup script pins both filenames and SHA256 hashes and rejects silent replacements:

| File | SHA256 |
| --- | --- |
| `llama-b9982-bin-win-vulkan-x64.zip` | `b8c49b3ff732d663dbbf9b1fcefb1153816d072c89bc0579197cea5d19873616` |
| `Qwen.Qwen3-VL-Embedding-2B.Q4_K_M.gguf` | `42a4ebc629ecc6514649e12b1529b857f54900273bb854f853c970fb90edd09d` |
| `mmproj-Qwen.Qwen3-VL-Embedding-2B.f16.gguf` | `3f89a7768ffa6606935319f71bf56bb71871249ba549bf1080a0caea7a088613` |

Upstream sources: the llama.cpp [b9982 release](https://github.com/ggml-org/llama.cpp/releases/tag/b9982) and the [DevQuasar GGUF repository](https://huggingface.co/DevQuasar/Qwen.Qwen3-VL-Embedding-2B-GGUF).

## Launch and first-run setup

```powershell
.\start_memesort.ps1
```

The launcher automatically configures the project-local `llama-server.exe`. On the first visit to the setup page:

1. keep the default `Vulkan Balanced (llama.cpp)` profile;
2. keep the `Qwen3 2B` model;
3. verify that the model directory is discovered as `.models\gguf\qwen3-2b-q4_k_m`;
4. run the health check and confirm that it reports the physical GPU, a 2048d text smoke test, and an image smoke test;
5. select the library and import directories, then start indexing.

Existing library vectors are never mixed with the new GGUF vectors. Vulkan GGUF uses a separate recipe. Switching profiles creates the required rebuild jobs instead of writing Transformers and llama.cpp vectors into the same index recipe.

To use a llama.cpp installation outside the project, set the executable path before launch:

```powershell
$env:MEMESORT_LLAMA_SERVER = "D:\path\to\llama-server.exe"
.\start_memesort.ps1
```

## OCR

Paddle's Windows GPU wheels target CUDA and NVIDIA hardware and cannot use the 780M through Vulkan. The validated configuration therefore runs semantic embeddings on the 780M through Vulkan and runs OCR on CPU in the isolated `.venv-ocr` environment.

Default OCR configuration:

- `PP-OCRv5_mobile_det`
- `PP-OCRv5_mobile_rec`
- document orientation classification, document unwarping, and text-line orientation are disabled because they are unnecessary for meme images
- model files are cached in `.models\paddleocr` to avoid depending on Windows user-cache permissions

OCR models are downloaded automatically by the first OCR job. The environment can be overridden with:

```powershell
$env:MEMESORT_OCR_PYTHON = "$PWD\.venv-ocr\Scripts\python.exe"
$env:MEMESORT_OCR_DEVICE = "cpu"
$env:MEMESORT_OCR_LANG = "ch"
```

The worker protocol is explicitly UTF-8 on Windows. Results damaged by the encoding behavior in an older version are detected by their Unicode replacement characters and automatically queued for OCR reconstruction when the library is opened.

## Validation and evaluation

Run the focused backend and UI tests with:

```powershell
.venv\Scripts\python.exe -m unittest `
  tests.test_ocr_backend `
  tests.test_llama_cpp_backend `
  tests.test_web_static -v
```

`tests.test_library` still contains tests dedicated to the legacy Transformers backend. Install the legacy dependencies and run that suite only when maintaining the old backend. The primary llama.cpp path does not install PyTorch solely for those tests.

Evaluate still-image retrieval with local `example/` and `labels/labels.json` directories:

```powershell
.venv\Scripts\python.exe scripts\evaluate_still_image_search.py `
  --dataset-dir example `
  --labels-path labels\labels.json `
  --backend llama.cpp `
  --model-name-or-path .models\gguf\qwen3-2b-q4_k_m `
  --llama-server .runtime\llama.cpp-b9982-vulkan\llama-server.exe `
  --output .tmp_eval\llama_cpp_vulkan_still_eval.json
```

Evaluate GIF retrieval:

```powershell
.venv\Scripts\python.exe scripts\evaluate_gif_search.py `
  --dataset-dir gif_example `
  --labels-path labels\gif_labels.json `
  --backend llama.cpp `
  --model-name-or-path .models\gguf\qwen3-2b-q4_k_m `
  --llama-server .runtime\llama.cpp-b9982-vulkan\llama-server.exe `
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

Measured results for the current examples on the local Radeon 780M system:

| Evaluation | Result | Time |
| --- | --- | --- |
| 20 still images, semantic retrieval | R@1 95%; R@5/R@10 100% | 44.1s indexing; 0.384s average query |
| 10 GIFs, semantic retrieval | R@1/R@5/R@10 100% | 27.9s indexing; 0.395s average query |
| 20 still images, OCR | 86.0% line recall; 84.6% asset-any-hit | 13.0s inference; 0.649s per image |

These measurements are regression baselines for a small example set, not final throughput or generalization claims for a large library.

## Troubleshooting

Check whether Vulkan can see the GPU:

```powershell
.\.runtime\llama.cpp-b9982-vulkan\llama-server.exe --list-devices
```

If `Vulkan0` is absent, update the display driver and retry. Do not install the llama.cpp CUDA build for an AMD 780M; the shared backend uses the Windows Vulkan build.

If the health check reports a GGUF hash mismatch, delete the damaged file and rerun the setup script. Do not place a different quantization or conversion into the same recipe.

If the first OCR model download fails, verify access to the Paddle or Hugging Face model source, remove the incomplete model directory under `.models\paddleocr`, and retry. Do not switch PaddleOCR back to `gpu:0`; it runs on CPU for the 780M configuration.

## Repository layout

- `memesort_worker/`: application, indexing, retrieval, OCR coordination, Web API, and UI
- `scripts/setup_windows_llama.ps1`: Windows llama.cpp environment setup
- `scripts/evaluate_*.py`: still-image, GIF, and OCR evaluation tools
- `tests/`: worker, backend, and UI regression tests
- `CONTEXT.md`: domain terminology and architecture boundaries
- `.models/`, `.runtime/`, and `.venv*`: local artifacts excluded from Git
