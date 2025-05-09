# Concurrent Log Handler (CLH)

[![PyPI version](https://img.shields.io/pypi/v/concurrent-log-handler.svg)](https://pypi.org/project/concurrent-log-handler/)
[![Python versions](https://img.shields.io/pypi/pyversions/concurrent-log-handler.svg)](https://pypi.org/project/concurrent-log-handler/)
[![License](https://img.shields.io/pypi/l/concurrent-log-handler.svg)](https://github.com/Preston-Landers/concurrent-log-handler/blob/main/LICENSE)
[![Build Status](https://img.shields.io/github/actions/workflow/status/Preston-Landers/concurrent-log-handler/clh_tests.yaml?branch=master)](https://github.com/Preston-Landers/concurrent-log-handler/actions)

The `concurrent-log-handler` package provides robust logging handlers for Python's standard `logging` package (PEP 282).
It enables multiple processes (and threads) to safely write to a single log file, with built-in support for size-based
and time-based log rotation and optional log compression.

This package is meant for applications that run in multiple processes, potentially across different hosts sharing a
network drive, and require a centralized logging solution without the complexity of external logging services.

## What's new

- **Version 0.9.26**: (May 2025)
    - Improved performance, especially on POSIX systems.
    - Added testing for Python 3.13 and improved project configuration and documentation.

Details in the [CHANGELOG.md](CHANGELOG.md).

## Key Features

* **Concurrent Logging:** Allows multiple processes and threads to safely write to the same log file without
  corrupting each other's messages.
    * Note that this happens in a blocking manner; i.e., if one process is writing to the log file, other
      processes will wait until the first process is done before writing their messages.
* **File Rotation:**
    * `ConcurrentRotatingFileHandler`: Rotates logs when they reach a specified size.
    * `ConcurrentTimedRotatingFileHandler`: Rotates logs based on time intervals (e.g., hourly, daily) and optionally by
      size.
* **Cross-Platform:** Supports both Windows and POSIX systems (Linux, macOS, etc.).
* **Reliable Locking:** Uses `portalocker` for advisory file locking to ensure exclusive write access during log
  emission and rotation.
    * Advisory means that other (e.g., external) processes could ignore the lock on POSIX.
* **Log Compression:** Optionally compresses rotated log files using gzip (`use_gzip=True`).
* **Asynchronous Logging:** Includes an optional `QueueListener` / `QueueHandler` for background logging, minimizing
  impact on application performance.
* **Customizable:**
    * Control over rotated file naming (`namer`).
    * Set owner and mode permissions for rotated files on Unix-like systems.
    * Specify custom line endings (`newline`, `terminator`).
    * Place lock files in a separate directory (`lock_file_directory`).
* **Python 3.6+:** Modern Python support (for Python 2.7, use
  version [0.9.22](https://github.com/Preston-Landers/concurrent-log-handler/releases/tag/0.9.22)).

## Primary Use Cases

CLH is primarily designed for scenarios where:

* Multiple processes of a Python application need to log to a shared file.
* These processes might be on the same machine or on different machines accessing a shared network drive.
* Log files need to be automatically rotated based on size or time.

Note that this package is not primarily intended for intensive high-throughput logging scenarios,
but rather for general-purpose logging in multi-process applications.

### Alternatives to CLH

While CLH offers a robust file-based solution, consider these alternatives for different needs:

* **Cloud Logging Services:** Azure Monitor, AWS CloudWatch Logs, Google Cloud Logging, Logstash, etc. These are
  excellent for distributed systems and offer advanced analysis features.
* **Custom Logging Server:** Implement a centralized logging server as demonstrated in
  the [Python Logging Cookbook](https://docs.python.org/3/howto/logging-cookbook.html#sending-and-receiving-logging-events-across-a-network).
  CLH's `QueueHandler` and `QueueListener` can be adapted for this pattern.

## Installation

Install the package using `pip`:

```bash
pip install concurrent-log-handler
````

This will also install `portalocker`. On Windows, `portalocker` has a dependency on `pywin32`.

To install from source:

```bash
python setup.py install
```

## Quick Start: Basic Usage

Here's a simple example using `ConcurrentRotatingFileHandler` for size-based rotation:

```python
import logging
from concurrent_log_handler import ConcurrentRotatingFileHandler
import os

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Use an absolute path for the log file
logfile = os.path.abspath("mylogfile.log")

# Configure the handler: rotate after 512KB, keep 5 backups
# Mode "a" for append
rotate_handler = ConcurrentRotatingFileHandler(
    logfile, "a", maxBytes=512 * 1024, backupCount=5
)
logger.addHandler(rotate_handler)

logger.info("This is an exciting log message!")
logger.info("Multiple processes can write here concurrently.")
```

For more examples, including `asyncio` background logging,
see [src/example.py](src/example.py).

## Important Usage Guidelines

To ensure correct and reliable operation when using CLH in a multi-process environment, please keep the following
in mind:

1. **Handler Instantiation per Process:**

    * Each process *must* create its own instance of the CLH handler (`ConcurrentRotatingFileHandler` or
      `ConcurrentTimedRotatingFileHandler`).
    * You **cannot** serialize a handler instance and reuse it in another process.
      * For example, you cannot pass it from a parent to a child process via `multiprocessing` in spawn mode.
    * This limitation is because the file lock objects and other internal states within the handler cannot be safely
      serialized and shared across process boundaries.
    * This requirement **does not** apply to threads within the same process; threads can share a single CLH instance.
    * This requirement also **may not** apply to child processes created via `fork()` (e.g., with Gunicorn `--preload`),
      where file descriptors might be inherited. However, explicit instantiation in each process is the safest approach.

2. **Consistent Configuration:**

    * All processes writing to the *same log file* **must** use identical settings for the CLH handler (e.g.,
      `maxBytes`, `backupCount`, `use_gzip`, rotation interval, etc.).
    * Do not mix CLH handlers with other logging handlers (like `RotatingFileHandler` from the standard library) writing
      to the same file. This can lead to unpredictable behavior and data loss.

3. **Networked/Cloud Storage:**

    * When logging to files on network shares (NFS, SMB/CIFS) or cloud-synced folders (Dropbox, Google Drive, OneDrive),
      ensure that the advisory file locking provided by `portalocker` works correctly in your specific environment.
    * The `lock_file_directory` option allows you to place the lock file in a different location (e.g., a local fast
      filesystem) than the log file itself. This can resolve issues with locking on certain network shares. However, if
      multiple hosts write to the same shared log, they *must* all have access to this common lock file location.
    * Alternatively, configure your cloud sync software to ignore CLH lock files (typically `.<logfilename>.lock` or
      files in the `lock_file_directory`).
    * If you run into problems, try the `keep_file_open=False` option to close the log file after each write. This
      may help with certain networked filesystems but can impact performance.

4. **One Handler Instance per Log File:**

    * If your application writes to multiple distinct log files, each log file requires its own dedicated CLH handler
      instance within each process.

## Handler Details

CLH provides two main handler classes:

### `ConcurrentRotatingFileHandler` (Size-based Rotation)

This handler rotates logs when the file size exceeds `maxBytes`. Note that the actual file sizes may exceed
`maxBytes`. How much larger depends on the size of the log message being written when the rollover occurs.

```python
from concurrent_log_handler import ConcurrentRotatingFileHandler

# Example: Rotate at 10MB, keep 3 backups, compress rotated logs
handler = ConcurrentRotatingFileHandler(
    "app.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=3,
    use_gzip=True
)
```

### `ConcurrentTimedRotatingFileHandler` (Time-based Rotation)

This handler rotates logs at specified time intervals (e.g., hourly, daily, weekly). It can *also* rotate based on size
if `maxBytes` is set to a non-zero value.

By default, it rotates hourly and does not perform size-based rotation (`maxBytes=0`).

```python
from concurrent_log_handler import ConcurrentTimedRotatingFileHandler

# Example: Rotate daily, keep 7 backups, also rotate if file exceeds 20MB
handler = ConcurrentTimedRotatingFileHandler(
    filename="app_timed.log",
    when="D",  # 'D' for daily, 'H' for hourly, 'M' for minute, 'W0'-'W6' for weekly
    interval=1,
    backupCount=7,
    maxBytes=20 * 1024 * 1024,  # Optional size-based rotation
    use_gzip=True
)
```

It's recommended to use keyword arguments when configuring this class due to the number of parameters and their
ordering. For more details on time-based rotation options (`when`, `interval`, `utc`), refer to the Python standard
library documentation for `TimedRotatingFileHandler`.

### Common Configuration Options

Both handlers share several configuration options (passed as keyword arguments):

* `logfile` / `filename`: Path to the log file.
* `mode`: File open mode (default: `'a'` for append).
* `backupCount`: Number of rotated log files to keep.
* `encoding`: Log file encoding (e.g., `'utf-8'`).
* `delay`: Defer file opening until the first log message is emitted (boolean).
* `use_gzip`: (Default: `False`) If `True`, compresses rotated log files using gzip.
* `owner`: Tuple `(uid, gid)` or `['username', 'groupname']` to set file ownership on Unix.
* `chmod`: Integer file mode (e.g., `0o640`) to set permissions on Unix.
* `lock_file_directory`: Path to a directory where lock files should be stored, instead of next to the log file.
* `newline`: (Default: platform-specific) Specify newline characters. E.g., `''` (let `terminator` handle it).
* `terminator`: (Default: platform-specific, e.g., `\n` on POSIX, `\r\n` on Windows). Specify record terminator.
    * To force Windows-style CRLF on Unix: `kwargs={'newline': '', 'terminator': '\r\n'}`
    * To force Unix-style LF on Windows: `kwargs={'newline': '', 'terminator': '\n'}`
* `namer`: A callable function to customize the naming of rotated files. See `BaseRotatingHandler.namer` in Python docs.
* `keep_file_open`: Defaults to `True` for enhanced performance by keeping the log file (and lock file) open between
  writes. This is recommended for most use cases.
    * Set to `False` if you need to ensure the log file is closed after each write, for example, for compatibility with
      certain networked filesystems.
    * On Windows, the log file will always be closed after writes to allow for rotation, but this option still affects
      whether the lock file is kept open.

## Logging Configuration File Usage (`fileConfig`)

You can configure CLH using Python's `logging.config.fileConfig`:

```ini
[loggers]
keys = root

[handlers]
keys = concurrentRotatingFile, concurrentTimedRotatingFile

[formatters]
keys = simpleFormatter

[logger_root]
level = INFO
handlers = concurrentRotatingFile, concurrentTimedRotatingFile

[handler_concurrentRotatingFile]
class = concurrent_log_handler.ConcurrentRotatingFileHandler
level = INFO
formatter = simpleFormatter
args = ("rotating_size.log", "a")
kwargs = {'maxBytes': 10485760, 'backupCount': 5, 'use_gzip': True}
# For Python < 3.7, kwargs are not supported. You might need to subclass or use code config.

[handler_concurrentTimedRotatingFile]
class = concurrent_log_handler.ConcurrentTimedRotatingFileHandler
level = INFO
formatter = simpleFormatter
args = ("rotating_timed.log",) # filename
kwargs = {'when': 'H', 'interval': 1, 'backupCount': 24, 'use_gzip': True}

[formatter_simpleFormatter]
format = %(asctime)s - %(name)s - %(levelname)s - %(message)s
```

**Note:** Ensure you `import concurrent_log_handler` in your Python code *before* calling `fileConfig()`. Python 3.7+ is
recommended for `kwargs` support in config files.

## Background / Asynchronous Logging

For improved performance, especially in I/O-bound applications, CLH provides a utility to easily convert configured
handlers to use a background logging thread. Log messages are placed on a queue and processed by a separate thread,
allowing your application to continue without waiting for disk I/O.

Otherwise, logging calls are blocking, meaning the application will wait for the log message to be written to disk 
before continuing to run the next line of code.


```python
import logging
from concurrent_log_handler import ConcurrentRotatingFileHandler
from concurrent_log_handler.queue import setup_logging_queues

# 1. Setup your logging as usual
logger = logging.getLogger("my_app")
handler = ConcurrentRotatingFileHandler("app.log", maxBytes=1024 * 1024, backupCount=5)
logger.addHandler(handler)
logger.setLevel(logging.INFO)

# 2. Convert handlers to use background queues
setup_logging_queues()

# Now, logging calls are non-blocking
logger.info("This message will be processed in a background thread.")
```

**Important Considerations for Background Logging:**

* **Unbounded Queue:** The current implementation uses an unbounded queue. If log messages are generated faster than
  they can be written to disk, the queue will grow indefinitely, potentially consuming all available memory. This is a
  known limitation and may be addressed in a future release.
* **No Completion Feedback:** There's no direct way for the caller to know when a log message has actually been written
  to disk (no "Future" or "Promise" is returned).
* **Application Shutdown:** Ensure proper logger shutdown (e.g., `logging.shutdown()`) to flush the queue, especially if
  messages at the end of the application's lifecycle are critical.

Refer to the docstring in `concurrent_log_handler/queue.py`
and [src/example.py](src/example.py) for more details.

## Best Practices and Limitations

* **`maxBytes` is a Guideline:** The actual log file size might slightly exceed `maxBytes` because the check is
  performed *before* writing a new log message. The file can grow by the size of that last message. This behavior
  prioritizes preserving log records. Standard `RotatingFileHandler` is stricter but may truncate.
* **`backupCount` Performance:** Avoid excessively high `backupCount` values (e.g., \> 20-50). Renaming many files
  during rotation can be slow, and this occurs while the log file is locked. Consider increasing `maxBytes` instead if
  you need to retain more history in fewer files.
* **Gzip Compression:** Enabling `use_gzip` adds CPU overhead. Using the background logging queue can help offload this.
* **Restart on Configuration Change:** If you change logging settings (e.g., rotation parameters), it's often best to
  restart all application processes to ensure all writers use the new, consistent configuration.

## For Developers (Contributing)

If you plan to modify or contribute to CLH:

1. **Clone the repository:**

   ```bash
   git clone https://github.com/Preston-Landers/concurrent-log-handler.git
   cd concurrent-log-handler
   ```

2. **Create a virtual environment and activate it:**

   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install in editable mode with development dependencies:**

   ```bash
   pip install -e .[dev]
   ```

   This installs `hatch`, `black` and other development tools.

4. **Run tests:**

   ```bash
   # Run tests on the current (venv) Python version
   pytest
   
   # Generate coverage report
   pytest --cov --cov-report=html --cov-report=xml --cov-report=lcov --cov-report=term-missing
   
   # Run tests across all supported Python versions
   hatch test
   ```

5. **Build for distribution:**

   ```bash
   # Ensure clean build
   python setup.py clean --all
   # Build sdist and wheel
   python setup.py build sdist bdist_wheel
   ```

   The distributable files will be in the `dist/` folder. To upload (maintainers only):

   ```bash
   pip install twine
   twine upload dist/*
   ```

## Historical Note

This package is a fork of Lowell Alleman's `ConcurrentLogHandler` 0.9.1. The fork was created to address a
hanging/deadlocking
issue ([Launchpad Bug \#1265150](https://bugs.launchpad.net/python-concurrent-log-handler/+bug/1265150)) and has since
incorporated numerous other fixes, features, and modernizations.

## Project Links

* **GitHub Repository:**
  [https://github.com/Preston-Landers/concurrent-log-handler](https://github.com/Preston-Landers/concurrent-log-handler)
* **PyPI Package:** [https://pypi.org/project/concurrent-log-handler/](https://pypi.org/project/concurrent-log-handler/)

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for a detailed history of changes.

## Contributors

The original version was by Lowell Alleman. Subsequent contributions are listed in [CONTRIBUTORS.md](CONTRIBUTORS.md).

## License

This project is licensed under the terms of the [LICENSE file](./LICENSE) (Apache 2.0 terms).
