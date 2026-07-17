# MemeSort

MemeSort is a local Windows-first asset library for semantic meme and reaction-media retrieval. This document defines the durable domain language used by the code and documentation.

## Library

**Asset**:
One imported file managed by the library as a durable internal copy. An asset may be a still image or a GIF.
_Avoid_: Image, file, source file

**Library Copy**:
The managed internal file that defines an asset. Deleting, moving, or changing the original source file does not change the asset's identity.
_Avoid_: Cache file, temp copy

**Source Path**:
The original filesystem path recorded as import metadata. It is not the asset identity and the library does not depend on it remaining available.
_Avoid_: Asset path, canonical path

**Source Record**:
One recorded import origin for an asset. Equal content imported from multiple locations is one asset with multiple source records.
_Avoid_: Duplicate asset, alias file

**Derived Artifact**:
A regenerable output from an asset, such as a thumbnail, extracted GIF frame, embedding, or OCR result. Derived artifacts are deleted with the asset.
_Avoid_: Asset, original file

**Orphan Asset**:
An asset with no source records. Orphan assets are deleted with their derived artifacts.
_Avoid_: Unlinked asset, archived asset

**Pending Asset**:
An asset that exists in the library but does not yet have its active-recipe embeddings. It can be browsed and managed before it is searchable.
_Avoid_: Failed import, missing file

**Indexed Asset**:
An asset with the required active-recipe embeddings. It participates in semantic retrieval.
_Avoid_: Processed file, ready file

**Failed Asset**:
An asset whose active-recipe embedding job failed. It remains available for browsing, retry, and diagnostics but is not searchable.
_Avoid_: Broken import, corrupt asset

## Inference and search

**Pinned Runtime**:
The one semantic inference environment defined by `runtime-manifest.json`: the Windows x64 llama.cpp Vulkan build, explicit `Vulkan0`, and the pinned GGUF main model plus multimodal projector. It accepts only supported AMD, Intel, or NVIDIA Vulkan0 devices. There is no runtime selection or fallback.
_Avoid_: Runtime profile, backend option, device setting

**Runtime Descriptor**:
The read-only application description derived from the manifest. It exposes the backend, device, pinned llama.cpp build, model identity, embedding dimension and dtype, fingerprints, and preprocessing parameters. It is shown to the UI and is never persisted as user selection.
_Avoid_: Runtime settings, selected profile, selected model

**Model Artifact**:
The exact verified GGUF main model and multimodal projector loaded by the pinned runtime. A developer changes it only by editing the manifest and rerunning setup.
_Avoid_: Model picker, custom model path, interchangeable conversion

**Active Index Recipe**:
The manifest-derived compatibility definition for semantic embeddings: model artifact hashes, output dimension, preprocessing, embedding instruction, pooling, normalization, and storage dtype. Semantic retrieval uses only this recipe.
_Avoid_: Current profile, model choice

**Recipe Activation**:
The transactional installation of the manifest-derived active index recipe. If the recipe changes, incompatible semantic embeddings are reset and indexing work is queued; vectors with different dimensions are never mixed.
_Avoid_: Profile switching, fallback migration

**Embedding Item**:
One searchable unit derived from an asset for the active index recipe. A still image produces one item; a GIF can produce multiple frame items.
_Avoid_: Search result, asset vector

**Matched Frame**:
The GIF embedding item that contributes the strongest score for a query. The user-facing result remains the asset.
_Avoid_: Frame result, sub-asset

**Search Request**:
One UUID-scoped text or image retrieval operation. Pending search work may be cancelled independently, for example when the user changes pages. Search jobs have priority over waiting indexing work but never preempt a running inference call.
_Avoid_: Global search cancel, parallel model server

## OCR

**OCR Worker**:
The isolated Python 3.12 CPU process running PaddleOCR PP-OCRv5 mobile. It is deliberately separate from semantic Vulkan inference and can work on any supported semantic GPU vendor.
_Avoid_: Semantic backend, Vulkan OCR

## Invariants

- Asset identity is content hash; source records describe origins.
- Semantic inference is llama.cpp on manifest-pinned `Vulkan0` only.
- The manifest is the single developer upgrade surface for llama.cpp and GGUF artifacts.
- The setup script is the supported semantic-runtime installer and verifies artifact hashes.
- One llama-server and one serialized inference scheduler serve both indexing and search.
- Search priority is non-preemptive; batching is not implemented.
- OCR is CPU-only and isolated from the semantic runtime.
- Persisted health is informational. A health check in the current application session authorizes indexing.

## Example dialogue

Dev: "If the user deletes the original folder, do we lose the asset?"

Domain Expert: "No. The asset is the library copy. A source path is only origin metadata."

Dev: "What happens if the same image is imported from two folders?"

Domain Expert: "MemeSort keeps one asset and adds a second source record. Removing the last source record deletes the orphan asset and its derived artifacts."

Dev: "Can an asset exist before it is searchable?"

Domain Expert: "Yes. It is a pending asset until indexing creates the active-recipe embeddings."

Dev: "A new manifest model has a different vector dimension. Can we keep the old vectors?"

Domain Expert: "Not in the active recipe. Recipe activation resets incompatible semantic vectors and queues every asset for the new dimension."

Dev: "Can the application fall back to CPU or a different GPU backend?"

Domain Expert: "No. The runtime health check must admit the manifest-pinned Vulkan0 device before indexing starts."
