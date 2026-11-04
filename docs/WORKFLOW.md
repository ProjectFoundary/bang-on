# Direct first, generate second

## Plate and interaction

Import a Keep original footage shot. Original encoded uploads remain stored. Supported native timing, dimensions and colour metadata are retained by the working pipeline; the explicit legal-range RGB option clips otherwise unrepresentable out-of-range RGB. See the import notice for the chosen path.

You may provide mattes/depth or generate them through the configured backend. A **holdout** controls visibility. A **collider** changes simulated flow. An object can do either or both, independently per effect. 2D matte mode needs no depth. Depth 3D reconstructs approximate finite surfaces from depth and selected objects; it is not a calibrated moving mesh solve.

For a depth PNG, open **Effects → Interaction**, choose **Depth 3D**, and set **Depth convention** to match the file. Choose **Bright is far** for a normalized distance pass like the house example’s Blender depth. The control also updates a loaded pass. Use **Select object from depth** to pick a region, set its role, and assign it to the effect. If the selection spills onto a nearby surface, restrict the region or import a separate object matte. **Collider + holdout** both redirects the fluid and hides the portion behind the surface. The bundled house shot also includes hand-authored CG solids, so depth-picked surfaces in a fresh shot will approximate its interaction.

## Local simulation and guide

Choose an effect, click to place it, and adjust size, timing, direction, wind and lighting. Smoke density changes optical thickness; brightness changes simulated illumination. Review the action locally before submitting a model job. Source tracking is independent of which mattes collide with the effect.

Prepare guide captures the isolated element on black and its independent geometric visibility. Painted guides use broad shading regions, temporal blending and configurable sparse cadence. Inspect the processed Guide link. Preparing or downloading a simulation preview is local and does not submit a model generation.

## RGB and alpha

Generate the selected element to run LTX Fluid RGB, then a separate AlphaGen pass. Inspect both outputs. Approval is explicit and pins that version; reruns create another version. Unknown/failed submissions are retained for inspection and are not automatically retried as new paid jobs.

Advanced guide settings expose a separate AlphaGen seed (1234 for new captures). Changing only that seed can reuse the finished RGB when the captured guide is identical. It creates a new immutable version and runs a new matte pass; inspect and approve it manually. Older frozen recipes keep their original seed behavior. A failed colour-matte check remains a failure, rather than being silently accepted. If AlphaGen returns colour, try **Advanced · Comfy models → Use full BF16 for AlphaGen** and check backend readiness before preparing/generating a new version. That profile can reuse unchanged RGB and needs more GPU memory.

Changing simulation or guide settings invalidates the affected generation. Cached finishing controls can reuse the existing generated assets. Review final images after editing matte multiply/erosion/blur, opacity, exposure, original body/generated edge blend, optical holdout softness or glow.

## Finish

The original plate is the base for composites. Black-backed RGB is treated according to its stored association; it is not multiplied by alpha a second time. Optical visibility remains independent from the original simulation alpha so generated edge wisps can survive.

Optional MiniMax interaction lighting generates a separate signed light/shadow difference. It uses references and can introduce artifacts; inspect before enabling it. A new lighting generation uses backend resources; adjusting a cached pass does not rerun it.

For muzzle flashes, select the exact firing frames and set a simple placement/direction/size layout. Frame image edits and localized lighting are transferred back into the native shot. Model availability and the local muzzle allowance gate generation.

Build the final take after reviewing/approving the intended elements. Download the composite or use layer export for native-timed float OpenEXR RGBA and manifests. [Export options](EXPORT_OPTIONS.md) describes association, colour and remaining adapter limits.
