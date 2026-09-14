# SciMirror Stage A supplement plan

## Frozen starting point

- Repository commit: `e9cd32ed07a9298696bd19ce53bbe221f15724a5`.
- `stage_a_reference` is the committed `retrieve_stage_a_fixed` implementation; the supplement does not change its ranking formula.
- Existing Stage A outputs and `data/demo_papers.jsonl` remain read-only.
- The only pre-existing untracked inputs are the supplement task, older intermediate Stage A runs, review data, and old archives; they are preserved.

## Actual module mapping

- Retrieval/query/dedup/policy decomposition: `scimirror/v03_retrieval.py`.
- Frozen-state protocol and Stage A reference runner: `scimirror/stage_a.py`.
- Production corpus dispatch and engine adapter: `scimirror/corpus.py`, `scimirror/v02_engine.py`, `scimirror/v03_pipeline.py`.
- Supplement evidence builder, isolated gold loader, E1–E4 runners, metrics, validation and packaging: `scimirror/stage_a_supplement.py`.
- Cross-platform command entry: `execute_stage_a_supplement.py`.

## Minimal change and data strategy

1. Deterministically build a separate expanded synthetic corpus with content cards before evaluation labels.
2. Keep gold family IDs and query-relative relevance in separate files and enforce a production-field whitelist.
3. Compare identical `stage_a_reference` and `stage_a_supplement` ranker behavior on original/expanded corpora; equality is an expected, reportable result because no ranking defect requires a formula change.
4. Add independent policy unit and end-to-end fixtures to distinguish formula transmission from lack of choice capacity.
5. Recompute gold cluster, dedup, shortfall and all-topic-pair metrics from row-level logs.
6. Exercise the existing 20-Agent six-phase engine for one balanced/open 12-tick mock world without changing social rules.

## Previously observed limitation

The completed Stage A expanded no evidence: all 324 fixed-ranker calls shortfalled and the three policies selected identical sets because the original corpus usually supplies only one independent template family per topic. The supplement preserves this original-corpus result and adds genuinely different evidence families rather than padding retrieval results.
