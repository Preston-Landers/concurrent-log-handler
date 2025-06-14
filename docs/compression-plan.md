# Extensible Compression Options for CLH

Hello Claude. You are an expert Python developer with deep knowledge of creating
robust, extensible, and backward-compatible libraries.

Your task is to refactor and enhance the log file compression functionality
within the `ConcurrentRotatingFileHandler` class. The goal is to move from a
hardcoded `gzip` implementation to a flexible, strategy-based system that
supports multiple compression methods, including built-in libraries, optional
libraries, and external system commands.

The current full source code for the existing CLH core code is in the
`src/concurrent_log_handler/__init__.py` file.

The test suite is in the `tests` folder and tests can be run with `pytest`.

## **Architectural Guidance: The Strategy Pattern**

The recommended approach is to use a **Strategy Design Pattern**. This will
involve:

1. Creating an abstract base class, `Compressor`, that defines the interface
    for all compression strategies.
2. Implementing a concrete `Compressor` subclass for each compression method.
3. In the `ConcurrentRotatingFileHandler`, instantiating the chosen strategy
    class and delegating the compression task to it.

This approach will keep the main handler class clean and make it easy to add new
compression methods in the future.

## **1. Requirements for the Handler (`ConcurrentRotatingFileHandler`)**

### **A. Constructor (`__init__`) Enhancements**

- **Maintain Backward Compatibility:** The existing `use_gzip: bool = False`
  parameter must continue to work. If a user passes `use_gzip=True` and the new
  `compression` parameter is `None`, it should trigger `gzip` compression and
  issue a `DeprecationWarning`.
- **Introduce New `compression` Parameter:** Add a new parameter `compression:
  Optional[Union[str, Dict]] = None`. This will be the primary way to configure
  compression. It will accept:
  - A string (e.g., `'gzip'`, `'bz2'`) for built-in or standard optional
      libraries.
  - A dictionary for configuring external command compressors (e.g., `{'cmd':
      'xz', 'ext': '.xz'}`).
- **Instantiate the Strategy:** The `__init__` method should contain a factory
  or helper method (e.g., `_create_compressor`) that inspects
  `self.compression_config` and returns an instance of the appropriate
  `Compressor` subclass. This instance should be stored as `self.compressor`.

### **B. Refactor `doRollover`**

- The logic for calling the compressor should be moved into `doRollover`.
- After the log file is successfully renamed to a temporary name (e.g.,
  `app.log.rotate.123...`), `doRollover` should check if `self.compressor`
  exists.
- If it exists, `doRollover` should call the compressor to process the temporary
  file. It should handle the file extension dynamically based on the
  compressor's properties.
- The old `do_gzip` method should be completely removed.

## **2. Requirements for the `Compressor` Strategy Classes**

You should create an abstract base class and several concrete implementations.
Consider placing these in a new file:
`src/concurrent_log_handler/compression.py`.

### **A. `Compressor` Abstract Base Class**

```python
from abc import ABC, abstractmethod

class Compressor(ABC):
    @property
    @abstractmethod
    def extension(self) -> str:
        """The file extension for this compression type (e.g., '.gz')."""
        pass

    @abstractmethod
    def compress(self, input_path: str, output_path: str) -> None:
        """Compresses the input file to the output file."""
        pass
```

### **B. Concrete Implementations**

1. **Built-in Libraries:** Create classes for Python's standard libraries:
    `GzipCompressor`, `Bz2Compressor`, and `LzmaCompressor`.
2. **Optional Library (Example):** Add logic to the factory method to handle
    `'zstd'`. It should be wrapped in a `try...except ImportError` block, so the
    program runs without `zstandard` installed but can use it if available.
3. **External Command:** Create an `ExternalCmdCompressor` class.
    - Its constructor should accept the command, extension, and an optional
      `non_blocking` flag: `__init__(self, cmd: str, ext: str, non_blocking:
      bool = False)`.
    - It must use the `subprocess` module.
    - **Crucially, the default implementation should be synchronous
      (`subprocess.run`) for reliability.** This is safer because the main
      process waits for compression to complete.
    - The `non_blocking=True` option should use `subprocess.Popen` to "fire and
      forget" the compression task. Please add a comment noting the reliability
      risks of this approach.

## **3. Testing Requirements**

Please update the test suite to validate the new functionality:

1. Test backward compatibility by instantiating with `use_gzip=True`.
2. Test the `compression` parameter with a built-in string value, like `'bz2'`.
3. Test the `compression` parameter with the dictionary configuration for an
    external command (you can use a common command like `gzip` or `cat` for
    testing purposes).
4. Test the failure case where a non-installed optional library (like `'zstd'`)
    is requested, ensuring it fails gracefully without crashing.

Add new additional test case with the `compression` parameter instead of
modifying the existing cases to add it.

It is preferable to put all new tests in a new file, though modifying 
the existing test files is also permissible.

## **Final Deliverables**

Please update the code of the main CLH source tree to provide the features described above.

1. The new `src/concurrent_log_handler/compression.py` file containing the
    `Compressor` classes and related logic.
2. The modified `src/concurrent_log_handler/__init__.py` file with the
    changes to `ConcurrentRotatingFileHandler`.
3. The modified test file(s) with the new tests for the compression features.

