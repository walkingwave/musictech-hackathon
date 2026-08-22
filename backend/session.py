"""On-disk session storage.

One directory per session, holding the vocal, the guides, the stems, the
MIDI, and a meta.json recording how each stem was produced. That manifest
is the provenance record: prompt, backend, seed and noise are enough to
reproduce any stem exactly.

    sessions/<uuid>/
      vocal.wav
      meta.json
      guides/bass.wav
      stems/bass.wav
      midi/bass.mid
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from .config import SAMPLE_RATE, SESSIONS_DIR
from .models import Analysis, StemResult


@dataclass
class Session:
    """A working directory plus the analysis and stems produced in it."""

    id: str
    root: Path

    # --- lifecycle -----------------------------------------------------

    @classmethod
    def create(cls, vocal: np.ndarray, sr: int) -> "Session":
        session_id = uuid.uuid4().hex[:12]
        session = cls(id=session_id, root=SESSIONS_DIR / session_id)

        for subdir in ("guides", "stems", "midi"):
            (session.root / subdir).mkdir(parents=True, exist_ok=True)

        sf.write(session.vocal_path, vocal, sr)
        session._write_meta({"id": session_id, "analysis": None, "stems": {}})
        return session

    @classmethod
    def load(cls, session_id: str) -> "Session":
        root = SESSIONS_DIR / session_id
        if not root.is_dir():
            raise FileNotFoundError(f"no such session: {session_id}")
        return cls(id=session_id, root=root)

    # --- paths ---------------------------------------------------------

    @property
    def vocal_path(self) -> Path:
        return self.root / "vocal.wav"

    @property
    def meta_path(self) -> Path:
        return self.root / "meta.json"

    def guide_path(self, part: str) -> Path:
        return self.root / "guides" / f"{part}.wav"

    def stem_path(self, part: str) -> Path:
        return self.root / "stems" / f"{part}.wav"

    def midi_path(self, part: str) -> Path:
        return self.root / "midi" / f"{part}.mid"

    # --- audio ---------------------------------------------------------

    def read_vocal(self) -> tuple[np.ndarray, int]:
        audio, sr = sf.read(self.vocal_path, dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        return audio, sr

    def write_audio(self, path: Path, audio: np.ndarray) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(path, audio, SAMPLE_RATE)

    # --- metadata ------------------------------------------------------

    def _read_meta(self) -> dict:
        return json.loads(self.meta_path.read_text())

    def _write_meta(self, meta: dict) -> None:
        self.meta_path.write_text(json.dumps(meta, indent=2))

    @property
    def analysis(self) -> Analysis:
        meta = self._read_meta()
        if meta.get("analysis") is None:
            raise ValueError(f"session {self.id} has not been analyzed yet")
        return Analysis.from_dict(meta["analysis"])

    def save_analysis(self, analysis: Analysis) -> None:
        meta = self._read_meta()
        meta["analysis"] = analysis.to_dict()
        self._write_meta(meta)

    def save_stem(self, result: StemResult) -> None:
        meta = self._read_meta()
        meta["stems"][result.part] = result.to_dict()
        self._write_meta(meta)

    def to_dict(self) -> dict:
        return self._read_meta()
