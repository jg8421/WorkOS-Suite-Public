"""Local integrations for WorkOS Suite."""
from pathlib import Path
import sys

# Direct module use and CI load the same reviewed PDF parser as the launcher.
_vendor=Path(__file__).resolve().parents[1]/'components'/'workos'/'vendor'
if _vendor.is_dir() and str(_vendor) not in sys.path:sys.path.insert(0,str(_vendor))
__version__ = '1.0.3'
