"""Chunk 10: the plumbing for unattended runs. Logging, a run lock, and the status files.

* setup_logging()   a rotating log file (logs/weekly.log) plus the console
* capture_output()  sends everything the rest of the code print()s into that log, so the Chunk 3-9 modules
                    did not have to change to be logged
* RunLock           stops a second run from starting while one is already going
* write_status() / append_history()   logs/last_run.json and logs/run_history.jsonl: "did it run, what happened?"
"""
import json
import logging
import logging.handlers
import os
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime

LOGGER_NAME = "mart"


# ---------------------------------------------------------------- logging
def setup_logging(log_dir: str, log_file: str, max_bytes: int, backups: int, console: bool = True) -> logging.Logger:
    """Rotating file (UTF-8) + console. Safe to call more than once: old handlers are replaced."""
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, log_file), maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    if console:
        # Business names can be Arabic or Urdu. A Windows console or redirected stream in cp1252 would crash
        # on them, so unprintable characters become "?" on the console (the log file keeps them intact).
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(errors="replace")
            except (AttributeError, ValueError):
                pass
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(fmt)
        logger.addHandler(console_handler)
    return logger


class _StreamToLogger:
    """File-like object: complete lines written to it become log records."""

    encoding = "utf-8"
    errors = "replace"

    def __init__(self, logger: logging.Logger, level: int, fallback):
        self.logger, self.level, self.fallback = logger, level, fallback
        self._buf = ""
        self._lock = threading.Lock()
        self._local = threading.local()

    def write(self, text) -> int:
        text = text if isinstance(text, str) else str(text)
        if getattr(self._local, "busy", False):      # logging itself tried to print: do not loop
            try:
                self.fallback.write(text)
            except Exception:
                pass
            return len(text)
        self._local.busy = True
        try:
            with self._lock:
                self._buf += text
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    if line.strip():
                        self.logger.log(self.level, line.rstrip())
        finally:
            self._local.busy = False
        return len(text)

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False

    def drain(self) -> None:
        """Log a last line that never got its newline."""
        with self._lock:
            rest, self._buf = self._buf.strip(), ""
        if rest:
            self.logger.log(self.level, rest)


@contextmanager
def capture_output(logger: logging.Logger):
    """While active, print() and stderr output of every module goes to the log (stdout=INFO, stderr=WARNING)."""
    old_out, old_err, old_raise = sys.stdout, sys.stderr, logging.raiseExceptions
    out = _StreamToLogger(logger, logging.INFO, old_out)
    err = _StreamToLogger(logger, logging.WARNING, old_err)
    sys.stdout, sys.stderr = out, err
    logging.raiseExceptions = False          # a logging hiccup must never become a crash (or a print loop)
    try:
        yield logger
    finally:
        out.drain()
        err.drain()
        sys.stdout, sys.stderr = old_out, old_err
        logging.raiseExceptions = old_raise


# ---------------------------------------------------------------- run lock
class RunLock:
    """A lock file created atomically. A lock older than `stale_hours` belongs to a crashed run and is replaced.

    Age is the only test on purpose: checking whether the old process is alive with os.kill(pid, 0) is not safe on
    Windows (there it terminates the process).
    """

    def __init__(self, path: str, stale_hours: float = 6):
        self.path, self.stale_seconds = path, stale_hours * 3600
        self.held = False

    def acquire(self, log=print) -> bool:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        for _ in range(3):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(self.path)
                except OSError:                  # it vanished between the two calls: try again
                    continue
                if age < self.stale_seconds:
                    return False
                log(f"Removing a stale lock ({age / 3600:.1f} hours old): the previous run must have crashed.")
                try:
                    os.remove(self.path)
                except OSError:
                    pass
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "started": now_iso()}, f)
            self.held = True
            return True
        return False

    def holder(self) -> str:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return f"started {data.get('started')} (pid {data.get('pid')})"
        except (OSError, ValueError):
            return "unknown"

    def release(self) -> None:
        if self.held:
            try:
                os.remove(self.path)
            except OSError:
                pass
            self.held = False


# ---------------------------------------------------------------- status files
def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_status(path: str, status: dict) -> None:
    """logs/last_run.json: the latest run only, replaced atomically."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def append_history(path: str, status: dict) -> None:
    """logs/run_history.jsonl: one line per run, so a missed week is easy to spot."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(status, ensure_ascii=False) + "\n")
