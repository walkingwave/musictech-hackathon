"""Create cleaned audio and musical-analysis metadata without generating stems.

    uv run analysis-test --input samples/vocal.wav
    uv run analysis-test --input samples/beatbox.wav --mode beatbox
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf

from .. import analysis, melody
from ..analysis_export import build_metadata, write_metadata
from ..preprocess import InputQuality, preprocess_for_analysis
from .run_artifacts import TEST_RUNS_DIR, create_run_directory


@dataclass
class AnalysisRun:
    """Artifacts and in-memory results from one analysis validation run."""

    output: Path
    cleaned_path: Path
    metadata_path: Path
    metadata: dict
    analysis: object
    notes: list
    quality: InputQuality


def clean_test_runs() -> int:
    """Remove only generated validation artifacts, retaining the run root."""
    if not TEST_RUNS_DIR.exists():
        print(f"test-run directory does not exist: {TEST_RUNS_DIR}")
        return 0

    removed = 0
    for child in TEST_RUNS_DIR.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        elif child.is_file() or child.is_symlink():
            child.unlink()
        else:
            continue
        removed += 1
    print(f"removed {removed} test run(s) from {TEST_RUNS_DIR}")
    return 0


def run_analysis(
    input_path: str | Path,
    *,
    mode: str = "voice",
    trim: bool = True,
    high_pass: bool = True,
    output: str | Path | None = None,
    force: bool = False,
    test_name: str = "analysis_test",
) -> AnalysisRun:
    """Run the analysis CLI pipeline and persist its cleaned WAV and metadata."""
    destination = create_run_directory(test_name, output=output, force=force)
    cleaned, sample_rate, quality = preprocess_for_analysis(
        input_path,
        mode=mode,
        trim=trim,
        high_pass=high_pass,
    )
    result = analysis.analyze(cleaned, sample_rate)
    notes = melody.track(cleaned, sample_rate)

    cleaned_path = destination / "cleaned.wav"
    metadata_path = destination / "metadata.json"
    sf.write(cleaned_path, cleaned, sample_rate, subtype="FLOAT")
    metadata = build_metadata(
        original_filename=Path(input_path).name,
        quality=quality,
        analysis=result,
        notes=notes,
    )
    write_metadata(metadata_path, metadata)
    return AnalysisRun(destination, cleaned_path, metadata_path, metadata, result, notes, quality)


def main() -> int:
    parser = argparse.ArgumentParser(prog="analysis-test", description=__doc__)
    parser.add_argument("--input", help="path to a vocal or beatbox recording")
    parser.add_argument("--clean", action="store_true", help="remove all prior generated test runs")
    parser.add_argument("--output", help="explicit output directory; non-empty directories require --force")
    parser.add_argument("--mode", choices=("voice", "beatbox"), default="voice")
    parser.add_argument("--no-trim", action="store_true", help="keep leading and trailing silence")
    parser.add_argument("--no-high-pass", action="store_true", help="disable conservative high-pass filtering")
    parser.add_argument("--force", action="store_true", help="allow writing into a non-empty output directory")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    if args.clean:
        if args.input or args.output:
            parser.error("--clean cannot be combined with --input or --output")
        return clean_test_runs()
    if not args.input:
        parser.error("--input is required unless --clean is used")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        run = run_analysis(
            args.input,
            mode=args.mode,
            trim=not args.no_trim,
            high_pass=not args.no_high_pass,
            output=args.output,
            force=args.force,
        )
    except (OSError, ValueError, FileNotFoundError) as error:
        parser.error(str(error))

    print(f"analysis test: {run.output}")
    print(f"  cleaned:  {run.cleaned_path}")
    print(f"  metadata: {run.metadata_path}")
    print(f"  analysis: {run.analysis.bpm:.1f} BPM · {run.analysis.key} {run.analysis.mode} · {len(run.analysis.bars)} bars")
    print(f"  melody:   {len(run.notes)} notes, {len(run.metadata['analysis']['pitches'])} unique pitches")
    for warning in run.quality.warnings:
        print(f"  warning:  {warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
