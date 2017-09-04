# Copyright 2013 Lowell Alleman
#
#   Licensed under the Apache License, Version 2.0 (the "License"); you may not
#   use this file except in compliance with the License. You may obtain a copy
#   of the License at http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#   WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#   License for the specific language governing permissions and limitations
#   under the License.

""" concurrent_log_handler: A smart replacement for the standard RotatingFileHandler

ConcurrentRotatingFileHandler:  This class is a log handler which is a drop-in
replacement for the python standard log handler 'RotateFileHandler', the primary
difference being that this handler will continue to write to the same file if
the file cannot be rotated for some reason, whereas the RotatingFileHandler will
strictly adhere to the maximum file size.  Unfortunately, if you are using the
RotatingFileHandler on Windows, you will find that once an attempted rotation
fails, all subsequent log messages are dropped.  The other major advantage of
this module is that multiple processes can safely write to a single log file.

To put it another way:  This module's top priority is preserving your log
records, whereas the standard library attempts to limit disk usage, which can
potentially drop log messages. If you are trying to determine which module to
use, there are number of considerations: What is most important: strict disk
space usage or preservation of log messages? What OSes are you supporting? Can
you afford to have processes blocked by file locks?

Concurrent access is handled by using file locks, which should ensure that log
messages are not dropped or clobbered. This means that a file lock is acquired
and released for every log message that is written to disk. (On Windows, you may
also run into a temporary situation where the log file must be opened and closed
for each log message.) This can have potentially performance implications. In my
testing, performance was more than adequate, but if you need a high-volume or
low-latency solution, I suggest you look elsewhere.

Warning: see notes in the README.md about changing rotation settings like maxBytes.
If different processes are writing to the same file, they should all have the same
settings at the same time, or unexpected behavior may result. This may mean that if you
change the logging settings at any point you may need to restart your app service
so that all processes are using the same settings at the same time.

This module currently only support the 'nt' and 'posix' platforms due to the
usage of the portalocker module.  I do not have access to any other platforms
for testing, patches are welcome.

See the README file for an example usage of this module.

This module supports Python 2.6 and later.

"""

import os
import sys
import traceback
from logging import LogRecord
from logging.handlers import BaseRotatingHandler

from concurrent_log_handler.portalocker import LOCK_EX, LOCK_NB, LockException, lock, unlock

try:
    import codecs
except ImportError:
    codecs = None

# Random numbers for rotation temp file names, using secrets module if available (Python 3.6).
# Otherwise use `random.SystemRandom` if available, then fall back on `random.Random`.
try:
    # noinspection PyPackageRequirements
    from secrets import randbits
except ImportError:
    import random

    if hasattr(random, "SystemRandom"):  # May not be present in all Python editions
        # Should be safe to reuse `SystemRandom` - not software state dependant
        randbits = random.SystemRandom().getrandbits
    else:
        def randbits(nb):
            return random.Random().getrandbits(nb)

try:
    import gzip
except ImportError:
    gzip = None

__version__ = '0.9.7'
__author__ = "Preston Landers <planders@gmail.com>"
# __author__ = "Lowell Alleman"
__all__ = [
    "ConcurrentRotatingFileHandler",
]


# Workaround for handleError() in Python 2.7+ where record is written to stderr
class NullLogRecord(LogRecord):
    def __init__(self, *args, **kw):
        super(NullLogRecord, self).__init__(*args, **kw)

    def __getattr__(self, attr):
        return None


