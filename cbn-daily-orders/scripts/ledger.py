"""Transactional local customer-handoff ledger."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import re
import sqlite3

from common import digest, read_json, text


def row_key(row):
    return row['orderId'], row['skuId']


def output_group(row):
    return 'sample' if row.get('sample') else row['group']


@contextmanager
def connect(settings, initialize=False):
    if not text(settings.get('shop')) or not text(settings.get('stateFile')):
        raise ValueError('正式运行需要配置店铺和交接台账路径')
    path = Path(settings['stateFile'])
    if not initialize and not path.is_file():
        raise ValueError('交接台账缺失，请核对原台账或先运行 init 初始化新工作区')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        if initialize:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS handoffs (
                shop TEXT NOT NULL, orderId TEXT NOT NULL, skuId TEXT NOT NULL,
                fingerprint TEXT NOT NULL, batchId TEXT NOT NULL, batchDate TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('reserved','sent')),
                reference TEXT, PRIMARY KEY(shop,orderId,skuId)
              );
              CREATE TABLE IF NOT EXISTS handoff_events (
                id INTEGER PRIMARY KEY, shop TEXT NOT NULL, batchId TEXT NOT NULL,
                action TEXT NOT NULL, reference TEXT, createdAt TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
              );
            ''')
        yield db
    finally:
        db.close()


def previous_handoffs(settings):
    with connect(settings) as db:
        return {(row['orderId'], row['skuId']): dict(row) for row in
                db.execute('SELECT * FROM handoffs WHERE shop=?', (settings['shop'],))}


def update_handoff(config, args):
    settings = config.get('handoff', {})
    reference = text(args.reference)
    if args.action == 'confirm' and not reference:
        raise ValueError('确认交接需要 --reference 提供实际已发记录编号')
    if args.action != 'confirm' and reference:
        raise ValueError('--reference 用于确认已交接')
    groups = args.groups.split(',')
    if len(set(groups)) != len(groups) or not set(groups) <= {'smallFace', 'papaya', 'doz', 'sample'}:
        raise ValueError('请核对 --groups')
    if len({'doz' if item == 'sample' else item for item in groups}) != 1:
        raise ValueError('一次登记一个客户的附件。DOZ 订单和样品可一起登记')
    batch_path = Path(args.batch).resolve()
    batch = read_json(batch_path)
    manifest = batch.get('handoff') or {}
    if not manifest.get('id') or manifest.get('shop') != settings.get('shop'):
        raise ValueError('交接需要同一店铺正式出表产生的 batch.json')
    rows = [row for row in batch.get('rows', []) if output_group(row) in groups]
    if (not rows or len({row_key(row) for row in rows}) != len(rows)
            or any(not isinstance(row.get(field), str) or not re.fullmatch(pattern, row[field])
                   for row in rows for field, pattern in [('orderId', '[0-9]+'), ('skuId', '[0-9]+'),
                                                         ('fingerprint', '[a-f0-9]{64}')])):
        raise ValueError('批次订单明细不完整或重复')
    if manifest.get('rowsHash') and manifest['rowsHash'] != digest(batch['rows']):
        raise ValueError('批次台账内容已修改，请核对原始批次')
    for group in groups:
        file = manifest.get('files', {}).get(group, {})
        name = file.get('name', '')
        if (not name or Path(name).name != name or '\\' in name
                or not any(output_group(row) == group for row in rows)):
            raise ValueError(f'此批次没有 {group} 的可交接记录')
        if args.action == 'reserve' and digest((batch_path.parent / name).read_bytes()) != file.get('hash'):
            raise ValueError(f'{group} 附件已变更，请核对并重新出表')
    with connect(settings) as db:
        db.execute('BEGIN IMMEDIATE')
        try:
            updated = 0
            for row in rows:
                key = settings['shop'], *row_key(row)
                previous = db.execute('SELECT * FROM handoffs WHERE shop=? AND orderId=? AND skuId=?', key).fetchone()
                if args.action == 'reserve':
                    if previous:
                        raise ValueError('所选附件含已交接或待确认的订单，请核对记录或重新出表')
                    db.execute('INSERT INTO handoffs VALUES (?,?,?,?,?,?,?,NULL)',
                               (*key, row['fingerprint'], manifest['id'], batch['date'], 'reserved'))
                else:
                    if (not previous or previous['batchId'] != manifest['id']
                            or previous['fingerprint'] != row['fingerprint']):
                        raise ValueError('交接记录与当前批次不符')
                    if previous['status'] == 'sent':
                        if args.action == 'confirm' and previous['reference'] == reference:
                            continue
                        raise ValueError('订单已确认交接，请核对原记录')
                    if args.action == 'confirm':
                        db.execute("UPDATE handoffs SET status='sent',reference=? WHERE shop=? AND orderId=? AND skuId=?",
                                   (reference, *key))
                    else:
                        db.execute('DELETE FROM handoffs WHERE shop=? AND orderId=? AND skuId=?', key)
                updated += 1
            # Older ledgers receive the audit table on the next init without changing their order state.
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='handoff_events'").fetchone():
                db.execute('INSERT INTO handoff_events(shop,batchId,action,reference) VALUES(?,?,?,?)',
                           (settings['shop'], manifest['id'], args.action, reference or None))
            db.execute('COMMIT')
        except Exception:
            db.execute('ROLLBACK')
            raise
    return {'date': batch['date'], 'groups': groups, 'action': args.action, 'rows': len(rows), 'updated': updated}
