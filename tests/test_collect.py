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


import json as _json

from pipeline.collect.web import ComposeCollector, MegaCollector, PaikCollector, ShopifyCollector, StarbucksCollector


def mock_http(handler):
    def wrapped(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return handler(request)
    return PoliteClient(delay=0, transport=httpx.MockTransport(wrapped), sleep=lambda s: None)


def test_starbucks_skips_non_json_codes(tmp_path):
    def handler(request):
        if request.url.path.endswith("W0000003.js"):
            return httpx.Response(200, text='{"list": [{"product_NM": "아메리카노"}]}')
        return httpx.Response(200, text="<html>not json</html>")

    files = StarbucksCollector(codes=("W0000003", "W0000001")).collect(tmp_path, mock_http(handler))
    assert [f.name for f in files] == ["W0000003.json"]


def test_mega_stops_at_empty_page(tmp_path):
    def handler(request):
        page = int(request.url.params["page"])
        body = '<li><a class="inner_modal_open"></a></li>' if page <= 2 else "<ul></ul>"
        return httpx.Response(200, text=body)

    files = MegaCollector().collect(tmp_path, mock_http(handler))
    assert [f.name for f in files] == ["page_1.html", "page_2.html"]


def test_paik_saves_page(tmp_path):
    files = PaikCollector().collect(tmp_path, mock_http(lambda r: httpx.Response(200, text="<div class='hover'></div>")))
    assert files[0].read_text(encoding="utf-8") == "<div class='hover'></div>"


def test_shopify_paginates(tmp_path):
    def handler(request):
        page = int(request.url.params["page"])
        products = [{"id": page, "title": f"p{page}"}] if page <= 2 else []
        return httpx.Response(200, json={"products": products})

    files = ShopifyCollector(domains=("shop.test",)).collect(tmp_path, mock_http(handler))
    data = _json.loads(files[0].read_text(encoding="utf-8"))
    assert files[0].name == "shop.test.json"
    assert [p["id"] for p in data["products"]] == [1, 2]


def test_compose_stops_at_page_with_no_items(tmp_path):
    def handler(request):
        page = int(request.url.params["page"])
        body = ('<td class="text-center" data-label="품목명">아메리카노</td>' if page <= 2
                else '<tr><td colspan="5">등록된 상품이 없습니다.</td></tr>')
        return httpx.Response(200, text=body)

    files = ComposeCollector().collect(tmp_path, mock_http(handler))
    assert [f.name for f in files] == ["page_1.html", "page_2.html"]


def test_compose_sends_coffee_category_tag(tmp_path):
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, text="<div></div>")

    ComposeCollector().collect(tmp_path, mock_http(handler))
    assert seen[0]["search_tag"] == "02. 커피ㆍ콜드브루" and seen[0]["tab"] == "nutrition"


def test_registry_lists_all_sources():
    from pipeline.collect.registry import ALL_COLLECTORS
    assert [c.name for c in ALL_COLLECTORS] == [
        "cqi", "roasterdb", "sca_wheel", "coffeereview_kaggle", "starbucks", "mega", "paik", "shopify", "compose"]
