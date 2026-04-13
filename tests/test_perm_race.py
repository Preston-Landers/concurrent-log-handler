# ruff: noqa: INP001

"""Regression tests for the file-permission race window between file creation
and the post-create ``_do_chown_and_chmod()`` call.

Bug summary
-----------
When ``chmod`` and ``umask`` are both configured (a typical setup when
multiple system users share a log file), ``ConcurrentRotatingFileHandler``
creates files under the configured umask and then calls
``_do_chown_and_chmod()`` afterward to apply the target permissions. Between
the create and the chmod, the file is visible in the filesystem with
umask-derived permissions. A different-user process that opens the file
during that window gets ``PermissionError``.

These tests don't need root or multiple users. They patch
``_do_chown_and_chmod()`` to spy on the file's mode at the moment the call
is invoked: if the on-disk mode is anything other than the configured
``chmod`` at that point, a race window exists.

Three call sites are exercised:

* ``_open_lockfile()`` -- the per-handler ``.<name>.lock`` file
* ``do_open()``       -- the main log file
* ``doRollover()``    -- the ``.1.gz`` file produced during size-based rotation
"""

import logging
import os
import stat
from pathlib import Path
from typing import List, Tuple

import pytest

from concurrent_log_handler import ConcurrentRotatingFileHandler

# umask/chmod semantics are POSIX-specific.
pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="umask/chmod race only applies on POSIX"
)

# 0o666 -- world readable+writable. Any process (any user) should be able
# to open files with this mode.
TARGET_CHMOD = (
    stat.S_IRUSR
    | stat.S_IWUSR
    | stat.S_IRGRP
    | stat.S_IWGRP
    | stat.S_IROTH
    | stat.S_IWOTH
)
# 0o077 -- strip group and other bits, forcing umask-derived files to 0o600.
RESTRICTIVE_UMASK = 0o077


Observation = Tuple[str, int]  # (path, mode)


def _install_chmod_spy(monkeypatch: pytest.MonkeyPatch) -> List[Observation]:
    """Patch ``_do_chown_and_chmod`` to record the file's on-disk mode just
    before the original method runs. Returns the (mutable) list that will
    be populated as files are created.
    """
    observed: List[Observation] = []
    original = ConcurrentRotatingFileHandler._do_chown_and_chmod

    def spy(self: ConcurrentRotatingFileHandler, filename: str) -> None:
        if os.path.exists(filename):
            observed.append((filename, stat.S_IMODE(os.stat(filename).st_mode)))
        return original(self, filename)

    monkeypatch.setattr(ConcurrentRotatingFileHandler, "_do_chown_and_chmod", spy)
    return observed


def _make_logger(name: str, handler: logging.Handler) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def _check_target_modes(
    observed: List[Observation], targets: List[str]
) -> List[Tuple[str, str]]:
    """Filter ``observed`` to only paths matching one of ``targets`` (suffix
    match), then return any whose mode is not ``TARGET_CHMOD``.

    The race only matters for files at predictable, public names that other
    processes know to open. Transient files with random names (the tempfiles
    created inside ``_atomic_create_with_perms``, or the ``.rotate.<random>``
    intermediates in ``doRollover``) are *not* publicly observable and may
    legitimately be chmod-ed in place.
    """
    target_obs = [(p, m) for p, m in observed if any(p.endswith(t) for t in targets)]
    return [(p, oct(m)) for p, m in target_obs if m != TARGET_CHMOD]


def test_no_perm_race_on_lockfile_and_logfile_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """The lockfile and the main log file must not be visible on disk with
    umask-derived permissions before ``_do_chown_and_chmod`` corrects them.
    """
    observed = _install_chmod_spy(monkeypatch)

    handler = ConcurrentRotatingFileHandler(
        str(tmp_path / "race.log"),
        maxBytes=0,
        backupCount=0,
        encoding="utf-8",
        chmod=TARGET_CHMOD,
        umask=RESTRICTIVE_UMASK,
    )
    logger = _make_logger(f"clh.race.{request.node.name}", handler)
    try:
        logger.info("trigger lockfile + log file creation")
    finally:
        handler.close()
        logger.handlers.clear()

    # Sanity: both the lockfile and the log file should have been observed.
    seen_paths = {p for p, _ in observed}
    assert any(p.endswith("race.log") for p in seen_paths), (
        f"Expected _do_chown_and_chmod to be invoked on the log file; "
        f"observed paths were: {sorted(seen_paths)}"
    )
    assert any(p.endswith(".__race.lock") for p in seen_paths), (
        f"Expected _do_chown_and_chmod to be invoked on the lockfile; "
        f"observed paths were: {sorted(seen_paths)}"
    )

    bad = _check_target_modes(observed, ["race.log", ".__race.lock"])
    assert not bad, (
        f"Public-name file(s) were visible on disk with the wrong mode "
        f"(expected {oct(TARGET_CHMOD)}) before _do_chown_and_chmod "
        f"corrected them: {bad}. This is the permission race window that "
        f"causes PermissionError for cross-user log access."
    )


def test_no_perm_race_on_gzip_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """The ``.1.gz`` file produced during size-based rotation must not be
    visible on disk with umask-derived permissions before
    ``_do_chown_and_chmod`` corrects it.
    """
    observed = _install_chmod_spy(monkeypatch)

    log_file = tmp_path / "race_gz.log"
    handler = ConcurrentRotatingFileHandler(
        str(log_file),
        maxBytes=200,
        backupCount=3,
        encoding="utf-8",
        chmod=TARGET_CHMOD,
        umask=RESTRICTIVE_UMASK,
        use_gzip=True,
    )
    logger = _make_logger(f"clh.race_gz.{request.node.name}", handler)
    try:
        # Write enough output to force at least one rotation.
        for i in range(50):
            logger.info("padding message %02d %s", i, "x" * 50)
    finally:
        handler.close()
        logger.handlers.clear()

    # Only the public ".N.gz" rotation names matter; transient ".rotate.<N>.gz"
    # tempfiles created during do_gzip have random names that no other process
    # would attempt to open.
    public_gz_targets = [f"race_gz.log.{i}.gz" for i in range(1, 5)]
    public_gz_seen = [
        (p, m) for p, m in observed if any(p.endswith(t) for t in public_gz_targets)
    ]
    assert public_gz_seen, (
        f"Expected at least one public-name rotated .gz file to be observed "
        f"by _do_chown_and_chmod, but none were. All observations: {observed}"
    )

    bad = _check_target_modes(observed, public_gz_targets)
    assert not bad, (
        f"Public-name rotated .gz file(s) were visible on disk with the wrong "
        f"mode (expected {oct(TARGET_CHMOD)}) before _do_chown_and_chmod "
        f"corrected them: {bad}. This is the permission race window on the "
        f"gzip rotation path."
    )
