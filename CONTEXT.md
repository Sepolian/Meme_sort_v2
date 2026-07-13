# MemeSort

MemeSort is a local Windows-first asset library for semantic meme and reaction-media retrieval. It exists to give the project a stable shared language for the library concepts, independent of implementation choices.

## Language

### Library

**Asset**:
One imported file managed by the library as a durable internal copy. An asset may be a still image or a GIF, with future support for other media types.
_Avoid_: Image, file, source file

**Derived Artifact**:
A non-source output generated from an asset, such as a thumbnail, contact sheet, extracted frame, or embedding. Derived artifacts can be regenerated and are deleted with the asset.
_Avoid_: Asset, original file

**Source Path**:
The original filesystem path an asset was imported from, stored as metadata only. It is not the asset's identity and the library does not depend on it remaining valid.
_Avoid_: Asset path, canonical path

**Source Record**:
One recorded import origin for an asset. A single asset may have many source records when the same content is imported multiple times from different locations.
_Avoid_: Duplicate asset, alias file

**Library Copy**:
The managed internal file copy that defines the asset in the library. Deleting, moving, or changing the original source file does not change the asset's identity.
_Avoid_: Cache file, temp copy

**Orphan Asset**:
A library asset with no remaining source records. Orphan assets are not kept in the library; when the last source record is removed, the asset and its derived artifacts are deleted.
_Avoid_: Unlinked asset, archived asset

**Indexed Asset**:
An asset whose required search embeddings exist for the active compatible index recipe. Indexed assets participate in semantic retrieval.
_Avoid_: Processed file, ready file

**Pending Asset**:
An asset that already exists in the library but is not yet an indexed asset. A pending asset can be browsed and managed before it becomes searchable.
_Avoid_: Failed import, missing file

**Failed Asset**:
An asset whose required embeddings for the active index recipe failed to complete. A failed asset remains in the library for browsing, retry, and diagnostics, but it is not searchable.
_Avoid_: Broken import, corrupt asset

### Search

**Runtime Profile**:
A user-selectable execution mode that defines how embeddings are produced and validated on a machine, such as `cpu-low-memory` or `cuda-balanced`. A runtime profile influences the active index recipe but is not itself the compatibility boundary.
_Avoid_: Device, backend, recipe

**Inference Backend**:
The engine contract used to execute an embedding model, such as Transformers/PyTorch or llama.cpp. A backend is selected by a runtime profile; application indexing and search code should not depend on backend-specific request formats.
_Avoid_: Runtime profile, model format

**Model Artifact**:
The concrete files loaded by an inference backend, such as a Hugging Face Safetensors snapshot or a GGUF main model plus multimodal projector. Quantization and conversion differences may make artifacts from the same embedding family incompatible for indexing.
_Avoid_: Embedding family, runtime profile

**Active Index Recipe**:
The single compatibility definition that determines which embeddings are current for search. Semantic retrieval uses one active index recipe at a time.
_Avoid_: Current model, active profile

**Stale Embedding**:
An embedding that was created under a different index recipe from the active one. Stale embeddings may be retained temporarily for reindex or rollback purposes, but they do not participate in current search.
_Avoid_: Legacy result, fallback vector

**Embedding Family**:
A named embedding model line that can have multiple incompatible variants, such as Qwen3-VL-Embedding 2B and 8B. Variants in the same family may still require separate index recipes when output dimensions or preprocessing contracts differ.
_Avoid_: Same model, drop-in replacement

**Embedding Item**:
A single searchable unit derived from an asset for one index recipe. A still image may contribute one embedding item, while a GIF may contribute multiple frame embedding items.
_Avoid_: Search result, asset vector

**Matched Frame**:
The GIF frame whose embedding item contributes the strongest score for a query. A matched frame explains why a GIF asset ranked well, but it is not itself a user-facing search result.
_Avoid_: Frame result, sub-asset

## Flagged Ambiguities

- **Asset vs Image**: Use `asset` for the durable library object. Use `image` only for a still-image media subtype or a specific embedding item kind.
- **Runtime Profile vs Active Index Recipe**: Use `runtime profile` for the user-facing execution choice. Use `active index recipe` for the compatibility boundary that decides whether embeddings are current or stale.
- **Embedding Family vs Model Artifact**: A 2B Qwen3-VL model can exist as both Safetensors and quantized GGUF artifacts. Do not assume their vectors are index-compatible merely because they share a model family.

## Example Dialogue

Dev: "If the user deletes the original folder, do we lose the asset?"

Domain Expert: "No. The asset is the library copy. The source path is only metadata about where it came from."

Dev: "So duplicate names from different folders can still become separate assets?"

Domain Expert: "Only if the content is actually different. If the content hash matches an existing asset, we keep one asset and add another source record."

Dev: "What if the user removes the last remaining source record?"

Domain Expert: "Then the asset should not survive as an orphan. We delete the asset and everything derived from it."

Dev: "Can an asset exist before it is searchable?"

Domain Expert: "Yes. A pending asset already exists in the library, but only an indexed asset participates in semantic search."

Dev: "If a GIF only gets some of its required frame embeddings, is it indexed?"

Domain Expert: "No. That asset stays failed for the current recipe. Partial embedding output can help retry or diagnostics, but it does not make the asset searchable."

Dev: "What happens after switching from one runtime profile to another?"

Domain Expert: "If the active index recipe changes, the old embeddings become stale. They can be kept temporarily, but current search only uses embeddings from the active recipe."

Dev: "When a GIF matches, is the result the frame or the GIF?"

Domain Expert: "The result is always the asset. A matched frame only explains which part of the GIF made that asset rank."
