# portalocker.py - Cross-platform (posix/nt) API for flock-style file locking.
#                  Requires python 1.5.2 or better.
"""Cross-platform (posix/nt) API for flock-style file locking.

TODO: possibly change to use this: https://github.com/WoLpH/portalocker

Synopsis:

   from concurrent_log_handler import portalocker
   file = open("somefile", "r+")
   portalocker.lock(file, portalocker.LOCK_EX)
   file.seek(12)
   file.write("foo")
   file.close()

If you know what you're doing, you may choose to

   portalocker.unlock(file)

before closing the file, but why?

Methods:

   lock( file, flags )
   unlock( file )

Constants:

   LOCK_EX
   LOCK_SH
   LOCK_NB

Exceptions:

    LockException

Notes:

On Windows this requires PyWin32.

@WARNING: if obtaining an exclusive lock on a file you wish to write to, be sure to open the file
in "a" (append) mode if you wish to avoid accidentally deleting the contents of the file. You can
always seek(0) before writing to overwrite the previous contents once the lock is obtained.

@WARNING: the locks this module performs are ADVISORY locks only - the operating system does NOT
protect against processes violating these locks.


History:

I learned the win32 technique for locking files from sample code
provided by John Nielsen <nielsenjf@my-deja.com> in the documentation
that accompanies the win32 modules.

Author: Jonathan Feinberg <jdf@pobox.com>,
        Lowell Alleman <lalleman@mfps.com>,
        Rick van Hattem <Rick.van.Hattem@Fawo.nl>
        Preston Landers <planders@gmail.com>
Version: 0.4
URL:  https://github.com/WoLpH/portalocker
"""

import os
import sys
import time
import unittest

__all__ = [
    "lock",
    "unlock",
    "LOCK_EX",
    "LOCK_SH",
    "LOCK_NB",
    "LockException",
]


class LockException(RuntimeError):
    # Error codes:
    LOCK_FAILED = 1


class LockTimeoutException(RuntimeError):
    """
    readLockedFile will raise this when a lock acquisition attempt times out.
    """
    pass


if os.name == 'nt':
    try:
        import win32con
        import win32file
        import pywintypes
        import struct
    except ImportError as e:
        raise ImportError("PyWin32 must be installed to use this package. %s" % (e,))

    LOCK_EX = win32con.LOCKFILE_EXCLUSIVE_LOCK
    LOCK_SH = 0  # the default
    LOCK_NB = win32con.LOCKFILE_FAIL_IMMEDIATELY
    # is there any reason not to reuse the following structure?
    __overlapped = pywintypes.OVERLAPPED()


    def UnpackSigned32bitInt(hexnum):
        """Given a bytestring such as b'\xff\xff\x00\x00', interpret it as a SIGNED 32
        bit integer and return the result, -65536 in this case.

        This function was needed because somewhere along the line Python
        started interpreting a literal string like this (in source code)::

         >>> print(0xFFFF0000)
         -65536

        But then in Python 2.6 (or somewhere between 2.1 and 2.6) it started
        interpreting that as an unsigned int, which converted into a much
        larger longint.::

         >>> print(0xFFF0000)
         4294901760

        This allows the original behavior.  Currently needed to specify
        flags for Win32API locking calls.

        @TODO: candidate for docstring test cases!!"""

        # return struct.unpack(
        #     '!i', codecs.decode(hexnum, 'hex'))[0]
        return struct.unpack('!i', hexnum)[0]


    nNumberOfBytesToLockHigh = UnpackSigned32bitInt(b'\xff\xff\x00\x00')

elif os.name == 'posix':
    import fcntl

    LOCK_EX = fcntl.LOCK_EX
    LOCK_SH = fcntl.LOCK_SH
    LOCK_NB = fcntl.LOCK_NB
else:
    raise RuntimeError("portalocker only defined for nt and posix platforms")

if os.name == 'nt':
    def lock(file, flags):
        hfile = win32file._get_osfhandle(file.fileno())
        try:
            win32file.LockFileEx(hfile, flags, 0, nNumberOfBytesToLockHigh, __overlapped)
        except pywintypes.error as exc_value:
            # error: (33, 'LockFileEx', 'The process cannot access the file because another
            # process has locked a portion of the file.')
            if exc_value.winerror == 33:
                raise LockException(str(exc_value))
            else:
                # Q:  Are there exceptions/codes we should be dealing with here?
                raise


    def unlock(file):
        hfile = win32file._get_osfhandle(file.fileno())
        try:
            win32file.UnlockFileEx(hfile, 0, nNumberOfBytesToLockHigh, __overlapped)
        except pywintypes.error as exc_value:
            if exc_value.winerror == 158:
                # error: (158, 'UnlockFileEx', 'The segment is already unlocked.')
                # To match the 'posix' implementation, silently ignore this error
                pass
            else:
                # Q:  Are there exceptions/codes we should be dealing with here?
                raise

