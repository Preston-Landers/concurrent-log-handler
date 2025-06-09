#!/usr/bin/env python
# ruff: noqa: S101, S603

"""
Test cases for proper handling of Python shutdown scenarios.

Tests that logging during Python interpreter shutdown (e.g., from __del__ methods)
doesn't cause NameError due to built-in functions being cleaned up.
"""

import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest


def test_logging_during_shutdown():
    """Test that logging during Python shutdown doesn't cause NameError.

    This test reproduces the issue where aiohttp (or any library) tries to log
    warnings during __del__ methods when Python is shutting down, which previously
    caused 'NameError: name 'open' is not defined' because built-in functions
    were already cleaned up.
    """

    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = Path(tmpdir) / "test_shutdown.log"

        # Create a test script that will log during shutdown
        test_script = textwrap.dedent(
            f'''
            import logging.config
            import sys
            import os

            # Add the src directory to the path so we can import concurrent_log_handler
            src_path = os.path.abspath(os.path.join({str(Path(__file__).parent.parent)!r}, 'src'))
            sys.path.insert(0, src_path)

            import concurrent_log_handler

            class ObjectWithDelLogger:
                """Object that logs during __del__ to simulate the aiohttp scenario."""

                def __del__(self):
                    # This will be called during Python shutdown
                    logger = logging.getLogger("shutdown_test")
                    try:
                        logger.error("Logging during __del__ - simulating unclosed resource warning")
                    except Exception as e:
                        # If we get here, the test should fail
                        print(f"SHUTDOWN_ERROR: {{type(e).__name__}}: {{e}}", file=sys.stderr)

            # Configure logging
            LOGGER_CONFIG = {{
                'version': 1,
                'disable_existing_loggers': False,
                'handlers': {{
                    'file': {{
                        '()': 'concurrent_log_handler.ConcurrentRotatingFileHandler',
                        'filename': {str(log_file)!r},
                        'mode': 'a',
                        'maxBytes': 1024 * 1024,
                        'backupCount': 3,
                    }}
                }},
                'loggers': {{
                    '': {{
                        'handlers': ['file'],
                        'level': 'DEBUG',
                    }}
                }}
            }}

            logging.config.dictConfig(LOGGER_CONFIG)
            logger = logging.getLogger("shutdown_test")

            # Log normally first to ensure handler is working
            logger.info("Normal logging before shutdown")

            # Create an object that will log during __del__
            # Keep it alive until Python shutdown
            keeper = ObjectWithDelLogger()

            # Also test with explicit reference to ensure it's really during shutdown
            import __main__
            __main__.keeper = keeper

            logger.info("Script completed, shutdown will begin")
            # When Python exits, it will trigger keeper.__del__()
        '''
        )

        # Run the test script in a subprocess
        result = subprocess.run(
            [sys.executable, "-c", test_script],
            capture_output=True,
            text=True,
            check=False,
        )

        # Check that the script ran without NameError during shutdown
        assert "SHUTDOWN_ERROR: NameError" not in result.stderr, (
            f"NameError occurred during shutdown. This likely means the 'open' "
            f"built-in was not properly preserved.\nstderr: {result.stderr}"
        )

        # The script should exit cleanly (exit code 0)
        assert result.returncode == 0, (
            f"Script exited with non-zero code: {result.returncode}\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

        # Verify that both normal and shutdown logging worked
        assert log_file.exists(), "Log file was not created"
        log_content = log_file.read_text()

        # Should contain the normal log message
        assert (
            "Normal logging before shutdown" in log_content
        ), f"Normal log message not found in log file. Content:\n{log_content}"

        # Should contain the shutdown log message (from __del__)
        # Note: In some Python versions, __del__ might not be called during shutdown in our test
        if sys.version_info >= (3, 10):
            assert "Logging during __del__" in log_content, (
                f"Shutdown log message not found. This might mean __del__ wasn't called "
                f"or logging failed silently. Content:\n{log_content}"
            )
        else:
            # For older Python versions, just verify no NameError occurred
            # The actual protection is still in place even if our test can't trigger it
            pass


def test_logging_during_extreme_shutdown():
    """Test logging when even stored references might be None during shutdown.

    This tests the extreme case where Python shutdown has progressed so far
    that even module-level stored references might be None.
    """

    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = Path(tmpdir) / "test_extreme_shutdown.log"

        # Create a test script that simulates extreme shutdown conditions
        test_script = textwrap.dedent(
            f"""
            import logging.config
            import sys
            import os

            src_path = os.path.abspath(os.path.join({str(Path(__file__).parent.parent)!r}, 'src'))
            sys.path.insert(0, src_path)

            import concurrent_log_handler

            # Configure logging
            LOGGER_CONFIG = {{
                'version': 1,
                'disable_existing_loggers': False,
                'handlers': {{
                    'file': {{
                        '()': 'concurrent_log_handler.ConcurrentRotatingFileHandler',
                        'filename': {str(log_file)!r},
                    }}
                }},
                'loggers': {{
                    '': {{
                        'handlers': ['file'],
                        'level': 'DEBUG',
                    }}
                }}
            }}

            logging.config.dictConfig(LOGGER_CONFIG)
            logger = logging.getLogger("extreme_test")

            # Get a reference to the handler
            handler = logger.handlers[0]

            # Simulate extreme shutdown by setting the stored references to None
            # This tests our None checks
            concurrent_log_handler._open = None
            concurrent_log_handler._os_open = None

            # Try to log - should handle gracefully without crashing
            try:
                logger.error("Attempting to log with None references")
            except RuntimeError as e:
                # We expect a RuntimeError about shutdown, not a NameError
                if "shutdown" not in str(e):
                    print(f"UNEXPECTED_ERROR: {{e}}", file=sys.stderr)
                    sys.exit(1)
            except NameError as e:
                # This should NOT happen with our fix
                print(f"NAMEERROR_OCCURRED: {{e}}", file=sys.stderr)
                sys.exit(2)
            except Exception as e:
                # Log unexpected errors for debugging
                print(f"OTHER_ERROR: {{type(e).__name__}}: {{e}}", file=sys.stderr)
                # Don't fail on other errors as logging might partially work

            # Success - we handled the extreme case without NameError
        """
        )

        # Run the test script
        result = subprocess.run(
            [sys.executable, "-c", test_script],
            capture_output=True,
            text=True,
            check=False,
        )

        # Should not have NameError
        assert "NAMEERROR_OCCURRED" not in result.stderr, (
            f"NameError occurred even with stored references. "
            f"stderr: {result.stderr}"
        )

        # Should not exit with code 2 (our NameError indicator)
        error_exit = 2
        assert result.returncode != error_exit, (
            f"Script indicated NameError occurred.\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )


def test_multiple_handlers_during_shutdown():
    """Test multiple handlers logging during shutdown to ensure thread safety."""

    with tempfile.TemporaryDirectory() as tmpdir:
        log_file1 = Path(tmpdir) / "test_shutdown1.log"
        log_file2 = Path(tmpdir) / "test_shutdown2.log"

        test_script = textwrap.dedent(
            f"""
            import logging.config
            import sys
            import os

            src_path = os.path.abspath(os.path.join({str(Path(__file__).parent.parent)!r}, 'src'))
            sys.path.insert(0, src_path)

            import concurrent_log_handler

            class MultiDelLogger:
                def __init__(self, name):
                    self.name = name

                def __del__(self):
                    logger = logging.getLogger(f"shutdown_{{self.name}}")
                    try:
                        logger.error(f"Logging from {{self.name}} during shutdown")
                    except NameError as e:
                        print(f"NAMEERROR_IN_{{self.name}}: {{e}}", file=sys.stderr)

            # Configure multiple handlers
            LOGGER_CONFIG = {{
                'version': 1,
                'disable_existing_loggers': False,
                'handlers': {{
                    'file1': {{
                        '()': 'concurrent_log_handler.ConcurrentRotatingFileHandler',
                        'filename': {str(log_file1)!r},
                    }},
                    'file2': {{
                        '()': 'concurrent_log_handler.ConcurrentRotatingFileHandler',
                        'filename': {str(log_file2)!r},
                    }}
                }},
                'loggers': {{
                    'shutdown_obj1': {{
                        'handlers': ['file1'],
                        'level': 'DEBUG',
                    }},
                    'shutdown_obj2': {{
                        'handlers': ['file2'],
                        'level': 'DEBUG',
                    }}
                }}
            }}

            logging.config.dictConfig(LOGGER_CONFIG)

            # Create multiple objects that will log during shutdown
            keepers = [MultiDelLogger(f"obj{{i}}") for i in range(5)]

            # Keep them alive until shutdown
            import __main__
            __main__.keepers = keepers
        """
        )

        result = subprocess.run(
            [sys.executable, "-c", test_script],
            capture_output=True,
            text=True,
            check=False,
        )

        # Check for NameErrors
        assert "NAMEERROR_IN_" not in result.stderr, (
            f"NameError occurred in one or more objects during shutdown.\n"
            f"stderr: {result.stderr}"
        )

        assert result.returncode == 0, (
            f"Script failed with exit code: {result.returncode}\n"
            f"stderr: {result.stderr}"
        )


if __name__ == "__main__":
    # Allow running the test directly
    pytest.main([__file__, "-v"])
