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


# --- separation cleanup -------------------------------------------------

# The soft mask never fully mutes a bin: hard spectral gates produce
# "musical noise" (random tinkling), which is worse than the residue.
_MASK_FLOOR = 0.12
# How far above its own noise floor a bin must sit to pass untouched.
_GATE_BETA = 1.6
# Downward expansion below this fraction of the stem's own loud level.
_EXPAND_THRESHOLD = 0.12


def cleanup_separated(stem: np.ndarray, part: str) -> np.ndarray:
    """Reduce separation residue without generating anything new.

    Demucs leaves two audible artefacts: a broadband low-level smear under
    the whole stem (the "grain"), and other instruments faintly bleeding in
    the gaps where this part is not playing (the "mud"). Both live well
    below the actual notes, so both respond to level-domain treatment:

      spectral gate   per-frequency soft Wiener mask against the stem's own
                      noise floor — pulls down bins that never rise much
                      above their floor, which is exactly what residue does.
      expander        pushes the near-silent stretches further down, so the
                      gaps between notes are gaps rather than a wash of the
                      rest of the band.

    Everything here is subtractive and conservative — the audio that comes
    out is only ever the audio that went in, quieter in the wrong places.
    """
    out = np.asarray(stem, dtype=np.float32)
    if not out.size or float(np.abs(out).max()) < 1e-5:
        return out
    out = _spectral_gate(out)
    out = _expand_quiet(out)
    return out.astype(np.float32)


def _spectral_gate(audio: np.ndarray) -> np.ndarray:
    from scipy.signal import istft, stft

    _, _, spec = stft(audio, fs=SAMPLE_RATE, nperseg=2048, noverlap=1536)
    magnitude = np.abs(spec)

    # Each frequency bin's own noise floor: the level it idles at when the
    # instrument is not actively using it.
    floor = np.percentile(magnitude, 20, axis=1, keepdims=True)
    reference = (_GATE_BETA * floor) ** 2
    mask = magnitude**2 / (magnitude**2 + reference + 1e-12)
    mask = np.maximum(mask, _MASK_FLOOR)

    _, cleaned = istft(spec * mask, fs=SAMPLE_RATE, nperseg=2048, noverlap=1536)
    cleaned = np.asarray(cleaned, dtype=np.float32)
    # stft/istft round-trip can differ by a few samples.
    if len(cleaned) < len(audio):
        cleaned = np.pad(cleaned, (0, len(audio) - len(cleaned)))
    return cleaned[: len(audio)]


def _expand_quiet(audio: np.ndarray) -> np.ndarray:
    from scipy.signal import sosfilt, butter

    # Short-window envelope, smoothed so the gain does not pump.
    window = max(1, int(0.05 * SAMPLE_RATE))
    kernel = np.ones(window, dtype=np.float32) / window
    envelope = np.sqrt(np.convolve(np.square(audio), kernel, mode="same") + 1e-12)
    envelope = sosfilt(butter(1, 20.0, btype="lowpass", fs=SAMPLE_RATE, output="sos"), envelope)
    envelope = np.abs(np.asarray(envelope, dtype=np.float32)) + 1e-9

    loud = float(np.percentile(envelope, 95))
    threshold = _EXPAND_THRESHOLD * loud
    if threshold <= 0:
        return audio
    # 2:1 downward expansion below the threshold, unity above.
    ratio = np.minimum(1.0, envelope / threshold)
    gain = np.where(envelope >= threshold, 1.0, ratio).astype(np.float32)
    return audio * gain
