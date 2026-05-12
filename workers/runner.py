"""Shared harness for background worker subprocesses.

Handles log-file open, stdout/stderr redirect, exception capture with
traceback, mark-failed callback, and I/O cleanup. Each worker module
provides a body_fn with its logic and a mark_failed_fn to update job status.
"""

import sys
import traceback

from paths import DUMPS_DIR


def run_worker(wiki: str, log_suffix: str, mark_failed_fn, body_fn) -> int:
    """Run body_fn inside a redirected log file with exception capture.

    Opens dumps/{wiki}_{log_suffix}.log in append mode, redirects stdout and
    stderr for the duration of body_fn, then restores them. On exception:
    prints the traceback to the log and calls mark_failed_fn(error_message).

    Returns 0 on success, 1 on any exception raised by body_fn.
    """
    log_path = DUMPS_DIR / f"{wiki}_{log_suffix}.log"
    log_file = open(log_path, "a", buffering=1)
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = log_file
    try:
        return body_fn()
    except Exception:
        msg = traceback.format_exc()
        print(f"[{log_suffix}] FAILED:\n{msg}", flush=True)
        try:
            mark_failed_fn(str(sys.exc_info()[1]))
        except Exception:
            pass
        return 1
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
        log_file.close()
