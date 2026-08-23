#!/usr/bin/env bash
#
# One-shot setup. Safe to re-run.
#
#   ./scripts/setup.sh
#
# Installs uv, the rubberband binary, and Python dependencies, then
# generates a test vocal so the pipeline has something to run against.

set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> uv"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  echo "    installed. Add ~/.local/bin to your PATH in ~/.zshrc:"
  echo "      export PATH=\"\$HOME/.local/bin:\$PATH\""
else
  echo "    already installed ($(uv --version))"
fi

# pyrubberband shells out to this binary; without it, alignment fails at
# runtime rather than at install time.
echo "==> rubberband"
if ! command -v rubberband >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    brew install rubberband
  else
    echo "    !! no Homebrew. Install rubberband manually:"
    echo "       https://breakfastquay.com/rubberband/"
  fi
else
  echo "    already installed"
fi

echo "==> python dependencies"
uv sync

echo "==> web frontend"
if ! command -v npm >/dev/null 2>&1; then
  echo "    !! npm is required to build the web frontend. Install Node.js, then re-run setup."
  exit 1
fi
npm --prefix web ci
npm --prefix web run build

echo "==> test fixtures"
uv run python scripts/make_test_vocals.py

if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> wrote .env — add STABILITY_API_KEY for the api backend"
  echo "                 add DEEPSEEK_API_KEY for the chat agent"
fi

cat <<'DONE'

Setup complete.

  Run the app:   uv run uvicorn backend.api:app --reload
                 http://127.0.0.1:8000

  Run the CLI:   uv run btg --input samples/fixtures/amin_100.wav --part bass

Both work right now on the `mock` backend — no model weights or API key needed.

To enable the local Stable Audio 3 MLX backend:

  1. clone Stability's repo next to this one:
       git clone --depth=1 https://github.com/Stability-AI/stable-audio-3 ../sa3-mlx-src
  2. install the MLX backend:
       cd ../sa3-mlx-src/optimized/mlx && ./install.sh -y
  3. optionally set BTG_MLX_ROOT in .env if you installed it somewhere else
  4. verify:
       uv run btg --input samples/fixtures/amin_100.wav --part bass --backend local

To enable the DeepSeek chat agent, create a DeepSeek API key and set
DEEPSEEK_API_KEY in .env. Without it, /api/interpret falls back to rules.

DONE
