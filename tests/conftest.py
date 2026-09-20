import sys
from pathlib import Path

# Make both the package and the synthetic-pose helper importable when pytest is
# run from anywhere in the repo.
ROOT = Path(__file__).resolve().parent.parent
for path in (ROOT, ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
