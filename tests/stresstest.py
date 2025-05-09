#!/usr/bin/env python
# ruff: noqa: S311, G004

"""
This is a simple stress test for concurrent_log_handler. It creates a number of processes and each process
logs a number of messages to a log file. We then validate that the sum of all log files (including
rotations) contains the expected number of messages and that each message is unique.

It can be run directly from the CLI for a single test run with certain parameters.
There is also a pytest based unit test that exercises several different sets of options.

This test requires Python 3.7 due to the use of dataclasses.
"""

import argparse
import glob
import gzip
import logging
import multiprocessing
import os
import random
import re
import string
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from concurrent_log_handler import (
    ConcurrentRotatingFileHandler,
    ConcurrentTimedRotatingFileHandler,
)
from concurrent_log_handler.__version__ import __version__


@dataclass(frozen=True)
class TestOptions:
    """Options to configure the stress test."""

    __test__ = False  # not a test case itself.

    # kwargs to pass to ConcurrentRotatingFileHandler
    log_opts: dict = field(default_factory=lambda: TestOptions.default_log_opts())

    log_file: str = field(default="stress_test.log")
    log_dir: str = field(default="output_tests")  # Base directory for logs
    num_processes: int = field(default=10)
    log_calls: int = field(default=1_000)

    use_asyncio: bool = field(default=False)  # Currently not implemented in test
    induce_failure: bool = field(default=False)
    sleep_min: float = field(default=0.0001)
    sleep_max: float = field(default=0.01)

    use_timed: bool = field(default=False)
    "Use time-based rotation class instead of size-based."

    min_rollovers: int = field(default=70)
    """Minimum number of TOTAL rollovers to expect across all processes.
    This value needs careful tuning based on test parameters."""

    # New field for explicit keep_file_open control
    keep_file_open_override: Optional[bool] = field(default=None)
    """If set, overrides the handler's default keep_file_open. None means use handler default."""

    @classmethod
    def default_log_opts(cls, override_values: Optional[Dict] = None) -> dict:
        rv = {
            "maxBytes": 1024 * 10,
            "backupCount": 2000,  # High default to ensure all lines are kept for most tests
            "encoding": "utf-8",
            "debug": False,
            "use_gzip": False,
            # 'keep_file_open' is not set here; handler's default (True) will apply
            # unless overridden by 'keep_file_open_override' in TestOptions.
        }
        if override_values:
            rv.update(override_values)
        return rv

    @classmethod
    def default_timed_log_opts(cls, override_values: Optional[Dict] = None) -> dict:
        rv = {
            "maxBytes": 0,  # For timed rotation, often no size limit by default
            "when": "S",
            "interval": 3,
            "backupCount": 2000,
            "encoding": "utf-8",
            "debug": False,
            "use_gzip": False,
        }
        if override_values:
            rv.update(override_values)
        return rv


class SharedCounter:
    def __init__(self, initial_value=0):
        self.value = multiprocessing.Value("i", initial_value)
        self.lock = multiprocessing.Lock()

    def increment(self, n=1):
        with self.lock:  # Simpler locking for multiprocessing.Value
            self.value.value += n

    def get_value(self):
        with self.lock:
            return self.value.value


class ConcurrentLogHandlerBuggyMixin:
    def emit(self, record):
        random_choice = random.randint(1, 100)
        if 1 <= random_choice <= 5:  # noqa: PLR2004
            return
        if 6 <= random_choice <= 10:  # noqa: PLR2004
            super().emit(record)
            super().emit(record)
        else:
            super().emit(record)


class ConcurrentLogHandlerBuggy(
    ConcurrentLogHandlerBuggyMixin, ConcurrentRotatingFileHandler
):
    pass


class ConcurrentTimedLogHandlerBuggy(
    ConcurrentLogHandlerBuggyMixin, ConcurrentTimedRotatingFileHandler
):
    pass


