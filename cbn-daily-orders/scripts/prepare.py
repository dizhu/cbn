"""Create the four customer workbooks from a TikTok Japan export."""
from __future__ import annotations

from collections import defaultdict
from copy import copy
from datetime import date
from pathlib import Path
import re
from uuid import uuid4

from openpyxl.utils import column_index_from_string

from common import (GROUPS, Table, business_date, digest, header_key, identifier, normalized,
                    output_run, phone, positive_int, postal, price, text, write_cell, write_json)
from ledger import output_group, previous_handoffs, row_key


CORE = {'orderId': '注文ID', 'skuId': 'SKU ID', 'product': '商品名', 'quantity': '数量'}
CUSTOMER = {'sellerSku': 'セラーSKU', 'unitPrice': 'SKUの元の価格', 'recipient': '受取人',
            'phone': '電話番号', 'postalCode': '郵便番号', 'prefecture': '都道府県',
            'city': '市区町村', 'town': '町名', 'address1': '詳細住所1', 'address2': '詳細住所2'}
PATTERNS = {'smallFace': r'小[顔颜]', 'papaya': '青パパ', 'doz': 'DOZ'}


def read_orders(path, spec=None, scope=None):
    spec = dict(spec or {})
    spec.setdefault('firstDataRow', spec.get('headerRow', 1) + 2)
    table = Table(path, spec)
    aliases = spec.get('headers', {})
    cols = {field: table.column(aliases.get(field, label)) for field, label in CORE.items()}
    status = table.column(aliases['status']) if 'status' in aliases else table.column(['注文のサブ状況', '注文状況'], False)
    if status is None and scope != 'to-ship':
        raise ValueError('订单表无状态列。核对来源是待发货筛选导出后，使用 --source-scope to-ship')
    allowed = {normalized(value) for value in spec.get('pendingStatuses', ['発送待ち', '未発送', '待发货'])}
    rows = []
    keys = set()
    for number, cells in table.rows:
        try:
            if status is not None and normalized(cells[status]) not in allowed:
                raise ValueError('注文状況 不在待发货范围')
            product = text(cells[cols['product']])
            if not product:
                raise ValueError('商品名 为空')
            groups = [group for group, pattern in PATTERNS.items() if re.search(pattern, normalized(product), re.I)]
            if len(groups) > 1:
                raise ValueError('商品名 同时匹配多个客户')
            row = {'orderId': identifier(cells[cols['orderId']], '注文ID', True),
                   'skuId': identifier(cells[cols['skuId']], 'SKU ID', True),
                   'quantity': positive_int(cells[cols['quantity']]), 'product': product,
                   'group': groups[0] if groups else None, 'sourceRow': number, 'cells': cells}
            if row_key(row) in keys:
                raise ValueError('订单与 SKU 重复，请先核对')
            keys.add(row_key(row))
            rows.append(row)
        except ValueError as error:
            raise ValueError(f'TikTok 订单第 {number} 行，{error}') from error
    return table, rows


def customer_order(row, table, aliases):
    row = dict(row, quantityFactor=1, customerQuantity=row['quantity'], sample=False)
    def get(field, required=True):
        return table.value(row['cells'], aliases.get(field, CUSTOMER[field]), required)
    try:
        row['phone'] = phone(get('phone'))
        row['recipient'] = text(get('recipient'))
        row['postalCode'] = postal(get('postalCode'))
        parts = [text(get(field, field != 'address2')) for field in ['prefecture', 'city', 'town', 'address1', 'address2']]
        if not row['recipient'] or not parts[0] or not parts[1] or not any(parts[2:]):
            raise ValueError('收件人或地址不完整')
        if any(re.search(r'[*＊●•]|<redacted>|\bmasked\b', value, re.I)
               for value in [row['recipient'], *parts]):
            raise ValueError('收件资料包含遮蔽内容，请使用完整的订单导出')
        row['address'] = ''.join(parts)
        if row['group'] == 'doz':
            row['sellerSku'] = identifier(get('sellerSku'), 'セラーSKU')
            row['unitPrice'] = price(get('unitPrice'))
            row['sample'] = row['unitPrice'] == 0
            if re.match('[A-Za-z]', normalized(row['address'])):
                raise ValueError('模板要求收件地址首字符使用非英文')
            row['quantityFactor'] = 6 if re.fullmatch('[0-9]+', normalized(row['sellerSku'])) else 1
            row['customerQuantity'] = row['quantity'] * row['quantityFactor']
        return row
    except ValueError as error:
        raise ValueError(f'{row["group"]} 订单第 {row["sourceRow"]} 行，{error}') from error


