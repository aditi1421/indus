import sys

import pytest

for p in ("/Users/aditi/Downloads/packages/aides", "/Users/aditi/Downloads/packages/wraps"):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture(autouse=True)
def _isolate_sc_cache():
    """The Supreme Court lookup cache lives for a day and is module level, so
    without this one test's cached result silently answers the next one's call."""
    import casestatus
    casestatus.clear_cache()
    yield
    casestatus.clear_cache()