def worker_process(
    test_opts: TestOptions, process_id: int, rollover_counter: SharedCounter
):
    logger = logging.getLogger(f"Process-{process_id}")
    logger.setLevel(logging.DEBUG)

    # Start with a copy of log_opts from TestOptions
    final_log_opts = test_opts.log_opts.copy()

    # Apply keep_file_open_override if set in TestOptions
    if test_opts.keep_file_open_override is not None:
        final_log_opts["keep_file_open"] = test_opts.keep_file_open_override

    if test_opts.use_timed:
        file_handler_class = (
            ConcurrentTimedLogHandlerBuggy
            if test_opts.induce_failure
            else ConcurrentTimedRotatingFileHandler
        )
    else:
        file_handler_class = (
            ConcurrentLogHandlerBuggy
            if test_opts.induce_failure
            else ConcurrentRotatingFileHandler
        )

    # Ensure main log directory exists; handler creates lock_file_directory if specified and different
    os.makedirs(test_opts.log_dir, exist_ok=True)
    log_path = os.path.join(test_opts.log_dir, test_opts.log_file)

    # The handler itself will create lock_file_directory if it's specified in final_log_opts
    # and doesn't exist.
    file_handler = file_handler_class(log_path, mode="a", **final_log_opts)

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(message)s")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    char_choices = string.ascii_letters
    if final_log_opts.get("encoding", "utf-8") == "utf-8":  # Check final_log_opts
        char_choices = (
            string.ascii_letters
            + " \U0001d122\U00024b00\u20a0ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ"
        )

    for i in range(test_opts.log_calls):
        random_str = "".join(random.choices(char_choices, k=20))
        logger.debug(f"{process_id}-{i}-{random_str}")
        time.sleep(random.uniform(test_opts.sleep_min, test_opts.sleep_max))

    # num_rollovers is an instance variable on the handler.
    # The SharedCounter should be incremented with the final count from this handler instance.
    rollover_counter.increment(getattr(file_handler, "num_rollovers", 0))
    file_handler.close()  # Crucial to flush, close files, and release locks.


