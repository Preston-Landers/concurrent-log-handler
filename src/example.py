import concurrent_log_handler
import logging
import logging.config

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
            'class': 'logging.handlers.ConcurrentRotatingFileHandler',
            'level': 'DEBUG',
            'formatter': 'default',
            'filename': 'test.log',
            'maxBytes': 30,
            'backupCount': 10,
        }
    },
    'root': {
        'handlers': ['file'],
        'level': 'DEBUG',
    },
}

logging.config.dictConfig(LOGGING)
logger = logging.getLogger('mylogger')

for idx in range(0, 10):
    logger.debug('%d > A debug message' % idx)
