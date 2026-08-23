"""Conservative preprocessing for analysis-validation runs.

This module deliberately avoids aggressive enhancement. Tempo, downbeat, pitch,
and beatbox-transient analysis depend on timing and spectral detail that heavy
noise reduction, compression, or pitch correction can damage.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf
from scipy import signal

from .config import SAMPLE_RATE

MIN_DURATION_SECONDS = 0.25
MAX_DURATION_SECONDS = 180.0
SILENCE_RELATIVE_DB = -40.0
SILENCE_PADDING_SECONDS = 0.15
VOICE_HIGH_PASS_HZ = 70.0
BEATBOX_HIGH_PASS_HZ = 30.0
TARGET_PEAK_DBFS = -3.0


@dataclass
class InputQuality:
    source_sample_rate: int
    source_channels: int
    source_duration_seconds: float
    cleaned_duration_seconds: float
    peak_dbfs: float
    rms_dbfs: float
    clipped_fraction: float
    leading_trim_seconds: float
    trailing_trim_seconds: float
    warnings: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def preprocess_for_analysis(
    input_path: str | Path,
    *,
    mode: str = "voice",
    trim: bool = True,
    high_pass: bool = True,
) -> tuple[np.ndarray, int, InputQuality]:
    """Decode and conservatively prepare one recording for musical analysis.

    Returns a mono, 44.1 kHz float32 waveform plus measurements/warnings. The
    caller is responsible for writing the resulting analysis WAV.
    """
    if mode not in ("voice", "beatbox"):
        raise ValueError("mode must be 'voice' or 'beatbox'")

    path = Path(input_path)
    if not path.is_file():
        raise FileNotFoundError(f"input file not found: {path}")

    try:
        source, source_sr = sf.read(path, dtype="float32", always_2d=True)
    except RuntimeError as error:
        raise ValueError(
            f"could not decode {path.name}. WAV/AIFF/FLAC are supported by SoundFile; "
            "convert browser WebM/Opus recordings with FFmpeg before analysis."
        ) from error

    if source.size == 0 or not np.isfinite(source).all():
        raise ValueError("input contains no finite audio samples")

    source_channels = source.shape[1]
    source_duration = len(source) / source_sr
    if source_duration < MIN_DURATION_SECONDS:
        raise ValueError(f"input is too short ({source_duration:.2f}s; minimum is {MIN_DURATION_SECONDS}s)")
    if source_duration > MAX_DURATION_SECONDS:
        raise ValueError(f"input is too long ({source_duration:.1f}s; maximum is {MAX_DURATION_SECONDS:.0f}s)")

    warnings: list[str] = []
    source_peak = float(np.max(np.abs(source)))
    clipped_fraction = float(np.mean(np.abs(source) >= 0.999))
    if clipped_fraction > 0:
        warnings.append("input contains clipped samples; re-recording is recommended")

    if source_channels > 1:
        correlation = _channel_correlation(source)
        if correlation is not None and correlation < -0.2:
            warnings.append("channels appear phase-opposed; mono downmix may reduce important audio")

    # Analysis currently expects one channel. Arithmetic mean is intentional
    # and predictable; retain the original upload separately if needed.
    audio = source.mean(axis=1, dtype=np.float32)
    if source_sr != SAMPLE_RATE:
        audio = librosa.resample(audio, orig_sr=source_sr, target_sr=SAMPLE_RATE).astype(np.float32)

    # DC offset has no musical value and can bias level/silence measurements.
    audio = (audio - np.mean(audio, dtype=np.float64)).astype(np.float32)

    leading_trim = 0.0
    trailing_trim = 0.0
    if trim:
        audio, leading_trim, trailing_trim = _trim_outer_silence(audio, SAMPLE_RATE)

    if high_pass:
        cutoff = BEATBOX_HIGH_PASS_HZ if mode == "beatbox" else VOICE_HIGH_PASS_HZ
        audio = _high_pass(audio, SAMPLE_RATE, cutoff)

    peak_before_gain = float(np.max(np.abs(audio))) if len(audio) else 0.0
    rms_before_gain = _rms(audio)
    if peak_before_gain < 1e-5 or rms_before_gain < 1e-5:
        raise ValueError("input is near silent after preprocessing; record a louder, closer signal")
    if _dbfs(rms_before_gain) < -45:
        warnings.append("input is very quiet; analysis may be unreliable")

    target_peak = 10 ** (TARGET_PEAK_DBFS / 20)
    audio = (audio * (target_peak / peak_before_gain)).astype(np.float32)

    quality = InputQuality(
        source_sample_rate=int(source_sr),
        source_channels=int(source_channels),
        source_duration_seconds=round(source_duration, 6),
        cleaned_duration_seconds=round(len(audio) / SAMPLE_RATE, 6),
        peak_dbfs=round(_dbfs(float(np.max(np.abs(audio)))), 3),
        rms_dbfs=round(_dbfs(_rms(audio)), 3),
        clipped_fraction=round(clipped_fraction, 8),
        leading_trim_seconds=round(leading_trim, 6),
        trailing_trim_seconds=round(trailing_trim, 6),
        warnings=warnings,
    )
    return audio, SAMPLE_RATE, quality


def _trim_outer_silence(audio: np.ndarray, sr: int) -> tuple[np.ndarray, float, float]:
    """Trim only outer silence and retain padding for phrase/onset context."""
    frame_length = min(2048, max(256, len(audio)))
    hop_length = min(512, max(64, frame_length // 4))
    rms = librosa.feature.rms(y=audio, frame_length=frame_length, hop_length=hop_length)[0]
    peak = float(np.max(rms)) if len(rms) else 0.0
    if peak <= 0:
        raise ValueError("input is silent")

    threshold = peak * (10 ** (SILENCE_RELATIVE_DB / 20))
    active = np.flatnonzero(rms >= threshold)
    if len(active) == 0:
        raise ValueError("input is near silent")

    padding = int(SILENCE_PADDING_SECONDS * sr)
    start = max(0, int(active[0] * hop_length) - padding)
    end = min(len(audio), int((active[-1] + 1) * hop_length + frame_length) + padding)
    if end - start < max(1, int(MIN_DURATION_SECONDS * sr)):
        raise ValueError("audible region is too short for musical analysis")

    return audio[start:end].astype(np.float32), start / sr, (len(audio) - end) / sr


def _high_pass(audio: np.ndarray, sr: int, cutoff_hz: float) -> np.ndarray:
    """Apply a gentle 2nd-order high-pass without touching very short audio."""
    if len(audio) < 32:
        return audio.astype(np.float32)
    sos = signal.butter(2, cutoff_hz, btype="highpass", fs=sr, output="sos")
    try:
        return signal.sosfiltfilt(sos, audio).astype(np.float32)
    except ValueError:
        # The filter's padding requirement is not meaningful for tiny clips.
        return signal.sosfilt(sos, audio).astype(np.float32)


def _channel_correlation(audio: np.ndarray) -> float | None:
    if audio.shape[1] < 2:
        return None
    left, right = audio[:, 0], audio[:, 1]
    if np.std(left) < 1e-8 or np.std(right) < 1e-8:
        return None
    return float(np.corrcoef(left, right)[0, 1])


def _rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))


def _dbfs(value: float) -> float:
    return float(20 * np.log10(max(value, 1e-12)))
