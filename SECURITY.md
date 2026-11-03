# Security and private data

Bang On runs locally and stores installation data outside the public source release. Saved connection keys stay server-side in the installation data folder. That credential file is not an OS-encrypted vault.

Do not commit `data/`, `.env`, connection/local-settings files, generated project caches, provider receipts or footage that is not intended for public distribution. The repository `.gitignore` excludes these paths. Example credentials in tests are deliberately synthetic.

The released example video and bundled CG plane/house media are intentional public assets. Source code and packaged archives are checked for credentials, private paths and unwanted runtime files before publication. See `docs/RELEASE_CHECKS.md` for the release audit scope; this is not a claim of a formal security penetration test.

Use GitHub's private vulnerability reporting on this repository when available. Do not include real keys or private footage in public issues. For a normal bug, use a synthetic reproduction and remove sensitive fields from logs. Generation sends the required selected assets to the backend you configure; review connection and generation actions accordingly.