class ConcurrentRotatingFileHandler(BaseRotatingHandler):
    """
    Handler for logging to a set of files, which switches from one file to the
    next when the current file reaches a certain size. Multiple processes can
    write to the log file concurrently, but this may mean that the file will
    exceed the given size.
    """

    def __init__(self, filename, mode='a', maxBytes=0, backupCount=0,
                 encoding=None, debug=False, delay=0, use_gzip=False):
        """
        Open the specified file and use it as the stream for logging.

        :param filename: name of the log file to output to.
        :param mode: write mode: defaults to 'a' for text append
        :param maxBytes: rotate the file at this size in bytes
        :param backupCount: number of rotated files to keep before deleting.
        :param encoding: text encoding for logfile
        :param debug: add extra debug statements to this class (for development)
        :param delay: see note below
        :param use_gzip: automatically gzip rotated logs if available.

        By default, the file grows indefinitely. You can specify particular
        values of maxBytes and backupCount to allow the file to rollover at
        a predetermined size.

        Rollover occurs whenever the current log file is nearly maxBytes in
        length. If backupCount is >= 1, the system will successively create
        new files with the same pathname as the base file, but with extensions
        ".1", ".2" etc. appended to it. For example, with a backupCount of 5
        and a base file name of "app.log", you would get "app.log",
        "app.log.1", "app.log.2", ... through to "app.log.5". The file being
        written to is always "app.log" - when it gets filled up, it is closed
        and renamed to "app.log.1", and if files "app.log.1", "app.log.2" etc.
        exist, then they are renamed to "app.log.2", "app.log.3" etc.
        respectively.

        If maxBytes is zero, rollover never occurs.

        On Windows, it is not possible to rename a file that is currently opened
        by another process.  This means that it is not possible to rotate the
        log files if multiple processes is using the same log file.  In this
        case, the current log file will continue to grow until the rotation can
        be completed successfully.  In order for rotation to be possible, all of
        the other processes need to close the file first.  A mechanism, called
        "degraded" mode, has been created for this scenario.  In degraded mode,
        the log file is closed after each log message is written.  So once all
        processes have entered degraded mode, the next rotation attempt should
        be successful and then normal logging can be resumed.  Using the 'delay'
        parameter may help reduce contention in some usage patterns.

        This log handler assumes that all concurrent processes logging to a
        single file will are using only this class, and that the exact same
        parameters are provided to each instance of this class.  If, for
        example, two different processes are using this class, but with
        different values for 'maxBytes' or 'backupCount', then odd behavior is
        expected. The same is true if this class is used by one application, but
        the RotatingFileHandler is used by another.
        """
        self.stream = None
        # Absolute file name handling done by FileHandler since Python 2.5  
        super(ConcurrentRotatingFileHandler, self).__init__(
            filename, mode, encoding=encoding, delay=delay)
        self.delay = delay
        self._rotateFailed = False
        self.maxBytes = maxBytes
        self.backupCount = backupCount

        self.stream_lock = None
        self._debug = debug
        self.use_gzip = True if gzip and use_gzip else False

        # How many times have we recursively locked ourselves?
        # https://bugs.launchpad.net/python-concurrent-log-handler/+bug/1265150
        self._stream_lock_count = 0

        # For debug mode, swap out the "_degrade()" method with a more a verbose one.
        if debug:
            self._degrade = self._degrade_debug
            # self._console_log("concurrent-log-handler init %s" % (hash(self)), stack=False)

    def _open_lockfile(self):
        if self.stream_lock and not self.stream_lock.closed:
            self._console_log("Lockfile already open in this process")
            return
        # Use 'file.lock' and not 'file.log.lock' (Only handles the normal "*.log" case.)
        if self.baseFilename.endswith(".log"):
            lock_file = self.baseFilename[:-4]
        else:
            lock_file = self.baseFilename
        lock_file += ".lock"
        lock_path, lock_name = os.path.split(lock_file)
        # hide the file on Unix and generally from file completion
        lock_name = ".__" + lock_name
        lock_file = os.path.join(lock_path, lock_name)
        self._console_log(
            "concurrent-log-handler %s opening %s" % (hash(self), lock_file), stack=False)
        self.stream_lock = open(lock_file, "wb", buffering=0)

    def _do_file_unlock(self):
        self._console_log("in _do_file_unlock for %s" % (self.stream_lock,), stack=False)
        self._stream_lock_count = 0
        try:
            if self.stream_lock:
                unlock(self.stream_lock)
                self.stream_lock.close()
                self.stream_lock = None
            self._close()
            self._console_log(
                ">release complete lock for %s" % (self.stream_lock,), stack=False)
        except (OSError, ValueError, LockException) as e:
            # May be closed already but that's ok
            self._console_log(e, stack=True)
            # pass

    def _open(self, mode=None):
        """
        Open the current base file with the (original) mode and encoding.
        Return the resulting stream.
        
        Note:  Copied from stdlib.  Added option to override 'mode'
        """
        if mode is None:
            mode = self.mode
        if self.encoding is None:
            stream = open(self.baseFilename, mode)
        else:
            stream = codecs.open(self.baseFilename, mode, self.encoding)
        return stream

    def _close(self):
        """ Close file stream.  Unlike close(), we don't tear anything down, we
        expect the log to be re-opened after rotation."""
        if self.stream:
            try:
                if not self.stream.closed:
                    # Flushing probably isn't technically necessary, but it feels right
                    self.stream.flush()
                    self.stream.close()
            finally:
                self.stream = None

    def _console_log(self, msg, stack=False):
        if not self._debug:
            return
        import threading
        tid = threading.current_thread().name
        pid = os.getpid()
        stack_str = ''
        if stack:
            stack_str = ":\n" + "".join(traceback.format_stack())
        print("[%s %s %s] %s%s" % (tid, pid, hash(self), msg, stack_str,))

    def acquire(self):
        """ Acquire thread and file locks.  Re-opening log for 'degraded' mode.
        """
        self._console_log("In acquire", stack=True)

        # handle thread lock
        super(ConcurrentRotatingFileHandler, self).acquire()

        # noinspection PyBroadException
        try:
            self._open_lockfile()
        except Exception:
            self.handleError(NullLogRecord())

        # Issue a file lock.  (This is inefficient for multiple active threads
        # within a single process. But if you're worried about high-performance,
        # you probably aren't using this log handler.)
        self._stream_lock_count += 1
        self._console_log(">> stream_lock_count = %s" % (self._stream_lock_count,))
        if self._stream_lock_count == 1:
            self._console_log(">Getting lock for %s" % (self.stream_lock,), stack=True)

            lock(self.stream_lock, LOCK_EX)
            self.stream = self._open()
            # self._console_log("Got lock", stack=False)
            # Stream will be opened as part by FileHandler.emit()

    # noinspection PyBroadException
    def release(self):
        """ Release file and thread locks. If in 'degraded' mode, close the
        stream to reduce contention until the log files can be rotated. """
        self._console_log("In release", stack=True)
        try:
            if self._rotateFailed:
                self._close()
        except Exception:
            self.handleError(NullLogRecord())
        finally:
            try:
                self._stream_lock_count -= 1
                if self._stream_lock_count < 0:
                    self._stream_lock_count = 0
                if self._stream_lock_count == 0:
                    self._do_file_unlock()
                    self._console_log("#completed release", stack=False)
                elif self._stream_lock_count:
                    self._console_log(
                        "#inner release (%s)" % (self._stream_lock_count,), stack=True)
            except Exception:
                self.handleError(NullLogRecord())
            finally:
                # release thread lock
                super(ConcurrentRotatingFileHandler, self).release()

    def close(self):
        """
        Close log stream and stream_lock. """
        self._console_log("In close()", stack=True)
        try:
            self._close()
        finally:
            self._do_file_unlock()
            self.stream_lock = None
            super(ConcurrentRotatingFileHandler, self).close()

    def _degrade(self, degrade, msg, *args):
        """ Set degrade mode or not.  Ignore msg. """
        self._rotateFailed = degrade
        del msg, args  # avoid pychecker warnings

    def _degrade_debug(self, degrade, msg, *args):
        """ A more colorful version of _degade(). (This is enabled by passing
        "debug=True" at initialization).
        """
        if degrade:
            if not self._rotateFailed:
                sys.stderr.write("Degrade mode - ENTERING - (pid=%d)  %s\n" %
                                 (os.getpid(), msg % args))
                self._rotateFailed = True
        else:
            if self._rotateFailed:
                # self._console_log("Exiting degrade")
                sys.stderr.write("Degrade mode - EXITING  - (pid=%d)   %s\n" %
                                 (os.getpid(), msg % args))
                self._rotateFailed = False

    def doRollover(self):
        """
        Do a rollover, as described in __init__().
        """
        self._close()
        if self.backupCount <= 0:
            # Don't keep any backups, just overwrite the existing backup file
            # Locking doesn't much matter here; since we are overwriting it anyway
            self.stream = self._open("w")
            return
        try:
            # Determine if we can rename the log file or not. Windows refuses to
            # rename an open file, Unix is inode base so it doesn't care.

            # Attempt to rename logfile to tempname:
            # There is a slight race-condition here, but it seems unavoidable
            tmpname = None
            while not tmpname or os.path.exists(tmpname):
                tmpname = "%s.rotate.%08d" % (self.baseFilename, randbits(64))
            try:
                # Do a rename test to determine if we can successfully rename the log file
                os.rename(self.baseFilename, tmpname)
                if self.use_gzip:
                    self.do_gzip(tmpname)
            except (IOError, OSError):
                exc_value = sys.exc_info()[1]
                self._console_log(
                    "rename failed.  File in use? exception=%s" % (exc_value,))
                self._degrade(
                    True, "rename failed.  File in use? exception=%s", exc_value)
                return

            gzip_ext = ''
            if self.use_gzip:
                gzip_ext = '.gz'

            def do_rename(source_fn, dest_fn):
                self._console_log("Rename %s -> %s" % (source_fn, dest_fn + gzip_ext))
                if os.path.exists(dest_fn):
                    os.remove(dest_fn)
                if os.path.exists(dest_fn + gzip_ext):
                    os.remove(dest_fn + gzip_ext)
                source_gzip = source_fn + gzip_ext
                if os.path.exists(source_gzip):
                    os.rename(source_gzip, dest_fn + gzip_ext)
                elif os.path.exists(source_fn):
                    os.rename(source_fn, dest_fn)

            # Q: Is there some way to protect this code from a KeyboardInterrupt?
            # This isn't necessarily a data loss issue, but it certainly does 
            # break the rotation process during stress testing.

            # There is currently no mechanism in place to handle the situation
            # where one of these log files cannot be renamed. (Example, user
            # opens "logfile.3" in notepad); we could test rename each file, but
            # nobody's complained about this being an issue; so the additional
            # code complexity isn't warranted.
            for i in range(self.backupCount - 1, 0, -1):
                sfn = "%s.%d" % (self.baseFilename, i)
                dfn = "%s.%d" % (self.baseFilename, i + 1)
                if os.path.exists(sfn + gzip_ext):
                    do_rename(sfn, dfn)
            dfn = self.baseFilename + ".1"
            do_rename(tmpname, dfn)
            self._console_log("Rotation completed")
            self._degrade(False, "Rotation completed")
        finally:
            # Re-open the output stream, but if "delay" is enabled then wait
            # until the next emit() call. This could reduce rename contention in
            # some usage patterns.
            if not self.delay:
                self.stream = self._open()

    def shouldRollover(self, record):
        """
        Determine if rollover should occur.

        For those that are keeping track. This differs from the standard
        library's RotatingLogHandler class. Because there is no promise to keep
        the file size under maxBytes we ignore the length of the current record.
        """
        del record  # avoid pychecker warnings
        # Is stream is not yet open, skip rollover check. (Check will occur on
        # next message, after emit() calls _open())
        if self.stream is None:
            return False
        if self._shouldRollover():
            # If some other process already did the rollover (which is possible
            # on Unix) the file our stream may now be named "log.1", thus
            # triggering another rollover. Avoid this by closing and opening
            # "log" again.
            self._close()
            self.stream = self._open()
            return self._shouldRollover()
        return False

    def _shouldRollover(self):
        if self.maxBytes > 0:  # are we rolling over?
            self.stream.seek(0, 2)  # due to non-posix-compliant Windows feature
            if self.stream.tell() >= self.maxBytes:
                return True
            else:
                self._degrade(False, "Rotation done or not needed at this time")
        return False

    def do_gzip(self, input_filename):
        if not gzip:
            self._console_log("#no gzip available", stack=False)
            return
        out_filename = input_filename + ".gz"
        # TODO: we probably need to buffer large files here to avoid memory problems
        with open(input_filename, "rb") as input_fh:
            with gzip.open(out_filename, "wb") as gzip_fh:
                gzip_fh.write(input_fh.read())
        os.remove(input_filename)
        self._console_log("#gzipped: %s" % (out_filename,), stack=False)
        return


# Publish this class to the "logging.handlers" module so that it can be use
# from a logging config file via logging.config.fileConfig().
import logging.handlers

logging.handlers.ConcurrentRotatingFileHandler = ConcurrentRotatingFileHandler
