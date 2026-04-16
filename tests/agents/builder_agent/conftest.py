import os
from pathlib import Path

os.environ.setdefault(
    "PROGENITOR_DATA_DIR",
    str(Path(__file__).resolve().parents[3] / "data"),
)
