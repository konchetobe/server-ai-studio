# The public Runpod worker — in-depth reference

Source: `github.com/huchukato/runpod-minimax-h3` (main, read 2026-11-05).
Purpose: document how the public worker is built and what it expects, for
debugging and for keeping the optional Vast.ai bootstrap equivalent.

---

## 1. Layers

### Image

```
FROM huchukato/comfyui-base:cu130
```

CUDA **13.0**, baked ComfyUI at `/opt/comfyui-baked`, Python venv at `/workspace/runpod-slim/ComfyUI/.venv-cu130`.

**12 custom node packs**, cloned at build time:

| pack | role |
|---|---|
`ComfyUI-VideoHelperSuite` | `VHS_VideoCombine` — the video+audio muxer
`ComfyUI-Easy-Use` | misc utilities, face detailer host
`ComfyUI-QwenVL-Mod` | `QwenVL_Unified` — prompt expander (optional at runtime)
`ComfyUI-RIFE-TensorRT-Auto` | frame interpolation (TensorRT)
`ComfyUI-Upscaler-TensorRT-Auto` | 2× upscale (TensorRT)
`ComfyUI-TagForge` | wildcards/tags
`ComfyUI-PerfectVideoResolution` | model-aware width/height from an aspect preset
`ComfyUI-MiniMaxH3-Director-Enhanced` | storyboard/Director graphs
`ComfyUI-Pixaroma` | **the image loaders used for references**
`ComfyUI-KJNodes` | sampling helpers, previews
`ComfyUI-Impact-Pack` / `-Subpack` | face detailer, SAM

