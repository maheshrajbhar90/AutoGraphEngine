#!/usr/bin/env python3
"""
Update an existing eight-card market JSON file from a pasted text report
using a locally running Ollama model.

Default files:
  market_input.txt  - paste the latest dashboard/report text here
  daily_data.json   - existing JSON file whose schema must be preserved

Run:
  python update_market_json.py
  python update_market_json.py --dry-run
  python update_market_json.py --input market_input.txt --output daily_data.json

Requirements:
  - Ollama running locally (default: http://localhost:11434)
  - Model available locally (default: gpt-oss:20b)
No third-party Python packages are required.
"""

from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b")
DEFAULT_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
DEFAULT_INPUT = "market_inteligent/market_input.txt"
DEFAULT_OUTPUT = "market_inteligent/daily_data.json"


class UpdateError(Exception):
    """Expected, user-actionable update failure."""


def read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise UpdateError(f"JSON file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise UpdateError(
            f"Existing JSON is invalid at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc


def infer_schema(value: Any) -> dict[str, Any]:
    """Infer a strict JSON schema from the current file's structure."""
    if isinstance(value, dict):
        properties = {key: infer_schema(item) for key, item in value.items()}
        return {
            "type": "object",
            "properties": properties,
            "required": list(value.keys()),
            "additionalProperties": False,
        }

    if isinstance(value, list):
        if not value:
            item_schema: dict[str, Any] = {}
        else:
            # Merge item schemas so heterogeneous arrays remain representable.
            schemas = [infer_schema(item) for item in value]
            item_schema = merge_schemas(schemas)
        return {"type": "array", "items": item_schema}

    if value is None:
        return {}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    return {}


def merge_schemas(schemas: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine inferred schemas for array entries without losing known keys."""
    if not schemas:
        return {}
    if all(s == schemas[0] for s in schemas):
        return schemas[0]

    types = {s.get("type") for s in schemas}
    if len(types) == 1 and "object" in types:
        all_keys: list[str] = []
        for schema in schemas:
            for key in schema.get("properties", {}):
                if key not in all_keys:
                    all_keys.append(key)
        properties = {}
        required = []
        for key in all_keys:
            child_schemas = [
                s["properties"][key]
                for s in schemas
                if key in s.get("properties", {})
            ]
            properties[key] = merge_schemas(child_schemas)
            if all(key in s.get("required", []) for s in schemas):
                required.append(key)
        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

    if len(types) == 1 and "array" in types:
        return {
            "type": "array",
            "items": merge_schemas([s.get("items", {}) for s in schemas]),
        }

    # Different primitive types (for example null and number) are allowed.
    nonempty = [s for s in schemas if s]
    if not nonempty:
        return {}
    return {"anyOf": nonempty}


def validate_against_schema(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate generated data against the schema inferred from the existing JSON."""
    if "anyOf" in schema:
        alternatives = schema["anyOf"]
        if any(not validate_against_schema(value, option, path) for option in alternatives):
            return []
        return [f"{path}: value does not match any permitted existing type"]

    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            return [f"{path}: expected object, got {type(value).__name__}"]
        errors: list[str] = []
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key}: required key is missing")
        for key in value:
            if key not in properties:
                errors.append(f"{path}.{key}: unexpected key (schema change)")
            else:
                errors.extend(
                    validate_against_schema(value[key], properties[key], f"{path}.{key}")
                )
        return errors

    if expected == "array":
        if not isinstance(value, list):
            return [f"{path}: expected array, got {type(value).__name__}"]
        errors: list[str] = []
        item_schema = schema.get("items", {})
        for index, item in enumerate(value):
            errors.extend(validate_against_schema(item, item_schema, f"{path}[{index}]"))
        return errors

    if expected == "string" and not isinstance(value, str):
        return [f"{path}: expected string, got {type(value).__name__}"]
    if expected == "boolean" and not isinstance(value, bool):
        return [f"{path}: expected boolean, got {type(value).__name__}"]
    if expected == "integer" and (
        not isinstance(value, int) or isinstance(value, bool)
    ):
        return [f"{path}: expected integer, got {type(value).__name__}"]
    if expected == "number" and (
        not isinstance(value, (int, float)) or isinstance(value, bool)
    ):
        return [f"{path}: expected number, got {type(value).__name__}"]
    return []


def extract_json(text: str) -> Any:
    """Parse JSON, tolerating accidental Markdown fences around the response."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise UpdateError("Ollama did not return valid JSON. No file was changed.")


def call_ollama(
    report: str,
    current_data: Any,
    schema: dict[str, Any],
    model: str,
    host: str,
    timeout: int,
) -> Any:
    system_prompt = """
You are a careful financial-market report data extraction and JSON update engine.
Return one complete JSON object and nothing else.

Rules:
1. The CURRENT JSON is the authoritative schema/template. Preserve every existing key,
   nesting level, value type, and card1-card8 structure. Do not add, rename, or remove keys.
2. Update values only when the pasted report explicitly supports them.
3. If a field is absent from the report, retain its CURRENT JSON value. Do not guess,
   calculate missing values, or fill gaps from general market knowledge.
4. The report may contain an embedded JSON block. Treat it as source material only if
   it is clearly part of the latest report. If it conflicts with explicit dashboard
   figures or narrative elsewhere in the pasted report, do not silently choose:
   retain the current JSON value for the disputed field.
5. Keep null as null when the source has no value. Do not convert null to zero.
6. Preserve sector names and stock symbols exactly as supplied. Arrays may grow or shrink
   when the report clearly provides a different number of sectors/stocks, but each item
   must follow the existing array item structure.
7. Numeric fields must remain numeric; percentage strings and currency strings should
   follow the existing field's type and convention.
8. Do not invent sector performance, FII/DII flows, stock entries, dates, PCR, VIX,
   macro values, or recommendations.
9. Use the report's stated report date for the top-level date only when it is explicit.
10. Return a complete updated copy of CURRENT JSON, not a patch or explanation.
""".strip()

    user_prompt = (
        "CURRENT JSON (preserve its schema):\n"
        + json.dumps(current_data, ensure_ascii=False, indent=2)
        + "\n\nLATEST PASTED MARKET REPORT:\n"
        + report
        + "\n\nUpdate the current JSON using only supported report data."
    )

    payload = {
        "model": model,
        "stream": False,
        "format": schema,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "options": {"temperature": 0.0},
    }

    request = urllib.request.Request(
        f"{host}/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise UpdateError(
            f"Cannot connect to Ollama at {host}. Start Ollama and verify the model. "
            f"Details: {reason}"
        ) from exc
    except TimeoutError as exc:
        raise UpdateError(f"Ollama timed out after {timeout} seconds.") from exc
    except json.JSONDecodeError as exc:
        raise UpdateError("Ollama returned an unreadable API response.") from exc

    content = result.get("message", {}).get("content")
    if not content:
        raise UpdateError(f"Ollama returned no message content: {result}")
    return extract_json(content)


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Update daily market JSON from market_input.txt using local Ollama."
    )
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Pasted report text file")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Existing JSON to update")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Local Ollama model")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Ollama host URL")
    parser.add_argument("--timeout", type=int, default=600, help="Ollama timeout in seconds")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate and preview changes without modifying the JSON file",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.is_file():
        raise UpdateError(
            f"Input file not found: {input_path}\n"
            f"Create it and paste the latest market report into it."
        )

    report = input_path.read_text(encoding="utf-8").strip()
    if not report:
        raise UpdateError(f"Input file is empty: {input_path}")

    current = read_json(output_path)
    if not isinstance(current, dict):
        raise UpdateError("The existing daily_data.json must contain a JSON object.")

    # Enforce the expected eight-card top-level structure.
    missing_cards = [f"card{i}" for i in range(1, 9) if f"card{i}" not in current]
    if missing_cards:
        raise UpdateError(
            "Existing JSON is missing expected card keys: " + ", ".join(missing_cards)
        )

    schema = infer_schema(current)
    print(f"Input report : {input_path}")
    print(f"JSON target  : {output_path}")
    print(f"Ollama model : {args.model}")
    print("Extracting and mapping report data locally...")

    updated = call_ollama(
        report=report,
        current_data=current,
        schema=schema,
        model=args.model,
        host=args.host,
        timeout=args.timeout,
    )

    errors = validate_against_schema(updated, schema)
    if errors:
        print("\nVALIDATION FAILED — original JSON was not changed:", file=sys.stderr)
        for error in errors[:50]:
            print(f"  - {error}", file=sys.stderr)
        if len(errors) > 50:
            print(f"  ... and {len(errors) - 50} more errors", file=sys.stderr)
        return 2

    # Ensure the result can be serialized and parsed again before touching the file.
    try:
        serialized = json.dumps(updated, ensure_ascii=False, indent=2)
        json.loads(serialized)
    except (TypeError, ValueError) as exc:
        raise UpdateError(f"Generated JSON failed serialization validation: {exc}") from exc

    old_text = json.dumps(current, ensure_ascii=False, indent=2).splitlines()
    new_text = serialized.splitlines()
    diff = list(difflib.unified_diff(
        old_text, new_text,
        fromfile=str(output_path) + " (before)",
        tofile=str(output_path) + " (proposed)",
        lineterm="",
    ))

    if not diff:
        print("\nNo changes detected. Existing JSON remains unchanged.")
        return 0

    print("\nProposed changes:")
    print("\n".join(diff[:160]))
    if len(diff) > 160:
        print(f"... diff truncated ({len(diff) - 160} additional lines)")

    if args.dry_run:
        print("\nDRY RUN: validation passed; original JSON was not modified.")
        return 0

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = output_path.with_name(
        f"{output_path.stem}.backup_{timestamp}{output_path.suffix}"
    )
    shutil.copy2(output_path, backup_path)
    atomic_write_json(output_path, updated)

    # Read back the committed file to verify it is valid JSON and still schema-compatible.
    written = read_json(output_path)
    final_errors = validate_against_schema(written, schema)
    if final_errors:
        shutil.copy2(backup_path, output_path)
        raise UpdateError(
            "Post-write validation failed. Original file was restored from backup."
        )

    print("\nSUCCESS: daily JSON updated.")
    print(f"Backup saved: {backup_path}")
    print(f"Updated file: {output_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UpdateError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
