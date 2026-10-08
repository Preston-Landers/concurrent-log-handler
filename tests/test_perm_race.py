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

Call sites exercised:

* ``_open_lockfile()`` -- the per-handler ``.<name>.lock`` file
* ``do_open()``       -- the main log file
* ``doRollover()``    -- the ``.1.gz`` file produced during size-based rotation
* ``ConcurrentTimedRotatingFileHandler`` -- its main log file and ``.gz`` files

Other tests cover the follow-up fixes for the 0.9.30 release: falling back
when the filesystem has no hard links, tolerating chmod failures on files
that another user owns, and honoring ``umask`` when only ``owner`` is set.
"""

import errno
import gzip
import logging
import os
import stat
import time
from pathlib import Path
from typing import List, Optional, Tuple

import pytest

from concurrent_log_handler import (
    ConcurrentRotatingFileHandler,
    ConcurrentTimedRotatingFileHandler,
)

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


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _log_records(handler: logging.Handler, name: str, messages: List[str]) -> None:
    """Log each message through a fresh logger, then close the handler.

    ``logger.info()`` never raises: a failed write only goes to
    ``handleError()``. So callers must check the file contents, not exceptions.
    """
    logger = _make_logger(name, handler)
    try:
        for msg in messages:
            logger.info(msg)
    finally:
        handler.close()
        logger.handlers.clear()


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
    """The ``.N.gz`` files produced during size-based rotation must never be
    visible on disk with umask-derived permissions. The ``.gz`` is written
    under a temporary name with the right perms and then renamed, so
    ``_do_chown_and_chmod`` should not need to correct a public name at all.
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
    public_gz = [p for p in tmp_path.iterdir() if p.name in public_gz_targets]
    assert public_gz, (
        f"Expected at least one rotated .gz file, found: "
        f"{sorted(p.name for p in tmp_path.iterdir())}"
    )
    wrong_final = [
        (p.name, oct(_mode(p))) for p in public_gz if _mode(p) != TARGET_CHMOD
    ]
    assert not wrong_final, (
        f"Rotated .gz file(s) have the wrong final mode "
        f"(expected {oct(TARGET_CHMOD)}): {wrong_final}"
    )

    bad = _check_target_modes(observed, public_gz_targets)
    assert not bad, (
        f"Public-name rotated .gz file(s) were visible on disk with the wrong "
        f"mode (expected {oct(TARGET_CHMOD)}) before _do_chown_and_chmod "
        f"corrected them: {bad}. This is the permission race window on the "
        f"gzip rotation path."
    )


RECORDS = ["record one", "record two", "record three"]


def _missing_records(log_file: Path) -> List[str]:
    content = log_file.read_text() if log_file.exists() else ""
    return [r for r in RECORDS if r not in content]


