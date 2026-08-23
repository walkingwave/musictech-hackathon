"""Headless runner, for the dev loop.

Faster than clicking through the UI when tuning prompts or noise values:

    uv run btg --input samples/vocal.wav --part bass
    uv run btg --input samples/vocal.wav --part bass --noise 0.6 --backend local
    uv run btg --input samples/vocal.wav --all --backend api
    uv run btg --input samples/vocal.wav --part bass --sweep 0.5,0.65,0.8,0.9
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys

from . import config, pipeline, sa3_backend
from .models import PARTS


def main() -> int:
    parser = argparse.ArgumentParser(prog="btg", description=__doc__)
    parser.add_argument("--input", required=True, help="path to the vocal audio file")
    parser.add_argument("--part", choices=PARTS, help="which backing part to generate")
    parser.add_argument("--hum-target", choices=("melody", "bass"),
                        help="transform the recorded hum into this MIDI-guided part")
    parser.add_argument("--all", action="store_true", help="generate every part")
    parser.add_argument("--style", default="", help='free-text style, e.g. "bossa nova"')
    parser.add_argument("--noise", type=float,
                        help="0-1; higher diverges further from the guide. "
                             "Default is per-part (see config.PART_NOISE)")
    parser.add_argument("--backend", choices=list(sa3_backend.BACKENDS),
                        help=f"default: {config.DEFAULT_BACKEND}")
    parser.add_argument("--seed", type=int, help="fixed seed, for reproducible output")
    parser.add_argument("--sweep", help="comma-separated noise values to compare")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    config.ensure_dirs()

    if not args.part and not args.all and not args.hum_target:
        parser.error("pass --part <name>, --all, or --hum-target")
    if args.hum_target and (args.part or args.all):
        parser.error("--hum-target cannot be combined with --part or --all")

    session, analysis = pipeline.analyze_vocal(args.input)
    print(f"\nsession {session.id}")
    print(f"  {analysis.bpm:.1f} BPM · {analysis.key} {analysis.mode} · {len(analysis.bars)} bars")
    print(f"  chords: {' '.join(bar.chord for bar in analysis.bars[:8])}")

    if args.hum_target:
        result = pipeline.generate_from_hum(
            session, target=args.hum_target, prompt=args.style, noise=args.noise,
            backend=args.backend, seed=args.seed,
        )
        print(f"\n  hum -> {args.hum_target}")
        print(f"    backend: {result.backend_used}  seed: {result.seed}")
        print(f"    audio:   {session.root / result.wav_path}")
        print(f"    midi:    {session.root / result.midi_path}")
        return 0

    parts = list(PARTS) if args.all else [args.part]
    noise_values = [float(v) for v in args.sweep.split(",")] if args.sweep else [args.noise]
    # None means "let the pipeline pick the per-part default".

    for part in parts:
        for noise in noise_values:
            result = pipeline.generate_stem(
                session, part, style=args.style, noise=noise,
                backend=args.backend, seed=args.seed,
            )

            # Every generation writes to stems/<part>.wav, so a sweep would
            # overwrite itself. Keep a copy per noise value to compare.
            audio_path = session.root / result.wav_path
            if args.sweep:
                audio_path = shutil.copy(
                    audio_path, session.root / "stems" / f"{part}_noise{noise}.wav"
                )

            print(f"\n  {part} @ noise={noise}")
            print(f"    backend: {result.backend_used}  seed: {result.seed}")
            print(f"    audio:   {audio_path}")

    print(f"\nall output under {session.root}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
