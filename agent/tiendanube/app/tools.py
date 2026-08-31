"""
Herramientas de negocio de Vaprizzio.

Este módulo funciona como una capa estable entre:

- Telegram / OpenClaw
- comandos de terminal
- futuras integraciones
- la lógica actual de Tiendanube y Google Sheets

No contiene credenciales ni lógica del webhook.
Reutiliza las funciones ya probadas de order_sync_v2.py.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.order_sync_v2 import (
    SyncError,
    cancel_internal_order,
    find_by_internal_order,
    set_internal_order_status,
)


ESTADOS_VALIDOS = {
    "recibido": "Recibido",
    "aceptado": "Aceptado",
    "en curso": "En curso",
    "enviado": "Enviado",
    "entregado": "Entregado",
}


class ToolError(RuntimeError):
    """Error controlado de una herramienta de negocio."""


def _texto_limpio(value: Any) -> str:
    return str(value or "").strip()


def _normalizar(value: Any) -> str:
    """
    Convierte un texto a una forma simple para poder comparar
    mayúsculas, minúsculas y acentos.
    """
    text = _texto_limpio(value).lower()

    normalized = unicodedata.normalize(
        "NFKD",
        text,
    )

    return "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )


def _normalizar_numero_orden(value: Any) -> str:
    """
    Valida el número interno visible en Google Sheets.
    """
    value_text = _texto_limpio(value)

    match = re.search(r"\d+", value_text)

    if not match:
        raise ToolError(
            "No se encontró un número de orden válido."
        )

    return match.group(0)


def _valor_fila(
    row: list[Any],
    column_number: int,
) -> str:
    index = column_number - 1

    if index < 0 or index >= len(row):
        return ""

    return _texto_limpio(row[index])


def _fila_como_diccionario(
    headers: list[Any],
    row: list[Any],
) -> dict[str, str]:
    result: dict[str, str] = {}

    max_length = max(
        len(headers),
        len(row),
    )

    for index in range(max_length):
        header = (
            _texto_limpio(headers[index])
            if index < len(headers)
            else ""
        )

        value = (
            _texto_limpio(row[index])
            if index < len(row)
            else ""
        )

        key = header or f"columna_{index + 1}"

        result[key] = value

    return result


def buscar_orden(
    numero_orden: Any,
) -> dict[str, Any]:
    """
    Busca una orden utilizando el número interno de Google Sheets.

    Es una operación únicamente de lectura.
    """
    internal_order = _normalizar_numero_orden(
        numero_orden
    )

    try:
        found = find_by_internal_order(
            internal_order
        )
    except Exception as exc:
        raise ToolError(
            f"No se pudo consultar la orden "
            f"{internal_order}: {exc}"
        ) from exc

    if not found:
        return {
            "ok": False,
            "encontrada": False,
            "orden": internal_order,
            "mensaje": (
                f"No se encontró la orden "
                f"{internal_order}."
            ),
        }

    worksheet, row_numbers, tiendanube_id = found
    values = worksheet.get_all_values()

    headers: list[Any] = (
        values[0]
        if values
        else []
    )

    rows: list[dict[str, Any]] = []

    for row_number in row_numbers:
        if row_number <= 0:
            continue

        row = (
            values[row_number - 1]
            if row_number <= len(values)
            else []
        )

        rows.append(
            {
                "numero_fila": row_number,
                "datos": _fila_como_diccionario(
                    headers,
                    row,
                ),
            }
        )

    return {
        "ok": True,
        "encontrada": True,
        "orden": internal_order,
        "tiendanube_order_id": (
            _texto_limpio(tiendanube_id)
        ),
        "hoja": worksheet.title,
        "filas": rows,
    }


def cambiar_estado(
    numero_orden: Any,
    estado: Any,
) -> dict[str, Any]:
    """
    Cambia el estado de una orden en Google Sheets.
    """
    internal_order = _normalizar_numero_orden(
        numero_orden
    )

    normalized_status = _normalizar(estado)

    aliases = {
        "recibido": "Recibido",
        "aceptado": "Aceptado",
        "en curso": "En curso",
        "preparando": "En curso",
        "preparado": "En curso",
        "preparada": "En curso",
        "procesando": "En curso",
        "enviado": "Enviado",
        "enviada": "Enviado",
        "despachado": "Enviado",
        "despachada": "Enviado",
        "entregado": "Entregado",
        "entregada": "Entregado",
    }

    canonical_status = aliases.get(
        normalized_status
    )

    if not canonical_status:
        raise ToolError(
            "Estado inválido. Los estados disponibles son: "
            "Recibido, Aceptado, En curso, Enviado y Entregado."
        )

    try:
        result = set_internal_order_status(
            internal_order,
            canonical_status,
        )
    except SyncError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        raise ToolError(
            f"No se pudo cambiar el estado de la orden "
            f"{internal_order}: {exc}"
        ) from exc

    return {
        "ok": True,
        "accion": "cambiar_estado",
        "orden": internal_order,
        "estado": canonical_status,
        "resultado": result,
    }


def cancelar_orden(
    numero_orden: Any,
) -> dict[str, Any]:
    """
    Cancela una orden usando la lógica segura existente.

    Orden de ejecución:
    1. Cancela en Tiendanube.
    2. Tiendanube repone el stock.
    3. Tiendanube envía el correo.
    4. Se elimina la venta de Google Sheets.
    """
    internal_order = _normalizar_numero_orden(
        numero_orden
    )

    try:
        result = cancel_internal_order(
            internal_order
        )
    except SyncError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:
        raise ToolError(
            f"No se pudo cancelar la orden "
            f"{internal_order}: {exc}"
        ) from exc

    return {
        "ok": True,
        "accion": "cancelar_orden",
        "orden": internal_order,
        "resultado": result,
    }


def interpretar_accion(
    texto: Any,
) -> dict[str, Any]:
    """
    Interpreta frases simples en español.

    Esta función solamente interpreta. No modifica nada.
    """
    original_text = _texto_limpio(texto)
    normalized_text = _normalizar(original_text)

    order_match = re.search(
        r"(?:orden|pedido|numero|nro|n[°º])?\s*#?\s*(\d+)",
        normalized_text,
    )

    if not order_match:
        return {
            "ok": False,
            "entendida": False,
            "texto": original_text,
            "mensaje": (
                "No pude identificar el número de orden."
            ),
        }

    internal_order = order_match.group(1)

    cancel_words = (
        "cancel",
        "anul",
    )

    delivered_words = (
        "entreg",
        "recibio",
        "recibido por el cliente",
    )

    shipped_words = (
        "envie",
        "enviado",
        "despach",
        "prepare",
        "preparado",
        "lista para enviar",
    )

    progress_words = (
        "en curso",
        "empeza",
        "empece",
        "preparando",
        "procesando",
    )

    accepted_words = (
        "acept",
        "pago aprobado",
        "aprobar",
    )

    if any(
        word in normalized_text
        for word in cancel_words
    ):
        return {
            "ok": True,
            "entendida": True,
            "accion": "cancelar_orden",
            "orden": internal_order,
            "requiere_confirmacion": True,
            "texto": original_text,
        }

    if any(
        word in normalized_text
        for word in delivered_words
    ):
        return {
            "ok": True,
            "entendida": True,
            "accion": "cambiar_estado",
            "orden": internal_order,
            "estado": "Entregado",
            "requiere_confirmacion": False,
            "texto": original_text,
        }

    if any(
        word in normalized_text
        for word in shipped_words
    ):
        return {
            "ok": True,
            "entendida": True,
            "accion": "cambiar_estado",
            "orden": internal_order,
            "estado": "Enviado",
            "requiere_confirmacion": False,
            "texto": original_text,
        }

    if any(
        word in normalized_text
        for word in progress_words
    ):
        return {
            "ok": True,
            "entendida": True,
            "accion": "cambiar_estado",
            "orden": internal_order,
            "estado": "En curso",
            "requiere_confirmacion": False,
            "texto": original_text,
        }

    if any(
        word in normalized_text
        for word in accepted_words
    ):
        return {
            "ok": True,
            "entendida": True,
            "accion": "cambiar_estado",
            "orden": internal_order,
            "estado": "Aceptado",
            "requiere_confirmacion": False,
            "texto": original_text,
        }

    if (
        "buscar" in normalized_text
        or "mostrar" in normalized_text
        or "ver orden" in normalized_text
        or "ver pedido" in normalized_text
    ):
        return {
            "ok": True,
            "entendida": True,
            "accion": "buscar_orden",
            "orden": internal_order,
            "requiere_confirmacion": False,
            "texto": original_text,
        }

    return {
        "ok": False,
        "entendida": False,
        "orden": internal_order,
        "texto": original_text,
        "mensaje": (
            "Identifiqué el número de orden, pero no la acción."
        ),
    }


def ejecutar_accion(
    texto: Any,
    *,
    confirmar_cancelacion: bool = False,
) -> dict[str, Any]:
    """
    Interpreta y ejecuta una frase.

    Las cancelaciones requieren confirmación explícita.
    """
    interpretation = interpretar_accion(texto)

    if not interpretation.get("entendida"):
        return interpretation

    action = interpretation.get("accion")
    internal_order = interpretation.get("orden")

    if action == "buscar_orden":
        return buscar_orden(
            internal_order
        )

    if action == "cambiar_estado":
        return cambiar_estado(
            internal_order,
            interpretation.get("estado"),
        )

    if action == "cancelar_orden":
        if not confirmar_cancelacion:
            return {
                "ok": False,
                "ejecutada": False,
                "requiere_confirmacion": True,
                "accion": "cancelar_orden",
                "orden": internal_order,
                "mensaje": (
                    f"La cancelación de la orden "
                    f"{internal_order} requiere confirmación."
                ),
            }

        return cancelar_orden(
            internal_order
        )

    raise ToolError(
        f"Acción no soportada: {action}"
    )
