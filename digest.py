import requests

COURT_NAMES = {"sc": "Supreme Court", "dhc": "Delhi HC", "mhc": "Meghalaya HC"}


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
    for r in rows:
        lines.append(f"• {r['case_no']} — {r['client']} — {COURT_NAMES.get(r['court'], r['court'])}")
        lines.append(r["matches"][0])
        lines.append("")
    _post(send_url, "\n".join(lines).strip())
    return 1


if __name__ == "__main__":
    print(f"digest messages posted: {run()}")
