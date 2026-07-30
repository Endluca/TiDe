#!/bin/sh
set -eu

fail() {
  printf 'contract probe refused to start: %s\n' "$1" >&2
  exit 1
}

database_url="${DATABASE_URL:-}"
expected_database="${TIDE_CONTRACT_PROBE_EXPECTED_DATABASE:-}"
require_ssl="${TIDE_CONTRACT_PROBE_REQUIRE_SSL:-true}"
probe_sql_file="${TIDE_CONTRACT_PROBE_SQL_FILE:-/contract-probe.sql}"

[ -n "${database_url}" ] || fail "DATABASE_URL is required"
[ -n "${expected_database}" ] \
  || fail "TIDE_CONTRACT_PROBE_EXPECTED_DATABASE is required"
[ -r "${probe_sql_file}" ] || fail "contract probe SQL file is not readable"
case "${expected_database}" in
  *[!a-z0-9_]* | [0-9]* | "")
    fail "expected database name is not a safe PostgreSQL identifier"
    ;;
esac
case "${require_ssl}" in
  true | false) ;;
  *) fail "TIDE_CONTRACT_PROBE_REQUIRE_SSL must be true or false" ;;
esac

case "${database_url}" in
  postgresql+psycopg://*)
    contract_url="postgresql://${database_url#postgresql+psycopg://}"
    ;;
  postgresql://*)
    contract_url="${database_url}"
    ;;
  *)
    fail "DATABASE_URL must use PostgreSQL"
    ;;
esac

case "${contract_url}" in
  *" "* | *"
"*)
    fail "DATABASE_URL contains whitespace"
    ;;
esac

query="${contract_url#*\?}"
sslmode_count=0
sslmode_value=""
conflicting_ssl=false
if [ "${query}" != "${contract_url}" ]; then
  old_ifs="${IFS}"
  IFS='&'
  for parameter in ${query}; do
    key="${parameter%%=*}"
    value="${parameter#*=}"
    case "${key}" in
      sslmode)
        sslmode_count=$((sslmode_count + 1))
        sslmode_value="${value}"
        ;;
      ssl)
        conflicting_ssl=true
        ;;
    esac
  done
  IFS="${old_ifs}"
fi

if [ "${require_ssl}" = "true" ]; then
  [ "${sslmode_count}" -eq 1 ] \
    && [ "${sslmode_value}" = "verify-full" ] \
    || fail "production DATABASE_URL must contain exactly one sslmode=verify-full"
  [ "${conflicting_ssl}" = "false" ] \
    || fail "DATABASE_URL must not contain a conflicting ssl parameter"
fi

# The database grants are the primary write barrier; this session-level guard
# makes accidental writes fail even if a future grant drifts.
PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=60000 -c lock_timeout=5000"
export PGOPTIONS

exec psql \
  -X \
  --no-password \
  -v ON_ERROR_STOP=1 \
  -v expected_database="${expected_database}" \
  -v require_ssl="${require_ssl}" \
  "${contract_url}" \
  -f "${probe_sql_file}"