Plus `libgl1 libglib2.0-0` (VideoHelperSuite's OpenCV import fails without them, and a failed import unregisters the whole pack → `missing_node_type`), `transformers>=5.2.0` (no-deps), `runpod>=1.12`, `requests`, `boto3`, `huggingface_hub[cli]`, `hf_transfer`.

### Entrypoint (`serverless-entrypoint.sh`)

1. **Materialise** baked ComfyUI into `/workspace/runpod-slim/ComfyUI` if absent.
2. Write `extra_model_paths.yaml` pointing every model class at **`/runpod-volume/models`** (`diffusion_models`, `text_encoders`, `loras`, `vae`, `LLM`, `ultralytics/bbox`, `sams`, `vae_approx`, `upscale_models`…).
3. **Patch `build_sam.py`** in place: `torch.load(f)` → `torch.load(f, weights_only=False)` — the legacy SAM checkpoint otherwise fails on torch ≥ 2.6.
4. **Force re-download** of `sam_vit_b_01ec64.pth` if its size ≠ `375042383` bytes (a corrupt early copy would otherwise be skipped as "present").
5. **Populate the volume** from `models-manifest.txt` unless `VOLUME_AUTOPOPULATE=false`; concurrent workers serialise on a `flock`.
6. **Redirect TensorRT engines** to the volume: `models/tensorrt` becomes a symlink to `/runpod-volume/models/tensorrt`, so engines compile once ever. Safe only because the endpoint is pinned to one GPU pool.
7. `exec python3 /opt/serverless/handler.py`.

---

## 2. Handler contract

Input fields (from `handler.py` docs):

```json
{
  "prompt_graph": { ... },        // API-format workflow; optional if a template name is given
  "prompt": "six-part text",
  "images": ["https://… or base64"],   // fills LoadImage nodes in id order
  "video": "https://… or base64",      // fills VHS_LoadVideo
  "seconds": 10,
  "video_size": "1344x768",
  "ref_image_size": "match" | "max",
  "enhance": false,
  "upscale": false,
  "rife": false,
  "seed": 12345,
  "job_name": "scene-01-a",
  "s3Config": { ... }             // optional: offload >10 MB results to S3/volume
}
```

Output: `{"outputs":[{"filename","b64"}], "texts":{…}}`; results over `OUTPUT_INLINE_MAX_MB` (10 MB) or over the 15 MB budget are uploaded to `s3Config` and returned as a presigned URL. **Runpod drops results over ~20 MB.**

### Presets (`MINIMAX_CONFIGS`)

| preset | UNET | steps | sampler | scheduler | LoRA | tau |
|---|---|---|---|---|---|---|
`native` | fl2va | 20 | res_multistep | simple | — | — |
`native_turbo` | fl2va | 8 | euler | simple | fl2v_turbo | 1.3 |
**`r2va_singularity`** | Singularity | **20** | res_multistep | simple | — | — |
**`r2va_singularity_turbo`** | Singularity | **6** | **euler** | **beta** | ref2v_turbo_4step | **1.3** |
`10eros` / `10eros_turbo` | 10eros | 20 / 8 | … | … | … | 1.3 |

Both Singularity presets set `sigma_off: True` — the `MiniMaxH3SigmaShift` node is disabled.

---

## 3. The R2V workflow (`MiniMaxH3-Singularity-R2VA.json`, 38 nodes)

| node | class | role |
|---|---|---|
`105:6` | `UNETLoader` | Singularity ref2va pruned int8 |
`105:213` | `LoraLoaderModelOnly` | ref2v turbo 4-step, strength 1.0 |
`105:13` | `CLIPLoader` | Qwen3-VL-32B heretic NVFP4, `type: minimax` |
`105:11` / `105:24` | `VAELoader` ×2 | video fp16 / audio fp32 |
`105:262` | `ModelAttentionBackend` | **comfy kitchen attention** |
`105:255` | `BlockSparseAttention` | **sol-attn**, `selection.tau 1`, start 0.2, end 1, `min_tokens 12288`, `extra_tokens 256`, `sink_conditioning exact_kv_and_rows` |
`105:9` | `BasicScheduler` | **beta**, **steps 6**, denoise 1 |
`105:156` | `KSamplerSelect` | **euler** |
`105:16` | `BasicGuider` | model + positive |
`105:15` | `RandomNoise` | seed |
`105:14` | `SamplerCustomAdvanced` | sampler |
`105:10` / `105:23` | `VAEDecode` / `VAEDecodeAudio` | latent → image / audio |
`220` | `ComfyUI-PerfectVideoResolution` | `MiniMax H3`, aspect `3:4`, native **864×1184**, `divisible_by 32`, `use_image_aspect true` |
`259`, `260` | `PixaromaLoadImageMini` | references 1–2 (**state: `max_mp 1`, `longest_side 1024`**) |
`261` | `PixaromaImageInfo` | feeds `ref_image_0` |
`264` | `PixaromaGroupSwitch` | reference group toggle |
`265` | `PixaromaShowText` | the prompt preview node |
`126` | `VHS_VideoCombine` | **frame_rate 48**, h264-mp4, crf 20, audio from `105:23` |
`254:176/177/185/186` | RIFE + upscaler TensorRT | post chain: `save.images ← rife.frames ← upscale.images ← VAEDecode` |
`250`,`251` | Pixaroma timer/monitor | telemetry |

### Reference pipeline (this is the part that matters)

- References go through **`PixaromaLoadImageMini`**, whose state caps them at **`max_mp: 1`, `longest_side: 1024`** — i.e. every reference is downscaled to ≤1024 px on the long edge before conditioning.
- The handler uploads each image and patches, per loader node:
  `image`, `LoadImageMiniState`, `LoadImagePixState.orig_name`.
- Slot order is by node id: `259` → `ref_image_0` (via `PixaromaImageInfo`), `260` → `ref_image_1`, then further slots.
- Unused loaders are dropped and any dependent `PixaromaImageInfo` node is pruned so the graph stays valid.

### Post-processing rewiring

- `upscale = false` → `rife.frames` is rewired to the VAE decode output.
- `rife = false` → `save.images` is rewired to RIFE's own input.
- `VHS_VideoCombine.frame_rate` is **halved** (48 → 24) when RIFE is off, because the saver bakes the interpolated rate.

---

## 4. Models (`models-manifest.txt`, 16 entries, ~100 GB)

| subdir | file |
|---|---|
`diffusion_models` | `minimax_h3_fl2va_pruned_int8_convrot`
`diffusion_models` | `Minimax-h3_Singularity_ref2va_Pruned_v1.3_int8`
`text_encoders` | `qwen3vl_32b_heretic_minimax_h3_nvfp4`
`loras` | fl2v turbo 8-step 768p · ref2v turbo 4-step · character swap · realism people
`ultralytics/bbox`, `sams` | face detector + SAM
`vae` | video fp16, audio fp32
`vae_approx` | `taeh3`
`LLM/GGUF` | Qwen3.5-9B Defiant-Fable Q6_K + mmproj
`tensorrt` | prebuilt RIFE + 2× upscaler engines (arch-specific)

---

## 5. Parity matrix — public worker vs our Vast build

| item | public worker | our Vast box | verdict |
|---|---|---|---|
references | `PixaromaLoadImageMini`, **1024 px cap** | stock `LoadImage`, full size | **differs** |
`ref_image_size` | passed through from the job | `max` on the 20-set, `match` on one test | differs per run |
steps (turbo) | **6** | **4** | **differs** |
`BlockSparseAttention.selection.tau` | **1.3** | 1 | **differs** |
sampler/scheduler | euler / beta | euler / beta | ✅ match |
sigma shift | disabled (`sigma_off`) | absent (equivalent) | ✅ |
UNET | Singularity **v1.3** pruned int8 | **v2** pruned int8 | **differs** |
custom nodes | 12 packs | none | **differs** |
resolution node | PerfectVideoResolution, aspect-driven | hardcoded 1344×768 | equivalent when 16:9 |
save chain | VHS 48→24 fps | native CreateVideo/SaveVideo at 24 | equivalent |
TensorRT RIFE/upscale | on volume | absent | not needed unless enabled |

**Conclusion: three settings explain most of the quality gap — 6 steps vs 4, tau 1.3 vs 1, and 1024-px references vs full-size.** The node packs matter less for still-image adherence than those three.

---

## 6. Make Vast equivalent

Minimum viable parity (no custom packs needed):

1. `BasicScheduler.steps = 6`.
2. `BlockSparseAttention` `selection.tau = 1.3`.
3. Reference handling: resize each reference to **long edge ≤1024** before `LoadImage` (the app can do this at upload time — same cap the Pixaroma loader applies), then `ref_image_size` as the job asks.
4. Keep euler + beta, turbo LoRA, Singularity UNET.

Full parity (needs the packs):

5. Install `ComfyUI-Pixaroma`, `ComfyUI-VideoHelperSuite`, `ComfyUI-PerfectVideoResolution` and switch the graph to Pixaroma loaders + VHS saver.
6. Add v1.3 pruned int8 to the box for a true checkpoint A/B.
7. RIFE/upscale only if wanted — those bring TensorRT, which is per-architecture.

---

## 7. Gotchas recorded in the repo

- A failed `opencv` import unregisters **all** of VideoHelperSuite → `missing_node_type`.
- `sam_vit_b` is a legacy tar checkpoint; torch ≥ 2.6 needs `weights_only=False`.
- Concurrent cold workers race on the volume populate — the `flock` is load-bearing.
- TensorRT engines are arch-specific; the symlink is only safe because the endpoint is pinned to `BLACKWELL_96`.
- Runpod drops results >~20 MB, hence the inline/S3 split.
