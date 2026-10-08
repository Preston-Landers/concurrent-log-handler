# ruff: noqa: INP001

"""Regression tests: a forked child must not inherit the parent's lock state.

0.9.29 added ``_thread_lock`` (an RLock) to serialize threads in ``_do_lock()``.
If one thread is inside ``emit()`` when another thread calls ``fork()``, the
child gets a copy of the RLock in the held state. The thread that holds it does
not exist in the child, so nothing releases it, and the child's first log call
blocks forever.

The child also inherits ``is_locked=True``, and its lock file FD shares the
open file description with the parent. If the child called ``close()`` before
it logged (for example, through ``logging.shutdown()`` at exit), the
``unlock()`` in ``close()`` released the parent's file lock while the parent
was still writing.

On Python 3.9+, logging calls ``Handler._at_fork_reinit()`` in the child. The
handler overrides it to reset ``_thread_lock`` and ``is_locked`` as well.
"""

import logging
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Callable, Tuple

import portalocker
import pytest

from concurrent_log_handler import ConcurrentRotatingFileHandler

TIMEOUT = 10  # seconds
# Child exit code: it acquired the file lock, so the parent's lock was gone.
LOCK_WAS_RELEASED = 2

pytestmark = [
    pytest.mark.skipif(
        sys.platform == "win32", reason="os.fork() not available on Windows"
    ),
    pytest.mark.skipif(
        sys.version_info < (3, 9),
        reason="Before 3.9, logging holds every handler lock across fork(), "
        "so emit() cannot be in progress and these bugs cannot occur",
    ),
    # Python 3.12+ warns about fork() in a process with threads.
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
]


class PausingHandler(ConcurrentRotatingFileHandler):
    """Holds the locks inside do_write() until the test sets ``resume``."""

    def __init__(
        self, filename: str, in_write: threading.Event, resume: threading.Event
    ) -> None:
        super().__init__(filename)
        self._in_write = in_write
        self._resume = resume

    def do_write(self, msg: str) -> None:
        if msg == "from thread":
            self._in_write.set()
            self._resume.wait(TIMEOUT)
        super().do_write(msg)


def _start_paused_write(
    tmp_path: Path, name: str
) -> Tuple[PausingHandler, logging.Logger, threading.Thread, threading.Event]:
    """Start a thread that is paused inside do_write(), holding every lock.

    Returns the handler, its logger, the thread, and the event that lets the
    thread finish its write.
    """
    in_write = threading.Event()
    resume = threading.Event()
    handler = PausingHandler(str(tmp_path / "fork.log"), in_write, resume)
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    thread = threading.Thread(target=logger.info, args=("from thread",))
    thread.start()
    assert in_write.wait(TIMEOUT), "The thread never reached do_write()"
    return handler, logger, thread, resume


def _fork_and_run(child: Callable[[], int]) -> int:
    """Fork. In the child, run *child* and exit with its result; return the PID.

    If the child hangs, SIGALRM ends it, and the parent sees that in the exit
    status. An exception in *child* gives exit code 1.
    """
    pid = os.fork()
    if pid == 0:
        signal.alarm(TIMEOUT)
        exit_code = 1
        try:
            exit_code = child()
        finally:
            os._exit(exit_code)
    return pid


def test_fork_while_another_thread_is_in_emit(tmp_path: Path) -> None:
    handler, logger, thread, resume = _start_paused_write(tmp_path, "clh.fork_deadlock")

    def child() -> int:
        logger.info("from child")
        return 0

    pid = _fork_and_run(child)
    resume.set()
    thread.join(TIMEOUT)
    _, status = os.waitpid(pid, 0)
    handler.close()
    logger.handlers.clear()

    assert not os.WIFSIGNALED(status), (
        f"The child hung on its first log call and was killed by signal "
        f"{os.WTERMSIG(status)}. It inherited _thread_lock in the held state."
    )
    assert os.WEXITSTATUS(status) == 0, "The child's log call raised an exception"
    content = (tmp_path / "fork.log").read_text()
    assert "from thread" in content
    assert "from child" in content


def test_child_close_keeps_parent_lock(tmp_path: Path) -> None:
    """A child that closes the handler before it logs must not release the
    parent's file lock. The parent thread stays paused inside do_write(), so
    it holds the lock for the whole life of the child.
    """
    handler, logger, thread, resume = _start_paused_write(
        tmp_path, "clh.fork_child_close"
    )

    def child() -> int:
        handler.close()
        # A fresh open gives a new open file description, so this lock request
        # competes with the parent's. It must fail while the parent holds it.
        with open(handler.lockFilename, "r+") as lock_file:
            try:
                portalocker.lock(lock_file, portalocker.LOCK_EX | portalocker.LOCK_NB)
            except portalocker.exceptions.LockException:
                return 0
            return LOCK_WAS_RELEASED

    pid = _fork_and_run(child)
    _, status = os.waitpid(pid, 0)
    resume.set()
    thread.join(TIMEOUT)
    handler.close()
    logger.handlers.clear()

    assert not os.WIFSIGNALED(status), f"The child hung (signal {os.WTERMSIG(status)})"
    assert os.WEXITSTATUS(status) != LOCK_WAS_RELEASED, (
        "The child's close() released the parent's file lock. "
        "It inherited is_locked=True and unlocked the shared file description."
    )
    assert os.WEXITSTATUS(status) == 0, "The child raised an exception"
    assert "from thread" in (tmp_path / "fork.log").read_text()
