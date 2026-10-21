"""Put each Lambda / container source folder on sys.path, mirroring how they run."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for folder in (
    ROOT / "pipeline" / "change_detector",
    ROOT / "pipeline" / "functions" / "api",
    ROOT / "pipeline" / "functions" / "scene_search",
    ROOT / "pipeline" / "functions" / "report",
):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
