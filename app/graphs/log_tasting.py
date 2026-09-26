import asyncio
from typing import TypedDict

import httpx
from langgraph.graph import END, START, StateGraph

from app.config import PARSE_NOTE_TASK
from app.core.learning import update_profile
from app.core.parse import NoteSignals, note_messages
from app.models import Item, Profile
from app.tracing import traced
from pipeline.llm import LLMError


class LogState(TypedDict, total=False):
    user_id: str
    profile: Profile
    item: Item
    rating: int
    note: str | None
    target: dict
    persist_tasting: bool
    signals: dict | None
    new_profile: Profile
    changes: list[str]
    tasting_id: int | None
    summary: str


def build_log_graph(deps):
    async def parse_note(state: LogState) -> dict:
        note = (state.get("note") or "").strip()
        if not note:
            return {"signals": None}
        try:
            parsed = await deps.chat_json(PARSE_NOTE_TASK, note_messages(note), NoteSignals)
            return {"signals": parsed.model_dump(exclude_none=True)}
        except (LLMError, httpx.HTTPError):
            return {"signals": None}

    async def update(state: LogState) -> dict:
        tag_to_cat, _ = await asyncio.to_thread(deps.repo.taxonomy)
        new, changes = update_profile(state["profile"], state["item"], state["rating"], tag_to_cat,
                                      state.get("signals"))
        return {"new_profile": new, "changes": changes}

    async def persist(state: LogState) -> dict:
        tasting_id = None
        if state.get("persist_tasting", True):
            tasting_id = await asyncio.to_thread(
                lambda: deps.repo.save_tasting(state["user_id"], rating=state["rating"], note=state.get("note"),
                                               parsed_signals=state.get("signals"), **state["target"]))
        await asyncio.to_thread(deps.repo.save_profile, state["user_id"], state["new_profile"], tasting_id)
        return {"tasting_id": tasting_id}

    async def summarize(state: LogState) -> dict:
        return {"summary": " · ".join(state["changes"]) if state["changes"] else "취향 변화 없음"}

    g = StateGraph(LogState)
    for name, fn in (("parse_note", parse_note), ("update", update), ("persist", persist), ("summarize", summarize)):
        g.add_node(name, traced(f"log.{name}")(fn))
    g.add_edge(START, "parse_note")
    g.add_edge("parse_note", "update")
    g.add_edge("update", "persist")
    g.add_edge("persist", "summarize")
    g.add_edge("summarize", END)
    return g.compile()
