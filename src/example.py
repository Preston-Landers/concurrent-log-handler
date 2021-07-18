import time
import logging
import logging.config

"""
This is an example which shows how you can use ConcurrentLogHandler. If you have 

Two basic options are demonstrated: 
 * ASYNC_LOGGING = False - using as a regular synchronous log handler. 
    That means when your program logs a statement, it's processed and written to the
    log file as part of the original thread.
 * ASYNC_LOGGING = True - performs logging statements in a background thread asynchronously. 
    This uses Python's `asyncio` package.
"""


def my_program():

    ASYNC_LOGGING = False

    # Somewhere in your program, usually at startup or config time, you can
    # call your logging setup function. If you're in an multiprocess environment,
    # each separate process that wants to write to the same file should call the same
    # or very similar logging setup code.
    my_logging_setup(use_async=ASYNC_LOGGING)

    # Now for the meat of your program...
    logger = logging.getLogger("MyExample")
    logger.setLevel(logging.DEBUG)  # optional to set this level here

    for idx in range(0, 20):
        time.sleep(0.05)
        print("Loop %d; logging a message." % idx)
        logger.debug('%d > A debug message.' % idx)
        if idx % 2 == 0:
            logger.info('%d > An info message.' % idx)
    print("Done with example; exiting.")

    # Optional; you can manually stop the logging queue listeners at any point
    # or let it happen at process exit.
    if ASYNC_LOGGING:
        from concurrent_log_handler.queue import stop_queue_listeners
        stop_queue_listeners()


def my_logging_setup(log_name='example.log', use_async=False):
    """
    An example of setting up logging in Python using a JSON dictionary to configure it.
    You can also use an outside .conf text file; see ConcurrentLogHandler/README.md

    If you want to use async logging, call this after your main logging setup as shown below:

    concurrent_log_handler.queue.setup_logging_queues()
    """

    # Import this to install logging.handlers.ConcurrentRotatingFileHandler
    # The noinspection thing is so PyCharm doesn't think we're using this for no reason
    # noinspection PyUnresolvedReferences
    import concurrent_log_handler

    logging_config = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'default': {
                'format': '%(asctime)s %(levelname)s %(name)s %(message)s'
            },
            'example2': {
                'format': '[%(asctime)s][%(levelname)s][%(filename)s:%(lineno)s]'
                          '[%(process)d][%(message)s]',
            }
        },

        # Set up our concurrent logger handler. Need one of these per unique file.
        'handlers': {
            'my_concurrent_log': {
                'level': 'DEBUG',
                'class': 'logging.handlers.ConcurrentRotatingFileHandler',

                # Example of a custom format for this log.
                'formatter': 'example2',
                # 'formatter': 'default',

                'filename': log_name,

                # Optional: set an owner and group for the log file
                # 'owner': ['greenfrog', 'admin'],

                # Sets permissions to owner and group read+write
                'chmod': 0o0660,

                # Note: this is abnormally small to make it easier to demonstrate rollover.
                # A more reasonable value might be 10 MiB or 10485760
                'maxBytes': 240,

                # Number of rollover files to keep
                'backupCount': 10,

                # 'use_gzip': True,
            }
        },

        # Tell root logger to use our concurrent handler
        'root': {
            'handlers': ['my_concurrent_log'],
            'level': 'DEBUG',
        },
    }

    logging.config.dictConfig(logging_config)

    if use_async:
        # To enable background logging queue, call this near the end of your logging setup.
        from concurrent_log_handler.queue import setup_logging_queues
        setup_logging_queues()

    return


if __name__ == '__main__':
    my_program()
