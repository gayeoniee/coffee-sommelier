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
    assert len(sleeps) == 2 and all(0 < s <= 1.0 for s in sleeps)


def test_download_writes_file(tmp_path):
    c = make_client({"/f.csv": lambda r: httpx.Response(200, content=b"a,b\n1,2\n")}, [])
    p = c.download("https://a.test/f.csv", tmp_path / "sub" / "f.csv")
    assert p.read_bytes() == b"a,b\n1,2\n"


def test_robots_server_error_disallows_host():
    c = make_client({"/robots.txt": lambda r: httpx.Response(503),
                     "/data": lambda r: httpx.Response(200, text="ok")}, [])
    with pytest.raises(RobotsDisallowed):
        c.get("https://a.test/data")


def test_robots_network_error_disallows_host():
    def down(r):
        raise httpx.ConnectError("down", request=r)
    c = make_client({"/robots.txt": down, "/data": lambda r: httpx.Response(200, text="ok")}, [])
    with pytest.raises(RobotsDisallowed):
        c.get("https://a.test/data")


def test_polite_client_verify_flag_reaches_httpx(monkeypatch):
    import httpx
    from pipeline.http import PoliteClient
    seen = {}
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: seen.update(kw) or real(**{k: v for k, v in kw.items() if k != "verify"}))
    PoliteClient(verify=False)
    assert seen["verify"] is False
