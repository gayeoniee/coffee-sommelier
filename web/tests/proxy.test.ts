// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET, POST } from "@/app/api/[...path]/route";

const ctx = (path: string[]) => ({ params: Promise.resolve({ path }) });

afterEach(() => vi.unstubAllGlobals());

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
