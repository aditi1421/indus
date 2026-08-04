"""Matched matters must reach the model whole, not as a truncated sample.

Found in production 2026-08-04: "what matters are listed today" answered with
3 matters when the advocate appeared in about a dozen. Each match was fed to
the model as its full cause-list extract -- an In re matter carries dozens of
counsel lines -- so the 4,000-character tool budget was spent after a few
matters and the rest never reached the model.
"""

import cases
import causelists
import skills


def _item(case_no, counsel_lines=30):
    filler = "\n".join(f"SOME COUNSEL NAME {i} [R-{i}]," for i in range(counsel_lines))
    return f"12 {case_no} IN RE: A MATTER WITH MANY PARTIES\nVersus\n{filler}"


def test_summarize_item_is_one_bounded_line_keeping_the_case_number():
    line = causelists.summarize_item(_item("SLP(C) No. 4871/2022"))

    assert "\n" not in line
    assert "SLP(C) No. 4871/2022" in line
    assert len(line) <= 180


def test_a_short_item_summarizes_to_itself_on_one_line():
    assert causelists.summarize_item("5 MA 825/2023\nGOHAR vs UPSRTC") == (
        "5 MA 825/2023 GOHAR vs UPSRTC")


def test_every_matched_matter_reaches_the_model(monkeypatch):
    case_nos = [f"C.A. No. {1000 + i}/2020" for i in range(12)]
    monkeypatch.setattr(cases, "listings_for", lambda date: {
        "rows": [{"court": "sc", "token": "AVIJIT MANI", "case_no": "", "aor": "",
                  "parties": "matters listing AVIJIT MANI",
                  "matches": [_item(n) for n in case_nos]}],
        "checked": ["sc"], "unavailable": []})

    out = skills.todays_causelist_matches("2026-08-04")

    for case_no in case_nos:
        assert case_no in out
    assert "[truncated" not in out


def test_search_causelist_with_many_hits_lists_them_all(monkeypatch):
    case_nos = [f"C.A. No. {1000 + i}/2020" for i in range(12)]
    monkeypatch.setattr(causelists, "search",
                        lambda court, date, query, **kw: [_item(n) for n in case_nos])

    out = skills.search_causelist("sc", "2026-08-04", "AVIJIT MANI")

    for case_no in case_nos:
        assert case_no in out
    assert "[truncated" not in out


def test_summary_centers_on_the_matched_text_not_the_block_head():
    """The Supreme Court's extracted text smears connected matters into one
    block, so the head of a block can be a different case than the one that
    matched. The window must land on the match."""
    block = ("116. Connected SATYA KHURANA C.A. No. 7926/2014 " + "FILLER " * 60 +
             "STATE OF MEGHALAYA AVIJIT MANI TRIPATHI SLP(C) No. 26508/2023 Versus "
             "BRIGHTSTARWELL MARBANIANG " + "MORE " * 40)

    line = causelists.summarize_item(block, around="AVIJIT MANI")

    assert "AVIJIT MANI" in line
    assert len(line) <= 170


def test_the_headline_counts_listings_not_search_tokens(monkeypatch):
    """"3 firm matters" on a 12-listing day was the number the model repeated.
    The header must count what is listed, not how many searches matched."""
    case_nos = [f"C.A. No. {1000 + i}/2020" for i in range(12)]
    monkeypatch.setattr(cases, "listings_for", lambda date: {
        "rows": [{"court": "sc", "token": "AVIJIT MANI", "case_no": "", "aor": "",
                  "parties": "matters listing AVIJIT MANI",
                  "matches": [_item(n) for n in case_nos]}],
        "checked": ["sc"], "unavailable": []})

    out = skills.todays_causelist_matches("2026-08-04")

    assert out.startswith("12 listing(s)")


def test_a_block_shared_by_two_searches_is_shown_once(monkeypatch):
    shared = _item("C.A. No. 7926/2014")
    monkeypatch.setattr(cases, "listings_for", lambda date: {
        "rows": [
            {"court": "sc", "token": "W.P.(C) No. 299/2025", "case_no": "W.P.(C) No. 299/2025",
             "aor": "2648", "parties": "Paul vs UOI", "matches": [shared]},
            {"court": "sc", "token": "AVIJIT MANI", "case_no": "", "aor": "",
             "parties": "matters listing AVIJIT MANI", "matches": [shared]},
        ],
        "checked": ["sc"], "unavailable": []})

    out = skills.todays_causelist_matches("2026-08-04")

    assert out.count("7926/2014") == 1
    assert out.startswith("1 listing(s)")


def test_search_causelist_with_one_hit_keeps_the_full_item(monkeypatch):
    """A specific lookup ("when is 132/2016 listed?") needs the item's detail
    lines -- the court number and the time live there, not in the summary."""
    item = "3 W.P.(C) No. 132/2016 RAJNEESH KUMAR PANDEY\nCOURT NO. : 4\n@ 3 PM."
    monkeypatch.setattr(causelists, "search", lambda court, date, query, **kw: [item])

    out = skills.search_causelist("sc", "2026-08-04", "132/2016")

    assert "COURT NO. : 4" in out
    assert "@ 3 PM." in out
