#!/usr/bin/env python
# ruff: noqa: S101, PT006

"""
Pytest based unit test cases to drive stresstest.py.
"""

import os

import pytest
from stresstest import TestOptions, run_stress_test

# --- Base Test Configurations (will be varied with keep_file_open) ---
# Note: min_rollovers will likely need tuning, especially with KFO variations.
# Values are initial estimates.
BASE_TEST_CONFIGS = {
    "default": TestOptions(min_rollovers=70),  # Default KFO is handler's default (True)
    "backupCount_3": TestOptions(
        log_opts=TestOptions.default_log_opts({"backupCount": 3}),
        min_rollovers=50,  # Reduced due to frequent deletion
    ),
    "backupCount_3_gzip": TestOptions(
        log_opts=TestOptions.default_log_opts({"backupCount": 3, "use_gzip": True}),
        min_rollovers=50,
    ),
    "low_proc_high_calls": TestOptions(
        num_processes=2, log_calls=6_000, min_rollovers=80
    ),
    "high_proc_low_calls": TestOptions(
        num_processes=12, log_calls=600, min_rollovers=45
    ),
    "high_stress_small_maxbytes": TestOptions(
        num_processes=15,
        log_calls=1_500,
        min_rollovers=300,  # Adjusted
        log_opts=TestOptions.default_log_opts({"maxBytes": 1024 * 5}),
    ),
    "gzip_enabled": TestOptions(
        log_opts=TestOptions.default_log_opts({"use_gzip": True}), min_rollovers=70
    ),
    "debug_enabled_low_stress": TestOptions(
        num_processes=3,
        log_calls=500,
        min_rollovers=8,
        log_opts=TestOptions.default_log_opts({"debug": True}),
    ),
    # Timed rotation tests
    "timed_basic_debug": TestOptions(
        use_timed=True,
        log_calls=5_000,
        min_rollovers=5,
        log_opts=TestOptions.default_timed_log_opts({"interval": 3, "debug": True}),
    ),
    "timed_backupCount_3_gzip": TestOptions(
        use_timed=True,
        num_processes=4,
        log_calls=3_000,
        min_rollovers=4,
        log_opts=TestOptions.default_timed_log_opts(
            {"backupCount": 3, "interval": 3, "use_gzip": True}
        ),
    ),
    "timed_issue68_check": TestOptions(  # maxBytes with timed
        use_timed=True,
        log_calls=2_000,
        min_rollovers=80,  # Increased rollovers
        num_processes=3,
        log_opts=TestOptions.default_timed_log_opts(
            {"maxBytes": 512, "interval": 3, "use_gzip": True}
        ),
    ),
    "timed_backup_maxbytes_gzip_debug": TestOptions(
        use_timed=True,
        log_calls=1_000,
        min_rollovers=80,  # Increased rollovers
        log_opts=TestOptions.default_timed_log_opts(
            {
                "maxBytes": 1024,
                "backupCount": 5,
                "interval": 5,
                "use_gzip": True,
                "debug": True,
            }
        ),
    ),
    # --- New test cases from priorities ---
    "CRFH_backupCount_0_size_rot": TestOptions(  # Idea 2
        log_opts=TestOptions.default_log_opts({"maxBytes": 1024 * 2, "backupCount": 0}),
        log_calls=2000,
        num_processes=3,
        min_rollovers=30,  # Expect rollovers, but content is truncated
    ),
    "CTRFH_backupCount_0_timed_rot": TestOptions(  # Idea 2
        use_timed=True,
        log_opts=TestOptions.default_timed_log_opts(
            # Rotate every 1s
            {"backupCount": 0, "interval": 1}
        ),
        log_calls=1000,
        num_processes=2,
        min_rollovers=3,  # Enough for a few timed rollovers
    ),
    "CRFH_tiny_maxBytes_gzip": TestOptions(  # Idea 3
        log_opts=TestOptions.default_log_opts(
            {"maxBytes": 200, "backupCount": 30, "use_gzip": True}
        ),  # backupCount reduced
        num_processes=3,
        log_calls=300,  # Reduced calls as many files generated
        min_rollovers=80,  # Expect many rollovers for small files
    ),
    "CRFH_custom_lock_dir": TestOptions(  # Idea 4
        log_dir="output_tests_main_logs",  # Main log dir
        log_opts=TestOptions.default_log_opts(
            # Path relative to where pytest is run, or use absolute path
            {
                "lock_file_directory": os.path.join(
                    "output_tests_main_logs", "custom_locks_here"
                )
            }
        ),
        min_rollovers=70,
    ),
    "CRFH_zero_maxBytes_no_rot": TestOptions(  # Idea 6
        log_opts=TestOptions.default_log_opts({"maxBytes": 0, "backupCount": 5}),
        log_calls=3000,
        num_processes=2,
        min_rollovers=0,  # Expect zero size-based rollovers
    ),
}

