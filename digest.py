import requests

COURT_NAMES = {"sc": "Supreme Court", "dhc": "Delhi HC", "mhc": "Meghalaya HC"}
MAX_MATTERS = 25


def _listings(date):
    import cases
    return cases.listings_for(date)


def _post(url, text):
    requests.post(url, json={"Text": text}, timeout=30).raise_for_status()


def run(date=None, send_url="http://127.0.0.1:8601/send") -> int:
    if date is None:
        import aides
        date = str(aides.now(tz="Asia/Kolkata").date())
    rows = _listings(date)
    if not rows:
        return 0
    lines = [f"Good morning. {len(rows)} firm matter(s) listed today ({date}):", ""]
    display_rows = rows[:MAX_MATTERS]
    for r in display_rows:
        lines.append(f"• {r['case_no']} — {r['client']} — {COURT_NAMES.get(r['court'], r['court'])}")
        lines.append(r["matches"][0])
        lines.append("")
    if len(rows) > MAX_MATTERS:
        extra = len(rows) - MAX_MATTERS
        lines.append(f"…and {extra} more matter(s) listed — ask me for the full list.")
    _post(send_url, "\n".join(lines).strip())
    return 1


if __name__ == "__main__":
    print(f"digest messages posted: {run()}")
