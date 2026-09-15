# Stage A closure experiment

The closure experiment compares the committed `stage_a_fixed` reference with the separately implemented `stage_a_semantic_guarded` ranker. It uses 108 deterministic states, two corpora, two rankers, and three policies (1296 retrieval cases). These rows are diagnostics, not independent social worlds.

The semantic ranker gates evidence using only title and abstract phrases or concept-group co-occurrence within an 18-token window. Field, memory, recognition, and policy affect only context/reranking and cannot admit a failed document. Profiles are separate from `data/topics.json`, so recognition classification remains frozen.

Quality thresholds are frozen in `configs/stage_a_closure.json`. Failure produces an archive and exit code 2; it must not trigger threshold or gold editing. All gold is Codex-designed synthetic engineering annotation and requires later independent human validation.

Linux command:

```bash
python3 execute_stage_a_closure.py all --config configs/stage_a_closure.json --output outputs_stage_a_closure/<run_id>
```

No network, LLM, embedding service, GPU, or paid API is used.
