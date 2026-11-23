"""Prepare shipment imports after matching explicit parcel evidence."""
from __future__ import annotations

from collections import defaultdict
import re

from common import Table, digest, identifier, normalized, output_run, positive_int, text, write_cell, write_json
from ledger import row_key
from prepare import read_orders


UPLOAD = {'orderId': '注文ID', 'skuId': 'SKU ID', 'product': '商品名', 'quantity': '数量',
          'carrier': '配送業者名', 'tracking': '追跡ID', 'receipt': '領収書ID'}


def feedback_rows(config, paths):
    records, issues = [], []
    for index, argument in enumerate(paths, 1):
        group, separator, filename = argument.partition('=')
        if not separator or group not in ('smallFace', 'papaya', 'doz'):
            raise ValueError('反馈参数应使用 smallFace、papaya 或 doz=本地文件.xlsx')
        spec = config.get('feedback', {}).get(group, {})
        quantity_unit = spec.get('quantityUnit', 'platform')
        if quantity_unit not in ('platform', 'customer'):
            raise ValueError(f'{group} 的 quantityUnit 只可为 platform 或 customer')
        if quantity_unit == 'customer' and not spec.get('quantity'):
            raise ValueError(f'{group} 的 customer quantityUnit 需要配置已发数量列 quantity')
        table = Table(filename, spec)
        defaults = {'orderId': '客户订单编号' if group == 'doz' else '注文ID',
                    'tracking': '快递单号' if group == 'doz' else '伝票番号',
                    'carrier': '快递公司' if group == 'doz' else '配送業者名'}
        columns = {field: table.column(spec.get(field, label), field != 'carrier' or not spec.get('defaultCarrier'))
                   for field, label in defaults.items()}
        columns['skuId'] = table.column(spec.get('skuId', 'SKU ID'), bool(spec.get('skuId')))
        columns['quantity'] = table.column(spec['quantity']) if spec.get('quantity') else None
        whole = spec.get('wholeOrder')
        if whole is not None and (not text(whole.get('header')) or not text(whole.get('value'))):
            raise ValueError(f'{group} 的整单同包配置需要列名和确认值')
        whole_col = table.column(whole['header']) if whole else None
        for number, cells in table.rows:
            order_id = None
            try:
                order_id = identifier(table.at(cells, columns['orderId']), '客户订单编号', True)
                raw_tracking = table.at(cells, columns['tracking'])
                if not text(raw_tracking):
                    raise ValueError('缺少快递单号')
                tracking = identifier(raw_tracking, '快递单号')
                if not re.fullmatch('[A-Za-z0-9-]+', tracking):
                    raise ValueError('快递单号格式错误')
                raw_carrier = normalized(table.at(cells, columns['carrier']) or spec.get('defaultCarrier'))
                carrier = config.get('carriers', {}).get(raw_carrier)
                if not isinstance(carrier, str) or not carrier.strip():
                    raise ValueError(f'配送业者缺少已核实的名称映射（{raw_carrier or "回传为空"}）')
                sku = table.at(cells, columns['skuId'])
                sku_id = identifier(sku, 'SKU ID', True) if text(sku) else None
                count = table.at(cells, columns['quantity'])
                count = positive_int(count, '回传数量') if text(count) else None
                whole_package = bool(whole and normalized(table.at(cells, whole_col)) == normalized(whole['value']))
                if whole_package and sku_id:
                    raise ValueError('整单同包确认行应使用订单 ID 范围')
                records.append({'group': group, 'orderId': order_id, 'skuId': sku_id, 'carrier': carrier.strip(),
                                'tracking': tracking, 'shippedQuantity': count, 'wholePackage': whole_package,
                                'quantityUnit': quantity_unit})
            except ValueError as error:
                issues.append({'feedbackFile': index, 'row': number, 'orderId': order_id, 'reason': str(error)})
    return records, issues


def by_order(rows):
    result = defaultdict(list)
    for row in rows:
        result[row['orderId']].append(row)
    return result


def validate_batch(batch, config):
    rows = batch.get('rows')
    if not isinstance(rows, list) or not isinstance(batch.get('manualOrders'), list):
        raise ValueError('需使用 prepare 实际生成的 batch.json')
    keys = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('批次明细格式错误')
        for field in ('orderId', 'skuId'):
            if not isinstance(row.get(field), str) or not re.fullmatch('[0-9]+', row[field]):
                raise ValueError('批次明细编号格式错误')
        if row_key(row) in keys or row.get('group') not in ('smallFace', 'papaya', 'doz'):
            raise ValueError('批次明细重复或客户错误')
        keys.add(row_key(row))
        count = positive_int(row.get('quantity'))
        factor = row.get('quantityFactor')
        if (type(factor) is not int or factor not in (1, 6) or factor == 6 and row['group'] != 'doz'
                or row.get('customerQuantity') != count * factor):
            raise ValueError('批次数量换算不一致')
    manifest = batch.get('handoff')
    if manifest:
        if manifest.get('shop') != config.get('handoff', {}).get('shop'):
            raise ValueError('回填批次与当前店铺不符')
        if manifest.get('rowsHash') and manifest['rowsHash'] != digest(rows):
            raise ValueError('批次台账内容已修改')