def validate_log_file(  # noqa: C901, PLR0911, PLR0912, PLR0915
    test_opts: TestOptions, run_time: float, expect_all: bool = True
) -> bool:
    process_tracker = {i: {} for i in range(test_opts.num_processes)}

    log_path = os.path.join(test_opts.log_dir, test_opts.log_file)
    all_log_files = sorted(glob.glob(f"{log_path}*"), reverse=True)

    encoding = test_opts.log_opts.get("encoding", "utf-8")  # Use .get for safety
    chars_read = 0

    if (
        not all_log_files
        and test_opts.log_calls > 0
        and not (
            not test_opts.use_timed
            and test_opts.log_opts.get("maxBytes", 0) > 0
            and test_opts.log_opts.get("backupCount", 0) == 0
        )
    ):
        print("Error: No log files found, but logs were expected.")
        return False

    for current_log_file in all_log_files:
        opener = open
        is_gzipped_file = current_log_file.endswith(".gz")
        if test_opts.log_opts.get("use_gzip"):
            if is_gzipped_file:
                opener = gzip.open
            elif current_log_file != log_path:  # Rotated file should be gzipped
                # Allow main log file to be uncompressed if it hasn't rotated yet
                if os.path.exists(
                    f"{current_log_file}.gz"
                ):  # Check if .gz version exists for some reason
                    pass  # Potentially a race condition where main file is about to be gzipped
                else:
                    print(
                        f"Warning: use_gzip was set, but rotated log file is not gzipped: {current_log_file}"
                    )
                    # Depending on strictness, this could be an error.

        msg_parts_size = 3

        try:
            with opener(current_log_file, "rb") as file:  # Always read as binary
                for line_no, binary_line in enumerate(file):
                    try:
                        line_content = binary_line.decode(encoding)
                    except UnicodeDecodeError as ude:
                        print(
                            f"UnicodeDecodeError in {current_log_file} at line {line_no + 1}: {ude}. Binary: {binary_line!r}"
                        )
                        return False  # Decoding error is a failure

                    chars_read += len(line_content)
                    parts = line_content.strip().split(" - ")
                    if len(parts) < msg_parts_size:  # asctime, name, message
                        print(
                            f"Warning: Malformed line in {current_log_file} at line {line_no + 1}: '{line_content.strip()}'"
                        )
                        continue  # Skip malformed line

                    message = parts[
                        -1
                    ]  # The actual log message "process_id-message_id-random_str"

                    msg_parts = message.split("-", 2)  # Split only twice
                    if len(msg_parts) < msg_parts_size:
                        print(
                            f"Warning: Malformed message part in {current_log_file} at line {line_no + 1}: '{message}'"
                        )
                        continue

                    process_id_str, message_id_str, _sub_message = msg_parts

                    try:
                        process_id = int(process_id_str)
                        message_id = int(message_id_str)
                    except ValueError:
                        print(
                            f"Warning: Non-integer process_id or message_id in {current_log_file} at line {line_no + 1}: '{message}'"
                        )
                        continue

                    msg_state = {
                        "file": os.path.basename(
                            current_log_file
                        ),  # Shorter name for printing
                        "line_no": line_no + 1,  # 1-based for humans
                    }
                    if message_id in process_tracker.get(
                        process_id, {}
                    ):  # Check if process_id is valid
                        print(
                            f"Error: Duplicate message from Process-{process_id}: ID {message_id}\n"
                            f"  Original: {process_tracker[process_id][message_id]}\n"
                            f"  Duplicate: {msg_state} (line content: '{line_content.strip()}')"
                        )
                        return False
                    if process_id not in process_tracker:
                        print(
                            f"Error: Unexpected process_id {process_id} found in logs."
                        )
                        return False
                    process_tracker[process_id][message_id] = msg_state
        except (
            gzip.BadGzipFile,
            EOFError,
        ) as e:  # Catch common issues with gzip files or incomplete files
            print(f"Error reading or decoding file {current_log_file}: {e}")
            return False

    total_logged_successfully = sum(
        len(messages) for messages in process_tracker.values()
    )
    expected_total_messages = test_opts.num_processes * test_opts.log_calls

    if expect_all:
        for process_id_val in range(test_opts.num_processes):
            if len(process_tracker[process_id_val]) != test_opts.log_calls:
                print(
                    f"Error: Missing messages from Process-{process_id_val}: "
                    f"Found {len(process_tracker[process_id_val])}, expected {test_opts.log_calls}"
                )
                # Optionally list some missing message_ids here
                return False
        if (
            total_logged_successfully != expected_total_messages
        ):  # Should be redundant if above check passes
            print(
                f"Error: Total messages logged ({total_logged_successfully}) != expected ({expected_total_messages})"
            )
            return False
    else:  # Not expecting all, but print info
        print(
            f"Info: 'expect_all_lines' was False. Validated {total_logged_successfully} unique messages."
        )
        if total_logged_successfully > expected_total_messages:
            print(
                f"Error: Logged more messages ({total_logged_successfully}) than expected ({expected_total_messages}) even with expect_all=False."
            )
            return False

    read_speed_info = ""
    if run_time > 0:
        read_speed_info = f"({chars_read / run_time:.2f} chars/sec)"
    print(
        f"{run_time:.2f} seconds to read {chars_read} chars "
        f"from {len(all_log_files)} files {read_speed_info}"
    )
    return True


