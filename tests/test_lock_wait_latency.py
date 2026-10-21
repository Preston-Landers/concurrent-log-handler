# ruff: noqa: INP001, S603

"""Regression test for issue #90: a waiting emit() must not stall for 1 second.

portalocker 3.2.0 and later use ``msvcrt.locking(LK_LOCK)`` for exclusive locks
on Windows. If the lock is held, the C runtime sleeps 1 second between retries,
even when the holder releases the lock a few milliseconds later. So a process
that logs while another process writes waits about 1000 ms, not the few
milliseconds that the other write takes.

In the first test, a second process holds the lock file for a short time. The
main process logs during that time. The emit must finish soon after the second
process releases the lock.

The other tests mock ``lock()`` to check the polling logic of
``_lock_stream_lock()`` on all platforms.
"""

import logging
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock

import pytest
from portalocker import LOCK_EX, LOCK_NB, AlreadyLocked, LockException
from pytest_mock import MockerFixture

from concurrent_log_handler import ConcurrentRotatingFileHandler

HOLD_SECONDS = 0.2  # How long the second process holds the lock
# The emit must wait for the holder, so it takes at least part of HOLD_SECONDS.
# If it takes less, the holder released the lock too early and the test proves
# nothing.
MIN_WAIT_SECONDS = 0.05
# The 1 second stall gives at least 1.0 s. This limit leaves room for a slow CI
# machine.
MAX_WAIT_SECONDS = 0.75
TIMEOUT = 30  # seconds

HOLDER_SCRIPT = textwrap.dedent("""
    import sys
    import time

    from portalocker import LOCK_EX, lock, unlock

    with open(sys.argv[1], "a") as fh:
        lock(fh, LOCK_EX)
        print("locked", flush=True)
        time.sleep(float(sys.argv[2]))
        unlock(fh)
    """)


def test_emit_does_not_stall_while_another_process_holds_lock(
    tmp_path: Path,
) -> None:
    handler = ConcurrentRotatingFileHandler(str(tmp_path / "wait.log"))
    logger = logging.getLogger("clh.lock_wait_latency")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    try:
        # The first emit makes sure that the lock file exists.
        logger.info("before")

        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                HOLDER_SCRIPT,
                handler.lockFilename,
                str(HOLD_SECONDS),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "locked"

            t0 = time.perf_counter()
            logger.info("while locked")
            waited = time.perf_counter() - t0

            assert holder.wait(TIMEOUT) == 0
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait(TIMEOUT)
    finally:
        handler.close()
        logger.handlers.clear()

    assert waited >= MIN_WAIT_SECONDS, (
        f"The emit took only {waited * 1000:.0f} ms, so it did not wait for the "
        "second process. The test did not create lock contention."
    )
    assert waited < MAX_WAIT_SECONDS, (
        f"The emit waited {waited * 1000:.0f} ms for a lock that was held for "
        f"{HOLD_SECONDS * 1000:.0f} ms. Expected less than "
        f"{MAX_WAIT_SECONDS * 1000:.0f} ms."
    )
    content = (tmp_path / "wait.log").read_text()
    assert "before" in content
    assert "while locked" in content


@pytest.fixture
def handler(tmp_path: Path) -> Iterator[ConcurrentRotatingFileHandler]:
    handler = ConcurrentRotatingFileHandler(str(tmp_path / "mock.log"))
    handler.lockPollInterval = 0
    yield handler
    handler.close()


def test_windows_polls_while_lock_is_held(
    handler: ConcurrentRotatingFileHandler, mocker: MockerFixture
) -> None:
    busy_polls = 2
    handler.is_posix = False
    mock_lock = mocker.patch(
        "concurrent_log_handler.lock",
        side_effect=[AlreadyLocked()] * busy_polls + [None],
    )

    handler._lock_stream_lock(MagicMock())

    assert mock_lock.call_count == busy_polls + 1
    for call in mock_lock.call_args_list:
        assert call[0][1] == LOCK_EX | LOCK_NB


def test_windows_raises_other_lock_errors_at_once(
    handler: ConcurrentRotatingFileHandler, mocker: MockerFixture
) -> None:
    handler.is_posix = False
    mock_lock = mocker.patch(
        "concurrent_log_handler.lock", side_effect=LockException("not contention")
    )

    with pytest.raises(LockException):
        handler._lock_stream_lock(MagicMock())

    assert mock_lock.call_count == 1


def test_posix_uses_one_blocking_lock(
    handler: ConcurrentRotatingFileHandler, mocker: MockerFixture
) -> None:
    handler.is_posix = True
    mock_lock = mocker.patch("concurrent_log_handler.lock")

    handler._lock_stream_lock(MagicMock())

    mock_lock.assert_called_once()
    assert mock_lock.call_args[0][1] == LOCK_EX


def test_contention_does_not_use_up_lock_attempts(
    handler: ConcurrentRotatingFileHandler, mocker: MockerFixture
) -> None:
    """Waiting for another process is not a failed attempt, so _do_lock()
    must not raise "Cannot acquire lock" while the other process holds the
    lock longer than maxLockAttempts polls."""
    busy_polls = 10
    handler.is_posix = False
    handler.maxLockAttempts = 2
    mock_lock = mocker.patch(
        "concurrent_log_handler.lock",
        side_effect=[AlreadyLocked()] * busy_polls + [None],
    )
    mocker.patch("concurrent_log_handler.unlock")

    handler._do_lock()
    try:
        assert handler.is_locked
        assert mock_lock.call_count == busy_polls + 1
    finally:
        handler._do_unlock()
    assert not handler.is_locked
