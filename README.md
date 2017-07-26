## concurrent-log-handler ##

This is a fork of Lowell Alleman's ConcurrentLogHandler 0.9.1 which fixes
a hanging/deadlocking problem. See this:

https://bugs.launchpad.net/python-concurrent-log-handler/+bug/1265150

Summary of other changes:

* Renamed package to `concurrent_log_handler`
* portalocker is inside the package, not a separate module.
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



NOTES: This module has not yet be tested in a multi-threaded environment. I see
no reason why this should be an issue, but no stress-testing has been done in a
threaded situation. If this is important to you, you could always add threading
support to the stresstest.py script and send me the patch.

Update (Preston): this works fine in a multi-process concurrency environment but I have 
not tested it extensively with threads or async, but those locks should be handled by the 
parent `logging` class. 


## Change Log ##

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
