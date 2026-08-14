from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.dts_source_consumer import (
    DtsConfigurationError,
    DtsConsumerSettings,
    DtsEventProcessor,
    DtsKafkaShadowConsumer,
    DtsRecordError,
    InMemoryShadowSink,
    safe_kafka_error_diagnostic,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate one Aliyun DTS subscription by decoding and routing "
            "events without writing the target database."
        )
    )
    parser.add_argument("--max-messages", type=int, default=100)
    parser.add_argument("--idle-timeout-ms", type=int, default=10_000)
    parser.add_argument(
        "--commit-offsets",
        action="store_true",
        help=(
            "Explicitly advance the DTS consumer-group position after each "
            "successfully decoded/routed event. Omit for a replay-only check."
        ),
    )
    return parser


def _run(args: argparse.Namespace) -> int:
    settings = DtsConsumerSettings.from_env()
    sink = InMemoryShadowSink()
    consumer = DtsKafkaShadowConsumer(
        settings,
        DtsEventProcessor(sink),
        idle_timeout_ms=args.idle_timeout_ms,
    )
    result = consumer.run(
        max_messages=args.max_messages,
        commit_offsets=args.commit_offsets,
    )
    print(
        json.dumps(
            {
                "mode": "DTS_SHADOW_NO_TARGET_WRITE",
                "connection": settings.safe_summary(),
                "result": result,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    try:
        return _run(build_parser().parse_args())
    except (DtsConfigurationError, DtsRecordError) as exc:
        payload: dict[str, bool | str] = {
            "status": "error",
            "error_code": str(exc),
        }
    except Exception as exc:
        diagnostic = safe_kafka_error_diagnostic(
            exc,
            fallback_error_code="DTS_SHADOW_KAFKA_REQUEST_FAILED",
        )
        payload = {
            "status": "error",
            **(
                diagnostic
                if diagnostic is not None
                else {
                    "error_code": "DTS_SHADOW_UNEXPECTED_ERROR",
                    "error_type": "UnexpectedError",
                    "retriable": False,
                }
            ),
        }
    print(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        file=sys.stderr,
        flush=True,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
