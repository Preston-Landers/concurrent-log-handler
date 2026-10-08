# ruff: noqa: INP001

"""Regression test: a forked child must not inherit a held ``_thread_lock``.

0.9.29 added ``_thread_lock`` (an RLock) to serialize threads in ``_do_lock()``.
If one thread is inside ``emit()`` when another thread calls ``fork()``, the
child gets a copy of the RLock in the held state. The thread that holds it does
not exist in the child, so nothing releases it, and the child's first log call
blocks forever.

On Python 3.9+, logging calls ``Handler._at_fork_reinit()`` in the child. The
handler overrides it to reset ``_thread_lock`` as well.
"""

import logging
import os
import signal
import sys
import threading
from pathlib import Path

import pytest

from concurrent_log_handler import ConcurrentRotatingFileHandler

TIMEOUT = 10  # seconds


@pytest.mark.skipif(
    sys.platform == "win32", reason="os.fork() not available on Windows"
)
@pytest.mark.skipif(
    sys.version_info < (3, 9),
    reason="Before 3.9, logging holds every handler lock across fork(), "
    "so emit() cannot be in progress and the deadlock cannot occur",
)
# Python 3.12+ warns about fork() in a process with threads.
@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_fork_while_another_thread_is_in_emit(tmp_path: Path) -> None:
    in_write = threading.Event()
    forked = threading.Event()

    class PausingHandler(ConcurrentRotatingFileHandler):
        """Holds the locks inside do_write() until the main thread has forked."""

        def do_write(self, msg: str) -> None:
            if msg == "from thread":
                in_write.set()
                forked.wait(TIMEOUT)
            super().do_write(msg)

    log_file = tmp_path / "fork.log"
    handler = PausingHandler(str(log_file))
    logger = logging.getLogger("clh.fork_deadlock")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    thread = threading.Thread(target=logger.info, args=("from thread",))
    thread.start()
    assert in_write.wait(TIMEOUT), "The thread never reached do_write()"

    pid = os.fork()
    if pid == 0:
        # Child. If the log call hangs, SIGALRM ends the process, and the
        # parent sees that in the exit status.
        signal.alarm(TIMEOUT)
        exit_code = 1
        try:
            logger.info("from child")
            exit_code = 0
        finally:
            os._exit(exit_code)

    forked.set()
    thread.join(TIMEOUT)
    _, status = os.waitpid(pid, 0)
    handler.close()
    logger.handlers.clear()

    assert not os.WIFSIGNALED(status), (
        f"The child hung on its first log call and was killed by signal "
        f"{os.WTERMSIG(status)}. It inherited _thread_lock in the held state."
    )
    assert os.WEXITSTATUS(status) == 0, "The child's log call raised an exception"
    content = log_file.read_text()
    assert "from thread" in content
    assert "from child" in content
