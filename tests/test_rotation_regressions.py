# ruff: noqa: INP001

"""Regression tests for rotation edge cases found in the 0.9.30 review.

* A failed gzip (for example, disk full) leaves a rotated file uncompressed.
  The next rotation must keep that file, not delete or overwrite it.
* The stale-handle check runs once per emit(), not twice.
* ConcurrentTimedRotatingFileHandler has a class docstring.
"""

import errno
import gzip
import logging
import time
from pathlib import Path
from typing import List

import pytest

from concurrent_log_handler import (
    ConcurrentRotatingFileHandler,
    ConcurrentTimedRotatingFileHandler,
)


def _make_logger(name: str, handler: logging.Handler) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def _records_on_disk(directory: Path, markers: List[str]) -> List[str]:
    """Return the markers found in any log file in *directory*, compressed or not."""
    found = set()
    for path in directory.iterdir():
        if not path.name.startswith("app.log"):
            continue
        if path.name.endswith(".gz"):
            with gzip.open(path) as gz_file:
                data = gz_file.read()
        else:
            data = path.read_bytes()
        found.update(m for m in markers if m.encode() in data)
    return sorted(found)


def _fail_gzip(*_args: object, **_kwargs: object) -> None:
    raise OSError(errno.ENOSPC, "No space left on device")


def _force_timed_rollover(handler: ConcurrentTimedRotatingFileHandler, at: int) -> None:
    """Persist a past rollover time, so the next write rotates."""
    handler.clh._do_lock()
    try:
        handler.rolloverAt = at
        handler.write_rollover_time()
    finally:
        handler.clh._do_unlock()


def test_failed_gzip_backup_is_kept_on_next_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """After a failed gzip, "app.log.1" is uncompressed. The rotation chain
    only looked for ".N.gz", so the next rotation deleted "app.log.1" even
    though backupCount allowed more files.
    """
    handler = ConcurrentRotatingFileHandler(
        str(tmp_path / "app.log"), maxBytes=100, backupCount=5, use_gzip=True
    )
    logger = _make_logger(f"clh.gzfail.{request.node.name}", handler)
    padding = "x" * 120
    try:
        logger.info("record-A %s", padding)
        with monkeypatch.context() as patch:
            patch.setattr(gzip, "open", _fail_gzip)
            logger.info("record-B %s", padding)  # Rotation 1: gzip fails.
        logger.info("record-C %s", padding)  # Rotation 2.
    finally:
        handler.close()
        logger.handlers.clear()

    markers = ["record-A", "record-B", "record-C"]
    assert _records_on_disk(tmp_path, markers) == markers, sorted(
        p.name for p in tmp_path.iterdir()
    )


def test_timed_failed_gzip_file_is_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """After a failed gzip, the timed handler leaves "app.log.<period>"
    uncompressed. A second rotation in the same period checked only for
    "app.log.<period>.gz", so rename() replaced the uncompressed file.
    """
    handler = ConcurrentTimedRotatingFileHandler(
        str(tmp_path / "app.log"), when="H", backupCount=5, use_gzip=True
    )
    logger = _make_logger(f"clh.timed_gzfail.{request.node.name}", handler)
    past = int(time.time()) - 1  # Same value twice gives the same period name.
    try:
        logger.info("record-A")
        _force_timed_rollover(handler, past)
        with monkeypatch.context() as patch:
            patch.setattr(gzip, "open", _fail_gzip)
            logger.info("record-B")  # Rotation 1: gzip fails.
        _force_timed_rollover(handler, past)
        logger.info("record-C")  # Rotation 2, same period name.
    finally:
        handler.close()
        logger.handlers.clear()

    markers = ["record-A", "record-B", "record-C"]
    assert _records_on_disk(tmp_path, markers) == markers, sorted(
        p.name for p in tmp_path.iterdir()
    )


@pytest.mark.parametrize("timed", [False, True], ids=["size", "timed"])
def test_stale_handle_checked_once_per_emit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    timed: bool,
) -> None:
    """emit() calls _check_stream() before shouldRollover(). _shouldRollover()
    called it again, which cost a second stat() and fstat() on every write.
    """
    log_file = str(tmp_path / "app.log")
    handler: logging.Handler
    if timed:
        handler = ConcurrentTimedRotatingFileHandler(
            log_file, when="H", maxBytes=1_000_000
        )
    else:
        handler = ConcurrentRotatingFileHandler(log_file, maxBytes=1_000_000)

    calls: List[int] = []
    real_check = ConcurrentRotatingFileHandler._check_stream

    def counting_check(self: ConcurrentRotatingFileHandler) -> None:
        calls.append(1)
        real_check(self)

    monkeypatch.setattr(ConcurrentRotatingFileHandler, "_check_stream", counting_check)

    num_records = 5
    logger = _make_logger(f"clh.checks.{request.node.name}", handler)
    try:
        for i in range(num_records):
            logger.info("record %d", i)
    finally:
        handler.close()
        logger.handlers.clear()

    assert len(calls) == num_records


def test_timed_handler_has_docstring() -> None:
    assert ConcurrentTimedRotatingFileHandler.__doc__
