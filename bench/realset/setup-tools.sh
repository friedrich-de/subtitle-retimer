#!/usr/bin/env bash
# Installs what the real-file kit needs into one directory, and nothing outside it:
# a virtual environment with this repository, ffsubsync and a local embedding
# model, and the alass binary.
#
#   bench/realset/setup-tools.sh TOOLS_DIR [REPO_DIR]
#
# Needs `uv`, `curl` and Python 3.11 or newer. A graphics card is used when
# PyTorch finds one, and is not required.
set -euo pipefail

TOOLS=${1:?usage: setup-tools.sh TOOLS_DIR [REPO_DIR]}
REPO=${2:-$(cd "$(dirname "$0")/../.." && pwd)}
ALASS_URL=https://github.com/kaegi/alass/releases/download/v2.0.0/alass-linux64

mkdir -p "$TOOLS/bin" "$TOOLS/tmp"
export UV_CACHE_DIR=${UV_CACHE_DIR:-$TOOLS/uv-cache}

[ -x "$TOOLS/venv/bin/python" ] || uv venv "$TOOLS/venv"
PY="$TOOLS/venv/bin/python"
uv pip install --python "$PY" -e "$REPO[dev]"
# ffsubsync asks for webrtcvad, which has to be compiled. The wheels package
# holds the same module ready built, so it goes in first and ffsubsync's own
# requirement is left out.
uv pip install --python "$PY" webrtcvad-wheels setuptools
uv pip install --python "$PY" --no-deps ffsubsync
uv pip install --python "$PY" auditok==0.1.5 charset_normalizer faust-cchardet chardet \
    ffmpeg-python numpy pysubs2 rich six srt tqdm typing_extensions
uv pip install --python "$PY" torch sentence-transformers

if [ ! -x "$TOOLS/bin/alass-cli" ]; then
    curl -fsSL -o "$TOOLS/bin/alass-cli" "$ALASS_URL"
    chmod +x "$TOOLS/bin/alass-cli"
fi

# The download cache is only needed while installing.
rm -rf "$UV_CACHE_DIR"
"$TOOLS/venv/bin/ffsubsync" --version
"$TOOLS/bin/alass-cli" --version
