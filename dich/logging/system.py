# The system log, for the admin: everything a task prints, at any level. File descriptors 1 and 2 are
# pointed at the attempt's system.log, so output of C libraries (ffmpeg, onnxruntime, ctranslate2) and of
# threads the driver starts is caught too. Only done in worker processes, which run one task at a time.
import contextlib
import logging
import os
import sys

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging():
    root = logging.getLogger()
    if not any(getattr(h, "_dich", False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler._dich = True
        handler.setFormatter(logging.Formatter(FORMAT))
        root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.captureWarnings(True)


@contextlib.contextmanager
def capture(path):
    """fd 1/2 (and sys.stdout/sys.stderr) → path while inside."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout.flush()
    sys.stderr.flush()
    saved = os.dup(1), os.dup(2)
    with path.open("ab", buffering=0) as file:
        os.dup2(file.fileno(), 1)
        os.dup2(file.fileno(), 2)
        try:
            yield
        finally:
            with contextlib.suppress(Exception):
                sys.stdout.flush()
                sys.stderr.flush()
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            os.close(saved[0])
            os.close(saved[1])
