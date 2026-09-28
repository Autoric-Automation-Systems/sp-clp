"""Where the dashboard can be reached.

The panel is meant to be watched, so it answers on every interface of the
machine rather than only on localhost: the operator sitting at it uses
127.0.0.1, and everyone else uses the machine name or the network address. That
is also what makes a friendly browser favourite possible, because a name has to
resolve to an address the server listens on.

SP_CLP_HOST and SP_CLP_PORT override the bind, which matters when another
service already uses port 8000 or when the panel must stay off the network.
"""

from __future__ import annotations

import os
import socket

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000

# TEST-NET-1 from RFC 5737. Connecting a UDP socket sends nothing; it only makes
# the system pick the local address it would use to reach the network.
_PROBE_TARGET = ("192.0.2.1", 9)


def bind_host() -> str:
    return os.environ.get("SP_CLP_HOST") or DEFAULT_HOST


def bind_port() -> int:
    try:
        return int(os.environ.get("SP_CLP_PORT") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def machine_name() -> str:
    """The short computer name, without the DNS suffix: PC-PLANTA.corp -> PC-PLANTA."""
    name = socket.gethostname().strip() or "localhost"
    return name.split(".")[0]


def lan_address() -> str | None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(_PROBE_TARGET)
        address = probe.getsockname()[0]
    except OSError:
        return None
    finally:
        probe.close()
    return address if address and address != "127.0.0.1" else None


def access_urls(port: int | None = None, host: str | None = None) -> list[str]:
    """Addresses to open the dashboard, friendliest first.

    Binding to localhost only leaves nothing but 127.0.0.1, so the list degrades
    to a single entry instead of advertising addresses that would not answer.
    """
    resolved_port = port or bind_port()
    if (host or bind_host()) in {"127.0.0.1", "localhost"}:
        return [f"http://localhost:{resolved_port}"]
    urls = [f"http://{machine_name()}:{resolved_port}"]
    address = lan_address()
    if address:
        urls.append(f"http://{address}:{resolved_port}")
    urls.append(f"http://localhost:{resolved_port}")
    return urls
