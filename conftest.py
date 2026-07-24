import sys

for p in ("/Users/aditi/Downloads/packages/aides", "/Users/aditi/Downloads/packages/wraps"):
    if p not in sys.path:
        sys.path.insert(0, p)
