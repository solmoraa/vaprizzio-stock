"""
Núcleo de negocio de Vaprizzio.

Reglas principales:

1. Venta manual:
   - registra la venta en Google Sheets;
   - descuenta stock de Google Sheets;
   - descuenta stock de Tiendanube.

2. Venta de Tiendanube:
   - sigue siendo procesada por los webhooks existentes;
   - Tiendanube descuenta su stock;
   - el webhook registra la venta y sincroniza Productos.

3. Cancelación manual:
   - repone stock en Tiendanube;
   - repone stock en Productos;
   - elimina las filas de la venta mensual.

4. Cancelación de Tiendanube:
   - sigue siendo procesada por order_sync_v2;
   - Tiendanube repone su stock;
   - el webhook sincroniza Productos y elimina la venta.
"""

from .products import buscar_producto, consultar_stock
from .sales import (
    cancelar_venta_manual,
    consultar_venta,
    registrar_venta_manual,
)
from .service import ejecutar_accion

__all__ = [
    "buscar_producto",
    "consultar_stock",
    "registrar_venta_manual",
    "cancelar_venta_manual",
    "consultar_venta",
    "ejecutar_accion",
]
