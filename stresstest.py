#!/usr/bin/env python
""" stresstest.py:  A stress-tester for ConcurrentRotatingFileHandler

This utility spawns a bunch of processes that all try to concurrently write to
the same file. This is pretty much the worst-case scenario for my log handler.
Once all of the processes have completed writing to the log file, the output is
compared to see if any log messages have been lost.

In the future, I may also add in support for testing with each process having
multiple threads.


"""

import gzip
import os
import sys
import string
from optparse import OptionParser
from subprocess import Popen
from time import sleep
from random import randint, choice

# local lib; for testing
from concurrent_log_handler import ConcurrentRotatingFileHandler, randbits

__version__ = '$Id$'
__author__ = 'Lowell Alleman'

ROTATE_COUNT = 5000


class RotateLogStressTester:
    def __init__(self, sharedfile, uniquefile, name="LogStressTester", logger_delay=0):
        self.sharedfile = sharedfile
        self.uniquefile = uniquefile
        self.name = name
        self.writeLoops = 100000
        self.rotateSize = 128 * 1024
        self.rotateCount = ROTATE_COUNT
        self.random_sleep_mode = False
        self.debug = True
        self.logger_delay = logger_delay
        self.log = None
        self.use_gzip = True

    def getLogHandler(self, fn):
        """ Override this method if you want to test a different logging handler
        class. """
        return ConcurrentRotatingFileHandler(
            fn, 'a', self.rotateSize,
            self.rotateCount, delay=self.logger_delay,
            encoding='utf-8',
            debug=self.debug, use_gzip=self.use_gzip)
        # To run the test with the standard library's RotatingFileHandler:
        # from logging.handlers import RotatingFileHandler
        # return RotatingFileHandler(fn, 'a', self.rotateSize, self.rotateCount)

    def start(self):
        from logging import getLogger, FileHandler, Formatter, DEBUG
        self.log = getLogger(self.name)
        self.log.setLevel(DEBUG)

        formatter = Formatter(
            '%(asctime)s [%(process)d:%(threadName)s] %(levelname)-8s %(name)s:  %(message)s')
        # Unique log handler (single file)
        handler = FileHandler(self.uniquefile, "w")
        handler.setLevel(DEBUG)
        handler.setFormatter(formatter)
        self.log.addHandler(handler)

        # If you suspect that the diff stuff isn't working, un comment the next
        # line.  You should see this show up once per-process.
        # self.log.info("Here is a line that should only be in the first output.")

        # Setup output used for testing
        handler = self.getLogHandler(self.sharedfile)
        handler.setLevel(DEBUG)
        handler.setFormatter(formatter)
        self.log.addHandler(handler)

        # If this ever becomes a real "Thread", then remove this line:
        self.run()

    def run(self):
        print("Hello, self.writeLoops: %s" % (self.writeLoops,))
        c = 0
        import random
        # Use a bunch of random quotes, numbers, and severity levels to mix it up a bit!
        msgs = ["I found %d puppies", "There are %d cats in your hatz",
                "my favorite number is %d", "I am %d years old.", "1 + 1 = %d",
                "%d/0 = DivideByZero", "blah!  %d thingies!", "8 15 16 23 48 %d",
                "the worlds largest prime number: %d", "%d happy meals!"]
        logfuncts = [self.log.debug, self.log.info, self.log.warning, self.log.error]

        self.log.info("Starting to write random log message.   Loop=%d", self.writeLoops)
        while c <= self.writeLoops:
            c += 1

            self.log.debug(
                "Triggering logging within format of another log: %r",
                InnerLoggerExample(
                    self.log, randbits(64), rand_string(1024 * 10)))

            msg = random.choice(msgs)
            logfunc = random.choice(logfuncts)
            logfunc(msg, randbits(64))

            if self.random_sleep_mode and c % 1000 == 0:
                # Sleep from 0-5 seconds
                s = randint(0, 5)
                print("PID %d sleeping for %d seconds" % (os.getpid(), s))
                sleep(s)
                # break
        self.log.info("Done writing random log messages.")


def iter_lognames(logfile, count):
    """ Generator for log file names based on a rotation scheme """
    for i in range(count - 1, 0, -1):
        yield "%s.%d" % (logfile, i)
    yield logfile


def iter_logs(iterable, missing_ok=False):
    """ Generator to extract log entries from shared log file. """
    for fn in iterable:
        opener = open
        log_path = fn
        log_path_gz = log_path + ".gz"
        if os.path.exists(log_path_gz):
            log_path = log_path_gz
            opener = gzip.open

        if os.path.exists(log_path):
            with opener(log_path, "r") as fh:
                for line in fh:
                    yield decode(line)
        elif not missing_ok:
            raise ValueError("Missing log file %s" % log_path)


def combine_logs(combinedlog, iterable, mode="w"):
    """ write all lines (iterable) into a single log file. """
    fp = open(combinedlog, mode)
    for chunk in iterable:
        fp.write(chunk)
    fp.close()


class InnerLoggerExample(object):
    def __init__(self, log, a, b):
        self.log = log
        self.a = a
        self.b = b

    def __str__(self):
        # This should trigger a logging event within the format() handling of another event
        self.log.debug("Inner logging example: a=%r, b=%r", self.a, self.b)
        return "<InnerLoggerExample a=%r>" % (self.a,)

    def __repr__(self):
        return str(self)


allchar = string.ascii_letters + string.punctuation + string.digits


def rand_string(str_len):
    chars = []
    for i in range(0, str_len):
        c = choice(allchar)
        if i % 10 == 0:
            c = " "
        chars.append(c)
    return "".join(chars)


parser = OptionParser(
    usage="usage:  %prog",
    version=__version__,
    description="Stress test the concurrent_log_handler module.")
