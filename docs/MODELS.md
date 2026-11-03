# Model setup

The app does not redistribute model weights. Configure your own Comfy backend, install/register the selected weights and required nodes, and run the in-app readiness check. Model filenames are configuration defaults, not proof of installed weight contents or available GPU memory.

The original-plate element route uses LTX 2.5 with these defaults from `keyfx/element_graphs.py`:

| Role | Default file |
| --- | --- |
| Transformer | `ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors` |
| Text encoder | `gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors` |
| Video VAE | `ltx-2.5-video-vae-conv-bf16.safetensors` |
| Audio VAE | `ltx-2.5-audio-vae-bf16.safetensors` |
| RGB Fluid LoRA | `AInVFX__ainvfx-fluid__ainvfx-fluid.safetensors` |
| AlphaGen LoRA | `Lightricks__LTX-25-22b-IC-LoRA-Alpha-Gen__ltx-2.5-22b-ic-lora-alpha-gen-0.9.safetensors` |

Fluid and AlphaGen use separate model chains. They are not stacked in one pass. API graph templates are included in `workflows/`; the application builds and snapshots the actual submitted graph and input hashes.

Upstream setup and terms: [LTX 2.5](https://huggingface.co/Lightricks/LTX-2.5), [AInVFX Fluid](https://huggingface.co/AInVFX/ainvfx-fluid), [AlphaGen documentation](https://docs.ltx.io/open-source-model/vfx-post-production/alpha-gen), [Comfy Cloud model imports](https://docs.comfy.org/cloud/import-models).

Optional depth reconstruction, object tracking, people alpha, image edits, MiniMax interaction lighting and the legacy H3 route have their own node/model requirements. The app lists the exact missing requirements for the selected operation. Nano Banana partner edits require Cloud access; Qwen/FLUX native routes need their corresponding weights. Access, licensing, inference cost and visual quality depend on your backend and chosen models.

The bundled examples and local simulations require none of these model downloads. New inference requires a ready backend; no credentials or paid allowance are supplied by this repository.
