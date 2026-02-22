#!/usr/bin/env python
"""
Regression tests for concurrent timed rotation across threads and forked processes.

Covers the race condition where forked child processes sharing the same handler's
lock-file FD bypass each other's flock() locks (flock locks are per-open-file-
description, so inherited FDs share the same lock object).

Without the fix: multiple forked children simultaneously enter doRollover(),
producing extra rotated files (.1, .2, .2, …).

With the fix (PID check in _do_lock): each child detects the fork and reopens
the lock file, getting an independent file description that flock() properly
serializes.
"""

import logging
import multiprocessing
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from concurrent_log_handler import ConcurrentTimedRotatingFileHandler

# Helpers


def _make_record(msg: str) -> logging.LogRecord:
    return logging.makeLogRecord(
        {"msg": msg, "levelno": logging.INFO, "levelname": "INFO", "name": "test"}
    )


def _backdate_rollover(
    handler: ConcurrentTimedRotatingFileHandler, seconds: int = 10
) -> None:
    """Set rolloverAt to `seconds` in the past and persist it to the lock file."""
    past_time = int(time.time()) - seconds
    handler.clh._do_lock()
    handler.rolloverAt = past_time
    handler.write_rollover_time()
    handler.clh._do_unlock()


# Module-level worker (must be importable / inheritable by fork)


def _forked_worker(
    handler: ConcurrentTimedRotatingFileHandler,
    barrier: "multiprocessing.Barrier",  # type: ignore[type-arg]
    result_queue: "multiprocessing.Queue",  # type: ignore[type-arg]
) -> None:
    """Child-process target: wait for all siblings, then emit one record."""
    try:
        barrier.wait(timeout=15)
        handler.emit(_make_record(f"pid {os.getpid()}"))
        result_queue.put(("rollovers", handler.num_rollovers))
    except Exception as exc:
        result_queue.put(("error", str(exc)))
    finally:
        handler.close()


# Threading test


def test_concurrent_timed_rotation_threads_single_rollover(tmp_path: Path) -> None:
    """
    N threads sharing one handler all try to trigger a timed rollover
    simultaneously.  The file lock must serialise them so exactly one rotation
    occurs and every subsequent thread skips (reads the updated rolloverAt).

    This tests in-process thread safety; threads share a process and therefore
    a single open-file-description for the lock file, so flock() does serialise
    them correctly (same-process flock upgrade).  The real concurrent-process
    scenario is tested in test_concurrent_timed_rotation_forked_handler_*.
    """
    log_file = tmp_path / "threaded.log"
    handler = ConcurrentTimedRotatingFileHandler(
        str(log_file), when="S", interval=3600, backupCount=5
    )
    handler.emit(_make_record("init"))

    _backdate_rollover(handler)

    N = 8
    barrier = threading.Barrier(N)
    errors: list = []

    def worker() -> None:
        try:
            barrier.wait()
            handler.emit(_make_record(f"thread {threading.get_ident()}"))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    handler.close()

    assert not errors, f"Thread errors: {errors}"

    rotated = [
        f
        for f in tmp_path.iterdir()
        if f.name.startswith("threaded.log.") and "lock" not in f.name
    ]
    assert handler.num_rollovers == 1, (
        f"Expected 1 rollover, got {handler.num_rollovers}. "
        f"Rotated files: {[f.name for f in rotated]}"
    )
    assert (
        len(rotated) == 1
    ), f"Expected 1 rotated file, found: {[f.name for f in rotated]}"


# Fork-based test (POSIX only)


