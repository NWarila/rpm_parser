# os_detect.py — Python 3.6 compatible

from shlex import split as shlex_split
from typing import Dict, Tuple

_OS_RELEASE_PATH = "/etc/os-release"

def read_os_release(path=None):  # type: (str) -> Dict[str, str]
    """
    Minimal /etc/os-release parser for 3.6+.
    Returns lowercase values for stable matching; keys stay as-is.
    """
    data = {}  # type: Dict[str, str]
    target = path or _OS_RELEASE_PATH
    try:
        with open(target, "r") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, raw_val = line.split("=", 1)
                key = key.strip()
                try:
                    parts = shlex_split(raw_val, posix=True)
                    val = parts[0] if parts else ""
                except ValueError:
                    val = raw_val.strip().strip('"').strip("'")
                data[key] = val.lower()
    except Exception:
        return {}
    return data

def detect_distro_keyword(choices, default=None, path=None):
    # type: (Tuple[str, ...], str, str) -> str
    """
    Strict 1:1 selection:
      1) Match ID exactly against choices
      2) Match first token in ID_LIKE against choices
      3) Return default if provided and present
      4) First non-'auto' in choices
      5) Else first entry or '' if choices is empty
    """
    lowered = tuple(c.lower() for c in choices)
    osr = read_os_release(path)
    os_id = osr.get("ID", "").strip().lower()
    id_like = osr.get("ID_LIKE", "").strip().lower()

    if os_id and os_id in lowered:
        return choices[lowered.index(os_id)]

    if id_like:
        for token in id_like.split():
            if token in lowered:
                return choices[lowered.index(token)]

    if default is not None and default.lower() in lowered:
        return choices[lowered.index(default.lower())]

    for idx, label in enumerate(lowered):
        if label != "auto":
            return choices[idx]

    return choices[0] if choices else ""
