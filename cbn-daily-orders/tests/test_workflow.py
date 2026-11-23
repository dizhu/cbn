from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

from openpyxl import Workbook, load_workbook
from common import load_config, phone, read_json, write_cell
from ledger import update_handoff
from orders import doctor, initialize
from prepare import prepare
from tracking import tracking


HEADERS = ['注文ID', '注文状況', '注文のサブ状況', 'SKU ID', 'セラーSKU', '商品名', 'バリエーション', '数量',
           'SKUの元の価格', 'カスタマーのユーザー名', '受取人', '名', '姓', '国', '郵便番号', '都道府県',
           '市区町村', '町名', '詳細住所1', '詳細住所2', '電話番号', '配送先情報', 'まとめて出品', 'カスタマーからのメッセージ']
FEEDBACK = ['客户订单编号', '快递单号', '快递公司', 'SKU ID', '已发平台数量', '整单同包']
def ID(value):
    return f'900000000000000{value:03d}'


def SKU(value):
    return f'910000000000000{value:03d}'


def order(number, sku, product, count=1, seller='TK04', amount=1901, tel='(+81)090-0000-0001'):
    return [ID(number), '未発送', '発送待ち', SKU(sku), seller, product, 'デフォルト', count, f'{amount:,} JPY',
            'synthetic_user', 'サンプル 太郎', '太郎', 'サンプル', '日本', '012-3456', '東京都', 'サンプル市',
            '見本町', '1-2-3', '架空ビル103', tel, 'サンプル送り先', '', '']


def write_fixture(path, rows):
    book = Workbook()
    sheet = book.active
    for row_number, values in enumerate(rows, 1):
        for column, value in enumerate(values, 1):
            write_cell(sheet.cell(row_number, column), value)
    book.save(path)
    return path


