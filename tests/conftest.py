"""Shared test setup.

Every test request comes from the same address, so the real per-address limits
(10 uploads an hour) would stop the suite after a few tests. They are raised here,
before the app is imported. The limits themselves are tested in test_security.py
with their real values.
"""

import os

for name in (
    "SL_RATE_UPLOADS_PER_HOUR",
    "SL_RATE_ANALYSES_PER_HOUR",
    "SL_RATE_POLLS_PER_MINUTE",
    "SL_RATE_REQUESTS_PER_MINUTE",
):
    os.environ.setdefault(name, "1000000")
