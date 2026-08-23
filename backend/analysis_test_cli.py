"""Create cleaned audio and musical-analysis metadata without generating stems.

    uv run analysis-test --input samples/vocal.wav
    uv run analysis-test --input samples/beatbox.wav --mode beatbox
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path

import soundfile as sf

from . import analysis, melody
from .analysis_export import build_metadata, write_metadata
from .config import ANALYSIS_TESTS_DIR
from .preprocess import preprocess_for_analysis


def _clean_output_directory() -> int:
    """Remove prior analysis-test artifacts while retaining the parent folder."""
    if not ANALYSIS_TESTS_DIR.exists():
        print(f"analysis-tests directory does not exist: {ANALYSIS_TESTS_DIR}")
        return 0

    removed = 0
    for child in ANALYSIS_TESTS_DIR.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
        removed += 1
    print(f"removed {removed} analysis-test run(s) from {ANALYSIS_TESTS_DIR}")
    return 0


def _output_directory(requested: str | None, force: bool) -> Path:
    if requested:
        output = Path(requested)
    else:
        # Lexicographically sortable while retaining the date and time down
        # to the second, e.g. 2026-08-22_16-43-09.
        output = ANALYSIS_TESTS_DIR / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        suffix = 1
        while output.exists():
            output = ANALYSIS_TESTS_DIR / f"{datetime.now():%Y-%m-%d_%H-%M-%S}-{suffix:02d}"
            suffix += 1
    if output.exists() and any(output.iterdir()) and not force:
        raise FileExistsError(f"output directory is not empty: {output}; use --force to overwrite")
    output.mkdir(parents=True, exist_ok=True)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(prog="analysis-test", description=__doc__)
    parser.add_argument("--input", help="path to a vocal or beatbox recording")
    parser.add_argument("--clean", action="store_true", help="remove all prior runs from analysis-tests/")
    parser.add_argument("--output", help="output directory; defaults to analysis-tests/YYYY-MM-DD_HH-MM-SS")
    parser.add_argument("--mode", choices=("voice", "beatbox"), default="voice")
    parser.add_argument("--no-trim", action="store_true", help="keep leading and trailing silence")
    parser.add_argument("--no-high-pass", action="store_true", help="disable conservative high-pass filtering")
    parser.add_argument("--force", action="store_true", help="allow writing into a non-empty output directory")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    if args.clean:
        if args.input or args.output:
            parser.error("--clean cannot be combined with --input or --output")
        return _clean_output_directory()
    if not args.input:
        parser.error("--input is required unless --clean is used")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        output = _output_directory(args.output, args.force)
        cleaned, sr, quality = preprocess_for_analysis(
            args.input,
            mode=args.mode,
            trim=not args.no_trim,
            high_pass=not args.no_high_pass,
        )
        result = analysis.analyze(cleaned, sr)
        notes = melody.track(cleaned, sr)

        cleaned_path = output / "cleaned.wav"
        sf.write(cleaned_path, cleaned, sr, subtype="FLOAT")
        metadata = build_metadata(
            original_filename=Path(args.input).name,
            quality=quality,
            analysis=result,
            notes=notes,
        )
        write_metadata(output / "metadata.json", metadata)
    except (OSError, ValueError, FileNotFoundError) as error:
        parser.error(str(error))

    print(f"analysis test: {output}")
    print(f"  cleaned:  {cleaned_path}")
    print(f"  metadata: {output / 'metadata.json'}")
    print(f"  analysis: {result.bpm:.1f} BPM · {result.key} {result.mode} · {len(result.bars)} bars")
    print(f"  melody:   {len(notes)} notes, {len(metadata['analysis']['pitches'])} unique pitches")
    for warning in quality.warnings:
        print(f"  warning:  {warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
