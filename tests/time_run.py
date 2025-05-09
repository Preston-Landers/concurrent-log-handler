#!/usr/bin/env python3

"""
Helps measure the performance of pytest runs, especially on Windows

Run with:
python tests/time_pytest.py PYTEST_ARGS
"""

import contextlib
import subprocess
import sys
import time
from collections import defaultdict

import psutil


def run_and_measure():  # noqa: C901, PLR0915
    start_time = time.time()
    pytest_cmd = [sys.executable, "-m", "pytest"] + sys.argv[1:]
    process = subprocess.Popen(pytest_cmd)  # noqa: S603

    # Track our main process
    main_pid = process.pid

    # Dictionary to store processes and their initial CPU times
    active_processes = {}  # pid -> (process_obj, start_cpu_times)
    completed_processes = defaultdict(lambda: (0, 0))  # pid -> (user_time, system_time)

    try:
        # Initial setup with main process
        main_process = psutil.Process(main_pid)
        active_processes[main_pid] = (main_process, main_process.cpu_times())

        # Monitor for processes until main process exits
        while process.poll() is None:
            # Track all existing children
            try:
                all_children = []
                for p_obj, _ in list(active_processes.values()):
                    with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
                        all_children.extend(p_obj.children(recursive=True))

                # Check for new processes
                for child in all_children:
                    if (
                        child.pid not in active_processes
                        and child.pid not in completed_processes
                    ):
                        with contextlib.suppress(
                            psutil.NoSuchProcess, psutil.AccessDenied
                        ):
                            active_processes[child.pid] = (child, child.cpu_times())

                # Check for completed processes
                for pid in list(active_processes.keys()):
                    p_obj, start_times = active_processes[pid]
                    try:
                        # Check if process is still running
                        if not p_obj.is_running():
                            # Process has completed, get final CPU times
                            try:
                                end_times = p_obj.cpu_times()
                                user_time = end_times.user - start_times.user
                                system_time = end_times.system - start_times.system
                                completed_processes[pid] = (user_time, system_time)
                            except (psutil.NoSuchProcess, psutil.AccessDenied):
                                # If we can't get final times, use last known values
                                completed_processes[pid] = (
                                    start_times.user,
                                    start_times.system,
                                )
                            del active_processes[pid]
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        # Process has disappeared without us being able to check is_running()
                        completed_processes[pid] = (
                            start_times.user,
                            start_times.system,
                        )
                        del active_processes[pid]
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

            # Sleep a bit to avoid excessive CPU usage
            time.sleep(0.05)  # More frequent checking (50ms)

        # Process any remaining active processes
        for pid, (p_obj, start_times) in list(active_processes.items()):
            try:
                end_times = p_obj.cpu_times()
                user_time = end_times.user - start_times.user
                system_time = end_times.system - start_times.system
                completed_processes[pid] = (user_time, system_time)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                completed_processes[pid] = (start_times.user, start_times.system)

    except Exception as e:
        print(f"Error during monitoring: {e}")

    end_time = time.time()

    # Calculate totals
    total_user_time = sum(ut for ut, _ in completed_processes.values())
    total_system_time = sum(st for _, st in completed_processes.values())

    print("\n--- Performance Metrics ---")
    print(f"Total wall time: {end_time - start_time:.2f} seconds")
    print(f"Total user CPU time: {total_user_time:.2f} seconds")
    print(f"Total system CPU time: {total_system_time:.2f} seconds")
    print(f"Combined CPU time: {total_user_time + total_system_time:.2f} seconds")
    print(f"Process count: {len(completed_processes)}")

    return process.returncode


if __name__ == "__main__":
    sys.exit(run_and_measure())
