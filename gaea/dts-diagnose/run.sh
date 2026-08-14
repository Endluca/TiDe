#!/bin/sh
set -eu

umask 077

JAR_PATH=/deployments/dts-diagnose.jar
LOG_CONFIG_DIR=/deployments/config
LOG_DIR=/deployments/logs
CONFIG_DIR=/tmp/tit-dts-diagnose
CONFIG_PATH=${CONFIG_DIR}/config.properties
PID_PATH=/tmp/tit-dts-diagnose-java.pid
TOOL_SHA256=8a1c484a7c01fc5e684b57eb720a4757f3652027c6d1fea41bc5f69451556ef0

fail() {
  printf '{"mode":"DTS_OFFICIAL_DIAG","status":"configuration_error","error_code":"%s"}\n' "$1" >&2
  exit 64
}

require_value() {
  value=$(printenv "$1" 2>/dev/null || true)
  [ -n "$value" ] || fail "${1}_REQUIRED"
}

reject_line_breaks() {
  value=$(printenv "$1" 2>/dev/null || true)
  line_count=$(printf '%s' "$value" | wc -l | tr -d ' ')
  [ "$line_count" = "0" ] || fail "${1}_FORMAT_INVALID"
  carriage_return=$(printf '\r')
  case "$value" in
    *"$carriage_return"*) fail "${1}_FORMAT_INVALID" ;;
  esac
}

property_value() {
  # Java Properties treats backslash as an escape character. The approved DTS
  # values do not contain line breaks; double any literal backslashes here.
  printf '%s' "$1" | sed 's/\\/\\\\/g'
}

for name in \
  TIT_DTS_BROKER_URL \
  TIT_DTS_TOPIC \
  TIT_DTS_GROUP_ID \
  TIT_DTS_ACCOUNT \
  TIT_DTS_PASSWORD \
  TIT_DTS_DIAG_INIT_CHECKPOINT
do
  require_value "$name"
  reject_line_breaks "$name"
done

case "$TIT_DTS_BROKER_URL" in
  *[!A-Za-z0-9._:-]*) fail TIT_DTS_BROKER_URL_FORMAT_INVALID ;;
esac
for name in TIT_DTS_TOPIC TIT_DTS_GROUP_ID TIT_DTS_ACCOUNT
do
  value=$(printenv "$name")
  case "$value" in
    *[!A-Za-z0-9._-]*) fail "${name}_FORMAT_INVALID" ;;
  esac
done
case "$TIT_DTS_DIAG_INIT_CHECKPOINT" in
  *[!0-9]*) fail TIT_DTS_DIAG_INIT_CHECKPOINT_FORMAT_INVALID ;;
esac

diag_log_level=${TIT_DTS_DIAG_LOG_LEVEL:-TRACE}
case "$diag_log_level" in
  INFO|DEBUG|TRACE) ;;
  *) fail TIT_DTS_DIAG_LOG_LEVEL_FORMAT_INVALID ;;
esac

mkdir -p "$CONFIG_DIR" "$LOG_DIR"
rm -f "$CONFIG_PATH" "$PID_PATH"

broker=$(property_value "$TIT_DTS_BROKER_URL")
topic=$(property_value "$TIT_DTS_TOPIC")
sid=$(property_value "$TIT_DTS_GROUP_ID")
account=$(property_value "$TIT_DTS_ACCOUNT")
password=$(property_value "$TIT_DTS_PASSWORD")

{
  printf 'brokerUrl=%s\n' "$broker"
  printf 'topic=%s\n' "$topic"
  printf 'sid=%s\n' "$sid"
  printf 'userName=%s\n' "$account"
  printf 'password=%s\n' "$password"
  printf 'initCheckpoint=%s\n' "$TIT_DTS_DIAG_INIT_CHECKPOINT"
  printf 'subscribeMode=ASSIGN\n'
  printf 'isForceUseInitCheckpoint=true\n'
} > "$CONFIG_PATH"
chmod 0600 "$CONFIG_PATH"

printf '{"mode":"DTS_OFFICIAL_DIAG","status":"starting","tool_sha256":"%s","source_commit":"48596de62f01ea7d4b84082b562c33e9e5350287","broker_url":"%s","topic":"%s","sid":"%s","account":"%s","password_present":true,"init_checkpoint":%s,"subscribe_mode":"ASSIGN","force_init_checkpoint":true,"embedded_kafka_client":"1.0.0","embedded_request_timeout_default_ms":305000,"api_version_auto_timeout_supported":false,"log_level":"%s","console_log":true,"file_log":"%s/dts-new-subscribe.log"}\n' \
  "$TOOL_SHA256" "$TIT_DTS_BROKER_URL" "$TIT_DTS_TOPIC" \
  "$TIT_DTS_GROUP_ID" "$TIT_DTS_ACCOUNT" "$TIT_DTS_DIAG_INIT_CHECKPOINT" \
  "$diag_log_level" "$LOG_DIR"

java_pid=''
forward_signal() {
  if [ -n "$java_pid" ]; then
    kill -TERM "$java_pid" 2>/dev/null || true
  fi
}
cleanup() {
  rm -f "$CONFIG_PATH" "$PID_PATH"
}
trap forward_signal HUP INT TERM
trap cleanup EXIT

java \
  -Dtit.dts.diag.log.level="$diag_log_level" \
  -Ddts.log.dir="$LOG_DIR" \
  -cp "${LOG_CONFIG_DIR}:${JAR_PATH}" \
  com.aliyun.dts.subscribe.clients.DTSConsumerDemo \
  "$CONFIG_PATH" &
java_pid=$!
printf '%s\n' "$java_pid" > "$PID_PATH"
printf '{"mode":"DTS_OFFICIAL_DIAG","status":"java_started","pid":%s}\n' "$java_pid"

set +e
wait "$java_pid"
exit_code=$?
set -e
printf '{"mode":"DTS_OFFICIAL_DIAG","status":"java_exited","exit_code":%s,"log_file":"%s/dts-new-subscribe.log"}\n' \
  "$exit_code" "$LOG_DIR"
exit "$exit_code"
