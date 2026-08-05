import sys

import pytest

for p in ("/Users/aditi/Downloads/packages/aides", "/Users/aditi/Downloads/packages/wraps"):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture(autouse=True)
def _isolate_caches():
    """Module level caches outlive a single test, so without this one test's
    cached result silently answers the next test's call — which briefly made
    four captcha tests pass without ever running the code under test."""
    import billing
    import casestatus
    import mhcstatus
    import research
    cached = (billing, casestatus, mhcstatus, research)
    for mod in cached:
        mod.clear_cache()
    yield
    for mod in cached:
        mod.clear_cache()
