import httpx
import pytest

from pipeline.http import PoliteClient, RobotsDisallowed


def make_client(routes, sleeps):
    def handler(request: httpx.Request):
        fn = routes.get(request.url.path)
        return fn(request) if fn else httpx.Response(404)
    return PoliteClient(delay=1.0, transport=httpx.MockTransport(handler), sleep=sleeps.append)


def test_disallowed_by_robots_raises():
    c = make_client({"/robots.txt": lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /private\n"),
                     "/private/x": lambda r: httpx.Response(200, text="secret")}, [])
    with pytest.raises(RobotsDisallowed):
        c.get("https://a.test/private/x")


def test_missing_robots_allows_and_delays_second_request():
    sleeps = []
    c = make_client({"/data": lambda r: httpx.Response(200, text="ok")}, sleeps)
    assert c.get("https://a.test/data").text == "ok"
    assert c.get("https://a.test/data").text == "ok"
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 1.0


def test_download_writes_file(tmp_path):
    c = make_client({"/f.csv": lambda r: httpx.Response(200, content=b"a,b\n1,2\n")}, [])
    p = c.download("https://a.test/f.csv", tmp_path / "sub" / "f.csv")
    assert p.read_bytes() == b"a,b\n1,2\n"
