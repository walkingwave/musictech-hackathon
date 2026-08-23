"""One-shot samples for an instrument, so MIDI can be played back exactly.

Rendering a whole bar through audio-to-audio asks the model to reproduce a
melody, and it does not: measured against a played C major scale it
returned C G C A C A D. A sawtooth guide holds the notes far better than a
sine, but only because the output then keeps the guide's spectrum — which
means it is polishing the guide rather than inventing an instrument.

So do not ask it to play the part at all. Generate a handful of sustained
one-shots across the range, and let a sampler play the MIDI. The notes are
then exactly what was played, editing needs no regeneration, and the model
only has to do the thing it is good at: make one note sound like something.

Samples are cached on disk by prompt, so loading the same instrument onto
another track costs nothing.
"""

from __future__ import annotations

import hashlib
import logging

import numpy as np
import pretty_midi
import soundfile as sf

from . import config, render_guide, sa3_backend

log = logging.getLogger(__name__)

# Three samples covering C3-C5, the range most parts are played in.
#
# Deliberately few. Every pitch is a generation, and with retries five
# pitches took the better part of a minute per instrument — long enough
# that loading one reads as nothing happening at all. Three keeps it to
# roughly fifteen seconds while still putting a source within about an
# octave of any note that gets played.
DEFAULT_PITCHES = (48, 60, 72)  # C3 C4 C5

# Long enough to hold a sustained note plus release. Sampler playback
# truncates to the MIDI note's length anyway.
SAMPLE_SECONDS = 3.0

# High on purpose. The guide is a sawtooth, and at low divergence the model
# returns that sawtooth with a polish rather than an instrument: measured
# against flute, cello and piano prompts at 0.6, every output correlated
# 0.96-0.98 with the guide's spectrum and the three differed from each
# other by 0.02 — indistinguishable. At 0.85 they differ by 0.81.
#
# The cost is pitch: at 0.85 the model drifts, sometimes by an octave. That
# is affordable only because each sample's true pitch is measured after the
# fact and the sampler transposes from it (see detect_pitch). Do not lower
# this to fix a tuning problem — it trades the instrument away for it.
SAMPLE_NOISE = 0.85

# The sampler transposes from whatever pitch a sample actually landed on,
# so drift is survivable — but a large stretch at playback audibly slows or
# speeds the tone. Retry to land close, and keep the closest attempt.
# A fifth. The sampler transposes from wherever a sample landed, so drift
# is not fatal — only a large stretch at playback is. Tightening this to a
# major third tripled generation time to 38s an instrument, because nearly
# every pitch retried; the tolerance costs far less than the wait.
MAX_DRIFT = 7
RETRIES = 1


def instrument_id(prompt: str) -> str:
    """Stable id for a prompt, so identical instruments share their cache."""
    return hashlib.sha256(prompt.strip().lower().encode()).hexdigest()[:16]


def sample_dir(prompt: str):
    return config.CACHE_DIR / "instruments" / instrument_id(prompt)


def sample_path(prompt: str, pitch: int):
    return sample_dir(prompt) / f"{pitch}.wav"


def _one_note_guide(pitch: int, seconds: float) -> np.ndarray:
    """A single sustained note, rendered with harmonics.

    Sawtooth rather than sine on purpose: measured across a noise sweep, a
    saw guide kept 100% of played pitches up to 0.6 divergence where a sine
    guide fell to 0%. A sine gives the model almost nothing to lock onto.
    """
    midi = pretty_midi.PrettyMIDI(initial_tempo=120)
    instrument = pretty_midi.Instrument(program=0, name="sample")
    instrument.notes.append(
        pretty_midi.Note(velocity=100, pitch=pitch, start=0.0, end=seconds)
    )
    midi.instruments.append(instrument)

    previous = render_guide.WAVEFORMS.get("free")
    render_guide.WAVEFORMS["free"] = "saw"
    try:
        return render_guide.render(midi, duration=seconds, part="free")
    finally:
        render_guide.WAVEFORMS["free"] = previous


