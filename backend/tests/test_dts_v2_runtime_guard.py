from __future__ import annotations

from app.dts_v2_runtime_guard import (
    DtsV2RuntimeGuardError,
    DtsV2RuntimeTransactionState,
    PostgresDtsV2PrimaryTransactionGuard,
    guarded_runtime_state,
)


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def one(self):
        return self.value


class _Connection:
    def __init__(self, values):
        self.values = list(values)
        self.sql: list[str] = []

    def execute(self, statement, parameters=None):
        self.sql.append(str(statement))
        return _Result(self.values.pop(0))


def test_domain_guard_accepts_shadow_generation_zero_and_reads_mode() -> None:
    connection = _Connection(
        [0, "V1_COMPAT_DUAL_CAPTURE", 0]
    )
    generation = PostgresDtsV2PrimaryTransactionGuard().acquire(
        connection, component="DOMAIN"
    )
    assert generation == 0
    assert "tit.dts_v2_mode" in connection.sql[1]


def test_outbox_guard_closes_without_primary_but_requires_mode_readback() -> None:
    connection = _Connection([None, "ROLLED_BACK"])
    assert (
        PostgresDtsV2PrimaryTransactionGuard().acquire(
            connection, component="OUTBOX"
        )
        is None
    )


def test_guarded_runtime_state_is_typed_and_rejects_missing_state() -> None:
    assert guarded_runtime_state(
        _Connection([("V2_PRIMARY", 7)])
    ) == DtsV2RuntimeTransactionState("V2_PRIMARY", 7)
    try:
        guarded_runtime_state(_Connection([("V2_PRIMARY", None)]))
    except DtsV2RuntimeGuardError as exc:
        assert str(exc) == "DTS_V2_RUNTIME_GUARDED_STATE_REQUIRED"
    else:
        raise AssertionError("missing transaction state must fail closed")
