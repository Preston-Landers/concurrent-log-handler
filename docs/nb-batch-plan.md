# Non-Blocking Batch Logging Handler Roadmap

Proposed roadmap is excellent:

1. **Phase 1: Compression**
    * Enhance the core `ConcurrentRotatingFileHandler` with the flexible
      compression strategy. This is a self-contained feature that immediately
      adds value.

2. **Phase 2: Initial Batch Support in Core CLH**
    * Add the internal `_emit_batch()` API to the core handler and refactor
      `emit()` to use it. This prepares the foundation for performance
      enhancements.

3. **Phase 3: Create `NonBlockingConcurrentHandler` (Scaffolding)**
    * Implement the basic `NonBlockingConcurrentHandler` with its queue and
      `QueueListener`. In this initial version, the worker thread can simply
      pull one record at a time and call the core handler's `emit()` method.
    * **The key here, as you've identified, is to first prove that the
      non-blocking mechanism itself works correctly.**

4. **Phase 4: Update NBCH with Full Batch Features**
    * Now, with the scaffolding in place, enhance the
      `NonBlockingConcurrentHandler`. Add the `batch_size` and `batch_wait_time`
      parameters and implement the intelligent batch collection logic in the
      worker thread to call `_emit_batch()`.

5. **Phase 5: Full Async Support**
    * Proceed with the `AsyncConcurrentHandler` implementation, building on top
      of the robust and performant core that has now been fully developed and
      tested.

This is a much more robust and pragmatic plan. It separates concerns
beautifully, allowing you to focus on one set of problems at a time: first core
features, then non-blocking infrastructure, and finally non-blocking performance
optimization.

This is exactly how a complex software project should be planned. It minimizes
risk and ensures that each step delivers a testable, functional piece of the
puzzle.

Of course. Based on our detailed design discussion, here is a comprehensive
Claude Code prompt to implement the batching feature.

This prompt is structured as a two-part task: first enhancing the core handler
with the necessary internal API, and second, implementing the batch collection
logic in the new non-blocking handler.

## Claude Code Prompt

Hello Claude. You are an expert Python developer tasked with implementing a
high-performance batching feature for the `concurrent-log-handler` library. This
will involve enhancing the core synchronous handler and then creating a new
non-blocking handler that leverages this new capability.

The primary goal is to maximize logging throughput by minimizing file lock
contention, achieved by writing multiple log messages under a single lock.

You can find the necessary context files in the following locations:
* The full source code for the existing core handler is in:
  `src/concurrent_log_handler/__init__.py`
* The test suite is in the `tests/` folder. Tests are run with `pytest`.

Please implement this feature according to the following two-part plan.

### **Part 1: Enhance the Core `ConcurrentRotatingFileHandler`**

The first step is to add an internal batch-writing API to the existing
synchronous handler.

#### **A. Implement the `_emit_batch` Method**
-   In `ConcurrentRotatingFileHandler`, create a new internal method:
    `_emit_batch(self, messages: List[str])`.
-   This method will be responsible for the entire write-and-lock cycle for a
    list of pre-formatted message strings.
-   **Its logic must:**
    1.  Acquire the file lock via `self._do_lock()`.
    2.  Inside the `try...finally` block, perform a single
        `self.shouldRollover()` check. If a rollover is needed, call
        `self.doRollover()`.
    3.  Ensure the file stream is open.
    4.  Loop through the `messages` list and write each string to the stream.
    5.  Issue a single `self.stream.flush()` after all messages are written.
    6.  The `finally` block must release the lock via `self._do_unlock()`.

#### **B. Refactor the `emit` Method**
-   The existing `emit(self, record)` method must be refactored.
-   All its previous logic (locking, writing, unlocking) should be removed, as
    this is now handled by `_emit_batch`.
-   It should now only be responsible for formatting the record and then calling
    `self._emit_batch()` with a list containing just that one formatted message.
-   Example: `msg = self.format(record); self._emit_batch([msg])`
-   This ensures perfect backward compatibility for synchronous use.

### **Part 2: Create the `NonBlockingConcurrentHandler`**

Now, create the new non-blocking handler that will generate the batches.

#### **A. Create the New File and Class**
-   Create a new file: `src/concurrent_log_handler/nonblocking.py`.
-   Inside, create the new class:
    `NonBlockingConcurrentHandler(logging.Handler)`.

#### **B. Implement the Constructor**
-   The constructor signature must be: `__init__(self, handler, queue_size: int
    = 10000, batch_size: int = 1000, batch_wait_time: float = 1.0)`.
    - `handler`: The underlying core handler instance (from Part 1).
    - `batch_size`: The max number of records to collect before flushing.
    - `batch_wait_time`: The max time in seconds to wait for a batch to fill.
-   This handler should use a `queue.Queue` for buffering and a
    `logging.handlers.QueueListener` to manage the background thread.

#### **C. Implement the Batch Collection Logic**
-   This is the most critical part. The `QueueListener`'s background worker
    thread needs custom logic to assemble batches.
-   **The worker's loop should:**
    1.  Block on `self.queue.get(block=True, timeout=self.batch_wait_time)`.
        This elegantly handles the wait time. Use a `try...except queue.Empty`
        block to handle the timeout case where no record is received.
    2.  When a record is retrieved, start a new `batch` list with it.
    3.  Immediately spin with non-blocking `self.queue.get_nowait()` calls to
        pull all other available records from the queue, appending them to the
        `batch` list until either `batch_size` is reached or the queue is empty.
    4.  Once the batch is assembled, format all `LogRecord` objects into a list
        of strings.
    5.  Pass this list of strings to the core handler's batch API in a single
        call: `self.handler._emit_batch(formatted_messages)`.

#### **D. Package Integration & Testing**
-   **Integration:** In `src/concurrent_log_handler/__init__.py`, import the new
    `NonBlockingConcurrentHandler`.
-   **Testing:** Create a new test file, `tests/test_batching.py`. The tests
    must verify:
    1.  The `_emit_batch` method on the core handler works as expected (use
        `mocker.spy` to assert lock/unlock is called only once).
    2.  The `NonBlockingConcurrentHandler` flushes a batch when `batch_size` is
        reached.
    3.  The `NonBlockingConcurrentHandler` flushes a partial batch when
        `batch_wait_time` is exceeded.
    4.  A graceful `handler.stop()` call correctly flushes all pending messages
        from the queue.

### **Final Deliverables**

Please provide the complete contents for:
1.  The modified `src/concurrent_log_handler/__init__.py` file.
2.  The new `src/concurrent_log_handler/nonblocking.py` file.
3.  The new `tests/test_batching.py` file.
