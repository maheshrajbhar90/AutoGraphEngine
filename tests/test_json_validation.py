"""Placeholder tests for JSON validation.

The real validation logic lives in :mod:`validation.json_validator`.
These tests simply import the module to ensure it is syntactically
valid.
"""

def test_json_validator_import():
    import importlib
    mod = importlib.import_module("validation.json_validator")
    assert hasattr(mod, "validate_json")