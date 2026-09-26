import asyncio
import operator
from typing import Annotated, TypedDict

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from app.core.explain import card, template_explanation
from app.core.scoring import mmr_top_k, passes, score_item
from app.graphs.common import explain_to_stream
from app.models import Item, Profile
from app.tracing import traced

TOP_K = 3


class RecommendState(TypedDict, total=False):
    brand_key: str
    profile: Profile
    candidates: list[Item]
    ranked: list[dict]
    explanations: Annotated[list[dict], operator.add]


def empty_reason(profile: Profile, candidates: list[Item]) -> str:
    if not candidates:
        return "이 브랜드의 메뉴 정보가 없어요"
    if profile.caffeine_rule == "decaf_only" and not any(i.is_decaf or i.decaf_option for i in candidates):
        return "이 브랜드는 디카페인 메뉴가 없어요"
    return "조건에 맞는 메뉴가 없어요"


def build_recommend_graph(deps):
    async def load(state: RecommendState) -> dict:
        want_decaf = state["profile"].caffeine_rule in ("decaf_only", "low")
        return {"candidates": await asyncio.to_thread(deps.repo.brand_items, state["brand_key"], want_decaf)}

    async def rank(state: RecommendState) -> dict:
        profile, writer = state["profile"], get_stream_writer()
        tag_to_cat, tag_ko = await asyncio.to_thread(deps.repo.taxonomy)
        passed = [i for i in state["candidates"] if passes(profile, i)[0]]
        top = mmr_top_k([(i, score_item(profile, i, tag_to_cat)) for i in passed], tag_to_cat, k=TOP_K)
        if not top:
            writer({"type": "empty", "reason": empty_reason(profile, state["candidates"])})
            return {"ranked": []}
        writer({"type": "cards", "cards": [card(i, s, template_explanation(i, profile, s, tag_ko)) for i, s in top]})
        return {"ranked": [{"item": i, "score": s} for i, s in top]}

    def fan_out(state: RecommendState):
        if not state.get("ranked"):
            return END
        return [Send("explain", {"item": r["item"], "score": r["score"], "profile": state["profile"]})
                for r in state["ranked"]]

    async def explain(payload: dict) -> dict:
        _, tag_ko = await asyncio.to_thread(deps.repo.taxonomy)
        return {"explanations": [await explain_to_stream(deps, payload["item"], payload["profile"],
                                                         payload["score"], tag_ko)]}

    g = StateGraph(RecommendState)
    g.add_node("load", traced("recommend.load")(load))
    g.add_node("rank", traced("recommend.rank")(rank))
    g.add_node("explain", traced("recommend.explain")(explain))
    g.add_edge(START, "load")
    g.add_edge("load", "rank")
    g.add_conditional_edges("rank", fan_out, ["explain", END])
    g.add_edge("explain", END)
    return g.compile()