def template_spec(config, group):
    spec = config.get('templates', {}).get(group)
    if not spec:
        raise ValueError(f'缺少 {group} 的客户 Excel 模板配置')
    spec = dict(spec)
    if spec.get('reuse'):
        shared = config['templates'].get(spec['reuse'], {})
        if not shared.get('file') or shared.get('reuse'):
            raise ValueError(f'{group} 需要引用有实际文件的模板')
        spec = {**shared, **spec}
    return spec


def mapped_value(mapping, row, source, day):
    if sum(key in mapping for key in ('source', 'field', 'value')) != 1:
        raise ValueError('模板每列需要指定一个取值方式')
    if 'source' in mapping:
        value = source.value(row['cells'], mapping['source'], not mapping.get('optionalSource'))
    elif mapping.get('field') == 'date':
        value = date.fromisoformat(day)
    elif 'field' in mapping:
        field = mapping['field']
        field = 'customerQuantity' if field == 'quantity' else field
        if field not in row:
            raise ValueError(f'转换字段不存在（{field}）')
        value = row[field]
    else:
        value = mapping['value']
    if mapping.get('required') and not text(value):
        raise ValueError('存在必填空值')
    is_text = mapping.get('text') or mapping.get('field') not in (None, 'quantity', 'unitPrice', 'date')
    if is_text and text(value):
        value = identifier(value, '客户模板字段')
    return value, is_text


def fingerprint(row, spec, table):
    values = {col: mapped_value(mapping, row, table, '2000-01-01')[0]
              for col, mapping in spec.get('columns', {}).items() if mapping.get('field') != 'date'}
    # The handoff signature covers every transmitted column and canonical contact/quantity fields.
    return digest({'version': 2, 'group': output_group(row), 'product': row['product'], 'quantity': row['quantity'],
                   'phone': re.sub('[^0-9]', '', row['phone']), 'recipient': row['recipient'],
                   'postal': row['postalCode'], 'address': row['address'], 'columns': values})


def validate_template(spec):
    template = Table(spec['file'], spec)
    example = spec.get('exampleRow')
    if example is not None and example != template.first:
        raise ValueError('示例行必须位于数据区首行')
    if any(number != example for number, _ in template.rows):
        raise ValueError('客户模板的数据区有历史记录')
    name = spec.get('name', '')
    if not name or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((' ', '.')):
        raise ValueError('客户模板文件名无效')
    if not spec.get('columns'):
        raise ValueError(f'{name} 缺少模板字段映射')
    fields = {'orderId', 'skuId', 'product', 'quantity', 'recipient', 'phone', 'postalCode',
              'address', 'sellerSku', 'unitPrice', 'date'}
    for letters, mapping in spec['columns'].items():
        if not re.fullmatch('[A-Z]{1,3}', letters) or column_index_from_string(letters) > 16384:
            raise ValueError('模板列无效')
        if sum(key in mapping for key in ('source', 'field', 'value')) != 1:
            raise ValueError('模板每列需要指定一个取值方式')
        if 'field' in mapping and mapping['field'] not in fields:
            raise ValueError(f'转换字段不存在（{mapping["field"]}）')
        header = template.sheet.cell(template.header_row, column_index_from_string(letters)).value
        if 'header' not in mapping or header_key(header) != header_key(mapping['header']):
            raise ValueError(f'{name} {letters} 列表头与映射不符')
        if mapping.get('newHeader') and text(header):
            raise ValueError('附加列表头已有内容')
    return template


def fill_template(spec, rows, source, folder, day):
    template = validate_template(spec)
    if spec.get('exampleRow') is not None:
        for cell in template.sheet[spec['exampleRow']]:
            cell.value = None
    name = spec['name']
    sheet = template.sheet
    for letters, mapping in spec['columns'].items():
        column = column_index_from_string(letters)
        if not re.fullmatch('[A-Z]{1,3}', letters) or column > 16384:
            raise ValueError('模板列无效')
        original = sheet.cell(template.header_row, column)
        if 'header' not in mapping or header_key(original.value) != header_key(mapping['header']):
            raise ValueError(f'{name} {letters} 列表头与映射不符')
        if mapping.get('newHeader'):
            if text(original.value):
                raise ValueError('附加列表头已有内容')
            write_cell(original, mapping['newHeader'], True)
        if mapping.get('width'):
            # openpyxl retains column group intervals, split a group before changing one column.
            for key, dimension in list(sheet.column_dimensions.items()):
                if dimension.min <= column <= dimension.max and dimension.min != dimension.max:
                    del sheet.column_dimensions[key]
                    from openpyxl.utils import get_column_letter
                    for first, last in [(dimension.min, column - 1), (column + 1, dimension.max)]:
                        if first <= last:
                            fragment = copy(dimension)
                            fragment.min, fragment.max = first, last
                            sheet.column_dimensions[get_column_letter(first)] = fragment
            sheet.column_dimensions[letters].width = mapping['width']
        for index, row in enumerate(rows, template.first):
            cell = sheet.cell(index, column)
            if index > template.first and cell.style_id == 0:
                cell._style = copy(sheet.cell(template.first, column)._style)
            value, as_text = mapped_value(mapping, row, source, day)
            write_cell(cell, value, as_text)
            if mapping.get('field') == 'date':
                cell.number_format = 'yyyy/mm/dd'
    if spec.get('filterEndColumn'):
        sheet.auto_filter.ref = f'A{template.header_row}:{spec["filterEndColumn"]}{template.first + len(rows) - 1}'
    filename = f'{name}-{day}.xlsx'
    if (folder / filename).exists():
        raise ValueError('客户文件名重复')
    template.book.save(folder / filename)
    return filename


