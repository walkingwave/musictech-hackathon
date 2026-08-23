"""Stage 3: render arranger MIDI into a rough audio guide track.

This is a deliberately crude synth. The guide only has to be
*structurally* correct — right notes, right times — because Stable Audio 3
replaces the timbre entirely. Spending effort on making it sound good
would be wasted.

Written in pure numpy on purpose: fluidsynth and SoundFonts are a system
dependency that would be one more thing to break during setup.
"""

from __future__ import annotations

import numpy as np
import pretty_midi

from .config import SAMPLE_RATE

# Per-part oscillator choice. Sawtooth has more harmonics for the model to
# latch onto; sine is cleaner for parts where pitch clarity matters more.
WAVEFORMS = {"bass": "saw", "piano": "saw", "guitar": "saw", "harmony": "sine", "melody": "saw", "mix": "saw", "free": "sine"}

# Attack/release in seconds. Short ramps prevent clicks at note edges.
ATTACK = 0.005
RELEASE = 0.03


def render(midi: pretty_midi.PrettyMIDI, duration: float, part: str) -> np.ndarray:
    """MIDI -> mono float32 guide audio of exactly `duration` seconds."""
    total_samples = int(duration * SAMPLE_RATE)
    buffer = np.zeros(total_samples, dtype=np.float32)

    for instrument in midi.instruments:
        for note in instrument.notes:
            if instrument.is_drum:
                sound = _render_drum_hit(note.pitch, note.velocity)
            else:
                sound = _render_pitched_note(
                    pitch=note.pitch,
                    seconds=note.end - note.start,
                    velocity=note.velocity,
                    waveform=WAVEFORMS.get(part, "sine"),
                )
            _mix_into(buffer, sound, at_sample=int(note.start * SAMPLE_RATE))

    return _normalize(buffer)


def _render_pitched_note(pitch: int, seconds: float, velocity: int, waveform: str) -> np.ndarray:
    frequency = 440.0 * 2 ** ((pitch - 69) / 12)
    t = np.arange(int(seconds * SAMPLE_RATE), dtype=np.float32) / SAMPLE_RATE

    if waveform == "saw":
        # Naive sawtooth. Aliasing is fine here — the model does not care.
        wave = 2.0 * ((frequency * t) % 1.0) - 1.0
    else:
        wave = np.sin(2 * np.pi * frequency * t)

    return wave * _envelope(len(t)) * (velocity / 127.0)


def _render_drum_hit(pitch: int, velocity: int) -> np.ndarray:
    """Approximate a kit piece: pitched thump for kick, filtered noise otherwise."""
    from .arrange import DRUM_HAT, DRUM_KICK

    if pitch == DRUM_KICK:
        seconds = 0.15
        t = np.arange(int(seconds * SAMPLE_RATE), dtype=np.float32) / SAMPLE_RATE
        # Pitch sweep from 120Hz down to 40Hz - the classic kick shape.
        frequency = 120 * np.exp(-t * 20) + 40
        sound = np.sin(2 * np.pi * frequency * t)
    else:
        seconds = 0.05 if pitch == DRUM_HAT else 0.12
        n = int(seconds * SAMPLE_RATE)
        noise = np.random.default_rng(pitch).standard_normal(n).astype(np.float32)
        if pitch == DRUM_HAT:
            # Crude high-pass: differencing kills low frequencies.
            noise = np.diff(noise, prepend=0.0)
        sound = noise

    decay = np.exp(-np.linspace(0, 8, len(sound), dtype=np.float32))
    return sound * decay * (velocity / 127.0)


def _envelope(n: int) -> np.ndarray:
    """Attack/release ramp, to avoid clicks at note boundaries."""
    env = np.ones(n, dtype=np.float32)
    attack = min(int(ATTACK * SAMPLE_RATE), n // 2)
    release = min(int(RELEASE * SAMPLE_RATE), n // 2)
    if attack:
        env[:attack] = np.linspace(0, 1, attack, dtype=np.float32)
    if release:
        env[-release:] = np.linspace(1, 0, release, dtype=np.float32)
    return env


def _mix_into(buffer: np.ndarray, sound: np.ndarray, at_sample: int) -> None:
    """Add `sound` into `buffer`, clipped to the buffer's bounds."""
    if at_sample >= len(buffer) or len(sound) == 0:
        return
    end = min(at_sample + len(sound), len(buffer))
    buffer[at_sample:end] += sound[: end - at_sample]


def _normalize(buffer: np.ndarray, peak: float = 0.8) -> np.ndarray:
    """Scale to a fixed peak so guides have consistent level across parts."""
    highest = float(np.max(np.abs(buffer)))
    return buffer if highest == 0 else (buffer / highest * peak).astype(np.float32)
