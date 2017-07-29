## concurrent-log-handler ##

This is a fork of Lowell Alleman's ConcurrentLogHandler 0.9.1 which fixes
a hanging/deadlocking problem. See this:

https://bugs.launchpad.net/python-concurrent-log-handler/+bug/1265150

Summary of other changes:

* Renamed package to `concurrent_log_handler`
* portalocker is inside the package, not a separate module.
* Provide `use_gzip` option to compress rotated logs
* Support for Windows
  * Note: PyWin32 is required on Windows, but can't be installed as an
    automatic dependency because it's not currently installable through pip.
* Fix for deadlocking problem with recent versions of Python
* More secure generation of random numbers for temporary filenames
* Change the name of the lockfile to have .__ in front of it.

## Instructions ##

You can install this module by issuing the following command:

    python setup.py install

To build a Python "wheel", use the following:

    python setup.py bdist_wheel
    # Copy the .whl file from under the "dist" folder



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



To use this module from a logging config file, use a handler entry like this:

    [handler_hand01]
    class=handlers.ConcurrentRotatingFileHandler
    level=NOTSET
    formatter=form01
    args=("rotating.log", "a", 512*1024, 5)

Note: you must have a "import concurrent_log_handler" before you call fileConfig(). For
more information see http://docs.python.org/lib/logging-config-fileformat.html

This module is designed to function well in a multi-threaded or multi-processes 
concurrent environment. However, all writers to a given log file should be using
the same class and the *same settings* at the same time, otherwise unexpected
behavior may result during file rotation. 

This may mean that if you change the logging settings at any point you may need to 
restart your app service so that all processes are using the same settings at the same time.


## Change Log ##

- 0.9.5: Add gzip_logs option to compress rotated logs.

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
