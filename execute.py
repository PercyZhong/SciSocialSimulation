#!/usr/bin/env python3
"""Codex execution file: create isolated environment, test, execute offline experiment."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


# 创建项目虚拟环境，运行测试并执行完整的离线 mock 实验。
def main():
    if sys.version_info < (3,11):
        raise SystemExit('Install Python 3.11+ then rerun execute.py')
    subprocess.run([sys.executable, str(ROOT/'run.py'), 'setup'], check=True, cwd=ROOT)
    python = ROOT/'.venv'/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
    subprocess.run([str(python), str(ROOT/'run.py'), 'test'], check=True, cwd=ROOT)
    subprocess.run([str(python), str(ROOT/'run.py'), 'run', '--config', 'configs/mock.json'], check=True, cwd=ROOT)
    print('Done. Open newest outputs/<run>/REPORT.md. No paid API request was made.')


if __name__ == '__main__':
    main()
