"""Unit tests for the pure spend aggregator.

The fixtures are built from :data:`REAL_ENTRY_LINE` — the same verbatim line from
this repository's ``.factory/metrics/ledger.jsonl`` that ``test_ledger.py`` reads,
copied here rather than imported so neither test file's fixtures can be changed
out from under the other. It matters that it is real: it carries THREE models in
one ``by_model`` object at the factory's own eight-place cost figures, which is
the case a hand-simplified stand-in would not have.

Covers the attribution rule by name, the multi-model breakdown at exact figures,
totals checked against an independently computed sum (an aggregate checked only
against itself proves arithmetic, not correctness), the zeroed empty case, and the
by-level cut.
"""

from __future__ import annotations

import json
import math

from factory_console.domain.ledger import LedgerEntry
from factory_console.domain.spend import ATTRIBUTION_RULE, COST_DECIMAL_PLACES
from factory_console.domain.spend_calc import aggregate

# A real ledger line, verbatim — the same fixture ``tests/unit/test_ledger.py``
# uses (see this module's docstring). Note the three models in ``by_model`` and the
# session id, which must never reach a spend projection.
REAL_ENTRY_LINE = (
    '{"ts":"2026-07-30T16:33:22Z","agent":"lead","level":"ticket","ids":["T71"],'
    '"model":"sonnet","effort":"medium","wall_min":12,"turns":27,"peak_context":133027,'
    '"tokens":{"input":8546,"output":40143,"cache_read":7261803,'
    '"cache_creation":232826,"total":7543318},'
    '"cost_usd":5.740558350000003,"cost_scope":"lane",'
    '"session_id":"81dda660-3f1a-4c67-9f0e-2b7c5d9a1e04",'
    '"review_tier":null,"sessions":1,'
    '"by_model":{'
    '"claude-haiku-4-5-20251001":{"input":112,"output":903,"cache_read":0,'
    '"cache_creation":0,"cost_usd":0.0041205},'
    '"claude-sonnet-5":{"input":8434,"output":39240,"cache_read":7261803,'
    '"cache_creation":232826,"cost_usd":5.02143785},'
    '"claude-opus-4-8[1m]":{"input":0,"output":0,"cache_read":0,'
    '"cache_creation":0,"cost_usd":0.7150000}}}'
)


def _entry(**overrides: object) -> LedgerEntry:
    """Build a LedgerEntry from the real line, with fields overridden.

    Overriding the parsed DOCUMENT rather than the model keeps every fixture on
    the real line's shape — including the fields the console does not model.
    """
    document = json.loads(REAL_ENTRY_LINE)
    document.update(overrides)
    return LedgerEntry.model_validate(document)


# --------------------------------------------------------------------------- #
# The attribution rule: full cost to each id, and it says so by name
# --------------------------------------------------------------------------- #


def test_attribution_rule_is_reported_by_name() -> None:
    # The numbers alone cannot tell a client whether costs were split or repeated,
    # so the rule travels with them. Asserted by literal, not by re-reading the
    # constant the code used: renaming the rule is a wire-contract change.
    assert aggregate([_entry()]).attribution == "full-to-each-id"
    assert ATTRIBUTION_RULE == "full-to-each-id"


def test_a_multi_id_entry_gives_its_full_cost_to_every_id() -> None:
    entry = _entry(ids=["T1", "T2"], cost_usd=4.0)

    report = aggregate([entry])

    assert {row.ticketId: row.attributedCostUsd for row in report.byTicket} == {
        "T1": 4.0,
        "T2": 4.0,
    }, "each named ticket gets the FULL cost — not half of it"
    assert report.totals.costUsd == 4.0, "the lane still cost four dollars, once"
    attributed = sum(row.attributedCostUsd for row in report.byTicket)
    assert attributed > report.totals.costUsd, (
        "attributed cost exceeding the true total is the documented consequence"
    )


def test_a_repeated_id_on_one_entry_is_attributed_once() -> None:
    # "Each id" means each DISTINCT id: one lane naming a ticket twice is still
    # one lane, and charging it twice would be double attribution to a single id.
    report = aggregate([_entry(ids=["T1", "T1"], cost_usd=4.0)])

    (row,) = report.byTicket
    assert row.ticketId == "T1"
    assert row.attributedCostUsd == 4.0
    assert row.entries == 1


def test_an_entry_naming_no_ticket_still_counts_in_the_totals() -> None:
    report = aggregate([_entry(ids=[], cost_usd=2.5)])

    assert report.byTicket == [], "there is no ticket to attribute it to"
    assert report.totals.costUsd == 2.5, "but the money was still spent"
    assert report.totals.entries == 1


