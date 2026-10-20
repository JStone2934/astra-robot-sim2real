# Conda environment for simulation, analysis, and hardware checks

This page records the conda environment used for local work on this repository: the
pinned MuJoCo simulation stack, the figure/table pipeline, and the offline hardware
tests. The released instructions in [REPRODUCING.md](REPRODUCING.md) use `venv`; a conda
environment is equivalent because every runtime dependency is installed from PyPI.

## What was created

| Item | Value |
|---|---|
| Environment path | `.conda/envs/astra` (Python 3.12.15) |
| Reproduce | `bash setup_conda.sh` |
| Activate | `conda activate "$PWD/.conda/envs/astra"` |

The environment lives inside the repository because conda's package cache, the shared
`envs_dirs` registry, and `$HOME` are read-only on this host. `setup_conda.sh` therefore
points `CONDA_PKGS_DIRS`, `PIP_CACHE_DIR`, and `TMPDIR` at `.conda/pkgs`, `.cache/pip`,
and `.tmp`. The environment is not listed by `conda env list` for the same reason; use
the full path to activate it.

Installed versions (the setup script saves `pip freeze` to the local
`.conda/pip-freeze.txt`):

- Simulation pins from `sim/requirements-local.txt`: `mujoco==3.10.0`,
  `numpy==2.3.5`, `Pillow==12.0.0`, `PyOpenGL==3.1.10`, `imageio-ffmpeg==0.6.0`,
  `playwright==1.58.0`.
- Analysis from `analysis/requirements.txt`: `matplotlib==3.11.2` (with numpy/Pillow
  pinned by the constraints above). The analysis renderers import `mujoco`, so this
  environment also runs `analysis/render_*.py`.
- Offline hardware checks: `pytest==9.1.1`, `opencv-python==5.0.0.93`,
  `pyserial==3.5`.
- `sim/requirements-local.lock.txt` is a full transitive lock; the three packages
  resolved slightly newer here (`glfw 2.11.0`, `fsspec 2026.9.0`, `absl-py 2.5.1`).
  Install the lock file instead if a byte-identical transitive set is required.

`conda env config vars set` records the runtime variables so they are applied on
activation:

| Variable | Value | Why |
|---|---|---|
| `MUJOCO_GL` | `egl` | headless rendering, matching `sim/run.sh` |
| `MPLCONFIGDIR` | `.cache/matplotlib` | `$HOME/.config` is read-only |
| `PLAYWRIGHT_BROWSERS_PATH` | `.browser-runtime` | keeps the optional Chromium shell in the repo |
| `PYTHONDONTWRITEBYTECODE` | `1` | matches `sim/run.sh` |

## Commands

```bash
conda activate "$PWD/.conda/envs/astra"

# Simulation API smoke test (see the note on the monitor below)
PYTHON="$CONDA_PREFIX/bin/python" bash sim/run.sh scripts/check_runtime.py --seconds 5 --no-render

# Rendered initial-state preview: .conda/preview/*.png + state.json + setup.json
PYTHON="$CONDA_PREFIX/bin/python" bash sim/run.sh scripts/preview_initial.py --output .conda/preview

# Figures, tables, prompt appendix, behavioral audit, release checks
python analysis/build_results.py
python analysis/build_prompt_appendix.py
python analysis/audit_behavior.py
python analysis/check_release.py

# Offline hardware tests
python -m pytest real/control_api/tests real/experiment/tests -q
```

## Verification performed

- `sim_env.service` starts, `state`/`schema` respond, invalid `reset`/`observe`/`base`/
  `move` requests are rejected, `base` is idempotent per request id, motion targets move
  joints, the physics clock advances in real time (max lateness ≈ 4 ms), and the state
  journal is gap-free.
- EGL rendering works through Mesa `llvmpipe`: one `observe` call returns three
  640×480 JPEG images in ≈0.3 s, so the camera loop keeps up with the simulator.
  `scripts/preview_initial.py` produced the expected lobby, follow, and wrist views.
- The four analysis commands exit 0 on a copy of the working tree; `check_release.py`
  reports the released counts (30 fixed-start, 18 paired-offset, 27 local, 12 real)
  with no broken links.
- `pytest real/control_api/tests real/experiment/tests -q` → `55 passed`.

## Host limitations

- **No GPU device nodes.** `sys/class/drm` and `/proc/driver/nvidia` show an NVIDIA
  driver, but `/dev/nvidia*` and `/dev/dri` are not exposed, so EGL falls back to
  software rasterization. `scripts/check_runtime.py` asserts a four-view monitor at
  10 fps (`frames > (seconds-3)*8`) and a pre-warm age under 1 s; those assertions
  assume GPU-backed EGL and fail here even with `--no-render`, because the operator
  monitor keeps rendering. On this host, validate with `scripts/preview_initial.py`
  plus a manual service `observe` request, or run the released smoke test on a GPU host.
- **No Playwright browser download.** `python -m playwright install --only-shell
  chromium` needs `storage.googleapis.com`, which is unreachable from this host. The
  dashboard video recorder in `sim/sim_env/dashboard_recording.py` therefore stays
  disabled; run the install on a network with access to that host if recording is
  needed.

## Removing and rebuilding

```bash
rm -rf .conda .cache .browser-runtime   # environment, package cache, matplotlib cache
bash setup_conda.sh                     # ~900 MB, mostly mujoco, matplotlib, playwright, opencv
```
