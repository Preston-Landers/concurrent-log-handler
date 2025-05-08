#!/usr/bin/env bash

echo -e "Checking formatting with black..."
black .

echo -e "\nChecking for problems with ruff..."
ruff check .

echo -e "\nRunning mypy to check types..."
mypy --version
mypy --install-types --non-interactive src/concurrent_log_handler
