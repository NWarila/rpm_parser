# __init__.py
"""
rpm_praser package.

Keep this file minimal:
- No heavy imports
- No I/O, env reads, CLI parsing, or logging setup
- Only version and explicit public exports
"""

# --- Version ---------------------------------------------------------------
# If you eventually build wheels, you can switch to importlib.metadata here.
__version__ = "0.0.1"

# Optional helper if you'd like a function form
def get_version() -> str:
    return __version__


# --- Public API ------------------------------------------------------------
# Re-export only the symbols you want external code to import from the top-level.
# Example (uncomment when you create these modules):
# from .rpm_query import iter_rpm_file_records  # noqa: F401

__all__ = [
    "__version__",
    "get_version",
    # "iter_rpm_file_records",  # add as you expose things
]


# --- Compatibility notes ---------------------------------------------------
# Avoid importing optional/third-party deps here; import them lazily where used.
# Do not configure logging here; do it in your CLI entrypoint instead.
# Do not execute code at import time (no prints, no runtime checks).
