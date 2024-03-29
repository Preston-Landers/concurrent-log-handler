#!/usr/bin/env python
# ruff: noqa: S101, PT006

"""
Pytest based unit test cases to drive stresstest.py.

See comments about backupCount in stresstest.py. In short,
if backupCount is set here less than 10, we assume that
some logs are deleted before the end of the test and therefore
don't test specifically for missing lines/items.
"""

import pytest
from stresstest import TestOptions, run_stress_test

TEST_CASES = {
    "default test options": TestOptions(),
    "backupCount=3": TestOptions(
        log_opts=TestOptions.default_log_opts({"backupCount": 3}),
    ),
    "backupCount=3, use_gzip=True": TestOptions(
        log_opts=TestOptions.default_log_opts({"backupCount": 3, "use_gzip": True}),
    ),
    "num_processes=2, log_calls=6_000": TestOptions(
        num_processes=2, log_calls=6_000, min_rollovers=80
    ),
    "induce_failure=True": TestOptions(induce_failure=True, min_rollovers=60),
    "num_processes=12, log_calls=600": TestOptions(
        num_processes=12, log_calls=600, min_rollovers=45
    ),
    "num_processes=15, log_calls=1_500, maxBytes=1024 * 5": TestOptions(
        num_processes=15,
        log_calls=1_500,
        min_rollovers=320,
        log_opts=TestOptions.default_log_opts(
            {
                "maxBytes": 1024 * 5,  # rotate more often
            }
        ),
    ),
    "use_gzip=True": TestOptions(
        log_opts=TestOptions.default_log_opts(
            {
                "use_gzip": True,
            }
        )
    ),
    "induce_failure=True, log_calls=500, use_gzip=True": TestOptions(
        induce_failure=True,
        log_calls=500,
        min_rollovers=20,
        log_opts=TestOptions.default_log_opts(
            {
                "use_gzip": True,
            }
        ),
    ),
    "num_processes=3, log_calls=500, debug=True": TestOptions(
        num_processes=3,
        log_calls=500,
        min_rollovers=8,
        log_opts=TestOptions.default_log_opts(
            {
                "debug": True,
            }
        ),
    ),
    "use_timed=True, interval=3, log_calls=5_000, debug=True": TestOptions(
        use_timed=True,
        log_calls=5_000,
        min_rollovers=5,
        log_opts=TestOptions.default_timed_log_opts(
            {
                "interval": 3,
                "debug": True,
            }
        ),
    ),
    "backupCount=3, use_gzip=True, use_timed=True, interval=3, log_calls=3_000, num_processes=4": TestOptions(
        use_timed=True,
        num_processes=4,
        log_calls=3_000,
        min_rollovers=4,
        log_opts=TestOptions.default_timed_log_opts(
            {
                "backupCount": 3,
                "interval": 3,
                "use_gzip": True,
            }
        ),
    ),
    "backupCount=4, use_timed=True, interval=4, log_calls=3_000, num_processes=5": TestOptions(
        use_timed=True,
        num_processes=5,
        log_calls=3_000,
        min_rollovers=3,
        log_opts=TestOptions.default_timed_log_opts(
            {
                "backupCount": 4,
                "interval": 4,
                "use_gzip": False,
            }
        ),
    ),
    # This checks the issue in Issue #68 - in Timed mode, when we have to rollover more
    # often than the interval due to size limits, the naming of the files is incorrect.
    # The check for the incorrect names is done in `run_stress_test()`. The following case can
    # also check it.
    "use_timed=True, maxBytes=512B, interval=3, log_calls=2_000, use_gzip=True, num_processes=3": TestOptions(
        use_timed=True,
        log_calls=2_000,
        min_rollovers=5,
        num_processes=3,
        log_opts=TestOptions.default_timed_log_opts(
            {
                "maxBytes": 512,
                "interval": 3,
                "use_gzip": True,
            }
        ),
    ),
    "backupCount=5, use_timed=True, maxBytes=1KiB, interval=5, log_calls=1_000, use_gzip=True, debug=True": TestOptions(
        use_timed=True,
        log_calls=1_000,
        min_rollovers=5,
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
    "use_timed=True, num_processes=15, interval=1, log_calls=5_000, use_gzip=True": TestOptions(
        use_timed=True,
        log_calls=5_000,
        num_processes=15,
        min_rollovers=20,
        log_opts=TestOptions.default_timed_log_opts(
            {
                "interval": 1,
                "use_gzip": True,
            }
        ),
    ),
    "use_timed=True, maxBytes=100KiB, interval=1, log_calls=6_000, debug=True": TestOptions(
        use_timed=True,
        log_calls=6_000,
        min_rollovers=20,
        log_opts=TestOptions.default_timed_log_opts(
            {
                "maxBytes": 1024 * 100,
                "interval": 1,
                "debug": True,
            }
        ),
    ),
    "use_timed=True, maxBytes=100KiB, interval=2, log_calls=5_000, use_gzip=True": TestOptions(
        use_timed=True,
        log_calls=5_000,
        min_rollovers=20,
        log_opts=TestOptions.default_timed_log_opts(
            {
                "maxBytes": 1024 * 100,
                "interval": 2,
                "use_gzip": True,
                "debug": True,
            }
        ),
    ),
}


use_timed_only = False

test_cases = TEST_CASES
if use_timed_only:
    test_cases = {label: case for label, case in TEST_CASES.items() if case.use_timed}


@pytest.mark.parametrize("label, test_opts", test_cases.items())
def test_run_stress_test(label: str, test_opts: TestOptions):  # noqa: ARG001
    """Run the stress test with the given options and verify the result."""
    assert run_stress_test(test_opts) == (1 if test_opts.induce_failure else 0)
