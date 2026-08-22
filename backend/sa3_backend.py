"""Stage 4: the Stable Audio 3 adapter.

Three interchangeable backends behind one interface:

  mock   no model at all. Returns the guide track with noise mixed in, so
         the UI and pipeline can be developed and demoed without model
         access, credits, or a network connection.
  local  stable-audio-3 `small-music` running on this machine. Free and
         offline, but the weights are gated on Hugging Face.
  api    Stability's hosted `large` model. Best quality, costs credits,
         needs network.

The UI lets the user pick per generation, so keep the constructor cheap:
nothing here should load a model or hit the network until `generate` is
actually called.
"""

from __future__ import annotations

import hashlib
import io
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

import httpx
import numpy as np
import soundfile as sf

from . import config

log = logging.getLogger(__name__)


class Backend(Protocol):
    """What every backend must provide."""

    id: str
    label: str
    note: str

    def available(self) -> bool:
        """Can this backend actually run right now? Drives the UI selector."""
        ...

    def generate(
        self,
        prompt: str,
        init_audio: np.ndarray,
        noise: float,
        duration: float,
        seed: int,
    ) -> np.ndarray:
        """Guide audio + prompt -> generated mono audio at config.SAMPLE_RATE.

        `noise` is 0..1, higher meaning more divergence from `init_audio`.
        Both the local model's `init_noise_level` and the API's `strength`
        use that same convention, so no remapping is needed.
        """
        ...


# --- mock ---------------------------------------------------------------


class MockBackend:
    """Returns the guide with noise mixed in, proportional to `noise`.

    Sounds nothing like a real instrument, but has the right length, the
    right rhythm, and responds audibly to the noise slider — which is
    enough to build and test everything around it.
    """

    id = "mock"
    label = "Mock (no model)"
    note = "instant, offline, for UI development"

    def available(self) -> bool:
        return True

    def generate(self, prompt, init_audio, noise, duration, seed):
        rng = np.random.default_rng(seed)
        grain = rng.standard_normal(len(init_audio)).astype(np.float32) * 0.05
        return ((1 - noise) * init_audio + noise * (init_audio + grain)).astype(np.float32)


# --- local --------------------------------------------------------------


class LocalBackend:
    """Stability's MLX build, running on Apple Silicon.

    Invoked as a subprocess rather than imported: the MLX stack ships as
    its own checkout with its own virtualenv, and shelling out keeps its
    dependency tree (mlx, its own numpy pin) completely separate from
    ours. The cost is a process launch per generation, which is small
    next to sampling time.
    """

    id = "local"
    label = f"Local — {config.MLX_DIT} (MLX)"
    note = "free, offline, no account needed"

    def available(self) -> bool:
        installed = (config.MLX_ROOT / ".venv" / "bin" / "python").is_file()
        # Weights are checked too, not just the install. The MLX CLI
        # silently downloads anything missing, which presents as a hang.
        return installed and config.mlx_weights_present(config.MLX_DIT)

    def generate(self, prompt, init_audio, noise, duration, seed):
        with tempfile.TemporaryDirectory() as workdir:
            guide_path = Path(workdir) / "guide.wav"
            out_path = Path(workdir) / "out.wav"

            # The MLX CLI wants 44.1kHz 16-bit PCM specifically.
            sf.write(guide_path, init_audio, config.SAMPLE_RATE, subtype="PCM_16")

            command = [
                str(config.MLX_ROOT / ".venv" / "bin" / "python"),
                str(config.MLX_ROOT / "scripts" / "sa3_mlx.py"),
                "--prompt", prompt,
                "--init-audio", str(guide_path),
                "--init-noise-level", str(noise),
                "--dit", config.MLX_DIT,
                "--decoder", config.MLX_DECODERS[config.MLX_DIT],
                "--seconds", str(int(round(duration))),
                "--steps", str(config.DEFAULT_STEPS),
                "--seed", str(seed),
                "--out", str(out_path),
            ]

            log.info("mlx: %s dit=%s noise=%.2f", prompt[:60], config.MLX_DIT, noise)
            result = subprocess.run(
                command, cwd=config.MLX_ROOT, capture_output=True, text=True, timeout=900
            )

            if result.returncode != 0 or not out_path.exists():
                # Surface the last line of stderr rather than the whole
                # progress-bar dump, which is mostly carriage returns.
                detail = (result.stderr or result.stdout).strip().splitlines()
                raise RuntimeError(f"sa3_mlx failed: {detail[-1] if detail else 'no output'}")

            audio, _ = sf.read(out_path, dtype="float32")
            return _to_mono_numpy(audio)


