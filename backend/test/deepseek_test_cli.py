"""Validate DeepSeek interpretation against musical context extracted from audio.

The tool runs the same preprocessing/analysis path as ``analysis-test``, then
passes only bounded musical metadata—not audio bytes or filesystem paths—to
the production DeepSeek interpreter.

    uv run deepseek-test --input samples/fixtures/amin_100.wav \
        --prompt "add upright bass and Rhodes in bossa nova"
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from .. import interpret
from ..analysis_export import write_metadata
from ..grooves import ALL as GROOVES
from ..models import PARTS
from .analysis_test_cli import run_analysis
from .run_artifacts import create_run_directory


def context_from_metadata(metadata: dict[str, Any], existing_parts: list[str], style: str) -> interpret.Context:
    """Map serialized analysis output into the bounded production context."""
    musical = metadata["analysis"]
    return interpret.Context(
        style=style,
        bpm=float(musical["tempo_bpm"]),
        key=str(musical["key"]),
        mode=str(musical["mode"]),
        bars=len(musical["chords"]),
        existing_parts=existing_parts,
    )


def validate_plan(plan: interpret.Plan, *, expect_tracks: bool) -> list[dict[str, str]]:
    """Return all contract failures instead of stopping at the first one."""
    failures: list[dict[str, str]] = []
    grooves = {groove.name for groove in GROOVES}
    if expect_tracks and not plan.tracks:
        failures.append({"check": "tracks", "message": "expected at least one requested track"})
    if plan.groove not in grooves:
        failures.append({"check": "groove", "message": f"unsupported groove: {plan.groove}"})
    if plan.bpm is not None and not 20 <= plan.bpm <= 300:
        failures.append({"check": "bpm", "message": f"BPM outside 20..300: {plan.bpm}"})
    if plan.bars is not None and not 1 <= plan.bars <= 128:
        failures.append({"check": "bars", "message": f"bars outside 1..128: {plan.bars}"})
    if plan.mode not in (None, "major", "minor"):
        failures.append({"check": "mode", "message": f"unsupported mode: {plan.mode}"})
    for index, track in enumerate(plan.tracks):
        if track.part not in PARTS:
            failures.append({"check": f"tracks[{index}].part", "message": f"unsupported part: {track.part}"})
        if not track.name.strip():
            failures.append({"check": f"tracks[{index}].name", "message": "track name is empty"})
    return failures


def _write_json(path: Path, value: dict[str, Any]) -> None:
    """Use the project's atomic JSON writer for all generated JSON artifacts."""
    write_metadata(path, value)


def main() -> int:
    parser = argparse.ArgumentParser(prog="deepseek-test", description=__doc__)
    parser.add_argument("--input", required=True, help="vocal or beatbox audio to analyze")
    parser.add_argument("--prompt", required=True, help="plain-English arrangement request")
    parser.add_argument("--mode", choices=("voice", "beatbox"), default="voice")
    parser.add_argument("--style", default="", help="existing session style supplied as context")
    parser.add_argument("--existing-part", action="append", default=[], choices=PARTS,
                        help="existing session part; may be repeated")
    parser.add_argument("--output", help="explicit run directory; non-empty directories require --force")
    parser.add_argument("--force", action="store_true", help="allow writing into a non-empty output directory")
    parser.add_argument("--require-deepseek", action="store_true",
                        help="fail if credentials/API/validation causes rules fallback")
    parser.add_argument("--expect-tracks", action="store_true",
                        help="fail when the returned plan contains no tracks")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    if not args.prompt.strip():
        parser.error("--prompt cannot be empty")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    output = create_run_directory("deepseek_test", output=args.output, force=args.force)
    try:
        analysis_run = run_analysis(args.input, mode=args.mode, output=output, force=True)
        context = context_from_metadata(analysis_run.metadata, args.existing_part, args.style)
        request_artifact = {
            "schema_version": 1,
            "prompt": args.prompt,
            "model": interpret.config.BTG_AGENT_MODEL,
            "context": context.model_dump(),
            "contains_credentials": False,
            "contains_audio": False,
        }
        _write_json(output / "deepseek_request.json", request_artifact)

        plan, source = interpret.interpret_with_source(args.prompt, context)
        failures = validate_plan(plan, expect_tracks=args.expect_tracks)
        if args.require_deepseek and source != "deepseek":
            failures.append({"check": "interpreter", "message": "DeepSeek was required but rules fallback ran"})

        _write_json(output / "deepseek_response.json", {
            "schema_version": 1,
            "interpreter": source,
            "plan": plan.model_dump(),
        })
        _write_json(output / "validation.json", {
            "schema_version": 1,
            "passed": not failures,
            "interpreter": source,
            "checks": failures,
            "analysis_metadata": "metadata.json",
            "request": "deepseek_request.json",
            "response": "deepseek_response.json",
        })
    except Exception as error:  # record failures without exposing credentials or raw HTTP headers
        _write_json(output / "validation.json", {
            "schema_version": 1,
            "passed": False,
            "error_type": type(error).__name__,
            "error": str(error),
        })
        print(f"DeepSeek validation failed; artifacts: {output}", file=sys.stderr)
        print(f"  {type(error).__name__}: {error}", file=sys.stderr)
        return 1

    print(f"DeepSeek validation: {output}")
    print(f"  analysis: {analysis_run.analysis.bpm:.1f} BPM · {analysis_run.analysis.key} {analysis_run.analysis.mode}")
    print(f"  interpreter: {source}")
    print(f"  validation: {'passed' if not failures else 'failed'}")
    if failures:
        for failure in failures:
            print(f"  {failure['check']}: {failure['message']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
