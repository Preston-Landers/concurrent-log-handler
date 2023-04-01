#!/usr/bin/env python
# -*- coding: utf-8 -*-

# This is the module's setup script.  To install this module, run:
#
#   python setup.py install
#
"""RotatingFileHandler replacement with concurrency, gzip and Windows support
"""

import io
import os
import sys

from setuptools import setup

here = os.path.abspath(os.path.dirname(__file__))

with open("README.md", "r") as fh:
    long_description = fh.read()

about = {}
with io.open(
    os.path.join(here, "src", "concurrent_log_handler", "__version__.py"),
    "r",
    encoding="utf-8",
) as fh:
    exec(fh.read(), about)

extra = {"use_2to3": False}

classifiers = """\
Development Status :: 4 - Beta
Topic :: System :: Logging
Operating System :: POSIX
Operating System :: Microsoft :: Windows
Programming Language :: Python
Programming Language :: Python :: 2.7
Programming Language :: Python :: 3
Topic :: Software Development :: Libraries :: Python Modules
License :: OSI Approved :: Apache Software License
"""

package_keywords = (
    "logging, windows, linux, unix, rotate, QueueHandler, QueueListener, portalocker"
)

# https://github.com/Preston-Landers/concurrent-log-handler/issues/28
# If Python 2, don't allow fulfillment with portalocker 2.0 as it won't work
install_requires = [
    'portalocker<=1.7.1; python_version < "3"',
    'portalocker>=1.4.0; python_version >= "3"',
]

# use windows because mac os has sub string 'win'
if sys.platform.startswith("windows"):
    try:
        import win32file
    except ImportError:
        # Only require pywin32 if not already installed
        # version 223 introduced ability to install from pip
        install_requires.append("pywin32>=223")

setup(
    name=about["__title__"],
    version=about["__version__"],
    author=about["__author__"],
    author_email=about["__author_email__"],
    packages=["concurrent_log_handler"],
    package_dir={
        "": "src",
    },
    url=about["__url__"],
    license=about["__license__"],
    description=about["__description__"],
    long_description=long_description,
    long_description_content_type="text/markdown",
    # platforms=["nt", "posix"],
    install_requires=install_requires,
    extras_require={
        "dev": ["pytest", "tox", "black", "ruff"],
    },
    tests_require=["pytest"],
    keywords=package_keywords,
    classifiers=classifiers.splitlines(),
    zip_safe=True,
    **extra
)

# Local installation for development:
# virtualenv venv
# (activate)
# pip install -e .[dev]

# Development build:
# python setup.py clean --all build sdist bdist_wheel
