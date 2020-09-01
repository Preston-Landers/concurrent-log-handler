## concurrent-log-handler ##

This package provides an additional log handler for Python's standard logging
package (PEP 282). This handler will write log events to a log file which is
rotated when the log file reaches a certain size.  Multiple processes can
safely write to the same log file concurrently. Rotated logs can be gzipped
if desired. Both Windows and POSIX systems are supported.  An optional threaded 
queue logging handler is provided to perform logging in the background.

This is a fork of Lowell Alleman's ConcurrentLogHandler 0.9.1 which fixes
a hanging/deadlocking problem. See this:

https://bugs.launchpad.net/python-concurrent-log-handler/+bug/1265150

Summary of other changes:

* Renamed package to `concurrent_log_handler`
* Provide `use_gzip` option to compress rotated logs
* Support for Windows
* Uses file locking to ensure exclusive write access
  Note: file locking is advisory, not a hard lock against external processes
* More secure generation of random numbers for temporary filenames
* Change the name of the lockfile to have .__ in front of it.
* Provide a QueueListener / QueueHandler implementation for 
  handling log events in a background thread. Optional: requires Python 3. 
* Allow setting owner and mode permissions of rollover file on Unix
* Depends on `portalocker` package, which (on Windows only) depends on `PyWin32`

## Instructions and Usage ##

### Installation ###

You can download and install the package with `pip` using the following command:

    pip install concurrent-log-handler

This will also install the portalocker module, which on Windows in turn depends on pywin32.

If installing from source, use the following command:

    python setup.py install

To build a Python "wheel" for distribution, use the following:

    python setup.py clean --all bdist_wheel
    # Copy the .whl file from under the "dist" folder

### Important Requirements ###

Concurrent Log Handler (CLH) is designed to allow multiple processes to write to the same
logfile in a concurrent manner. It is important that each process involved MUST follow
these requirements:

 * Each process must create its OWN instance of the handler (`ConcurrentRotatingFileHandler`)

   * This requirement does not apply to threads within a given process. Different threads
     within a process can use the same CLH instance. Thread locking is handled automatically.

 * As a result of the above, you CANNOT serialize a handler instance and reuse it in another
   process. This means you cannot, for example, pass a CLH handler instance from parent process
   to child process using the `multiprocessing` package (or similar techniques). Each child
   process must initialize its own CLH instance. In the case of a multiprocessing target
   function, the child target function can call code to initialize a CLH instance. 
   If your app uses fork() then this may not apply; child processes of a fork() should 
   be able to inherit the object instance.

 * It is important that every process or thread writing to a given logfile must all use the
   same settings, especially related to file rotation. Also do not attempt to mix different 
   handler classes writing to the same file, e.g. do not also use a `RotatingFileHandler` on 
   the same file.

 * Special attention may need to be paid when the log file being written to resides on a network
   shared drive. Whether the multi-process advisory lock technique (via portalocker) works 
   on a network share may depend on the details of your configuration.

 * A separate handler instance is needed for each individual log file. For instance, if your 
   app writes to two different logs you will need to set up two CLH instances per process.
 
### Simple Example ###

Here is a simple usage example:

    from logging import getLogger, INFO
    from concurrent_log_handler import ConcurrentRotatingFileHandler
    import os
    
    log = getLogger()
    # Use an absolute path to prevent file rotation trouble.
    logfile = os.path.abspath("mylogfile.log")
    # Rotate log after reaching 512K, keep 5 old copies.
    rotateHandler = ConcurrentRotatingFileHandler(logfile, "a", 512*1024, 5)
    log.addHandler(rotateHandler)
    log.setLevel(INFO)
    
    log.info("Here is a very exciting log message, just for you")

See also the file `src/example.py` for a configuration and usage example.

### Configuration ###

To use this module from a logging config file, use a handler entry like this:

    [handler_hand01]
    class=handlers.ConcurrentRotatingFileHandler
    level=NOTSET
    formatter=form01
    args=("rotating.log", "a")
    kwargs={'backupCount': 5, 'maxBytes': 512*1024}
    
Please note that Python 3.7 and higher accepts keyword arguments (kwargs) in a logging 
config file, but earlier versions of Python only accept positional args.

Note: you must have a "import concurrent_log_handler" before you call fileConfig(). For
more information see http://docs.python.org/lib/logging-config-fileformat.html

### Line Endings ###

By default, the logfile will have line endings appropriate to the platform. On Windows
the line endings will be CRLF ('\r\n') and on Unix/Mac they will be LF ('\n'). 

It is possible to force another line ending format by using the newline and terminator
arguments.

The following would force Windows-style CRLF line endings on Unix:

    kwargs={'newline': '', 'terminator': '\r\n'}

