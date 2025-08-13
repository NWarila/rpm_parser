#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rpm_praser CLI — generate Ansible vars YAML grouped by RPM categories.

Behavior
--------
- Validates package name (policy auto-detected unless --distro given)
- Queries installed package contents
- Groups into: configuration, artifacts, docs, licenses, general
  * configuration: RPM 'config' flag
  * licenses:     RPM 'license' flag
  * docs:         RPM 'doc' flag
  * general:      symlinks (type == 'link') not otherwise categorized
  * artifacts:    everything else
- Writes 'files_<PACKAGE>.yml' by default (override with -o/--output)

Python 3.6 compatible.
"""

from argparse import ArgumentParser, Namespace
from collections import OrderedDict
from sys import stderr, stdout
from typing import Any, Dict, Iterable, List, Optional, Set

from . import get_version
from .os_detect import detect_distro_keyword
from .validators import validate_package_name
from .rpm_query import get_installed_package_dumps


_DESCRIPTION = "rpm_praser CLI"
_DISTRO_CHOICES = ("auto", "debian", "ubuntu", "rhel", "fedora")
_CATEGORY_ORDER = ("configuration", "artifacts", "docs", "licenses", "general")


# ------------------------------ CLI parsing ---------------------------------

def _parse_args(argv=None):  # type: (Optional[List[str]]) -> Namespace
    parser = ArgumentParser(description=_DESCRIPTION)
    parser.add_argument("-V", "--version", action="store_true", help="Print version and exit")
    parser.add_argument(
        "-d", "--distro",
        choices=_DISTRO_CHOICES,
        default="auto",
        help="Validation policy (default: auto-detect via /etc/os-release).",
    )
    parser.add_argument(
        "-o", "--output",
        metavar="PATH",
        help="Output YAML file path (default: files_<PACKAGE>.yml).",
    )
    parser.add_argument(
        "package",
        metavar="NAME",
        nargs="?",
        help="Installed RPM package NAME (e.g., shadow-utils).",
    )
    return parser.parse_args(argv)


def _resolve_distro(selected):  # type: (str) -> str
    if not selected or selected == "auto":
        return detect_distro_keyword(_DISTRO_CHOICES[1:], default="rhel")
    return selected


# ------------------------------ Grouping logic ------------------------------

def _category_for(entry):  # type: (Dict[str, Any]) -> str
    """Map an rpm file entry to one of our categories."""
    # 'flags' is a list of lowercase names (when librpm path is used). Be defensive.
    flags = entry.get("flags") or []
    if isinstance(flags, (list, tuple)):
        fset = set(str(x).lower() for x in flags)
    else:
        fset = set()

    if "config" in fset:
        return "configuration"
    if "license" in fset:
        return "licenses"
    if "doc" in fset:
        return "docs"

    ftype = str(entry.get("type") or "").lower()
    if ftype == "link":
        return "general"

    return "artifacts"


def _sanitize_key(path, used):  # type: (str, Set[str]) -> str
    """
    Turn a filesystem path into a safe YAML key:
      - strip leading '/'
      - lowercase
      - replace any non-alphanumeric with '_'
      - collapse consecutive '_' and trim at ends
      - ensure uniqueness with numeric suffix if needed
    """
    s = (path or "").strip()
    if s.startswith("/"):
        s = s[1:]
    s = s.lower()

    out_chars = []
    prev_underscore = False
    for ch in s:
        if ("a" <= ch <= "z") or ("0" <= ch <= "9"):
            out_chars.append(ch)
            prev_underscore = False
        else:
            if not prev_underscore:
                out_chars.append("_")
                prev_underscore = True
            # else skip additional underscores

    key = "".join(out_chars).strip("_") or "root"

    # Ensure uniqueness
    if key not in used:
        used.add(key)
        return key
    i = 2
    while True:
        candidate = "%s_%d" % (key, i)
        if candidate not in used:
            used.add(candidate)
            return candidate
        i += 1


def _state_from_type(ftype):  # type: (str) -> str
    """Normalize rpm type to Ansible 'state'."""
    t = (ftype or "").lower()
    if t == "dir" or t == "directory":
        return "directory"
    if t == "link" or t == "symlink":
        return "link"
    return "file"


def _abs_link_src(linkto, link_path):  # type: (str, str) -> Optional[str]
    """
    Convert an RPM link target into an absolute POSIX path.
    - Absolute targets are normalized and returned as-is.
    - Relative targets are resolved against the symlink's directory (dirname(path)).
    - No filesystem access; string-only normalization (safe).
    """
    import posixpath

    if not linkto:
        return None

    lt = str(linkto).strip()

    # Already absolute: normalize and return.
    if lt.startswith("/"):
        return posixpath.normpath(lt)

    # Resolve relative to the directory containing the link itself.
    base_dir = posixpath.dirname(str(link_path) or "/") or "/"
    if not base_dir.startswith("/"):
        base_dir = "/" + base_dir.lstrip("/")
    return posixpath.normpath(posixpath.join(base_dir, lt))


def _build_item(entry):  # type: (Dict[str, Any]) -> Dict[str, Any]
    """Build the vars dict expected by Ansible for a single file/link/dir."""
    from collections import OrderedDict

    path_val = entry.get("path") or ""

    item = OrderedDict()  # type: Dict[str, Any]
    item["follow"] = False
    item["force"] = True
    item["group"] = entry.get("group") or "root"
    # mode should be a string like '0644'
    mode = entry.get("mode")
    item["mode"] = str(mode) if mode is not None else ""
    item["owner"] = entry.get("owner") or "root"
    item["path"] = path_val
    state = _state_from_type(entry.get("type"))
    item["state"] = state

    # Only for links: compute absolute src from link target + link path directory.
    if state == "link":
        linkto = entry.get("linkto")
        src_abs = _abs_link_src(linkto, path_val)
        if src_abs:
            item["src"] = src_abs

    return item


def _merge_files_from_packages(pkg_objs):  # type: (List[Dict[str, Any]]) -> List[Dict[str, Any]]
    """
    Flatten and de-duplicate files across NEVRAs of the same NAME.
    Deduplicate by absolute path; first occurrence wins.
    """
    seen = set()  # type: Set[str]
    out = []      # type: List[Dict[str, Any]]
    for pkg in pkg_objs:
        for f in (pkg.get("files") or []):
            p = f.get("path")
            if not p or p in seen:
                continue
            seen.add(p)
            out.append(f)
    return out


def _build_yaml_data(files):  # type: (List[Dict[str, Any]]) -> OrderedDict
    """Assemble the final OrderedDict for YAML emission."""
    used_keys = set()  # type: Set[str]
    grouped = {k: OrderedDict() for k in _CATEGORY_ORDER}  # type: Dict[str, OrderedDict]

    for f in files:
        cat = _category_for(f)
        key = _sanitize_key(f.get("path") or "", used_keys)
        grouped[cat][key] = _build_item(f)

    # Top-level structure
    out = OrderedDict()
    out["files"] = OrderedDict((k, grouped[k]) for k in _CATEGORY_ORDER)
    return out


# ------------------------------ YAML emission -------------------------------

def _yaml_dump(data, path):  # type: (Dict[str, Any], str) -> None
    """
    Write YAML using PyYAML if available; otherwise a small built-in emitter
    that single-quotes strings and uses 2-space indents.
    """
    try:
        import yaml  # type: ignore
        # Prefer readable, block style, preserve insertion order
        with open(path, "w") as fh:
            yaml.safe_dump(
                data, fh,
                default_flow_style=False,
                sort_keys=False
            )
        return
    except Exception:
        pass  # fall through to tiny emitter

    def q(v):  # quote scalars like Ansible examples ('0644', '/path', etc.)
        if isinstance(v, bool):
            return "true" if v else "false"
        if v is None:
            return "''"
        s = str(v)
        # single-quote, escape single quotes by doubling them (YAML 1.1/1.2)
        return "'" + s.replace("'", "''") + "'"

    lines = []  # type: List[str]
    lines.append("files:")
    # files -> categories
    files = data.get("files", {})  # type: ignore
    for cat in _CATEGORY_ORDER:
        lines.append("  %s:" % cat)
        items = files.get(cat, {}) if isinstance(files, dict) else {}
        if not items:
            continue
        for key, props in items.items():
            lines.append("    %s:" % key)
            # maintain property order in _build_item
            for prop_key in ["follow", "force", "group", "mode", "owner", "path", "state", "src"]:
                if prop_key in props and props[prop_key] not in (None, ""):
                    lines.append("      %s: %s" % (prop_key, q(props[prop_key])))
                elif prop_key in ("follow", "force") and prop_key in props:
                    # booleans intentionally written even if False
                    lines.append("      %s: %s" % (prop_key, q(props[prop_key])))

    with open(path, "w") as fh:
        fh.write("\n".join(lines) + "\n")


# ------------------------------ Main logic ----------------------------------

def main(argv=None):  # type: (Optional[List[str]]) -> int
    args = _parse_args(argv)

    if args.version:
        stdout.write(get_version() + "\n")
        return 0

    if not args.package:
        stdout.write("usage: python -m rpm_praser [--distro {auto,debian,ubuntu,rhel,fedora}] NAME\n")
        stdout.write("hint: provide an installed package NAME, e.g., 'shadow-utils'\n")
        return 0

    distro = _resolve_distro(args.distro)

    try:
        name = validate_package_name(args.package, distro=distro)
        pkg_objs = get_installed_package_dumps(name)  # one per installed NEVRA
    except ValueError as exc:
        stderr.write("error: %s\n" % exc)
        return 2
    except LookupError as exc:
        stderr.write("error: %s\n" % exc)
        return 3
    except RuntimeError as exc:
        stderr.write("error: %s\n" % exc)
        return 4

    # Flatten & deduplicate file list across NEVRAs
    files = _merge_files_from_packages(pkg_objs)

    # Build YAML data
    yaml_data = _build_yaml_data(files)

    # Decide output path
    out_path = args.output or "files_%s.yml" % name
    try:
        _yaml_dump(yaml_data, out_path)
    except Exception as exc:
        stderr.write("error: failed to write YAML: %s\n" % exc)
        return 5

    stdout.write("wrote: %s\n" % out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