def run_stress_test(  # noqa: C901, PLR0911, PLR0912, PLR0915
    test_opts: TestOptions,
) -> int:
    # Ensure the main log directory exists
    if not os.path.exists(test_opts.log_dir):
        os.makedirs(test_opts.log_dir, exist_ok=True)

    # If a custom lock directory is specified, ensure it exists
    custom_lock_dir = test_opts.log_opts.get("lock_file_directory")
    if custom_lock_dir:
        # Resolve if relative to log_dir for local test setups
        if not os.path.isabs(custom_lock_dir) and test_opts.log_dir:
            # Check if it might be intended as a sub-directory of log_dir
            # This logic can be tricky. Assume test_opts.log_opts["lock_file_directory"]
            # is either absolute or correctly relative from where the test is run.
            # For now, just ensure it exists if specified.
            pass  # The handler will create it. Or, for cleanup, it needs to be known.
        if not os.path.exists(custom_lock_dir):
            os.makedirs(custom_lock_dir, exist_ok=True)  # Create if doesn't exist

    delete_log_files(test_opts)

    processes = []
    rollover_counter = SharedCounter()
    start_time = time.time()

    for i in range(test_opts.num_processes):
        p = multiprocessing.Process(
            target=worker_process, args=(test_opts, i, rollover_counter)
        )
        p.start()
        processes.append(p)

    for p in processes:
        p.join()  # Wait for all worker processes to complete

    end_time = time.time()
    actual_total_rollovers = rollover_counter.get_value()

    print(
        f"All processes finished. (Total Rollovers Recorded: "
        f"{actual_total_rollovers} - min_expected was {test_opts.min_rollovers})"
    )

    log_path_base = os.path.join(test_opts.log_dir, test_opts.log_file)

    # Allow a brief moment for file system operations to settle after processes exit.
    # This seems to help with our count check below.
    settle_time = 0.4
    print(f"Pausing for {settle_time}s to allow file system to settle...")
    time.sleep(settle_time)

    all_log_files = glob.glob(f"{log_path_base}*")
    use_gzip = test_opts.log_opts.get("use_gzip", False)
    gzip_ext_pattern = r"\.gz" if use_gzip else ""

    # Check for incorrect naming (Issue #68)
    for file_path_iter in all_log_files:
        # Looks for patterns like ".1.2.gz" or ".1.2" (multiple consecutive number extensions)
        if re.search(
            r"\.\d+\.\d+" + gzip_ext_pattern, os.path.basename(file_path_iter)
        ):
            print(
                f"Error: Incorrect naming of log file (multiple numeric extensions): {file_path_iter}"
            )
            return 1

    # --- Determine validation expectations ---
    expect_all_lines = True  # Default assumption
    current_backup_count = test_opts.log_opts.get(
        "backupCount", -1
    )  # Use -1 if not set, to distinguish from 0

    # For CRFH, maxBytes drives rotation. For CTRFH, maxBytes can supplement time.
    current_max_bytes = test_opts.log_opts.get("maxBytes", 0)

    if not test_opts.use_timed:  # ConcurrentRotatingFileHandler
        if current_max_bytes <= 0:  # No size-based rotation
            expect_all_lines = True
            print(
                "Info: CRFH with maxBytes <= 0. No size-based rotation. Expecting all lines in one file."
            )
            if len(all_log_files) != 1:
                print(
                    f"Error: Expected 1 log file for CRFH with maxBytes=0, found {len(all_log_files)}. Files: {all_log_files}"
                )
                return 1
            if (
                actual_total_rollovers != 0 and test_opts.min_rollovers == 0
            ):  # If min_rollovers expects 0
                print(
                    f"Error: CRFH with maxBytes=0 had {actual_total_rollovers} rollovers. Expected 0."
                )
                return 1
        elif (
            current_backup_count == 0 and current_max_bytes > 0
        ):  # Size rotation, but no backups kept
            expect_all_lines = False
            print(
                "Info: CRFH with backupCount=0 and maxBytes > 0. Log file is truncated. Not all lines validated."
            )
            if len(all_log_files) != 1:  # Only the main log file should exist
                print(
                    f"Error: Expected 1 log file for CRFH with backupCount=0, found {len(all_log_files)}. Files: {all_log_files}"
                )
                return 1
        elif current_backup_count > 0:  # Standard rotation with backups
            # Heuristic: if backupCount is significantly lower than the default "safe" value, assume messages might be lost
            # The default_log_opts()["backupCount"] is 2000.
            if (
                current_backup_count
                < TestOptions.default_log_opts()["backupCount"] / 20
            ):  # e.g., < 100
                expect_all_lines = False
                print(
                    f"Info: CRFH with low backupCount={current_backup_count}. Some messages might be lost. Not all lines validated."
                )
            if len(all_log_files) > current_backup_count + 1:
                print(
                    f"Error: Found {len(all_log_files)} files, but backupCount={current_backup_count} means at most {current_backup_count + 1} files expected."
                )
                return 1
    elif current_backup_count == 0:
        expect_all_lines = False  # Old timed files are deleted
        print(
            "Info: CTRFH with backupCount=0. Old rotated files are deleted. Not all lines validated."
        )
        # Max number of files could be small (e.g., 1 or 2: current + just rotated one)
        if len(all_log_files) > 2 and actual_total_rollovers > 1:  # noqa: PLR2004
            print(
                f"Warning: CTRFH with backupCount=0 has {len(all_log_files)} files. Expected few. Files: {all_log_files}"
            )
    elif current_backup_count > 0:
        if (
            current_backup_count
            < TestOptions.default_timed_log_opts()["backupCount"] / 20
        ):
            expect_all_lines = False
            print(
                f"Info: CTRFH with low backupCount={current_backup_count}. Some messages might be lost. Not all lines validated."
            )
        if len(all_log_files) > current_backup_count + 1:  # +1 for the active log file
            print(
                f"Error: Too many log files ({len(all_log_files)}) for CTRFH with backupCount={current_backup_count}. Expected <= {current_backup_count + 1}."
            )
            return 1

    # Rollover count check
    # Must be careful if min_rollovers is 0 for cases where no rotation is expected.
    if test_opts.min_rollovers == 0:
        # CRFH, no size rotation
        if (
            not test_opts.use_timed
            and current_max_bytes <= 0
            and actual_total_rollovers != 0
        ):
            print(
                f"Error: Expected 0 rollovers for CRFH with maxBytes=0, but got {actual_total_rollovers}."
            )
            return 1
        # For other min_rollovers=0 cases, it just means we don't have a strict minimum.
    elif actual_total_rollovers < test_opts.min_rollovers:
        print(
            f"Error: {actual_total_rollovers} rollovers occurred but expected at least {test_opts.min_rollovers}."
        )
        return 1

    if test_opts.induce_failure:
        print(
            "Note: Failure was induced. Validating log file is expected to show errors or pass by chance."
        )
        # Expect validate_log_file to return False (errors found)
        validation_passed_despite_failure = validate_log_file(
            test_opts, end_time - start_time, expect_all=False
        )
        if validation_passed_despite_failure:
            print(
                "Stress test with induced failure surprisingly passed validation (found no errors)."
            )
            return 0  # Pytest will assert this against expected 1.
        print(
            "Stress test with induced failure correctly failed validation (found errors)."
        )
        return 1  # This is the expected outcome for induce_failure=True

    # Final validation
    if validate_log_file(test_opts, end_time - start_time, expect_all=expect_all_lines):
        print("Stress test passed.")
        return 0

    print("Stress test failed.")
    return 1


