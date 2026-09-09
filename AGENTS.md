# SciMirror development and validation rules

## Target platform

- Linux is the authoritative runtime and experiment platform. Windows may be used only to edit and synchronize source files.
- Use Python 3.11 or newer. Never treat a run under an older interpreter as a valid test or experiment.
- Do not copy `.venv/`, `cache/`, or `outputs/` between Windows and Linux. Recreate the virtual environment on Linux.
- Keep runtime code platform-neutral: use `pathlib`, UTF-8, LF line endings, and Python standard-library APIs available on Linux. Do not add Windows-only paths, shell syntax, registry access, drive-letter assumptions, or `py.exe` dependencies.

## Required validation for code changes

Run validation in a Linux terminal from the project root:

```bash
python3 --version
python3 run.py doctor --config configs/mock.json
python3 run.py test
```

For changes to simulation state, events, branching, metrics, persistence, configuration, corpus handling, or backends, also run the complete offline experiment:

```bash
python3 run.py run --config configs/mock.json
```

Accept the mock experiment only when `status.json` is `completed`, `summary.csv` has 18 data rows, all 18 final states contain 20 agents at tick 30, fork hashes match within each seed, and all event logs replay successfully.

## LLM safety

- Do not run `llm_pilot` until `doctor --probe` and `estimate` succeed and the user explicitly authorizes live calls.
- Never print, read back, commit, or request a plaintext API key. Report only whether required variables are set.
- Never launch the 15,000-call research configuration without explicit user authorization and a reviewed budget.
- Mock and synthetic-corpus outputs are engineering validation only, not real scientific findings.
