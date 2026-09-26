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
from pipeline.collect.web import CoffeebeanCollector, MegaCollector, PaikCollector, ShopifyCollector, StarbucksCollector
from pipeline.collect.web import MegaCollector, PaikCollector, PaulbassettCollector, ShopifyCollector, StarbucksCollector


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
def test_coffeebean_paginates_each_category_and_stops_at_empty_page(tmp_path):
    per_category_pages = {13: 2, 14: 1, 12: 1}  # 에스프레소 음료, 브루드 커피, 아이스 블렌디드 (COFFEE)

    def handler(request):
        cid = int(request.url.params["category"])
        page = int(request.url.params.get("page", 1))
        if page > per_category_pages[cid]:
            return httpx.Response(200, text="<ul class='menu_list'></ul>")
        return httpx.Response(200, text=(
            f'<div class="category2"><a class="select_a">cat{cid}</a></div>'
            f'<ul class="menu_list"><li><dl class="txt"><dt><span class="kor">item{cid}-{page}</span></dt></dl></li></ul>'
        ))

    files = CoffeebeanCollector().collect(tmp_path, mock_http(handler))
    assert [f.name for f in files] == [
        "cat13_page1.html", "cat13_page2.html", "cat14_page1.html", "cat12_page1.html"]


def test_registry_lists_all_sources():
    from pipeline.collect.registry import ALL_COLLECTORS
    assert [c.name for c in ALL_COLLECTORS] == [
        "cqi", "roasterdb", "sca_wheel", "coffeereview_kaggle", "starbucks", "mega", "paik", "shopify", "coffeebean", "compose", "hollys", "paulbassett"]


def test_hollys_saves_espresso_page(tmp_path):
    from pipeline.collect.web import HollysCollector

    files = HollysCollector().collect(tmp_path, mock_http(lambda r: httpx.Response(200, text="<div class='menu_view01'></div>")))
    assert [f.name for f in files] == ["espresso.html"]
    assert files[0].read_text(encoding="utf-8") == "<div class='menu_view01'></div>"


def test_paulbassett_collector_is_a_dataclass():
    """Same shape as the other collectors (StarbucksCollector, HollysCollector, ...): a @dataclass
    with a `name` field customizable via the constructor, not a plain class with a bare annotation."""
    import dataclasses

    from pipeline.collect.web import PaulbassettCollector

    assert dataclasses.is_dataclass(PaulbassettCollector)
    assert PaulbassettCollector().name == "paulbassett"
    assert PaulbassettCollector(name="x").name == "x"


def test_paulbassett_builds_insecure_client_from_passed_in_delay(tmp_path, monkeypatch):
    """The site's TLS chain is self-signed: the collector must build its own verify=False client
    (the only place this project disables verification), reusing only the passed-in http's delay."""
    import pipeline.collect.web as web

    created = {}

    def fake_polite_client(*, delay, verify):
        created["delay"] = delay
        created["verify"] = verify
        return mock_http(lambda r: httpx.Response(200, text="<div class='menuList'></div>"))

    monkeypatch.setattr(web, "PoliteClient", fake_polite_client)
    passed_in = PoliteClient(delay=2.5, transport=httpx.MockTransport(lambda r: httpx.Response(404)), sleep=lambda s: None)
    files = PaulbassettCollector().collect(tmp_path, passed_in)
    assert created == {"delay": 2.5, "verify": False}
    assert [f.name for f in files] == ["list.html"]


def test_paulbassett_saves_list_and_detail_pages(tmp_path, monkeypatch):
    import pipeline.collect.web as web

    list_html = (
        "<div class='menuList'>"
        "<a onclick=\"goView('PB1');return false;\"></a>"
        "<a onclick=\"goView('PB2');return false;\"></a>"
        "</div>"
    )

    def handler(request):
        if request.url.path == "/menu/List.pb":
            return httpx.Response(200, text=list_html)
        dpid = request.url.params["dpid"]
        return httpx.Response(200, text=f"<div>{dpid}</div>")

    monkeypatch.setattr(web, "PoliteClient",
                        lambda *, delay, verify: mock_http(handler))
    files = PaulbassettCollector().collect(tmp_path, PoliteClient(delay=1.0, sleep=lambda s: None))
    assert sorted(f.name for f in files) == ["PB1.html", "PB2.html", "list.html"]
    assert (tmp_path / "PB1.html").read_text(encoding="utf-8") == "<div>PB1</div>"
