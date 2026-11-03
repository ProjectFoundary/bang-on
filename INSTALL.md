# Install Bang On v1.0.0

The app is called **Bang On**. Its package and launcher are called **keyfx**.

## Requirements

- Python **3.12** is the tested release runtime.
- **FFmpeg and FFprobe** must be on PATH (`ffmpeg -version` and `ffprobe -version`). They are not Python packages or bundled executables.
- A recent browser with **WebGL2** and hardware acceleration for local simulation.
- Your own Comfy Cloud account or a configured local ComfyUI plus API proxy for new model generation. No account is needed to open the included examples or run local previews.

Fresh-environment release checks run on macOS. The Windows/Linux commands below are reference instructions; separate-machine/GPU validation is still outstanding.

## Packaged installation — no Node required

Download `keyfx-1.0.0-py3-none-any.whl` and `requirements-runtime.txt` from the [v1.0.0 release](https://github.com/ProjectFoundary/bang-on/releases/tag/v1.0.0) into the same folder. The release also provides `SHA256SUMS`.

On macOS/Linux, run in that folder:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-runtime.txt
.venv/bin/python -m pip install --no-deps keyfx-1.0.0-py3-none-any.whl
.venv/bin/keyfx serve --port 8765
```

On Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-runtime.txt
.\.venv\Scripts\python.exe -m pip install --no-deps keyfx-1.0.0-py3-none-any.whl
.\.venv\Scripts\keyfx.exe serve --port 8765
```

Open **http://127.0.0.1:8765**. Keep the terminal open while using the app. Stop it with Ctrl+C. For later launches, run the same `keyfx serve` command from the installation folder.

The wheel includes the built interface, effect assets and editable **Plane smoke** / **House explosion** sample shots. Their saved references play offline. Changing an example does not change the bundled original, and deleted examples are not repeatedly reinstalled.

## Source installation

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), Node 20+ and FFmpeg first. Then:

```sh
git clone https://github.com/ProjectFoundary/bang-on.git
cd bang-on
npm --prefix web ci
npm --prefix web run build
uv sync --locked --python 3.12
uv run keyfx serve --port 8765
```

Build the interface before creating a distributable wheel. The packaged wheel installation does not need Node.

## Connect your own Comfy backend

In the app header, choose **Connect Comfy**.

**Comfy Cloud:** enter your own API key, choose **Test connection & check models**, then save. Obtain keys from [Comfy's account settings](https://platform.comfy.org/profile/api-keys). The test reads readiness information; it does not submit a generation. New generations use your account and credits.

**Local ComfyUI:** start your own ComfyUI with the required models/nodes. In a separate Python environment, follow the [official proxy quickstart](https://github.com/Comfy-Org/comfy-api-proxy#quickstart):

```sh
python -m pip install comfy-api-proxy
comfy-api-proxy
```

Enter your ComfyUI catalogue address (normally `http://127.0.0.1:8188`) and proxy address (normally `http://127.0.0.1:8189`) in Connect Comfy, test, then save. Add a proxy token if you configured one. Bang On is the browser app/server; it does not install a ComfyUI GPU environment or model weights for you. Local generation and optional Cloud partner models have separate settings.

Both **Fluid RGB** and **AlphaGen** must be ready before generating an element. [Model setup](docs/MODELS.md) lists the defaults and optional tools. The app reports missing models rather than switching providers silently.

## First effect

1. Open an included example, or create a **Keep original footage** shot.
2. Add **Smoke plume (HQ)**, **Smoke grenade**, **Explosion**, or **Fire** and click the plate to place it.
3. Adjust timing, size, wind and lighting; assign interaction only if needed.
4. Play the simulation and **Prepare guide**. Inspect the actual guide before generation.
5. Generate RGB + alpha, review the result, and explicitly approve the version you want.
6. Continue to Finish and export a composite or layers.

Muzzle generation defaults to a zero local allowance. To enable a local allowance, set `KEYFX_FLASH_CREDITS` before starting the app (for example `140`), or deliberately set it to `unlimited`. This is an application-side estimate, not a provider-enforced billing cap. Other generated tools also use backend resources/credits.

## Data and updates

Source checkouts default to `data/`. Installed wheels use the per-user KeyFX application-data folder. `KEYFX_DATA` selects a different data location. Set `KEYFX_SKIP_EXAMPLES=1` before first launch for an empty installation.

The optional `.env.example` is a reference; the app does not automatically load a `.env` file. Configure through Connect Comfy or set variables in your shell. Credentials are stored server-side in the installation data directory, not in the repository; the local credential file is not an OS-encrypted vault.

Before upgrading, stop the app and back up your data folder. Install the next wheel into the same virtual environment, or pull the source update, rebuild the frontend and run `uv sync --locked`. Keep the same data location to retain projects. This is a local single-installation app, not a multi-user hosting service.

If the page is blank, check that the frontend was built (source install) and that the terminal has no errors. If import/export fails, check FFmpeg/FFprobe on PATH. If a generation is unavailable, use the connection/model check before submitting anything.
