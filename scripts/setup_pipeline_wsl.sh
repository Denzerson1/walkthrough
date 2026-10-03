#!/usr/bin/env bash
# One-time setup of the capture pipeline inside WSL2 Ubuntu (docs/PIPELINE.md).
#
#   wsl -d Ubuntu -u root -- bash /mnt/c/<path to repo>/scripts/setup_pipeline_wsl.sh
#
# Idempotent: rerun it after a failure and it picks up where it stopped.
# The Windows NVIDIA driver supplies the GPU; nothing driver-side goes in here.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
REPO="$(cd "$(dirname "$0")/.." && pwd)"

# System tools. ffmpeg for frames; COLMAP comes from pycolmap below because
# Ubuntu 26.04's colmap package is broken (missing libPoseLib).
apt-get update -q
apt-get install -y -q build-essential git curl ninja-build ffmpeg gcc-14 g++-14

# CUDA 12.9 compiler and headers, for building gsplat's fused-ssim. CUDA 12
# to match torch's cu124 build (torch refuses a different major). Not the
# cuda-toolkit metapackage: it pulls Nsight, which needs libtinfo5, which
# Ubuntu 26.04 no longer has.
if [ ! -x /usr/local/cuda-12.9/bin/nvcc ]; then
  curl -sSfL -o /tmp/cuda-keyring.deb \
    https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/cuda-keyring_1.1-1_all.deb
  dpkg -i /tmp/cuda-keyring.deb
  apt-get update -q
  apt-get install -y -q cuda-compiler-12-9 cuda-libraries-dev-12-9 cuda-cudart-dev-12-9
fi
# glibc 2.41+ declares cospi/sinpi/rsqrt (and the f variants) noexcept;
# CUDA 12's math_functions.h declares them without, and nvcc rejects the
# mismatch. Add the noexcept to CUDA's six declarations. Anchored on the
# exact declarations, so it is a no-op on a header that already differs.
sed -i -E 's/^(extern __DEVICE_FUNCTIONS_DECL__ __device_builtin__ (double|float) +(rsqrtf?|sinpif?|cospif?)\((double|float) x\));$/\1 noexcept (true);/' \
  /usr/local/cuda-12.9/include/crt/math_functions.h

if ! command -v uv >/dev/null && [ ! -x "$HOME/.local/bin/uv" ]; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

# Trainer venv: Python 3.10 + torch 2.4.1/cu124 + gsplat 1.5.3 prebuilt for
# exactly that pair, and gsplat's reference trainer at the same tag.
[ -d "$HOME/gsplat" ] || git clone --depth 1 --branch v1.5.3 \
  https://github.com/nerfstudio-project/gsplat.git "$HOME/gsplat"
GS="$HOME/.venvs/gsplat/bin/python"
[ -x "$GS" ] || uv venv --python 3.10 "$HOME/.venvs/gsplat"
uv pip install --python "$GS" torch==2.4.1 torchvision==0.19.1 \
  --index-url https://download.pytorch.org/whl/cu124
uv pip install --python "$GS" "gsplat==1.5.3+pt24cu124" \
  --index-url https://docs.gsplat.studio/whl/pt24cu124 \
  --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match
export CUDA_HOME=/usr/local/cuda-12.9 PATH="/usr/local/cuda-12.9/bin:$PATH" \
  CC=gcc-14 CXX=g++-14 NVCC_CCBIN=gcc-14 TORCH_CUDA_ARCH_LIST=7.5 MAX_JOBS=8
uv pip install --python "$GS" setuptools wheel ninja
uv pip install --python "$GS" --no-build-isolation -r "$HOME/gsplat/examples/requirements.txt"
"$GS" -c "import torch, gsplat, fused_ssim; assert torch.cuda.is_available(); \
print('trainer ok:', torch.cuda.get_device_name(0), 'gsplat', gsplat.__version__)"

# Pipeline venv: the project plus its `pipeline` extra (pycolmap with CUDA).
# Outside the repo so it never collides with the Windows .venv.
cd "$REPO"
UV_PROJECT_ENVIRONMENT="$HOME/.venvs/walkthrough" uv sync --python 3.12 --extra pipeline
"$HOME/.venvs/walkthrough/bin/python" -c "import pycolmap; \
print('pipeline ok: pycolmap', pycolmap.__version__, 'cuda', pycolmap.has_cuda)"
