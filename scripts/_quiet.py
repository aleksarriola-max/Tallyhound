"""Imported first by the command-line scripts: outside a running app Streamlit warns "No runtime found" for every
cached function, which is noise in a terminal."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    import streamlit.logger as _log
    _log.set_log_level("error")
except Exception:  # noqa: BLE001 - quieter output is a nicety, never a reason to fail
    pass
