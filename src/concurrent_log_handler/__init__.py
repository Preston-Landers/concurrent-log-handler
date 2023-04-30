#!/usr/bin/env python
#
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

"""concurrent_log_handler: A smart replacement for the standard RotatingFileHandler

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

This module supports Python 3.6 and later.
(Support for older version was dropped in 0.9.23.)
"""

import errno
import os
import sys
import time
import traceback
import warnings
from contextlib import contextmanager
from logging.handlers import BaseRotatingHandler, TimedRotatingFileHandler

# noinspection PyPackageRequirements
from portalocker import LOCK_EX, lock, unlock

from concurrent_log_handler.__version__ import __author__, __version__  # noqa: F401

try:
    import grp
    import pwd
except ImportError:
    pwd = grp = None

# Random numbers for rotation temp file names, using secrets module if available (Python 3.6).
# Otherwise use `random.SystemRandom` if available, then fall back on `random.Random`.
try:
    # noinspection PyPackageRequirements,PyCompatibility
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

__all__ = [
    "ConcurrentRotatingFileHandler",
    "ConcurrentTimedRotatingFileHandler",
]

has_chown = hasattr(os, "chown")
has_chmod = hasattr(os, "chmod")


class ConcurrentRotatingFileHandler(BaseRotatingHandler):
    """Handler for logging to a set of files, which switches from one file to the
    next when the current file reaches a certain size. Multiple processes can
    write to the log file concurrently, but this may mean that the file will
    exceed the given size.
    """

    def __init__(  # noqa: PLR0913
        self,
        filename,
        mode="a",
        maxBytes=0,
        backupCount=0,
        encoding=None,
        debug=False,
        delay=None,
        use_gzip=False,
        owner=None,
        chmod=None,
        umask=None,
        newline=None,
        terminator="\n",
        unicode_error_policy="ignore",
        lock_file_directory=None,
    ):
        """Open the specified file and use it as the stream for logging.

        :param filename: name of the log file to output to.
        :param mode: write mode: defaults to 'a' for text append
        :param maxBytes: rotate the file at this size in bytes
        :param backupCount: number of rotated files to keep before deleting.
            Avoid setting this very high, probably 20 or less, and prefer setting maxBytes higher.
            A very large number of rollover files can slow down the rollover enough to cause
            problems due to the mass file renaming while the main lock is held.
        :param encoding: text encoding for logfile
        :param debug: add extra debug statements to this class (for development)
        :param delay: DEPRECATED: value is ignored
        :param use_gzip: automatically gzip rotated logs if available.
        :param owner: 2 element sequence with (user owner, group owner) of log files.  (Unix only)
        :param chmod: permission of log files.  (Unix only)
        :param umask: umask settings to temporarily make when creating log files.
            This is an alternative to chmod. It is mainly for Unix systems but
            can also be used on Windows. The Windows security model is more complex
            and this is not the same as changing access control entries.
        :param newline: None (default): use CRLF on Windows, LF on Unix. Set to '' for
        no translation, in which case the 'terminator' argument determines the line ending.
        :param terminator: set to '\r\n' along with newline='' to force Windows style
        newlines regardless of OS platform.
        :param unicode_error_policy: should be one of 'ignore', 'replace', 'strict'
        Determines what happens when a message is written to the log that the stream encoding
        doesn't support. Default is to ignore, i.e., drop the unusable characters.
        :param lock_file_directory: name of directory for all lock files as alternative
        living space; this is useful for when the main log files reside in a cloud synced
        drive like Dropbox, OneDrive, Google Docs, etc., which may prevent the lock files
        from working correctly. The lock file must be accessible to all processes writing
        to a shared log, including across all different hosts (machines).

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

        This log handler assumes that all concurrent processes logging to a
        single file will are using only this class, and that the exact same
        parameters are provided to each instance of this class.  If, for
        example, two different processes are using this class, but with
        different values for 'maxBytes' or 'backupCount', then odd behavior is
        expected. The same is true if this class is used by one application, but
        the RotatingFileHandler is used by another.
        """
        # noinspection PyTypeChecker
        self.stream = None
        self.stream_lock = None
        self.owner = owner
        self.chmod = chmod
        self.umask = umask
        self._set_uid = None
        self._set_gid = None
        self._rotateFailed = False
        self.maxBytes = maxBytes
        self.backupCount = backupCount
        self.newline = newline

        self._debug = debug
        self.use_gzip = bool(gzip and use_gzip)
        self.gzip_buffer = 8096
        self.maxLockAttempts = 20

        if unicode_error_policy not in ("ignore", "replace", "strict"):
            unicode_error_policy = "ignore"
            warnings.warn(
                "Invalid unicode_error_policy for concurrent_log_handler: "
                "must be ignore, replace, or strict. Defaulting to ignore.",
                UserWarning,
                stacklevel=3,
            )
        self.unicode_error_policy = unicode_error_policy

        if delay not in (None, True):
            warnings.warn(
                "concurrent_log_handler parameter `delay` is now ignored and implied as True, "
                "please remove from your config.",
                DeprecationWarning,
                stacklevel=3,
            )

        # Construct the handler with the given arguments in "delayed" mode
        # because we will handle opening the file as needed. File name
        # handling is done by FileHandler since Python 2.5.
        super(ConcurrentRotatingFileHandler, self).__init__(
            filename, mode, encoding=encoding, delay=True
        )

        self.terminator = terminator or "\n"

        if owner and has_chown and pwd and grp:
            # noinspection PyUnresolvedReferences
            self._set_uid = pwd.getpwnam(self.owner[0]).pw_uid
            # noinspection PyUnresolvedReferences
            self._set_gid = grp.getgrnam(self.owner[1]).gr_gid

        self.lockFilename = self.getLockFilename(lock_file_directory)
        self.is_locked = False

        # This is primarily for the benefit of the unit tests.
        self.num_rollovers = 0

    def getLockFilename(self, lock_file_directory):
        """
        Decide the lock filename. If the logfile is file.log, then we use `.__file.lock` and
        not `file.log.lock`. This only removes the extension if it's `*.log`.

        :param lock_file_directory: name of the directory for alternative living space of lock files
        :return: the path to the lock file.
        """
        lock_path, lock_name = self.baseLockFilename(self.baseFilename)
        if lock_file_directory:
            self.__create_lock_directory__(lock_file_directory)
            return os.path.join(lock_file_directory, lock_name)
        return os.path.join(lock_path, lock_name)

    @staticmethod
    def baseLockFilename(baseFilename):
        lock_file = baseFilename[:-4] if baseFilename.endswith(".log") else baseFilename
        lock_file += ".lock"
        lock_path, lock_name = os.path.split(lock_file)
        # hide the file on Unix and generally from file completion
        return lock_path, ".__" + lock_name

    @staticmethod
    def __create_lock_directory__(lock_file_directory):
        if not os.path.exists(lock_file_directory):
            try:
                os.makedirs(lock_file_directory)
            except OSError as err:
                if err.errno != errno.EEXIST:
                    # If directory already exists, then we're done. Everything else is fishy...
                    raise

    def _open_lockfile(self):
        if self.stream_lock and not self.stream_lock.closed:
            self._console_log("Lockfile already open in this process")
            return
        lock_file = self.lockFilename
        # self._console_log(
        #     f"concurrent-log-handler {hash(self)} opening {lock_file}",
        #     stack=False,
        # )

        with self._alter_umask():
            self.stream_lock = self.atomic_open(lock_file)

        self._do_chown_and_chmod(lock_file)

    def atomic_open(self, file_path):
        try:
            # Attempt to open the file in "r+" mode
            file = open(file_path, "r+", encoding=self.encoding, newline=self.newline)
        except FileNotFoundError:
            # If the file doesn't exist, create it atomically and open in "r+" mode
            try:
                fd = os.open(file_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                file = open(fd, "r+", encoding=self.encoding, newline=self.newline)
            except FileExistsError:
                # If the file was created between the first check and our attempt to create it, open it in "r+" mode
                file = open(
                    file_path, "r+", encoding=self.encoding, newline=self.newline
                )
        return file

    def _open(self, mode=None):  # noqa: ARG002
        # Normally we don't hold the stream open. Only do_open does that
        # which is called from do_write().
        return None

    def do_open(self, mode=None):
        """
        Open the current base file with the (original) mode and encoding.
        Return the resulting stream.

        Note:  Copied from stdlib.  Added option to override 'mode'
        """
        if mode is None:
            mode = self.mode

        with self._alter_umask():
            # noinspection PyArgumentList
            stream = open(
                self.baseFilename,
                mode=mode,
                encoding=self.encoding,
                newline=self.newline,
            )

        self._do_chown_and_chmod(self.baseFilename)

        return stream

    @contextmanager
    def _alter_umask(self):
        """Temporarily alter umask to custom setting, if applicable"""
        if self.umask is None:
            yield  # nothing to do
        else:
            prev_umask = os.umask(self.umask)
            try:
                yield
            finally:
                os.umask(prev_umask)

    def _close(self):
        """Close file stream.  Unlike close(), we don't tear anything down, we
        expect the log to be re-opened after rotation."""

        if self.stream:
            try:
                if not self.stream.closed:
                    # Flushing probably isn't technically necessary, but it feels right
                    self.stream.flush()
                    self.stream.close()
            finally:
                # noinspection PyTypeChecker
                self.stream = None

    def _console_log(self, msg, stack=False):
        if not self._debug:
            return
        import threading

        tid = threading.current_thread().name
        pid = os.getpid()
        stack_str = ""
        if stack:
            stack_str = ":\n" + "".join(traceback.format_stack())
        asctime = time.asctime()
        print(f"[{tid} {pid} {asctime}] {msg}{stack_str}")

    def emit(self, record):
        """Emit a record.

        Override from parent class to handle file locking for the duration of rollover and write.
        This also does the formatting *before* locks are obtained, in case the format itself does
        logging calls from within. Rollover also occurs while the lock is held.
        """
        # noinspection PyBroadException
        try:
            msg = self.format(record)
            try:
                self._do_lock()

                try:
                    if self.shouldRollover(record):
                        self.doRollover()
                except Exception as e:
                    self._console_log(
                        f"Unable to do rollover: {e}\n{traceback.format_exc()}"
                    )
                    # Continue on anyway

                self.do_write(msg)

            finally:
                self._do_unlock()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            self.handleError(record)

    def flush(self):
        """Does nothing; stream is flushed on each write."""
        return

    def do_write(self, msg):
        """Handling writing an individual record; we do a fresh open every time.
        This assumes emit() has already locked the file."""
        self.stream = self.do_open()
        stream = self.stream

        msg = msg + self.terminator
        try:
            stream.write(msg)
        except UnicodeError:
            # Try to emit in a form acceptable to the output encoding
            # The unicode_error_policy determines whether this is lossy.
            try:
                encoding = getattr(stream, "encoding", self.encoding or "us-ascii")
                msg_bin = msg.encode(encoding, self.unicode_error_policy)
                msg = msg_bin.decode(encoding, self.unicode_error_policy)
                stream.write(msg)
            except UnicodeError:
                # self._console_log(str(e))
                raise

        stream.flush()
        self._close()
        return

    def _do_lock(self):
        if self.is_locked:
            return  # already locked... recursive?
        self._open_lockfile()
        if self.stream_lock:
            for _i in range(self.maxLockAttempts):
                # noinspection PyBroadException
                try:
                    lock(self.stream_lock, LOCK_EX)
                    self.is_locked = True
                    # self._console_log("Acquired lock")
                    break
                except Exception:  # noqa: S112
                    continue
            else:
                raise RuntimeError(
                    f"Cannot acquire lock after {self.maxLockAttempts} attempts"
                )
        else:
            self._console_log("No self.stream_lock to lock", stack=True)

    def _do_unlock(self):
        if self.stream_lock:
            if self.is_locked:
                try:
                    unlock(self.stream_lock)
                    # self._console_log("Released lock")
                finally:
                    self.is_locked = False
                    self.stream_lock.close()
                    self.stream_lock = None
        else:
            self._console_log("No self.stream_lock to unlock", stack=True)

    def close(self):
        """Close log stream and stream_lock."""
        self._console_log("In close()", stack=True)
        try:
            self._close()
        finally:
            super(ConcurrentRotatingFileHandler, self).close()

    def doRollover(self):  # noqa: C901
        """
        Do a rollover, as described in __init__().
        """
        self._close()
        if self.backupCount <= 0:
            # Don't keep any backups, just overwrite the existing backup file
            # Locking doesn't much matter here; since we are overwriting it anyway
            self.stream = self.do_open("w")
            self._close()
            return

        # Determine if we can rename the log file or not. Windows refuses to
        # rename an open file, Unix is inode based, so it doesn't care.

        # Attempt to rename logfile to tempname:
        # There is a slight race-condition here, but it seems unavoidable
        tmpname = None
        while not tmpname or os.path.exists(tmpname):
            tmpname = f"{self.baseFilename}.rotate.{randbits(64):08}"
        try:
            # Do a rename test to determine if we can successfully rename the log file
            os.rename(self.baseFilename, tmpname)

            if self.use_gzip:
                self.do_gzip(tmpname)
        except OSError as e:
            self._console_log(f"rename failed.  File in use? e={e}", stack=True)
            return

        gzip_ext = ".gz" if self.use_gzip else ""

        def do_rename(source_fn, dest_fn):
            self._console_log(f"Rename {source_fn} -> {dest_fn + gzip_ext}")
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

        do_renames = []
        for i in range(1, self.backupCount):
            sfn = self.rotation_filename(f"{self.baseFilename}.{i}")
            dfn = self.rotation_filename(f"{self.baseFilename}.{i + 1}")
            if os.path.exists(sfn + gzip_ext):
                do_renames.append((sfn, dfn))
            else:
                # Break looking for more rollover files as soon as we can't find one
                # at the expected name.
                break

        for sfn, dfn in reversed(do_renames):
            do_rename(sfn, dfn)

        dfn = self.rotation_filename(self.baseFilename + ".1")
        do_rename(tmpname, dfn)

        if self.use_gzip:
            logFilename = self.baseFilename + ".1.gz"
            self._do_chown_and_chmod(logFilename)

        self.num_rollovers += 1
        self._console_log("Rotation completed (on size)")

    # noinspection PyUnusedLocal
    def shouldRollover(self, record):
        """
        Determine if rollover should occur.

        For those that are keeping track. This differs from the standard
        library's RotatingLogHandler class. Because there is no promise to keep
        the file size under maxBytes we ignore the length of the current record.
        TODO: should we reconsider this and make it more exact?
        """
        del record  # avoid pychecker warnings
        return self._shouldRollover()

    def _shouldRollover(self):
        if self.maxBytes > 0:  # are we rolling over?
            self.stream = self.do_open()
            try:
                self.stream.seek(0, 2)  # due to non-posix-compliant Windows feature
                if self.stream.tell() >= self.maxBytes:
                    return True
            finally:
                self._close()
        return False

    def do_gzip(self, input_filename):
        if not gzip:
            self._console_log("#no gzip available", stack=False)
            return
        out_filename = input_filename + ".gz"

        with open(input_filename, "rb") as input_fh, gzip.open(
            out_filename, "wb"
        ) as gzip_fh:
            while True:
                data = input_fh.read(self.gzip_buffer)
                if not data:
                    break
                gzip_fh.write(data)

        os.remove(input_filename)
        self._console_log(f"#gzipped: {out_filename}", stack=False)
        return

    def _do_chown_and_chmod(self, filename):
        if has_chown and self._set_uid is not None and self._set_gid is not None:
            os.chown(filename, self._set_uid, self._set_gid)

        if has_chmod and self.chmod is not None:
            os.chmod(filename, self.chmod)


# noinspection PyProtectedMember
class ConcurrentTimedRotatingFileHandler(TimedRotatingFileHandler):
    """A time-based rotating log handler that supports concurrent access across
    multiple processes or hosts (using logs on a shared network drive).

    You can also include size-based rotation by setting maxBytes > 0.
    WARNING: if you only want time-based rollover and NOT also size-based, set maxBytes=0,
    which is already the default.
    Please note that when size-based rotation is done, it still uses the naming scheme
    of the time-based rotation. If multiple rotations had to be done within the timeframe of
    the time-based rollover name, then a number like ".1" will be appended to the end of the name.

    Note that `errors` is ignored unless using Python 3.9 or later.
    """

    def __init__(  # noqa: PLR0913
        self,
        filename,
        when="h",
        interval=1,
        backupCount=0,
        encoding=None,
        delay=False,
        utc=False,
        atTime=None,
        errors=None,
        maxBytes=0,
        use_gzip=False,
        owner=None,
        chmod=None,
        umask=None,
        newline=None,
        terminator="\n",
        unicode_error_policy="ignore",
        lock_file_directory=None,
        **kwargs,
    ):
        if "mode" in kwargs:
            del kwargs["mode"]
        trfh_kwargs = {}
        if sys.version_info >= (3, 9):
            trfh_kwargs["errors"] = errors
        TimedRotatingFileHandler.__init__(
            self,
            filename,
            when=when,
            interval=interval,
            backupCount=backupCount,
            encoding=encoding,
            delay=delay,
            utc=utc,
            atTime=atTime,
            **trfh_kwargs,
        )
        self.clh = ConcurrentRotatingFileHandler(
            filename,
            mode="a",
            backupCount=backupCount,
            encoding=encoding,
            delay=delay,
            maxBytes=maxBytes,
            use_gzip=use_gzip,
            owner=owner,
            chmod=chmod,
            umask=umask,
            newline=newline,
            terminator=terminator,
            unicode_error_policy=unicode_error_policy,
            lock_file_directory=lock_file_directory,
            **kwargs,
        )
        self.num_rollovers = 0
        self.__internal_close()
        self.initialize_rollover_time()

    def __internal_close(self):
        # Don't need or want to hold the main logfile handle open unless we're actively writing to it.
        if self.stream:
            self.stream.close()
            # noinspection PyTypeChecker
            self.stream = None

    def _console_log(self, msg, stack=False):
        self.clh._console_log(msg, stack=stack)

    def emit(self, record):
        """
        Emit a record.

        Override from parent class to handle file locking for the duration of rollover and write.
        This also does the formatting *before* locks are obtained, in case the format itself does
        logging calls from within. Rollover also occurs while the lock is held.
        """
        # noinspection PyBroadException
        try:
            msg = self.format(record)
            try:
                self.clh._do_lock()

                try:
                    if self.shouldRollover(record):
                        self.doRollover()
                except Exception as e:
                    self._console_log(
                        "Unable to do rollover: {}\n{}".format(
                            e, traceback.format_exc()
                        )
                    )
                    # time.sleep(1000)

                self.clh.do_write(msg)

            finally:
                self.clh._do_unlock()
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            self.handleError(record)

    def read_rollover_time(self):
        # Lock must be held before calling this method
        lock_file = self.clh.stream_lock
        if not lock_file or not self.clh.is_locked:
            # Lock is not being held?
            self._console_log(
                "No rollover time (lock) file to read from. Lock is not held?"
            )
            return
        try:
            lock_file.seek(0)
            raw_time = lock_file.read()
        except OSError:
            self.rolloverAt = 0
            self._console_log(f"Couldn't read rollover time from file {lock_file!r}")
            return
        try:
            self.rolloverAt = int(raw_time.strip())
            # self._console_log(
            #     f"Read rollover time: {self.rolloverAt} - raw: {raw_time!r}"
            # )
        except ValueError:
            self.rolloverAt = 0
            self._console_log(f"Couldn't read rollover time from file: {raw_time!r}")

    def write_rollover_time(self):
        """Write the next rollover time (current value of self.rolloverAt) to the lock file."""
        lock_file = self.clh.stream_lock
        if not lock_file or not self.clh.is_locked:
            self._console_log(
                "No rollover time (lock) file to write to. Lock is not held?"
            )
            return
        lock_file.seek(0)
        lock_file.write(str(self.rolloverAt))
        lock_file.truncate()
        lock_file.flush()
        os.fsync(lock_file.fileno())
        self._console_log(f"Wrote rollover time: {self.rolloverAt}")

    def initialize_rollover_time(self):
        """Run by the __init__ to read an existing rollover time from the lockfile,
        and if it can't do that, compute and write a new one."""
        try:
            self.clh._do_lock()
            self.read_rollover_time()
            self._console_log(f"Initializing; reading rollover time: {self.rolloverAt}")
            if self.rolloverAt != 0:
                return
            current_time = int(time.time())
            new_rollover_at = self.computeRollover(current_time)
            while new_rollover_at <= current_time:
                new_rollover_at += self.interval
            self.rolloverAt = new_rollover_at
            self.write_rollover_time()

            self._console_log(f"Set initial rollover time: {self.rolloverAt}")
        finally:
            self.clh._do_unlock()

    def shouldRollover(self, record):
        """Determine if the rollover should occur."""
        # Read the latest rollover time from the file
        self.read_rollover_time()

        do_rollover = False
        if super(ConcurrentTimedRotatingFileHandler, self).shouldRollover(record):
            self._console_log("Rolling over because of time")
            do_rollover = True
        elif self.clh.shouldRollover(record):
            self.clh._console_log("Rolling over because of size")
            do_rollover = True
        if do_rollover:
            return True
        return False

    def doRollover(self):  # noqa: C901, PLR0912
        """
        do a rollover; in this case, a date/time stamp is appended to the filename
        when the rollover happens.  However, you want the file to be named for the
        start of the interval, not the current time.  If there is a backup count,
        then we have to get a list of matching filenames, sort them and remove
        the one with the oldest suffix.

        This code was adapted from the TimedRotatingFileHandler class from Python 3.11.
        """
        self.clh._close()
        self.__internal_close()

        # get the time that this sequence started at and make it a TimeTuple
        currentTime = int(time.time())
        dstNow = time.localtime(currentTime)[-1]
        t = self.rolloverAt - self.interval
        if self.utc:
            timeTuple = time.gmtime(t)
        else:
            timeTuple = time.localtime(t)
            dstThen = timeTuple[-1]
            if dstNow != dstThen:
                addend = 3600 if dstNow else -3600
                timeTuple = time.localtime(t + addend)
        dfn = self.rotation_filename(
            self.baseFilename + "." + time.strftime(self.suffix, timeTuple)
        )

        gzip_ext = ".gz" if self.clh.use_gzip else ""

        counter = 0
        while os.path.exists(dfn + gzip_ext):
            counter += 1
            ending = f".{counter-1}{gzip_ext}"
            if dfn.endswith(ending):
                dfn = dfn[: -len(ending)]
            dfn = f"{dfn}.{counter}"

        # if os.path.exists(dfn):
        #     os.remove(dfn)

        self.rotate(self.baseFilename, dfn)

        if self.clh.use_gzip:
            self.clh.do_gzip(dfn)

        if self.backupCount > 0:
            for s in self.getFilesToDelete():
                os.remove(s + gzip_ext)
        # if not self.delay:
        #     self.stream = self._open()
        newRolloverAt = self.computeRollover(currentTime)
        while newRolloverAt <= currentTime:
            newRolloverAt = newRolloverAt + self.interval
        # If DST changes and midnight or weekly rollover, adjust for this.
        if (self.when == "MIDNIGHT" or self.when.startswith("W")) and not self.utc:
            dstAtRollover = time.localtime(newRolloverAt)[-1]
            if dstNow != dstAtRollover:
                if not dstNow:  # noqa: SIM108
                    # DST kicks in before next rollover, so we need to deduct an hour
                    addend = -3600
                else:
                    # DST bows out before next rollover, so we need to add an hour
                    addend = 3600
                newRolloverAt += addend
        self.num_rollovers += 1
        self.rolloverAt = newRolloverAt
        self.write_rollover_time()
        self._console_log(f"Rotation completed (on time) {dfn}")


# Publish these classes to the "logging.handlers" module, so they can be used
# from a logging config file via logging.config.fileConfig().
import logging.handlers  # noqa: E402

logging.handlers.ConcurrentRotatingFileHandler = ConcurrentRotatingFileHandler
logging.handlers.ConcurrentTimedRotatingFileHandler = ConcurrentTimedRotatingFileHandler
