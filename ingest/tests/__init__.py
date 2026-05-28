import os
import sys

# Make sibling modules (bluesky_links, db_setup, reddit_links, etc.) importable
# regardless of where unittest is invoked from.
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
