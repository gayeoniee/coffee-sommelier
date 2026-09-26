// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET, POST, targetUrl } from "@/app/api/[...path]/route";

const ctx = (path: string[]) => ({ params: Promise.resolve({ path }) });

afterEach(() => vi.unstubAllGlobals());

describe("targetUrl", () => {
  it("strips a trailing slash from the base so the path never gets a double slash", () => {
    expect(targetUrl("http://localhost:8000/", ["me"], "")).toBe("http://localhost:8000/me");
    expect(targetUrl("http://localhost:8000///", ["recommend"], "?x=1")).toBe("http://localhost:8000/recommend?x=1");
    expect(targetUrl("http://localhost:8000", ["me"], "")).toBe("http://localhost:8000/me");
  });
});

describe("API_URL missing in production", () => {
  it("logs once and still defaults to localhost", async () => {
    vi.resetModules();
    const hadApiUrl = "API_URL" in process.env;
    const prevApiUrl = process.env.API_URL;
    delete process.env.API_URL;
    vi.stubEnv("NODE_ENV", "production");
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    try {
      const mod = await import("@/app/api/[...path]/route");
      await mod.GET(new Request("http://localhost:3000/api/me"), ctx(["me"]));
      expect(errorSpy).toHaveBeenCalledTimes(1);
      expect(fetchMock.mock.calls[0][0]).toBe("http://localhost:8000/me");
    } finally {
      errorSpy.mockRestore();
      vi.unstubAllEnvs();
      if (hadApiUrl) process.env.API_URL = prevApiUrl;
    }
  });
});

describe("api proxy", () => {
  it("forwards path, query, cookie and body; returns set-cookie and streamed body", async () => {
    const upstream = new Response("event: done\ndata: {}\n\n", {
      status: 200,
      headers: [["content-type", "text/event-stream"], ["set-cookie", "cs_uid=abc; HttpOnly"], ["x-internal", "no"]],
    });
    const fetchMock = vi.fn().mockResolvedValue(upstream);
    vi.stubGlobal("fetch", fetchMock);
    const req = new Request("http://localhost:3000/api/recommend?x=1", {
      method: "POST", body: '{"brand_key":"brand:sb"}',
      headers: { "content-type": "application/json", cookie: "cs_uid=abc", "x-evil": "1" },
    });
    const res = await POST(req, ctx(["recommend"]));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://localhost:8000/recommend?x=1");
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("cookie")).toBe("cs_uid=abc");
    expect(new Headers(init.headers).get("x-evil")).toBeNull();
    expect(new TextDecoder().decode(init.body)).toBe('{"brand_key":"brand:sb"}');
    expect(res.headers.get("set-cookie")).toContain("cs_uid=abc");
    expect(res.headers.get("x-internal")).toBeNull();
    expect(await res.text()).toBe("event: done\ndata: {}\n\n");
  });

  it("returns 502 when the backend is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    const res = await GET(new Request("http://localhost:3000/api/me"), ctx(["me"]));
    expect(res.status).toBe(502);
    expect(await res.json()).toEqual({ detail: "서버에 연결할 수 없어요" });
  });
});
