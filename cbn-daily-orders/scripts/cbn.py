#!/usr/bin/env python3
"""Bootstrap a workspace-local Python environment and run the CBN skill."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv

from locks import exclusive_file


SKILL = Path(__file__).resolve().parent.parent


def main():
    if sys.version_info < (3, 9):
        raise RuntimeError('需要 Python 3.9 或更新版本。请用 Codex 的工作区 Python 或 uv 的 Python 3.12 运行。')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('command', choices=['init', 'doctor', 'prepare', 'handoff', 'tracking', 'self-test'])
    args, remaining = parser.parse_known_args()
    workspace = args.workspace.expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    env = workspace / '.venv'
    executable = env / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    requirements = (SKILL / 'requirements.txt').read_bytes()
    stamp = env / 'cbn-requirements.txt'
    with exclusive_file(workspace / '.cbn-env.lock'):
        if not executable.is_file():
            venv.EnvBuilder(with_pip=True).create(env)
        ready = subprocess.run([str(executable), '-c', 'import openpyxl, defusedxml; assert openpyxl.__version__ == "3.1.5"'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if not ready or not stamp.is_file() or stamp.read_bytes() != requirements:
            subprocess.run([str(executable), '-m', 'pip', 'install', '--disable-pip-version-check', '--require-hashes',
                            '--only-binary=:all:', '-r', str(SKILL / 'requirements.txt')], check=True, stdout=sys.stderr)
            stamp.write_bytes(requirements)
    command = [str(executable), str(SKILL / 'scripts/orders.py'), args.command]
    if args.command in {'init', 'doctor'}:
        command.extend(['--workspace', str(workspace)])
    elif args.command != 'self-test' and '--config' not in remaining:
        command.extend(['--config', str(workspace / 'config.json')])
    command.extend(remaining)
    return subprocess.call(command)


if __name__ == '__main__':
    os.umask(0o077)
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f'CBN 启动失败。{error}', file=sys.stderr)
        raise SystemExit(1)
