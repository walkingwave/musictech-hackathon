"""Project-wide paths and settings.

Everything configurable lives here so teammates have one place to look.
Values come from the environment (see .env.example), with safe defaults.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# --- paths -------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DIR = REPO_ROOT / "sessions"
CACHE_DIR = REPO_ROOT / ".cache"
WEB_DIST_DIR = REPO_ROOT / "web" / "dist"


def _load_dotenv() -> None:
    """Small .env loader so setup works without another dependency."""
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

# --- audio -------------------------------------------------------------

# Stable Audio 3 works at 44.1kHz stereo. We keep everything at this rate
# so no stage of the pipeline has to resample.
SAMPLE_RATE = 44100
# auto uses Basic Pitch only when explicitly installed/configured; pYIN remains
# the dependency-free fallback for offline and lightweight setups.
PITCH_TRACKER = os.getenv("BTG_PITCH_TRACKER", "pyin").lower()

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

# Per-part overrides. Parts differ in how much freedom they can take
# before they stop matching the guide.
#
# Harmony and `free` are the fragile ones. Both ride sustained guides -
# a held vocal line, a held chord bed - and sustained material anchors the
# model far more weakly than a plucked or struck one. Measured against an
# A minor guide, both came back with pitch classes outside the key at 0.80
# and stayed inside it at 0.65.
# Bass and drums tolerate more freedom because their guides constrain
# rhythm and register more than exact pitch.
# How loud the already-generated stems sit under a new part's guide.
# The guide has to stay dominant — it carries the notes the new part plays —
# but the band underneath is what lets the model match their room and
# balance instead of generating into a vacuum. Pushed much past this the
# model starts re-rendering the whole mix rather than the one part.
ENSEMBLE_LEVEL = 0.3

# How many of the existing stems go into that bed. A session collects every
# take a user tried, and mixing all of them in makes the context a mush of
# unrelated ideas. The most recent few are the ones being worked on.
ENSEMBLE_MAX_TRACKS = 4

# Stable Audio's `strength` is how far the output is allowed to diverge from
# the input audio, and 0.8 is their recommended starting point. That is tuned
# for "here is a rough sketch, make it real" — but when the band is mixed into
# the input as context, 0.8 transforms most of that context away too. Their
# own guidance is to lower it when the output strays too far from the input,
# so a part generated against the band follows it more closely than one
# generated against a bare guide.
ENSEMBLE_STRENGTH_DROP = 0.05
ENSEMBLE_MIN_STRENGTH = 0.6

# A full mix needs more freedom than a single stem: the guide is a crude
# four-layer sketch, and holding the model to it too tightly renders the
# sketch rather than a record.
PART_NOISE = {"harmony": 0.65, "free": 0.65, "mix": 0.85}


def default_noise(part: str) -> float:
    return PART_NOISE.get(part, DEFAULT_NOISE)

# Sampling steps. Post-trained models are tuned for very few steps.
DEFAULT_STEPS = 8

# --- backends ----------------------------------------------------------

DEFAULT_BACKEND = os.environ.get("BTG_DEFAULT_BACKEND", "mock")
STABILITY_API_KEY = os.environ.get("STABILITY_API_KEY") or None

# Stable Audio 3 lives on the *unversioned* `stable-audio` path. The
# `stable-audio-2` path is a different, older service: its `model` field only
# accepts 'stable-audio-2.5' | 'stable-audio-2', so pointing at it silently
# generates with 2.5 no matter what the UI calls the backend.
STABILITY_API_URL = "https://api.stability.ai/v2beta/audio/stable-audio/audio-to-audio"
# Same family, no input audio: used for instrument sampling, which generates
# single notes from the prompt alone.
STABILITY_TEXT_URL = "https://api.stability.ai/v2beta/audio/stable-audio/text-to-audio"

# The only value that endpoint accepts today, sent explicitly so a future
# default flipping under us shows up as an error rather than a quiet downgrade.
STABILITY_MODEL = "stable-audio-3"

# Endpoint limits, for reference: duration <= 380s, steps <= 8, cfg_scale <= 25,
# strength 0..1, seed >= 0, output_format 'wav' | 'mp3'.
STABILITY_MAX_DURATION = 380

# Stable Audio 3 is asynchronous: the generation call returns a job id and the
# audio is collected from here. A bar or two of music comes back in seconds,
# but the queue is shared, so the timeout is generous rather than tight.
STABILITY_RESULTS_URL = "https://api.stability.ai/v2beta/results"
STABILITY_POLL_INTERVAL = 2.0
STABILITY_POLL_TIMEOUT = 300.0

# --- chat agent --------------------------------------------------------

BTG_AGENT_PROVIDER = os.environ.get("BTG_AGENT_PROVIDER", "deepseek")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY") or None
DEEPSEEK_API_URL = os.environ.get(
    "DEEPSEEK_API_URL",
    "https://api.deepseek.com/chat/completions",
)
BTG_AGENT_MODEL = os.environ.get("BTG_AGENT_MODEL", "deepseek-v4-flash")

# PyTorch local-runtime model ID. The MLX runtime selects its own DiT below.
TORCH_DIT = os.environ.get("BTG_TORCH_DIT", "small-music")

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


def mlx_venv_python() -> Path:
    """Path to the MLX checkout's venv interpreter, per platform.

    The MLX stack is Apple-Silicon-only, so on Windows this file simply will
    not exist and the local backend reports itself unavailable — but the venv
    layout still differs by OS (`Scripts/python.exe` vs `bin/python`), so
    resolve it correctly rather than assuming POSIX.
    """
    if sys.platform == "win32":
        return MLX_ROOT / ".venv" / "Scripts" / "python.exe"
    return MLX_ROOT / ".venv" / "bin" / "python"


def ensure_dirs() -> None:
    """Create the directories the app writes to. Safe to call repeatedly."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

# --- master-first stem splitting ---------------------------------------

# How far a stem may diverge from the master it is carved out of. Low on
# purpose: the whole point is that every stem is the SAME performance with
# the other instruments removed, so the model must keep the master's timing,
# harmony and room and only re-render the balance. Raise it and the stems
# drift back into being separate takes.
SPLIT_STRENGTH = 0.6

# The master itself diverges freely from its synthetic guide — the guide is
# a crude sketch and the master is the record.
MASTER_STRENGTH = 0.85

# How far the per-stem refinement pass may drift from the separated stem it
# re-renders. The separated stem is the right performance with separation
# artifacts on it; refinement is SA3 re-recording that exact part cleanly.
# Low, because the input is already the truth — only the timbre needs work.
REFINE_STRENGTH = 0.35
