"""Local timestamp-based identifiers."""

from datetime import datetime


def timestamp_id():
    return datetime.now().strftime("%Y%m%d%H%M%S%f")
