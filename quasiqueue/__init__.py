try:
    from . import _version  # type: ignore

    __version__ = _version.__version__
except Exception:
    __version__ = "0.0.0-dev"

from .builder import Builder  # noqa: F401
from .reader import reader_process  # noqa: F401
from .runner import QueueRunner as QuasiQueue  # noqa: F401
from .runner import run_queues  # noqa: F401
from .settings import Settings  # noqa: F401

__all__ = [
    "Builder",
    "QuasiQueue",
    "Settings",
    "reader_process",
    "run_queues",
]
