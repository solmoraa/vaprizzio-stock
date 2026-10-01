#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT = Path(
    "/home/openclaw/.openclaw/workspace/vaprizziobot"
)
TIENDANUBE = PROJECT / "tiendanube"

if str(TIENDANUBE) not in sys.path:
    sys.path.insert(0, str(TIENDANUBE))


from app.business.common import BusinessError  # noqa: E402
from app.business.products import consultar_stock  # noqa: E402
from app.business.costs import (  # noqa: E402
    actualizar_costo_usdt_marca,
    actualizar_valor_usdt,
)
def actualizar_costos_usdt_marcas(cambios: list[dict[str, Any]]) -> dict[str, Any]:
    resultados = []
    for cambio in cambios:
        marca = cambio.get("marca")
        costo = cambio.get("costo_usdt")
        if marca and costo:
            resultados.append(actualizar_costo_usdt_marca(str(marca), float(costo)))
    return {"ok": True, "resultados": resultados}
from app.business.sale_adjustments import ajustar_cantidad_venta_manual  # noqa: E402
from app.business.sales import (  # noqa: E402
    cancelar_venta_manual,
    consultar_venta,
    registrar_venta_manual,
    registrar_ventas_manuales,
)
from app.order_sync_v2 import (  # noqa: E402
    cancel_internal_order,
    find_by_internal_order,
    set_internal_order_status,
)
from app.tools import buscar_orden  # noqa: E402


def limpiar_texto_modelo(value: Any) -> str:
    import re
    import unicodedata

    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(
        char for char in text
        if not unicodedata.combining(char)
    )
    text = text.replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# FIX CATALOGO DINAMICO VENTA 20260831
def normalizar_modelo(value: Any) -> Any:
    """Normaliza solo alias exactos; los modelos del catalogo son dinamicos."""
    import re
    import unicodedata

    if not isinstance(value, str):
        return value

    original = " ".join(value.strip().split())
    if not original:
        return value

    key = unicodedata.normalize("NFKD", original)
    key = "".join(char for char in key if not unicodedata.combining(char))
    key = re.sub(r"[^a-z0-9]+", " ", key.casefold()).strip()

    # Solo abreviaturas que identifican un modelo sin ambiguedad. Nunca usar
    # coincidencias parciales: por ejemplo, "Lost Mary Dura" no puede caer en
    # el alias historico "Lost Mary" -> "Lost Mary Mixer 30k".
    exact_aliases = {
        "ice king": "Elfbar Ice King 40k",
        "elfbar ice king": "Elfbar Ice King 40k",
        "elfbar 15k": "Elfbar BC 15k",
        "bc 15k": "Elfbar BC 15k",
        "te30k": "Elfbar TE30K",
        "te 30k": "Elfbar TE30K",
        "bc pro": "Elfbar Create BC pro 40k",
        "elfbar bc pro": "Elfbar Create BC pro 40k",
        "pro 45k": "Elfbar pro 45K",
        "elfbar pro 45k": "Elfbar pro 45K",
        "ignite nano": "Ignite v-nano",
        "ignite 155": "Ignite v155",
        "v155": "Ignite v155",
        "ignite 250": "Ignite v250",
        "v250": "Ignite v250",
        "ignite 300": "Ignite v300 slim",
        "v300": "Ignite v300 slim",
        "pulse x": "Geek Bar Pulse X",
        "geek bar": "Geek Bar Pulse X",
        "geek bar pulse x": "Geek Bar Pulse X",
        "airmez": "Airmez Bluetooth 40k (Vape con Auriculares)",
        "maskking": "Maskking Extre 100K",
        "dummy": "Dummy 8k",
    }

    return exact_aliases.get(key, original)


