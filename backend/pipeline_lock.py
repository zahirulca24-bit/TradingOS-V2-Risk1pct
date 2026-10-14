"""Nonblocking cross-platform OS process lock for scanner/scheduler."""
from contextlib import contextmanager
import os
from pathlib import Path

from pipeline_store import STATE_DIR


@contextmanager
def exclusive_pipeline_lock():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / "pipeline.lock"
    with open(path, "a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("Another scheduler/scan instance is already active") from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("Another scheduler/scan instance is already active") from exc
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
