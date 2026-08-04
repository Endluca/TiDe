#!/bin/sh

set -eu

template_path="${1:-/etc/nginx/nginx.conf.template}"
config_path="${2:-/etc/nginx/nginx.conf}"

case "${MEMORY_SIZE:-}" in
  micro|small|medium|large)
    NGINX_WORKER_PROCESSES=4
    ;;
  ""|2xlarge)
    NGINX_WORKER_PROCESSES=8
    ;;
  4xlarge|8xlarge|16xlarge|32xlarge|64xlarge)
    NGINX_WORKER_PROCESSES=16
    ;;
  *)
    NGINX_WORKER_PROCESSES=2
    ;;
esac

if [ "${NGINX_WORKER_PROCESSES}" -gt 16 ]; then
  NGINX_WORKER_PROCESSES=16
fi
export NGINX_WORKER_PROCESSES

envsubst '${NGINX_WORKER_PROCESSES}' < "${template_path}" > "${config_path}"
echo "Rendered nginx.conf with worker_processes=${NGINX_WORKER_PROCESSES} for MEMORY_SIZE=${MEMORY_SIZE:-<unset>}." >&2
