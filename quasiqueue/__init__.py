from ._version import get_versions

__version__ = get_versions()["version"]
del get_versions

from .runner import QueueRunner as QuasiQueue
from .settings import Settings
