import { describe, expect, it } from "vitest";
import { parseBlock, readSse } from "@/lib/sse";

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(c) {
      chunks.forEach((s) => c.enqueue(enc.encode(s)));
      c.close();
    },
  });
}

async function collect(chunks: string[]) {
  const out = [];
  for await (const ev of readSse(streamOf(chunks))) out.push(ev);
  return out;
}

describe("sse", () => {
  it("parses one block", () => {
    expect(parseBlock('event: cards\ndata: {"cards": []}')).toEqual({ event: "cards", data: { cards: [] } });
    expect(parseBlock("event: done")).toBeNull();
  });

  it("joins events split across chunks, including multi-byte Korean", async () => {
    const full = 'event: explain_delta\ndata: {"key":"a","delta":"산미가 "}\n\nevent: done\ndata: {}\n\n';
    const bytes = new TextEncoder().encode(full);
    const cut = 30;                                   // split inside the first event (and inside a Hangul char)
    const dec = new TextDecoder();
    const chunks = [dec.decode(bytes.slice(0, cut), { stream: true }), dec.decode(bytes.slice(cut))];
    const events = await collect(chunks);
    expect(events).toEqual([
      { event: "explain_delta", data: { key: "a", delta: "산미가 " } },
      { event: "done", data: {} },
    ]);
  });

  it("handles a final block without trailing blank line", async () => {
    expect(await collect(['event: done\ndata: {}'])).toEqual([{ event: "done", data: {} }]);
  });
});
