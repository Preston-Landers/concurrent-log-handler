#!/usr/bin/env python

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
import string
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from concurrent_log_handler import ConcurrentRotatingFileHandler
from concurrent_log_handler.__version__ import __version__


@dataclass(frozen=True)
class TestOptions:
    """Options to configure the stress test."""

    __test__ = False  # not a test case itself.

    # kwargs to pass to ConcurrentRotatingFileHandler
    log_opts: dict = field(default_factory=lambda: TestOptions.default_log_opts())

    log_file: str = field(default="stress_test.log")
    log_dir: str = field(default="output_tests")
    num_processes: int = field(default=10)
    log_calls: int = field(default=1_000)

    use_asyncio: bool = field(default=False)
    induce_failure: bool = field(default=False)
    sleep_min: float = field(default=0.0001)
    sleep_max: float = field(default=0.01)

    @classmethod
    def default_log_opts(cls, override_values: Optional[Dict] = None) -> dict:
        rv = {
            "maxBytes": 1024 * 10,
            "backupCount": 1000,
            "encoding": "utf-8",
            "debug": False,
            "use_gzip": False,
        }
        if override_values:
            rv.update(override_values)
        return rv


class ConcurrentLogHandlerBuggy(ConcurrentRotatingFileHandler):
    def emit(self, record):
        # Introduce a random chance (e.g., 5%) to skip or duplicate a log message
        random_choice = random.randint(1, 100)

        if 1 <= random_choice <= 5:  # 5% chance to skip a log message
            return
        elif 6 <= random_choice <= 10:  # 5% chance to duplicate a log message
            super().emit(record)
            super().emit(record)
        else:
            super().emit(record)


def worker_process(test_opts: TestOptions, process_id: int):
    logger = logging.getLogger(f"Process-{process_id}")
    logger.setLevel(logging.DEBUG)
    log_opts = test_opts.log_opts

    file_handler_class = (
        ConcurrentLogHandlerBuggy
        if test_opts.induce_failure
        else ConcurrentRotatingFileHandler
    )
    log_path = os.path.join(test_opts.log_dir, test_opts.log_file)
    file_handler = file_handler_class(log_path, mode="a", **log_opts)

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(message)s")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    char_choices = string.ascii_letters
    if log_opts["encoding"] == "utf-8":
        char_choices = (
            string.ascii_letters
            + " \U0001d122\U00024b00\u20a0ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞßàáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ"
        )

    for i in range(test_opts.log_calls):
        random_str = "".join(random.choices(char_choices, k=20))
        logger.debug(f"{process_id}-{i}-{random_str}")
        time.sleep(random.uniform(test_opts.sleep_min, test_opts.sleep_max))


def validate_log_file(test_opts: TestOptions) -> bool:
    process_tracker = {i: set() for i in range(test_opts.num_processes)}

    # Sort log files, starting with the most recent backup
    log_path = os.path.join(test_opts.log_dir, test_opts.log_file)
    all_log_files = sorted(glob.glob(f"{log_path}*"), reverse=True)

    encoding = test_opts.log_opts["encoding"] or "utf-8"

    for current_log_file in all_log_files:
        opener = open
        if current_log_file.endswith(".gz"):
            opener = gzip.open
        with opener(current_log_file, "rb") as file:
            for line in file:
                line = line.decode(encoding)
                parts = line.strip().split(" - ")
                message = parts[-1]
                process_id, message_id, _ = message.split("-")
                process_id = int(process_id)
                message_id = int(message_id)

                if message_id in process_tracker[process_id]:
                    print(
                        f"Error: Duplicate message from Process-{process_id}: {message_id}"
                    )
                    return False

                process_tracker[process_id].add(message_id)

    log_calls = test_opts.log_calls
    for process_id, message_ids in process_tracker.items():
        if len(message_ids) != log_calls:
            print(
                f"Error: Missing messages from Process-{process_id}: "
                f"len(message_ids) {len(message_ids)} != log_calls {log_calls}"
            )
            return False

    return True


def run_stress_test(test_opts: TestOptions) -> int:
    delete_log_files(test_opts)

    # Create the log directory if it doesn't exist
    if not os.path.exists(test_opts.log_dir):
        os.makedirs(test_opts.log_dir)

    processes = []

    for i in range(test_opts.num_processes):
        p = multiprocessing.Process(target=worker_process, args=(test_opts, i))
        p.start()
        processes.append(p)

    for p in processes:
        p.join()

    if validate_log_file(test_opts):
        print("Stress test passed.")
        return 0
    else:
        print("Stress test failed.")
        return 1


def delete_log_files(test_opts: TestOptions):
    log_path = os.path.join(test_opts.log_dir, test_opts.log_file)
    log_files_to_remove = glob.glob(f"{log_path}*")
    removed_files = []
    for file in log_files_to_remove:
        try:
            os.remove(file)
            removed_files.append(file)
        except OSError as e:
            print(f"Error deleting log file {file}: {e}")
    if removed_files:
        print(f"Deleted {len(removed_files)} existing log files.")


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
        f"(default: {default_log_opts['maxBytes']})",
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

    args = parser.parse_args()
    test_opts = TestOptions(
        log_file=args.filename,
        num_processes=args.num_processes,
        log_calls=args.log_calls,
        sleep_min=args.sleep_min,
        sleep_max=args.sleep_max,
        use_asyncio=args.asyncio,
        induce_failure=args.fail,
        log_opts={
            "maxBytes": args.max_bytes,
            "backupCount": args.max_rotations,
            "encoding": args.encoding,
            "debug": args.debug,
            "use_gzip": args.gzip,
        },
    )

    # Implement the stress test using asyncio queue feature
    # TODO: future time-based rotation options
    if test_opts.use_asyncio:
        return 2  # Not implemented yet
    return run_stress_test(test_opts)


if __name__ == "__main__":
    raise SystemExit(main())
