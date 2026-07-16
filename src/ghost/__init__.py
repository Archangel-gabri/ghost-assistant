"""Ghost — voice + screen assistant — Super-fast AI helper with speech recognition."""

__version__ = "2.0.0"
__author__ = "Danya Kubrak"
__email__ = "archangel-gabri@users.noreply.github.com"
__license__ = "MIT"

__all__ = [
    "version",
    "main",
]

from .orchestrator import LLMSession

version = __version__
