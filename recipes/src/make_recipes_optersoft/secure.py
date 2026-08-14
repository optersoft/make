"""The `secure` group: generate a strong random password.

Ported from `secure.just`. The shell version drew from `/dev/urandom` through
`tr -dc` and `head -c`, which meant tolerating a SIGPIPE under `pipefail`
(`|| true`), asserting the length afterwards because of it, and re-rolling up to
100 times until a `-` or `_` happened to appear. All three exist only because
shell has no way to draw from an alphabet.

`secrets.choice` over the same alphabet is the same construction with none of
the workarounds -- and the guarantee is now exact rather than probabilistic:
one special character is placed and the result shuffled, instead of re-rolling
until one shows up.
"""

from __future__ import annotations

import secrets
import string

from make import group, note, sh

secure = group("secure")

#: URL-safe base64 alphabet: 6 bits of entropy per character, and safe to drop
#: into URLs, filenames and shell arguments unquoted.
ALPHABET = string.ascii_letters + string.digits + "-_"
SPECIAL = "-_"

MINIMUM_LENGTH = 8


def generate(length: int = 32) -> str:
    """A password of `length` characters, with at least one `-` or `_`.

    Importable on its own -- the recipe is a thin wrapper, so anything else in
    the fleet can generate a password the same way without shelling out.
    """
    if length < MINIMUM_LENGTH:
        raise ValueError(f"length must be >= {MINIMUM_LENGTH} (got {length}); short passwords are not secure")
    body = [secrets.choice(ALPHABET) for _ in range(length - 1)]
    body.append(secrets.choice(SPECIAL))
    secrets.SystemRandom().shuffle(body)
    return "".join(body)


@secure.recipe(name="password")
def password(length: int = 32, *, copy: bool = False) -> str:
    """Print a strong random password (default 32 characters, 192 bits).

    Args:
        length: number of characters; at least 8
        copy: put it on the macOS clipboard instead of stdout
    """
    value = generate(length)
    if copy:
        sh.require("pbcopy", hint="--copy is macOS only")
        sh("pbcopy", input=value, echo_cmd=False)
        note(f"copied a {length}-character password to the clipboard")
    else:
        print(value)
    return value