# --- api ----------------------------------------------------------------


class StabilityAPIBackend:
    """Stability's hosted audio-to-audio endpoint.

    Responses are cached on disk keyed by the full request, so re-running
    an identical generation costs neither a credit nor a round trip.
    """

    id = "api"
    label = "Stability API — large"
    note = "best quality, uses credits, needs network"

    def available(self) -> bool:
        return config.STABILITY_API_KEY is not None

    def generate(self, prompt, init_audio, noise, duration, seed):
        cache_key = _cache_key(prompt, init_audio, noise, duration, seed)
        cached = config.CACHE_DIR / f"{cache_key}.wav"
        if cached.exists():
            log.info("api cache hit %s", cache_key[:8])
            audio, _ = sf.read(cached, dtype="float32")
            return _to_mono_numpy(audio)

        wav_bytes = io.BytesIO()
        sf.write(wav_bytes, init_audio, config.SAMPLE_RATE, format="WAV")
        wav_bytes.seek(0)

        response = httpx.post(
            config.STABILITY_API_URL,
            headers={
                "Authorization": f"Bearer {config.STABILITY_API_KEY}",
                "Accept": "audio/*",
            },
            files={"audio": ("guide.wav", wav_bytes, "audio/wav")},
            data={
                "prompt": prompt,
                "strength": str(noise),
                "duration": str(int(duration)),
                "seed": str(seed),
                "output_format": "wav",
            },
            timeout=180.0,
        )
        response.raise_for_status()

        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(response.content)

        audio, _ = sf.read(io.BytesIO(response.content), dtype="float32")
        return _to_mono_numpy(audio)


# --- registry -----------------------------------------------------------

BACKENDS: dict[str, Backend] = {
    "mock": MockBackend(),
    "local": LocalBackend(),
    "api": StabilityAPIBackend(),
}

# Tried in order when the requested backend fails mid-generation.
FALLBACK_ORDER = ["local", "mock"]


def get(backend_id: str | None) -> Backend:
    backend_id = backend_id or config.DEFAULT_BACKEND
    if backend_id not in BACKENDS:
        raise ValueError(f"unknown backend: {backend_id}. Choose from {list(BACKENDS)}")
    return BACKENDS[backend_id]


def describe() -> list[dict]:
    """Backend list for the UI selector, with live availability."""
    return [
        {"id": b.id, "label": b.label, "note": b.note, "available": b.available()}
        for b in BACKENDS.values()
    ]


def generate_with_fallback(
    backend_id: str | None,
    prompt: str,
    init_audio: np.ndarray,
    noise: float,
    duration: float,
    seed: int,
) -> tuple[np.ndarray, str]:
    """Generate, degrading to a working backend if the chosen one fails.

    Returns (audio, backend_actually_used). A live demo must never show a
    stack trace because the venue wifi dropped — it should quietly produce
    something and tell the user what happened.
    """
    chosen = get(backend_id)
    candidates = [chosen] + [BACKENDS[i] for i in FALLBACK_ORDER if BACKENDS[i].id != chosen.id]

    last_error: Exception | None = None
    for backend in candidates:
        if not backend.available():
            continue
        try:
            audio = backend.generate(prompt, init_audio, noise, duration, seed)
            return audio, backend.id
        except Exception as error:  # noqa: BLE001 - any failure should fall through
            log.warning("backend %s failed: %s", backend.id, error)
            last_error = error

    raise RuntimeError(f"all backends failed; last error: {last_error}")


# --- conversion helpers -------------------------------------------------


def _cache_key(prompt: str, init_audio: np.ndarray, noise: float, duration: float, seed: int) -> str:
    digest = hashlib.sha256()
    digest.update(prompt.encode())
    digest.update(init_audio.tobytes())
    digest.update(f"{noise}|{duration}|{seed}".encode())
    return digest.hexdigest()


def _to_mono_numpy(audio) -> np.ndarray:
    """Whatever a backend returned -> mono float32 numpy."""
    audio = np.asarray(audio, dtype=np.float32)

    if audio.ndim == 1:
        return audio
    # Average whichever axis is the channel axis (the short one).
    channel_axis = int(np.argmin(audio.shape))
    return audio.mean(axis=channel_axis).astype(np.float32)