elif os.name == 'posix':
    def lock(file, flags):
        try:
            fcntl.flock(file, flags)
        except IOError as exc_value:
            # The exception code varies on different systems so we'll catch
            # every IO error
            raise LockException(str(exc_value))


    def unlock(file):
        fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def readLockedFile(
        filename,
        lockFilename=None,
        returnFilehandle=False,
        maxWaitTimeSec=30.0,
        sleepIntervalSec=0.05,
        binaryMode=True):
    """
    Reads the given filename after locking it against other writers (not
    other readers). By default returns the entire contents of file and
    then unlocks the file. Waits for up to 30 seconds (by default) to
    obtain a shared lock.  If the lock can't be obtained in this time, we
    raise a LockTimeoutException.  This used shared lock, which allows other
    concurrent readers, but not concurrent writers.  (Keep in mind this
    is an advisory lock only; it's possible to bypass these locks.)

    @param lockFilename: if the lock should be obtained on a separate
    "lock file" instead of locking the main file itself. As far as I can
    tell there is no real *need* to use a separate lockfile but wtconfig
    currently does.

    @param returnFilehandle: instead of returning the contents of the
    file, return the filehandle object so you can read at your leisure.
    Calling close() on the filehandle releases the lock so others can
    write to the file, and is your responsibility if you use this option.
    If you set this, you can't set lockFilename

    @param maxWaitTimeSec: default is to wait 30 seconds before raising
    a RuntimeError on failure to acquire the lock.

    @param sleepIntervalSec: amount of time to sleep (in seconds)
    between lock acquisition attempts.

    @param binaryMode: if False, open file in text mode if applicable.
    """

    fileMode = "rb"
    if not binaryMode:
        fileMode = "r"

    if lockFilename is None:
        lockFilename = filename
    elif returnFilehandle:
        # Can't allow this because to release the lock you'd have
        # to have a handle on the lockfile.
        msg = "You cannot set returnFilehandle and lockFilename at the same time."
        raise RuntimeError(msg)

    if not os.path.exists(lockFilename):
        raise IOError("Lock file does not exist: %s" % (lockFilename,))
    if not os.path.exists(filename):
        raise IOError("File does not exist: %s" % (filename,))

    obtainedLock = False
    lock_fileh = None
    giveUpTime = time.time() + maxWaitTimeSec
    while time.time() < giveUpTime:
        lock_fileh = open(lockFilename, fileMode)
        # sys.stderr.write("Attempting to lock %s.\n" % (lockFilename,))
        try:
            # Shared (read-only) lock with non-blocking option
            lock(lock_fileh, LOCK_SH | LOCK_NB)
        except:
            # xi = sys.exc_info()
            # sys.stderr.write("Sleeping because file is locked. %s %s\n" % (xi[1], xi[0]))
            # del xi
            lock_fileh.close()
            time.sleep(sleepIntervalSec)
            continue

        obtainedLock = True
        break

    if not obtainedLock:
        msg = "Unable to obtain lock on %s within %0.2f seconds." % (
            lockFilename, maxWaitTimeSec)
        raise LockTimeoutException(msg)

    if lockFilename != filename:
        fileh = open(filename, fileMode)
    else:
        fileh = lock_fileh

    if returnFilehandle:
        return fileh

    data = fileh.read()
    fileh.close()
    if lock_fileh:
        lock_fileh.close()  # release the lock.
    return data


class portalockerTests(unittest.TestCase):
    """
    Not really an effective test yet - should create an exclusive lock then spawn another process
    that attempts to obtain it.

    However you can sort of test this interactively by running the process once, leave it hanging
    at the prompt, and then running a second copy of this process.

    TODO: move to a different module.
    """

    testData = b"Hello, world.\n"

    def setUp(self):
        self.tfilename = os.path.join("test", "portalocker_test.txt")
        self.tfilename_lf = self.tfilename + ".lock"
        if not os.path.exists(self.tfilename):
            fh = open(self.tfilename, "wb")
            fh.write(self.testData)
            fh.close()
        else:
            sys.stderr.write(
                "File already existed: %s\n" % (self.tfilename,))
        if not os.path.exists(self.tfilename_lf):
            fh = open(self.tfilename_lf, "wb")
            fh.write(b"\n")  # contents dont matter
            fh.close()
        else:
            sys.stderr.write(
                "File already existed: %s\n" % (self.tfilename_lf,))

    def tearDown(self):
        if os.path.exists(self.tfilename):
            os.remove(self.tfilename)
        if os.path.exists(self.tfilename_lf):
            os.remove(self.tfilename_lf)

    def test_readLockedFile(self):
        newData = readLockedFile(self.tfilename)
        self.assertEqual(self.testData, newData)

        # Test a separate lockfile
        newData = readLockedFile(
            self.tfilename, lockFilename=self.tfilename_lf)
        self.assertEqual(self.testData, newData)

        # test returnFilehandle
        fh = readLockedFile(
            self.tfilename, returnFilehandle=True)
        w = input("\nHolding lock open. Press Enter when done >>")
        newData = fh.read()
        self.assertEqual(self.testData, newData)
        fh.close()

        # Test a write lock
        fh = open(self.tfilename, "ab")
        lock(fh, LOCK_EX)
        # time.sleep(2)
        fh.write(self.testData)
        fh.close()

        return 0


# def old_test():
#     """
#     Not really a functional unit test...
#     """
#     from time import time, strftime, localtime
#     from concurrent_log_handler import portalocker
#
#     log = open('log.txt', "a+")
#     portalocker.lock(log, portalocker.LOCK_EX)
#
#     timestamp = strftime("%m/%d/%Y %H:%M:%S\n", localtime(time()))
#     log.write(timestamp)
#
#     print("Wrote lines. Hit enter to release lock.")
#     dummy = sys.stdin.readline()
#
#     log.close()
#     return 0


if __name__ == '__main__':
    unittest.main()
