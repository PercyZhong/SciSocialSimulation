# Stage C implementation path map

- Decision protocol and provider adapter: `scimirror/decision_backend.py`, `scimirror/decision_schema.py`
- Secret-safe two-model registry: `scimirror/model_registry.py`, `configs/models.stage_c.example.json`
- Separate eight-call connection pilot: `configs/stage_c_pilot.example.json`
- Budget and usage accounting: `scimirror/usage_ledger.py`
- Frozen C0 matrix, mock/live execution, checkpoints and metrics: `scimirror/stage_c_frozen.py`
- C1 state-integration boundary (prepared, not executed): `scimirror/stage_c_live.py`
- Stage B archive repair and extracted-tree reproduction: `scimirror/stage_c_packaging.py`
- Stage C checksum, ZIP and extracted-tree replay: `scimirror/stage_c_delivery.py`
- CLI: `execute_stage_c.py`
- Tests: `tests/test_stage_c.py`

The existing production ranker and historical result directories are unchanged. C1 and all real API calls remain unauthorized and unexecuted in this delivery.
