"""Nonblocking cross-platform distributed and OS process lock for scanner/scheduler."""
from contextlib import contextmanager
import os
from pathlib import Path
import threading
from typing import Optional, Union

from db_adapter import get_active_engine, postgres_distributed_lock, POSITION_MONITOR_LOCK_KEY
from pipeline_store import STATE_DIR


class PipelineLockContentionError(RuntimeError):
    """Raised when the exclusive pipeline lock cannot be acquired because another scan/scheduler is active."""
    pass


class PositionMonitorLockContentionError(RuntimeError):
    """Raised when the exclusive position monitor lock cannot be acquired because another monitor worker is active."""
    pass


_process_local_lock = threading.Lock()
_position_monitor_local_lock = threading.Lock()


@contextmanager
def exclusive_pipeline_lock(db_path: Optional[Union[str, Path]] = None, dsn: Optional[str] = None):
    acquired_local = _process_local_lock.acquire(blocking=False)
    if not acquired_local:
        raise PipelineLockContentionError("Another scan instance is active in this process")

    try:
        engine = get_active_engine(db_path=db_path, dsn=dsn)
        if engine == "postgres":
            try:
                with postgres_distributed_lock(dsn=dsn):
                    yield
            except RuntimeError as exc:
                if "advisory lock held" in str(exc) or "Another scheduler" in str(exc):
                    raise PipelineLockContentionError(str(exc)) from exc
                raise
        else:
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
                        raise PipelineLockContentionError("Another scheduler/scan instance is already active") from exc
                    try:
                        yield
                    finally:
                        try:
                            stream.seek(0)
                            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                        except Exception:
                            pass
                else:
                    import fcntl
                    try:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError as exc:
                        raise PipelineLockContentionError("Another scheduler/scan instance is already active") from exc
                    try:
                        yield
                    finally:
                        try:
                            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                        except Exception:
                            pass
    finally:
        _process_local_lock.release()


def is_pipeline_locked(db_path: Optional[Union[str, Path]] = None, dsn: Optional[str] = None) -> bool:
    """Non-blocking query to inspect if the pipeline lock is currently held by any thread or process."""
    if _process_local_lock.locked():
        return True
    try:
        engine = get_active_engine(db_path=db_path, dsn=dsn)
        if engine == "postgres":
            from db_adapter import PSYCOPG2_AVAILABLE, PIPELINE_LOCK_KEY, normalize_postgres_dsn, get_database_url
            if not PSYCOPG2_AVAILABLE:
                return False
            import psycopg2
            target_dsn = normalize_postgres_dsn(dsn or get_database_url())
            raw_conn = psycopg2.connect(target_dsn, connect_timeout=3)
            raw_conn.autocommit = True
            cur = raw_conn.cursor()
            try:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (PIPELINE_LOCK_KEY,))
                res = cur.fetchone()
                locked = res[0] if res else False
                if locked:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (PIPELINE_LOCK_KEY,))
                    return False
                return True
            finally:
                cur.close()
                raw_conn.close()
        else:
            path = STATE_DIR / "pipeline.lock"
            if not path.exists():
                return False
            with open(path, "a+b") as stream:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                        return False
                    except OSError:
                        return True
                else:
                    import fcntl
                    try:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                        return False
                    except OSError:
                        return True
    except (OSError, PermissionError):
        return True
    except Exception:
        return False


@contextmanager
def exclusive_position_monitor_lock(db_path: Optional[Union[str, Path]] = None, dsn: Optional[str] = None):
    """Exclusive nonblocking distributed and process lock for periodic position monitoring."""
    acquired_local = _position_monitor_local_lock.acquire(blocking=False)
    if not acquired_local:
        raise PositionMonitorLockContentionError("Another position monitor instance is active in this process")

    try:
        engine = get_active_engine(db_path=db_path, dsn=dsn)
        if engine == "postgres":
            try:
                with postgres_distributed_lock(dsn=dsn, lock_key=POSITION_MONITOR_LOCK_KEY):
                    yield
            except RuntimeError as exc:
                if "advisory lock held" in str(exc) or "Another" in str(exc):
                    raise PositionMonitorLockContentionError(str(exc)) from exc
                raise
        else:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            path = STATE_DIR / "position_monitor.lock"
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
                        raise PositionMonitorLockContentionError("Another position monitor instance is already active") from exc
                    try:
                        yield
                    finally:
                        try:
                            stream.seek(0)
                            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                        except Exception:
                            pass
                else:
                    import fcntl
                    try:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError as exc:
                        raise PositionMonitorLockContentionError("Another position monitor instance is already active") from exc
                    try:
                        yield
                    finally:
                        try:
                            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                        except Exception:
                            pass
    finally:
        _position_monitor_local_lock.release()


def is_position_monitor_locked(db_path: Optional[Union[str, Path]] = None, dsn: Optional[str] = None) -> bool:
    """Non-blocking check if position monitor lock is currently held."""
    if _position_monitor_local_lock.locked():
        return True
    try:
        engine = get_active_engine(db_path=db_path, dsn=dsn)
        if engine == "postgres":
            from db_adapter import PSYCOPG2_AVAILABLE, normalize_postgres_dsn, get_database_url
            if not PSYCOPG2_AVAILABLE:
                return False
            import psycopg2
            target_dsn = normalize_postgres_dsn(dsn or get_database_url())
            raw_conn = psycopg2.connect(target_dsn, connect_timeout=3)
            raw_conn.autocommit = True
            cur = raw_conn.cursor()
            try:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (POSITION_MONITOR_LOCK_KEY,))
                res = cur.fetchone()
                locked = res[0] if res else False
                if locked:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (POSITION_MONITOR_LOCK_KEY,))
                    return False
                return True
            finally:
                cur.close()
                raw_conn.close()
        else:
            path = STATE_DIR / "position_monitor.lock"
            if not path.exists():
                return False
            with open(path, "a+b") as stream:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                        return False
                    except OSError:
                        return True
                else:
                    import fcntl
                    try:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                        return False
                    except OSError:
                        return True
    except (OSError, PermissionError):
        return True
    except Exception:
        return False