def delete_log_files(test_opts: TestOptions):
    # Main log directory files pattern
    log_path_pattern = os.path.join(test_opts.log_dir, f"{test_opts.log_file}*")
    files_to_remove = set(glob.glob(log_path_pattern))  # Use set for unique paths

    removed_files_count = 0

    # Determine lock file path
    # Full path to the base log file is needed for baseLockFilename if it's to derive the path part.
    full_base_log_path = os.path.abspath(
        os.path.join(test_opts.log_dir, test_opts.log_file)
    )

    lock_file_dir_from_opts = test_opts.log_opts.get("lock_file_directory")
    final_lock_file_path = ""

    if lock_file_dir_from_opts:
        # Ensure lock_file_dir_from_opts is absolute or resolve it correctly.
        # For tests, it's often relative to test_opts.log_dir or cwd.
        abs_lock_file_dir = os.path.abspath(lock_file_dir_from_opts)

        # baseLockFilename returns (path_of_log, .__lockname)
        # We only need the .__lockname part to join with our custom directory.
        _discarded_log_path_part, lock_name_component = (
            ConcurrentRotatingFileHandler.baseLockFilename(full_base_log_path)
        )  # Pass full path to get correct name part
        final_lock_file_path = os.path.join(abs_lock_file_dir, lock_name_component)
    else:
        # Default: lock file is co-located or structure derived by baseLockFilename
        lock_dir_part, lock_name_part = ConcurrentRotatingFileHandler.baseLockFilename(
            full_base_log_path
        )
        final_lock_file_path = os.path.join(lock_dir_part, lock_name_part)

    files_to_remove.add(final_lock_file_path)

    for file_to_remove_path in files_to_remove:
        try:
            # Make sure it's a file
            if os.path.exists(file_to_remove_path) and os.path.isfile(
                file_to_remove_path
            ):
                os.remove(file_to_remove_path)
                removed_files_count += 1
            # else: It's a directory or something else, don't try to os.remove()
        except OSError as e:
            print(f"Error deleting file {file_to_remove_path}: {e}")

    if removed_files_count > 0:
        print(f"Attempted to delete {removed_files_count} existing log/lock files.")

    # Clean up custom lock directory if it was specified, different from log_dir, and is now empty
    if lock_file_dir_from_opts:
        abs_lock_file_dir = os.path.abspath(lock_file_dir_from_opts)
        abs_log_dir = os.path.abspath(test_opts.log_dir)
        if os.path.exists(abs_lock_file_dir) and abs_lock_file_dir != abs_log_dir:
            try:
                if not os.listdir(abs_lock_file_dir):  # Check if empty
                    os.rmdir(abs_lock_file_dir)
                    print(f"Removed empty custom lock directory: {abs_lock_file_dir}")
            except OSError as e:
                # This can fail if hidden files like .DS_Store exist, or due to permissions
                print(
                    f"Warning: Could not remove custom lock directory {abs_lock_file_dir} (it might not be empty): {e}"
                )


