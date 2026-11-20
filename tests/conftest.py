"""Tests run with sign-in switched off unless a test switches it on (see test_auth.py)."""
import os

os.environ.setdefault("AUTH_ENABLED", "false")