def prepare(config, args):
    day = business_date(args.date)
    enabled = args.groups.split(',') if args.groups else list(GROUPS)
    if len(enabled) != len(set(enabled)) or not set(enabled) <= set(GROUPS):
        raise ValueError('请核对 --groups')
    mode = args.mode or 'live'
    previous = previous_handoffs(config.get('handoff', {})) if mode == 'live' else {}
    table, all_rows = read_orders(args.orders, config.get('orders'), args.source_scope)
    aliases = config.get('orders', {}).get('headers', {})
    selected = [customer_order(row, table, aliases) for row in all_rows if row['group']]
    specs = {group: template_spec(config, group) for group in enabled
             if any(output_group(row) == group for row in selected)}
    held = {}
    for row in selected:
        if output_group(row) in specs:
            row['fingerprint'] = fingerprint(row, specs[output_group(row)], table)
        saved = previous.get(row_key(row))
        if saved and saved['fingerprint'] != row.get('fingerprint'):
            held[row['orderId']] = ('已登记订单内容有变化', saved)
        elif saved and saved['status'] == 'reserved' and row['orderId'] not in held:
            held[row['orderId']] = ('此前交接结果待确认', saved)
    output_rows = {group: [] for group in GROUPS}
    issues, pending, eligible = [], defaultdict(int), []
    skipped = 0
    for row in selected:
        group = output_group(row)
        if group not in enabled:
            pending[group] += 1
        elif row['orderId'] in held:
            reason, saved = held[row['orderId']]
            issues.append({'orderId': row['orderId'], 'skuId': row['skuId'], 'sourceRow': row['sourceRow'],
                           'reason': reason, 'previousBatch': saved['batchId'], 'previousDate': saved['batchDate']})
        elif row_key(row) in previous:
            skipped += 1
        else:
            eligible.append(row)
            output_rows[group].append(row)
    per_order = defaultdict(set)
    for row in all_rows:
        per_order[row['orderId']].add(row['group'])
    manual = [order for order, groups in per_order.items() if len(groups) > 1 and groups != {None}]
    with output_run(args.out) as folder:
        files = []
        manifest = {'id': str(uuid4()), 'shop': config['handoff']['shop'], 'files': {}} if mode == 'live' else None
        for group, rows in output_rows.items():
            if rows:
                filename = fill_template(specs[group], rows, table, folder, day)
                files.append(filename)
                if manifest:
                    manifest['files'][group] = {'name': filename, 'hash': digest((folder / filename).read_bytes())}
        fields = ('orderId', 'skuId', 'group', 'quantity', 'customerQuantity', 'quantityFactor', 'sample', 'fingerprint')
        batch_rows = [{key: row[key] for key in fields if key in row} for row in eligible]
        if manifest:
            manifest['rowsHash'] = digest(batch_rows)
        batch = {'date': day, 'rows': batch_rows, 'manualOrders': manual, 'sourceHash': digest(Path(args.orders).read_bytes())}
        if manifest:
            batch['handoff'] = manifest
        summary = {'date': day, 'mode': mode, 'totalRows': len(all_rows),
                   'customerRows': {key: len(value) for key, value in output_rows.items()},
                   'otherRows': len(all_rows) - len(selected), 'manualOrders': len(manual), 'files': files}
        if pending:
            summary['unpreparedRows'] = dict(pending)
        if mode == 'live':
            summary.update(alreadyHandedOffRows=skipped, handoffReviewRows=len(issues))
        if issues:
            write_json(folder / 'handoff-issues.json', issues)
        write_json(folder / 'batch.json', batch)
        write_json(folder / 'summary.json', summary)
    return summary
