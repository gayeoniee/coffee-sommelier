import json
from dataclasses import dataclass

from pipeline.collect import latest_snapshot, run_collect


@dataclass
class OkCollector:
    name: str = "ok_src"

    def collect(self, out_dir, http):
        p = out_dir / "a.txt"
        p.write_text("x", encoding="utf-8")
        return [p]


@dataclass
class BoomCollector:
    name: str = "boom_src"

    def collect(self, out_dir, http):
        raise RuntimeError("site down")


def test_run_collect_isolates_failures(tmp_path):
    ms = run_collect([BoomCollector(), OkCollector()], tmp_path, http=None, snapshot="2026-09-24")
    assert [(m.source, m.ok) for m in ms] == [("boom_src", False), ("ok_src", True)]
    assert "site down" in ms[0].error
    manifest = json.loads((tmp_path / "ok_src" / "2026-09-24" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"] == ["a.txt"]


def test_latest_snapshot_skips_failed(tmp_path):
    run_collect([OkCollector()], tmp_path, None, "2026-09-01")
    run_collect([OkCollector()], tmp_path, None, "2026-09-10")
    (tmp_path / "ok_src" / "2026-09-20").mkdir()
    (tmp_path / "ok_src" / "2026-09-20" / "manifest.json").write_text('{"ok": false}', encoding="utf-8")
    assert latest_snapshot(tmp_path, "ok_src").name == "2026-09-10"
    assert latest_snapshot(tmp_path, "none") is None
