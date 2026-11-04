# Changelog

## Unreleased

- Add **Scene → Camera motion → Locked camera** for static plates. Moving mattes and local feature tracks remain available.
- Keep smoke below thin solid roof colliders until it can flow around their edges.
- Default to **Fast** for interactive smoke previews and keep the full simulation for guide capture.
- Save pending edits before switching shots. Make the effect picker and prepared guide easier to reach, and show the approval requirement before enabling layer export.

## v1.0.2 — 2026-11-04

- Add a **Depth convention** control for imported depth PNGs in **Effects → Interaction → Depth 3D**. Choose **Bright is far** for distance passes like the Blender depth in the house fireball example.
- Correct an existing depth pass in place. The live preview uses the revised interpretation with the same depth pixels and assigned objects.

## v1.0.1 — 2026-11-04

- Restore float OpenEXR export with `opencv-python-headless<5` and a regenerated lockfile.
- Reject unsafe project/render IDs and project-directory escapes before serving files, including encoded traversal requests.
- Run complete CI on Linux and macOS; use FastAPI lifespan startup and guard all-opaque matte distance calculations against overflow.
- Strengthen isolated-effect prompts after a live smoke test produced unrelated scenery.
- Add a separate AlphaGen seed for new captures, preserving legacy frozen recipes and RGB cache reuse when the guide is unchanged. Add an optional full-BF16 AlphaGen profile for difficult mattes, independently of the RGB models.
- Prevent stale batch progress/results from updating a different shot after navigation.
- Split the production JavaScript into smaller application, simulation and vendor chunks.
- Recheck live preview lighting, all visible effects, both muzzle backends, native movies and layer export. See [release checks](docs/RELEASE_CHECKS.md) for evidence and remaining limits.

## v1.0.0 — 2026-11-04

First public release of Bang On.

- Browser smoke/fire/explosion blocking, wind and lighting controls, matte/depth interaction and preview export.
- Isolated LTX Fluid + AlphaGen generation, explicit per-element approval and cached finishing.
- Exact-frame muzzle placement, localized lighting, native composites and OpenEXR layer export.
- Editable offline plane/house samples, generated reference clips, install guide, MIT license and approved example video.

See docs/LIMITATIONS.md for the release's current boundaries.
