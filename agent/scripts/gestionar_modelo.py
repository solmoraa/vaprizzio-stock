#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import re
import sys
from pathlib import Path
from typing import Any

PROJECT = Path('/home/openclaw/.openclaw/workspace/vaprizziobot')
TIENDANUBE = PROJECT / 'tiendanube'
if str(TIENDANUBE) not in sys.path:
    sys.path.insert(0, str(TIENDANUBE))

import gspread  # noqa: E402
from app.sync_core import (  # noqa: E402
    SyncError,
    api_request,
    append_variant_to_sheet,
    localized_text,
    normalize,
    read_products,
)
from app.business.costs import actualizar_costo_usdt_marca  # noqa: E402

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
IMAGE_MIMES = {'image/jpeg', 'image/png', 'image/gif', 'image/webp'}
MAX_IMAGE_SIZE = 10 * 1024 * 1024


def output(ok: bool, message: str, **data: Any) -> None:
    print(json.dumps({'ok': ok, 'mensaje': message, **data}, ensure_ascii=False, indent=2, default=str))


def clean(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '').strip())


def exact_product(model: str) -> dict[str, Any] | None:
    """Busca exclusivamente por nombre exacto; jamás usa coincidencia parcial."""
    target = normalize(model)
    page = 1
    while True:
        products = api_request('GET', 'products', params={'page': page, 'per_page': 100})
        if not products:
            return None
        for product in products:
            name = localized_text(product.get('name')).strip()
            if normalize(name) == target:
                return product
        if len(products) < 100:
            return None
        page += 1


def validate_image(raw_path: Any) -> Path:
    text = clean(raw_path).strip('\"\'')
    if text.startswith('file://'):
        text = text[7:]
    if not text:
        raise SyncError('Para crear un modelo nuevo tenés que adjuntar una imagen.')
    path = Path(text).expanduser().resolve()
    if not path.is_file():
        raise SyncError(f'No se encontró la imagen adjunta: {path}')
    if path.suffix.lower() not in IMAGE_EXTENSIONS:
        raise SyncError('La imagen debe ser JPG, PNG, GIF o WEBP.')
    mime, _ = mimetypes.guess_type(str(path))
    if mime not in IMAGE_MIMES:
        raise SyncError('El archivo adjunto no parece una imagen válida.')
    size = path.stat().st_size
    if size <= 0 or size >= MAX_IMAGE_SIZE:
        raise SyncError('La imagen debe pesar entre 1 byte y menos de 10 MB.')
    return path


def upload_image(product_id: str, model: str, path: Path) -> dict[str, Any]:
    response = api_request(
        'POST',
        f'products/{product_id}/images',
        payload={
            'filename': path.name,
            'position': 1,
            'attachment': base64.b64encode(path.read_bytes()).decode('ascii'),
            'alt': model,
        },
    )
    if not response.get('id'):
        raise SyncError('Tiendanube no devolvió el ID de la imagen.')
    return {'id': str(response['id']), 'url': str(response.get('src') or ''), 'position': response.get('position', 1)}