def match_shipments(batch, current, records, errors):
    pending_orders, returned_orders, booked_orders = by_order(current), by_order(records), by_order(batch['rows'])
    issues, upload = list(errors), []
    invalid = {row['orderId'] for row in errors}
    for order_id in returned_orders.keys() - booked_orders.keys():
        issues.append({'orderId': order_id, 'reason': '反馈订单不在该制作批次'})
    for order_id, booked in booked_orders.items():
        pending, returned = pending_orders[order_id], returned_orders[order_id]
        reason = match_order(order_id, booked, pending, returned, batch['manualOrders'], invalid)
        if reason:
            issues.append({'orderId': order_id, 'reason': reason})
        elif order_id not in invalid:
            first = returned[0]
            upload.extend({**row, 'carrier': first['carrier'], 'tracking': first['tracking']} for row in pending)
    return upload, issues


def match_order(order_id, booked, pending, returned, manual_orders, invalid):
    if not pending:
        return '当前待发货列表中无该订单'
    if order_id in manual_orders or len({row['group'] for row in pending}) > 1:
        return '同一订单跨履约方，需要人工拆单'
    if order_id in invalid:
        return None
    if not returned:
        return '等待物流反馈'
    live = {row['skuId']: row for row in pending}
    if len(pending) != len(booked) or len(live) != len(pending) or any(
            row['skuId'] not in live or (live[row['skuId']]['group'], live[row['skuId']]['quantity'])
            != (row['group'], row['quantity']) for row in booked):
        return '待发货的 SKU 或数量与制作批次不一致'
    if any(row['group'] != booked[0]['group'] or row['skuId'] and row['skuId'] not in live for row in returned):
        return '反馈客户或 SKU 与制作批次不符'
    packages = {(row['carrier'], row['tracking']) for row in returned}
    if len(packages) != 1:
        return '同一订单收到多个快递单号或配送业者，需要人工复核'
    whole = any(row['wholePackage'] for row in returned)
    covered = {row['skuId'] for row in returned if row['skuId']}
    if len(pending) > 1 and covered != set(live) and not whole:
        return '多 SKU 订单缺少完整的逐 SKU 反馈或整单同包确认'
    snapshot = {row['skuId']: row for row in booked}
    def expected_quantity(record):
        field = 'customerQuantity' if record.get('quantityUnit') == 'customer' else 'quantity'
        return snapshot[record['skuId']][field] if record['skuId'] else sum(row[field] for row in booked)
    if any(row['shippedQuantity'] is not None and row['shippedQuantity'] != expected_quantity(row)
           for row in returned):
        return '回传数量与制作批次及当前待发货数量不一致，请核对平台或客户单位'
    if not whole and any(row.get('quantityUnit') == 'customer' and row['shippedQuantity'] is None
                         for row in returned):
        return '客户单位回传缺少已发数量或整单同包确认'
    if not whole and any(row['quantity'] > 1 and not any(
            (record['skuId'] == row['skuId'] or len(pending) == 1 and not record['skuId'])
            and record['shippedQuantity'] == expected_quantity(record)
            for record in returned) for row in pending):
        return '订单缺少多件商品的已发数量或整单同包确认'
    return None


def fill_upload(path, spec, rows, destination):
    spec = dict(spec or {})
    spec.setdefault('headerRow', 2)
    spec.setdefault('firstDataRow', spec['headerRow'] + 2)
    table = Table(path, spec)
    aliases = spec.get('headers', {})
    columns = {field: table.column(aliases.get(field, label)) for field, label in UPLOAD.items()}
    mapped = set(columns.values())
    if any(text(value) and i not in mapped for _, cells in table.rows for i, value in enumerate(cells)):
        raise ValueError('上传模板数据区包含未映射列，请核对新模板')
    for row in table.sheet.iter_rows(min_row=table.first):
        for i in mapped:
            row[i].value = None
    for index, row in enumerate(rows, table.first):
        for field, column in columns.items():
            write_cell(table.sheet.cell(index, column + 1), row.get(field), field != 'quantity')
    table.book.save(destination)


def tracking(config, args):
    from common import read_json
    batch = read_json(args.batch)
    validate_batch(batch, config)
    _, current = read_orders(args.current, config.get('orders'), args.source_scope)
    records, issues = feedback_rows(config, args.feedback)
    upload, issues = match_shipments(batch, current, records, issues)
    with output_run(args.out) as folder:
        filename = 'TikTok配送情報.xlsx' if upload else None
        if filename:
            fill_upload(args.upload_template, config.get('upload'), upload, folder / filename)
        summary = {'sourceDate': batch['date'], 'uploadRows': len(upload), 'issues': len(issues), 'file': filename}
        write_json(folder / 'issues.json', issues)
        write_json(folder / 'summary.json', summary)
    return summary
