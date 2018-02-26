import logging
import logging.config
import concurrent_log_handler

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

logging.config.dictConfig(LOGGING)
logger = logging.getLogger(__name__)

for idx in range(0, 10):
    logger.debug('%d > A debug message' % idx)