def main():
    """Command line driver for ConcurrentRotatingFileHandler stress test."""
    parser = argparse.ArgumentParser(
        description=f"Concurrent Log Handler {__version__} stress test."
    )

    default_log_opts = TestOptions.default_log_opts()
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=default_log_opts["maxBytes"],
        help=f"Maximum log file size in bytes before rotation "
        f"(default: {default_log_opts['maxBytes']}). (Ignored if use-timed)",
    )
    use_timed_default = TestOptions.__annotations__["use_timed"].default
    parser.add_argument(
        "--use-timed",
        type=int,
        default=use_timed_default,
        help=f"Test the timed-based rotation class. "
        f"(default: {use_timed_default}).",
    )
    log_calls_default = TestOptions.__annotations__["log_calls"].default
    parser.add_argument(
        "--log-calls",
        type=int,
        default=log_calls_default,
        help=f"Number of log messages per process (default: {log_calls_default})",
    )
    num_processes_default = TestOptions.__annotations__["num_processes"].default
    parser.add_argument(
        "--num-processes",
        type=int,
        default=num_processes_default,
        help=f"Number of worker processes (default: {num_processes_default})",
    )
    sleep_min_default = TestOptions.__annotations__["sleep_min"].default
    parser.add_argument(
        "--sleep-min",
        type=float,
        default=sleep_min_default,
        help=f"Minimum random sleep time in seconds (default: {sleep_min_default})",
    )
    sleep_max_default = TestOptions.__annotations__["sleep_max"].default
    parser.add_argument(
        "--sleep-max",
        type=float,
        default=sleep_max_default,
        help=f"Maximum random sleep time in seconds (default: {sleep_max_default})",
    )
    parser.add_argument(
        "--asyncio",
        action="store_true",
        help="Use asyncio queue feature of Concurrent Log Handler. (TODO: Not implemented yet)",
    )

    # If this number is too low compared to the number of log calls, the log file will be deleted
    # before the test is complete and fail.
    parser.add_argument(
        "--max-rotations",
        type=int,
        default=default_log_opts["maxRotations"],
        help=f"Maximum number of rotations before deleting oldest log file "
        f"(default: {default_log_opts['maxRotations']})",
    )
    parser.add_argument(
        "--encoding",
        type=str,
        default=default_log_opts["encoding"],
        help=f"Encoding for log file (default: {default_log_opts['encoding']})",
    )
    parser.add_argument(
        "--debug",
        type=bool,
        default=default_log_opts["debug"],
        help=f"Enable debug flag on CLH (default: {default_log_opts['debug']})",
    )
    parser.add_argument(
        "--gzip",
        type=bool,
        default=default_log_opts["gzip"],
        help=f"Enable gzip compression on CLH (default: {default_log_opts['gzip']})",
    )

    filename_default = TestOptions.__annotations__["log_file"].default
    parser.add_argument(
        "--filename",
        type=str,
        default=filename_default,
        help=f"Filename for log file (default: {filename_default})",
    )

    log_dir_default = TestOptions.__annotations__["log_dir"].default
    parser.add_argument(
        "--log-dir",
        type=str,
        default=log_dir_default,
        help=f"Directory for log file output (default: {log_dir_default})",
    )

    induce_failure_default = TestOptions.__annotations__["induce_failure"].default
    parser.add_argument(
        "--fail",
        type=bool,
        default=induce_failure_default,
        help=f"Induce random failures in logging to cause a test failure. "
        f"(default: {induce_failure_default})",
    )

    parser.add_argument(
        "--when",
        type=str,
        default="s",
        help="Time interval for timed rotation (default: 's')",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=10,
        help="Interval for timed rotation (default: 10)",
    )

    args = parser.parse_args()
    log_opts = {
        "maxBytes": args.max_bytes,
        "backupCount": args.max_rotations,
        "encoding": args.encoding,
        "debug": args.debug,
        "use_gzip": args.gzip,
    }
    if args.use_timed:
        log_opts = {
            "backupCount": args.max_rotations,
            "encoding": args.encoding,
            "debug": args.debug,
            "use_gzip": args.gzip,
            "when": args.when,
            "interval": args.interval,
        }

    test_opts = TestOptions(
        log_file=args.filename,
        num_processes=args.num_processes,
        log_calls=args.log_calls,
        sleep_min=args.sleep_min,
        sleep_max=args.sleep_max,
        use_asyncio=args.asyncio,
        induce_failure=args.fail,
        log_opts=log_opts,
    )

    # Implement the stress test using asyncio queue feature
    # TODO: future time-based rotation options
    if test_opts.use_asyncio:
        return 2  # Not implemented yet
    return run_stress_test(test_opts)


