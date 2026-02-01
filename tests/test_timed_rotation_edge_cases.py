# ruff: noqa: INP001

import datetime
import sys
import time
from pathlib import Path

import pytest

from concurrent_log_handler import (
    ConcurrentTimedRotatingFileHandler,
)

# A known "bad" timestamp, like the start of the Unix Epoch
BAD_TIMESTAMP = 0.0


def test_rollover_with_bad_time(monkeypatch, tmp_path: Path):
    """
    Verify doRollover() doesn't create a malformed filename and correctly
    computes the next rollover time if time.time() fails mid-operation.
    """
    log_file = tmp_path / "test.log"

    # Let the handler initialize normally with a valid future rollover time.
    handler = ConcurrentTimedRotatingFileHandler(
        log_file, when="S", interval=10, debug=True
    )
    assert handler.rolloverAt > handler.MIN_VALID_TIMESTAMP

    # To trigger the rollover, set the rollover time to the past, but keep it
    # a valid timestamp. This avoids the initial sanity check in doRollover().
    handler.rolloverAt = int(time.time()) - 1

    # Now, patch time.time() to return the bad value. This will be hit by the
    # parent TimedRotatingFileHandler.shouldRollover logic.
    monkeypatch.setattr(time, "time", lambda: BAD_TIMESTAMP)

    # Trigger the rollover. Inside doRollover, the call to _get_current_time will
    # be activated, detect the bad timestamp, and recover.
    handler.doRollover()
    handler.close()

    # The key validation: check that no file with an epoch date stamp was created.
    found_files = list(tmp_path.glob("*"))
    for f in found_files:
        assert "1969" not in f.name
        assert "1970" not in f.name

    # Crucial check: doRollover should have successfully recovered and calculated
    # a new *future* rollover time based on the *recovered* time.
    # To verify this, we must get the real time, bypassing the monkeypatch.
    good_current_time = datetime.datetime.now().timestamp()  # noqa: DTZ005
    assert handler.rolloverAt > good_current_time


# Edge case for an edge case
@pytest.mark.skipif(
    sys.version_info < (3, 8), reason="Python 3.8 more permissive with dates"
)
def test_getFilesToDelete_ignores_unparseable_dates(tmp_path: Path, mocker):
    """
    This test ensures that files with date-like names that are not valid
    dates are ignored, rather than being sorted by mtime.

    GIVEN a handler with backupCount=1.
    WHEN the log directory contains:
         1. An old, valid backup file.
         2. A new, valid backup file.
         3. An invalidly-named file whose mtime is misleadingly old.
    THEN the method should ignore the invalid file and correctly delete the
         oldest valid file.
    """
    # 1. GIVEN: Set up the handler
    base_filename = tmp_path / "app.log"
    handler = ConcurrentTimedRotatingFileHandler(
        filename=str(base_filename),
        when="D",
        backupCount=1,  # Keep only 1 valid backup
        debug=True,  # Use debug to test the log output for the fix
        lock_file_directory=str(tmp_path),
    )

    # 2. GIVEN: A file list with a malformed date
    mock_filenames = [
        "app.log.2025-06-01.gz",  # Oldest valid file (should be deleted)
        "app.log.2025-06-05.gz",  # Newest valid file (should be kept)
        "app.log.9999-99-99.gz",  # Invalid date, matches regex
    ]
    mocker.patch("os.listdir", return_value=mock_filenames)

    # 3. GIVEN: A mock for getmtime where the invalid file is oldest
    now = time.time()
    mtime_map = {
        # This mtime will be used by the flawed code for the invalid file
        str(tmp_path / "app.log.9999-99-99.gz"): now
        - 86400 * 10,  # 10 days old
    }
    # We don't need to provide mtimes for the valid files as they shouldn't be used
    mocker.patch("os.path.getmtime", side_effect=lambda path: mtime_map.get(path))

    # Mock the console logger to check the output
    mock_console_log = mocker.patch.object(handler, "_console_log")

    # 4. WHEN: We call the method under test
    files_to_delete = handler.getFilesToDelete()

    # 5. THEN: Assert that ONLY the oldest VALID file is chosen
    expected_to_delete = [
        str(tmp_path / "app.log.2025-06-01.gz"),
    ]

    # --- EXPECTED BEHAVIOR ---
    #
    # With version 0.9.26:  (with mtime fallback):
    # 1. It fails `strptime` on '...9999-99-99.gz' and falls back to its mtime.
    # 2. It sorts the files based on a mix of real timestamps and our fake mtime.
    # 3. It incorrectly sees '...9999-99-99.gz' as the oldest file.
    # 4. With backupCount=1, it must delete 2 files. It will return a list
    #    containing the invalid file and the old valid file.
    # 5. The test FAILS.
    #
    # With the fix for issue #73 (with `continue` in except):
    # 1. It fails `strptime` on '...9999-99-99.gz' and `continue`s, skipping it.
    # 2. It adds only the two valid files to its sort list.
    # 3. With backupCount=1, it must delete 1 file. It correctly chooses the
    #    oldest of the valid files.
    # 4. The test PASSES.
    # 5. A debug message for the skipped file will have been logged.

    # Primary assertion for correctness
    assert sorted(files_to_delete) == sorted(expected_to_delete)

    # Secondary assertion to prove the fix was effective
    found_log = any(
        "Could not parse date" in call.args[0] and "9999-99-99" in call.args[0]
        for call in mock_console_log.call_args_list
    )
    assert (
        found_log
    ), "The fixed code should log a debug message for the unparseable file"

    handler.close()
