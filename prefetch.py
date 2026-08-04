"""Warm the cause-list cache before anyone asks.

Answering a question must not download a cause list. The Supreme Court
publishes many multi-megabyte PDFs; fetching and extracting them measured over
280 seconds on this box, while the gateway gives the agent 180. So this runs on
a timer well before the 07:00 digest, and requests only ever read the cache.
"""

import causelists

COURTS = ("sc", "dhc", "mhc")


def _fetch(court, date):
    causelists.fetch(court, date, network=True)


def _dates():
    import aides
    today = aides.now(tz="Asia/Kolkata").date()
    return [str(today), str(today + __import__("datetime").timedelta(days=1))]


def run(dates=None, courts=COURTS) -> int:
    """Fetch each court's list for each date. One failure never stops the rest:
    a court that has not published yet is normal, not an error."""
    warmed = 0
    for date in (dates if dates is not None else _dates()):
        for court in courts:
            try:
                _fetch(court, date)
                warmed += 1
                print(f"[prefetch] warmed {court} {date}")
            except Exception as e:
                print(f"[prefetch] {court} {date}: {type(e).__name__}: {str(e)[:120]}")
    return warmed


if __name__ == "__main__":
    print(f"[prefetch] lists warmed: {run()}")
