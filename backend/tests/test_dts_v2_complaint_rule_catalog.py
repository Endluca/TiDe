from __future__ import annotations

import pytest

from app.dts_v2_complaint_rule_catalog import (
    DtsV2ComplaintRuleCatalogError,
    PostgresDtsV2ComplaintRuleCatalog,
)


SHA = "a" * 64


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _Connection:
    def __init__(self, value=None):
        self.calls = []
        self.value = value or {
            "protocol_version": "complaint-rule-publication-result-v2",
            "status": "PUBLISHED",
            "replay_status": "APPLIED",
            "source_sha256": SHA,
            "previous_source_sha256": None,
            "activation_generation": 1,
            "publication_revision": 2,
            "content_hash": "b" * 64,
            "affected_category_count": 1,
            "courses_seen": 2,
            "courses_enqueued": 2,
            "courses_noop": 0,
            "publication_audit_id": "complaint-rule-publication:audit",
        }

    def execute(self, statement, params):
        self.calls.append((str(statement), dict(params)))
        return _Result(self.value)


def test_publish_calls_only_the_protected_catalog_command() -> None:
    connection = _Connection()
    result = PostgresDtsV2ComplaintRuleCatalog().publish(
        connection,
        source_sha256=SHA,
        expected_revision=0,
        idempotency_key="publish-a",
    )

    assert result["activation_generation"] == 1
    sql, params = connection.calls[0]
    assert "publish_complaint_rule_import_v2" in sql
    assert "INSERT " not in sql and "UPDATE " not in sql and "DELETE " not in sql
    assert params["idempotency_key"] == "publish-a"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"source_sha256": "A" * 64},
        {"expected_revision": -1},
        {"idempotency_key": "bad key"},
    ],
)
def test_publish_rejects_invalid_command_identity(kwargs) -> None:
    values = {
        "source_sha256": SHA,
        "expected_revision": 0,
        "idempotency_key": "publish-a",
    }
    values.update(kwargs)
    with pytest.raises(DtsV2ComplaintRuleCatalogError):
        PostgresDtsV2ComplaintRuleCatalog().publish(_Connection(), **values)
