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


def test_gold_label_judge_and_file_options():
    a = build_parser().parse_args(["gold-label"])
    assert (a.judge, a.file) == ("judge", "gold_enrich.csv")
    a = build_parser().parse_args(["gold-label", "--judge", "judge2", "--file", "gold_enrich_judge2.csv"])
    assert (a.judge, a.file) == ("judge2", "gold_enrich_judge2.csv")


def test_gold_score_file_option():
    a = build_parser().parse_args(["gold-score"])
    assert a.file == "gold_enrich.csv"
    a = build_parser().parse_args(["gold-score", "--file", "gold_enrich_judge2.csv"])
    assert a.file == "gold_enrich_judge2.csv"


def test_gold_agree_defaults_and_options():
    a = build_parser().parse_args(["gold-agree"])
    assert (a.cmd, a.a, a.b) == ("gold-agree", "gold_enrich.csv", "gold_enrich_judge2.csv")
    a = build_parser().parse_args(["gold-agree", "--a", "x.csv", "--b", "y.csv"])
    assert (a.a, a.b) == ("x.csv", "y.csv")
