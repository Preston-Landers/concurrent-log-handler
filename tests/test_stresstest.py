#!/usr/bin/env python

"""
Pytest based unit test cases to drive stresstest.py.
"""

import pytest

from stresstest import run_stress_test, TestOptions


@pytest.mark.parametrize(
    "test_opts",
    [
        TestOptions(),  # All default options.
        TestOptions(num_processes=2, log_calls=6_000),
        TestOptions(induce_failure=True),
        TestOptions(num_processes=12, log_calls=600),
        TestOptions(
            num_processes=15,
            log_calls=1_500,
            log_opts=TestOptions.default_log_opts(
                {
                    "maxBytes": 1024 * 5,  # rotate more often
                }
            ),
        ),
        TestOptions(
            log_opts=TestOptions.default_log_opts(
                {
                    "use_gzip": True,
                }
            )
        ),
        TestOptions(
            induce_failure=True,
            log_calls=500,
            log_opts=TestOptions.default_log_opts(
                {
                    "use_gzip": True,
                }
            ),
        ),
        TestOptions(
            num_processes=3,
            log_calls=500,
            log_opts=TestOptions.default_log_opts(
                {
                    "debug": True,
                }
            ),
        ),
        # Add more configurations if needed
    ],
)
def test_run_stress_test(test_opts):
    assert run_stress_test(test_opts) == (1 if test_opts.induce_failure else 0)
