"""Quality gates of the automated refresh (docs/adr/0015-automated-refresh.md). All must pass before anything is
published; the pure `check_*` functions decide, the `run_*` helpers produce their inputs."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from pipeline import settings

LOO_TOLERANCE = 0.02          # LOO acidity within +-1 may drop at most this much below the committed baseline
ROOT = settings.ROOT
FULL_EVALS = ("violations", "loo", "loo_open", "coverage", "coverage_open")
OPEN_EVALS = ("violations", "loo", "coverage")


@dataclass
class Gate:
    name: str
    ok: bool
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


def check_violations(variant: str, result: dict) -> Gate:
    v, n = result.get("violations"), result.get("checked")
    return Gate(f"violations_{variant}", v == 0 and bool(n), f"{v}/{n}건 위반")


def check_loo(variant: str, result: dict, baseline: dict | None, tol: float = LOO_TOLERANCE) -> Gate:
    new = (result.get("acidity") or {}).get("within1")
    base = ((baseline or {}).get("acidity") or {}).get("within1")
    if new is None:
        return Gate(f"loo_acidity_{variant}", False, "LOO 결과 없음")
    if base is None:
        return Gate(f"loo_acidity_{variant}", True, f"{new} (기준선 없음)")
    return Gate(f"loo_acidity_{variant}", new >= base - tol - 1e-9,
                f"{new} vs 기준선 {base} (허용 {base - tol:.3f} 이상)")


def check_size(fails: list[str]) -> Gate:
    return Gate("size_check", not fails, "; ".join(fails) or "삭제 비율 정상")


def check_command(name: str, returncode: int, output: str) -> Gate:
    lines = [line for line in output.strip().splitlines()
             if line.strip() and not line.startswith("-- Docs:") and set(line.strip()) != {"="}]
    tail = " / ".join(lines[-2:])
    return Gate(name, returncode == 0, tail[:400])


def run(cmd: list[str], env: dict | None = None, cwd: Path = ROOT, timeout: int = 3600) -> tuple[int, str]:
    e = {**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})}
    p = subprocess.run(cmd, cwd=cwd, env=e, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def run_evals(db_url: str, eval_dir: Path, variant: str) -> tuple[int, str]:
    """app.eval against a staging DB, results into eval_dir (never the committed data/eval)."""
    eval_dir.mkdir(parents=True, exist_ok=True)
    # loo reads the fixed leak-free target embeddings next to its results; give it the committed copy
    src = settings.DATA_DIR / "eval" / "loo_tagfree_query_embeddings.jsonl"
    if variant == "full" and src.exists() and not (eval_dir / src.name).exists():
        (eval_dir / src.name).write_bytes(src.read_bytes())
    names = FULL_EVALS if variant == "full" else OPEN_EVALS
    return run([sys.executable, "-m", "app.eval", *names],
               env={"DATABASE_URL": db_url, "EVAL_DIR": str(eval_dir), "DATA_VARIANT": variant})


def load_json(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
