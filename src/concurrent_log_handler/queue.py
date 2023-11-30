#!/usr/bin/env python

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

import asyncio
import atexit
import logging
import queue
import sys
from logging.handlers import QueueHandler, QueueListener
from typing import Dict, List, Optional, Tuple, Union

__author__ = "Preston Landers <planders@gmail.com>"

GLOBAL_LOGGER_HANDLERS: Dict[
    str, Tuple[List[logging.Handler], "AsyncQueueListener"]
] = {}


# create a thread with a event loop in case of creating a coroutine in self.handle
class AsyncQueueListener(QueueListener):
    def __init__(
        self,
        queue: queue.Queue,
        *handlers: logging.Handler,
        respect_handler_level: bool = False,
    ):
        super().__init__(queue, *handlers, respect_handler_level=respect_handler_level)
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    def _monitor(self) -> None:
        # set event loop in thread
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        super()._monitor()  # type: ignore[misc]

    def stop(self) -> None:
        # stop event loop
        if self.loop:
            self.loop.stop()
            self.loop.close()

        self.enqueue_sentinel()
        # set timeout in case thread occurs deadlock
        if self._thread:
            self._thread.join(1)
            self._thread = None


def setup_logging_queues() -> None:
    if sys.version_info.major < 3:  # noqa: PLR2004
        raise RuntimeError("This feature requires Python 3.")

    queue_listeners: List[AsyncQueueListener] = []

    previous_queue_listeners = []

    # Q: What about loggers created after this is called?
    # A: if they don't attach their own handlers they should be fine
    for logger_name in get_all_logger_names(include_root=True):
        logger = logging.getLogger(logger_name)
        if logger.handlers:
            ori_handlers: List[logging.Handler] = []

            # retrieve original handlers and listeners from GLOBAL_LOGGER_HANDLERS if exist
            if logger_name in GLOBAL_LOGGER_HANDLERS:
                # get original handlers
                ori_handlers.extend(GLOBAL_LOGGER_HANDLERS[logger_name][0])
                # reset lock in original handlers (solve deadlock)
                for handler in ori_handlers:
                    handler.createLock()
                # recover handlers in logger
                logger.handlers = []
                logger.handlers.extend(ori_handlers)
                # get previous listeners
                previous_queue_listeners.append(GLOBAL_LOGGER_HANDLERS[logger_name][1])
            else:
                ori_handlers.extend(logger.handlers)

            log_queue: queue.Queue[str] = queue.Queue(-1)  # No limit on size

            queue_handler = QueueHandler(log_queue)
            queue_listener = AsyncQueueListener(log_queue, respect_handler_level=True)

            queuify_logger(logger, queue_handler, queue_listener)
            # print("Replaced logger %s with queue listener: %s" % (
            #     logger, queue_listener
            # ))
            queue_listeners.append(queue_listener)

            # save original handlers and current listeners
            GLOBAL_LOGGER_HANDLERS[logger_name] = (ori_handlers, queue_listener)

    # stop previous listeners at first
    stop_queue_listeners(*previous_queue_listeners)

    for listener in queue_listeners:
        listener.start()

    atexit.register(stop_queue_listeners, *queue_listeners)


def stop_queue_listeners(*listeners: AsyncQueueListener) -> None:
    for listener in listeners:
        try:  # noqa: SIM105
            listener.stop()
            # if sys.stderr:
            #     sys.stderr.write("Stopped queue listener.\n")
            #     sys.stderr.flush()
        except:  # noqa: E722, S110
            pass
            # Don't need this in production...
            # if sys.stderr:
            #     err = "Error stopping log queue listener:\n" \
            #           + traceback.format_exc() + "\n"
            #     sys.stderr.write(err)
            #     sys.stderr.flush()


def get_all_logger_names(include_root: bool = False) -> List[str]:
    """Return ``list`` of names of all loggers than have been accessed.

    Warning: this is sensitive to internal structures in the standard logging module.
    """
    rv = list(logging.Logger.manager.loggerDict.keys())
    if include_root:
        rv.insert(0, "")
    return rv


def queuify_logger(
    logger: Union[logging.Logger, str],
    queue_handler: QueueHandler,
    queue_listener: QueueListener,
) -> None:
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
    handlers = [
        handler for handler in logger.handlers if handler not in queue_listener.handlers
    ]

    if handlers:
        # The default QueueListener stores handlers as a tuple.
        queue_listener.handlers = tuple(list(queue_listener.handlers) + handlers)

    # Remove logger's handlers and replace with single queue handler.
    del logger.handlers[:]
    logger.addHandler(queue_handler)
