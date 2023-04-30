#!/usr/bin/env python  # noqa: INP001
# ruff: noqa: S101, PT006

"""
Pytest based unit test cases to drive stresstest.py.
"""

import pytest
from stresstest import TestOptions, run_stress_test

TEST_CASES = {
    "default test options": TestOptions(),
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
    # TODO: it would be good to have some test cases that verify that backupCount is not exceeded.

    # Testing time intervals other than seconds is difficult because the tests would
    # take hours unless we find a way to mock things.
}


@pytest.mark.parametrize("label, test_opts", TEST_CASES.items())
def test_run_stress_test(label: str, test_opts: TestOptions):  # noqa: ARG001
    """Run the stress test with the given options and verify the result."""
    assert run_stress_test(test_opts) == (1 if test_opts.induce_failure else 0)
