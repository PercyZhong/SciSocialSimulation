"""Deterministic utilities: random streams are keyed by semantic identity."""
import hashlib
import json
import os
import random
import time
from pathlib import Path


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(obj):
    return hashlib.sha256(canonical(obj).encode('utf-8')).hexdigest()


def rng(seed, *keys):
    return random.Random(int(digest([seed, *keys])[:16], 16))


def dump(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    # Windows scanners/indexers can briefly hold the destination open. Keep the
    # atomic replace contract, but tolerate a short transient lock; Linux takes
    # the first attempt in normal operation. Persistent failures still surface.
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.02 * (2 ** attempt))


def clip(x):
    return max(0.0, min(1.0, x))
