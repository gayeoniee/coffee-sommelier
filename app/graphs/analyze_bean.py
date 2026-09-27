import asyncio
from dataclasses import replace
from typing import TypedDict

import httpx
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.config import PARSE_BEAN_TASK
from app.core.explain import card, template_explanation
from app.core.parse import BeanParse, bean_parse_messages, merge_llm_parse, needs_llm_parse, parse_bean_text
from app.core.predict import item_from_prediction, predict_from_neighbors
from app.core.scoring import passes, score_item
from app.graphs.common import explain_to_stream
from app.models import Item, ParsedBean, Prediction, Profile
from app.tracing import traced
from pipeline.llm import LLMError

K_NEIGHBORS = 10


class AnalyzeState(TypedDict, total=False):
    text: str | None
    coffee_id: int | None
    profile: Profile
    parsed: ParsedBean
    item: Item | None
    prediction: Prediction | None
    score: float
    violation: str | None
    explanation: dict


def build_analyze_graph(deps):
    async def parse(state: AnalyzeState) -> dict:
        parsed = parse_bean_text(state.get("text") or "")
        if state.get("coffee_id") is None and needs_llm_parse(parsed):
            try:
                llm = await deps.chat_json(PARSE_BEAN_TASK, bean_parse_messages(parsed.text), BeanParse)
                parsed = merge_llm_parse(parsed, llm)
            except (LLMError, httpx.HTTPError):
                pass                                # rules only
        return {"parsed": parsed}

    async def match(state: AnalyzeState) -> dict:
        if state.get("coffee_id") is not None:
            return {"item": await asyncio.to_thread(deps.repo.get_coffee, state["coffee_id"])}
        return {"item": await asyncio.to_thread(deps.repo.match_coffee, state["parsed"].text)}

    def route(state: AnalyzeState) -> str:
        return "score" if state.get("item") else "predict"

    async def predict(state: AnalyzeState) -> dict:
        parsed = state["parsed"]
        _, tag_ko = await asyncio.to_thread(deps.repo.taxonomy)
        base_rates = await asyncio.to_thread(deps.repo.tag_base_rates)
        degraded = False
        try:
            vec = await deps.embed(parsed.text)
            neighbors = await asyncio.to_thread(deps.repo.neighbors, vec, K_NEIGHBORS, parsed.origin_country,
                                                parsed.process)
        except (LLMError, httpx.HTTPError):
            degraded = True
            neighbors = await asyncio.to_thread(deps.repo.fallback_neighbors, parsed.origin_country, parsed.process)
        pred = predict_from_neighbors(neighbors, tag_ko, base_rates=base_rates)
        if degraded:
            pred = replace(pred, confidence="low")
        return {"item": item_from_prediction(parsed, pred), "prediction": pred}

    async def score(state: AnalyzeState) -> dict:
        profile, item = state["profile"], state["item"]
        tag_to_cat, tag_ko = await asyncio.to_thread(deps.repo.taxonomy)
        ok, why = passes(profile, item)
        s = score_item(profile, item, tag_to_cat)
        get_stream_writer()({"type": "cards", "cards": [
            card(item, s, template_explanation(item, profile, s, tag_ko, why), why, state.get("prediction"), tag_ko=tag_ko)]})
        return {"score": s, "violation": why}

    async def explain(state: AnalyzeState) -> dict:
        _, tag_ko = await asyncio.to_thread(deps.repo.taxonomy)
        return {"explanation": await explain_to_stream(deps, state["item"], state["profile"], state["score"], tag_ko,
                                                       state.get("prediction"), state.get("violation"))}

    g = StateGraph(AnalyzeState)
    for name, fn in (("parse", parse), ("match", match), ("predict", predict), ("score", score), ("explain", explain)):
        g.add_node(name, traced(f"analyze.{name}")(fn))
    g.add_edge(START, "parse")
    g.add_edge("parse", "match")
    g.add_conditional_edges("match", route, ["score", "predict"])
    g.add_edge("predict", "score")
    g.add_edge("score", "explain")
    g.add_edge("explain", END)
    return g.compile()
