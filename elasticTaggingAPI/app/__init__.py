"""
FastAPI application package for elastic tagging.
Ensures the parent project root is importable so we can reuse existing modules.
"""

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))




