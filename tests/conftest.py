import sys
from pathlib import Path

# Make `backend` importable as a top-level package and `synth` (this
# directory's helper module) importable from test files, regardless of
# where pytest is invoked from.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
