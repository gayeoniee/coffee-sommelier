"use client";
import { useCallback, useReducer, useRef } from "react";
import { ApiError, openStream } from "@/lib/api";
import { readSse, type SseEvent } from "@/lib/sse";
import { initialStream, reduceStream, type StreamState } from "@/lib/stream";

type Action = { type: "start" } | { type: "event"; ev: SseEvent } | { type: "fail"; message: string } | { type: "reset" };

function reducer(state: StreamState, a: Action): StreamState {
  if (a.type === "start") return { ...initialStream, status: "loading" };
  if (a.type === "reset") return initialStream;
  if (a.type === "fail") return { ...state, status: "error", error: a.message };
  return reduceStream(state, a.ev);
}

export function useStream() {
  const [state, dispatch] = useReducer(reducer, initialStream);
  const abortRef = useRef<AbortController | null>(null);

  const start = useCallback((path: "/recommend" | "/analyze", body: unknown) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    dispatch({ type: "start" });
    (async () => {
      try {
        const stream = await openStream(path, body, ac.signal);
        for await (const ev of readSse(stream)) dispatch({ type: "event", ev });
      } catch (e) {
        if (ac.signal.aborted) return;
        dispatch({ type: "fail", message: e instanceof ApiError ? e.message : "추천을 불러오지 못했어요" });
      }
    })();
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    dispatch({ type: "reset" });
  }, []);

  return { state, start, reset };
}