def normalizar_productos(
    productos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    resultado: list[dict[str, Any]] = []

    for producto in productos:
        if not isinstance(producto, dict):
            resultado.append(producto)
            continue

        nuevo = dict(producto)

        if nuevo.get("sabor") is not None:
            nuevo["sabor"] = str(
                nuevo["sabor"]
            ).strip().upper()

        modelo = (
            nuevo.get("marca")
            or nuevo.get("modelo")
            or nuevo.get("vape")
        )

        if modelo:
            modelo_normalizado = normalizar_modelo(modelo)
            nuevo["marca"] = modelo_normalizado

            # Evita enviar campos alternativos contradictorios.
            nuevo.pop("modelo", None)
            nuevo.pop("vape", None)

        resultado.append(nuevo)

    return resultado


def imprimir(data: Any) -> None:
    print(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


def cargar_json(raw: str) -> dict[str, Any]:
    value = str(raw or "").strip()

    if not value:
        return {}

    if value.startswith("@"):
        path = Path(value[1:]).expanduser()

        if not path.exists():
            raise BusinessError(
                f"No existe el archivo JSON {path}."
            )

        value = path.read_text(encoding="utf-8")

    try:
        result = json.loads(value)
    except json.JSONDecodeError as exc:
        raise BusinessError(
            f"JSON inválido: {exc}"
        ) from exc

    if not isinstance(result, dict):
        raise BusinessError(
            "Los datos deben ser un objeto JSON."
        )

    return result


def requerido(
    payload: dict[str, Any],
    field: str,
    label: str | None = None,
) -> Any:
    value = payload.get(field)

    if value is None or str(value).strip() == "":
        raise BusinessError(
            f"Falta indicar {label or field}."
        )

    return value


def ejecutar_consultar_stock(
    payload: dict[str, Any],
) -> dict[str, Any]:
    modelo = (
        payload.get("marca")
        or payload.get("modelo")
        or payload.get("vape")
    )

    return consultar_stock(
        marca=normalizar_modelo(modelo),
        sabor=payload.get("sabor"),
        solo_disponibles=bool(
            payload.get("solo_disponibles", False)
        ),
    )


def datos_venta(
    payload: dict[str, Any],
) -> dict[str, Any]:
    cliente = requerido(
        payload,
        "cliente",
        "el cliente",
    )
    productos = requerido(
        payload,
        "productos",
        "los productos",
    )
    plataforma = requerido(
        payload,
        "plataforma",
        "la plataforma de venta",
    )
    if payload.get("plataforma_confirmada") is not True:
        raise BusinessError(
            "Falta confirmar la plataforma de venta con el usuario."
        )
    forma_pago = (
        payload.get("forma_pago")
        or payload.get("forma_de_pago")
        or payload.get("formaPago")
        or payload.get("medio_pago")
        or payload.get("medio_de_pago")
        or payload.get("metodo_pago")
        or payload.get("metodo_de_pago")
        or payload.get("pago")
        or payload.get("payment_method")
        or payload.get("forma de pago")
    )
    if forma_pago in (None, ""):
        raise BusinessError("Falta indicar la forma de pago.")

    if not isinstance(productos, list) or not productos:
        raise BusinessError(
            "productos debe ser una lista no vacía."
        )

    return {
        "cliente": cliente,
        "productos": normalizar_productos(productos),
        "plataforma": plataforma,
        "forma_pago": forma_pago,
        "estado": payload.get("estado") or "Entregado",
        "fecha": payload.get("fecha"),
    }


def ejecutar_registrar_venta(
    payload: dict[str, Any],
) -> dict[str, Any]:
    return registrar_venta_manual(**datos_venta(payload))


def ejecutar_registrar_ventas(
    payload: dict[str, Any],
) -> dict[str, Any]:
    ventas = payload.get("ventas")

    if not isinstance(ventas, list) or not ventas:
        raise BusinessError("ventas debe ser una lista no vacía.")

    normalized = []
    previous: dict[str, Any] | None = None

    for index, raw_sale in enumerate(ventas, start=1):
        if not isinstance(raw_sale, dict):
            raise BusinessError(
                f"La venta {index} debe ser un objeto JSON."
            )

        sale = dict(raw_sale)
        if sale.pop("misma_forma_anterior", False) is True:
            if previous is None:
                raise BusinessError(
                    "La primera venta no puede usar datos anteriores."
                )
            for key in (
                "plataforma",
                "plataforma_confirmada",
                "forma_pago",
            ):
                sale.setdefault(key, previous[key])

        prepared = datos_venta(sale)
        normalized.append(prepared)
        previous = {
            "plataforma": prepared["plataforma"],
            "plataforma_confirmada": True,
            "forma_pago": prepared["forma_pago"],
        }

    return registrar_ventas_manuales(normalized)


def ejecutar_consultar_orden(
    payload: dict[str, Any],
) -> dict[str, Any]:
    numero = (
        payload.get("orden")
        or payload.get("numero_orden")
    )

    if numero is None or str(numero).strip() == "":
        raise BusinessError(
            "Falta indicar el número de orden."
        )

    # buscar_orden funciona tanto para órdenes manuales como
    # para órdenes provenientes de Tiendanube.
    return buscar_orden(numero)


def ejecutar_consultar_venta_manual(
    payload: dict[str, Any],
) -> dict[str, Any]:
    numero = (
        payload.get("orden")
        or payload.get("numero_orden")
    )

    if numero is None or str(numero).strip() == "":
        raise BusinessError(
            "Falta indicar el número de orden."
        )

    return consultar_venta(
        numero,
        hoja=payload.get("hoja"),
    )


def ejecutar_cambiar_estado(
    payload: dict[str, Any],
) -> dict[str, Any]:
    numero = (
        payload.get("orden")
        or payload.get("numero_orden")
    )
    estado = payload.get("estado")

    if numero is None or str(numero).strip() == "":
        raise BusinessError(
            "Falta indicar el número de orden."
        )

    if estado is None or str(estado).strip() == "":
        raise BusinessError(
            "Falta indicar el nuevo estado."
        )

    try:
        return set_internal_order_status(
            numero,
            estado,
        )
    except Exception as exc:
        raise BusinessError(str(exc)) from exc


def ejecutar_cancelar_manual(
    payload: dict[str, Any],
) -> dict[str, Any]:
    numero = (
        payload.get("orden")
        or payload.get("numero_orden")
    )

    if numero is None or str(numero).strip() == "":
        raise BusinessError(
            "Falta indicar el número de orden."
        )

    if payload.get("confirmar") is not True:
        raise BusinessError(
            "La cancelación requiere confirmar=true."
        )

    return cancelar_venta_manual(
        numero,
        hoja=payload.get("hoja"),
    )


def ejecutar_cancelar_orden(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Detecta automáticamente si la orden es manual o proviene
    de Tiendanube.

    Manual:
        repone Sheets y Tiendanube;
        elimina la venta de Sheets.

    Tiendanube:
        cancela en Tiendanube;
        Tiendanube repone su stock;
        elimina la venta de Sheets;
        sincroniza Productos.
    """
    numero = (
        payload.get("orden")
        or payload.get("numero_orden")
    )

    if numero is None or str(numero).strip() == "":
        raise BusinessError(
            "Falta indicar el número de orden."
        )

    if payload.get("confirmar") is not True:
        raise BusinessError(
            "La cancelación requiere confirmar=true."
        )

    try:
        found = find_by_internal_order(
            numero,
            sheet_name=payload.get("hoja"),
        )
    except Exception as exc:
        raise BusinessError(
            f"No se pudo buscar la orden: {exc}"
        ) from exc

    if not found:
        raise BusinessError(
            f"No se encontró la orden {numero}."
        )

    worksheet, rows, tiendanube_order_id = found

    if str(tiendanube_order_id or "").strip():
        try:
            result = cancel_internal_order(numero)
        except Exception as exc:
            raise BusinessError(str(exc)) from exc

        return {
            **result,
            "tipo_detectado": "tiendanube",
        }

    result = cancelar_venta_manual(
        numero,
        hoja=worksheet.title,
    )

    return {
        **result,
        "tipo_detectado": "manual",
    }


def ejecutar_consultar_orden_externa(
    payload: dict[str, Any],
) -> dict[str, Any]:
    import subprocess

    numero = (
        payload.get("orden")
        or payload.get("numero_orden")
    )

    if numero is None or str(numero).strip() == "":
        raise BusinessError(
            "Falta indicar el número de orden."
        )

    command = [
        str(PROJECT / ".venv" / "bin" / "python"),
        str(PROJECT / "scripts" / "consultar_orden.py"),
        "--orden",
        str(numero),
    ]

    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    output = process.stdout.strip()

    if not output:
        raise BusinessError(
            process.stderr.strip()
            or "La consulta no devolvió información."
        )

    try:
        result = json.loads(output)
    except json.JSONDecodeError as exc:
        raise BusinessError(
            f"Respuesta inválida al consultar la orden: {output}"
        ) from exc

    return result


def ejecutar_actualizar_costo_usdt(
    payload: dict[str, Any],
) -> dict[str, Any]:
    cambios = payload.get("costos") or payload.get("cambios")

    # También acepta un diccionario: {"Elfbar Ice King 40k": 9.5, ...}
    if isinstance(cambios, dict):
        cambios = [
            {"marca": marca, "costo_usdt": costo}
            for marca, costo in cambios.items()
        ]

    if isinstance(cambios, list):
        return actualizar_costos_usdt_marcas(cambios)

    marca = requerido(payload, "marca", "la marca o modelo")
    costo = requerido(payload, "costo_usdt", "el costo USDT")

    try:
        costo_numero = float(str(costo).replace(",", "."))
    except ValueError as exc:
        raise BusinessError("El costo USDT debe ser un número.") from exc

    return actualizar_costo_usdt_marca(str(marca), costo_numero)


def ejecutar_actualizar_valor_usdt(
    payload: dict[str, Any],
) -> dict[str, Any]:
    valor = (
        payload.get("valor_usdt")
        or payload.get("dolar")
        or payload.get("valor_dolar")
    )

    if valor is None or str(valor).strip() == "":
        raise BusinessError("Falta indicar el Valor USDT.")

    try:
        valor_numero = float(str(valor).replace(",", "."))
    except ValueError as exc:
        raise BusinessError("El Valor USDT debe ser un número.") from exc

    return actualizar_valor_usdt(valor_numero)



# === GESTION_MODELOS_V2 ===
def _ejecutar_gestion_modelo(accion: str, payload: dict[str, Any]) -> dict[str, Any]:
    import subprocess

    command = [
        sys.executable,
        str(PROJECT / "scripts" / "gestionar_modelo.py"),
        accion,
        "--json",
        json.dumps(payload, ensure_ascii=False),
    ]
    process = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    raw = process.stdout.strip()
    if not raw:
        raise BusinessError(process.stderr.strip() or "La gestión del modelo no devolvió información.")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BusinessError(f"Respuesta inválida al gestionar el modelo: {raw}") from exc
    if not result.get("ok"):
        raise BusinessError(result.get("mensaje") or result.get("error") or "Falló la gestión del modelo.")
    return result


def ejecutar_crear_modelo(payload: dict[str, Any]) -> dict[str, Any]:
    return _ejecutar_gestion_modelo("crear-modelo", payload)


def ejecutar_eliminar_modelo(payload: dict[str, Any]) -> dict[str, Any]:
    return _ejecutar_gestion_modelo("eliminar-modelo", payload)


def ejecutar_ajustar_venta_manual(
    payload: dict[str, Any],
) -> dict[str, Any]:
    orden = (
        payload.get("orden")
        or payload.get("numero_orden")
    )
    if orden in (None, ""):
        raise BusinessError("Falta indicar la orden.")

    cambio = payload.get("cambio")
    cantidad_nueva = payload.get("cantidad_nueva")

    # También acepta: {"operacion":"sumar/restar", "cantidad":2}
    if cambio in (None, "") and cantidad_nueva in (None, ""):
        cantidad = payload.get("cantidad")
        operacion = str(payload.get("operacion") or "").strip().lower()
        if cantidad not in (None, "") and operacion in {"sumar", "agregar", "+"}:
            cambio = abs(int(cantidad))
        elif cantidad not in (None, "") and operacion in {"restar", "quitar", "-"}:
            cambio = -abs(int(cantidad))

    return ajustar_cantidad_venta_manual(
        orden,
        hoja=payload.get("hoja"),
        marca=(
            payload.get("marca")
            or payload.get("modelo")
            or payload.get("vape")
        ),
        sabor=payload.get("sabor"),
        variant_id=(
            payload.get("variant_id")
            or payload.get("tiendanube_variant_id")
        ),
        cambio=cambio,
        cantidad_nueva=cantidad_nueva,
        precio_unitario=payload.get("precio_unitario"),
    )

def ejecutar_modificar_plataforma_venta(
    payload: dict[str, Any],
) -> dict[str, Any]:
    from modificar_plataforma_venta import cambiar_plataforma_venta

    orden = payload.get("orden") or payload.get("numero_orden")
    if orden in (None, ""):
        raise BusinessError("Falta indicar la orden.")
    plataforma = requerido(
        payload,
        "plataforma",
        "la nueva plataforma de venta",
    )
    return cambiar_plataforma_venta(
        orden=orden,
        plataforma=plataforma,
        hoja=payload.get("hoja"),
    )


ACCIONES = {
    "modificar-plataforma-venta": ejecutar_modificar_plataforma_venta,
    "ajustar-venta-manual": ejecutar_ajustar_venta_manual,
    "crear-modelo": ejecutar_crear_modelo,
    "eliminar-modelo": ejecutar_eliminar_modelo,
    "consultar-stock": ejecutar_consultar_stock,
    "actualizar-costo-usdt": ejecutar_actualizar_costo_usdt,
    "actualizar-valor-usdt": ejecutar_actualizar_valor_usdt,
    "registrar-venta": ejecutar_registrar_venta,
    "registrar-ventas": ejecutar_registrar_ventas,
    "consultar-orden": ejecutar_consultar_orden_externa,
    "consultar-venta-manual": (
        ejecutar_consultar_venta_manual
    ),
    "cambiar-estado": ejecutar_cambiar_estado,
    "cancelar-manual": ejecutar_cancelar_manual,
    "cancelar-orden": ejecutar_cancelar_orden,
}


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agente_vaprizzio.py",
        description=(
            "Herramientas seguras del agente Vaprizzio."
        ),
    )

    parser.add_argument(
        "accion",
        choices=sorted(ACCIONES),
    )
    parser.add_argument(
        "--json",
        required=True,
        help=(
            "Objeto JSON con los datos de la operación."
        ),
    )

    return parser


# RESULTADOS ESTRUCTURADOS SIN ERROR DE SHELL 20260814
def main() -> int:
    parser = construir_parser()
    args = parser.parse_args()

    try:
        payload = cargar_json(args.json)
        operation = ACCIONES[args.accion]
        result = operation(payload)

        if not isinstance(result, dict):
            result = {
                "resultado": result,
            }

        if "ok" not in result:
            result["ok"] = True

        result["accion"] = args.accion
        imprimir(result)
        return 0

    except BusinessError as exc:
        imprimir(
            {
                "ok": False,
                "accion": args.accion,
                "error": str(exc),
            }
        )
        return 0

    except Exception as exc:
        imprimir(
            {
                "ok": False,
                "accion": args.accion,
                "error": (
                    f"{type(exc).__name__}: {exc}"
                ),
            }
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
