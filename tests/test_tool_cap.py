"""Tool results are fed back into the model, so their size is a running cost.

server.py already bounds what gets *persisted*. This bounds what the model is
handed in the first place: firm_register can return 100 spreadsheet rows in a
single result, and that lands in the prompt whether or not it is ever stored.
"""

import skills


def test_a_normal_sized_result_is_left_alone():
    text = "2 matters listed on 2026-08-04"

    assert skills._cap(text) == text


def test_an_oversized_result_is_truncated():
    huge = "row of register data\n" * 5000

    capped = skills._cap(huge)

    assert len(capped) <= skills.MAX_TOOL_RESULT_CHARS + 200


def test_the_truncation_tells_the_model_how_to_get_the_rest():
    capped = skills._cap("x" * 20000)

    assert "truncated" in capped
    assert "narrow" in capped.lower()


def test_truncation_keeps_the_source_tag():
    """Citing is the point. A long answer must not lose its source to the cap."""
    body = "register row\n" * 5000
    text = f"{body}\n[source: firm file register, 412 rows]"

    capped = skills._cap(text)

    assert capped.rstrip().endswith("[source: firm file register, 412 rows]")
    assert len(capped) <= skills.MAX_TOOL_RESULT_CHARS + 200


def test_a_short_result_with_a_source_tag_is_untouched():
    text = "2 matters listed\n[source: Delhi HC cause list, 2026-08-04]"

    assert skills._cap(text) == text


def test_registered_skills_apply_the_cap():
    @skills.skill
    def _oversized_probe():
        """A test skill that returns far too much."""
        return "y" * 30000

    assert len(_oversized_probe()) <= skills.MAX_TOOL_RESULT_CHARS + 200


def test_capping_does_not_break_a_tool_schema():
    """The cap wraps every skill, so tool parameters must still be introspectable
    or the model loses the ability to call them correctly."""
    by_name = {s.name: s for s in skills.SKILLS}

    schema = by_name["mhc_case_status_lookup"].params_json_schema

    assert {"case_type", "number", "year"} <= set(schema.get("properties", {}))
