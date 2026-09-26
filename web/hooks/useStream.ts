"use client";
import { useCallback, useEffect, useReducer, useRef } from "react";
import { ApiError, openStream } from "@/lib/api";
import { readSse, type SseEvent } from "@/lib/sse";
import { initialStream, reduceStream, settle, type StreamState } from "@/lib/stream";

type Action =
  | { type: "start" }
  | { type: "event"; ev: SseEvent }
  | { type: "fail"; message: string }
  | { type: "close" }
  | { type: "reset" };

// The stream closed without a "done" or "error" event (dropped connection, proxy timeout, …).
function close(state: StreamState): StreamState {
  if (state.cards.length === 0 && state.empty === null) {
    return { ...settle(state), status: "error", error: "연결이 끊겼어요. 다시 시도해 주세요" };
  }
  return reduceStream(state, { event: "done", data: {} });
}

function reducer(state: StreamState, a: Action): StreamState {
  if (a.type === "start") return { ...initialStream, status: "loading" };
  if (a.type === "reset") return initialStream;
  if (a.type === "fail") return { ...settle(state), status: "error", error: a.message };
  if (a.type === "close") return close(state);
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
      let sawEnd = false;
      try {
        const stream = await openStream(path, body, ac.signal);
        for await (const ev of readSse(stream)) {
          if (ev.event === "done" || ev.event === "error") sawEnd = true;
          dispatch({ type: "event", ev });
        }
        if (!sawEnd && !ac.signal.aborted) dispatch({ type: "close" });
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

  // Stop the in-flight stream (and its upstream LLM call) when the caller unmounts.
  useEffect(() => () => abortRef.current?.abort(), []);

  return { state, start, reset };
}
