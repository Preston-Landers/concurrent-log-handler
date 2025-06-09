# Concurrent Log Handler (CLH)

[![PyPI version](https://img.shields.io/pypi/v/concurrent-log-handler.svg)](https://pypi.org/project/concurrent-log-handler/)
[![Downloads](https://img.shields.io/pypi/dm/concurrent-log-handler.svg)](https://pepy.tech/project/concurrent-log-handler)
[![Stars](https://img.shields.io/github/stars/Preston-Landers/concurrent-log-handler.svg)](https://github.com/Preston-Landers/concurrent-log-handler/stargazers)
[![Forks](https://img.shields.io/github/forks/Preston-Landers/concurrent-log-handler.svg)](https://github.com/Preston-Landers/concurrent-log-handler/network/members)
[![Python versions](https://img.shields.io/pypi/pyversions/concurrent-log-handler.svg)](https://pypi.org/project/concurrent-log-handler/)
[![License](https://img.shields.io/pypi/l/concurrent-log-handler.svg)](https://github.com/Preston-Landers/concurrent-log-handler/blob/main/LICENSE)
[![Build Status](https://img.shields.io/github/actions/workflow/status/Preston-Landers/concurrent-log-handler/clh_tests.yaml?branch=master)](https://github.com/Preston-Landers/concurrent-log-handler/actions)
[![Contributors](https://img.shields.io/github/contributors/Preston-Landers/concurrent-log-handler.svg)](https://github.com/Preston-Landers/concurrent-log-handler/graphs/contributors)

The `concurrent-log-handler` package provides robust logging handlers for Python's standard `logging` package (PEP 282).
It enables multiple processes (and threads) to safely write to a single log file, with built-in support for size-based
and time-based log rotation and optional log compression.

This package is meant for applications that run in multiple processes, potentially across different hosts sharing a
network drive, and require a centralized logging solution without the complexity of external logging services.

NOTE: If you're reading this and the links to files like [CHANGELOG.md](CHANGELOG.md) don't work,
or links within the document either, try viewing
[this document on GitHub](https://github.com/Preston-Landers/concurrent-log-handler/) instead.

## What's new

See [CHANGELOG.md](CHANGELOG.md) for details.

- **Version 0.9.28**: (June 10th, 2025)
  - Fixes errors when apps, esp. asyncio based, try to log during interpreter shutdown.
    Issue [#80](https://github.com/Preston-Landers/concurrent-log-handler/issues/80)
- **Version 0.9.27**: (June 6th, 2025)
  - Fixes Issue [#73](https://github.com/Preston-Landers/concurrent-log-handler/issues/73)
    Fix timed rotation handler's file cleanup logic.
  - Fixes Issue [#79](https://github.com/Preston-Landers/concurrent-log-handler/issues/79)
    Harden timed handler's rollover mechanism against timestamp errors or other sync corruption.

- **Important Notice (June 2025): Background Logging Utility Deprecated**
  - The `concurrent_log_handler.queue` module is now **deprecated** (will be removed in v1.0.0).
  - It has compatibility issues with complex logging setups and other robustness concerns.
  - **Recommended:** Use the standard library patterns shown in [Performance Patterns](docs/patterns.md).
  - The core CLH handlers remain fully supported and are not affected by this deprecation.

- **Version 0.9.26**: (May 2025)
  - Improved performance, especially on POSIX systems.
  - Added testing for Python 3.13 and improved project configuration and documentation.

## Key Features

- **Concurrent Logging:** Multiple processes and threads safely write to the same log file.
- **File Rotation:** Size-based and time-based rotation with optional compression.  
- **Cross-Platform:** Windows and POSIX support with reliable file locking.
- **Customizable:** Control naming, permissions, line endings, and lock file placement.
- **Performance Optimized:** Keeps files open between writes for better performance.
- **Python 3.6 through current versions:** Modern Python support.
- **Focused Design:** Reliably handles file operations. For non-blocking behavior, see our recommended 
    [Application-Level Performance Patterns](docs/patterns.md).

## Primary Use Cases

CLH is primarily designed for scenarios where:

- Multiple processes of a Python application need to log to a shared file.
- These processes might be on the same machine or on different machines accessing a shared network drive.
- Log files need to be automatically rotated based on size or time.

Note that this package is not primarily intended for intensive high-throughput logging scenarios,
but rather for general-purpose logging in multi-process applications.

### Alternatives to CLH

While CLH offers a robust file-based solution, consider these alternatives for different needs:

- **Cloud Logging Services:** Azure Monitor, AWS CloudWatch Logs, Google Cloud Logging, Logstash, etc. These are
  excellent for distributed systems and offer advanced analysis features.
- **Custom Logging Server:** Implement a centralized logging server as demonstrated in
  the [Python Logging Cookbook](https://docs.python.org/3/howto/logging-cookbook.html#sending-and-receiving-logging-events-across-a-network).
  CLH's `QueueHandler` and `QueueListener` can be adapted for this pattern.

## Installation

Install the package using `pip`:

```bash
pip install concurrent-log-handler
```

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

   - Each process _must_ create its own instance of the CLH handler (`ConcurrentRotatingFileHandler` or
     `ConcurrentTimedRotatingFileHandler`).
   - You **cannot** serialize a handler instance and reuse it in another process.
     - For example, you cannot pass it from a parent to a child process via `multiprocessing` in spawn mode.
   - This limitation is because the file lock objects and other internal states within the handler cannot be safely
     serialized and shared across process boundaries.
   - This requirement **does not** apply to threads within the same process; threads can share a single CLH instance.
   - This requirement also **may not** apply to child processes created via `fork()` (e.g., with Gunicorn `--preload`),
     where file descriptors might be inherited. However, explicit instantiation in each process is the safest approach.

2. **Multiprocessing and Spawn mode:**

   Just to reemphasize the point above:

   - If you use `multiprocessing` with the default `spawn` start method (the default on Windows and macOS), 
     each child process must create its own CLH handler instance.
   - In your child process startup code, instantiate the handler as shown in the example above.
   - Usually this means you can call your standard logging setup function in the child.
   - Don't just initialize your logging code in the parent process and allow child processes to inherit loggers.
     
3. **Consistent Configuration:**

   - All processes writing to the _same log file_ **must** use identical settings for the CLH handler (e.g.,
     `maxBytes`, `backupCount`, `use_gzip`, rotation interval, etc.).
   - Do not mix CLH handlers with other logging handlers (like `RotatingFileHandler` from the standard library) writing
     to the same file. This can lead to unpredictable behavior and data loss.

4. **Networked/Cloud Storage:**

   - When logging to files on network shares (NFS, SMB/CIFS) or cloud-synced folders (Dropbox, Google Drive, OneDrive),
     ensure that the advisory file locking provided by `portalocker` works correctly in your specific environment.
   - The `lock_file_directory` option allows you to place the lock file in a different location (e.g., a local fast
     filesystem) than the log file itself. This can resolve issues with locking on certain network shares. However, if
     multiple hosts write to the same shared log, they _must_ all have access to this common lock file location.
   - Alternatively, configure your cloud sync software to ignore CLH lock files (typically `.<logfilename>.lock` or
     files in the `lock_file_directory`).
   - If you run into problems, try the `keep_file_open=False` option to close the log file after each write. This
     may help with certain networked filesystems but can impact performance.

5. **One Handler Instance per Log File:**

   - If your application writes to multiple distinct log files, each log file
     requires its own dedicated CLH handler instance within each process.

   - It is possible to have multiple CLH handlers that point to the same log file,
     for example, if you want to log different log levels or formats to the same
     file. However, all other rotation settings must be identical across these
     handlers.

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

This handler rotates logs at specified time intervals (e.g., hourly, daily, weekly). It can _also_ rotate based on size
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

- `logfile` / `filename`: Path to the log file.
- `mode`: File open mode (default: `'a'` for append).
- `backupCount`: Number of rotated log files to keep.
- `encoding`: Log file encoding (e.g., `'utf-8'`).
- `delay`: Defer file opening until the first log message is emitted (boolean).
- `use_gzip`: (Default: `False`) If `True`, compresses rotated log files using gzip.
- `owner`: Tuple `(uid, gid)` or `['username', 'groupname']` to set file ownership on Unix.
- `chmod`: Integer file mode (e.g., `0o640`) to set permissions on Unix.
- `lock_file_directory`: Path to a directory where lock files should be stored, instead of next to the log file.
- `newline`: (Default: platform-specific) Specify newline characters. E.g., `''` (let `terminator` handle it).
- `terminator`: (Default: platform-specific, e.g., `\n` on POSIX, `\r\n` on Windows). Specify record terminator.
  - To force Windows-style CRLF on Unix: `kwargs={'newline': '', 'terminator': '\r\n'}`
  - To force Unix-style LF on Windows: `kwargs={'newline': '', 'terminator': '\n'}`
- `namer`: A callable function to customize the naming of rotated files. See `BaseRotatingHandler.namer` in Python docs.
- `keep_file_open`: Defaults to `True` for enhanced performance by keeping the log file (and lock file) open between
  writes. This is recommended for most use cases.
  - Set to `False` if you need to ensure the log file is closed after each write, for example, for compatibility with
    certain networked filesystems.
  - On Windows, the log file will always be closed after writes to allow for rotation, but this option still affects
    whether the lock file is kept open.

---

## Non-Blocking Logging Patterns (Optional)

CLH handlers are **synchronous by design** for maximum reliability and
predictability. However, some applications need non-blocking logging to prevent
thread blocking during logging bursts or high-frequency logging scenarios.

### Quick Note on Performance:

With CLH's recent performance improvements (v0.9.26+), many applications won't
need non-blocking patterns. Test your specific use case first - synchronous
logging is simpler and more reliable.

### When You Might Need Non-Blocking Logging:
 
- Multiple threads logging heavily during error conditions
- Web application request threads that shouldn't block on I/O
- High-frequency logging in performance-critical applications
- Applications with strict latency requirements

### Quick Start: The Recommended Approach

For most non-blocking needs, use Python's standard library `QueueHandler`:

```python
import logging
import logging.handlers
import queue
import atexit
from concurrent_log_handler import ConcurrentRotatingFileHandler

# Create queue and handlers
log_queue = queue.Queue(maxsize=10000)
file_handler = ConcurrentRotatingFileHandler("app.log", maxBytes=10*1024*1024)
queue_handler = logging.handlers.QueueHandler(log_queue)

# Set up background processing
listener = logging.handlers.QueueListener(log_queue, file_handler)
listener.start()
atexit.register(listener.stop)

# Configure logger
logging.getLogger().addHandler(queue_handler)
logging.info("This won't block your thread!")
```

### Complete Guide

For comprehensive patterns including:
 
- **Web framework integration** (Django, Flask, FastAPI)
- **Graceful degradation** when queues fill up
- **Production monitoring** and error handling
- **Critical vs background logging** strategies
- **Performance comparisons** and best practices

**See the complete guide: [Performance Patterns](docs/patterns.md)**

This approach uses Python's standard library tools, giving you full control 
while keeping CLH focused on reliable file operations.

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

**Note:** Ensure you `import concurrent_log_handler` in your Python code _before_ calling `fileConfig()`. Python 3.7+ is
recommended for `kwargs` support in config files.

## Migration from Deprecated Background Logging

**⚠️ Removal Timeline: The `queue` module will be removed in v1.0.0 (planned for Summer 2025)**

**If you're currently using `concurrent_log_handler.queue.setup_logging_queues()`:**

This utility is deprecated and will be removed in v1.0.0 due to compatibility
issues with complex logging setups. It was provided for convenience but is not
an integral part of CLH's functionality.

**Important**: consider whether you need non-blocking logging at all. Typical 
applications can get reasonable performance with the normal (synchronous) CLH handlers.

### Migration Steps:

1. **Remove** calls to `setup_logging_queues()`

2. **Replace** with the standard library patterns shown above or in 
   [Performance Patterns](docs/patterns.md)
   
   **or**: simply use the CLH handlers directly in your application without the
   queue utility.

3. **Test** your application with the new approach

### Why the Change?

The old utility:
- ❌ Modified all handlers globally (monkey patching)  
- ❌ Had compatibility issues with advanced logging libraries
- ❌ Used unbounded queues (memory risk)
- ❌ Lacked proper error handling

The new approach:

- ✅ Uses standard library patterns
- ✅ Gives applications explicit control  
- ✅ Works with any logging setup
- ✅ Provides better error visibility

**For detailed migration examples, see [Performance Patterns](docs/patterns.md).**

## Best Practices and Limitations

- **`maxBytes` is a Guideline:** The actual log file size might slightly exceed `maxBytes` 
  because the check is performed _before_ writing a new log message. The file can
  grow by the size of that last message. This behavior prioritizes preserving log
  records. Standard `RotatingFileHandler` is stricter but may truncate.

- **`backupCount` Performance:** Avoid excessively high `backupCount` values (e.g., \> 20-50). 
  Renaming many files during rotation can be slow, and this occurs while the log
  file is locked. Consider increasing `maxBytes` instead if you need to retain
  more history in fewer files.

- **Gzip Compression:** Enabling `use_gzip` adds CPU overhead.

- **Restart on Configuration Change:** If you change logging settings (e.g.,
  rotation parameters), it's often best to restart all application processes to
  ensure all writers use the new, consistent configuration.

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
   
   # or run:
   hatch test

   # Generate coverage report
   pytest --cov --cov-report=html --cov-report=xml --cov-report=lcov --cov-report=term-missing

   # Tests across all supported Python versions in the GitHub Actions matrix
   # However, Python 3.6 and 3.7 are not supported by GitHub Actions due to their EOL status.
   ```

5. **Build for distribution:**

   ```bash
   hatch build --clean
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

- **GitHub Repository:**
  [https://github.com/Preston-Landers/concurrent-log-handler](https://github.com/Preston-Landers/concurrent-log-handler)
- **PyPI Package:** [https://pypi.org/project/concurrent-log-handler/](https://pypi.org/project/concurrent-log-handler/)

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for a detailed history of changes.

## Contributors

The original version was by Lowell Alleman. Subsequent contributions are listed in [CONTRIBUTORS.md](CONTRIBUTORS.md).

## License

This project is licensed under the terms of the [LICENSE file](./LICENSE) (Apache 2.0 terms).