def test_ticket_rows_carry_the_models_that_ticket_used() -> None:
    (row,) = aggregate([_entry(ids=["T71"])]).byTicket

    assert row.models == [
        "claude-haiku-4-5-20251001",
        "claude-opus-4-8[1m]",
        "claude-sonnet-5",
    ], "sorted, and verbatim as the factory wrote them"


# --------------------------------------------------------------------------- #
# byModel: every model of a multi-model entry, at the factory's exact figures
# --------------------------------------------------------------------------- #


def test_a_multi_model_entry_contributes_under_each_by_model_key_exactly() -> None:
    # THE case the real fixture exists for: one lane, three models, and the
    # per-model figures are the interesting part rather than a detail.
    report = aggregate([_entry()])

    by_model = {row.model: row for row in report.byModel}
    assert set(by_model) == {
        "claude-haiku-4-5-20251001",
        "claude-sonnet-5",
        "claude-opus-4-8[1m]",
    }, "model ids are kept verbatim, never mapped to display names"
    # Exact, not approximate: these are the factory's own figures and the console
    # must reproduce them.
    assert by_model["claude-sonnet-5"].costUsd == 5.02143785
    assert by_model["claude-haiku-4-5-20251001"].costUsd == 0.0041205
    assert by_model["claude-opus-4-8[1m]"].costUsd == 0.715
    assert by_model["claude-sonnet-5"].tokens.cacheRead == 7261803
    assert by_model["claude-haiku-4-5-20251001"].tokens.output == 903


def test_by_model_sums_the_same_model_across_entries() -> None:
    report = aggregate([_entry(), _entry()])

    by_model = {row.model: row.costUsd for row in report.byModel}
    assert by_model["claude-sonnet-5"] == round(
        math.fsum([5.02143785, 5.02143785]), COST_DECIMAL_PLACES
    )
    assert by_model["claude-haiku-4-5-20251001"] == round(
        math.fsum([0.0041205, 0.0041205]), COST_DECIMAL_PLACES
    )


def test_by_model_is_ordered_dearest_first() -> None:
    assert [row.model for row in aggregate([_entry()]).byModel] == [
        "claude-sonnet-5",
        "claude-opus-4-8[1m]",
        "claude-haiku-4-5-20251001",
    ]


def test_equal_cost_rows_are_broken_by_name_not_by_the_order_they_arrived() -> None:
    # The tie-break is the half of the ordering that insertion order would satisfy
    # by accident: with distinct costs, dropping the secondary key changes nothing.
    # Two ledgers holding the same records in a different order must serialise
    # identically, or a client diffing them sees churn that is not spend.
    costs = {"T-zulu": 1.0, "T-alpha": 1.0, "T-mike": 1.0}
    forward = aggregate([_entry(ids=[t], cost_usd=c) for t, c in costs.items()])
    backward = aggregate([_entry(ids=[t], cost_usd=c) for t, c in reversed(costs.items())])

    assert [row.ticketId for row in forward.byTicket] == ["T-alpha", "T-mike", "T-zulu"]
    assert [row.ticketId for row in backward.byTicket] == ["T-alpha", "T-mike", "T-zulu"]


def test_equal_cost_levels_are_also_broken_by_name() -> None:
    report = aggregate(
        [
            _entry(level="zulu", cost_usd=2.0),
            _entry(level="alpha", cost_usd=2.0),
        ]
    )

    assert [row.level for row in report.byLevel] == ["alpha", "zulu"]


def test_an_entry_without_a_by_model_breakdown_falls_back_to_its_model() -> None:
    # A rolled-up entry has no per-model split; its cost must still appear in the
    # by-model cut rather than dropping out of it.
    report = aggregate([_entry(by_model={}, model="sonnet", cost_usd=3.25)])

    (row,) = report.byModel
    assert row.model == "sonnet"
    assert row.costUsd == 3.25
    assert row.tokens.total == 7543318, "the entry's own written total, not a re-derived one"


def test_an_entry_naming_no_model_at_all_is_in_the_totals_only() -> None:
    report = aggregate([_entry(by_model={}, model=None, cost_usd=1.25)])

    assert report.byModel == [], "a record naming no model claims none"
    assert report.totals.costUsd == 1.25


