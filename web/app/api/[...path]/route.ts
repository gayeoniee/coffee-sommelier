// Deploy targets (e.g. serverless) can leave API_URL unset; fall back to localhost but say so once at boot.
if (!process.env.API_URL && process.env.NODE_ENV === "production") {
  console.error("API_URL이 설정되지 않았어요 — http://localhost:8000로 기본 동작해요");
}

const API_URL = process.env.API_URL ?? "http://localhost:8000";
const FORWARD_REQUEST = ["content-type", "cookie", "accept"];
const FORWARD_RESPONSE = ["content-type", "cache-control"];

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export const maxDuration = 60;

// Exported for tests: a trailing slash on API_URL (easy to leave in a deploy env var) must not
// produce a double slash before the forwarded path.
export function targetUrl(base: string, path: string[], search: string): string {
  return `${base.replace(/\/+$/, "")}/${path.map(encodeURIComponent).join("/")}${search}`;
}

type Ctx = { params: Promise<{ path: string[] }> };

async function forward(req: Request, { params }: Ctx): Promise<Response> {
  const { path } = await params;
  const url = new URL(req.url);
  const target = targetUrl(API_URL, path, url.search);
  const headers = new Headers();
  for (const h of FORWARD_REQUEST) {
    const v = req.headers.get(h);
    if (v) headers.set(h, v);
  }
  const hasBody = !["GET", "HEAD"].includes(req.method);
  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body: hasBody ? await req.arrayBuffer() : undefined,
      cache: "no-store",
      redirect: "manual",
      signal: req.signal, // client gone (e.g. new search) → stop the upstream LLM stream too
    });
  } catch {
    return Response.json({ detail: "서버에 연결할 수 없어요" }, { status: 502 });
  }
  const out = new Headers();
  for (const h of FORWARD_RESPONSE) {
    const v = upstream.headers.get(h);
    if (v) out.set(h, v);
  }
  for (const c of upstream.headers.getSetCookie()) out.append("set-cookie", c);
  if (out.get("content-type")?.startsWith("text/event-stream")) out.set("x-accel-buffering", "no");
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
