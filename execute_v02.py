#!/usr/bin/env python3
"""Create an isolated environment, test and run one v0.2 offline mock archive."""
import argparse
import datetime
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


# 创建v0.2环境，执行全套测试并运行不联网的20-Agent主实验。
def main():
    parser = argparse.ArgumentParser(description='SciMirror v0.2 offline executor')
    parser.add_argument('--suite', action='store_true', help='also run smoke, six ablations and 10-seed diagnostic')
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise SystemExit('Install Python 3.11+ then rerun execute_v02.py')
    subprocess.run([sys.executable, str(ROOT/'run.py'), 'setup'], check=True, cwd=ROOT)
    python = ROOT/'.venv'/('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    subprocess.run([str(python), str(ROOT/'v02_run.py'), 'test'], check=True, cwd=ROOT)
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    base = ROOT/'outputs_v02'/stamp
    runs = [('main', 'configs/v02_mock_full.json', 'full')]
    if args.suite:
        runs = [('smoke', 'configs/v02_mock_smoke.json', 'full'), *runs]
        runs.extend((f'ablations/{name}', 'configs/v02_mock_full.json', name) for name in
                    ('full', 'no_topic', 'no_retrieval', 'no_invitation', 'no_exit_policy', 'all_policy_paths_off'))
        runs.append(('diagnostic_10seed', 'configs/v02_mock_diagnostic.json', 'full'))
    for folder, config, ablation in runs:
        subprocess.run([str(python), str(ROOT/'v02_run.py'), 'run', '--config', config,
                        '--output', str(base/folder), '--ablation', ablation], check=True, cwd=ROOT)
    if args.suite:
        subprocess.run([str(python), str(ROOT/'v02_validate.py'), '--smoke', str(base/'smoke'),
                        '--main-run', str(base/'main'), '--ablations', str(base/'ablations'),
                        '--diagnostic', str(base/'diagnostic_10seed'), '--output',
                        str(base/'DELIVERY_VALIDATION.json')], check=True, cwd=ROOT)
    print('SciMirror v0.2 offline validation complete. No paid API request was made.')


if __name__ == '__main__':
    main()
