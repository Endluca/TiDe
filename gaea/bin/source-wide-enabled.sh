#!/bin/sh

set -eu

source_wide_enabled="${TIT_SOURCE_WIDE_ENABLED-true}"

case "${source_wide_enabled}" in
  true | false)
    printf '%s\n' "${source_wide_enabled}"
    ;;
  *)
    printf '%s\n' \
      'TIT_SOURCE_WIDE_ENABLED must be exactly true or false when set' >&2
    exit 64
    ;;
esac
