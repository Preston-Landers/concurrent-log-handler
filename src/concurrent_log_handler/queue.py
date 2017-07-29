#!/usr/bin/env python
# -*- coding: utf-8; mode: python; py-indent-offset: 4; indent-tabs-mode: nil -*-
# vim: fileencoding=utf-8 tabstop=4 expandtab shiftwidth=4

# Copyright 2017 Journyx, Inc., and other contributors

"""
Implement a threaded queue for loggers based on the standard logging.py
QueueHandler / QueueListener classes. Requires Python 3.

Calls to loggers will simply place the logging request on the queue and return immediately. A
background thread will handle the actual logging. This helps avoid blocking for write locks on
the logfiles.

Please note that this replaces the handlers of all currently configured Python loggers with a
proxy (QueueHandler).  Call `setup_logging_queues` to do this. That also sets up an `atexit`
callback which calls stop() on the QueueListener.

Source for some of these functions:
https://github.com/dgilland/logconfig/blob/master/logconfig/utils.py

Additional code provided by Journyx, Inc. http://www.journyx.com
"""

import atexit
import logging
# noinspection PyCompatibility
import queue
import sys
from logging.handlers import QueueHandler, QueueListener

__author__ = "Preston Landers <planders@gmail.com>"


def setup_logging_queues():
    if sys.version_info.major < 3:
        raise RuntimeError("This feature requires Python 3.")

    queue_listeners = []

    # Q: What about loggers created after this is called?
    # A: if they don't attach their own handlers they should be fine
    for logger in get_all_logger_names(include_root=True):
        logger = logging.getLogger(logger)
        if logger.handlers:
            log_queue = queue.Queue(-1)  # No limit on size

            queue_handler = QueueHandler(log_queue)
            queue_listener = QueueListener(
                log_queue, respect_handler_level=True)

            queuify_logger(logger, queue_handler, queue_listener)
            # print("Replaced logger %s with queue listener: %s" % (
            #     logger, queue_listener
            # ))
            queue_listeners.append(queue_listener)

    for listener in queue_listeners:
        listener.start()

    atexit.register(stop_queue_listeners, *queue_listeners)
    return


def stop_queue_listeners(*listeners):
    for listener in listeners:
        # noinspection PyBroadException
        try:
            listener.stop()
            # if sys.stderr:
            #     sys.stderr.write("Stopped queue listener.\n")
            #     sys.stderr.flush()
        except:
            pass
            # Don't need this in production...
            # if sys.stderr:
            #     err = "Error stopping log queue listener:\n" \
            #           + traceback.format_exc() + "\n"
            #     sys.stderr.write(err)
            #     sys.stderr.flush()


def get_all_logger_names(include_root=False):
    """Return ``list`` of names of all loggers than have been accessed.

    Warning: this is sensitive to internal structures in the standard logging module.
    """
    # noinspection PyUnresolvedReferences
    rv = list(logging.Logger.manager.loggerDict.keys())
    if include_root:
        rv.insert(0, '')
    return rv


def queuify_logger(logger, queue_handler, queue_listener):
    """Replace logger's handlers with a queue handler while adding existing
    handlers to a queue listener.

    This is useful when you want to use a default logging config but then
    optionally add a logger's handlers to a queue during runtime.

    Args:
        logger (mixed): Logger instance or string name of logger to queue-ify
            handlers.
        queue_handler (QueueHandler): Instance of a ``QueueHandler``.
        queue_listener (QueueListener): Instance of a ``QueueListener``.

    """
    if isinstance(logger, str):
        logger = logging.getLogger(logger)

    # Get handlers that aren't being listened for.
    handlers = [handler for handler in logger.handlers
                if handler not in queue_listener.handlers]

    if handlers:
        # The default QueueListener stores handlers as a tuple.
        queue_listener.handlers = \
            tuple(list(queue_listener.handlers) + handlers)

    # Remove logger's handlers and replace with single queue handler.
    del logger.handlers[:]
    logger.addHandler(queue_handler)
