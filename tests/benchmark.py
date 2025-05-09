#!/usr/bin/env python
import logging
import os
import platform
import shutil
import time

# Ensure concurrent_log_handler is installed or in your PYTHONPATH.
# If it's in a relative path (e.g., for development):
# import sys
# sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))
from concurrent_log_handler import ConcurrentRotatingFileHandler

# --- Configuration ---
# Directory for logs. This will be cleaned and recreated for each full script run.
LOG_FILE_DIR = "benchmark_clh_logs_favorable"
BASE_LOG_FILENAME = os.path.join(LOG_FILE_DIR, "benchmark_favorable.log")
# Number of log messages to write for each test run
NUM_MESSAGES = 150_000  # Increased for clearer difference
# Set maxBytes very high to effectively prevent rollover during the test
MAX_BYTES = 1 * 1024 * 1024 * 1024  # 1 GB
BACKUP_COUNT = 0  # No backups needed for this performance test
# Varied log messages to simulate different log content
MESSAGES_TO_LOG = [
    "This is a short log message.",
    "This is a slightly longer log message for variety, ensuring some I/O.",
    f"Debug information: user_id={os.urandom(4).hex()}, action='process_item', status='pending', item_count=42",
    "An error occurred: KeyError, 'config_option_xyz' not found in settings.",
    "Performance metric: database_query_time=0.008s, items_returned=150, cache_status='MISS'",
    "Verbose log entry detailing internal application state for diagnostic purposes: "
    + "Y" * 300,
    f"System Stats: CPU_Load={os.urandom(1).hex()}, Memory_Usage={os.urandom(2).hex()}MB",
]
# --- End Configuration ---


def get_lock_file_path_for_cleanup(base_log_filename_full_path):
    """
    Predicts the lock file path based on the handler's internal logic
    for default lock file placement (when handler's lock_file_directory is None).
    """
    lock_path_info = ConcurrentRotatingFileHandler.baseLockFilename(
        base_log_filename_full_path
    )
    # lock_path_info is a tuple: (directory_of_lock_file, lock_file_name_with_prefix)
    return os.path.join(lock_path_info[0], lock_path_info[1])


def cleanup_log_artifacts(log_filepath):
    """Removes the specified log file and its predicted lock file."""
    if os.path.exists(log_filepath):
        try:
            os.remove(log_filepath)
        except OSError as e:
            print(f"Warning: Could not remove log file {log_filepath}: {e}")

    predicted_lock_file = get_lock_file_path_for_cleanup(log_filepath)
    if os.path.exists(predicted_lock_file):
        try:
            os.remove(predicted_lock_file)
        except OSError as e:
            print(f"Warning: Could not remove lock file {predicted_lock_file}: {e}")


def setup_logger_for_benchmark(log_filepath, keep_file_open_config):
    """Configures and returns a logger instance for a benchmark case."""
    logger_name = f"benchmark_logger_fav_{str(keep_file_open_config).lower()}"

    # Ensure logger is clean by removing handlers from previous runs if any
    if logger_name in logging.Logger.manager.loggerDict:
        existing_logger = logging.getLogger(logger_name)
        existing_logger.handlers = []

    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)  # Process .info and higher messages

    # For performance measurement, the handler's internal debug logging must be OFF.
    handler_internal_debug = False

    handler = ConcurrentRotatingFileHandler(
        filename=log_filepath,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
        keep_file_open=keep_file_open_config,
        debug=handler_internal_debug,
    )

    # Use a minimal formatter to reduce formatting overhead during the benchmark.
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger, handler


def perform_logging_run(logger_instance, num_logs, log_messages_list):
    """Logs a specified number of messages and returns the time taken."""
    start_time = time.perf_counter()
    for i in range(num_logs):
        logger_instance.info(log_messages_list[i % len(log_messages_list)])
    return time.perf_counter() - start_time


