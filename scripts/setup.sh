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

echo "==> test fixtures"
uv run python scripts/make_test_vocals.py

if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> wrote .env — add STABILITY_API_KEY to enable the api backend"
fi

cat <<'DONE'

Setup complete.

  Run the app:   uv run uvicorn backend.api:app --reload
                 http://127.0.0.1:8000

  Run the CLI:   uv run btg --input samples/fixtures/amin_100.wav --part bass

Both work right now on the `mock` backend — no model weights or API key needed.

To enable the local Stable Audio 3 model (weights are gated on Hugging Face):

  1. accept the licence at
     https://huggingface.co/stabilityai/stable-audio-3-small-music
  2. create a token at https://huggingface.co/settings/tokens
  3. uv run hf auth login
  4. uv sync --extra local

DONE
