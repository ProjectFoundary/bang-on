# Current delivery — Bang On v1.0.1

The Export layers panel writes a cached ZIP with the selected native combined movie and SDR preview, signed light/shadow EXRs, and individual float RGBA EXR sequences for LTX elements. The manifest describes native timestamps, colour and alpha association. Export does not submit generation. The original encoded upload remains separate and intact.

Muzzle image edits are baked into the final combined native movie. They do not have a separate RGBA EXR sequence. The generated LTX RGB and matte retain their original model resolution and 24 fps detail. Nuke/After Effects adapters and ProRes element packages remain proposals.

The following 2 November audit records the earlier state and design options. Its statements about unavailable exports are historical.

---

# Element export options — 2 November 2026

This is a local-code audit and proposed delivery contract, not a claim that these
new export options are implemented or tested in Nuke/After Effects.

## What exists now

- The Results interface downloads a flattened native master and SDR preview.
  `native_media.export_native` writes lossless 8/16-bit RGB PNG-in-MOV, retaining
  supported source dimensions, precision, transfer/primaries and frame timing.
  The movie is opaque. The encoded upload is separately retained byte-identical.
- Each completed element retains its actual RGB and AlphaGen movies, decoded
  active-frame RGB/grayscale PNG sequences, hashes, recipes, processed guides,
  geometric visibility and timing maps. Element links currently expose movies;
  there is no combined RGBA element package/download button.
- Generated assets decode to 8-bit, 24 fps, at model resolution. Promoting these
  to float or source dimensions does not create more source detail. Padding and
  repeated model tail frames must be excluded using the frozen content rectangle
  and active-frame count.
- Tracking is a 2D similarity estimate and local KLT feature following, stored in
  logical 960×540 coordinates (quarter-pixel feature storage). A reference depth
  image, approximate focal length and relative depth scale build finite collision
  surfaces. This is not a calibrated moving 3D camera/object solve.
- Room light/shadow currently exist only as optional compositor calculations.
  They are not persisted/exported as independent image sequences. Light uses a
  broad Gaussian blur of generated RGB intensity, tinted warm; shadow is scalar
  transmittance derived from that field and geometric visibility. These are
  artistic screen-space approximations, not traced receiver illumination.

## First safe export: lossless element package

Offer a ZIP per selected completed/approved element, with no model submission:

```text
element.json                 # versioned contract, IDs, hashes, association
rgb/00000.png …              # actual decoded RGB, sRGB; no plate
alpha/00000.png …            # actual AlphaGen opacity data; never gamma encoded
visibility/00000.png …       # separate geometric visibility, unbaked
timing.json                  # 24fps element → native source frame/time mapping
tracking.json                # 2D camera matrices + resolved element path
geometry.json                # depth/matte IDs, roles, parameters, approximations
README.txt                   # exact composite equations/import interpretation
```

Preserve raw assets at their generated resolution/rate by default. Provide a
separately named native-canvas version only when explicitly selected, with the
resampling and native-to-model index map declared. Include rational source FPS,
time base, source PTS, trimmed timestamps, start frame/time, native dimensions,
reference frame, model content rectangle and active/model frame counts. Ordinary
image sequences cannot carry VFR timing themselves: retain the sidecar and require
an explicit CFR conform before producing an editor-oriented sequence/movie.

Export each camera matrix direction explicitly (`reference pixels → current
source pixels`), declare top-left origin, pixel aspect and matrix row order, and
include normalized and native-pixel element paths plus anchor edits. Warn that
generated images already contain camera motion: applying that track again will
double-transform them. A genuine scene/object 3D solve cannot be exported from
the present tracking data. Confidence/gaps should be exposed rather than
describing fallback camera motion as successful object tracking.

## Alpha and emissive fire

Current Fluid RGB is marked `black_premultiplied`: it contains black-backed,
already attenuated/emissive radiance. In linear light, with opacity A and optional
geometric visibility V, composite as:

```text
result = plate * (1 - A*V) + radianceRGB * V
```

Do not multiply this RGB by AlphaGen again. Do not blindly divide by tiny/zero A
to produce “straight RGBA”; bright fire/glow may have nonzero radiance with zero
or very small opacity. The lossless RGB-plus-data-alpha package is the safest
first contract. A combined RGBA export must state the radiance association and
retain emissive values outside alpha, with a supplied working composite example.

## Optional professional formats

| Format | Recommendation and limits |
| --- | --- |
| ZIP of RGB + alpha PNG sequences | First implementation. Exact decoded assets, explicit association/timing, no extra inference or codec damage. |
| Linear OpenEXR RGBA sequence | Preferred later comp interchange. Linear Rec.709 primaries, 1.0 = current 100-nit reference white, alpha as data. Preserve black-backed radiance; do not silently premultiply again. Half for compact delivery; float for exact derivations, signed light/difference layers or diagnostics. No claim of recovered HDR from 8-bit SDR generation. |
| ProRes 4444 MOV | Convenient optional editor delivery; lossy and less explicit about alpha association. Require alpha/colour/edge round-trip validation and an explicit CFR rate/conform. Keep the sequence package as source of truth. |
| Tracking JSON/CSV | First implementation. Native-time sample rows, source/native coordinate maps and approximation flags. |
| Nuke script / AE tracking import | Later adapters based on the documented package. Validate import, frame numbering, transforms, colour and alpha against a native reference composite in actual target applications before claiming support. |

Keep raw RGB/alpha and optional visibility-baked RGBA as distinct choices. A shelf
holdout baked into an element makes later repositioning/retracking difficult.
Export depth, named mattes and their collision/holdout roles separately. For
lighting, persist signed linear additive RGB and unitless shadow transmittance
as separate passes, with the precise order used by the compositor; do not bake
both into a “transparent explosion” or describe them as physically isolated light.

## Local tool capability checked

The installed FFmpeg advertises EXR float/half, RGBA float input and ZIP compression.
Its `prores_ks` encoder advertises 4444/4444XQ, alpha-enabled `yuva444p10le` input
and configurable alpha depth. These capabilities alone do not verify output
association or target-application compatibility. OpenCV's enabled EXR writer was
checked with a synthetic four-channel float image: negative RGB, values above
1 and alpha survived a ZIP-compressed float round trip exactly. No project
footage, model job, credential or external upload was used for this audit.

Relevant implementation: `keyfx/native_media.py`, `native_colour.py`,
`element_jobs.py`, `elements.py`, `element_api.py`, `geometry.py`, `track.py` and
`web/src/track.ts`. Existing live-provider acceptance limits still apply.
