from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.dts_source_metadata_observer import (
    DtsSourceMetadataObserverError,
    DtsSourceMetadataObserverSettings,
    OfficialJavaEventJsonlSource,
    observe_official_java_metadata,
    write_observation_report_atomic,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Aggregate value-blind source metadata from offline official_java "
            "EVENT JSONL. This command never connects to DTS or PostgreSQL."
        )
    )
    parser.add_argument("--region", choices=("dom", "ovs"))
    parser.add_argument("--input-jsonl")
    parser.add_argument("--output")
    parser.add_argument("--max-events")
    parser.add_argument("--max-duration-seconds")
    return parser


def _settings_with_cli_overrides(
    args: argparse.Namespace,
    environ: Mapping[str, str],
) -> DtsSourceMetadataObserverSettings:
    values = dict(environ)
    overrides = {
        "TIT_DTS_SOURCE_METADATA_REGION": args.region,
        "TIT_DTS_SOURCE_METADATA_INPUT_JSONL": args.input_jsonl,
        "TIT_DTS_SOURCE_METADATA_OUTPUT": args.output,
        "TIT_DTS_SOURCE_METADATA_MAX_EVENTS": args.max_events,
        "TIT_DTS_SOURCE_METADATA_MAX_DURATION_SECONDS": (
            args.max_duration_seconds
        ),
    }
    for name, value in overrides.items():
        if value is not None:
            values[name] = value
    return DtsSourceMetadataObserverSettings.from_env(values)


def _run(
    args: argparse.Namespace,
    *,
    environ: Mapping[str, str] | None = None,
    source: Iterable[Mapping[str, object]] | None = None,
) -> int:
    values = os.environ if environ is None else environ
    settings = _settings_with_cli_overrides(args, values)
    event_source = (
        OfficialJavaEventJsonlSource(settings.input_jsonl)
        if source is None
        else source
    )
    report = observe_official_java_metadata(
        event_source,
        source_region=settings.source_region,
        max_events=settings.max_events,
        max_duration_seconds=settings.max_duration_seconds,
    )
    write_observation_report_atomic(
        settings.output_path,
        report.canonical_json,
    )
    print(report.canonical_json, flush=True)
    return 0


def main() -> int:
    try:
        return _run(build_parser().parse_args())
    except DtsSourceMetadataObserverError as exc:
        error_code = str(exc)
    except Exception:
        error_code = "DTS_SOURCE_METADATA_OBSERVER_UNEXPECTED_ERROR"
    print(
        json.dumps(
            {"error_code": error_code},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=sys.stderr,
        flush=True,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
