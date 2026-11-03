# Comfy API workflow templates

These are API-format graphs with placeholder filenames, not UI-layout exports.
Replace loaders with your uploaded inputs and install the exact models/nodes in
README.md and keyfx/element_graphs.py. The app builds the actual graphs from a
frozen per-element recipe and records the submitted graph and hashes.

ltx_rgb_api.json uses an isolated simulated element on black with Fluid.
ltx_alpha_api.json uses the actual RGB output in a separate AlphaGen pass.
Neither element stage receives the source photograph. MiniMax receiver-lighting
uses the clean plate and fixed composite as references; the app retains only a
signed light/shadow difference and restores original elements on top. That
optional graph is authored by keyfx/lighting_integration.py. Legacy H3 and
localized Nano/Qwen templates are included. Model weights are not redistributed.
Template creation and inspection submit no model jobs.
