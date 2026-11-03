# v1.0.1 release checks

Release preparation: 4 November 2026. The repository URL and approved 1:43 demo remain the same.

## Automated and installed checks

- Local locked Python 3.12 environment: **640 backend tests passed**. The remaining warning is upstream Starlette TestClient's httpx deprecation; app startup and matte-overflow warnings are resolved.
- Svelte check: zero errors/warnings. All six core JavaScript suites pass. Production build succeeds with the main JavaScript about 356 KB, plus separate simulation/vendor chunks; no Vite size warning.
- OpenCV is locked to 4.14.0.94 under the `<5` dependency constraint. Float EXR, finishing-lighting and matte-treatment tests pass. CI runs the entire suite on Ubuntu 24.04 and macOS 15; see [hosted results](https://github.com/ProjectFoundary/bang-on/actions).
- Security regressions cover unsafe/encoded project and render IDs, double-encoded traversal, GET/HEAD/mutation requests, symlink escapes, allowed network hosts and ordinary safe downloads. Identifier validation applies before any project file is served.
- Wheel metadata identifies `keyfx` version `1.0.1`, MIT licensing, both license notices, the compiled interface and editable offline examples.
- Independent installed-wheel acceptance uses synthetic provider transport and blocks external sockets. It checks import, immutable RGB/alpha lifecycle, explicit approval, cached composite, original source bytes, pre-onset pixels, 12 native 16-bit frames at 24000/1001 fps, audio, movie decoding and EXR ZIP integrity. Twenty concurrent duplicate generation requests create one worker.

## Live workflow checks

These are separate from synthetic automated tests. An isolated local data store and an existing Comfy Cloud connection were used; credentials, project files, provider receipts and private source footage are excluded from Git and release assets.

- An established moving plane plate was imported, an HQ smoke plume placed and its browser guide captured. Real Fluid RGB and AlphaGen were reviewed and approved, composited over the source and exported as native movies and float EXR layers.
- Live smoke grenade, explosion, ground hit and wall hit also completed RGB, alpha, approval, native movie and layer export.
- Both Qwen and the optional Nano Banana muzzle paths completed. Only the selected firing frame changed in the native frame grid; other exported frames matched the imported source. Nano Banana remains optional.
- Export checks decoded the complete movies, checked dimensions/frame counts, original upload hashes, ZIP CRCs, manifest frame grids and decoded EXR content. Each live source interval retained 96 native frames.
- Preview-only HQ lighting checks changed key intensity, fill, rotation and elevation through the real interface. The captured volume RGB changed while alpha stayed byte-identical. No additional LTX runs were used for lighting comparisons. The rebuilt interface was reloaded for a final preview check.

The first smoke result included unrelated scenery. The revised positive isolation prompt produced smoke and the other intended effects. Generated visual quality still needs artist review.

**Remaining live issue:** fire RGB generated successfully, but several AlphaGen seeds returned coloured content or exceeded the verified near-gray tolerance. These versions stayed unapproved. A BF16 transformer comparison did not resolve it. Validation limits were not relaxed. The separate AlphaGen seed control permits an explicit new matte variation while retaining reusable RGB; it does not guarantee model success. Fire was tested, but a passing live fire matte/export is not claimed for this session.

## Privacy and remaining limits

The selected source, public Git history, nested example archives, wheel, source ZIP, requirements and approved demo are scanned for credentials, private runtime files and personal filesystem paths. Checks include exact known connection credentials without logging their values. Local projects, settings, caches, original plates and receipts are excluded. This is a release audit, not a formal penetration test.

The npm audit still reports three low-severity build-tool entries from the upstream deepmerge advisory, with no available npm fix; no moderate/high/critical findings. Separate physical Windows/Linux GPU acceptance is outstanding. See [limitations](LIMITATIONS.md).

## Reproduce

```sh
npm --prefix web ci
npm --prefix web run check
npm --prefix web run test:unit
npm --prefix web run build
uv sync --locked --python 3.12
KEYFX_SKIP_EXAMPLES=1 KEYFX_DATA=/tmp/bang-on-qa uv run pytest -q
uv build --wheel
```

Build the frontend before syncing from source, since the wheel includes `web/dist`. For installed acceptance, copy `tools/check_installed_release.py` outside the checkout and run it using the clean installed environment's Python `-I`, with an `--output` path. Add httpx as a test-only dependency.

The v1.0.0 Linux CI failures were caused by OpenCV 5's missing EXR codec. Version 1.0.1 changes the dependency and restores Linux coverage rather than skipping the affected tests. The original v1.0.0 tag remains unchanged.
