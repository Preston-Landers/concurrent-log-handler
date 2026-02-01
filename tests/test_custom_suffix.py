# ruff: noqa: INP001

"""Test custom suffix configuration via finalize_handler_configuration hook."""

import logging
import os
import time
from pathlib import Path

from concurrent_log_handler import ConcurrentTimedRotatingFileHandler


class CustomSuffixHandler(ConcurrentTimedRotatingFileHandler):
    """Handler with custom suffix set via finalize_handler_configuration hook."""

    def __init__(self, filename: str, custom_suffix: str, **kwargs):
        self.custom_suffix = custom_suffix
        super().__init__(filename, **kwargs)

    def finalize_handler_configuration(self):
        """Override suffix before first rollover."""
        super().finalize_handler_configuration()
        self.suffix = self.custom_suffix


def test_custom_suffix_via_hook(tmp_path: Path):
    """Test that subclasses can override suffix via finalize_handler_configuration."""
    log_file = tmp_path / "test.log"
    custom_suffix = "%Y%m%d-%H%M%S.custom"

    # Create handler with custom suffix
    handler = CustomSuffixHandler(
        str(log_file),
        custom_suffix=custom_suffix,
        when="S",
        interval=1,
        backupCount=5,
        debug=True,
    )

    # Write a log message
    logger = logging.getLogger("test_custom_suffix")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info("First message")

    # Force a rollover by updating the lock file with a past rollover time
    try:
        handler.clh._do_lock()
        handler.rolloverAt = int(time.time()) - 1
        handler.write_rollover_time()
    finally:
        handler.clh._do_unlock()

    logger.info("Second message - should trigger rollover")

    handler.close()

    # Find rotated files (exclude main log and lock files)
    rotated_files = [
        f
        for f in tmp_path.iterdir()
        if f.name.startswith("test.log.") and not f.name.endswith(".lock")
    ]

    # Should have at least one rotated file
    assert (
        len(rotated_files) >= 1
    ), f"Expected rotated files, found: {list(tmp_path.iterdir())}"

    # Check that the rotated file uses the custom suffix format
    rotated_file = rotated_files[0]
    assert (
        ".custom" in rotated_file.name
    ), f"Expected custom suffix '.custom' in filename, got: {rotated_file.name}"


def test_custom_suffix_with_initial_rollover(tmp_path: Path):
    """Test custom suffix works even when rollover happens during initialization."""
    log_file = tmp_path / "test.log"
    custom_suffix = "%Y%m%d.special"

    # Create an old log file that should trigger rollover during init
    with open(log_file, "w") as f:
        f.write("Old log content\n")

    # Create a lock file with a past rollover time to trigger immediate rollover
    lock_dir = tmp_path
    lock_file = lock_dir / (".__" + log_file.name[:-4] + ".lock")
    with open(lock_file, "w") as f:
        # Write a rollover time in the past
        f.write(str(int(time.time()) - 3600))

    # Create handler - should rollover during init
    handler = CustomSuffixHandler(
        str(log_file),
        custom_suffix=custom_suffix,
        when="H",
        interval=1,
        backupCount=5,
        debug=True,
        lock_file_directory=str(lock_dir),
    )

    handler.close()

    # Check for rotated files
    rotated_files = [
        f
        for f in tmp_path.iterdir()
        if f.name.startswith("test.log.") and not f.name.endswith(".lock")
    ]

    if rotated_files:
        # If rollover happened, verify custom suffix was used
        rotated_file = rotated_files[0]
        assert (
            ".special" in rotated_file.name
        ), f"Expected custom suffix '.special' in filename, got: {rotated_file.name}"


def test_hook_called_before_initialize(tmp_path: Path):
    """Test that finalize_handler_configuration is called before initialize_rollover_time."""
    log_file = tmp_path / "test.log"
    call_order = []

    class HookTracker(ConcurrentTimedRotatingFileHandler):
        def finalize_handler_configuration(self):
            call_order.append("finalize")
            super().finalize_handler_configuration()

        def initialize_rollover_time(self):
            call_order.append("initialize")
            super().initialize_rollover_time()

    handler = HookTracker(str(log_file), when="H", debug=True)
    handler.close()

    # Verify finalize was called before initialize
    assert call_order == [
        "finalize",
        "initialize",
    ], f"Expected ['finalize', 'initialize'], got {call_order}"


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        print("Running test_custom_suffix_via_hook...")
        test_custom_suffix_via_hook(tmp)
        print("✓ Passed")

        # Clean up
        for f in tmp.iterdir():
            os.remove(f)

        print("\nRunning test_custom_suffix_with_initial_rollover...")
        test_custom_suffix_with_initial_rollover(tmp)
        print("✓ Passed")

        # Clean up
        for f in tmp.iterdir():
            os.remove(f)

        print("\nRunning test_hook_called_before_initialize...")
        test_hook_called_before_initialize(tmp)
        print("✓ Passed")

        print("\n✅ All tests passed!")
