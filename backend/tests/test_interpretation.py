from app.llm.interpret import Interpretation, Proposal, apply_proposals, interpret, quote_found
from app.spec import completeness
from app.spec.ops import PathError, apply_set, diff_specs, spec_hash
from app.spec.schema import StrategySpec
from app.summary.render import render_text

EXAMPLE = "Use the 10 EMA and 20 EMA with a 1:3 risk-to-reward target."


def test_example_gives_two_separate_indicators_and_no_signal():
    s = StrategySpec()
    res = interpret(s, [], EXAMPLE)
    new = apply_proposals(s, res, [EXAMPLE])
    assert [i.id for i in new.indicators] == ["ema10", "ema20"]
    assert new.target.type == "r_multiple" and new.target.r == 3
    # Naming the indicators must not create any entry rule (no assumed crossover).
    assert new.entry.long is None and new.entry.short is None and new.entry.order is None
    assert new.stop is None and new.timeframe is None and new.sizing is None
    ids = {q.id for q in completeness.check(new)}
    for must in ("timeframe", "entry-trigger", "entry-order", "stop", "sizing", "flat-eod", "commission", "slippage", "same-bar"):
        assert must in ids, must
    txt = render_text(new)
    assert "No relationship between them is assumed" in txt
    assert "crosses" not in txt


def test_proposal_without_user_quote_is_not_applied():
    s = StrategySpec()
    res = Interpretation(engine="test", model=None, proposals=[
        Proposal("timeframe", "5m", "five minute bars"),           # user never said this
        Proposal("instrument", "NQ", "NQ"),                         # user did
        Proposal("stop", {"type": "nonsense"}, "NQ"),               # invalid value
        Proposal("not.a.path", 1, "NQ"),
    ])
    new = apply_proposals(s, res, ["Trade NQ please"])
    st = {p.path: p.status for p in res.proposals}
    assert new.instrument == "NQ" and new.timeframe is None
    assert st == {"timeframe": "needs_confirmation", "instrument": "applied", "stop": "invalid", "not.a.path": "invalid"}


def test_quote_matching_is_whitespace_and_case_insensitive():
    assert quote_found("10  ema", ["Use the 10 EMA please"])
    assert not quote_found("20 sma", ["Use the 10 EMA please"])
    assert not quote_found("", ["anything"])


def test_hash_changes_with_any_rule_and_diff_lists_it():
    a = StrategySpec(instrument="NQ")
    b = apply_set(a, "timeframe", "5m")
    assert spec_hash(a) != spec_hash(b)
    assert diff_specs(a, b) == [{"path": "timeframe", "before": None, "after": "5m"}]
    try:
        apply_set(a, "secret", 1)
        raise AssertionError("should fail")
    except PathError:
        pass


def test_target_none_needs_another_exit():
    s = StrategySpec.model_validate({"target": {"type": "none"}, "directions": "long"})
    assert "no-exit" in {q.id for q in completeness.check(s)}
    s2 = apply_set(s, "management.time_exit_bars", 10)
    assert "no-exit" not in {q.id for q in completeness.check(s2)}
