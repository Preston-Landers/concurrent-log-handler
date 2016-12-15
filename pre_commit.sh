#!/bin/bash
sed -i src/cloghandler.py -re "s/^__revision__ .*/__revision__ = ''/"
