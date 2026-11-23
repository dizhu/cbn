#!/usr/bin/env python3
"""Portable CBN order processing. Commands print one JSON result to stdout."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

from common import SKILL, load_config, read_json
from locks import exclusive_file
from ledger import connect, update_handoff
from prepare import prepare
from tracking import tracking


def initialize(args):
    workspace = Path(args.workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_path = workspace / 'config.json'
    with exclusive_file(workspace / '.cbn-init.lock'):
        if config_path.exists():
            return doctor(argparse.Namespace(workspace=str(workspace)))
        templates = workspace / 'templates'
        templates.mkdir(mode=0o700, exist_ok=True)
        for filename in ('papaya-orders.xlsx', 'doz-orders.xlsx', 'doz-samples.xlsx'):
            target = templates / filename
            if target.exists():
                if target.read_bytes() != (SKILL / 'assets' / filename).read_bytes():
                    raise ValueError(f'模板已存在且内容不同（{filename}），请先核对工作区')
            else:
                shutil.copyfile(SKILL / 'assets' / filename, target)
        config = read_json(SKILL / 'assets' / 'config.default.json')
        settings = {**config['handoff'], 'stateFile': str(workspace / config['handoff']['stateFile'])}
        with connect(settings, initialize=True):
            pass
        pending = workspace / '.config.pending.json'
        with pending.open('w', encoding='utf-8') as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        pending.replace(config_path)
    return doctor(argparse.Namespace(workspace=str(workspace)))


def doctor(args):
    workspace = Path(args.workspace).expanduser().resolve()
    filename = workspace / 'config.json'
    if not filename.is_file():
        raise ValueError('工作区尚未初始化，请先运行 init')
    config = load_config(filename)
    from prepare import template_spec, validate_template
    for group in ('smallFace', 'papaya', 'doz', 'sample'):
        spec = template_spec(config, group)
        table = validate_template(spec)
        if table.rows:
            raise ValueError(f'{group} 空白模板含订单数据')
    with connect(config.get('handoff', {})) as db:
        db.execute('SELECT COUNT(*) FROM handoffs').fetchone()
    return {'workspace': str(workspace), 'config': str(filename), 'python': sys.version.split()[0],
            'ordersReady': True, 'trackingCarrierMappings': len(config.get('carriers', {})),
            'trackingNeeds': [] if config.get('carriers') else
            ['实际物流回传表头及配送业者原文', '同店当前 TikTok 上传模板认可的配送业者名称']}


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'doctor'):
        child = commands.add_parser(name)
        child.add_argument('--workspace', required=True)
    for name in ('prepare', 'handoff', 'tracking'):
        child = commands.add_parser(name)
        child.add_argument('--config', required=True)
        if name == 'prepare':
            child.add_argument('--orders', required=True)
            child.add_argument('--date')
            child.add_argument('--mode', choices=('live', 'replay'), default='live')
            child.add_argument('--groups')
        else:
            child.add_argument('--batch', required=True)
        if name == 'handoff':
            child.add_argument('--groups', required=True)
            child.add_argument('--action', choices=('reserve', 'confirm', 'release'), required=True)
            child.add_argument('--reference')
        else:
            child.add_argument('--out', required=True)
            child.add_argument('--source-scope', choices=('to-ship',))
        if name == 'tracking':
            child.add_argument('--current', required=True)
            child.add_argument('--feedback', action='append', required=True)
            child.add_argument('--upload-template', required=True)
    commands.add_parser('self-test')
    args = parser.parse_args(argv)
    if args.command == 'self-test':
        return subprocess.call([sys.executable, '-m', 'unittest', 'discover', '-s', str(SKILL / 'tests'), '-v'])
    if args.command == 'init':
        result = initialize(args)
    elif args.command == 'doctor':
        result = doctor(args)
    else:
        config = load_config(args.config)
        result = {'prepare': prepare, 'handoff': update_handoff, 'tracking': tracking}[args.command](config, args)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    os.umask(0o077)
    try:
        raise SystemExit(cli())
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as error:
        print(f'CBN 处理失败。{error}', file=sys.stderr)
        raise SystemExit(1)
