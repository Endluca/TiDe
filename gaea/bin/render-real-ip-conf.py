#!/usr/bin/env python3

from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import sys


def main() -> int:
    raw_networks = os.environ.get("TIDE_TRUSTED_PROXY_CIDRS", "").strip()
    if not raw_networks:
        raw_networks = os.environ.get("TIT_TRUSTED_PROXY_IPS", "")
    values = [value.strip() for value in raw_networks.split(",") if value.strip()]
    if not values:
        raise SystemExit(
            "TIDE_TRUSTED_PROXY_CIDRS or TIT_TRUSTED_PROXY_IPS is required"
        )

    networks: list[str] = []
    for value in values:
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise SystemExit(
                f"TIDE_TRUSTED_PROXY_CIDRS contains an invalid IP/CIDR: {value}"
            ) from exc
        if network.prefixlen == 0:
            raise SystemExit(
                "TIDE_TRUSTED_PROXY_CIDRS must not trust an entire address family"
            )
        networks.append(network.with_prefixlen)

    if len(sys.argv) > 2:
        raise SystemExit("usage: render-real-ip-conf.py [destination]")
    destination = Path(
        sys.argv[1] if len(sys.argv) == 2 else "/etc/nginx/conf.d/00-real-ip.conf"
    )
    temporary = destination.with_suffix(".conf.tmp")
    lines = [*(f"set_real_ip_from {network};" for network in networks)]
    lines.extend(
        [
            "real_ip_header X-Forwarded-For;",
            "real_ip_recursive on;",
            "",
        ]
    )
    temporary.write_text("\n".join(lines), encoding="ascii")
    temporary.replace(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