# CLI main function (not a primary focus for this update, but should be aware of new TestOptions fields)
def main_tmp():
    # ... (argparse setup would need to include --keep-file-open if desired for CLI) ...
    # This part is complex to update fully here. Assuming pytest is the main driver.
    parser = argparse.ArgumentParser(
        description=f"Concurrent Log Handler {__version__} stress test."
    )
    # ... (existing args) ...
    parser.add_argument(
        "--keep-file-open",
        type=str,  # Read as string 'true'/'false' then convert, or use action
        default=None,  # Represents "handler default"
        help="Override keep_file_open: 'true', 'false', or leave unset for handler default.",
    )
    # args = parser.parse_args()

    # kfo_override = None
    # if args.keep_file_open is not None:
    #     if args.keep_file_open.lower() == "true":
    #         kfo_override = True
    #     elif args.keep_file_open.lower() == "false":
    #         kfo_override = False

    # Construct log_opts and TestOptions, including kfo_override
    # ...
    # test_opts = TestOptions(..., keep_file_open_override=kfo_override, ...)
    # return run_stress_test(test_opts)
    print(
        "CLI runner for stresstest.py needs further updates for new options. Please use pytest."
    )
    return 2


def main_tmp_2():
    """Command line driver for ConcurrentRotatingFileHandler stress test."""
    parser = argparse.ArgumentParser(
        description=f"Concurrent Log Handler {__version__} stress test."
    )
    # Get defaults from TestOptions to keep them in sync
    default_opts_instance = TestOptions()
    default_log_opts_instance = TestOptions.default_log_opts()
    default_timed_log_opts_instance = TestOptions.default_timed_log_opts()

    parser.add_argument("--log-file", default=default_opts_instance.log_file)
    parser.add_argument("--log-dir", default=default_opts_instance.log_dir)
    parser.add_argument(
        "--num-processes", type=int, default=default_opts_instance.num_processes
    )
    parser.add_argument(
        "--log-calls", type=int, default=default_opts_instance.log_calls
    )
    parser.add_argument(
        "--sleep-min", type=float, default=default_opts_instance.sleep_min
    )
    parser.add_argument(
        "--sleep-max", type=float, default=default_opts_instance.sleep_max
    )
    parser.add_argument(
        "--min-rollovers", type=int, default=default_opts_instance.min_rollovers
    )

    parser.add_argument(
        "--use-timed", action="store_true", default=default_opts_instance.use_timed
    )
    parser.add_argument(
        "--induce-failure",
        action="store_true",
        default=default_opts_instance.induce_failure,
    )

    # Log opts
    parser.add_argument(
        "--max-bytes",
        type=int,
        help=f"Default for size: {default_log_opts_instance['maxBytes']}, for timed: {default_timed_log_opts_instance['maxBytes']}",
    )
    parser.add_argument(
        "--backup-count",
        type=int,
        help=f"Default: {default_log_opts_instance['backupCount']}",
    )
    parser.add_argument("--encoding", default=default_log_opts_instance["encoding"])
    # Renamed for clarity
    parser.add_argument(
        "--debug-handler",
        action="store_true",
        default=default_log_opts_instance["debug"],
    )
    parser.add_argument(
        "--use-gzip", action="store_true", default=default_log_opts_instance["use_gzip"]
    )
    parser.add_argument(
        "--keep-file-open",
        choices=["true", "false", "default"],
        default="default",
        help="Override handler's keep_file_open: 'true', 'false', or 'default'.",
    )
    parser.add_argument(
        "--lock-file-directory",
        type=str,
        default=None,
        help="Custom directory for lock files.",
    )

    # Timed specific log opts
    parser.add_argument(
        "--when",
        default=default_timed_log_opts_instance["when"],
        help="For timed rotation (e.g., 'S', 'M', 'H', 'D')",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=default_timed_log_opts_instance["interval"],
        help="For timed rotation.",
    )

    # asyncio not handled by CLI yet
    # parser.add_argument("--asyncio", action="store_true", default=default_opts_instance.use_asyncio)

    args = parser.parse_args()

    kfo_override: Optional[bool] = None
    if args.keep_file_open == "true":
        kfo_override = True
    elif args.keep_file_open == "false":
        kfo_override = False

    # Determine base log_opts and then override
    if args.use_timed:
        current_log_opts = TestOptions.default_timed_log_opts()
        if args.max_bytes is not None:
            current_log_opts["maxBytes"] = (
                args.max_bytes
            )  # User can override timed default of 0
        current_log_opts["when"] = args.when
        current_log_opts["interval"] = args.interval
    else:  # Size-based
        current_log_opts = TestOptions.default_log_opts()
        if args.max_bytes is not None:
            current_log_opts["maxBytes"] = args.max_bytes

    # Common log_opts overrides
    if args.backup_count is not None:
        current_log_opts["backupCount"] = args.backup_count
    current_log_opts["encoding"] = args.encoding
    current_log_opts["debug"] = args.debug_handler
    current_log_opts["use_gzip"] = args.use_gzip
    if args.lock_file_directory:
        current_log_opts["lock_file_directory"] = args.lock_file_directory

    # Note: keep_file_open is handled by TestOptions.keep_file_open_override, not directly in log_opts dict

    cli_test_opts = TestOptions(
        log_file=args.log_file,
        log_dir=args.log_dir,
        num_processes=args.num_processes,
        log_calls=args.log_calls,
        use_asyncio=False,  # args.asyncio, # Not implemented
        induce_failure=args.induce_failure,
        sleep_min=args.sleep_min,
        sleep_max=args.sleep_max,
        use_timed=args.use_timed,
        min_rollovers=args.min_rollovers,
        keep_file_open_override=kfo_override,
        log_opts=current_log_opts,
    )

    print(f"Running CLI stress test with resolved options: {cli_test_opts}")
    if cli_test_opts.use_asyncio:
        print("Asyncio mode not implemented for CLI runner yet.")
        return 2

    return run_stress_test(cli_test_opts)


if __name__ == "__main__":
    # Note: Running stresstest.py directly is not fully configured for all new TestOptions.
    # It's recommended to use test_stresstest.py with pytest.
    raise SystemExit(main())
