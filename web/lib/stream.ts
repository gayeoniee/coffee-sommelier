import type { SseEvent } from "./sse";
import type { Card } from "./types";

export type Explanation = { text: string; status: "streaming" | "done" | "fallback" };

export type StreamState = {
  status: "idle" | "loading" | "done" | "error";
  cards: Card[];
  explanations: Record<string, Explanation>;
  empty: string | null;
  error: string | null;
};

export const initialStream: StreamState = { status: "idle", cards: [], explanations: {}, empty: null, error: null };

function setExplanation(state: StreamState, key: string, ex: Explanation): StreamState {
  return { ...state, explanations: { ...state.explanations, [key]: ex } };
}

export function reduceStream(state: StreamState, ev: SseEvent): StreamState {
  switch (ev.event) {
    case "cards": {
      const cards: Card[] = ev.data.cards;
      return {
        ...state,
        cards,
        explanations: Object.fromEntries(cards.map((c) => [c.key, { text: "", status: "streaming" } as Explanation])),
      };
    }
    case "empty":
      return { ...state, empty: ev.data.reason };
    case "explain_delta": {
      const cur = state.explanations[ev.data.key] ?? { text: "", status: "streaming" };
      return setExplanation(state, ev.data.key, { text: cur.text + ev.data.delta, status: "streaming" });
    }
    case "explain_done":
      return setExplanation(state, ev.data.key, { text: ev.data.text, status: "done" });
    case "explain_fallback":
      return setExplanation(state, ev.data.key, { text: ev.data.text, status: "fallback" });
    case "error":
      return { ...state, status: "error", error: ev.data.message };
    case "done":
      return { ...state, status: state.status === "error" ? "error" : "done" };
    default:
      return state;
  }
}
