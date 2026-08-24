#!/bin/sh

set -eu

enabled="${TIT_V2_RUNTIME_ENABLED-false}"
case "${enabled}" in
  true | false)
    printf '%s\n' "${enabled}"
    ;;
  *)
    printf '%s\n' \
      'TIT_V2_RUNTIME_ENABLED must be exactly true or false when set' >&2
    exit 64
    ;;
esac
