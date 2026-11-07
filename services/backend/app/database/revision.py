"""Schema revision the application code expects.

The API image does not ship ``migrations/`` inside the installed ``app`` package, so the
expected Alembic head is a code constant. ``tests/test_health.py`` asserts it equals the
single head in ``migrations/versions/``; a new migration must update it.
"""

EXPECTED_ALEMBIC_HEAD = "0014_alembic_version_read"
