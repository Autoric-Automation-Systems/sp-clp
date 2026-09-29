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
from pathlib import Path

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000

# The name the operator is invited to bookmark. It has to exist in the Windows
# resolver (hosts file, or the computer itself renamed to it), which is why the
# panel checks before advertising it.
ALIAS = "sp-clp"

HOSTS_FILES = (
    Path(r"C:\Windows\System32\drivers\etc\hosts"),
    Path("/etc/hosts"),
)

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


def website(host: str, port: int) -> str:
    """Port 80 needs no suffix, which is what makes http://sp-clp possible."""
    return f"http://{host}" if port == 80 else f"http://{host}:{port}"


def alias_ready() -> bool:
    """Whether the alias resolves on this machine.

    The hosts file and the computer name are checked directly instead of asking
    the resolver: a plant network without a DNS server can make a name lookup
    hang for seconds, and this runs on every help page view.
    """
    if machine_name().casefold() == ALIAS.casefold():
        return True
    for path in HOSTS_FILES:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            names = line.split("#", 1)[0].split()[1:]
            if any(name.casefold() == ALIAS.casefold() for name in names):
                return True
    return False


def alias_url(port: int | None = None) -> str:
    return website(ALIAS, port or bind_port())


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


def _usable(address: str) -> bool:
    # Loopback is offered last as localhost, and a 169.254 address is what a dead
    # cable or a missing DHCP server produces: nobody reaches the panel on it.
    return not address.startswith("127.") and not address.startswith("169.254.")


def local_addresses() -> list[str]:
    """Every IPv4 address this machine answers on, the default route one first.

    A machine with one network card and one address answers the same as before.
    The reason to look further is the Windows box that carries more than one: the
    plant network and a Wi-Fi network, a VirtualBox NAT card, WSL or Hyper-V. The
    default route there points at the address nobody else can reach, which is how
    the help page ends up advertising an address that does not work.
    """
    found: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = info[4][0]
            if _usable(address) and address not in found:
                found.append(address)
    except OSError:
        # A plant network without DNS can refuse the name lookup; the probe below
        # is the only address then, which is what this returned before.
        pass
    primary = lan_address()
    if primary and primary in found:
        found.remove(primary)
    if primary:
        found.insert(0, primary)
    return found


def access_urls(port: int | None = None, host: str | None = None) -> list[str]:
    """Addresses to open the dashboard, friendliest first.

    Binding to localhost only leaves nothing but 127.0.0.1, so the list degrades
    to a single entry instead of advertising addresses that would not answer.
    """
    resolved_port = port or bind_port()
    if (host or bind_host()) in {"127.0.0.1", "localhost"}:
        return [website("localhost", resolved_port)]
    urls: list[str] = []
    if alias_ready():
        urls.append(alias_url(resolved_port))
    urls.append(website(machine_name(), resolved_port))
    for address in local_addresses():
        urls.append(website(address, resolved_port))
    urls.append(website("localhost", resolved_port))
    return urls
