from app.repo import _name_tokens


def test_name_tokens_drop_generic_words_and_unify_synonyms():
    assert _name_tokens("원두 에티오피아 사포 모스토 무산소 내추럴") == _name_tokens("에티오피아 사포 모스토 애너로빅 내추럴")
    assert _name_tokens("Colombia Geisha Washed 200g") == {"colombia", "gesha", "washed"}
    assert "원두" not in _name_tokens("원두 케냐 AA")
