"""Narrow client for the protected complaint-rule publication command."""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_RESULT_FIELDS = frozenset(
    {
        "protocol_version",
        "status",
        "replay_status",
        "source_sha256",
        "previous_source_sha256",
        "activation_generation",
        "publication_revision",
        "content_hash",
        "affected_category_count",
        "courses_seen",
        "courses_enqueued",
        "courses_noop",
        "publication_audit_id",
    }
)


class DtsV2ComplaintRuleCatalogError(RuntimeError):
    """The protected catalog command returned an unprovable result."""


class PostgresDtsV2ComplaintRuleCatalog:
    def publish(
        self,
        connection: Connection,
        *,
        source_sha256: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        if not isinstance(source_sha256, str) or _SHA256.fullmatch(
            source_sha256
        ) is None:
            raise DtsV2ComplaintRuleCatalogError(
                "DTS_V2_COMPLAINT_RULE_SOURCE_SHA_INVALID"
            )
        if type(expected_revision) is not int or expected_revision < 0:
            raise DtsV2ComplaintRuleCatalogError(
                "DTS_V2_COMPLAINT_RULE_EXPECTED_REVISION_INVALID"
            )
        if not isinstance(idempotency_key, str) or _IDEMPOTENCY_KEY.fullmatch(
            idempotency_key
        ) is None:
            raise DtsV2ComplaintRuleCatalogError(
                "DTS_V2_COMPLAINT_RULE_IDEMPOTENCY_KEY_INVALID"
            )
        value = connection.execute(
            text(
                "SELECT public.publish_complaint_rule_import_v2("
                ":source_sha256,:expected_revision,:idempotency_key)"
            ),
            {
                "source_sha256": source_sha256,
                "expected_revision": expected_revision,
                "idempotency_key": idempotency_key,
            },
        ).scalar_one()
        if not isinstance(value, Mapping) or set(value) != _RESULT_FIELDS:
            raise DtsV2ComplaintRuleCatalogError(
                "DTS_V2_COMPLAINT_RULE_PUBLICATION_RESULT_INVALID"
            )
        result = dict(value)
        if (
            result.get("protocol_version")
            != "complaint-rule-publication-result-v2"
            or result.get("status") != "PUBLISHED"
            or result.get("replay_status") not in {"APPLIED", "REPLAYED"}
            or result.get("source_sha256") != source_sha256
            or not isinstance(result.get("content_hash"), str)
            or _SHA256.fullmatch(result["content_hash"]) is None
            or type(result.get("activation_generation")) is not int
            or result["activation_generation"] != expected_revision + 1
            or type(result.get("publication_revision")) is not int
            or result["publication_revision"] < 2
        ):
            raise DtsV2ComplaintRuleCatalogError(
                "DTS_V2_COMPLAINT_RULE_PUBLICATION_RESULT_INVALID"
            )
        for name in (
            "affected_category_count",
            "courses_seen",
            "courses_enqueued",
            "courses_noop",
        ):
            if type(result.get(name)) is not int or result[name] < 0:
                raise DtsV2ComplaintRuleCatalogError(
                    "DTS_V2_COMPLAINT_RULE_PUBLICATION_RESULT_INVALID"
                )
        if result["courses_seen"] != (
            result["courses_enqueued"] + result["courses_noop"]
        ):
            raise DtsV2ComplaintRuleCatalogError(
                "DTS_V2_COMPLAINT_RULE_PUBLICATION_COUNT_MISMATCH"
            )
        return result


__all__ = [
    "DtsV2ComplaintRuleCatalogError",
    "PostgresDtsV2ComplaintRuleCatalog",
]
