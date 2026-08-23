"""Stage 7: make separately generated stems sit together as a mix.

Cohesion has two halves. The generation side (shared production text, the
ensemble bed, one tone seed) makes the parts *related*; this module is the
mixing side, the things an engineer does to any multitracked band before it
reads as one record:

  carve      every part gets the frequency range it owns. The model renders
             each stem as a finished full-range recording, so a piano stem
             carries real low-end energy — stack four of those and the low
             octaves are mud, which is most of what "clashing" sounds like.
             High-passing everything but the bass and drums is the oldest
             mixing move there is.
  balance    each stem comes back normalized to its own peak, so five parts
             all play at "as loud as possible" and fight. Real mixes have a
             hierarchy: rhythm section as the floor, comping under the lead.
             Stems are levelled to per-role RMS targets instead.

Both are deliberately gentle. This is not mastering — the user has faders —
it is defaults that make the first playback sound like a band instead of an
argument.
"""

from __future__ import annotations

import logging

import numpy as np
from scipy.signal import butter, sosfilt

from .config import SAMPLE_RATE

log = logging.getLogger(__name__)

# Where each part's spectrum is allowed to start, in Hz. The bass and the
# kick own everything below ~100Hz; a chordal or lead part playing there is
# not adding music, only masking them.
HIGHPASS_HZ: dict[str, float] = {
    "bass": 30.0,
    "drums": 35.0,
    "piano": 110.0,
    "guitar": 110.0,
    "harmony": 160.0,
    "melody": 140.0,
    "free": 110.0,
    # A mix is already the whole band balanced by the model; only rumble
    # below the audible band is removed.
    "mix": 30.0,
}

# Loudness targets by part, as RMS measured over the bars the part actually
# plays. Not equal on purpose: the rhythm section is the floor everything
# stands on, comping sits under the lead, pads sit under everything.
RMS_TARGET: dict[str, float] = {
    "drums": 0.11,
    "bass": 0.11,
    "piano": 0.07,
    "guitar": 0.07,
    "harmony": 0.055,
    "melody": 0.09,
    "free": 0.08,
    "mix": 0.13,
}

# A stem can come back much quieter than its target (a sparse brushed-drums
# take), and boosting it 20dB amplifies noise and bleed more than music.
MAX_BOOST = 4.0
PEAK_CEILING = 0.95


def polish(stem: np.ndarray, part: str) -> np.ndarray:
    """Carve and balance one stem so it defaults to sitting in the mix."""
    out = np.asarray(stem, dtype=np.float32)
    if not out.size:
        return out

    out = _highpass(out, HIGHPASS_HZ.get(part, 110.0))
    out = _balance(out, RMS_TARGET.get(part, 0.08))
    return out.astype(np.float32)


def _highpass(audio: np.ndarray, cutoff_hz: float) -> np.ndarray:
    """Second-order Butterworth high-pass — a gentle 12dB/oct slope.

    Steeper filters ring; this only has to stop a piano stem competing with
    the bass an octave below its actual left hand, not surgically notch it.
    """
    if cutoff_hz <= 0:
        return audio
    sos = butter(2, cutoff_hz, btype="highpass", fs=SAMPLE_RATE, output="sos")
    return sosfilt(sos, audio).astype(np.float32)


def _balance(audio: np.ndarray, target_rms: float) -> np.ndarray:
    """Scale toward a per-role loudness, measured only where the part plays.

    The activity gate leaves whole bars of true silence in a stem, and RMS
    over the full length would read a part that plays half the time as 3dB
    quieter than it is — then boost it so its playing bars are too loud.
    Measuring over the audible samples sidesteps that.
    """
    audible = np.abs(audio) > 1e-4
    if not audible.any():
        return audio

    rms = float(np.sqrt(np.mean(np.square(audio[audible]))))
    if rms < 1e-6:
        return audio

    gain = min(target_rms / rms, MAX_BOOST)
    out = audio * gain

    peak = float(np.abs(out).max())
    if peak > PEAK_CEILING:
        out = out * (PEAK_CEILING / peak)
    return out
