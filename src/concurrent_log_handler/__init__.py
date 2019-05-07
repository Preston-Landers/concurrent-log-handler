#!/usr/bin/env python
# -*- coding: utf-8; mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vim: fileencoding=utf-8 tabstop=4 expandtab shiftwidth=4
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

This module supports Python 2.6 and later.

"""

import io
import os
import sys
import time
import traceback
import warnings
from contextlib import contextmanager
from logging import LogRecord
from logging.handlers import BaseRotatingHandler

from portalocker import LOCK_EX, lock, unlock

try:
    import pwd
    import grp
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

__version__ = '0.9.16'
__author__ = "Preston Landers <planders@gmail.com>"
# __author__ = "Lowell Alleman"
__all__ = [
    "ConcurrentRotatingFileHandler",
]

PY2 = False
if sys.version_info[0] == 2:
    PY2 = True


# Workaround for handleError() in Python 2.7+ where record is written to stderr
# TODO: unused - probably can delete now.
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

    def __init__(
            self, filename, mode='a', maxBytes=0, backupCount=0,
            encoding=None, debug=False, delay=None, use_gzip=False,
            owner=None, chmod=None, umask=None, newline=None, terminator="\n",
            unicode_error_policy='ignore',
    ):
        """
        Open the specified file and use it as the stream for logging.

        :param filename: name of the log file to output to.
        :param mode: write mode: defaults to 'a' for text append
        :param maxBytes: rotate the file at this size in bytes
        :param backupCount: number of rotated files to keep before deleting.
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
        self.stream = None
        self.stream_lock = None
        self.owner = owner
        self.chmod = chmod
        self.umask = umask
        self._set_uid = None
        self._set_gid = None
        self.use_gzip = True if gzip and use_gzip else False
        self._rotateFailed = False
        self.maxBytes = maxBytes
        self.backupCount = backupCount
        self.newline = newline

        self._debug = debug
        self.use_gzip = True if gzip and use_gzip else False
        self.gzip_buffer = 8096

        if unicode_error_policy not in ('ignore', 'replace', 'strict'):
            unicode_error_policy = 'ignore'
            warnings.warn(
                "Invalid unicode_error_policy for concurrent_log_handler: "
                "must be ignore, replace, or strict. Defaulting to ignore.",
                UserWarning)
        self.unicode_error_policy = unicode_error_policy

        if delay not in (None, True):
            warnings.warn(
                'parameter delay is now ignored and implied as True, '
                'please remove from your config.',
                DeprecationWarning)

        # Construct the handler with the given arguments in "delayed" mode
        # because we will handle opening the file as needed. File name
        # handling is done by FileHandler since Python 2.5.
        super(ConcurrentRotatingFileHandler, self).__init__(
            filename, mode, encoding=encoding, delay=True)

        self.terminator = terminator or "\n"

        if owner and os.chown and pwd and grp:
            self._set_uid = pwd.getpwnam(self.owner[0]).pw_uid
            self._set_gid = grp.getgrnam(self.owner[1]).gr_gid

        self.lockFilename = self.getLockFilename()
        self.is_locked = False

    def getLockFilename(self):
        """
        Decide the lock filename. If the logfile is file.log, then we use `.__file.lock` and
        not `file.log.lock`. This only removes the extension if it's `*.log`.

        :return: the path to the lock file.
        """
        if self.baseFilename.endswith(".log"):
            lock_file = self.baseFilename[:-4]
        else:
            lock_file = self.baseFilename
        lock_file += ".lock"
        lock_path, lock_name = os.path.split(lock_file)
        # hide the file on Unix and generally from file completion
        lock_name = ".__" + lock_name
        return os.path.join(lock_path, lock_name)

    def _open_lockfile(self):
        if self.stream_lock and not self.stream_lock.closed:
            self._console_log("Lockfile already open in this process")
            return
        lock_file = self.lockFilename
        self._console_log(
            "concurrent-log-handler %s opening %s" % (hash(self), lock_file), stack=False)

        with self._alter_umask():
            self.stream_lock = open(lock_file, "wb", buffering=0)

        self._do_chown_and_chmod(lock_file)

    def _open(self, mode=None):
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
            stream = io.open(
                self.baseFilename, mode=mode, encoding=self.encoding, newline=self.newline)

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
        asctime = time.asctime()
        print("[%s %s %s] %s%s" % (tid, pid, asctime, msg, stack_str,))

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
                self._do_lock()

                try:
                    if self.shouldRollover(record):
                        self.doRollover()
                except Exception as e:
                    self._console_log("Unable to do rollover: %s" % (e,), stack=True)
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

        if PY2:
            self.do_write_py2(msg)
        else:
            msg = msg + self.terminator
            try:
                stream.write(msg)
            except UnicodeError:
                # Try to emit in a form acceptable to the output encoding
                # The unicode_error_policy determines whether this is lossy.
                try:
                    encoding = getattr(stream, 'encoding', self.encoding or 'us-ascii')
                    msg_bin = msg.encode(encoding, self.unicode_error_policy)
                    msg = msg_bin.decode(encoding, self.unicode_error_policy)
                    stream.write(msg)
                except UnicodeError:
                    # self._console_log(str(e))
                    raise

        stream.flush()
        self._close()
        return

    # noinspection PyCompatibility,PyUnresolvedReferences
    def do_write_py2(self, msg):
        stream = self.stream
        term = self.terminator
        policy = self.unicode_error_policy

        encoding = getattr(stream, 'encoding', None)

        # as far as I can tell, this should always be set from io.open, but just in case...
        if not encoding:
            if not self.encoding:
                self._console_log(
                    "Warning, unable to determine encoding of logging stream; assuming utf-8")
            encoding = self.encoding or 'utf-8'

        if not isinstance(msg, unicode):
            msg = unicode(msg, encoding, policy)

        # Add in the terminator.
        if not isinstance(term, unicode):
            term = unicode(term, encoding, policy)
        msg = msg + term
        stream.write(msg)

    def _do_lock(self):
        if self.is_locked:
            return   # already locked... recursive?
        self._open_lockfile()
        if self.stream_lock:
            for i in range(10):
                # noinspection PyBroadException
                try:
                    lock(self.stream_lock, LOCK_EX)
                    self.is_locked = True
                    break
                except Exception:
                    continue
            else:
                raise RuntimeError("Cannot acquire lock after 10 attempts")
        else:
            self._console_log("No self.stream_lock to lock", stack=True)

    def _do_unlock(self):
        if self.stream_lock:
            if self.is_locked:
                unlock(self.stream_lock)
                self.is_locked = False
            self.stream_lock.close()
            self.stream_lock = None
        else:
            self._console_log("No self.stream_lock to unlock", stack=True)

    def close(self):
        """
        Close log stream and stream_lock. """
        self._console_log("In close()", stack=True)
        try:
            self._close()
        finally:
            super(ConcurrentRotatingFileHandler, self).close()

    def doRollover(self):
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
                "rename failed.  File in use? exception=%s" % (exc_value,), stack=True)
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

        if self.use_gzip:
            logFilename = self.baseFilename + ".1.gz"
            self._do_chown_and_chmod(logFilename)

        self._console_log("Rotation completed")

    def shouldRollover(self, record):
        """
        Determine if rollover should occur.

        For those that are keeping track. This differs from the standard
        library's RotatingLogHandler class. Because there is no promise to keep
        the file size under maxBytes we ignore the length of the current record.
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

        with open(input_filename, "rb") as input_fh:
            with gzip.open(out_filename, "wb") as gzip_fh:
                while True:
                    data = input_fh.read(self.gzip_buffer)
                    if not data:
                        break
                    gzip_fh.write(data)

        os.remove(input_filename)
        self._console_log("#gzipped: %s" % (out_filename,), stack=False)
        return

    def _do_chown_and_chmod(self, filename):
        if self._set_uid and self._set_gid:
            os.chown(filename, self._set_uid, self._set_gid)

        if self.chmod and os.chmod:
            os.chmod(filename, self.chmod)


# Publish this class to the "logging.handlers" module so that it can be use
# from a logging config file via logging.config.fileConfig().
import logging.handlers

logging.handlers.ConcurrentRotatingFileHandler = ConcurrentRotatingFileHandler
