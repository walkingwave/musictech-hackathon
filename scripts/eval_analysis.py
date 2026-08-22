"""Score tempo and key detection against the fixture ground truth.

    uv run python scripts/eval_analysis.py

Run this after any change to analysis.py. It is the only way to tell an
improvement from a lucky guess on one file.

Scoring notes:
  - Tempo is correct within 2%. Half and double time are reported
    separately, because they are a different failure from being simply
    wrong, and are often musically defensible.
  - Key is scored two ways: exact (tonic and mode both right) and
    relative (right notes, wrong tonic — e.g. C major for A minor). The
    relative column is the one worth watching; it is the failure mode
    that plain profile correlation cannot fix.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.analysis import analyze  # noqa: E402
from backend.theory import note_to_pitch_class  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "samples" / "fixtures"

# The hard fixtures deliberately drift by +-3% across the take, so their
# "true" BPM is a mean that no tracker can hit exactly. Holding them to
# the easy tier's tolerance would score the rubato, not the detector.
TEMPO_TOLERANCE = {"easy": 0.02, "hard": 0.04}


def relative_key_pc(key: str, mode: str) -> int:
    """Pitch class of the relative major, so relatives compare equal."""
    pc = note_to_pitch_class(key)
    return pc if mode == "major" else (pc + 3) % 12


def main() -> int:
    manifest_path = FIXTURES / "manifest.json"
    if not manifest_path.exists():
        print("no fixtures. Run: uv run python scripts/make_test_vocals.py")
        return 1

    cases = json.loads(manifest_path.read_text())
    scores: dict[str, dict[str, int]] = {
        tier: {"n": 0, "tempo": 0, "tempo_octave": 0, "key": 0, "noteset": 0}
        for tier in ("easy", "hard")
    }

    print(f"{'file':<16} {'BPM':>16}  {'key':>22}   result")
    print("-" * 74)

    for case in cases:
        audio, sr = sf.read(FIXTURES / case["file"], dtype="float32")
        result = analyze(audio, sr)

        tier = "hard" if case.get("hard") else "easy"
        score = scores[tier]
        score["n"] += 1

        # --- tempo ---
        tolerance = TEMPO_TOLERANCE[tier]
        ratio = result.bpm / case["bpm"]
        if abs(ratio - 1) <= tolerance:
            score["tempo"] += 1
            tempo_mark = "ok"
        elif any(abs(ratio - factor) <= tolerance for factor in (0.5, 2.0)):
            score["tempo_octave"] += 1
            tempo_mark = "octave"
        else:
            tempo_mark = "FAIL"

        # --- key ---
        exact = result.key == case["key"] and result.mode == case["mode"]
        same_notes = relative_key_pc(result.key, result.mode) == relative_key_pc(
            case["key"], case["mode"]
        )
        if exact:
            score["key"] += 1
            key_mark = "ok"
        elif same_notes:
            key_mark = "relative"
        else:
            key_mark = "FAIL"
        if same_notes:
            score["noteset"] += 1

        print(
            f"{case['file']:<16} "
            f"{result.bpm:>6.1f}/{case['bpm']:<3} {tempo_mark:<6} "
            f"{result.key + ' ' + result.mode:>10} / {case['key'] + ' ' + case['mode']:<9} "
            f"{key_mark}"
        )

    print("-" * 74)
    for tier, score in scores.items():
        if not score["n"]:
            continue
        n = score["n"]
        print(
            f"{tier:<5} n={n:<3} "
            f"tempo {score['tempo']}/{n}  "
            f"(+-octave {score['tempo'] + score['tempo_octave']}/{n})   "
            f"key {score['key']}/{n}  "
            f"(note-set {score['noteset']}/{n})"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
