"""
validators.py — Data-driven, no-regex validators for package names.

Why this design
---------------
- Single entrypoint: validate_package_name(name, distro)
- Policy registry per distro (Debian/Ubuntu vs. RPM/RHEL/Fedora)
- Fail-closed, ASCII-only, allow-list checks (no regex)
- Minimal imports; pure functions; clear comments

Security posture
----------------
- ASCII-only: blocks Unicode confusables and control characters
- Rejects whitespace/control chars explicitly
- RPM reserved symbols (':', '<', '>', '=') are forbidden
- Error messages are generic (do not echo untrusted input)
"""

from collections import namedtuple
from typing import Mapping
from types import MappingProxyType


# ------------------------------ Character sets ------------------------------

# Keep explicit character sets for readability and auditing.
LOWER = "abcdefghijklmnopqrstuvwxyz"
UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
DIGITS = "0123456789"


# ------------------------------ Policy model --------------------------------

Policy = namedtuple("Policy", [
    "min_len", "max_len",
    "start_chars", "allowed_chars",
    "forbidden_chars"
])


# ------------------------------ Policies ------------------------------------

# Registry with common synonyms. Add new distros by inserting one line here.
_POLICY_REGISTRY = MappingProxyType({  # type: Mapping[str, Policy]
    "ubuntu": Policy(
        min_len=2,
        max_len=128,
        start_chars=frozenset(LOWER + DIGITS),
        allowed_chars=frozenset(LOWER + DIGITS + "+-."),
        forbidden_chars=frozenset(""),
    ),
    "rhel": Policy(
        min_len=1,
        max_len=128,
        start_chars=frozenset(LOWER + UPPER + DIGITS),
        allowed_chars=frozenset(LOWER + UPPER + DIGITS + "+-_."),
        forbidden_chars=frozenset(":<=>"),
    ),
})


# ------------------------------ Core validator ------------------------------

def validate_package_name(name: str, distro: str) -> str:
    # type: (str, str) -> str
    """
    Validate a package name for the given distro/ecosystem.

    Args:
        name: The candidate package name string.
        distro: One of the keys in the policy registry (e.g., 'debian', 'ubuntu',
                'rpm', 'rhel', 'redhat', 'fedora').

    Returns:
        The validated, trimmed name.

    Raises:
        ValueError: if the name violates policy or the distro is unsupported.

    Notes:
        - This function performs:
            1) type/ASCII/whitespace/control checks
            2) length checks
            3) distro-specific checks (first char, allowed chars, reserved chars)
        - It never mutates input (e.g., does not lowercase automatically).
    """
    if not isinstance(name, str):
        raise ValueError("Package name must be a string.")

    key = (distro or "").strip().lower()
    policy = _POLICY_REGISTRY.get(key)
    if policy is None:
        supported = ", ".join(sorted(_POLICY_REGISTRY.keys()))
        raise ValueError("Unsupported distro. Supported: %s" % supported)

    # Trim surrounding whitespace. Inner whitespace is disallowed below.
    s = name.strip()

    # ASCII-only (3.6-safe): reject non-ASCII by attempting to encode.
    try:
        s.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError("Package name must be ASCII.")

    # Reject any whitespace and ASCII control characters anywhere.
    for ch in s:
        if ch.isspace() or ord(ch) < 32:
            raise ValueError("Package name must not contain whitespace or control characters.")

    # Enforce length bounds early (cheap checks).
    if len(s) < policy.min_len:
        raise ValueError("Package name must be at least %d characters." % policy.min_len)
    if len(s) > policy.max_len:
        raise ValueError("Package name too long (>%d characters)." % policy.max_len)

    # Fast-fail on reserved/forbidden characters.
    for ch in s:
        if ch in policy.forbidden_chars:
            raise ValueError("Package name contains reserved characters.")

    # First character rule (e.g., Debian: [a-z0-9], RPM: alphanumeric).
    if s[0] not in policy.start_chars:
        raise ValueError("Package name must start with a valid alphanumeric character.")

    # All characters must be allowed by the policy's allow-list.
    allowed = policy.allowed_chars  # local binding for speed
    for ch in s:
        if ch not in allowed:
            raise ValueError("Package name contains an invalid character.")

    return s


# Public API
__all__ = ["validate_package_name"]
