try:
    from . import _version  # type: ignore

    __version__ = _version.__version__
except:  # noqa: E722
    __version__ = "0.0.0-dev"

from .runner import QueueRunner as QuasiQueue  # noqa: F401
from .settings import Settings  # noqa: F401
