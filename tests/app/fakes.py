import asyncio
import itertools
import uuid

from app.graphs import Deps
from app.models import Item, Neighbor, Profile
from pipeline.llm import LLMError

TAG_TO_CAT = {"lemon": "fruity", "jasmine": "floral", "chocolate": "nutty/cocoa", "caramelized": "sweet"}
TAG_KO = {"lemon": "레몬", "chocolate": "초콜릿"}


class FakeRepo:
    def __init__(self):
        self.users: dict[str, str | None] = {}
        self.profiles: dict[str, Profile] = {}
        self.history_rows: dict[str, list[dict]] = {}
        self.tastings: list[dict] = []
        self.coffees = {
            1: Item(key="coffee:1", name="Ethiopia Yirgacheffe Washed", source="db", acidity=5, body=2, sweetness=3,
                    tags=("lemon", "jasmine"), coffee_id=1, origin_country="Ethiopia", process="washed"),
            2: Item(key="coffee:2", name="Brazil Cerrado", source="db", acidity=1, body=4, sweetness=3,
                    tags=("chocolate",), coffee_id=2, origin_country="Brazil", process="natural"),
        }
        self.menu = {
            "brand:sb": [
                Item(key="menu:10", name="아메리카노", source="brand_bean", acidity=2, body=4, sweetness=2,
                     tags=("chocolate",), decaf_option=True, caffeine_mg=150, brand="스타벅스",
                     decaf_surcharge_krw=300, menu_item_id=10, confidence="medium"),
                Item(key="menu:11", name="카페 라떼", source="brand_bean", acidity=2, body=4, sweetness=3,
                     tags=("chocolate",), decaf_option=True, caffeine_mg=75, is_milk=True, brand="스타벅스",
                     menu_item_id=11, confidence="medium"),
                Item(key="menu:12", name="블론드 아메리카노", source="brand_bean", acidity=4, body=2, sweetness=3,
                     tags=("lemon",), decaf_option=False, caffeine_mg=170, brand="스타벅스", menu_item_id=12,
                     confidence="medium"),
                Item(key="menu:13", name="콜드 브루", source="brand_bean", acidity=3, body=3, sweetness=2,
                     tags=("caramelized",), decaf_option=False, caffeine_mg=155, brand="스타벅스", menu_item_id=13,
                     confidence="medium"),
            ],
            "brand:nodecaf": [Item(key="menu:20", name="에스프레소", source="brand_bean", acidity=3, body=4,
                                   sweetness=2, caffeine_mg=120, brand="X", menu_item_id=20, confidence="medium")],
        }
        self.tasting_ids = itertools.count(1)

    # users/profiles
    def create_user(self):
        uid = str(uuid.uuid4())
        self.users[uid] = None
        return uid

    def user_exists(self, uid):
        return uid in self.users

    def set_nickname(self, uid, nickname):
        self.users[uid] = nickname

    def get_nickname(self, uid):
        return self.users.get(uid)

    def get_profile(self, uid):
        return self.profiles.get(uid)

    def save_profile(self, uid, p, tasting_id=None):
        self.profiles[uid] = p
        self.history_rows.setdefault(uid, []).insert(0, {"snapshot": p.to_dict(), "tasting_id": tasting_id})

    def history(self, uid, limit=20):
        return self.history_rows.get(uid, [])[:limit]

    def recent_tastings(self, uid, limit=20):
        return [t for t in reversed(self.tastings) if t["user_id"] == uid][:limit]

    def save_tasting(self, uid, **kw):
        tid = next(self.tasting_ids)
        self.tastings.append({"id": tid, "user_id": uid, **kw})
        return tid

    # catalog
    def taxonomy(self):
        return TAG_TO_CAT, TAG_KO

    def list_brands(self):
        return [{"key": "brand:sb", "name": "스타벅스", "decaf_available": True, "decaf_surcharge_krw": 300,
                 "notes": "", "has_menu": True}]

    def brand_items(self, brand_key, want_decaf):
        items = self.menu.get(brand_key, [])
        if not want_decaf:
            return list(items)
        from dataclasses import replace
        return [replace(i, order_decaf=bool(i.decaf_option and not i.is_decaf
                                             and not (i.caffeine_mg is not None and i.caffeine_mg <= 100)))
                for i in items]

    def get_menu_item(self, menu_item_id, want_decaf):
        for brand_key in self.menu:
            for i in self.brand_items(brand_key, want_decaf):
                if i.menu_item_id == menu_item_id:
                    return i
        return None

    def get_coffee(self, coffee_id):
        return self.coffees.get(coffee_id)

    def match_coffee(self, text):
        t = " ".join((text or "").split()).lower()
        return next((c for c in self.coffees.values() if c.name.lower() == t), None)

    def search_coffees(self, q, limit=8):
        q = (q or "").strip().lower()
        if len(q) < 2:
            return []
        return [{"id": c.coffee_id, "name": c.name, "roaster": "R", "origin_country": c.origin_country,
                 "is_decaf": c.is_decaf} for c in self.coffees.values() if q in c.name.lower()][:limit]

    def neighbors(self, vec, k=10, origin=None, process=None, exclude_id=None):
        return [Neighbor(100 + i, f"n{i}", 0.9 - i * 0.05, 4 + (i % 2) * 0.5, 2, 3, ("lemon",)) for i in range(k)]

    def fallback_neighbors(self, origin, process, limit=50):
        if not origin and not process:
            return []
        return [Neighbor(200 + i, f"f{i}", 1.0, 3, 3, 3, ("chocolate",)) for i in range(4)]

    def sample_coffees(self):
        return [{"coffee_id": 1, "name": "Ethiopia Yirgacheffe Washed", "summary": "bright", "tags": ["lemon"]},
                {"coffee_id": 2, "name": "Brazil Cerrado", "summary": "choco", "tags": ["chocolate"]}]


def fake_deps(repo=None, tokens=("잘 ", "맞아요"), fail_keys=(), parse=None, embed_fails=False,
              json_fails=False) -> Deps:
    repo = repo or FakeRepo()
    calls = {"stream": 0, "json": 0, "embed": 0}

    async def stream_text(task, messages):
        calls["stream"] += 1
        content = messages[-1]["content"]
        if any(k in content for k in fail_keys):
            raise LLMError("boom")
        for t in tokens:
            await asyncio.sleep(0)
            yield t

    async def chat_json(task, messages, schema):
        calls["json"] += 1
        if json_fails:
            raise LLMError("json boom")
        return schema.model_validate(parse or {})

    async def embed(text):
        calls["embed"] += 1
        if embed_fails:
            raise LLMError("embed boom")
        return [0.0] * 8

    d = Deps(repo=repo, embed=embed, stream_text=stream_text, chat_json=chat_json)
    d.calls = calls
    return d


def run_events(graph, inputs) -> list[dict]:
    async def go():
        return [chunk async for chunk in graph.astream(inputs, stream_mode="custom")]
    return asyncio.run(go())
