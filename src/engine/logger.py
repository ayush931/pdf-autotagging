"""
Engine Logging & Diagnostic Output System
Provides structured, clean logging with verbosity levels (QUIET, INFO, VERBOSE, DEBUG).
"""

import sys
import time
from enum import IntEnum

# Ensure standard UTF-8 stream handling on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class Verbosity(IntEnum):
    QUIET = 0      # Errors only
    NORMAL = 1     # Key summary & phase transitions
    VERBOSE = 2    # Per-page details, tag counts, stream operations
    DEBUG = 3      # Low-level operator traces & matrix calculations


class EngineLogger:
    """
    Structured logger for the PDF Auto-Tagging & Remediation Engine.
    """

    def __init__(self, verbosity: Verbosity = Verbosity.NORMAL):
        self.verbosity = verbosity
        self.start_times = {}

    def set_verbosity(self, level: Verbosity):
        self.verbosity = level

    def _format_msg(self, level_str: str, msg: str) -> str:
        timestamp = time.strftime("%H:%M:%S")
        return f"[{timestamp}] [{level_str}] {msg}"

    def debug(self, msg: str, category: str = "DEBUG"):
        if self.verbosity >= Verbosity.DEBUG:
            print(self._format_msg(f"DEBUG:{category}", msg), file=sys.stderr)

    def info(self, msg: str):
        if self.verbosity >= Verbosity.NORMAL:
            print(self._format_msg("INFO", msg))

    def verbose(self, msg: str):
        if self.verbosity >= Verbosity.VERBOSE:
            print(self._format_msg("DETAILS", msg))

    def success(self, msg: str):
        if self.verbosity >= Verbosity.NORMAL:
            print(self._format_msg("SUCCESS", msg))

    def warning(self, msg: str):
        if self.verbosity >= Verbosity.NORMAL:
            print(self._format_msg("WARNING", msg), file=sys.stderr)

    def error(self, msg: str):
        if self.verbosity >= Verbosity.QUIET:
            print(self._format_msg("ERROR", msg), file=sys.stderr)

    def phase(self, title: str):
        if self.verbosity >= Verbosity.NORMAL:
            bar = "=" * 70
            print(f"\n{bar}\n >>> {title.upper()}\n{bar}")

    def start_timer(self, name: str):
        self.start_times[name] = time.perf_counter()

    def stop_timer(self, name: str) -> float:
        elapsed = time.perf_counter() - self.start_times.get(name, time.perf_counter())
        return round(elapsed, 4)


# Global default logger instance
logger = EngineLogger()
