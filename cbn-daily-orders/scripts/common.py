"""Local workbook I/O and validation shared by the order and handoff commands."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from locks import exclusive_file


SKILL = Path(__file__).resolve().parent.parent
GROUPS = ('smallFace', 'papaya', 'doz', 'sample')
JST = timezone(timedelta(hours=9))


def text(value):
    return '' if value is None else str(value).strip()


def normalized(value):
    return unicodedata.normalize('NFKC', text(value))


def header_key(value):
    return re.sub(r'\s+', '', normalized(value)).casefold()


def read_json(path):
    with Path(path).open(encoding='utf-8') as stream:
        return json.load(stream)


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def digest(value):
    data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, separators=(',', ':'),
                                                            sort_keys=True, allow_nan=False).encode()
    return hashlib.sha256(data).hexdigest()


def identifier(value, label, digits=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f'{label} 为空或格式错误')
    if isinstance(value, (int, float)) and (not float(value).is_integer() or abs(value) >= 10 ** 15):
        raise ValueError(f'{label} 需要从原始文件按文本读取，Excel 数字可能已失真')
    result = normalized(value) if digits else text(value)
    if not result or (digits and not re.fullmatch('[0-9]+', result)):
        raise ValueError(f'{label} 应为完整编号文本')
    return result


def positive_int(value, label='数量'):
    number = normalized(value)
    if isinstance(value, bool) or not re.fullmatch('[0-9]+', number) or not 0 < int(number) <= 10 ** 9:
        raise ValueError(f'{label} 应为正整数')
    return int(number)


def price(value):
    number = re.sub(r'^(?:JPY|¥)\s*|\s*(?:JPY|円)$', '', normalized(value), flags=re.I)
    if not re.fullmatch(r'(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?', number):
        raise ValueError('SKUの元の価格 缺失或格式错误')
    try:
        amount = Decimal(number.replace(',', ''))
        if not amount.is_finite() or amount > 10 ** 12:
            raise ValueError('SKUの元の価格 超出范围')
        return int(amount) if amount == amount.to_integral() else float(amount)
    except InvalidOperation as error:
        raise ValueError('SKUの元の価格 格式错误') from error


def phone(value):
    if not isinstance(value, str):
        raise ValueError('電話番号 需要原始文本，避免丢失开头的 0')
    source = re.sub('[‐‑‒–—−]', '-', normalized(value))
    prefix = re.match(r'^(?:\(\s*\+81\s*\)|\+81)(?:\s*-\s*)?', source)
    domestic = source[prefix.end() if prefix else 0:].strip()
    domestic = re.sub(r'\(([0-9]+)\)', r'\1', domestic)
    if re.fullmatch(r'[0-9][0-9 -]*[0-9]', domestic):
        number = re.sub('[ -]', '', domestic)
        needs_zero = prefix is not None and not number.startswith('0')
        digits = '0' + number if needs_zero else number
        if re.fullmatch(r'0[1-9][0-9]{8,9}', digits):
            return digits if needs_zero else domestic
    raise ValueError('電話番号 应为日本国内号码（0 开头，10 或 11 位数字）')


def postal(value):
    result = normalized(identifier(value, '郵便番号'))
    if not re.fullmatch('[0-9]{3}-?[0-9]{4}', result):
        raise ValueError('郵便番号 应保留 7 位数字')
    return result


def business_date(value=None):
    if value is None:
        return datetime.now(JST).date().isoformat()
    if not re.fullmatch('[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        raise ValueError('日期应为 YYYY-MM-DD')
    return date.fromisoformat(value).isoformat()


class Table:
    def __init__(self, path, spec=None):
        spec = spec or {}
        path = Path(path)
        if path.suffix.lower() != '.xlsx':
            raise ValueError('请使用 .xlsx 工作簿')
        try:
            with ZipFile(path) as archive:
                if sum(entry.file_size for entry in archive.infolist()) > 150_000_000:
                    raise ValueError('工作簿解压体积超过 150 MB')
        except BadZipFile as error:
            raise ValueError('工作簿损坏，请重新下载完整的 .xlsx 原件') from error
        self.book = load_workbook(path, data_only=False, keep_links=False)
        name = spec.get('sheet')
        if name and name not in self.book.sheetnames:
            raise ValueError(f'工作表不存在（{name}）')
        self.sheet = self.book[name] if name else self.book.worksheets[0]
        self.header_row = spec.get('headerRow', 1)
        self.first = spec.get('firstDataRow', self.header_row + 1)
        if (type(self.header_row) is not int or type(self.first) is not int
                or not 1 <= self.header_row < self.first <= 1_048_576):
            raise ValueError('请核对 headerRow 和 firstDataRow')
        if self.sheet.max_row > 100_000 or self.sheet.max_column > 300:
            raise ValueError('工作簿超出单批 100000 行、300 列范围')
        self.headers = [cell.value for cell in self.sheet[self.header_row]]
        self.rows = [(index, [cell.value for cell in row]) for index, row in
                     enumerate(self.sheet.iter_rows(min_row=self.first), self.first)
                     if any(cell.value is not None and text(cell.value) for cell in row)]

    def column(self, labels, required=True):
        labels = [labels] if isinstance(labels, str) else labels
        for label in labels:
            matches = [i for i, value in enumerate(self.headers) if header_key(value) == header_key(label)]
            if len(matches) > 1:
                raise ValueError(f'表头重复（{label}）')
            if matches:
                return matches[0]
        if required:
            raise ValueError(f'缺少表头（{labels[0]}）')
        return None

    @staticmethod
    def at(row, column):
        return row[column] if column is not None and column < len(row) else None

    def value(self, row, labels, required=True):
        return self.at(row, self.column(labels, required))


def write_cell(cell, value, as_text=False):
    if as_text and value is not None:
        value = text(value)
    cell.value = value
    if isinstance(value, str):
        cell.data_type = 's'
    if as_text:
        cell.number_format = '@'


def load_config(path):
    filename = Path(path).expanduser().resolve()
    config = read_json(filename)
    base = filename.parent / config.get('pathBase', '.')
    for spec in config.get('templates', {}).values():
        if spec.get('file'):
            spec['file'] = str((base / spec['file']).resolve())
    settings = config.get('handoff', {})
    if settings.get('stateFile'):
        settings['stateFile'] = str((base / settings['stateFile']).resolve())
    return config


@contextmanager
def output_run(destination):
    output = Path(destination).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = output.parent / '.cbn-locks' / (digest(str(output).encode()) + '.lock')
    with exclusive_file(lock):
        if output.exists():
            raise ValueError('目标批次目录已存在，请核对结果或使用新目录')
        stage = Path(tempfile.mkdtemp(prefix='.cbn-', dir=output.parent))
        try:
            yield stage
            os.rename(stage, output)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
