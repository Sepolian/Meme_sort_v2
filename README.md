# MemeSort

MemeSort is a local Windows library for image and GIF assets. It keeps a managed library copy of imported media, creates semantic embeddings and OCR locally, and supports text search, image search, similar-asset retrieval, and duplicate review.

Semantic inference has one supported runtime: the pinned `llama.cpp` Vulkan build and pinned EmbeddingGemma 2 Q8_0 GGUF bundle declared by [runtime-manifest.json](runtime-manifest.json). It always uses `Vulkan0`; there is no CPU, CUDA, Transformers, custom-model, or external-server fallback. PaddleOCR remains an isolated CPU service for OCR.

## Supported environment

- Windows 10/11 x64
- A Vulkan-capable AMD (`0x1002`), Intel (`0x8086`), or NVIDIA (`0x10de`) GPU selected as `Vulkan0`
- Python 3.13.14 for the application and Python 3.12.13 for isolated OCR
- llama.cpp `b11457` Windows Vulkan build
- EmbeddingGemma 2 `Q8_0` GGUF with its matching `Q8_0` multimodal projector
- PaddlePaddle 3.2.2 CPU with PaddleOCR 3.6.0 / PP-OCRv5 mobile

The runtime health check validates the manifest activation, verifies the pinned artifacts, admits one of the supported GPU vendors at `Vulkan0`, and requires 768-dimensional text and image embeddings. The Runtime Descriptor and health diagnostics identify EmbeddingGemma 2 and b11457. Windows real-device setup and inference acceptance for this bundle remains a release gate; the historical Qwen Radeon 780M smoke test does not qualify it.

The managed server uses mean pooling, L2 normalization, float32 vectors, 2048-token context/batch/physical microbatch sizes, one parallel slot, 99 GPU layers, Flash Attention off, and `GGML_VK_DISABLE_F16=1` in the child environment. It retains the model's default visual budget. Text inputs are `task: search result | query: ` followed directly by the query, including the trailing space; image and GIF-frame inputs contain only media, with no text instruction.

## Setup

