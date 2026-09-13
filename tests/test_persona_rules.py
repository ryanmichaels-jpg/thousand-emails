"""Rules coverage guard: if a seed change introduces titles the persona rules cannot place,
this fails loudly instead of the constant-OTHER fixture LLM quietly absorbing them.
File-only (no database)."""
from api.scoring.personas import load_rules, rules_coverage


def test_rules_cover_at_least_95_percent_of_distinct_titles():
    coverage, n_titles = rules_coverage(load_rules())
    assert n_titles > 100
    assert coverage >= 0.95, f"rules cover only {coverage:.2%} of {n_titles} distinct titles"
