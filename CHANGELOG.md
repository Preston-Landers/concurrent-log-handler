## Change Log ##

- 0.9.19: Fix Python 2 compatibility (again), thanks @buddly27
    Fix accidental detection of 'darwin' (Mac OS) as Windows in setup.py

- 0.9.18: Remove ez_setup from the setup.py

- 0.9.17: Contains the following fixes:
  * Catch exceptions when unlocking the lock.
  * Clarify documentation, esp. with use of multiprocessing
  * In Python 2, don't request/allow portalocker 2.0 which won't work.  (Require portalocker<=1.7.1)
  
  NOTE: the next release will likely be a 1.0 release candidate.
  
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
