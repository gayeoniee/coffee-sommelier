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

// An explanation still streaming when the stream ends will never finish: show the card's template instead.
function settle(state: StreamState): StreamState {
  const explanations = { ...state.explanations };
  for (const c of state.cards) {
    if (explanations[c.key]?.status === "streaming") explanations[c.key] = { text: c.template, status: "fallback" };
  }
  return { ...state, explanations };
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
      return { ...settle(state), status: "error", error: ev.data.message };
    case "done":
      return { ...settle(state), status: state.status === "error" ? "error" : "done" };
    default:
      return state;
  }
}
