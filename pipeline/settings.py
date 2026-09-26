import os
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
RAW_DIR = DATA_DIR / "raw"
NORMALIZED_DIR = Path(os.getenv("NORMALIZED_DIR") or DATA_DIR / "normalized")
ENRICHED_DIR = DATA_DIR / "enriched"
EMBEDDED_DIR = DATA_DIR / "embedded"
REPORTS_DIR = DATA_DIR / "reports"
CURATED_DIR = DATA_DIR / "curated"
EVAL_DIR = Path(os.getenv("EVAL_DIR") or DATA_DIR / "eval")
CONFIG_DIR = ROOT / "config"
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://coffee:coffee@localhost:5432/coffee")


def load_config(name: str) -> dict:
    return yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8"))


def embedded_dir(model: str) -> Path:
    """Embedding cache per model, so switching models never overwrites another model's vectors."""
    return EMBEDDED_DIR / re.sub(r"[^A-Za-z0-9._-]+", "_", model)