def generate_samples(
    prompt: str,
    pitches: tuple[int, ...] = DEFAULT_PITCHES,
    seconds: float = SAMPLE_SECONDS,
    backend: str | None = None,
    seed: int | None = None,
    force: bool = False,
) -> list[dict]:
    """Generate (or reuse) one sustained one-shot per pitch."""
    directory = sample_dir(prompt)
    directory.mkdir(parents=True, exist_ok=True)

    out = []
    for index, pitch in enumerate(pitches):
        path = sample_path(prompt, pitch)

        if force or not path.exists():
            guide = _one_note_guide(pitch, seconds)
            best = None

            for attempt in range(RETRIES + 1):
                # Seeds vary per pitch and per attempt: one seed across the
                # set produces samples that share artifacts and sound cloned
                # rather than like one instrument across its range.
                offset = index + attempt * 101
                audio, backend_used = sa3_backend.generate_with_fallback(
                    backend_id=backend,
                    prompt=f"{prompt}, single sustained note, one shot, no reverb tail",
                    init_audio=guide,
                    noise=SAMPLE_NOISE,
                    duration=seconds,
                    seed=(seed + offset) if seed is not None else None,
                )
                audio = _trim_and_fade(audio)
                actual = detect_pitch(audio) or pitch
                drift = abs(actual - pitch)

                if best is None or drift < best[2]:
                    best = (audio, actual, drift)
                if drift <= MAX_DRIFT:
                    break
                log.info("pitch %d drifted %+d, retrying", pitch, actual - pitch)

            audio, actual, drift = best
            # Deliberately NOT retuned. Resampling a sample onto its
            # intended pitch drags its whole spectrum with it: measured
            # across four instruments it collapsed three of four centroids
            # to under 600Hz and cut distinctness from 0.76 to 0.27 — it
            # gives back exactly the timbre that high divergence bought.
            # The sampler transposes from the detected pitch instead,
            # which is what a sampler does anyway.
            sf.write(path, audio, config.SAMPLE_RATE)
            log.info(
                "sampled %s at %d (sounds %d) via %s",
                instrument_id(prompt), pitch, actual, backend_used,
            )

        actual = detect_pitch(sf.read(path, dtype="float32")[0])
        if actual is not None and actual != pitch:
            log.info("sample for %d actually sounds %d; sampler will compensate", pitch, actual)

        out.append({"pitch": pitch, "actual_pitch": actual or pitch, "path": str(path)})

    return out


def detect_pitch(audio: np.ndarray) -> int | None:
    """The sample's actual fundamental, as a MIDI note number.

    Stored alongside each sample so the sampler transposes from the pitch
    that was *generated* rather than the one that was asked for. The model
    still occasionally jumps an octave, and measuring beats hoping.

    Uses the strongest spectral peak below 1.5kHz rather than a pitch
    tracker: on a bright sawtooth-derived tone, trackers routinely lock to
    the first harmonic and report an octave high.
    """
    import librosa

    window = audio[: config.SAMPLE_RATE * 2]
    if len(window) < 2048:
        return None

    spectrum = np.abs(np.fft.rfft(window))
    freqs = np.fft.rfftfreq(len(window), 1 / config.SAMPLE_RATE)
    band = (freqs > 40) & (freqs < 1500)
    if not band.any():
        return None

    peak = freqs[band][int(np.argmax(spectrum[band]))]
    return int(round(float(librosa.hz_to_midi(peak))))


def _retune(audio: np.ndarray, semitones: float, seconds: float) -> np.ndarray:
    """Resample a note onto a different pitch.

    Kept for callers that genuinely want a fixed pitch, but NOT used when
    generating instrument samples: shifting a sample onto its intended
    pitch moves its whole spectrum, which undoes the timbre that the high
    divergence was there to produce. See generate_samples.
    """
    if abs(semitones) < 0.5:
        return audio

    ratio = 2 ** (semitones / 12)
    source = np.arange(len(audio), dtype=np.float64)
    target = np.arange(0, len(audio), ratio, dtype=np.float64)
    shifted = np.interp(target, source, audio).astype(np.float32)

    want = int(seconds * config.SAMPLE_RATE)
    if len(shifted) >= want:
        return shifted[:want]
    # Shifting down leaves it short; loop the sustain to fill rather than
    # padding with silence, which would end the note early.
    tail = shifted[len(shifted) // 4 :]
    while len(shifted) < want and len(tail):
        shifted = np.concatenate([shifted, tail])
    return shifted[:want]


def _trim_and_fade(audio: np.ndarray, threshold: float = 0.02) -> np.ndarray:
    """Drop leading silence and fade the tail.

    Leading silence would delay every note the sampler triggers, turning a
    tight part into a sloppy one. The fade stops the sampler clicking when
    it cuts a sample short for a brief note.
    """
    loud = np.flatnonzero(np.abs(audio) > threshold)
    if len(loud):
        audio = audio[loud[0] :]

    fade = min(int(0.02 * config.SAMPLE_RATE), len(audio) // 4)
    if fade > 0:
        audio = audio.copy()
        audio[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
    return audio