def test_no_hard_links_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Some filesystems have no hard links (FAT/exFAT, many SMB/CIFS and FUSE
    mounts), so ``os.link()`` fails with EOPNOTSUPP or EPERM. The handler must
    fall back to create-then-chmod and keep every record.
    """

    def no_link(_src: str, dst: str) -> None:
        raise OSError(errno.EOPNOTSUPP, "Operation not supported", dst)

    monkeypatch.setattr(os, "link", no_link)

    log_file = tmp_path / "nolink.log"
    handler = ConcurrentRotatingFileHandler(
        str(log_file), chmod=TARGET_CHMOD, umask=RESTRICTIVE_UMASK
    )
    _log_records(handler, f"clh.nolink.{request.node.name}", RECORDS)

    missing = _missing_records(log_file)
    assert not missing, f"Records lost when os.link() is not supported: {missing}"
    # The fallback still applies the configured mode after it creates the files.
    assert _mode(log_file) == TARGET_CHMOD
    assert _mode(tmp_path / ".__nolink.lock") == TARGET_CHMOD
    # No temp files are left behind.
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        ".__nolink.lock",
        "nolink.log",
    ]


def test_chmod_skipped_when_mode_already_correct(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """The Issue #87 setup: user A created the files with the configured mode,
    and user B (same config, not the owner) opens them. Only the owner can
    chmod a file, so B must not try when the mode is already correct.
    """
    log_file = tmp_path / "shared.log"
    for path in (log_file, tmp_path / ".__shared.lock"):
        path.touch()
        path.chmod(TARGET_CHMOD)

    chmod_calls: List[str] = []
    real_chmod = os.chmod

    def spy_chmod(path: str, mode: int) -> None:
        chmod_calls.append(str(path))
        real_chmod(path, mode)

    monkeypatch.setattr(os, "chmod", spy_chmod)

    handler = ConcurrentRotatingFileHandler(str(log_file), chmod=TARGET_CHMOD)
    _log_records(handler, f"clh.shared.{request.node.name}", RECORDS)

    assert not chmod_calls, f"Unneeded chmod calls: {chmod_calls}"
    assert not _missing_records(log_file)


def test_chmod_not_permitted_still_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """The Issue #87 setup, but the existing files do not have the configured
    mode. This process does not own them, so chmod fails with EPERM. The
    files are still writable, so every record must be kept.
    """
    log_file = tmp_path / "foreign.log"
    lock_file = tmp_path / ".__foreign.lock"
    foreign = {str(log_file), str(lock_file)}
    for path in (log_file, lock_file):
        path.touch()
        path.chmod(0o644)  # Writable by this process, but not TARGET_CHMOD.

    real_chmod = os.chmod

    def chmod_as_non_owner(path: str, mode: int) -> None:
        if str(path) in foreign:
            raise PermissionError(errno.EPERM, "Operation not permitted", str(path))
        real_chmod(path, mode)

    monkeypatch.setattr(os, "chmod", chmod_as_non_owner)

    handler = ConcurrentRotatingFileHandler(str(log_file), chmod=TARGET_CHMOD)
    _log_records(handler, f"clh.foreign.{request.node.name}", RECORDS)

    missing = _missing_records(log_file)
    assert not missing, f"Records lost when chmod is not permitted: {missing}"


@pytest.mark.parametrize("umask", [0o002, 0o077])
def test_owner_without_chmod_honors_umask(
    tmp_path: Path, request: pytest.FixtureRequest, umask: int
) -> None:
    """With ``owner`` set but no ``chmod``, new files must get the mode the
    configured ``umask`` gives, as in 0.9.29: 0o664 for 0o002 and 0o600 for
    0o077. Not always 0o600 (what tempfile.mkstemp() gives), and never wider
    than the umask allows.
    """
    pwd = pytest.importorskip("pwd")
    grp = pytest.importorskip("grp")
    # chown to our own user and primary group needs no privileges.
    owner = (pwd.getpwuid(os.getuid()).pw_name, grp.getgrgid(os.getgid()).gr_name)

    log_file = tmp_path / "owned.log"
    handler = ConcurrentRotatingFileHandler(str(log_file), owner=owner, umask=umask)
    _log_records(handler, f"clh.owned.{request.node.name}", RECORDS)

    expected = oct(0o666 & ~umask)
    modes = {p.name: oct(_mode(p)) for p in (log_file, tmp_path / ".__owned.lock")}
    assert modes == {"owned.log": expected, ".__owned.lock": expected}


def test_timed_handler_log_file_never_has_wrong_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """``ConcurrentTimedRotatingFileHandler.__init__`` must not let the stdlib
    create the log file. The stdlib uses the process umask and applies no
    chmod, so the file would have the wrong mode until the first write.
    """
    observed = _install_chmod_spy(monkeypatch)

    log_file = tmp_path / "timed.log"
    # Make the process umask restrictive, so a file the stdlib creates is
    # certain to have the wrong mode.
    prev_umask = os.umask(RESTRICTIVE_UMASK)
    try:
        handler = ConcurrentTimedRotatingFileHandler(
            str(log_file),
            when="H",
            backupCount=3,
            chmod=TARGET_CHMOD,
            umask=RESTRICTIVE_UMASK,
        )
    finally:
        os.umask(prev_umask)
    mode_after_init = _mode(log_file) if log_file.exists() else None
    _log_records(handler, f"clh.timed.{request.node.name}", RECORDS)

    assert mode_after_init in (None, TARGET_CHMOD), (
        f"After __init__, the log file had mode {oct(mode_after_init or 0)}, "
        f"expected {oct(TARGET_CHMOD)} (or no file yet)"
    )
    assert _mode(log_file) == TARGET_CHMOD
    assert not _missing_records(log_file)
    bad = _check_target_modes(observed, ["timed.log", ".__timed.lock"])
    assert not bad, f"Public-name file(s) visible with the wrong mode: {bad}"


def test_no_perm_race_on_timed_gzip_rotation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """The timed handler gzips straight to the public ``.gz`` name. That file
    must already have the configured mode when gzip starts to write it, and
    must keep that mode.
    """
    modes_at_open: List[Tuple[str, Optional[int]]] = []
    real_gzip_open = gzip.open

    def spy_gzip_open(filename: str, mode: str = "rb", **kwargs: int) -> object:
        if "w" in mode:
            path = Path(filename)
            modes_at_open.append((path.name, _mode(path) if path.exists() else None))
        return real_gzip_open(filename, mode, **kwargs)

    monkeypatch.setattr(gzip, "open", spy_gzip_open)

    log_file = tmp_path / "timed_gz.log"
    handler = ConcurrentTimedRotatingFileHandler(
        str(log_file),
        when="H",
        backupCount=3,
        use_gzip=True,
        chmod=TARGET_CHMOD,
        umask=RESTRICTIVE_UMASK,
    )
    logger = _make_logger(f"clh.timed_gz.{request.node.name}", handler)
    try:
        logger.info("before rollover")
        # Move the shared rollover time into the past, so the next write rotates.
        handler.clh._do_lock()
        try:
            handler.rolloverAt = int(time.time()) - 1
            handler.write_rollover_time()
        finally:
            handler.clh._do_unlock()
        logger.info("after rollover")
    finally:
        handler.close()
        logger.handlers.clear()

    names = sorted(p.name for p in tmp_path.iterdir())
    gz_files = [p for p in tmp_path.iterdir() if p.name.endswith(".gz")]
    assert gz_files, f"No .gz file after rollover: {names}"
    wrong_final = [
        (p.name, oct(_mode(p))) for p in gz_files if _mode(p) != TARGET_CHMOD
    ]
    assert not wrong_final, f"Rotated .gz file(s) have the wrong mode: {wrong_final}"

    wrong_at_open = [
        (n, oct(m) if m else None) for n, m in modes_at_open if m != TARGET_CHMOD
    ]
    assert not wrong_at_open, (
        f"These .gz files did not exist with mode {oct(TARGET_CHMOD)} when gzip "
        f"started to write them (None = not created yet): {wrong_at_open}"
    )
