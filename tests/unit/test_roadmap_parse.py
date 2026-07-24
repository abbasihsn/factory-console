"""Unit tests for the pure roadmap parser :func:`parse_milestones`.

These pin the parsing rules on INLINE strings (headings, checkbox state, id
extraction, prose/pre-heading tolerance, and total-never-raises behavior) and on
BOTH checked-in fixture ``ROADMAP.md`` documents so the structured breakdown the
``/roadmap`` view renders cannot silently drift from the authored roadmaps.
Deterministic and I/O-free apart from reading the two fixtures.
"""

from pathlib import Path

import pytest

from factory_console.domain.deps import RoadmapItem, RoadmapMilestone
from factory_console.file_adapter.roadmap_parse import parse_milestones

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "projects"
WITH_RUN_STATE_ROADMAP = FIXTURES / "with_run_state" / "ROADMAP.md"
MINIMAL_ROADMAP = FIXTURES / "minimal" / "ROADMAP.md"


def _only(milestones: list[RoadmapMilestone]) -> RoadmapMilestone:
    assert len(milestones) == 1
    return milestones[0]


# --------------------------------------------------------------------------- #
# Headings open milestones — h2 only
# --------------------------------------------------------------------------- #


def test_h2_heading_opens_a_milestone_named_after_the_heading_text() -> None:
    milestones = parse_milestones("## MVP\n- a thing")
    milestone = _only(milestones)
    assert milestone.name == "MVP"
    assert [item.text for item in milestone.items] == ["a thing"]


def test_multiple_h2_headings_become_ordered_milestones() -> None:
    milestones = parse_milestones("## First\n- x\n## Second\n- y")
    assert [m.name for m in milestones] == ["First", "Second"]
    assert [i.text for m in milestones for i in m.items] == ["x", "y"]


def test_h1_title_and_h3_subheading_do_not_open_a_milestone() -> None:
    body = "# Title\n- pre item\n### Subsection\n- also pre"
    # No ## heading anywhere -> no milestones, and the list items are dropped.
    assert parse_milestones(body) == []


def test_h3_under_a_milestone_does_not_start_a_new_one() -> None:
    body = "## Real\n- kept\n### Not a milestone\n- still under Real"
    milestone = _only(parse_milestones(body))
    assert milestone.name == "Real"
    assert [item.text for item in milestone.items] == ["kept", "still under Real"]


# --------------------------------------------------------------------------- #
# Checkbox state -> done
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "line, expected_done",
    [
        ("- [x] done lower", True),
        ("- [X] done upper", True),
        ("- [ ] not done", False),
        ("- no checkbox", None),
        ("* [x] star bullet", True),
    ],
)
def test_checkbox_marker_maps_to_done(line: str, expected_done: bool | None) -> None:
    milestone = _only(parse_milestones(f"## M\n{line}"))
    assert milestone.items[0].done is expected_done


def test_checkbox_token_is_stripped_from_text() -> None:
    milestone = _only(parse_milestones("## M\n- [x] Build the thing"))
    assert milestone.items[0].text == "Build the thing"


# --------------------------------------------------------------------------- #
# ticketId extraction
# --------------------------------------------------------------------------- #


def test_parenthesized_id_is_extracted() -> None:
    milestone = _only(parse_milestones("## M\n- [ ] Endpoints (CAD-131)"))
    assert milestone.items[0].ticketId == "CAD-131"


def test_no_space_bold_id_is_extracted() -> None:
    milestone = _only(parse_milestones("## M\n- **T01** ship it"))
    assert milestone.items[0].ticketId == "T01"


def test_spaced_bold_label_is_not_an_id() -> None:
    milestone = _only(parse_milestones("## M\n- **Weekly digest** — a recap"))
    assert milestone.items[0].ticketId is None


def test_single_word_bold_label_without_a_digit_is_not_an_id() -> None:
    # A bold word that matches the id character-class but carries no digit
    # (e.g. **Reminders**) is a prose label, not a ticket id.
    milestone = _only(parse_milestones("## M\n- **Reminders** — nudge before a break"))
    assert milestone.items[0].ticketId is None


def test_spaced_bold_label_with_same_line_parenthesized_id_still_extracts_the_id() -> None:
    milestone = _only(parse_milestones("## M\n- **Weekly digest** — recap (CAD-131)."))
    assert milestone.items[0].ticketId == "CAD-131"


