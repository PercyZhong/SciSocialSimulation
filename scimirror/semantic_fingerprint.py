"""Cross-platform raw and semantic fingerprints for frozen retrieval inputs."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

SCHEMA_VERSION = "semantic_fingerprint_1"


def raw_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _pairs_no_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_constant(value):
    raise ValueError(f"Non-finite JSON number: {value}")


def strict_json_text(text):
    return json.loads(text.lstrip("\ufeff"), object_pairs_hook=_pairs_no_duplicates,
                      parse_constant=_reject_constant)


def normalize_text_newlines(value):
    return value.replace("\r\n", "\n").replace("\r", "\n") if isinstance(value, str) else value


def normalize_value(value):
    if isinstance(value, dict):
        return {str(key): normalize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite float")
    return normalize_text_newlines(value)


def canonical_bytes(value):
    return json.dumps(normalize_value(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def semantic_sha256(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def json_fingerprint(path):
    path = Path(path)
    return {"raw_sha256": raw_sha256(path), "semantic_sha256": semantic_sha256(
        strict_json_text(path.read_text(encoding="utf-8-sig")))}


def source_fingerprint(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    normalized = normalize_text_newlines(text).encode("utf-8")
    return {"raw_sha256": raw_sha256(path),
            "source_sha256": hashlib.sha256(normalized).hexdigest()}


INTEGER_FIELDS = {
    "year", "rank", "grade", "score", "revision", "final_grade",
    "reviewer_a_score", "reviewer_b_score", "candidate_rank", "final_rank",
}
FLOAT_FIELDS = {
    "lambda", "bm25_k1", "bm25_b", "score", "bm25_raw", "topic_score_raw",
    "topic_score_normalized", "rerank_score", "gate_threshold",
}
BOOLEAN_FIELDS = {"canonical", "synthetic", "unsure", "unresolved", "human_validated"}


def _typed_cell(key, value):
    value = normalize_text_newlines(value)
    if value == "":
        return None
    if key in INTEGER_FIELDS:
        if isinstance(value, bool):
            raise ValueError(f"Boolean is not integer for {key}")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid integer for {key}: {value!r}") from error
        if str(value).strip() not in {str(parsed), f"+{parsed}"}:
            raise ValueError(f"Non-canonical integer for {key}: {value!r}")
        return parsed
    if key in FLOAT_FIELDS:
        try:
            parsed = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid float for {key}: {value!r}") from error
        if not math.isfinite(parsed):
            raise ValueError(f"Non-finite float for {key}")
        return parsed
    if key in BOOLEAN_FIELDS:
        if isinstance(value, bool):
            return value
        lowered = str(value).lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
        raise ValueError(f"Invalid boolean for {key}: {value!r}")
    return value


def read_csv_typed(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("Missing or duplicate CSV columns")
        return [{key: _typed_cell(key, row.get(key, "")) for key in reader.fieldnames}
                for row in reader]


def read_jsonl_strict(path):
    rows = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        if line.strip():
            try:
                rows.append(strict_json_text(line))
            except ValueError as error:
                raise ValueError(f"Invalid JSONL line {number}: {error}") from error
    return rows


SORT_KEYS = {"corpus": "paper_id", "queries": "query_id", "family_map": "paper_id"}


def records_fingerprint(path, kind, primary_key=None, order_sensitive=False):
    path = Path(path)
    rows = read_csv_typed(path) if path.suffix.lower() == ".csv" else read_jsonl_strict(path)
    key = primary_key or SORT_KEYS.get(kind)
    if key:
        values = [str(row.get(key, "")) for row in rows]
        if any(not value for value in values) or len(values) != len(set(values)):
            raise ValueError(f"Missing or duplicate primary key: {key}")
        if not order_sensitive:
            rows = sorted(rows, key=lambda row: str(row[key]))
    return {"raw_sha256": raw_sha256(path), "semantic_sha256": semantic_sha256(rows),
            "record_count": len(rows), "kind": kind, "schema_version": SCHEMA_VERSION}


def combine_fingerprint(parts):
    return semantic_sha256({"schema_version": SCHEMA_VERSION, "parts": parts})