class Workflow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cbn-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / '客户 工作目录'
        initialize(argparse.Namespace(workspace=self.workspace))
        self.config = load_config(self.workspace / 'config.json')
        self.config['carriers'] = {'synthetic-carrier': 'TEST CARRIER'}
        self.config['feedback'] = {'doz': {'quantity': '已发平台数量',
                                           'wholeOrder': {'header': '整单同包', 'value': '已核实'}}}
        self.rows = [order(1, 1, '小顔 テスト美容液'), order(2, 2, '青パパイヤ テスト', 2),
                     order(3, 3, 'DozoFreesh 水果茶 オレンジ', 2, '00123', 6633),
                     order(3, 4, 'DOZ 水果茶 レモン', tel='+8190-0000-0001'),
                     order(4, 5, 'DOZ 水果茶 サンプル', amount=0),
                     order(5, 6, 'DOZ 水果茶 オレンジ', seller='00007'),
                     order(5, 7, '倉庫向けテスト商品'), order(6, 8, 'DOZ 水果茶 グレープ'),
                     order(7, 9, '青パパイヤ テスト', 2), order(8, 10, '倉庫向けテスト商品'),
                     order(9, 11, '小颜 テスト美容液'), order(10, 12, '青パパイヤ テスト')]
        self.source = self.source_file('source', self.rows)
        self.upload = write_fixture(self.root / 'upload.xlsx', [
            ['配送情報（検証用）'], ['注文ID', 'SKU ID', '商品名', '数量', '配送業者名', '追跡ID', '領収書ID'],
            ['Required', 'Optional', 'Optional', 'Optional', 'Required', 'Required', 'Optional'],
            ['old-order', 'old-sku', 'old-product', 99, 'old-carrier', 'old-tracking']])

    def source_file(self, name, rows):
        return write_fixture(self.root / (name + '.xlsx'), [HEADERS, ['TikTok Shop 固有の注文 ID'], *rows])

    def prepare(self, name='customer', **overrides):
        args = dict(orders=str(self.source), out=str(self.root / name), date='2026-11-09', mode='replay',
                    groups=None, source_scope=None)
        args.update(overrides)
        return prepare(self.config, argparse.Namespace(**args))

    def handoff(self, action, group='doz,sample', name='customer', reference=None):
        args = argparse.Namespace(batch=str(self.root / name / 'batch.json'), groups=group,
                                  action=action, reference=reference)
        return update_handoff(self.config, args)

    def shipping(self, details, name='tracking', current=None):
        feedback = write_fixture(self.root / (name + '.xlsx'), [FEEDBACK, *details])
        args = argparse.Namespace(batch=str(self.root / 'customer/batch.json'), current=str(current or self.source),
                                  source_scope=None, feedback=['doz=' + str(feedback)],
                                  upload_template=str(self.upload), out=str(self.root / name))
        result = tracking(self.config, args)
        return result, read_json(self.root / name / 'issues.json')

    def test_four_workbooks(self):
        result = self.prepare()
        self.assertEqual(result['customerRows'], {'smallFace': 2, 'papaya': 3, 'doz': 4, 'sample': 1})
        self.assertEqual(result['otherRows'], 2)
        sheet = load_workbook(self.root / 'customer/【DozoFreesh】注文-2026-11-09.xlsx')['订单']
        self.assertEqual([sheet.cell(2, col).value for col in (2, 4, 5, 7, 8, 9, 17, 21)],
                         [ID(3), '00123', 12, '090-0000-0001', '012-3456',
                          '東京都サンプル市見本町1-2-3架空ビル103', 6633, SKU(3)])
        self.assertEqual(sheet['G3'].value, '09000000001')
        self.assertEqual(sheet['B2'].data_type, 's')
        self.assertEqual(sheet.auto_filter.ref, 'A1:V5')
        self.assertEqual(sheet['V1'].value, '商品名')
        samples = load_workbook(self.root / 'customer/【DozoFreesh】サンプル-2026-11-09.xlsx').active
        self.assertEqual(samples['C2'].value, datetime(2026, 11, 9))
        self.assertEqual(samples['J2'].value, ID(4))
        self.assertIsNone(samples['A2'].value)
        papaya = load_workbook(self.root / 'customer/【青パパイヤ】注文-2026-11-09.xlsx').active
        self.assertEqual(papaya['R2'].value, '(+81)090-0000-0001')

    def test_setup_idempotent(self):
        self.prepare(mode='live')
        self.handoff('reserve')
        before = Path(self.config['handoff']['stateFile']).read_bytes()
        initialize(argparse.Namespace(workspace=self.workspace))
        self.assertEqual(Path(self.config['handoff']['stateFile']).read_bytes(), before)

    def test_interrupted_init_can_retry(self):
        workspace = self.root / 'retry-init'
        with patch('orders.connect', side_effect=OSError('synthetic interrupted initialization')):
            with self.assertRaises(OSError):
                initialize(argparse.Namespace(workspace=workspace))
        self.assertFalse((workspace / 'config.json').exists())
        self.assertTrue(initialize(argparse.Namespace(workspace=workspace))['ordersReady'])

    def test_corrupt_workbook_has_readable_error(self):
        source = self.root / 'corrupt.xlsx'
        source.write_text('truncated download')
        with self.assertRaisesRegex(ValueError, '工作簿损坏'):
            self.prepare(orders=str(source))

    def test_doctor_rejects_changed_header_mapping(self):
        path = self.workspace / 'config.json'
        config = read_json(path)
        config['templates']['papaya']['columns']['B']['header'] = 'WRONG COLUMN'
        path.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, '列表头与映射不符'):
            doctor(argparse.Namespace(workspace=self.workspace))

    def test_phone_variants(self):
        for source, expected in [('(+81)090-0000-0001', '090-0000-0001'), ('+8190-0000-0001', '09000000001'),
                                 ('+81 (0)90 0000 0001', '090 0000 0001'), ('03(0000)0001', '0300000001'),
                                 ('０９０ー００００ー０００１'.replace('ー', '－'), '090-0000-0001')]:
            with self.subTest(source=source):
                self.assertEqual(phone(source), expected)
        for source in ['7', '090000000', '+4409000000001', '090ABC00001', '09000000001 ext 3', 9000000001, None]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                phone(source)

    def test_invalid_data_rolls_back(self):
        self.rows[2][HEADERS.index('受取人')] = None
        source = self.source_file('invalid', self.rows)
        with self.assertRaisesRegex(ValueError, '收件人'):
            self.prepare(orders=str(source))
        self.assertFalse((self.root / 'customer').exists())

    def test_numeric_identifier_rejected(self):
        self.rows[0][0] = int(ID(1))
        with self.assertRaisesRegex(ValueError, '失真'):
            self.prepare(orders=str(self.source_file('numeric-id', self.rows)))

    def test_statusless_needs_explicit_scope(self):
        source = write_fixture(self.root / 'statusless.xlsx', [
            [value for i, value in enumerate(HEADERS) if i not in (1, 2)], ['说明'],
            *[[value for i, value in enumerate(row) if i not in (1, 2)] for row in self.rows]])
        with self.assertRaisesRegex(ValueError, '无状态列'):
            self.prepare(orders=str(source))
        self.assertEqual(len(self.prepare(orders=str(source), source_scope='to-ship')['files']), 4)

    def test_wrong_status_rejected_even_with_scope(self):
        self.rows[0][2] = '発送済み'
        with self.assertRaisesRegex(ValueError, '不在待发货'):
            self.prepare(orders=str(self.source_file('shipped', self.rows)), source_scope='to-ship')

    def test_duplicate_keys_and_ambiguous_product(self):
        for rows in [self.rows + [self.rows[0]], [order(1, 1, '小顔 DOZ テスト')]]:
            with self.subTest(rows=len(rows)), self.assertRaises(ValueError):
                self.prepare(orders=str(self.source_file('duplicate', rows)))

    def test_formula_like_note_stays_text(self):
        self.rows[0][-1] = '=HYPERLINK("https://example.invalid", "test")'
        self.prepare(orders=str(self.source_file('formula', self.rows)))
        cell = load_workbook(self.root / 'customer/【小顔】注文-2026-11-09.xlsx').active['U2']
        self.assertEqual(cell.value, self.rows[0][-1])
        self.assertEqual(cell.data_type, 's')

    def test_customer_text_preserves_fullwidth_characters(self):
        self.rows[0][HEADERS.index('商品名')] = '小顔 ＡＢＣ＆１２３ テスト'
        self.rows[2][HEADERS.index('詳細住所2')] = '見本ビル１０３ Ａ棟'
        self.prepare(orders=str(self.source_file('fullwidth', self.rows)))
        face = load_workbook(self.root / 'customer/【小顔】注文-2026-11-09.xlsx').active
        doz = load_workbook(self.root / 'customer/【DozoFreesh】注文-2026-11-09.xlsx').active
        self.assertEqual(face['D2'].value, self.rows[0][HEADERS.index('商品名')])
        self.assertTrue(doz['I2'].value.endswith('見本ビル１０３ Ａ棟'))

    def test_empty_selection(self):
        result = self.prepare(orders=str(self.source_file('none', [order(1, 1, '倉庫用テスト')])) )
        self.assertEqual(result['files'], [])
        self.assertEqual(result['otherRows'], 1)

    def test_existing_output_preserved(self):
        self.prepare()
        before = (self.root / 'customer/summary.json').read_bytes()
        with self.assertRaisesRegex(ValueError, '目录已存在'):
            self.prepare()
        self.assertEqual(before, (self.root / 'customer/summary.json').read_bytes())

    def test_no_sent_mark_for_generated_files(self):
        self.prepare(mode='live')
        result = self.prepare('tomorrow', mode='live', date='2026-11-10')
        self.assertEqual(sum(result['customerRows'].values()), 10)

    def test_cross_day_reserved_sent_release(self):
        self.prepare(mode='live')
        self.assertEqual(self.handoff('reserve')['updated'], 5)
        reserved = self.prepare('reserved', mode='live', date='2026-11-10')
        self.assertEqual(reserved['handoffReviewRows'], 5)
        self.assertEqual(self.handoff('release')['updated'], 5)
        self.assertEqual(sum(self.prepare('released', mode='live')['customerRows'].values()), 10)
        self.handoff('reserve')
        self.assertEqual(self.handoff('confirm', reference='synthetic-message-id')['updated'], 5)
        self.assertEqual(self.handoff('confirm', reference='synthetic-message-id')['updated'], 0)
        sent = self.prepare('sent', mode='live', date='2026-11-10')
        self.assertEqual(sent['alreadyHandedOffRows'], 5)
        self.assertEqual(sent['customerRows'], {'smallFace': 2, 'papaya': 3, 'doz': 0, 'sample': 0})
        with self.assertRaises(ValueError):
            self.handoff('release')

    def test_changed_sent_order_held(self):
        self.prepare(mode='live')
        self.handoff('reserve')
        self.handoff('confirm', reference='synthetic-message')
        self.rows[2][HEADERS.index('町名')] = '変更先町'
        changed = self.source_file('changed', self.rows)
        self.assertEqual(self.prepare('changed', mode='live', orders=str(changed))['handoffReviewRows'], 2)

    def test_edited_attachment_and_batch_rejected(self):
        result = self.prepare(mode='live')
        file = self.root / 'customer' / result['files'][0]
        file.write_bytes(file.read_bytes() + b'test-edited')
        with self.assertRaisesRegex(ValueError, '附件已变更'):
            self.handoff('reserve', 'smallFace')
        path = self.root / 'customer/batch.json'
        batch = read_json(path)
        batch['rows'][0]['quantity'] = 3
        path.write_text(json.dumps(batch))
        with self.assertRaisesRegex(ValueError, '台账内容已修改'):
            self.handoff('reserve')

    def test_ledger_missing_never_resets(self):
        Path(self.config['handoff']['stateFile']).unlink()
        with self.assertRaisesRegex(ValueError, '台账缺失'):
            self.prepare(mode='live')

    def test_reserve_is_atomic_under_concurrency(self):
        self.prepare(mode='live')
        self.prepare('other', mode='live')
        raw = read_json(self.workspace / 'config.json')
        config = self.workspace / 'concurrent.json'
        config.write_text(json.dumps(raw))
        def reserve(name):
            return subprocess.run([sys.executable, str(SCRIPTS / 'orders.py'), 'handoff', '--config', str(config),
                                   '--batch', str(self.root / name / 'batch.json'), '--groups', 'smallFace',
                                   '--action', 'reserve'], capture_output=True, text=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, ['customer', 'other']))
        self.assertEqual(sorted(result.returncode for result in results), [0, 1])
        with closing(sqlite3.connect(self.config['handoff']['stateFile'])) as db:
            self.assertEqual(db.execute('SELECT COUNT(*),COUNT(DISTINCT batchId) FROM handoffs').fetchone(), (2, 1))

    def test_selected_group_preserves_other_sent_fingerprints(self):
        self.rows[3][HEADERS.index('SKUの元の価格')] = '0 JPY'
        self.source = self.source_file('mixed-regular-sample', self.rows)
        self.prepare(mode='live', groups='sample')
        self.handoff('reserve', 'sample')
        self.handoff('confirm', 'sample', reference='synthetic-sample-handoff')
        result = self.prepare('regular-next-day', mode='live', groups='doz', date='2026-11-10')
        self.assertEqual(result['handoffReviewRows'], 0)
        batch = read_json(self.root / 'regular-next-day/batch.json')
        self.assertTrue(any(row['orderId'] == ID(3) and row['skuId'] == SKU(3) for row in batch['rows']))

    def test_multi_sku_complete_quantities(self):
        self.prepare()
        result, issues = self.shipping([[ID(3), '000000000033', 'synthetic-carrier', SKU(3), 2],
                                        [ID(3), '000000000033', 'synthetic-carrier', SKU(4), 1]])
        self.assertEqual(result['uploadRows'], 2)
        self.assertFalse(any(row['orderId'] == ID(3) for row in issues))
        sheet = load_workbook(self.root / 'tracking/TikTok配送情報.xlsx').active
        self.assertEqual([sheet['D4'].value, sheet['D5'].value], [2, 1])
        self.assertEqual(sheet['F4'].value, '000000000033')
        self.assertEqual(sheet['A3'].value, 'Required')
        self.assertIsNone(sheet['A6'].value)

    def test_customer_unit_multi_sku_quantities(self):
        self.config['feedback']['doz']['quantityUnit'] = 'customer'
        self.prepare()
        result, issues = self.shipping([[ID(3), '0003', 'synthetic-carrier', SKU(3), 12],
                                        [ID(3), '0003', 'synthetic-carrier', SKU(4), 1]])
        self.assertEqual(result['uploadRows'], 2)
        self.assertFalse(any(row['orderId'] == ID(3) for row in issues))
        sheet = load_workbook(self.root / 'tracking/TikTok配送情報.xlsx').active
        self.assertEqual([sheet['D4'].value, sheet['D5'].value], [2, 1])

    def test_customer_unit_whole_order_uses_each_sku_factor(self):
        self.config['feedback']['doz']['quantityUnit'] = 'customer'
        self.prepare()
        result, _ = self.shipping([[ID(3), '0003', 'synthetic-carrier', None, 13, '已核实']])
        self.assertEqual(result['uploadRows'], 2)
        for quantity in [3, 12, 18]:
            with self.subTest(quantity=quantity):
                result, issues = self.shipping([[ID(3), '0003', 'synthetic-carrier', None, quantity, '已核实']],
                                               'wrong-customer-total-' + str(quantity))
                self.assertEqual(result['uploadRows'], 0)
                self.assertTrue(any('回传数量' in row['reason'] for row in issues))

    def test_customer_units_reject_partial_and_missing_quantity(self):
        self.config['feedback']['doz']['quantityUnit'] = 'customer'
        self.rows[7][HEADERS.index('セラーSKU')] = '00008'
        self.source = self.source_file('customer-units', self.rows)
        self.prepare()
        result, _ = self.shipping([[ID(6), '0006', 'synthetic-carrier', SKU(8), 6]])
        self.assertEqual(result['uploadRows'], 1)
        for quantity in [None, 1, 5, 7]:
            with self.subTest(quantity=quantity):
                result, issues = self.shipping([[ID(6), '0006', 'synthetic-carrier', SKU(8), quantity]],
                                               'partial-customer-units-' + str(quantity))
                self.assertEqual(result['uploadRows'], 0)
                self.assertTrue(any('数量' in row['reason'] for row in issues))

    def test_unknown_quantity_unit_rejected(self):
        self.config['feedback']['doz']['quantityUnit'] = 'guessed'
        self.prepare()
        with self.assertRaisesRegex(ValueError, 'quantityUnit'):
            self.shipping([[ID(3), '0003', 'synthetic-carrier', SKU(3), 2]])

    def test_multi_sku_requires_evidence(self):
        self.prepare()
        for name, details, expected in [
            ('order-only', [[ID(3), '0003', 'synthetic-carrier']], '缺少完整'),
            ('partial-sku', [[ID(3), '0003', 'synthetic-carrier', SKU(3), 2]], '缺少完整'),
            ('no-quantity', [[ID(3), '0003', 'synthetic-carrier', SKU(3)],
                             [ID(3), '0003', 'synthetic-carrier', SKU(4)]], '缺少多件商品'),
            ('partial-quantity', [[ID(3), '0003', 'synthetic-carrier', SKU(3), 1],
                                  [ID(3), '0003', 'synthetic-carrier', SKU(4), 1]], '回传数量'),
            ('split', [[ID(3), '0003', 'synthetic-carrier', SKU(3), 2],
                        [ID(3), '0004', 'synthetic-carrier', SKU(4), 1]], '多个快递单号'),
        ]:
            with self.subTest(name=name):
                result, issues = self.shipping(details, name)
                self.assertEqual(result['uploadRows'], 0)
                self.assertTrue(any(expected in row['reason'] for row in issues))

    def test_confirmed_whole_order(self):
        self.prepare()
        result, _ = self.shipping([[ID(3), '0003', 'synthetic-carrier', None, 3, '已核实']])
        self.assertEqual(result['uploadRows'], 2)

    def test_single_sku_multiple_units_requires_quantity_evidence(self):
        self.rows[7][HEADERS.index('数量')] = 2
        self.source = self.source_file('single-sku-multiple-units', self.rows)
        self.prepare()
        result, issues = self.shipping([[ID(6), '0006', 'synthetic-carrier']])
        self.assertEqual(result['uploadRows'], 0)
        self.assertTrue(any('缺少多件商品' in row['reason'] for row in issues))
        result, _ = self.shipping([[ID(6), '0006', 'synthetic-carrier', SKU(8), 2]], 'complete-single-sku')
        self.assertEqual(result['uploadRows'], 1)

    def test_mixed_fulfillment_held(self):
        self.prepare()
        result, issues = self.shipping([[ID(5), '0005', 'synthetic-carrier', None, None, '已核实']])
        self.assertEqual(result['uploadRows'], 0)
        self.assertTrue(any('跨履约方' in row['reason'] for row in issues))

    def test_stale_quantity_held(self):
        self.prepare()
        self.rows[2][HEADERS.index('数量')] = 1
        result, issues = self.shipping([[ID(3), '0003', 'synthetic-carrier', None, 3, '已核实']],
                                      current=self.source_file('less', self.rows))
        self.assertEqual(result['uploadRows'], 0)
        self.assertTrue(any('制作批次不一致' in row['reason'] for row in issues))

    def test_missing_carriers_reports_data_needed(self):
        self.prepare()
        self.config['carriers'] = {}
        result, issues = self.shipping([[ID(4), '0004', 'synthetic-carrier']])
        self.assertIsNone(result['file'])
        self.assertTrue(any('synthetic-carrier' in row['reason'] for row in issues))

    def test_feedback_conflict_invalidates_order(self):
        self.prepare()
        result, issues = self.shipping([[ID(4), '0004', 'synthetic-carrier'], [ID(4), None, 'synthetic-carrier']])
        self.assertEqual(result['uploadRows'], 0)
        self.assertTrue(any('缺少快递单号' in row['reason'] for row in issues))


if __name__ == '__main__':
    unittest.main()
