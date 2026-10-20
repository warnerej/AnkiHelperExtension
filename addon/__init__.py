import sys
from pathlib import Path

# Vendored runtime deps (httpx) are built into vendor/ by scripts/package.sh.
_vendor = Path(__file__).parent / "vendor"
if _vendor.is_dir() and str(_vendor) not in sys.path:
    sys.path.insert(0, str(_vendor))

try:
    from aqt import mw
except ImportError:  # plain Python (tests)
    mw = None

if mw is not None:
    from . import ui

    controller = ui.setup()
