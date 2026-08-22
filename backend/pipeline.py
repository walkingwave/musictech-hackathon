"""The pipeline: vocal in, backing stems out.

This module is the one place that knows the order of operations. Every
other backend module does exactly one stage and knows nothing about the
others, so this is where to look to understand the flow.

    analyze_vocal()  stage 1
    generate_stem()  stages 2-5, for one part
"""

from __future__ import annotations

import logging
import random

import numpy as np
import soundfile as sf

from . import align, arrange, prompts, render_guide, sa3_backend
from .analysis import analyze
from .config import DEFAULT_NOISE, SAMPLE_RATE
from .models import Analysis, Part, StemResult
from .session import Session

log = logging.getLogger(__name__)


def analyze_vocal(vocal_path) -> tuple[Session, Analysis]:
    """Stage 1. Create a session from an audio file and analyze it."""
    audio, sr = sf.read(vocal_path, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    if sr != SAMPLE_RATE:
        import librosa

        audio = librosa.resample(audio, orig_sr=sr, target_sr=SAMPLE_RATE)
        sr = SAMPLE_RATE

    session = Session.create(audio, sr)
    analysis = analyze(audio, sr)
    session.save_analysis(analysis)

    log.info(
        "session %s: %.1f BPM, %s %s, %d bars",
        session.id, analysis.bpm, analysis.key, analysis.mode, len(analysis.bars),
    )
    return session, analysis


def generate_stem(
    session: Session,
    part: Part,
    style: str = "",
    noise: float = DEFAULT_NOISE,
    backend: str | None = None,
    seed: int | None = None,
) -> StemResult:
    """Stages 2-5 for one part: arrange, render a guide, generate, align.

    The guide track is what makes the output land in time and in key. It
    carries the rhythm and harmony through the model's noised latent, so
    what comes back has the right skeleton and a real instrument's timbre.
    """
    analysis = session.analysis
    vocal, sr = session.read_vocal()
    seed = seed if seed is not None else random.randint(0, 2**31 - 1)

    # Stage 2: notes on the grid.
    midi = arrange.arrange(part, analysis, vocal, sr)
    midi_path = session.midi_path(part)
    midi.write(str(midi_path))

    # Stage 3: a rough audio rendering of those notes.
    guide = render_guide.render(midi, duration=analysis.duration, part=part)
    session.write_audio(session.guide_path(part), guide)

    # Stage 4: hand the guide to Stable Audio 3.
    prompt = prompts.build(part, analysis, style)
    log.info("generating %s [%s] seed=%d", part, prompt, seed)

    raw, backend_used = sa3_backend.generate_with_fallback(
        backend_id=backend,
        prompt=prompt,
        init_audio=guide,
        noise=noise,
        duration=analysis.duration,
        seed=seed,
    )

    # Stage 5: correct whatever drift the model introduced.
    stem = align.align(raw, guide, target_bpm=analysis.bpm)
    session.write_audio(session.stem_path(part), stem)

    result = StemResult(
        part=part,
        wav_path=str(session.stem_path(part).relative_to(session.root)),
        midi_path=str(midi_path.relative_to(session.root)),
        backend_used=backend_used,
        prompt=prompt,
        noise=noise,
        seed=seed,
    )
    session.save_stem(result)
    return result


def mix(session: Session, parts: list[Part], include_vocal: bool = True) -> np.ndarray:
    """Sum the generated stems (and optionally the vocal) into one preview."""
    vocal, _ = session.read_vocal()
    tracks = [vocal] if include_vocal else []

    for part in parts:
        path = session.stem_path(part)
        if path.exists():
            audio, _ = sf.read(path, dtype="float32")
            tracks.append(audio)

    if not tracks:
        return np.zeros(0, dtype=np.float32)

    length = max(len(t) for t in tracks)
    total = np.zeros(length, dtype=np.float32)
    for track in tracks:
        total[: len(track)] += track

    # Headroom, so summing four stems does not clip.
    peak = float(np.max(np.abs(total)))
    return total if peak == 0 else (total / peak * 0.9).astype(np.float32)