def parse_flavors(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get('sabores')
    if raw is None:
        raw = payload.get('sabor')

    if isinstance(raw, str):
        stock_default = int(payload.get('stock', 0))
        items = [
            {'sabor': part.strip(), 'stock': stock_default}
            for part in re.split(r'\s*[|,;]\s*', raw)
            if part.strip()
        ]
    elif isinstance(raw, list):
        items = []
        stock_default = int(payload.get('stock', 0))
        for value in raw:
            if isinstance(value, str):
                items.append({'sabor': value, 'stock': stock_default})
            elif isinstance(value, dict):
                items.append({
                    'sabor': value.get('sabor') or value.get('nombre'),
                    'stock': value.get('stock', stock_default),
                    'sku': value.get('sku', ''),
                })
    else:
        items = []

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        flavor = clean(item.get('sabor'))
        if not flavor:
            continue
        key = normalize(flavor)
        if key in seen:
            continue
        stock = int(item.get('stock', 0))
        if stock < 0:
            raise SyncError(f'El stock de {flavor} no puede ser negativo.')
        result.append({'sabor': flavor, 'stock': stock, 'sku': clean(item.get('sku'))})
        seen.add(key)
    if not result:
        raise SyncError('Tenés que indicar al menos un sabor con su stock.')
    return result


def create_model(payload: dict[str, Any]) -> dict[str, Any]:
    model = clean(payload.get('modelo') or payload.get('marca'))
    if not model:
        raise SyncError('Falta el nombre del modelo.')
    if exact_product(model) is not None:
        raise SyncError(f'El modelo {model!r} ya existe. Para sumar sabores usá agregar variante, no crear modelo.')

    flavors = parse_flavors(payload)
    price = float(payload.get('precio') or payload.get('precio_venta') or 0)
    usdt_cost = float(payload.get('costo_usdt') or payload.get('costo') or 0)
    if price <= 0:
        raise SyncError('El precio de venta debe ser mayor que cero.')
    if usdt_cost <= 0:
        raise SyncError('El costo USDT debe ser mayor que cero.')
    image_path = validate_image(payload.get('imagen') or payload.get('image'))
    published = bool(payload.get('publicado', True))

    variants = []
    for item in flavors:
        variant = {
            'values': [{'es': item['sabor']}],
            'price': str(price),
            'stock': item['stock'],
        }
        if item.get('sku'):
            variant['sku'] = item['sku']
        variants.append(variant)

    created_product_id = ''
    sheet_rows: list[int] = []
    try:
        product = api_request('POST', 'products', payload={
            'name': {'es': model},
            'published': published,
            'attributes': [{'es': 'Sabor'}],
            'variants': variants,
        })
        created_product_id = str(product.get('id') or '')
        if not created_product_id:
            raise SyncError('Tiendanube no devolvió el Product ID del modelo creado.')

        remote_variants = product.get('variants') or []
        if len(remote_variants) != len(flavors):
            raise SyncError('Tiendanube no creó todos los sabores solicitados.')

        image = upload_image(created_product_id, model, image_path)

        synced = []
        for variant in remote_variants:
            row_data = append_variant_to_sheet(
                product_name=model,
                product_id=created_product_id,
                variant=variant,
            )
            synced.append(row_data)

        cost_result = actualizar_costo_usdt_marca(model, usdt_cost)

        return {
            'ok': True,
            'mensaje': 'Modelo nuevo creado con imagen, sabores, stock y precio; costo USDT aplicado en Google Sheets.',
            'modelo': model,
            'product_id': created_product_id,
            'imagen': image,
            'sabores': [{'sabor': f['sabor'], 'stock': f['stock']} for f in flavors],
            'precio_venta': price,
            'costo_usdt': usdt_cost,
            'costo': cost_result,
            'tiendanube': True,
            'google_sheets': True,
        }
    except Exception:
        # Rollback conservador: si se creó el modelo remoto pero falló algo posterior,
        # se elimina para evitar modelos a medio crear.
        if created_product_id:
            try:
                api_request('DELETE', f'products/{created_product_id}')
            except Exception:
                pass
        try:
            worksheet, _, _, products = read_products()
            rows = sorted([p.row for p in products if str(p.product_id) == created_product_id], reverse=True)
            for row in rows:
                worksheet.delete_rows(row)
        except Exception:
            pass
        raise


def _find_aux_cost_table(values: list[list[str]]) -> tuple[int, dict[str, int]] | None:
    required = {normalize('Marca'), normalize('Costo USDT')}
    for row_number, row in enumerate(values, start=1):
        columns: dict[str, int] = {}
        for idx, value in enumerate(row, start=1):
            key = normalize(value)
            if key:
                columns[key] = idx
        if required.issubset(columns):
            return row_number, columns
    return None


def clear_cost_row_for_brand(worksheet: gspread.Worksheet, model: str) -> int:
    values = worksheet.get_all_values()
    table = _find_aux_cost_table(values)
    if table is None:
        return 0
    header, columns = table
    brand_col = columns[normalize('Marca')]
    end_col = max(columns.values())
    cleared = 0
    for row_number, row in enumerate(values[header:], start=header + 1):
        value = row[brand_col - 1] if brand_col <= len(row) else ''
        if normalize(value) == normalize(model):
            start = gspread.utils.rowcol_to_a1(row_number, brand_col)
            end = gspread.utils.rowcol_to_a1(row_number, end_col)
            worksheet.update(f'{start}:{end}', [[''] * (end_col - brand_col + 1)], value_input_option='USER_ENTERED')
            cleared += 1
    return cleared


def delete_model(payload: dict[str, Any]) -> dict[str, Any]:
    model = clean(payload.get('modelo') or payload.get('marca'))
    if not model:
        raise SyncError('Falta el nombre exacto del modelo a eliminar.')
    product = exact_product(model)
    if product is None:
        raise SyncError(f'No existe un modelo llamado exactamente {model!r} en Tiendanube.')
    product_id = str(product['id'])

    worksheet, _, _, products = read_products()
    rows = sorted([p.row for p in products if str(p.product_id) == product_id], reverse=True)

    api_request('DELETE', f'products/{product_id}')
    for row in rows:
        worksheet.delete_rows(row)
    cost_rows = clear_cost_row_for_brand(worksheet, model)

    return {
        'ok': True,
        'mensaje': 'Modelo eliminado completamente de Tiendanube, Productos y tabla de costos USDT.',
        'modelo': model,
        'product_id': product_id,
        'filas_productos_eliminadas': len(rows),
        'filas_costos_eliminadas': cost_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('accion', choices=['crear-modelo', 'eliminar-modelo'])
    parser.add_argument('--json', required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(args.json)
        result = create_model(payload) if args.accion == 'crear-modelo' else delete_model(payload)
        message = result.pop('mensaje')
        result.pop('ok', None)
        output(True, message, **result)
        return 0
    except Exception as exc:
        output(False, str(exc), tipo_error=type(exc).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