def test_first_id_shaped_token_left_to_right_wins() -> None:
    # A bold id precedes a parenthesized id -> the earlier bold one is chosen.
    milestone = _only(parse_milestones("## M\n- **T01** relates to (CAD-999)"))
    assert milestone.items[0].ticketId == "T01"


def test_text_keeps_the_id_and_bold_markers() -> None:
    milestone = _only(parse_milestones("## M\n- **Weekly digest** — recap (CAD-131)."))
    assert milestone.items[0].text == "**Weekly digest** — recap (CAD-131)."


# --------------------------------------------------------------------------- #
# Tolerance: prose, pre-heading items, empty/garbage
# --------------------------------------------------------------------------- #


def test_prose_lines_are_ignored() -> None:
    body = "## M\nsome intro prose\n- real item\nmore prose"
    milestone = _only(parse_milestones(body))
    assert [item.text for item in milestone.items] == ["real item"]


def test_list_items_before_any_heading_are_ignored() -> None:
    body = "- orphan a\n* orphan b\n## M\n- kept"
    milestone = _only(parse_milestones(body))
    assert [item.text for item in milestone.items] == ["kept"]


def test_empty_string_yields_no_milestones() -> None:
    assert parse_milestones("") == []


def test_heading_less_body_yields_no_milestones() -> None:
    assert parse_milestones("just prose\n- a bullet\nmore prose") == []


@pytest.mark.parametrize(
    "garbage",
    [
        "",
        "   \n\t\n   ",
        "## \n- \n* \n[x]\n**",
        "###### deep\n- [z] weird checkbox (())",
        "## M\n- ** unbalanced bold (CAD-",
        "\x00\x01 binary-ish \ud83d",
    ],
)
def test_never_raises_on_garbage(garbage: str) -> None:
    # Total function: any input returns a list, never an exception.
    assert isinstance(parse_milestones(garbage), list)


def test_weird_checkbox_letter_is_treated_as_no_checkbox() -> None:
    # Only [x]/[X]/[ ] are checkboxes; [z] is not, so done stays None and the
    # bracket token remains part of the text.
    milestone = _only(parse_milestones("## M\n- [z] odd"))
    assert milestone.items[0].done is None
    assert milestone.items[0].text == "[z] odd"


# --------------------------------------------------------------------------- #
# Real fixtures
# --------------------------------------------------------------------------- #


def test_with_run_state_fixture_parses_expected_milestones() -> None:
    body = WITH_RUN_STATE_ROADMAP.read_text(encoding="utf-8")
    milestones = parse_milestones(body)
    assert [m.name for m in milestones] == [
        "MVP — check in and see your streak",
        "v1 — momentum (epics)",
        "v2 — together (epics)",
        "Run-state note",
    ]

    mvp = milestones[0]
    assert len(mvp.items) == 4
    assert mvp.items[0] == RoadmapItem(
        text="Habit schema and append-only event store (CAD-100)",
        ticketId="CAD-100",
        done=True,
    )
    assert mvp.items[2].done is False and mvp.items[2].ticketId == "CAD-125"
    # The unchecked, id-less final item.
    assert mvp.items[3] == RoadmapItem(text="Minimal board UI", ticketId=None, done=False)

    v1 = milestones[1]
    by_prefix = {item.text.split(" ")[0]: item for item in v1.items}
    assert by_prefix["**Weekly"].ticketId == "CAD-131"  # spaced bold + parenthesized id
    assert by_prefix["**Weekly"].done is None  # no checkbox on epic lines
    assert by_prefix["**Reminders**"].ticketId is None  # digit-less bold label

    # A prose-only trailing milestone still opens (name), with zero items.
    assert milestones[3].items == []


def test_minimal_fixture_parses_expected_milestones() -> None:
    body = MINIMAL_ROADMAP.read_text(encoding="utf-8")
    milestones = parse_milestones(body)
    assert [m.name for m in milestones] == [
        "MVP — make ranger reports usable",
        "v1 — put conditions in front of hikers (epics)",
        "v2 — proactive and personal (epics)",
        "Principles",
    ]

    mvp = milestones[0]
    assert len(mvp.items) == 4
    assert mvp.items[0] == RoadmapItem(
        text="Canonical trail-report schema and store",
        ticketId=None,
        done=True,
    )
    assert mvp.items[1].ticketId == "TM-001" and mvp.items[1].done is False

    # The numbered "Principles" list uses 1./2./3. bullets, which are NOT
    # -/* list items, so that milestone carries no parsed items.
    assert milestones[3].items == []
