#!/usr/bin/env bash
# Reproducible conda environment for astra-robot-sim2real.
#
# Creates .conda/envs/astra (Python 3.12) with
#   * the pinned simulation stack from sim/requirements-local.txt,
#   * the analysis requirements from analysis/requirements.txt,
#   * the offline hardware-test dependencies from docs/REPRODUCING.md.
# Package caches are kept inside the repository: conda's shared package cache and
# ~/.cache may be read-only on sandboxed or shared hosts.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_DIR="$ROOT/.conda/envs/astra"
export CONDA_PKGS_DIRS="$ROOT/.conda/pkgs"
export PIP_CACHE_DIR="$ROOT/.cache/pip"
export TMPDIR="$ROOT/.tmp"
mkdir -p "$CONDA_PKGS_DIRS" "$PIP_CACHE_DIR" "$TMPDIR"

if [ ! -x "$ENV_DIR/bin/python" ]; then
  conda create -y -p "$ENV_DIR" python=3.12 pip
fi
PY="$ENV_DIR/bin/python"

# Keep the pinned simulation stack while installing the optional extras.
cat > "$ROOT/.conda/constraints.txt" <<'EOF'
mujoco==3.10.0
numpy==2.3.5
Pillow==12.0.0
PyOpenGL==3.1.10
playwright==1.58.0
imageio-ffmpeg==0.6.0
EOF

"$PY" -m pip install --no-input -r "$ROOT/sim/requirements-local.txt"
"$PY" -m pip install --no-input -c "$ROOT/.conda/constraints.txt" \
  -r "$ROOT/analysis/requirements.txt" pytest opencv-python pyserial
"$PY" -m pip check
"$PY" -m pip freeze > "$ROOT/.conda/pip-freeze.txt"

# Applied automatically by `conda activate`.
conda env config vars set -p "$ENV_DIR" \
  MUJOCO_GL=egl \
  MPLCONFIGDIR="$ROOT/.cache/matplotlib" \
  PLAYWRIGHT_BROWSERS_PATH="$ROOT/.browser-runtime" \
  PYTHONDONTWRITEBYTECODE=1 >/dev/null

cat <<MSG
Environment ready: $ENV_DIR

  conda activate "$ENV_DIR"

Simulation smoke test and preview:
  PYTHON="\$CONDA_PREFIX/bin/python" bash sim/run.sh scripts/check_runtime.py --seconds 5 --no-render
  PYTHON="\$CONDA_PREFIX/bin/python" bash sim/run.sh scripts/preview_initial.py --output .conda/preview

Analysis and offline hardware tests:
  python analysis/build_results.py
  python -m pytest real/control_api/tests real/experiment/tests -q
MSG
