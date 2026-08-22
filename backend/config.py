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
# Higher = more divergence.
#
# Measured on sm-music against a synthesized bass guide, comparing output
# to the guide it was conditioned on:
#
#   0.40  waveform correlation 0.85, centroid 4026Hz  - passthrough, the
#         model hands the guide straight back
#   0.60  correlation 0.08, centroid 3774Hz           - decorrelated but
#         still saw-bright, not a bass timbre
#   0.75  correlation 0.00, centroid  537Hz           - genuinely new audio
#   0.90  correlation 0.02, centroid  435Hz           - most instrument-like
#
# So anything below MIN_USEFUL_NOISE is wasted UI range: the timbre does
# not change. Grid lock held at every level tested (99.4 BPM, zero drift),
# so raising this costs nothing in timing.
DEFAULT_NOISE = 0.8
MIN_USEFUL_NOISE = 0.6

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

# Which DiT to run. `medium` (1.4B) sounds better and still fits in 16GB
# of unified memory; `sm-music` (0.6B) is faster for sweeping parameters.
# Each DiT pairs with a specific decoder.
MLX_DECODERS = {"sm-music": "same-s", "sm-sfx": "same-s", "medium": "same-l"}

# Filenames the MLX stack expects, per DiT. Used to check what is actually
# downloaded before selecting one.
MLX_WEIGHTS = {
    "medium": ["dit_medium_f16.npz", "same_l_encoder_f32.npz", "same_l_decoder_f32.npz"],
    "sm-music": ["dit_sm-music_f16.npz", "same_s_encoder_f32.npz", "same_s_decoder_f32.npz"],
}
MLX_SHARED_WEIGHTS = ["t5gemma_f16.npz"]


def mlx_weights_present(dit: str) -> bool:
    """Are all the weight files for this DiT on disk and non-empty?

    Worth checking rather than assuming: the MLX CLI silently tries to
    download anything missing from HuggingFace, which on a slow link looks
    like a hang rather than an error.
    """
    weights = MLX_ROOT / "models" / "mlx"
    needed = MLX_WEIGHTS.get(dit, []) + MLX_SHARED_WEIGHTS
    return all((weights / name).is_file() and (weights / name).stat().st_size > 0 for name in needed)


def default_mlx_dit() -> str:
    """Prefer the best DiT whose weights are actually downloaded.

    Defaulting to `medium` unconditionally means a fresh checkout hangs on
    an 8GB download the first time someone clicks Generate.
    """
    override = os.environ.get("BTG_MLX_DIT")
    if override:
        return override
    return next((dit for dit in ("medium", "sm-music") if mlx_weights_present(dit)), "sm-music")


MLX_DIT = default_mlx_dit()


def ensure_dirs() -> None:
    """Create the directories the app writes to. Safe to call repeatedly."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
