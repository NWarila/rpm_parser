#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rpm_query.py — Query installed RPM package(s) with librpm (python3-rpm).

- No subprocess calls; uses rpm.TransactionSet() directly.
- Mirrors the spirit of `rpm -q --dump <name>` for installed packages.
- Returns a structured Python object, easy to consume/serialize.

Public API
----------
get_installed_package_dumps(pkg_name: str) -> list[dict]
    Returns a list of package objects (one per installed NEVRA) with 'files'.

Each package object:
{
  "name": "shadow-utils",
  "epoch": 0 | int | None,
  "version": "4.14.5",
  "release": "27.el9",
  "arch": "x86_64",
  "nevra": "shadow-utils-4.14.5-27.el9.x86_64",  # 'epoch:' prefix added if present
  "digest_algo": 8,                               # rpm FILEDIGESTALGO (int), if available
  "files": [
      {
        "path": "/etc/default/useradd",
        "size": 123,
        "mtime": 1690000000,
        "mode": "0644",             # permissions only (4-octal)
        "owner": "root",
        "group": "root",
        "linkto": null,             # symlink target if any
        "flags": ["config"],        # decoded RPMFILE_* names (lowercase)
        "flags_raw": 1,             # raw bitmask
        "type": "file"              # "file" | "dir" | "link" | "other"
      },
      ...
  ]
}
"""


from typing import Any, Dict, List, Optional


# ------------------------------ Small helpers -------------------------------

def _octal_perms(mode_val: int) -> str:
    """Return 4-digit octal permission bits (mask out file type)."""
    return format(int(mode_val) & 0o7777, "04o")


def _file_type_from_mode(mode_val: int) -> str:
    """Map POSIX mode type bits to a simple string."""
    m = int(mode_val)
    t = m & 0o170000
    if t == 0o040000:
        return "dir"
    if t == 0o120000:
        return "link"
    if t == 0o100000:
        return "file"
    return "other"


def _hdr_list(hdr: Any, tag: int) -> List[Any]:
    """Safely extract a list-like value from an rpm header tag."""
    try:
        val = hdr[tag]  # type: ignore[index]
    except Exception:
        return []
    if isinstance(val, (list, tuple)):
        return list(val)
    return [val]


def _nevra(hdr: "rpm.hdr") -> str:
    """Build NEVRA string (with optional epoch)."""
    import rpm  # lazy import
    name = hdr[rpm.RPMTAG_NAME]
    epoch = hdr.get(rpm.RPMTAG_EPOCH) if hasattr(hdr, "get") else None  # hdr.get may exist
    version = hdr[rpm.RPMTAG_VERSION]
    release = hdr[rpm.RPMTAG_RELEASE]
    arch = hdr[rpm.RPMTAG_ARCH]
    if epoch in (None, 0, "0"):
        return f"{name}-{version}-{release}.{arch}"
    return f"{name}-{epoch}:{version}-{release}.{arch}"


def _decode_file_flags(bitmask: int) -> List[str]:
    """
    Convert RPM file flags bitmask to a list of lowercase names using available
    RPMFILE_* constants on this system (forward-compatible).
    """
    import rpm  # lazy import

    names: List[str] = []
    try:
        attrs = dir(rpm)
    except Exception:
        return names

    for attr in attrs:
        if not attr.startswith("RPMFILE_"):
            continue
        if attr.startswith("RPMFILE_STATE_"):
            # Skip state constants (not bit flags)
            continue
        val = getattr(rpm, attr, None)
        if not isinstance(val, int) or val == 0:
            continue
        try:
            if bitmask & val:
                names.append(attr.replace("RPMFILE_", "").lower())
        except Exception:
            # Extremely defensive; ignore odd values
            continue

    # Sort for stable output
    names.sort()
    return names


# ------------------------------ Core function --------------------------------

def get_installed_package_dumps(pkg_name: str) -> List[Dict[str, Any]]:
    """
    Return installed package info (one per NEVRA) with per-file metadata.

    Args:
        pkg_name: RPM package NAME (not NVRA). Validate before calling if desired.

    Returns:
        List[dict]: one dict per installed package, each with 'files' list.

    Raises:
        RuntimeError: if librpm is unavailable.
        LookupError: if no installed package matches the NAME.
    """
    try:
        import rpm  # Provided by: sudo dnf install -y python3-rpm
    except Exception as exc:
        raise RuntimeError(
            "python3-rpm (librpm bindings) is unavailable. Install the matching bindings for your Python."
        ) from exc

    # Optional: use your own validator if you want—uncomment next two lines:
    # from .validators import validate_package_name
    # pkg_name = validate_package_name(pkg_name, distro="rpm")

    ts = rpm.TransactionSet()
    mi = ts.dbMatch("name", pkg_name)
    headers = list(mi)
    if not headers:
        raise LookupError(f"Package not installed: {pkg_name}")

    result: List[Dict[str, Any]] = []

    for hdr in headers:
        # Pull parallel per-file arrays from the header
        names = _hdr_list(hdr, rpm.RPMTAG_FILENAMES)
        sizes = _hdr_list(hdr, rpm.RPMTAG_FILESIZES)
        mtimes = _hdr_list(hdr, rpm.RPMTAG_FILEMTIMES)
        digests = _hdr_list(hdr, rpm.RPMTAG_FILEDIGESTS)
        modes = _hdr_list(hdr, rpm.RPMTAG_FILEMODES)
        owners = _hdr_list(hdr, rpm.RPMTAG_FILEUSERNAME)
        groups = _hdr_list(hdr, rpm.RPMTAG_FILEGROUPNAME)
        links = _hdr_list(hdr, rpm.RPMTAG_FILELINKTOS)
        flags = _hdr_list(hdr, rpm.RPMTAG_FILEFLAGS)

        # Optional: file digest algorithm (int per RPMTAG_FILEDIGESTALGO)
        try:
            digest_algo = hdr[rpm.RPMTAG_FILEDIGESTALGO]
        except Exception:
            digest_algo = None

        files: List[Dict[str, Any]] = []
        n = len(names)
        for i in range(n):
            # Defensive indexing (older RPMs can have length mismatches)
            mode_i = int(modes[i]) if i < len(modes) else 0
            perms = _octal_perms(mode_i)
            flags_i = int(flags[i]) if i < len(flags) else 0

            entry: Dict[str, Any] = {
                "path": str(names[i]),
                "size": int(sizes[i]) if i < len(sizes) else None,
                "mtime": int(mtimes[i]) if i < len(mtimes) else None,
                "digest": str(digests[i]) if i < len(digests) and digests[i] else None,
                "mode": perms,
                "owner": str(owners[i]) if i < len(owners) else "root",
                "group": str(groups[i]) if i < len(groups) else "root",
                "linkto": (str(links[i]) if i < len(links) and links[i] else None),
                "flags": _decode_file_flags(flags_i),
                "flags_raw": flags_i,
                "type": _file_type_from_mode(mode_i),
            }
            files.append(entry)

        pkg_obj: Dict[str, Any] = {
            "name": hdr[rpm.RPMTAG_NAME],
            "epoch": (hdr.get(rpm.RPMTAG_EPOCH) if hasattr(hdr, "get") else None),
            "version": hdr[rpm.RPMTAG_VERSION],
            "release": hdr[rpm.RPMTAG_RELEASE],
            "arch": hdr[rpm.RPMTAG_ARCH],
            "nevra": _nevra(hdr),
            "digest_algo": digest_algo,
            "files": files,
        }
        result.append(pkg_obj)

    return result