Reserve at least 5 GB of disk space and install a display driver with working Vulkan support. From the repository root:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup_windows_llama.ps1
```

Setup is the only supported installer for the semantic runtime. It reads `runtime-manifest.json`, then downloads and verifies the project-local uv tool, llama.cpp Vulkan archive, GGUF model and projector; creates `.venv` and `.venv-ocr`; writes the activation record; and displays `llama-server --list-devices`.

The main model and projector total 864,676,640 bytes (approximately 865 MB). Artifact sizes and SHA256 hashes are pinned in the manifest:

| File | Bytes | SHA256 |
| --- | ---: | --- |
| `llama-b11457-bin-win-vulkan-x64.zip` | 33,377,746 | `d01301582c711a69b9747b5984710d6ca99e57d95f680d3d33753cec570b4cb6` |
| `embeddinggemma-2-Q8_0.gguf` | 309,855,520 | `6f1bd4ac6c5df7444f9cca7ca36cafe6cfa34cd6f49fefb1e0b4be8143aed8bc` |
| `mmproj-Q8_0.gguf` | 554,821,120 | `90e7b0238009e2954f856f2081dcf7f35af026b64c765e98f4777053e1754460` |

The Windows archive was downloaded and checked against the official llama.cpp [b11457 release](https://github.com/ggml-org/llama.cpp/releases/tag/b11457). Model downloads use the immutable [Unsloth GGUF revision 031f0d4b](https://huggingface.co/unsloth/embeddinggemma-2-GGUF/tree/031f0d4b35536f69ab3509d4893c923264fcf253); its published sizes and hashes match the evaluated bundle. The projector also contains an audio encoder; supported Assets remain still images and GIFs.

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

```text
Double-click setup_portable_runtime.bat
```

The batch wrapper runs the packaged PowerShell script with a process-local execution-policy bypass. It reads the packaged manifest, verifies every downloaded Vulkan/GGUF artifact by size and SHA256, and writes only below `MemeSortData`. It creates `MemeSortData\runtime\ocr-venv` and provisions the fixed PP-OCRv5 mobile cache at `MemeSortData\models\paddleocr`; neither is present in the ZIP. Use `setup_portable_runtime.bat -Offline` only when the required verified downloads are already present in `MemeSortData\runtime\downloads`.

## Desktop development and verification

The portable build requires Windows x64, Python 3.13 with uv, Node.js 20 with npm, and the Rust stable/MSVC toolchain. From the repository root:

```powershell
uv sync --locked --group build
Set-Location desktop
npm ci
npm run lint
npm run typecheck
npm test
Set-Location src-tauri
cargo fmt --check
cargo test
cargo clippy --all-targets --all-features -- -D warnings
Set-Location ..\..
.\scripts\build_portable.ps1
.\scripts\test_portable_smoke.ps1
```

The smoke harness starts the packaged headless sidecar with no models installed, checks its one-time handshake and portable Library root, sends the explicit shutdown command, and confirms that the package did not include model or Vulkan runtime files.

## Launch and use

Run `MemeSort.exe` from the extracted portable folder, after its portable setup has completed. In the application:

1. Run the Vulkan health check and confirm the reported GPU vendor, `Vulkan0`, and 768d text and image smoke tests.
2. Import a local folder. Files are copied into the library; repeated content adds a source record rather than another asset.
3. Start indexing. One managed llama-server and one serialized inference queue serve all searches and background indexing; search jobs have priority but do not interrupt a running indexing call.

Changing the manifest is a developer upgrade, not an in-app setting. Update the artifact and model fields in `runtime-manifest.json`, rerun setup, and run the health check before indexing. Opening the Library activates the manifest-derived recipe. The Qwen-to-Gemma change resets the incompatible 2048d semantic vectors and old embedding jobs, then queues every Asset for new 768d embeddings in one transaction. Repeated activation keeps the same recipe and jobs; a queueing failure rolls the activation back.

Reindexing preserves Library Copies, Source Records, thumbnails, existing OCR results, and Accepted Duplicate Pairs. Pending and Failed Assets remain available for browsing, management, and retry. Semantic retrieval uses only the Active Index Recipe, so coverage returns as indexing completes; the old Qwen vectors cannot be reused or mixed with Gemma vectors. Existing OCR can still contribute to text results while semantic embeddings regenerate.

Still Assets keep 480px preprocessing. GIFs keep up to four sampled frames and appear as one Asset with their strongest Matched Frame. Text search combines semantic and OCR results, image search embeds the query's visual content, and similar-Asset search compares active-recipe embeddings. Near-duplicate review retains its adjustable 0.92 default and excludes Accepted Duplicate Pairs; it requires human review and does not delete Assets automatically.

## OCR

Semantic embeddings use Vulkan. OCR intentionally uses CPU in `.venv-ocr` for the repository workflow and `MemeSortData\runtime\ocr-venv` in the portable package, so it remains independent of the GPU vendor and the Vulkan inference path.

The default OCR stack is fixed to PaddleOCR PP-OCRv5 mobile (`PP-OCRv5_mobile_det` and `PP-OCRv5_mobile_rec`) on CPU, with document orientation, unwarping, and text-line orientation disabled. A missing environment is an explicit setup error, never a debug OCR fallback. Its cache is `.models\paddleocr` in the repository workflow and `MemeSortData\models\paddleocr` in a portable package; the worker protocol is UTF-8 on Windows.

## Validation and evaluation

The accepted replacement tradeoff was measured on one Linux Vega 11 Vulkan device with 30 Assets and 60 Embedding Items. Default-budget Gemma encoded those items 3.53 times faster and answered short keyword queries approximately 4.96 times faster than Qwen. Complete-description Recall@1 changed from 29/30 to 27/30; short-keyword Recall@1 with fixed OCR fusion changed from 36/43 to 35/43. These embedding timings exclude OCR, import, SQLite, and some preprocessing work. They do not promise equivalent retrieval quality, whole-import speedups, or Windows performance.

The 0.92 duplicate-review default is retained without recalibration. The saved comparison scanned all 435 unordered pairs in the 30-Asset sample and found the same two candidates for Qwen and default-budget Gemma, with changed scores. That sample has no duplicate ground truth and does not establish a calibrated threshold.

The 2026-10-07 implementation smoke used the shared Pinned Runtime, managed b11457 launch, and existing Library interfaces on Linux with AMD Radeon Vega 11 (RADV RAVEN). It produced 14 finite, normalized float32 vectors of exactly 768 dimensions, indexed two distinct still Assets and a four-frame GIF into six Embedding Items, and passed text/image/GIF-image search, similar-Asset retrieval, duplicate review, and Accepted Duplicate Pair exclusion. The two still vectors had cosine similarity 0.742881; an image query selected GIF frame 2 as the strongest match. One server served the session and stopped on close. The temporary Library was removed; the Windows manifest and all 33 original evaluation artifacts were unchanged, and no production Library was opened.

For Linux, the harness substituted local executable/model/activation paths and the native Vulkan loader, and bypassed Windows-only platform admission. It retained the manifest's Windows target, verified model hashes, prompt policy, and effective inference settings. OCR returned empty test results to isolate semantic inference; the automated Library regressions cover OCR fusion and preservation. Local checks used Python 3.12.3, NumPy 2.4.2, Pillow 10.2.0, and Linux llama.cpp 0.6.0-dev build 11457 (`5ad1c5da0`), rather than the Windows setup toolchain. This evidence qualifies the implemented semantic workflow on that Linux device; Windows and other GPU vendors remain unverified.

### Windows release qualification

Complete and retain this record on supported Windows x64 Vulkan0 hardware before releasing the replacement. Record each tested device and driver; a pass on one device does not qualify every AMD, Intel, or NVIDIA GPU.

| Check | Required evidence |
| --- | --- |
| Hardware admission | Windows build, GPU name/vendor ID, driver version, and matching native Vulkan/`llama-server --list-devices` Vulkan0 identity; supported AMD, Intel, or NVIDIA admission and clear rejection of an unsupported or absent device. |
| Repository and portable setup | Clean repository setup and portable setup as applicable; verified Windows archive/main/projector sizes and SHA256, correct managed paths, current activation fingerprint, and explicit errors for missing or tampered artifacts. |
| Current-session health | Descriptor identifies EmbeddingGemma 2, b11457, Vulkan0, and 768d; real text/image outputs are finite and normalized, stale activation/health cannot authorize indexing, and a new session requires its own health check. |
| Library and reindexing | Still and four-frame GIF indexing succeeds; a Qwen Library queues new semantic work without mixing dimensions and retains Library Copies, Source Records, OCR, and Accepted Duplicate Pairs. |
| Retrieval and review | Text search including Chinese OCR fusion, still/GIF image queries, one GIF Asset with the strongest Matched Frame, similar-Asset retrieval, exact-content coalescing, adjustable 0.92 duplicate review, and Keep Both exclusion. |
| Startup, precision, and ownership | Actual 2048 context/batch/microbatch, mean/L2, one slot, 99 layers, Flash Attention off, `GGML_VK_DISABLE_F16=1`, and no visual-budget overrides; distinct images yield distinct valid vectors; startup/precision failures are visible; idle unloading and application shutdown stop the owned process. |
| Performance and resources | Cold-start time, still/GIF encoding time, text/image query latency, and CPU/RAM/GPU-memory observations during indexing/search and after shutdown. Compare on that device before claiming a Windows speedup or whole-import improvement. |

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
.\.runtime\llama.cpp-b11457-vulkan\llama-server.exe --list-devices
```

If `Vulkan0` is absent or its vendor is not AMD, Intel, or NVIDIA, update the display driver or use a supported GPU. If activation or a GGUF hash check fails, rerun the setup script; do not replace an artifact manually. If OCR setup fails, remove an incomplete `.models\paddleocr` directory and retry its job with network access.

## Repository layout

- `runtime-manifest.json`: the sole developer-controlled semantic runtime and model definition
- `memesort_worker/`: application, indexing, retrieval, OCR coordination, and authenticated sidecar API
- `scripts/setup_windows_llama.ps1`: manifest-driven Windows runtime setup
- `scripts/evaluate_*.py`: still-image, GIF, and OCR evaluation tools
- `tests/`: Vulkan runtime, worker, OCR, and UI regression tests
- `CONTEXT.md`: domain terminology and architecture boundaries
- `.models/`, `.runtime/`, and `.venv*`: repository-local artifacts excluded from Git
