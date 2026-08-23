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
import time
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
        init_audio: np.ndarray | None,
        noise: float,
        duration: float,
        seed: int,
    ) -> np.ndarray:
        """Guide audio + prompt -> generated mono audio at config.SAMPLE_RATE.

        `init_audio` may be None, in which case the model generates from the
        prompt alone. That matters for instrument samples: conditioning a
        flute on a sawtooth makes it inherit the sawtooth.

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
        if init_audio is None:
            n = int(duration * config.SAMPLE_RATE)
            return (rng.standard_normal(n).astype(np.float32) * 0.05)
        grain = rng.standard_normal(len(init_audio)).astype(np.float32) * 0.05
        return ((1 - noise) * init_audio + noise * (init_audio + grain)).astype(np.float32)


# --- local --------------------------------------------------------------
#
# "local" is a single option in the UI, but Stable Audio 3 ships two very
# different local runtimes and which one can run depends on the machine:
#
#   MLX   Stability's Apple-Silicon build. A separate checkout with its own
#         venv, driven as a subprocess. Fast on a Mac, exists nowhere else.
#   Torch The PyTorch package (`stable_audio_3`), imported in-process. Runs
#         on any platform — CPU everywhere, CUDA on NVIDIA — so it is what
#         makes the local backend work on Windows and Linux. The `small`
#         DiTs run on CPU; `medium` needs CUDA and Flash Attention 2.
#
# LocalBackend picks whichever runtime is actually installed, preferring MLX
# on a Mac because it is the faster of the two there.


class _MLXRuntime:
    """Stability's MLX build, running on Apple Silicon as a subprocess.

    Shelling out rather than importing keeps the MLX stack's dependency tree
    (mlx, its own numpy pin) completely separate from ours. The cost is a
    process launch per generation, which is small next to sampling time.
    """

    label = f"{config.MLX_DIT} (MLX)"

    def status(self) -> str:
        """ready | not-installed. MLX weights are ungated, so no access step."""
        return "ready" if self.available() else "not-installed"

    def available(self) -> bool:
        installed = config.mlx_venv_python().is_file()
        # Weights are checked too, not just the install. The MLX CLI
        # silently downloads anything missing, which presents as a hang.
        return installed and config.mlx_weights_present(config.MLX_DIT)

    def generate(self, prompt, init_audio, noise, duration, seed):
        with tempfile.TemporaryDirectory() as workdir:
            guide_path = Path(workdir) / "guide.wav"
            out_path = Path(workdir) / "out.wav"

            command = [
                str(config.mlx_venv_python()),
                str(config.MLX_ROOT / "scripts" / "sa3_mlx.py"),
                "--prompt", prompt,
                "--dit", config.MLX_DIT,
                "--decoder", config.MLX_DECODERS[config.MLX_DIT],
                "--seconds", str(int(round(duration))),
                "--steps", str(config.DEFAULT_STEPS),
                "--seed", str(seed),
                "--out", str(out_path),
            ]

            # No guide means text-to-audio: the model builds the sound from
            # the prompt alone rather than reshaping something.
            if init_audio is not None:
                # The MLX CLI wants 44.1kHz 16-bit PCM specifically.
                sf.write(guide_path, init_audio, config.SAMPLE_RATE, subtype="PCM_16")
                command += ["--init-audio", str(guide_path), "--init-noise-level", str(noise)]

            log.info("mlx: %s dit=%s noise=%s", prompt[:60], config.MLX_DIT,
                     f"{noise:.2f}" if init_audio is not None else "text-to-audio")
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


class _TorchRuntime:
    """The PyTorch `stable_audio_3` package, run in-process.

    Installed with `uv sync --extra local`. Works on any platform, which is
    what gives Windows and Linux a real local model. The model is loaded
    lazily and kept, so the first generation pays the load cost and the rest
    do not — the constructor still stays cheap, as the Backend protocol asks.
    """

    label = f"{config.TORCH_DIT} (PyTorch)"

    # How long a non-ready status probe is trusted before re-checking. A
    # "ready" result is cached forever (access does not get revoked mid-run);
    # a "no access" or "offline" result is re-probed after this, so granting
    # access on Hugging Face takes effect without a server restart.
    _PROBE_TTL = 30.0

    def __init__(self):
        self._model = None
        self._status = None  # (status_str, checked_at)

    def installed(self) -> bool:
        import importlib.util

        return (
            importlib.util.find_spec("stable_audio_3") is not None
            and importlib.util.find_spec("torch") is not None
        )

    def status(self) -> str:
        """One of: ready | no-access | offline | not-installed.

        Cheap and cached. "ready" means either the weights are already in the
        HF cache (usable offline) or the gated repo is accessible with the
        current token. Anything else means picking `local` would fail, so the
        selector should say so rather than silently falling back to mock.
        """
        now = time.monotonic()
        if self._status is not None:
            value, checked = self._status
            if value == "ready" or now - checked < self._PROBE_TTL:
                return value
        value = self._probe()
        self._status = (value, now)
        return value

    def _probe(self) -> str:
        if not self.installed():
            return "not-installed"
        try:
            from stable_audio_3.model_configs import models
            from huggingface_hub import auth_check, try_to_load_from_cache
            from huggingface_hub.utils import GatedRepoError, RepositoryNotFoundError
        except Exception:  # noqa: BLE001 - treat any import failure as not installed
            return "not-installed"

        cfg = models.get(config.TORCH_DIT)
        if cfg is None:
            return "no-access"  # unknown DiT name; nothing we can verify

        # Already downloaded once -> usable with no network at all.
        if isinstance(try_to_load_from_cache(cfg.repo_id, cfg.ckpt_path), str):
            return "ready"

        try:
            auth_check(cfg.repo_id)  # raises if the gated repo is not accessible
            return "ready"
        except (GatedRepoError, RepositoryNotFoundError):
            return "no-access"
        except Exception:  # noqa: BLE001 - network down, not cached: cannot confirm
            return "offline"

    def available(self) -> bool:
        return self.status() == "ready"

    def _load(self):
        if self._model is None:
            from stable_audio_3 import StableAudioModel

            log.info("loading stable_audio_3 %s (first run only)", config.TORCH_DIT)
            model = StableAudioModel.from_pretrained(config.TORCH_DIT)
            # Prefer CUDA when the build exposes it; small DiTs run on CPU too.
            try:
                import torch

                if torch.cuda.is_available():
                    model = model.to("cuda")
            except Exception:  # noqa: BLE001 - device move is best-effort
                pass
            self._model = model
        return self._model

    def generate(self, prompt, init_audio, noise, duration, seed):
        import torch

        model = self._load()

        kwargs = dict(prompt=prompt, duration=int(round(duration)))
        if init_audio is not None:
            # torchaudio's convention: (channels, samples) float tensor + rate.
            waveform = torch.from_numpy(np.ascontiguousarray(init_audio)).unsqueeze(0)
            kwargs["init_audio"] = (waveform, config.SAMPLE_RATE)
            kwargs["init_noise_level"] = float(noise)
        if seed is not None:
            kwargs["seed"] = int(seed)

        log.info("torch: %s dit=%s noise=%s", prompt[:60], config.TORCH_DIT,
                 f"{noise:.2f}" if init_audio is not None else "text-to-audio")
        try:
            audio = model.generate(**kwargs)
        except TypeError:
            # Older/newer builds may not accept `seed`; retry without it rather
            # than failing the whole backend over one optional kwarg.
            kwargs.pop("seed", None)
            audio = model.generate(**kwargs)

        return _torch_to_mono_numpy(audio)


class LocalBackend:
    """The local Stable Audio 3 model, on whatever runtime this machine has.

    MLX on Apple Silicon (faster there), PyTorch everywhere else. Presented
    as one `local` option so the UI does not have to know which is running.
    """

    id = "local"

    # Notes per non-ready state, so the disabled option says *why* and what to
    # do about it rather than just "unavailable".
    _NOTES = {
        "no-access": (
            "needs Hugging Face access — accept the licence at "
            "huggingface.co/stabilityai/stable-audio-3-small-music"
        ),
        "offline": "installed, but can't reach Hugging Face to verify access",
        "not-installed": "not installed — run: uv sync --extra local",
    }

    def __init__(self):
        # MLX first: on a Mac it is the faster runtime; off a Mac it is never
        # available, so this collapses to the PyTorch runtime elsewhere.
        self._runtimes = [_MLXRuntime(), _TorchRuntime()]

    def _active(self):
        return next((r for r in self._runtimes if r.available()), None)

    @property
    def label(self) -> str:
        active = self._active()
        return f"Local — {active.label}" if active else "Local"

    @property
    def note(self) -> str:
        if self._active() is not None:
            return "free, offline, no account needed"
        # Not runnable: report the most actionable runtime's status. Torch is
        # the one installable everywhere, so prefer its reason unless it is
        # simply absent and MLX has a more specific one.
        statuses = [r.status() for r in self._runtimes]
        for state in ("no-access", "offline", "not-installed"):
            if state in statuses:
                return self._NOTES[state]
        return "not installed — run: uv sync --extra local"

    def available(self) -> bool:
        return self._active() is not None

    def generate(self, prompt, init_audio, noise, duration, seed):
        active = self._active()
        if active is None:
            raise RuntimeError("no local runtime installed (MLX or PyTorch)")
        return active.generate(prompt, init_audio, noise, duration, seed)


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
        if init_audio is None:
            raise RuntimeError("the hosted endpoint requires a guide track")
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
    init_audio: np.ndarray | None,
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


def _torch_to_mono_numpy(audio) -> np.ndarray:
    """Whatever `StableAudioModel.generate` returned -> mono float32 numpy.

    Builds differ: some return a bare tensor, some a (waveform, sample_rate)
    tuple like torchaudio.load. Handle both, and move off the GPU/autograd
    before converting.
    """
    if isinstance(audio, tuple):
        audio = audio[0]
    if hasattr(audio, "detach"):  # a torch.Tensor
        audio = audio.detach().to("cpu").numpy()
    return _to_mono_numpy(audio)


def _to_mono_numpy(audio) -> np.ndarray:
    """Whatever a backend returned -> mono float32 numpy."""
    audio = np.asarray(audio, dtype=np.float32)

    if audio.ndim == 1:
        return audio
    # Average whichever axis is the channel axis (the short one).
    channel_axis = int(np.argmin(audio.shape))
    return audio.mean(axis=channel_axis).astype(np.float32)