def test_a_ticket_row_names_exactly_the_models_of_the_by_model_cut() -> None:
    # The two cuts decide "which models" from one source, so they cannot drift.
    # Asserted across all three shapes of entry: a breakdown, a bare scalar model,
    # and neither.
    for overrides, expected in (
        ({}, ["claude-haiku-4-5-20251001", "claude-opus-4-8[1m]", "claude-sonnet-5"]),
        ({"by_model": {}, "model": "sonnet"}, ["sonnet"]),
        ({"by_model": {}, "model": None}, []),
    ):
        report = aggregate([_entry(ids=["T1"], **overrides)])

        (row,) = report.byTicket
        assert row.models == expected
        assert row.models == sorted(r.model for r in report.byModel), (
            "a ticket names exactly the models that were billed to the by-model cut"
        )


# --------------------------------------------------------------------------- #
# byLevel: the factory owns the vocabulary
# --------------------------------------------------------------------------- #


def test_by_level_keys_on_whatever_level_the_factory_wrote() -> None:
    report = aggregate(
        [
            _entry(level="ticket", cost_usd=4.0),
            _entry(level="review", cost_usd=1.0),
            _entry(level="a-level-invented-tomorrow", cost_usd=2.0),
        ]
    )

    assert [(row.level, row.costUsd, row.entries) for row in report.byLevel] == [
        ("ticket", 4.0, 1),
        ("a-level-invented-tomorrow", 2.0, 1),
        ("review", 1.0, 1),
    ], "no closed set of levels; an unrecognised one appears rather than vanishing"


# --------------------------------------------------------------------------- #
# Totals: checked against an independently computed sum
# --------------------------------------------------------------------------- #


def test_totals_match_a_sum_computed_independently_of_the_aggregator() -> None:
    # The expected figure is computed here from the fixture costs directly — not
    # by calling aggregate() — so this checks the total against the ledger rather
    # than against itself.
    costs = [5.740558350000003, 1.40123456, 0.1, 0.2, 0.3, 2.7182818284, 3.14159]
    entries = [_entry(cost_usd=cost, ids=[f"T{i}"]) for i, cost in enumerate(costs)]

    report = aggregate(entries)

    assert report.totals.costUsd == round(math.fsum(costs), COST_DECIMAL_PLACES)
    assert report.totals.entries == len(costs)


def test_the_total_does_not_depend_on_the_order_entries_were_read_in() -> None:
    # What fsum buys over ``+=``: a total that is a function of the values alone.
    # 0.1 + 0.2 + 0.3 summed naively differs in the last places by order.
    costs = [0.1, 0.2, 0.3, 5.740558350000003, 0.0041205]
    forward = aggregate([_entry(cost_usd=cost) for cost in costs])
    backward = aggregate([_entry(cost_usd=cost) for cost in reversed(costs)])

    assert forward.totals.costUsd == backward.totals.costUsd


def test_many_small_entries_do_not_accumulate_rounding_error() -> None:
    # 1000 entries of a figure with eight decimal places: a per-entry round, or a
    # naive running sum, drifts from the true value here.
    cost = 0.00412051
    report = aggregate([_entry(cost_usd=cost) for _ in range(1000)])

    assert report.totals.costUsd == round(math.fsum([cost] * 1000), COST_DECIMAL_PLACES)
    assert report.totals.costUsd == 4.12051


def test_totals_sum_the_entries_token_counts() -> None:
    report = aggregate([_entry(), _entry()])

    assert report.totals.tokens.input == 8546 * 2
    assert report.totals.tokens.total == 7543318 * 2, "the factory's written total, summed"


# --------------------------------------------------------------------------- #
# Empty is valid, not an error — and carries no absence claim
# --------------------------------------------------------------------------- #


def test_no_entries_yields_zeroed_totals_and_empty_cuts() -> None:
    report = aggregate([])

    assert report.totals.costUsd == 0.0
    assert report.totals.entries == 0
    assert report.totals.tokens.total == 0
    assert report.byTicket == []
    assert report.byModel == []
    assert report.byLevel == []
    assert report.attribution == "full-to-each-id"


# --------------------------------------------------------------------------- #
# The session id never reaches a spend shape
# --------------------------------------------------------------------------- #


def test_no_session_id_reaches_the_report_in_any_form() -> None:
    dumped = aggregate([_entry()]).model_dump_json()

    assert "session_id" not in dumped
    assert "81dda660" not in dumped


# --------------------------------------------------------------------------- #
# Bounded output: the response is sized by tickets and models, not by entries
# --------------------------------------------------------------------------- #


def test_output_size_is_bounded_by_ticket_and_model_counts_not_ledger_length() -> None:
    report = aggregate([_entry(ids=["T71"]) for _ in range(500)])

    assert report.totals.entries == 500
    assert len(report.byTicket) == 1
    assert len(report.byModel) == 3
    assert len(report.byLevel) == 1
