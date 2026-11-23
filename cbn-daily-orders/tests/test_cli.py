"""Exercise the actual CLI and preserve clean JSON output and failure behavior."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_workflow import HEADERS, SCRIPTS, order, write_fixture


class CommandLine(unittest.TestCase):
    def test_complete_customer_command_sequence(self):
        with tempfile.TemporaryDirectory(prefix='cbn-cli-') as temporary:
            root = Path(temporary)
            workspace = root / '客户 订单'
            entry = SCRIPTS / 'orders.py'
            def call(command, *args):
                return subprocess.run([sys.executable, str(entry), command, *map(str, args)],
                                      check=False, capture_output=True, encoding='utf-8')
            result = call('init', '--workspace', workspace)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)['ordersReady'])
            source = write_fixture(root / '原始 订单.xlsx', [HEADERS, ['TikTok Shop 字段说明'],
                order(1, 1, '小顔 テスト'), order(2, 2, '青パパイヤ テスト'),
                order(3, 3, 'DOZ 水果茶'), order(4, 4, 'DOZ サンプル', amount=0)])
            args = ('--config', workspace / 'config.json', '--orders', source,
                    '--date', '2026-11-09', '--out', root / '完整批次')
            result = call('prepare', *args)
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout)
            self.assertEqual(summary['customerRows'], {'smallFace': 1, 'papaya': 1, 'doz': 1, 'sample': 1})
            repeated = call('prepare', *args)
            self.assertEqual(repeated.returncode, 1)
            self.assertIn('目录已存在', repeated.stderr)
            source.write_text('incomplete download', encoding='utf-8')
            damaged = call('prepare', *args[:-1], root / 'broken')
            self.assertEqual(damaged.returncode, 1)
            self.assertIn('工作簿损坏', damaged.stderr)
            self.assertNotIn('Traceback', damaged.stderr)


if __name__ == '__main__':
    unittest.main()
