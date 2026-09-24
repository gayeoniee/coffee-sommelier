from pipeline.__main__ import STAGES, build_parser


def test_run_defaults_to_all_stages():
    a = build_parser().parse_args(["run"])
    assert a.cmd == "run" and a.only is None and STAGES == ["collect", "normalize", "enrich", "embed", "load"]


def test_run_options():
    a = build_parser().parse_args(["run", "--only", "collect", "--only", "normalize", "--source", "mega", "--limit", "5", "--retry-failed"])
    assert (a.only, a.source, a.limit, a.retry_failed) == (["collect", "normalize"], ["mega"], 5, True)


def test_query_decaf_flags():
    p = build_parser()
    assert p.parse_args(["query", "x"]).decaf is None
    assert p.parse_args(["query", "x", "--decaf"]).decaf is True
    assert p.parse_args(["query", "x", "--no-decaf", "-k", "3"]).decaf is False


def test_gold_label_command_exists():
    assert build_parser().parse_args(["gold-label"]).cmd == "gold-label"
