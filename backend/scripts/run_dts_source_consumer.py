from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.dts_source_consumer import (
    DtsConsumerSettings,
    DtsEventProcessor,
    DtsKafkaShadowConsumer,
    InMemoryShadowSink,
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


def main() -> int:
    args = build_parser().parse_args()
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


if __name__ == "__main__":
    raise SystemExit(main())
