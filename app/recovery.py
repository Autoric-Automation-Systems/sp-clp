"""Password recovery from the command line.

A local panel with no internet and no mailbox has nowhere to send a reset link, so
recovery is a command run on the panel machine, the same shape as
``grafana-cli admin reset-admin-password`` and ``occ user:resetpassword``. Whoever
reaches the console can already delete ``data/sp-clp.sqlite3``; offering a narrow
path is better than pushing the customer into a destructive one.

The command is one-shot by construction. Nothing is written to an environment
variable or to the registry, so restarting the panel cannot repeat the reset, which
is the failure mode an ``SP_CLP_RESET_PASSWORD`` variable would carry the moment
somebody used ``setx``.

The password is read from the console with :mod:`getpass`, so it never enters the
shell history, the process command line or the log.
"""

from __future__ import annotations

import argparse
import getpass

from .security import hash_password
from .storage import Storage

FLAG = "--reset-password"
MIN_LENGTH = 8


def parse_options(argv: list[str]) -> argparse.Namespace:
    """Read the start-up flags.

    Unknown arguments are ignored: the panel is usually opened from a shortcut, and
    an unexpected argument there must never be the reason it refuses to start.
    """
    parser = argparse.ArgumentParser(
        description="Painel local de monitoramento dos CLPs Siemens S7-1200."
    )
    parser.add_argument(
        FLAG,
        dest="reset_password",
        action="store_true",
        help="pede a nova senha no console e encerra sem subir o painel",
    )
    options, _ = parser.parse_known_args(argv)
    return options


def read_new_password(reader=None, writer=None) -> str | None:
    """Ask for the password twice and return it, or None when it cannot be used."""
    ask = reader or getpass.getpass
    say = writer or print
    first = ask("Nova senha: ")
    if first != ask("Repita a nova senha: "):
        say("As senhas não conferem. Nada foi alterado.")
        return None
    if len(first) < MIN_LENGTH:
        say(f"A senha precisa de pelo menos {MIN_LENGTH} caracteres. Nada foi alterado.")
        return None
    return first


def apply_reset(storage: Storage, reader=None, writer=None) -> bool:
    """Replace the stored hash and say what to do next.

    The server holds its sessions in memory, so a reset made while the panel is
    running only takes effect for logging in again. Ending those sessions needs a
    restart, which is what the operator is told to do.
    """
    say = writer or print
    password = read_new_password(reader=reader, writer=say)
    if password is None:
        return False
    storage.set_setting("password_hash", hash_password(password))
    say("Senha do painel redefinida. Inicie o painel normalmente.")
    say("Se o painel já estava aberto, feche-o antes: as sessões abertas só caem no próximo arranque.")
    return True
