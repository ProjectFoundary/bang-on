# Publishing an update

This is the public release checkout. Keep private runtime data and provider credentials out of it. Each tagged release is a frozen snapshot; do not move an existing release tag.

1. Make and review the code changes. For work from another checkout, transfer only intended source files, never its `.git`, `data`, `.env`, experiments or project caches.
2. Update the package version in `pyproject.toml`, run `uv lock`, and add a changelog entry. Preserve the `keyfx` command for compatibility.
3. Rebuild the interface, run the documented tests, build the wheel and check it in a fresh environment outside the source tree.
4. Export pinned runtime requirements with `uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file requirements-runtime.txt` for the release assets. Review the exported header for machine-local paths.
5. Audit source, Git history, nested example ZIPs, the wheel and any demo media. Run a secret scanner such as Gitleaks with archive traversal; inspect metadata and newly added files. Do not publish local audit reports containing private paths or matches.
6. Commit the reviewed snapshot, push `main`, create a new version tag and publish a GitHub release containing the wheel, runtime requirements, optional approved demo and SHA256SUMS. Verify public access and download integrity.

For normal code updates from this checkout:

```sh
git status
git add <reviewed-files>
git commit -m "Describe the change"
git push origin main
```

The initial release excludes private development history. Future commits should continue from this public history. No model-generation keys belong in repository settings or release assets.

## GitHub Actions

The build and test workflow at `.github/workflows/checks.yml` runs on pushes to `main`, pull requests and manual dispatch. It checks and builds the interface, runs the complete backend suite and builds the release wheel on macOS 15 Apple Silicon. The known Linux EXR codec limitation is recorded in the release checks and installation guide. It uses read-only repository permissions and does not require model-provider credentials or paid inference.

Editing workflow files requires GitHub credentials with workflow-write permission. Local release checks are recorded in [docs/RELEASE_CHECKS.md](docs/RELEASE_CHECKS.md), and hosted run results are available on [Actions](https://github.com/ProjectFoundary/bang-on/actions).
