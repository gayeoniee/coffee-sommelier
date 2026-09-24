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


import httpx

from pipeline.collect.datasets import CQI, KaggleCollector, UrlFilesCollector
from pipeline.http import PoliteClient


def test_url_files_collector_downloads_each_file(tmp_path):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, content=b"x") if request.url.path != "/robots.txt" else httpx.Response(404)

    http = PoliteClient(delay=0, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    c = UrlFilesCollector("t", {"a.csv": "https://h.test/a.csv", "b.json": "https://h.test/b.json"})
    files = c.collect(tmp_path, http)
    assert sorted(f.name for f in files) == ["a.csv", "b.json"]
    assert "/a.csv" in seen and "/b.json" in seen


def test_cqi_collector_targets_three_files():
    assert set(CQI.urls) == {"arabica_2018.csv", "robusta_2018.csv", "arabica_2023.csv"}


def test_kaggle_collector_uses_downloader(tmp_path):
    def fake_dl(dataset, dest):
        (dest / "data.csv").write_text("a\n1\n", encoding="utf-8")

    c = KaggleCollector(datasets=("own/one", "own/two"), downloader=fake_dl)
    files = c.collect(tmp_path, http=None)
    assert [f.relative_to(tmp_path).as_posix() for f in files] == ["own__one/data.csv", "own__two/data.csv"]
