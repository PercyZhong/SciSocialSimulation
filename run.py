#!/usr/bin/env python3
"""Single cross-platform entry point; run from any directory."""
import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


# 将命令行中的相对路径统一解析为相对于项目根目录的绝对路径。
def project_path(value):
    """Resolve CLI paths consistently when run.py is launched outside the repo."""
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


# 解析命令行参数并分发环境配置、测试、实验、诊断和重放命令。
def main():
    parser = argparse.ArgumentParser(description='SciMirror execution entry')
    parser.add_argument('command', choices=['setup','test','run','estimate','doctor','replay'])
    parser.add_argument('--config', default='configs/mock.json')
    parser.add_argument('--output')
    parser.add_argument('--events')
    parser.add_argument('--probe', action='store_true')
    args = parser.parse_args()
    if sys.version_info < (3,11):
        raise SystemExit('Python >= 3.11 required')
    if args.command == 'setup':
        import venv
        venv.EnvBuilder(with_pip=False).create(ROOT/'.venv')
        print('Created .venv; no third-party dependencies. Select .venv Python in VS Code.')
    elif args.command == 'test':
        raise SystemExit(subprocess.call([sys.executable,'-m','unittest','discover','-s','tests','-v'], cwd=ROOT))
    elif args.command in ('run','estimate'):
        from scimirror.experiment import load_config, run, estimate
        cfg = load_config(project_path(args.config))
        print(json.dumps(estimate(cfg), indent=2))
        if args.command == 'run':
            import datetime
            out = (project_path(args.output) if args.output else
                   ROOT/'outputs'/datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
            print(run(cfg, out))
    elif args.command == 'replay':
        from scimirror.events import replay
        from scimirror.common import digest
        if not args.events:
            raise SystemExit('--events required')
        w = replay(project_path(args.events))
        print(json.dumps({'tick': w.tick, 'agents': len(w.agents), 'state_hash': digest(w.export())}))
    else:
        from scimirror.experiment import load_config, estimate
        from scimirror.corpus import Corpus
        cfg_path = project_path(args.config)
        cfg = load_config(cfg_path)
        corpus_path = project_path(cfg['corpus'])
        corpus = Corpus(corpus_path, cfg['cutoff_year'], cfg['allow_synthetic'])
        print('Python:', sys.version.split()[0])
        print('Executable:', Path(sys.executable).resolve())
        print('Platform:', platform.platform())
        print('Root:', ROOT)
        print('Config:', cfg_path.resolve())
        print('Backend:', cfg['backend'])
        print('Corpus:', corpus_path.resolve())
        print('Corpus papers after validation/filtering:', len(corpus.papers))
        print('Project writable:', 'YES' if os.access(ROOT, os.W_OK) else 'NO')
        print('Estimate:', json.dumps(estimate(cfg), separators=(',', ':')))
        for k in ['SCIMIRROR_BASE_URL','SCIMIRROR_MODEL','SCIMIRROR_API_KEY']:
            print(k, 'SET' if os.getenv(k) else 'UNSET')
        if args.probe:
            from scimirror.backend import Backend, validate_response
            from scimirror.state import initialize
            from scimirror.engine import observation
            if cfg['backend'] != 'chat':
                raise SystemExit('Use a chat config for --probe')
            world = initialize(cfg['agents'], cfg['seeds'][0])
            ctx = observation(world, 's00', corpus)
            response = Backend(cfg,ROOT).generate('propose',ctx,['probe',42])
            validate_response('propose',response,ctx)
            print('Live endpoint probe passed: two valid candidate cards.')


if __name__ == '__main__':
    main()
