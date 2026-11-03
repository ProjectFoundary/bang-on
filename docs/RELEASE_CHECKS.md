# v1.0.0 release checks

Release preparation:4 November2026. This is a first public snapshot, not a publication of the private development history.

## Verified on macOS

- Fresh Python3.12.9 environment using the public snapshot's locked dependencies: **605 backend tests passed**, with3 upstream deprecation warnings.
- Frontend: Svelte check reports zero errors/warnings; all6 core JavaScript suites pass; production build succeeds. Vite's main-bundle size advisory is nonblocking.
- Built wheel reports `Name: keyfx`, `Version:1.0.0`, `License-Expression:MIT` and includes both license files, built UI, effect presets and the2 offline example archives.
- Independently installed the wheel and hashed runtime requirements into a new environment outside the source checkout. Real import, guide preparation, immutable RGB/alpha lifecycle, review, explicit approval, cached composite, audio-retaining native export and OpenEXR layer ZIP passed using synthetic provider transport.
- The installed acceptance retains original upload bytes and pre-onset pixels, exports12native16-bit frames at24000/1001fps with audio, rejects foreign host/origin requests, and confirms that20concurrent duplicate generation requests create only one worker. No model jobs are submitted by release validation.
- The public demo is the approved1:43.417 cut with its original encoded picture and VO. Nonessential container metadata is stripped.

## Privacy audit scope

The selected source tree, fresh public Git history, nested bundled-example ZIP contents, release wheel and approved demo assets are checked for credentials and personal filesystem paths. Developer data, environment files, connection settings, caches, receipts, private footage and local audit logs are excluded. Gitleaks8.30.1 is used with archive traversal, supplemented by local path/credential and metadata checks. Public copyright attribution and intentional demo/example media remain included.

## Current limits

This is a macOS fresh-environment check, not a separate-machine or Windows/Linux GPU pass. Core automated tests use synthetic model transport, not live inference. GPU/browser quality and model availability vary by installation. See LIMITATIONS.md.

The npm audit reports3low-severity build-tool entries from one upstream deepmerge advisory, with no available npm fix at preparation time; no moderate/high/critical npm findings were reported. This is documented in LIMITATIONS.md. The release audit is not a claim of a formal penetration test.

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

For installed-package acceptance, copy `tools/check_installed_release.py` outside the source checkout and run it with the installed environment's Python `-I` and an `--output` path. It blocks external network access and substitutes only the model transport.

GitHub Actions is supplied as an optional template at `tools/github-checks.yml`; hosted CI has not run for v1 because the publishing credential does not have workflow-write permission. The verification above was performed locally.
