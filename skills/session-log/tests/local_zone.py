"""The machine's local time zone set for a test, to check what a time near year 1 or 9999 does to the conversion to local time."""
import contextlib
import os
import time


@contextlib.contextmanager
def local_zone(name):
    previous = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = previous
        time.tzset()
