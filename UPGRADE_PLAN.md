# SciMirror v0.2 upgrade plan

## Baseline

- Baseline checked on Windows/Python 3.12.7 because the authoritative remote Linux terminal is user-operated.
- Existing v0.1 test suite: 12/12 passed before v0.2 changes.
- `execute.py`, `configs/mock.json`, `configs/llm_pilot.json`, `configs/llm_research.json`, `sample_results/`, and existing `outputs/` remain v0.1-compatible and are not overwritten.
- v0.2 uses explicit `schema_version: "0.2"`, separate configurations, cache protocol, output schema, and `execute_v02.py`.

## Implementation map

| Requirement | Current v0.1 location | v0.2 implementation |
|---|---|---|
| Independent recognition | `engine.py`: `recognition=1-novelty` | `topics.py`: frozen topic classifier/statistics and community-attention proxy |
| Policy context and audit | policy weights embedded in `engine.py` | `policy.py`: stage switches, neutralization and utility decompositions; decision audit events/export |
| Topic and retrieval paths | `observation()` retrieves only by field | topic selection in `engine.py`; policy-aware fixed candidate retrieval in `corpus.py` |
| Invitation path | variable pool and fixed cross-field bonus | `candidate_pool.py`: frozen K schedule; invitation utility over the offered list |
| Exit path and projects | no project entity or exit | `v02_state.py` plus `v02_engine.py`: `Project` state and merge/exit/transition rules |
| Effort and credit | energy only | `accounting.py`: append-only effort ledger, conserved fractional credit and output accounting |
| Split outputs | `metrics.py`: one `outputs` field | v0.2 flow/stock/effort metrics and expanded paired comparisons |
| Replay/archive | per-branch replay inside `experiment.py` | retained replay plus independent `replay_validation.json` and audit exports |

## Compatibility strategy

- `schema_version` is mandatory for v0.2 configs. The v0.1 loader continues to accept only the existing v0.1 files; the v0.2 runner rejects a missing or different schema explicitly.
- Old journals remain readable because new `Project` and Agent fields have restoration defaults; old results stay read-only.
- v0.2 IDs never include policy labels. Experimental labels remain in private branch metadata and are excluded from blind-review rows.
- All policy paths can be neutralized independently. `all_policy_paths_off` also neutralizes institutional feedback, prompts, semantic cache keys and decision records.

## Execution order

1. Add topics, policy, candidate schedule, projects and accounting modules with invariants.
2. Extend state, corpus and backend schema while preserving v0.1 entry behavior.
3. Add a separate v0.2 engine/experiment pipeline and explicit configs.
4. Add targeted unit/integration tests and run the complete suite.
5. Run the 18-branch v0.2 main mock, six 18-branch ablations, and the fixed 10-seed diagnostic.
6. Validate replay, conservation and archive files; update README/protocol and write `UPGRADE_REPORT.md`.

No live LLM request or document download is authorized by this plan.
