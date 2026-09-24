import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class Manifest:
    source: str
    snapshot: str
    files: list[str]
    ok: bool
    error: str | None = None


class Collector(Protocol):
    name: str

    def collect(self, out_dir: Path, http) -> list[Path]: ...


def run_collect(collectors, raw_root: Path, http, snapshot: str) -> list[Manifest]:
    results = []
    for c in collectors:
        out = raw_root / c.name / snapshot
        out.mkdir(parents=True, exist_ok=True)
        try:
            files = c.collect(out, http)
            m = Manifest(c.name, snapshot, [f.relative_to(out).as_posix() for f in files], True)
        except Exception as e:  # one broken source must not stop the others
            m = Manifest(c.name, snapshot, [], False, f"{type(e).__name__}: {e}")
        (out / "manifest.json").write_text(json.dumps(asdict(m), ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(m)
    return results


def latest_snapshot(raw_root: Path, source: str) -> Path | None:
    base = raw_root / source
    if not base.exists():
        return None
    for d in sorted((p for p in base.iterdir() if p.is_dir()), reverse=True):
        mf = d / "manifest.json"
        if mf.exists() and json.loads(mf.read_text(encoding="utf-8")).get("ok"):
            return d
    return None
