import asyncio
import json
import logging
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from app import config, telemetry, tracing
from app.core.flavors import PREFERENCE_CHIPS
from app.graphs import default_deps
from app.graphs.analyze_bean import build_analyze_graph
from app.graphs.log_tasting import build_log_graph
from app.graphs.recommend import build_recommend_graph
from app.models import Item, Profile

log = logging.getLogger("coffee.api")


class ProfileIn(BaseModel):
    caffeine_rule: Literal["decaf_only", "low", "any"]
    milk_ok: bool
    acidity: float = Field(ge=1, le=5)
    body: float = Field(ge=1, le=5)
    sweetness: float = Field(ge=1, le=5)
    flavor_likes: list[Literal[PREFERENCE_CHIPS]] = Field(default_factory=list)  # type: ignore[valid-type]
    nickname: str | None = Field(default=None, max_length=30)


class NicknameIn(BaseModel):
    nickname: str | None = Field(default=None, max_length=30)


class SampleIn(BaseModel):
    coffee_id: int
    liked: bool


class RecommendIn(BaseModel):
    brand_key: str


class AnalyzeIn(BaseModel):
    text: str | None = Field(default=None, max_length=300)
    coffee_id: int | None = None

    @model_validator(mode="after")
    def one_input(self):
        if not (self.text and self.text.strip()) and self.coffee_id is None:
            raise ValueError("text 또는 coffee_id가 필요해요")
        return self


class PredictedIn(BaseModel):
    acidity: float | None = None
    body: float | None = None
    sweetness: float | None = None
    tags: list[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=10)
    is_decaf: bool = False


