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
identity_override=false
if [ "${query}" != "${contract_url}" ]; then
  old_ifs="${IFS}"
  IFS='&'
  for parameter in ${query}; do
    key="${parameter%%=*}"
    value="${parameter#*=}"
    case "${key}" in
      "" | [!a-z_]* | *[!a-z0-9_]*)
        fail "DATABASE_URL query parameter name is not canonical"
        ;;
    esac
    case "${key}" in
      sslmode)
        sslmode_count=$((sslmode_count + 1))
        sslmode_value="${value}"
        ;;
      ssl)
        conflicting_ssl=true
        ;;
      database | dbname | host | hostaddr | options | port | service | servicefile | user)
        identity_override=true
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
else
  if [ "${PGDATABASE+x}" = x ] \
    || [ "${PGHOST+x}" = x ] \
    || [ "${PGHOSTADDR+x}" = x ] \
    || [ "${PGPORT+x}" = x ] \
    || [ "${PGSERVICE+x}" = x ] \
    || [ "${PGSERVICEFILE+x}" = x ] \
    || [ "${PGUSER+x}" = x ]; then
    fail "approved private-line probe forbids ambient libpq connection identity variables"
  fi
  [ "${identity_override}" = "false" ] \
    || fail "DATABASE_URL must not override connection identity in query parameters"
  [ "${sslmode_count}" -eq 1 ] \
    && [ "${sslmode_value}" = "disable" ] \
    || fail "approved private-line DATABASE_URL must contain exactly one sslmode=disable"
  [ "${conflicting_ssl}" = "false" ] \
    || fail "DATABASE_URL must not contain a conflicting ssl parameter"

  url_without_scheme="${contract_url#postgresql://}"
  case "${url_without_scheme}" in
    */*) ;;
    *) fail "approved private-line DATABASE_URL is missing a database path" ;;
  esac
  authority="${url_without_scheme%%/*}"
  path_and_query="${url_without_scheme#*/}"
  case "${authority}" in
    *@*) ;;
    *) fail "approved private-line DATABASE_URL must include tide_sys_admin" ;;
  esac
  userinfo="${authority%@*}"
  host_and_port="${authority##*@}"
  username="${userinfo%%:*}"
  database_name="${path_and_query%%\?*}"
  [ "${username}" = "tide_sys_admin" ] \
    || fail "approved private-line DATABASE_URL must use tide_sys_admin"
  [ "${host_and_port}" = "tide-system.rwlb.singapore.rds.aliyuncs.com:5432" ] \
    || fail "approved private-line DATABASE_URL endpoint is not allowlisted"
  [ "${database_name}" = "tide_system_test" ] \
    || fail "approved private-line DATABASE_URL must select tide_system_test"
  [ "${expected_database}" = "tide_system_test" ] \
    || fail "approved private-line expected database must be tide_system_test"
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
