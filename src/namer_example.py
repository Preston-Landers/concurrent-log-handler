import logging
import logging.config
import time
from datetime import date

"""
This is an example which shows how you can use
custom namer function with ConcurrentRotatingFileHandler
"""


def log_file_namer(logger_name: str) -> str:
    # path/name.log.N
    logger_name, backup_number = logger_name.rsplit(".", maxsplit=1)
    # path/name.log
    logger_name = logger_name.replace(".log", "")
    curr_date = date.today().strftime("%Y_%m_%d")  # noqa: DTZ011

    return f"{logger_name}_{curr_date}_({backup_number}).log"


def my_program():
    import concurrent_log_handler

    # Now for the meat of your program...
    logger = logging.getLogger("MyExample")
    logger.setLevel(logging.DEBUG)  # optional to set this level here

    handler = concurrent_log_handler.ConcurrentRotatingFileHandler(
        "namer_test.log", "a", maxBytes=512, backupCount=2
    )
    handler.namer = log_file_namer
    logger.addHandler(handler)

    for idx in range(50):
        time.sleep(0.05)
        print("Loop %d; logging a message." % idx)
        logger.debug("%d > A debug message.", idx)
        if idx % 2 == 0:
            logger.info("%d > An info message.", idx)
    print("Done with example; exiting.")


if __name__ == "__main__":
    my_program()
