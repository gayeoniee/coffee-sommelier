"""The open feature-model config has exactly one writer (docs/adr/0013-open-labels-weak-supervision.md, 한계)."""
import json
from pathlib import Path

from scripts.train_feature_model import CONFIG_PATH, SHIPPED_RECIPES

ROOT = Path(__file__).resolve().parents[1]


def test_ablation_script_never_writes_config():
    src = (ROOT / "scripts" / "ablate_open_labels.py").read_text(encoding="utf-8")
    assert "CONFIG_DIR" not in src and "feature_model_open.json\")" not in src


def test_shipped_config_matches_recipes_and_has_no_timestamp():
    doc = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert "trained_at" not in doc                        # re-running the trainer reproduces the file byte-for-byte
    assert list(doc["attrs"]) == [a for a in ("acidity", "body", "sweetness") if a in SHIPPED_RECIPES]
    for attr, (kind, arg) in SHIPPED_RECIPES.items():
        spec = doc["attrs"][attr]
        if kind == "ablation":
            assert spec["config"] == arg
        else:
            assert "config" not in spec and spec["uses_nbr"] is arg