parser.add_option(
    "--log-calls", metavar="NUM",
    action="store", type="int", default=50000,
    help="Number of logging entries to write to each log file.  "
         "Default is %default")
parser.add_option(
    "--random-sleep-mode",
    action="store_true", default=False)
parser.add_option(
    "--debug",
    action="store_true", default=False)
parser.add_option(
    "--logger-delay",
    action="store_true", default=False,
    help="Enable the 'delay' mode in the logger class. "
         "This means that the log file will be opened on demand.")


def main_client(args):
    (options, args) = parser.parse_args(args)
    if len(args) != 2:
        raise ValueError("Require 2 arguments.  We have %d args" % len(args))
    (shared, client) = args

    if os.path.isfile(client):
        sys.stderr.write("Already a client using output file %s\n" % client)
        sys.exit(1)
    tester = RotateLogStressTester(shared, client, logger_delay=options.logger_delay)
    tester.random_sleep_mode = options.random_sleep_mode
    tester.debug = options.debug
    tester.writeLoops = options.log_calls
    tester.start()
    print("We are done  pid=%d" % os.getpid())


class TestManager:
    class ChildProc(object):
        """ Very simple child container class."""
        __slots__ = ["popen", "sharedfile", "clientfile"]

        def __init__(self, **kwargs):
            self.update(**kwargs)

        def update(self, **kwargs):
            for key, val in kwargs.items():
                setattr(self, key, val)

    def __init__(self, output_path):
        self.output_path = output_path
        self.tests = []
        self.client_stdout = open(os.path.join(output_path, "client_stdout.txt"), "a")
        self.client_stderr = open(os.path.join(output_path, "client_stderr.txt"), "a")

    def launchPopen(self, *args, **kwargs):
        if "stdout" not in kwargs:
            kwargs["stdout"] = self.client_stdout
        if "stderr" not in kwargs:
            kwargs["stderr"] = self.client_stdout
        proc = Popen(*args, **kwargs)
        cp = self.ChildProc(popen=proc)
        self.tests.append(cp)
        return cp

    def wait(self, check_interval=3):
        """ Wait for all child test processes to complete. """
        print("Waiting while children are out running and playing!")
        while True:
            sleep(check_interval)
            waiting = []
            for cp in self.tests:
                if cp.popen.poll() is None:
                    waiting.append(cp.popen.pid)
            if not waiting:
                break
            print("Waiting on %r " % waiting)
        print("All children have stopped.")

    def checkExitCodes(self):
        for cp in self.tests:
            stdout_str, stderr_str = cp.popen.communicate()
            exit_code = cp.popen.poll()
            if exit_code != 0:
                print(stderr_str)
                print(stderr_str)
                print("cp exit code: %s: %s" % (cp, exit_code))
                return False
        return True


def unified_diff(a, b, out=sys.stdout):
    import difflib
    ai = open(a).readlines()
    bi = open(b).readlines()
    for line in difflib.unified_diff(ai, bi, a, b):
        out.write(line)


def main_runner(args):
    parser.add_option(
        "--processes", metavar="NUM",
        action="store", type="int", default=3,
        help="Number of processes to spawn.  Default: %default")
    parser.add_option(
        "--delay", metavar="secs",
        action="store", type="float", default=2.5,
        help="Wait SECS before spawning next processes.  "
             "Default: %default")
    parser.add_option(
        "-p", "--path", metavar="DIR",
        action="store", default="test_output",
        help="Path to a temporary directory.  Default: '%default'")

    this_script = args[0]
    (options, args) = parser.parse_args(args)
    options.path = os.path.abspath(options.path)
    if not os.path.isdir(options.path):
        os.makedirs(options.path)

    manager = TestManager(options.path)
    shared = os.path.join(options.path, "shared.log")
    for client_id in range(options.processes):
        client = os.path.join(options.path, "client.log_client%s.log" % client_id)
        cmdline = [sys.executable, this_script, "client", shared, client,
                   "--log-calls=%d" % options.log_calls]
        if options.random_sleep_mode:
            cmdline.append("--random-sleep-mode")
        if options.debug:
            cmdline.append("--debug")
        if options.logger_delay:
            cmdline.append("--logger-delay")

        child = manager.launchPopen(cmdline)
        child.update(sharedfile=shared, clientfile=client)
        sleep(options.delay)

    # Wait for all of the subprocesses to exit
    manager.wait()
    # Check children exit codes
    if not manager.checkExitCodes():
        sys.stderr.write("One or more of the child process has failed.\n"
                         "Aborting test.\n")
        sys.exit(2)

    client_combo = os.path.join(options.path, "client.log.combo")
    shared_combo = os.path.join(options.path, "shared.log.combo")

    # Combine all of the log files...
    client_files = [child.clientfile for child in manager.tests]

    if False:
        def sort_em(iterable):
            return iterable
    else:
        sort_em = sorted

    print("Writing out combined client logs...")
    combine_logs(client_combo, sort_em(iter_logs(client_files)))
    print("done.")

    print("Writing out combined shared logs...")
    shared_log_files = iter_lognames(shared, ROTATE_COUNT)
    log_lines = iter_logs(shared_log_files, missing_ok=True)
    combine_logs(shared_combo, sort_em(log_lines))
    print("done.")

    print("Running internal diff:  "
          "(If the next line is 'end of diff', then the stress test passed!)")
    unified_diff(client_combo, shared_combo)
    print("   --- end of diff ----")


def decode(thing, encoding="utf-8"):
    if isinstance(thing, bytes):
        return thing.decode(encoding=encoding)
    return thing


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1].lower() == "client":
        main_client(sys.argv[2:])
    else:
        main_runner(sys.argv)
