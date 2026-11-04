# v1 limitations

Version 1.0.2. Inspect generated results before delivery.

- Local smoke/fire preview requires WebGL2 and a suitable browser/GPU. Performance varies by hardware; guide capture can run slower than playback.
- Source imports can cover up to60seconds. Individual generated element windows are limited to about5seconds at24fps. Model sampling/resolution limit generated detail even when native source timing and output dimensions are retained.
- Depth/camera reconstruction and generated-wisp depth ordering are approximate. A tracked silhouette/depth shell is not general animated mesh or SDF support.
- The demo includes earlier art-directed CG experiments. Bundled editable plane/house scenes expose the current implementation and retain archived generated clips as references; exact motion/lighting parity with every archived shot is not guaranteed.
- AlphaGen can return a coloured reconstruction instead of a usable matte. Validation rejects such outputs without silently converting or automatically spending on retries. Try the full BF16 AlphaGen profile in Advanced · Comfy models or change the independent AlphaGen seed in Advanced guide settings, then prepare/generate a new version. Unchanged RGB can be reused. Full BF16 needs more GPU memory and resolved the tested fire case; other results still need review. AlphaGen output can also need matte adjustment. Fine hair, motion blur, smoky edges and occlusions require inspection. Source-preserving compositing does not make generated interaction physically exact.
- Supported native colour paths are explicit. The legal-range RGB working copy clips out-of-range decoded RGB; strict mode can reject it. Unsupported Log/RAW/ICC/dynamic-HDR or other source encodings may need conversion before import. Original encoded uploads are retained.
- Lighting differences, glow and shadow are compositing approximations. Optional generated scene/actor lighting can change apparent texture or introduce residue.
- Nuke/After Effects adapters and ProRes element packaging are not shipped. Float OpenEXR layer export and browser/native movie outputs are available as described in the export guide.
- Bang On pins OpenCV below 5 to restore float EXR support. Linux and macOS CI include export/lighting regression coverage; physical Windows/Linux GPU acceptance remains outstanding.
- macOS fresh-environment installation is checked for this release. Separate physical-machine and Windows/Linux GPU acceptance is not complete. Unit tests use synthetic provider transport; live access and visual quality vary by installation.
- This is a local single-user server. It is not an authenticated multi-user public service. Keep its default loopback binding for normal desktop use.

The npm build-tool audit currently reports three low-severity entries arising from the same upstream deepmerge prototype-poisoning advisory ([GHSA-gcqx-w48m-w2h4](https://github.com/advisories/GHSA-gcqx-w48m-w2h4)); npm reports no available fix. These are development/build dependencies. No moderate, high or critical npm findings were reported at release preparation.