@pytest.mark.skipif(
    sys.platform == "win32", reason="os.fork() not available on Windows"
)
def test_concurrent_timed_rotation_forked_handler_single_rollover(
    tmp_path: Path,
) -> None:
    """
    Regression test for the flock/fork race condition.

    A handler is created BEFORE forking (the documented incorrect usage pattern
    that users frequently hit in practice).  N forked children all attempt to
    rotate at the same time.

    Without the fix:
        flock() is per-open-file-description.  All children share the parent's
        open file description for the lock file, so every child's flock() call
        returns immediately (same description already "holds" the lock).
        Multiple children enter doRollover() concurrently → extra rotated files.

    With the fix (_do_lock detects PID change and reopens the lock file):
        Each child gets its own file description.  flock() properly serialises
        them.  Exactly one child rotates; the rest read the updated rolloverAt
        and skip.
    """
    log_file = tmp_path / "fork_test.log"

    # ── Create handler BEFORE fork ──────────────────────────────────────────
    # This is the "incorrect usage" pattern from the bug report.
    handler = ConcurrentTimedRotatingFileHandler(
        str(log_file), when="S", interval=3600, backupCount=5
    )
    handler.emit(_make_record("init"))

    # Force every child to want a rollover
    _backdate_rollover(handler)

    N = 5
    ctx = multiprocessing.get_context("fork")
    barrier: multiprocessing.Barrier = ctx.Barrier(N)
    result_queue: multiprocessing.Queue = ctx.Queue()

    processes = [
        ctx.Process(
            target=_forked_worker,
            args=(handler, barrier, result_queue),
        )
        for _ in range(N)
    ]
    for p in processes:
        p.start()
    for p in processes:
        p.join(timeout=30)

    # Parent's copy is no longer needed
    handler.close()

    results: list = [result_queue.get_nowait() for _ in range(N)]
    child_errors = [r[1] for r in results if r[0] == "error"]
    assert not child_errors, f"Child process errors: {child_errors}"

    total_rollovers: int = sum(r[1] for r in results if r[0] == "rollovers")

    rotated = [
        f
        for f in tmp_path.iterdir()
        if f.name.startswith("fork_test.log.") and "lock" not in f.name
    ]

    assert total_rollovers == 1, (
        f"Expected exactly 1 rollover across {N} forked processes, got {total_rollovers}. "
        f"Rotated files: {[f.name for f in rotated]}"
    )
    assert (
        len(rotated) == 1
    ), f"Expected exactly 1 rotated file, found: {[f.name for f in rotated]}"


@pytest.mark.skipif(
    sys.platform == "win32", reason="os.fork() not available on Windows"
)
def test_concurrent_timed_rotation_forked_handler_with_gzip(tmp_path: Path) -> None:
    """Same as above but with gzip enabled, exercising the .gz path in doRollover."""
    log_file = tmp_path / "fork_gzip.log"

    handler = ConcurrentTimedRotatingFileHandler(
        str(log_file), when="S", interval=3600, backupCount=5, use_gzip=True
    )
    handler.emit(_make_record("init"))
    _backdate_rollover(handler)

    N = 4
    ctx = multiprocessing.get_context("fork")
    barrier: multiprocessing.Barrier = ctx.Barrier(N)
    result_queue: multiprocessing.Queue = ctx.Queue()

    processes = [
        ctx.Process(target=_forked_worker, args=(handler, barrier, result_queue))
        for _ in range(N)
    ]
    for p in processes:
        p.start()
    for p in processes:
        p.join(timeout=30)

    handler.close()

    results: list = [result_queue.get_nowait() for _ in range(N)]
    child_errors = [r[1] for r in results if r[0] == "error"]
    assert not child_errors, f"Child process errors: {child_errors}"

    total_rollovers: int = sum(r[1] for r in results if r[0] == "rollovers")

    rotated = [
        f
        for f in tmp_path.iterdir()
        if f.name.startswith("fork_gzip.log.") and "lock" not in f.name
    ]

    assert total_rollovers == 1, (
        f"Expected 1 rollover, got {total_rollovers}. "
        f"Rotated files: {[f.name for f in rotated]}"
    )
    # With gzip there should be exactly one .gz file
    gz_files = [f for f in rotated if f.name.endswith(".gz")]
    assert (
        len(gz_files) == 1
    ), f"Expected 1 gzipped rotated file, found: {[f.name for f in gz_files]}"
