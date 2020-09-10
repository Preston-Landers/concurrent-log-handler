import logging
import logging.config
from concurrent_log_handler import queue, ConcurrentRotatingFileHandler

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'default': {
            'format': '%(asctime)s %(levelname)s %(name)s %(message)s'
        },
    },
    'handlers': {
        'file': {
            'level': 'DEBUG',
            'class': 'logging.handlers.ConcurrentRotatingFileHandler',
            'formatter': 'default',
            'filename': 'test.log',
            'owner': ['greenfrog', 'admin'],
            'chmod': 0o0660,
            'maxBytes': 30,
            'backupCount': 10,
            'use_gzip': True,
            'delay': True
        }
    },
    'root': {
        'handlers': ['file'],
        'level': 'DEBUG',
    },
}

LOG_NAME = 'test.log'

# logging.config.dictConfig(LOGGING)
logger = logging.getLogger(LOG_NAME)
logger.setLevel(logging.DEBUG)

handler = ConcurrentRotatingFileHandler(filename='test.log', mode='a', maxBytes=10 * 1000000,
                                                               backupCount=10, use_gzip=False,
                                                               encoding='utf-8')
handler.setLevel(logging.DEBUG)
TIME_FMT = "%Y-%m-%d %H:%M:%S"
FORMAT_STR = logging.Formatter(fmt='[%(asctime)s][%(levelname)s][%(filename)s:%(lineno)s][%(process)d][%(message)s]',
                               datefmt=TIME_FMT)
handler.setFormatter(FORMAT_STR)
logger.addHandler(handler)

# setup first time
queue.setup_logging_queues()
# setup second time
queue.setup_logging_queues()
# setup third time
queue.setup_logging_queues()
# setup forth time
queue.setup_logging_queues()

print("GLOBAL_LOGGER_HANDLERS:", queue.GLOBAL_LOGGER_HANDLERS)
print("logger.handlers:", logger.handlers)

for idx in range(0, 10):
    logger.debug('%d > A debug message' % idx)

queue.stop_queue_listeners()
