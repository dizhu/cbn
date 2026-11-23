"""Verify the shipped skill has its templates and no customer order rows."""
import json
from pathlib import Path
import re
import unittest

from openpyxl import load_workbook

SKILL = Path(__file__).resolve().parents[1]


class Package(unittest.TestCase):
    def test_assets_are_blank_and_metadata_is_clean(self):
        for filename in ('papaya-orders.xlsx', 'doz-orders.xlsx', 'doz-samples.xlsx'):
            book = load_workbook(SKILL / 'assets' / filename)
            with self.subTest(filename=filename):
                sheet = book.worksheets[0]
                self.assertFalse(any(cell.value is not None for row in sheet.iter_rows(min_row=2) for cell in row))
                self.assertIn(book.properties.creator, (None, 'openpyxl'))
                self.assertFalse(book.properties.lastModifiedBy)
                self.assertFalse(any(cell.comment or cell.hyperlink for item in book for row in item for cell in row))

    def test_defaults_include_all_four_outputs(self):
        config = json.loads((SKILL / 'assets/config.default.json').read_text(encoding='utf-8'))
        self.assertEqual(set(config['templates']), {'smallFace', 'papaya', 'doz', 'sample'})
        for spec in config['templates'].values():
            if 'file' in spec:
                self.assertTrue((SKILL / 'assets' / Path(spec['file']).name).is_file())
        self.assertEqual(config['carriers'], {})

    def test_markdown_resource_links_exist(self):
        for document in [SKILL / 'SKILL.md', *sorted((SKILL / 'references').glob('*.md'))]:
            for path in re.findall(r'\]\(([^)]+)\)', document.read_text(encoding='utf-8')):
                if not path.startswith(('http:', 'https:', '#')):
                    self.assertTrue((document.parent / path).is_file(), f'{document.name}: {path}')


if __name__ == '__main__':
    unittest.main()
