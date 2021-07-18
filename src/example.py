import time
import logging
import logging.config

"""
This is an example which shows how you can use ConcurrentLogHandler as a regular  synchronous 
log handler. That means when your program logs a statement, it's processed and written to the
log file as part of the original thread.

See example_queue.py for a variation which performs logging statements in the background
asynchronously (using Python `asyncio` package).
"""


# noinspection DuplicatedCode
def my_program():
    # Somewhere in your program, usually at startup or config time, you can
    # call your logging setup function. If you're in an multiprocess environment,
    # each separate process that wants to write to the same file should call the same
    # or very similar logging setup code.
    my_logging_setup()

    # Now for the meat of your program...
    logger = logging.getLogger("MyExample")
    logger.setLevel(logging.DEBUG)  # optional

    for idx in range(0, 20):
        time.sleep(0.05)
        print("Loop %d; logging a message." % idx)
        logger.debug('%d > A debug message' % idx)
    print("Done with example.")


# noinspection DuplicatedCode
def my_logging_setup(log_name='example.log'):
    """
    An example of setting up logging in Python using a JSON dictionary to configure it.
    You can also use an outside .conf text file; see ConcurrentLogHandler/README.md
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
                'maxBytes': 120,

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
    return


if __name__ == '__main__':
    my_program()