FAILURE_TEST_CONFIGS = {
    "default_induce_failure": TestOptions(
        induce_failure=True, min_rollovers=50
    ),  # Adjusted
    "gzip_induce_failure_low_calls": TestOptions(
        induce_failure=True,
        log_calls=500,
        min_rollovers=15,  # Adjusted
        log_opts=TestOptions.default_log_opts({"use_gzip": True}),
    ),
}

# --- Generate final test cases with KFO variations ---
FINAL_TEST_CASES_LIST = []


def _copy_test_options(opts: TestOptions, **overrides) -> TestOptions:
    """Helper to create a new TestOptions instance with some fields overridden."""
    current_values = opts.__dict__.copy()
    current_values.update(overrides)
    return TestOptions(**current_values)


for label, base_opts in BASE_TEST_CONFIGS.items():
    # Variant with keep_file_open_override = True
    FINAL_TEST_CASES_LIST.append(
        pytest.param(
            _copy_test_options(base_opts, keep_file_open_override=True),
            id=f"{label} (KFO=T)",
        )
    )
    # Variant with keep_file_open_override = False
    FINAL_TEST_CASES_LIST.append(
        pytest.param(
            _copy_test_options(base_opts, keep_file_open_override=False),
            id=f"{label} (KFO=F)",
        )
    )
    # Variant with keep_file_open_override = None (handler default)
    # Only add if distinct from True, or if we want to explicitly test "None"
    # Since handler default is True, KFO=T and KFO=None are effectively the same for the handler's behavior.
    # To reduce redundancy, we can skip adding an explicit KFO=None if it matches KFO=T.
    # For full explicitness:
    # FINAL_TEST_CASES_LIST.append(
    #     pytest.param(_copy_test_options(base_opts, keep_file_open_override=None), id=f"{label} (KFO=default)")
    # )


for label, base_opts in FAILURE_TEST_CONFIGS.items():
    # For failure tests, run with default KFO (which is True via handler) and explicit False
    FINAL_TEST_CASES_LIST.append(
        pytest.param(
            _copy_test_options(base_opts, keep_file_open_override=True),
            id=f"{label} (KFO=T)",
        )
    )
    FINAL_TEST_CASES_LIST.append(
        pytest.param(
            _copy_test_options(base_opts, keep_file_open_override=False),
            id=f"{label} (KFO=F)",
        )
    )


@pytest.mark.parametrize("test_opts_param", FINAL_TEST_CASES_LIST)
def test_run_stress_test(
    test_opts_param: TestOptions,
):
    """Run the stress test with the given options and verify the result."""
    # Get id from pytest.param if available
    # label = getattr(test_opts_param, "id", str(test_opts_param))
    # Pytest provides the id directly in the test report, so label here is mostly for debug printing.

    print(f"\nRunning test variant: {test_opts_param}")  # pytest captures this with -s

    # Specific pre-test setup if needed, e.g. ensuring lock_file_directory path
    # This is now handled more robustly inside run_stress_test and delete_log_files.

    expected_return_code = 1 if test_opts_param.induce_failure else 0
    actual_return_code = run_stress_test(test_opts_param)

    assert (
        actual_return_code == expected_return_code
    ), f"Test with options {test_opts_param} failed. Expected return code {expected_return_code}, got {actual_return_code}"
