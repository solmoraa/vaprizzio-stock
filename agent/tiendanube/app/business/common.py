from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo


TIMEZONE = ZoneInfo("America/Argentina/Buenos_Aires")

ESTADOS_VALIDOS = {
    "recibido": "Recibido",
    "aceptado": "Aceptado",
    "en curso": "En curso",
    "encurso": "En curso",
    "enviado": "Enviado",
    "entregado": "Entregado",
}


class BusinessError(RuntimeError):
    """Error controlado del núcleo de negocio."""


def ahora() -> datetime:
    return datetime.now(TIMEZONE)


def texto(value: Any) -> str:
    return str(value or "").strip()


def normalizar(value: Any) -> str:
    result = texto(value).casefold()

    result = "".join(
        char
        for char in unicodedata.normalize("NFD", result)
        if unicodedata.category(char) != "Mn"
    )

    result = re.sub(r"[^a-z0-9]+", " ", result)
    return " ".join(result.split())


def entero(value: Any, *, nombre: str = "cantidad") -> int:
    try:
        result = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise BusinessError(
            f"{nombre.capitalize()} inválida: {value!r}."
        ) from exc

    return result


def numero(value: Any, *, nombre: str = "importe") -> float:
    if isinstance(value, (int, float)):
        return float(value)

    raw = texto(value)

    if not raw:
        raise BusinessError(
            f"{nombre.capitalize()} vacío."
        )

    raw = raw.replace("$", "").replace(" ", "")

    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        decimals = len(raw.split(",")[-1])

        if decimals in {1, 2}:
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif raw.count(".") > 1:
        raw = raw.replace(".", "")
    elif "." in raw:
        decimals = len(raw.split(".")[-1])

        if decimals == 3:
            raw = raw.replace(".", "")

    try:
        return float(raw)
    except ValueError as exc:
        raise BusinessError(
            f"{nombre.capitalize()} inválido: {value!r}."
        ) from exc


def dinero(value: float | int) -> float | int:
    value = float(value)

    if value.is_integer():
        return int(value)

    return round(value, 2)


def fecha_desde_value(
    value: Any = None,
) -> datetime:
    if value is None or texto(value) == "":
        return ahora()

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=TIMEZONE)

        return value.astimezone(TIMEZONE)

    if isinstance(value, date):
        return datetime(
            value.year,
            value.month,
            value.day,
            tzinfo=TIMEZONE,
        )

    raw = texto(value)

    formatos = (
        "%d/%m/%Y",
        "%d/%m/%y",
        "%Y-%m-%d",
        "%d-%m-%Y",
    )

    for formato in formatos:
        try:
            parsed = datetime.strptime(raw, formato)
            return parsed.replace(tzinfo=TIMEZONE)
        except ValueError:
            pass

    raise BusinessError(
        "Fecha inválida. Usá DD/MM/AAAA o AAAA-MM-DD."
    )


def fecha_para_sheet(value: datetime) -> str:
    return f"{value.day}/{value.month}/{value.year}"


def estado_canonico(value: Any) -> str:
    key = normalizar(value)

    if not key:
        return "Recibido"

    canonical = ESTADOS_VALIDOS.get(key)

    if canonical is None:
        allowed = ", ".join(
            sorted(set(ESTADOS_VALIDOS.values()))
        )

        raise BusinessError(
            f"Estado inválido: {value!r}. "
            f"Estados permitidos: {allowed}."
        )

    return canonical


def celda(row: list[Any], column_number: int | None) -> str:
    if not column_number or column_number <= 0:
        return ""

    index = column_number - 1

    if index >= len(row):
        return ""

    return texto(row[index])


def columna_a_letra(column: int) -> str:
    if column <= 0:
        raise ValueError("La columna debe ser mayor a cero.")

    letters = ""

    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(65 + remainder) + letters

    return letters