class TastingIn(BaseModel):
    """Exactly one target: a DB coffee, a scraped menu item, or free text.

    Recommend cards of brands without scraped menus have menu_item_id = None. To log one, the client sends
    input_text = f"{card.brand} {card.name}" and predicted = the card's acidity/body/sweetness/tags/is_decaf.
    """
    coffee_id: int | None = None
    menu_item_id: int | None = None
    input_text: str | None = Field(default=None, max_length=300)
    predicted: PredictedIn | None = None
    order_decaf: bool = False
    rating: int = Field(ge=1, le=5)
    note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def exactly_one_target(self):
        if sum(x is not None for x in (self.coffee_id, self.menu_item_id, self.input_text)) != 1:
            raise ValueError("coffee_id, menu_item_id, input_text 중 하나만 보내 주세요")
        return self


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def create_app(repo=None, deps=None, cookie_secure: bool | None = None) -> FastAPI:
    if repo is None:
        from app.repo import Repo
        from pipeline import settings
        repo = Repo(settings.DATABASE_URL)
    deps = deps or default_deps(repo)
    telemetry.configure()
    secure = config.cookie_secure() if cookie_secure is None else cookie_secure
    graphs = {"recommend": build_recommend_graph(deps), "analyze": build_analyze_graph(deps),
              "log": build_log_graph(deps)}

    app = FastAPI(title="Coffee Sommelier API", version="0.2.0")
    app.add_middleware(CORSMiddleware, allow_origins=config.allowed_origins(), allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    def current_user(request: Request) -> str:
        uid = request.cookies.get(config.COOKIE_NAME)
        if not repo.user_exists(uid):
            raise HTTPException(401, "세션이 없어요. /session을 먼저 호출하세요")
        return uid

    def profile_of(uid: str) -> Profile:            # sync: call via asyncio.to_thread from async endpoints
        p = repo.get_profile(uid)
        if p is None:
            raise HTTPException(409, "온보딩이 필요해요")
        return p

    def stream(graph, inputs: dict, name: str) -> StreamingResponse:
        async def gen():
            tok = telemetry.begin(name, **(
                {"brand": inputs["brand_key"]} if name == "recommend"
                else {"input": "coffee_id" if inputs.get("coffee_id") is not None else "text"}))
            completed = False
            try:
                with tracing.span(name):
                    try:
                        async for event in graph.astream(inputs, stream_mode="custom"):
                            if event["type"] == "cards":
                                telemetry.add("cards", len(event["cards"]))
                            yield _sse(event["type"], event)
                    except Exception:           # never leak a traceback into the stream
                        log.exception("%s stream failed", name)
                        telemetry.add("error", True)
                        yield _sse("error", {"message": "처리 중 오류가 발생했어요"})
                completed = True
                yield _sse("done", {})
            finally:                            # also on client disconnect (CancelledError / GeneratorExit)
                tracing.flush()
                telemetry.end(tok, aborted=not completed)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.post("/session")
    def session(request: Request, response: Response):
        uid = request.cookies.get(config.COOKIE_NAME)
        new = not repo.user_exists(uid)
        if new:
            uid = repo.create_user()
            response.set_cookie(config.COOKIE_NAME, uid, max_age=config.COOKIE_MAX_AGE, httponly=True,
                                secure=secure, samesite=config.cookie_samesite())
        return {"user_id": uid, "new": new, "has_profile": repo.get_profile(uid) is not None}

    @app.get("/me")
    def me(uid: str = Depends(current_user)):
        p = repo.get_profile(uid)
        return {"user_id": uid, "nickname": repo.get_nickname(uid), "profile": p.to_dict() if p else None,
                "history": repo.history(uid), "tastings": repo.recent_tastings(uid)}

    @app.put("/me/profile")
    def put_profile(body: ProfileIn, uid: str = Depends(current_user)):
        old = repo.get_profile(uid)
        weights = dict(old.flavor_weights) if old else {}
        # the chips replace the previous likes; learned dislikes (negative weights) stay
        weights.update({c: 0.0 for c, w in weights.items() if w > 0 and c not in body.flavor_likes})
        weights.update({c: max(weights.get(c, 0.0), 0.5) for c in body.flavor_likes})
        p = Profile(caffeine_rule=body.caffeine_rule, milk_ok=body.milk_ok, acidity=body.acidity, body=body.body,
                    sweetness=body.sweetness, flavor_weights=weights, n_updates=old.n_updates if old else 0)
        repo.save_profile(uid, p)
        if body.nickname is not None:
            repo.set_nickname(uid, body.nickname.strip() or None)
        return {"profile": p.to_dict()}

    @app.put("/me/nickname")
    def put_nickname(body: NicknameIn, uid: str = Depends(current_user)):
        nick = (body.nickname or "").strip() or None
        repo.set_nickname(uid, nick)
        return {"nickname": nick}

    @app.get("/onboarding/samples")
    def samples(uid: str = Depends(current_user)):
        return repo.sample_coffees()

    @app.post("/onboarding/samples")
    async def post_samples(body: Annotated[list[SampleIn], Field(max_length=3)], uid: str = Depends(current_user)):
        p = await asyncio.to_thread(profile_of, uid)
        if p.n_updates > 0:
            raise HTTPException(409, "이미 온보딩을 마쳤어요")
        for s in body:
            item = await asyncio.to_thread(repo.get_coffee, s.coffee_id)
            if item is None:
                raise HTTPException(404, f"원두 {s.coffee_id}를 찾을 수 없어요")
            out = await graphs["log"].ainvoke({"user_id": uid, "profile": p, "item": item,
                                               "rating": 5 if s.liked else 1, "note": None, "target": {},
                                               "persist_tasting": False})
            p = out["new_profile"]
        return {"profile": p.to_dict()}

    @app.get("/brands")
    def brands(uid: str = Depends(current_user)):
        return repo.list_brands()

    @app.get("/coffees/search")
    def search(q: str = Query("", max_length=100), uid: str = Depends(current_user)):
        return repo.search_coffees(q)

    @app.post("/recommend")
    def recommend(body: RecommendIn, uid: str = Depends(current_user)):
        return stream(graphs["recommend"], {"brand_key": body.brand_key, "profile": profile_of(uid)}, "recommend")

    @app.post("/analyze")
    async def analyze(body: AnalyzeIn, uid: str = Depends(current_user)):
        p = await asyncio.to_thread(profile_of, uid)
        if body.coffee_id is not None and await asyncio.to_thread(repo.get_coffee, body.coffee_id) is None:
            raise HTTPException(404, f"원두 {body.coffee_id}를 찾을 수 없어요")
        return stream(graphs["analyze"], {"text": body.text, "coffee_id": body.coffee_id, "profile": p}, "analyze")

    @app.post("/tastings")
    async def tastings(body: TastingIn, uid: str = Depends(current_user)):
        p = await asyncio.to_thread(profile_of, uid)
        rule = "decaf_only" if body.order_decaf else p.caffeine_rule
        if body.coffee_id is not None:
            item = await asyncio.to_thread(repo.get_coffee, body.coffee_id)
            target = {"coffee_id": body.coffee_id}
        elif body.menu_item_id is not None:
            item = await asyncio.to_thread(repo.get_menu_item, body.menu_item_id, rule)
            target = {"menu_item_id": body.menu_item_id}
        else:
            pred = body.predicted or PredictedIn()
            item = Item(key="input", name=body.input_text, source="predicted", acidity=pred.acidity, body=pred.body,
                        sweetness=pred.sweetness, tags=tuple(pred.tags), is_decaf=pred.is_decaf)
            target = {"input_text": body.input_text, "predicted": pred.model_dump()}
        if item is None:
            raise HTTPException(404, "대상을 찾을 수 없어요")
        with tracing.span("log_tasting"):
            out = await graphs["log"].ainvoke({"user_id": uid, "profile": p, "item": item, "rating": body.rating,
                                               "note": body.note, "target": target, "persist_tasting": True})
        tracing.flush()
        return {"summary": out["summary"], "changes": out["changes"], "profile": out["new_profile"].to_dict()}

    return app


def get_app() -> FastAPI:
    """uvicorn factory: `uv run uvicorn app.api:get_app --factory`"""
    return create_app()
