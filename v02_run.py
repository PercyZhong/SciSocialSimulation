#!/usr/bin/env python3
"""Cross-platform command line entry for SciMirror v0.2."""
import argparse
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


# 将相对路径按项目根目录解析。
def project_path(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT/path


# 解析v0.2运行、估算、测试和重放命令。
def main():
    parser = argparse.ArgumentParser(description='SciMirror v0.2 runner')
    parser.add_argument('command', choices=['run', 'estimate', 'test', 'replay'])
    parser.add_argument('--config', default='configs/v02_mock_full.json')
    parser.add_argument('--output')
    parser.add_argument('--events')
    parser.add_argument('--ablation', default='full')
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise SystemExit('Python >= 3.11 required')
    if args.command == 'test':
        import subprocess
        raise SystemExit(subprocess.call([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=ROOT))
    if args.command == 'replay':
        from scimirror.common import digest
        from scimirror.events import replay_v02
        if not args.events:
            raise SystemExit('--events required')
        world = replay_v02(project_path(args.events))
        print(json.dumps({'tick': world.tick, 'agents': len(world.agents), 'state_hash': digest(world.export())}))
        return
    from scimirror.v02_experiment import estimate_v02, load_v02_config, run_v02
    cfg = load_v02_config(project_path(args.config))
    print(json.dumps(estimate_v02(cfg), indent=2))
    if args.command == 'run':
        target = (project_path(args.output) if args.output else ROOT/'outputs_v02'/
                  datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        print(run_v02(cfg, target, args.ablation))


if __name__ == '__main__':
    main()


