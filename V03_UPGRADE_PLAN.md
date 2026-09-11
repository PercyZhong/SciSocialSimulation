# SciMirror v0.3 upgrade plan

## Baseline

- Starting commit: `3f5b5d0`; only `SCIMIRROR_V03_CODEX_TASK.md` was untracked.
- Local engineering environment: Windows, Python 3.12.7, standard library only.
- Existing v0.1/v0.2 regression suite: 23/23 passed before v0.3 changes.
- No tracked v0.2 run archive is present in this checkout. v0.3 comparisons therefore use newly generated, explicitly labelled runs and frozen probes; they are not represented as historical evidence.
- No authorized external reviewer or paid-model budget is available. The local workflow will stop at `awaiting_external_reviews` after exporting a human-review package.

## Implementation map

| Requirement | Existing implementation | v0.3 mapping |
|---|---|---|
| Query construction and gated ranking | `corpus.py::retrieve_v02` | `v03_retrieval.py`, called by the existing v0.2 engine through an explicit adapter |
| Frozen/live retrieval diagnostics | retrieval records inside decision audit | dedicated v0.3 probe and audit exporters |
| Review population/sample/blinding/import | v0.2 blind-review CSV only | `v03_review.py` with public/private separation and persistent random IDs |
| Agreement and quality estimators | none | `v03_review.py` and `v03_analysis.py` |
| Project-flow and output duplicates | aggregate v0.2 metrics | `v03_analysis.py` post-simulation analysis |
| Commands/archive validation | `v02_run.py`, `v02_validate.py` | `execute_v03.py`, `v03_pipeline.py` and schema 0.3 delivery validation |

## Compatibility

- v0.1/v0.2 commands, configurations, event schema and old output directories remain unchanged.
- v0.3 configuration is explicitly adapted to the v0.2 simulation state machine; the adapter and hashes are recorded in the v0.3 manifest.
- `legacy_v02` remains selectable. `relevance_gated` shares one relevance pool across policies, permits shortfall and never pads with unrelated evidence.
- Review/analysis code reads immutable final states and cannot update simulation state or simulation caches.
- `outputs_v03/`, review private mappings and runtime caches are not committed.

## Execution

1. Implement retrieval query/ranker/audit and targeted fixtures.
2. Implement review sampling, blinding, import, agreement and quality/duplicate/flow analysis.
3. Add versioned configs and CLI, then run regression plus v0.3 tests.
4. Run frozen probes, smoke, 3-seed main, full/no_topic/no_retrieval, and seeds 100–109 diagnostic.
5. Export a review package from the 10-seed population and mark external review/quality inference as waiting.
6. Validate archives and write `V03_UPGRADE_REPORT.md` without real-world causal claims.
