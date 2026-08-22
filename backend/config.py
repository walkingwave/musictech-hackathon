"""Project-wide paths and settings.

Everything configurable lives here so teammates have one place to look.
Values come from the environment (see .env.example), with safe defaults.
"""

from __future__ import annotations

import os
from pathlib import Path

# --- paths -------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = REPO_ROOT / "sessions"
CACHE_DIR = REPO_ROOT / ".cache"
FRONTEND_DIR = REPO_ROOT / "frontend"

# --- audio -------------------------------------------------------------

# Stable Audio 3 works at 44.1kHz stereo. We keep everything at this rate
# so no stage of the pipeline has to resample.
SAMPLE_RATE = 44100

# --- generation defaults -----------------------------------------------

# How far the model is allowed to drift from the guide track.
# Higher = more divergence. Stability's docs suggest starting at 0.8 and
# dropping to 0.6-0.75 if the output wanders off the guide.
DEFAULT_NOISE = 0.8

# Sampling steps. Post-trained models are tuned for very few steps.
DEFAULT_STEPS = 8

# --- backends ----------------------------------------------------------

DEFAULT_BACKEND = os.environ.get("BTG_DEFAULT_BACKEND", "mock")
STABILITY_API_KEY = os.environ.get("STABILITY_API_KEY") or None

STABILITY_API_URL = "https://api.stability.ai/v2beta/audio/stable-audio-3/audio-to-audio"

# --- local MLX backend -------------------------------------------------

# Stability's Apple Silicon build, installed separately as a sibling
# checkout with its own venv. Weights come from the *ungated*
# `stabilityai/stable-audio-3-optimized` repo, so this path needs no
# HuggingFace account - unlike the PyTorch `stable-audio-3-small-music`
# weights, which are gated behind licence approval.
#
# Install:
#   git clone --depth=1 https://github.com/Stability-AI/stable-audio-3
#   cd stable-audio-3/optimized/mlx && ./install.sh -y
MLX_ROOT = Path(
    os.environ.get(
        "BTG_MLX_ROOT",
        REPO_ROOT.parent / "sa3-mlx-src" / "optimized" / "mlx",
    )
)

# Which DiT to run. `medium` (1.4B) fits comfortably on 16GB of unified
# memory and sounds better; `sm-music` (0.6B) is faster for sweeping
# parameters. Each DiT pairs with a specific decoder.
MLX_DIT = os.environ.get("BTG_MLX_DIT", "medium")
MLX_DECODERS = {"sm-music": "same-s", "sm-sfx": "same-s", "medium": "same-l"}


def ensure_dirs() -> None:
    """Create the directories the app writes to. Safe to call repeatedly."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
