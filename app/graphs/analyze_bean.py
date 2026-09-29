import asyncio
from dataclasses import replace
from typing import TypedDict

import httpx
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.config import PARSE_BEAN_TASK
from app.core.explain import card, template_explanation
from app.core.featuremodel import calibrated_confidence, with_feature_model
from app.core.parse import BeanParse, bean_parse_messages, merge_llm_parse, needs_llm_parse, parse_bean_text
from app.core.predict import (
    FILL_EXCLUDE_SOURCES, FILL_MIN_TAGS, item_from_prediction, predict_from_neighbors, with_model_attrs,
    with_model_tags, with_tag_fill, with_text_cues,
)
from app.core.scoring import passes, score_item
from app.core.tagcooc import with_cooc_fill
from app.core.textcues import text_tags
from app.graphs.common import explain_to_stream
from app.models import Item, ParsedBean, Prediction, Profile, cap_confidence
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
        tag_to_cat, tag_ko = await asyncio.to_thread(deps.repo.taxonomy)
        base_rates = await asyncio.to_thread(deps.repo.tag_base_rates)
        degraded = False
        vec = None
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
        else:
            if deps.tag_fill and len(pred.tags) < FILL_MIN_TAGS:
                # open variant: most of the pool carries no flavor tags, so a thin vote is topped up from the k
                # nearest TAGGED beans (docs/adr/0017-open-tag-fill.md); a later tag model still replaces it
                tagged = await asyncio.to_thread(deps.repo.neighbors, vec, K_NEIGHBORS, parsed.origin_country,
                                                 parsed.process, exclude_sources=FILL_EXCLUDE_SOURCES,
                                                 tagged_only=True)
                pred = with_tag_fill(pred, tagged, tag_ko, base_rates=base_rates)
            if deps.tag_model is not None:
                # the learned tag model (app/core/tagmodel.py) replaces the neighbour-vote's tags when it's
                # loaded and the embedding call succeeded; confidence/n_neighbors stay from predict_from_neighbors.
                pred = with_model_tags(pred, deps.tag_model.tags(vec), tag_ko)
            if deps.attr_model is not None:
                # the learned attribute model (app/core/attrmodel.py) replaces acidity/body/sweetness wherever
                # it shipped a value; an attribute it doesn't cover (e.g. open-variant sweetness) keeps the
                # neighbour average. Evidence/confidence/n_neighbors are untouched either way.
                pred = with_model_attrs(pred, deps.attr_model.predict(vec))
        if deps.feature_model is not None:
            # open variant: the interpretable roaster-gauge feature model (app/core/featuremodel.py) replaces
            # the neighbour average for the attributes it shipped, using that average as one of its features.
            # It needs no embedding, so it also applies in degraded mode (docs/adr/0011-roaster-gauges-feature-
            # model.md). Priority: text cues > feature model > neighbour average.
            pred = with_feature_model(pred, deps.feature_model, parsed, tag_to_cat, tag_ko)
        # explicit cues in the user's own text (app/core/textcues.py) outrank both the model and the neighbour
        # average -- applied last, regardless of degraded/model state (docs/adr/0010-body-heaviness.md).
        pred = with_text_cues(pred, parsed.text, tag_ko=tag_ko, tag_to_cat=tag_to_cat)
        if deps.tag_cooc is not None:
            # open variant: a guest's note word still alone after the vote is topped up from what the licence-clean
            # beans that carry it also say (docs/adr/0018-open-tag-cooccurrence.md); static table, no embedding
            pred = with_cooc_fill(pred, text_tags(parsed.text, tag_to_cat, tag_ko, free_text=True), deps.tag_cooc,
                                  tag_ko)
        if deps.feature_model is not None and not degraded:
            # open variant: calibrated per-attribute confidence from grouped-CV residuals (ADR 0016); the
            # degraded path keeps "low"
            pred = calibrated_confidence(deps.feature_model, pred, parsed, tag_to_cat, tag_ko)
        # a missing core attribute caps the confidence (one -> medium, two+ -> low)
        pred = replace(pred, confidence=cap_confidence(pred.confidence, (pred.acidity, pred.body, pred.sweetness)))
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
        tag_to_cat, tag_ko = await asyncio.to_thread(deps.repo.taxonomy)
        return {"explanation": await explain_to_stream(deps, state["item"], state["profile"], state["score"], tag_ko,
                                                       state.get("prediction"), state.get("violation"),
                                                       tag_to_cat=tag_to_cat)}

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
