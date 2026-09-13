# SciMirror Stage A implementation plan

## Baseline snapshot

- Commit: `2c98c8437d203037e471d4ac9eedc95b26d23492`
- Tracked-file-list hash: `b41130eeedb7bd2959090ce76247f7aa8181dd6d`
- Local diagnostic interpreter: Python 3.12.7; authoritative delivery remains Linux Python 3.11+.
- Existing regression suite: 41/41 passed before Stage A changes.
- Corpus SHA256: `5f48660b9dcc0bc0b2d937c35d95304007af99efb6bcd5c0a83fc29cdbd21086`
- Topics SHA256: `8dd02ba6c840b24ed04e32d0f327ce74a7bfc6d6648bea0d6033c2d7e0ee83f6`
- v0.3 retrieval-config SHA256: `0e4c2dd5022dd3aa1f3596cc14ad74952d595d1c05e3f598c1459de4e4c9a27f`
- Pre-existing untracked task documents, archives, and review files are user data and are not modified.

## Module mapping and reproduced behavior

- Query construction, relevance, gating, ranking, and deduplication: `scimirror/v03_retrieval.py`.
- Corpus loading and production retrieval dispatch: `scimirror/corpus.py`.
- Semantic-memory input and read-history update: `scimirror/v02_engine.py` and `scimirror/v02_state.py`.
- v0.3 configuration and production orchestration: `scimirror/v03_pipeline.py` and `execute_v03.py`.
- Existing tests: `tests/test_v03.py`.

The archived v0.3 main run reproduces the reported symptoms: 1,440 retrieval calls, 28,800 audited candidates, zero nonzero memory components, all 28,800 candidates qualified, and 204 of 4,320 selected slots without the selected topic label.

Root-cause hypotheses confirmed from the implementation:

1. Memory receives underscore topic IDs such as `agent_memory`; the existing tokenizer retains the underscore while papers contain separate words, producing zero lexical overlap.
2. Qualification uses the weighted total including field preference. With field weight 0.2 and threshold 0.08, a same-field match alone passes the gate.
3. Recall is corpus-wide, but field preference participates in the value used for both candidate truncation and qualification, so it can suppress cross-field topic evidence before reranking.
4. Near-duplicate clustering uses a 0.97 lexical threshold and does not normalize the explicit synthetic `scenario N` / `Variant N` markers, so known template variants remain separate.

## Minimal implementation scope

- Preserve `relevance_gated` exactly as the traceable `baseline_v03` behavior.
- Add an explicit `stage_a_fixed` production retrieval mode; do not change any old configuration meaning.
- Normalize topic IDs and semantic-memory terms consistently without changing the corpus tokenizer used by social metrics.
- Separate topic/evidence relevance from field, memory, and policy preferences. Gate only on evidence relevance, then rerank the qualified common pool.
- Add fixture-aware duplicate normalization only for records explicitly marked synthetic; preserve meaningful numbers for ordinary documents.
- Add a frozen-state experiment driver, configuration, 15 targeted probes, analysis, resume checks, and validation.
- Add production-interface regression tests while leaving reward, collaboration, invitation, exit, accounting, and review mechanisms unchanged.

## Baseline and fixed coexistence

The Stage A matrix calls the current v0.3 implementation as `baseline_v03` and the new implementation as `stage_a_fixed` against the same corpus snapshot, topic snapshot, frozen states, policies, read-history input, and candidate budget. The frozen-state hash excludes ranker and policy. Old v0.3 outputs remain read-only; new artifacts are written only below `outputs_stage_a/`.
