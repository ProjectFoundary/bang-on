# Contributing

Use a source checkout with Node20+, Python3.12, uv and FFmpeg/FFprobe. Build the frontend and sync the locked Python environment as described in INSTALL.md.

Before proposing a change:

```sh
uv run pytest -q
npm --prefix web run check
npm --prefix web run test:unit
npm --prefix web run build
uv build --wheel
```

The core suite uses synthetic transports and must not spend model credits. Browser/GPU developer fixtures in `web/tests/` have separate setup and are not evidence of provider output quality. Keep changes scoped, preserve old projects/takes, and describe any timing, colour or model-signature changes.

Do not attach credentials, runtime data or private source media. Use synthetic test fixtures. Changes are contributed under the repository's MIT license. See RELEASING.md for the release checklist.