def run_single_benchmark_case(
    log_filepath, keep_file_open_setting, num_messages_to_log, messages_sample
):
    """Manages setup, execution, and cleanup for one benchmark configuration."""
    print(f"Running benchmark: keep_file_open = {keep_file_open_setting}")

    # Ensure no artifacts from a previous iteration within the same script run.
    cleanup_log_artifacts(log_filepath)

    logger, handler = setup_logger_for_benchmark(log_filepath, keep_file_open_setting)

    time_taken = perform_logging_run(logger, num_messages_to_log, messages_sample)

    handler.close()  # Essential for releasing file handles and locks.
    logger.removeHandler(handler)  # Clean up the logger.

    print(
        f"Time taken (keep_file_open={keep_file_open_setting}): {time_taken:.4f} seconds"
    )

    # You might want to verify log file existence/size after the run for sanity check.
    # if os.path.exists(log_filepath):
    #     print(f"Log file '{os.path.basename(log_filepath)}' size: {os.path.getsize(log_filepath) / (1024*1024):.2f} MB")

    # Cleanup artifacts of this specific run.
    cleanup_log_artifacts(log_filepath)

    return time_taken


def main_benchmark():
    print("Starting ConcurrentRotatingFileHandler 'Favorable Case' Benchmark")
    print(
        "  Goal: Highlight performance of 'keep_file_open' in a single-threaded, high-volume scenario without rollovers."
    )
    print(f"  Python version: {platform.python_version()}")
    print(f"  OS: {platform.system()} {platform.release()} ({os.name})")
    print(
        f"  Logging {NUM_MESSAGES} messages for each case to '{BASE_LOG_FILENAME}'.\n"
    )

    # Clean and prepare the log directory for the benchmark runs.
    if os.path.exists(LOG_FILE_DIR):
        shutil.rmtree(LOG_FILE_DIR)
    os.makedirs(LOG_FILE_DIR, exist_ok=True)

    # --- Benchmark with keep_file_open = True ---
    time_true = run_single_benchmark_case(
        BASE_LOG_FILENAME, True, NUM_MESSAGES, MESSAGES_TO_LOG
    )

    print("-" * 50)

    # --- Benchmark with keep_file_open = False ---
    time_false = run_single_benchmark_case(
        BASE_LOG_FILENAME, False, NUM_MESSAGES, MESSAGES_TO_LOG
    )

    print("-" * 50)
    print("\nBenchmark Summary ('Favorable Case'):")
    print(f"  Time with keep_file_open=True:  {time_true:.4f} seconds")
    print(f"  Time with keep_file_open=False: {time_false:.4f} seconds")

    float_tolerance = 1e-4

    if time_true == 0 or time_false == 0:
        print(
            "  Could not reliably calculate performance difference (one run was zero or failed)."
        )
    elif (
        abs(time_true - time_false) < float_tolerance
    ):  # Check if times are virtually identical
        print("  Both configurations performed almost identically.")
    elif time_true < time_false:
        percentage_improvement = ((time_false - time_true) / time_false) * 100
        print(f"  keep_file_open=True was {percentage_improvement:.2f}% faster.")
    else:  # time_false < time_true
        percentage_slower = ((time_true - time_false) / time_true) * 100
        print(
            f"  keep_file_open=True was {percentage_slower:.2f}% slower (i.e., keep_file_open=False was faster)."
        )
        if os.name == "nt":
            print(
                "    Note: On Windows, 'keep_file_open=True' for the main log stream is internally overridden to 'False'"
            )
            print(
                "    to allow file rotation. Any observed difference primarily reflects lock file handling and other minor overheads."
            )

    print(
        f"\nBenchmark finished. The log directory '{LOG_FILE_DIR}' was cleaned after each test case."
    )
    print(
        "If you wish to inspect logs, remove cleanup_log_artifacts() calls or the initial rmtree."
    )


if __name__ == "__main__":
    main_benchmark()
