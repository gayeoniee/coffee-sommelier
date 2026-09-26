import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useStream } from "@/hooks/useStream";

afterEach(() => vi.unstubAllGlobals());

function sseStream(setup: (c: ReadableStreamDefaultController<Uint8Array>) => void) {
  const enc = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(c) {
      const wrapped = {
        enqueue: (s: string) => c.enqueue(enc.encode(s)),
        close: () => c.close(),
        error: (e: unknown) => c.error(e),
      };
      setup(wrapped as unknown as ReadableStreamDefaultController<Uint8Array>);
    },
  });
}

const cardsBlock = 'event: cards\ndata: {"cards":[{"key":"a","name":"a","brand":null,"score":80,"source":"db","confidence":"high","acidity":2,"body":2,"sweetness":2,"tags":[],"tags_ko":[],"is_decaf":false,"order_decaf":false,"decaf_surcharge_krw":null,"caffeine_mg":null,"is_milk":false,"coffee_id":1,"menu_item_id":null,"violation":null,"template":"템플릿 a"}]}\n\n';

describe("useStream", () => {
  it("closing without a done/error event settles streaming explanations and leaves loading", async () => {
    const body = sseStream((c) => {
      (c as unknown as { enqueue: (s: string) => void }).enqueue(cardsBlock);
      (c as unknown as { close: () => void }).close();
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status: 200 })));
    const { result } = renderHook(() => useStream());
    act(() => result.current.start("/recommend", { brand_key: "sb" }));
    await waitFor(() => expect(result.current.state.status).not.toBe("loading"));
    expect(result.current.state.explanations.a).toEqual({ text: "템플릿 a", status: "fallback" });
  });

  it("closing with no cards and no empty reason (a dropped connection) surfaces an error", async () => {
    const body = sseStream((c) => (c as unknown as { close: () => void }).close());
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status: 200 })));
    const { result } = renderHook(() => useStream());
    act(() => result.current.start("/recommend", { brand_key: "sb" }));
    await waitFor(() => expect(result.current.state.status).toBe("error"));
    expect(result.current.state.error).toBe("연결이 끊겼어요. 다시 시도해 주세요");
  });

  it("a mid-read network error settles explanations and sets an error", async () => {
    const enc = new TextEncoder();
    let reads = 0;
    const body = new ReadableStream<Uint8Array>({
      pull(c) {
        reads += 1;
        if (reads === 1) c.enqueue(enc.encode(cardsBlock));
        else c.error(new TypeError("network"));
      },
    });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(body, { status: 200 })));
    const { result } = renderHook(() => useStream());
    act(() => result.current.start("/recommend", { brand_key: "sb" }));
    await waitFor(() => expect(result.current.state.status).toBe("error"));
    expect(result.current.state.error).toBe("추천을 불러오지 못했어요");
    expect(result.current.state.explanations.a).toEqual({ text: "템플릿 a", status: "fallback" });
  });
});
