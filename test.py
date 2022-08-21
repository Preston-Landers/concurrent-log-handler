# See stresstest.py for a more intensive test.
# This is more like a very quick test of basic functionality.

import logging.config
from pathlib import Path


def get_logging_config():
    logconfig_dict = {
        'version': 1,
        'formatters': {
            'standard': {
                'format': '%(asctime)s - %(levelname)s - %(name)s - %(message)s'
            }
        },
        'root': {
            'handlers': ['default'],
            'level': 'DEBUG',
        },
        'handlers': {
            'default': {
                'level': 'DEBUG',
                'formatter': 'standard',
                'class': 'logging.StreamHandler'
            },
            'gunicorn_access': {
                'level': 'DEBUG',
                'encoding': 'utf_8',
                'formatter': 'standard',
                'class': 'concurrent_log_handler.ConcurrentRotatingFileHandler',
                'filename': 'logging/access.log',
                'maxBytes': 1024,
                'backupCount': 2,
                'lock_file_directory': str(Path(Path(__file__).parent, 'lock/test_access'))
            },
            'gunicorn_error': {
                'level': 'DEBUG',
                'encoding': 'utf_8',
                'formatter': 'standard',
                'class': 'concurrent_log_handler.ConcurrentRotatingFileHandler',
                'filename': 'logging/error.log',
                'maxBytes': 1024,
                'backupCount': 3,
                'lock_file_directory': str(Path(Path(__file__).parent, 'lock/test_error'))
            }
        },
        'loggers': {
            'gunicorn.access': {
                'handlers': ['gunicorn_access'],
                'level': 'DEBUG',
                'propagate': False
            },
            'gunicorn.error': {
                'handlers': ['gunicorn_error'],
                'level': 'DEBUG',
                'propagate': False
            },
        }
    }
    return logconfig_dict


if __name__ == '__main__':
    # Create logging directory
    Path.mkdir(Path(Path(__file__).parent, 'logging'), exist_ok=True)

    # Load logging configuration
    logging.config.dictConfig(get_logging_config())

    # Root logger
    log1 = logging.getLogger(__name__)
    log1.debug("Here we go...")

    # Access logger
    log2 = logging.getLogger('gunicorn.access')
    log2.debug("There are 4 lights!!!")

    # Error logger
    log3 = logging.getLogger('gunicorn.error')
    log3.error("The cake is a lie!!!")
