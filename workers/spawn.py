"""Subprocess spawn helper usable from both the FastAPI app and worker modules.

Lives in ``workers/`` so workers can dispatch successor jobs without importing
``app.*``. ``app.helpers.spawn_worker`` forwards here so route handlers keep
their existing call site.
"""

import subprocess
import sys

import paths


def spawn_worker(module: str, wiki: str, job_id: int, log_suffix: str) -> None:
    """Spawn a worker subprocess as a detached, log-redirected process.

    Args:
        module: Dotted module path, e.g. ``"workers.embed"``.
        wiki: Wiki name used to derive the log file name.
        job_id: Job id passed to the worker's CLI.
        log_suffix: Tag for the log file name (``"refresh"`` or ``"embed"``).
    """
    log_path = paths.BASE_DIR / "dumps" / f"{wiki}_{log_suffix}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as log:
        subprocess.Popen(
            [sys.executable, "-m", module, "--wiki", wiki, "--job-id", str(job_id)],
            cwd=paths.BASE_DIR,
            start_new_session=True,
            stdout=log,
            stderr=log,
        )
