"""Stage C model registry with secret-safe offline validation."""
import json
import os
from pathlib import Path


REQUIRED = {"model_key", "provider", "endpoint_type", "base_url_env", "api_key_env",
            "requested_model_id", "returned_model_id", "capabilities", "temperature",
            "max_output_tokens", "provider_options", "pricing"}


def load_registry(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    models = data.get("models", [])
    if len(models) != 2 or len({m.get("model_key") for m in models}) != 2:
        raise ValueError("Stage C requires exactly two unique models")
    for model in models:
        missing = REQUIRED - set(model)
        if missing:
            raise ValueError(f"Model {model.get('model_key')} missing {sorted(missing)}")
        if model["endpoint_type"] != "openai_compatible_chat":
            raise ValueError("Unsupported endpoint_type")
        if model["temperature"] != 0.7 or model["max_output_tokens"] != 512:
            raise ValueError("Stage C frozen sampling parameters changed")
    return data


def environment_status(registry):
    """Return presence booleans only; never expose environment values."""
    rows = []
    for model in registry["models"]:
        rows.append({
            "model_key": model["model_key"],
            "api_key_env": model["api_key_env"],
            "api_key_set": bool(os.getenv(model["api_key_env"])),
            "base_url_env": model["base_url_env"],
            "base_url_set": bool(os.getenv(model["base_url_env"])),
        })
    return rows


def public_manifest(registry):
    hidden = {"api_key"}
    return {"schema_version": registry["schema_version"], "models": [
        {k: v for k, v in model.items() if k not in hidden} for model in registry["models"]
    ]}