The following would force Unix-style LF line endings on Windows:

    kwargs={'newline': '', 'terminator': '\n'}

### Background logging queue ###

To use the background logging queue, you must call this code at some point in your
app where it sets up logging configuration. Please read the doc string in the
file `concurrent_log_handler/queue.py` for more details. This requires Python 3.

    from concurrent_log_handler.queue import setup_logging_queues
    
    # convert all configured loggers to use a background thread
    setup_logging_queues()

This module is designed to function well in a multi-threaded or multi-processes 
concurrent environment. However, all writers to a given log file should be using
the same class and the *same settings* at the same time, otherwise unexpected
behavior may result during file rotation. 

This may mean that if you change the logging settings at any point you may need to 
restart your app service so that all processes are using the same settings at the same time.


## Other Usage Details ##

The `ConcurrentRotatingFileHandler` class is a drop-in replacement for
Python's standard log handler `RotatingFileHandler`. This module uses file
locking so that multiple processes can concurrently log to a single file without
dropping or clobbering log events. This module provides a file rotation scheme
like with `RotatingFileHandler`.  Extra care is taken to ensure that logs
can be safely rotated before the rotation process is started. (This module works
around the file rename issue with `RotatingFileHandler` on Windows, where a
rotation failure means that all subsequent log events are dropped).

This module attempts to preserve log records at all cost. This means that log
files will grow larger than the specified maximum (rotation) size. So if disk
space is tight, you may want to stick with `RotatingFileHandler`, which will
strictly adhere to the maximum file size.

Important:

If you have multiple instances of a script (or multiple scripts) all running at
the same time and writing to the same log file, then *all* of the scripts should
be using `ConcurrentRotatingFileHandler`. You should not attempt to mix
and match `RotatingFileHandler` and `ConcurrentRotatingFileHandler`.
The file locking is advisory only - it is respected by other Concurrent Log Handler
instances, but does not protect against outside processes (or different Python logging 
file handlers) from writing to a log file in use.

## Change Log ##

- 0.9.16: Fix publishing issue with incorrect code included in the wheel 
  Affects Python 2 mainly - see Issue #21

- 0.9.15: Fix bug from last version on Python 2. (Issue #21) Thanks @condontrevor
  Also, on Python 2 and 3, apply unicode_error_policy (default: ignore) to convert 
  a log message to the output stream's encoding. I.e., by default it will filter 
  out (remove) any characters in a log message which cannot be converted to the 
  output logfile's encoding.

- 0.9.14: Fix writing LF line endings on Windows when encoding is specified.
  Added newline and terminator kwargs to allow customizing line ending behavior.
  Thanks to @vashek

- 0.9.13: Fixes Crashes with ValueError: I/O operation on closed file (issue #16)
  Also should fix issue #13 with crashes related to Windows file locking.
  Big thanks to @terencehonles, @nsmcan, @wkoot, @dismine for doing the hard parts

- 0.9.12: Add umask option (thanks to @blakehilliard) 
   This adds the ability to control the permission flags when creating log files.

- 0.9.11: Fix issues with gzip compression option (use buffering)

- 0.9.10: Fix inadvertent lock sharing when forking
   Thanks to @eriktews for this fix

- 0.9.9: Fix Python 2 compatibility broken in last release 

- 0.9.8: Bug fixes and permission features
   * Fix for issue #4 - AttributeError: 'NoneType' object has no attribute 'write'
      This error could be caused if a rollover occurred inside a logging statement
      that was generated from within another logging statement's format() call.
   * Fix for PyWin32 dependency specification (explicitly require PyWin32)
   * Ability to specify owner and permissions (mode) of rollover files [Unix only]   

- 0.9.7/0.9.6: Fix platform specifier for PyPi

- 0.9.5: Add `use_gzip` option to compress rotated logs. Add an optional threaded 
logging queue handler based on the standard library's `logging.QueueHandler`.

- 0.9.4: Fix setup.py to not include tests in distribution.

- 0.9.3: Refactoring release
   * For publishing fork on pypi as `concurrent-log-handler` under new package name.
   * NOTE: PyWin32 is required on Windows but is not an explicit dependency because 
           the PyWin32 package is not currently installable through pip.
   * Fix lock behavior / race condition

- 0.9.2: Initial release of fork by Preston Landers based on a fork of Lowell Alleman's 
  ConcurrentLogHandler 0.9.1
   * Fixes deadlocking issue with recent versions of Python
   * Puts `.__` prefix in front of lock file name
   * Use `secrets` or `SystemRandom` if available.
   * Add/fix Windows support

## Contributors ##

The following folks were kind enough to contribute to this fork:

https://github.com/Preston-Landers

https://github.com/und3rc

https://github.com/wcooley

https://github.com/greenfrog82

https://github.com/blakehilliard

https://github.com/eriktews
