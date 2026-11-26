"""Log files for troubleshooting: everything the program prints also goes to
<install folder>\\logs\\<name>-<date>-<time>.log, so users can send a log when
something goes wrong.

Logs hold only what the program prints: progress, timings, settings, folder
paths, notebook file names and errors. Never the transcribed text.

If the install folder's logs\\ isn't writable (it normally is: the installer gives
users write access to it), logs go to %LOCALAPPDATA%\\ChandraNotebookOCR\\logs.
"""
import atexit
import datetime
import faulthandler
import glob
import os
import platform
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
KEEP_LOGS = 30  # per kind (run / gui); older ones are deleted

log_path = None  # set by start()
_log_file = None


def _candidate_dirs():
    yield os.path.join(PROJECT_DIR, "logs")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        yield os.path.join(local, "ChandraNotebookOCR", "logs")


def log_dir() -> str:
    """The folder logs are written to (the first writable candidate)."""
    for d in _candidate_dirs():
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".write-test")
            with open(probe, "w"):
                pass
            os.remove(probe)
            return d
        except OSError:
            continue
    return ""


class _Tee:
    """Writes to the original stream (if any) and to the log file."""

    def __init__(self, stream, skip_prefix=None):
        self.stream = stream
        self.skip_prefix = skip_prefix
        self._buf = ""
        atexit.register(self._flush_partial)

    def _log_line(self, line):
        # Progress bars redraw one line with '\r'; keep only its final state.
        line = line.rstrip("\r").rsplit("\r", 1)[-1]
        # GUI progress events ('@@{json}' lines) duplicate the text lines; keep them out.
        if self.skip_prefix and line.startswith(self.skip_prefix):
            return
        try:
            _log_file.write(f"[{datetime.datetime.now():%H:%M:%S}] {line}\n" if line.strip() else "\n")
            _log_file.flush()
        except (OSError, ValueError):
            pass

    def _flush_partial(self):
        if self._buf and _log_file is not None:
            self._log_line(self._buf)
            self._buf = ""

    def write(self, text):
        if self.stream is not None:
            try:
                self.stream.write(text)
            except (OSError, ValueError, UnicodeEncodeError):
                pass
        if _log_file is not None and text:
            self._buf += text
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                self._log_line(line)
        return len(text)

    def flush(self):
        if self.stream is not None:
            try:
                self.stream.flush()
            except (OSError, ValueError):
                pass

    def isatty(self):
        return self.stream is not None and self.stream.isatty()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def _prune(directory: str, kind: str):
    logs = sorted(glob.glob(os.path.join(directory, f"{kind}-*.log")))
    for old in logs[:-KEEP_LOGS]:
        try:
            os.remove(old)
        except OSError:
            pass


def start(kind: str) -> str:
    """Start copying stdout/stderr to a new log file. Returns its path ('' if none)."""
    global log_path, _log_file
    if _log_file is not None:
        return log_path
    directory = log_dir()
    if not directory:
        return ""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(directory, f"{kind}-{stamp}.log")
    try:
        _log_file = open(path, "a", encoding="utf-8", errors="replace")
    except OSError:
        return ""
    log_path = path
    _prune(directory, kind)

    _log_file.write(f"=== {kind} log started {datetime.datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
    _log_file.write(f"Program folder: {PROJECT_DIR}\n")
    _log_file.write(f"Windows: {platform.platform()}  CPU: {platform.processor()}\n")
    _log_file.write(f"Python: {sys.version.split()[0]} ({sys.executable})\n")
    _log_file.write(f"Command: {' '.join(sys.argv)}\n\n")
    _log_file.flush()

    sys.stdout = _Tee(sys.stdout, skip_prefix="@@")
    sys.stderr = _Tee(sys.stderr)
    # Native crashes (e.g. a GPU driver access violation) bypass Python's error
    # handling; this still writes the Python stack of every thread to the log.
    faulthandler.enable(file=_log_file, all_threads=True)
    return path
