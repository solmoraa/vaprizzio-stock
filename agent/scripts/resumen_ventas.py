#!/usr/bin/env python3

from __future__ import annotations

import argparse
import calendar
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests


# ============================================================
# CONFIGURACIÓN DEL PROYECTO
# ============================================================

WORKSPACE = Path(
    "/home/openclaw/.openclaw/workspace/vaprizziobot"
)

TIENDANUBE_DIR = WORKSPACE / "tiendanube"

if str(TIENDANUBE_DIR) not in sys.path:
    sys.path.insert(0, str(TIENDANUBE_DIR))

from app.sync_core import spreadsheet  # noqa: E402


# ============================================================
# CONSTANTES
# ============================================================

MESES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

NOMBRES_MESES = {
    1: "Enero",
    2: "Febrero",
    3: "Marzo",
    4: "Abril",
    5: "Mayo",
    6: "Junio",
    7: "Julio",
    8: "Agosto",
    9: "Septiembre",
    10: "Octubre",
    11: "Noviembre",
    12: "Diciembre",
}

ESTADOS_NO_VALIDOS = {
    "cancelado",
    "cancelada",
    "anulado",
    "anulada",
    "rechazado",
    "rechazada",
}

COLUMN_ALIASES = {
    "orden": {
        "orden",
        "numero de orden",
        "nro orden",
        "id orden",
    },
    "cantidad": {
        "cantidad",
        "unidades",
        "unidad",
    },
    "cliente": {
        "cliente",
        "nombre cliente",
    },
    "producto": {
        "vape",
        "producto",
        "marca",
        "modelo",
    },
    "sabor": {
        "sabor",
        "variante",
    },
    "estado": {
        "estado",
        "estado venta",
    },
    "fecha": {
        "fecha del pedido",
        "fecha",
        "fecha pedido",
    },
    "precio": {
        "precio venta",
        "precio",
        "total",
        "importe",
    },
    "plataforma": {
        "plataforma de ventas",
        "plataforma",
        "canal",
    },
    "ganancia": {
        "ganancia",
        "utilidad",
    },
    "pago": {
        "forma de pago",
        "metodo de pago",
        "medio de pago",
    },
}


# ============================================================
# MODELOS
# ============================================================

@dataclass
class Periodo:
    inicio: date
    fin: date
    descripcion: str


@dataclass
class Venta:
    orden: str
    cantidad: int
    cliente: str
    producto: str
    sabor: str
    estado: str
    fecha: date
    facturacion: float
    ganancia: float
    plataforma: str
    forma_pago: str


@dataclass
class Metricas:
    inicio: date
    fin: date
    ventas: int
    unidades: int
    facturacion: float
    ganancia_bruta: float
    costo: float
    ticket_promedio: float
    ganancia_promedio: float
    margen: float
    clientes_unicos: int
    productos: Counter
    sabores: Counter
    plataformas: Counter
    pagos: Counter


# ============================================================
# UTILIDADES
# ============================================================

def normalizar(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(
        char
        for char in text
        if not unicodedata.combining(char)
    )
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def texto(value: Any) -> str:
    return str(value or "").strip()


def parsear_numero(value: Any) -> float:
    if value is None:
        return 0.0

    if isinstance(value, (int, float)):
        return float(value)

    raw = str(value).strip()

    if not raw:
        return 0.0

    raw = (
        raw.replace("$", "")
        .replace("ARS", "")
        .replace("\xa0", "")
        .replace(" ", "")
    )

    negative = raw.startswith("(") and raw.endswith(")")

    if negative:
        raw = raw[1:-1]

    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        decimals = len(raw.rsplit(",", 1)[-1])

        if decimals <= 2:
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "." in raw:
        decimals = len(raw.rsplit(".", 1)[-1])

        if decimals == 3:
            raw = raw.replace(".", "")

    raw = re.sub(r"[^0-9.\-]", "", raw)

    try:
        number = float(raw)
    except ValueError:
        return 0.0

    return -number if negative else number


def parsear_entero(value: Any, default: int = 0) -> int:
    try:
        return int(round(parsear_numero(value)))
    except (TypeError, ValueError):
        return default


def parsear_fecha(value: Any) -> date | None:
    if value in (None, ""):
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    raw = str(value).strip()

    formatos = (
        "%d/%m/%Y",
        "%d/%m/%y",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d-%m-%y",
        "%d.%m.%Y",
    )

    for formato in formatos:
        try:
            return datetime.strptime(raw, formato).date()
        except ValueError:
            pass

    return None


def moneda(value: float) -> str:
    rounded = round(value)

    formatted = f"{abs(rounded):,}".replace(",", ".")

    if rounded < 0:
        return f"-${formatted}"

    return f"${formatted}"


def porcentaje(value: float) -> str:
    return f"{value:.1f}%".replace(".", ",")


def primer_dia_mes(value: date) -> date:
    return value.replace(day=1)


def ultimo_dia_mes(value: date) -> date:
    return value.replace(
        day=calendar.monthrange(value.year, value.month)[1]
    )


def desplazar_meses(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year = month_index // 12
    month = month_index % 12 + 1
    day = min(
        value.day,
        calendar.monthrange(year, month)[1],
    )

    return date(year, month, day)


def limpiar_consulta(value: str) -> str:
    query = normalizar(value)

    prefixes = (
        "dame un resumen de ventas de ",
        "dame el resumen de ventas de ",
        "resumen de las ventas de ",
        "resumen de ventas de ",
        "resumen ventas de ",
        "resumen de ventas",
        "resumen ventas",
        "resumen de ",
        "ventas de ",
        "ventas ",
        "resumen",
    )

    for prefix in prefixes:
        if query.startswith(prefix):
            query = query[len(prefix):].strip()
            break

    return query or "este mes"


# ============================================================
# INTERPRETACIÓN DEL PERÍODO
# ============================================================

def parsear_fecha_textual(
    value: str,
    *,
    year_default: int,
) -> date | None:
    raw = normalizar(value)

    numeric = re.fullmatch(
        r"(\d{1,2})[ /.-](\d{1,2})(?:[ /.-](\d{2,4}))?",
        raw,
    )

    if numeric:
        day = int(numeric.group(1))
        month = int(numeric.group(2))
        year_text = numeric.group(3)

        if year_text:
            year = int(year_text)

            if year < 100:
                year += 2000
        else:
            year = year_default

        try:
            return date(year, month, day)
        except ValueError:
            return None

    textual = re.fullmatch(
        r"(\d{1,2})(?: de)? ([a-z]+)(?: de)?(?: (\d{4}))?",
        raw,
    )

    if textual:
        day = int(textual.group(1))
        month = MESES.get(textual.group(2))
        year = int(textual.group(3) or year_default)

        if not month:
            return None

        try:
            return date(year, month, day)
        except ValueError:
            return None

    return None


def resolver_periodo(
    consulta: str,
    *,
    hoy: date | None = None,
) -> Periodo:
    today = hoy or date.today()
    query = limpiar_consulta(consulta)

    if query in {"hoy", "del dia", "dia"}:
        return Periodo(today, today, "Hoy")

    if query == "ayer":
        yesterday = today - timedelta(days=1)
        return Periodo(yesterday, yesterday, "Ayer")

    if query in {
        "esta semana",
        "semana",
        "semanal",
        "semana actual",
    }:
        start = today - timedelta(days=today.weekday())
        return Periodo(
            start,
            today,
            f"Esta semana ({start.strftime('%d/%m')} al "
            f"{today.strftime('%d/%m/%Y')})",
        )

    if query in {
        "semana pasada",
        "ultima semana",
        "la semana pasada",
    }:
        current_start = today - timedelta(days=today.weekday())
        end = current_start - timedelta(days=1)
        start = end - timedelta(days=6)

        return Periodo(
            start,
            end,
            f"Semana pasada ({start.strftime('%d/%m')} al "
            f"{end.strftime('%d/%m/%Y')})",
        )

    if query in {
        "este mes",
        "mes",
        "mensual",
        "mes actual",
    }:
        start = primer_dia_mes(today)

        return Periodo(
            start,
            today,
            f"{NOMBRES_MESES[today.month]} {today.year}",
        )

    if query in {
        "mes pasado",
        "ultimo mes",
        "el mes pasado",
    }:
        previous = desplazar_meses(
            primer_dia_mes(today),
            -1,
        )
        start = primer_dia_mes(previous)
        end = ultimo_dia_mes(previous)

        return Periodo(
            start,
            end,
            f"{NOMBRES_MESES[start.month]} {start.year}",
        )

    if query in {
        "este ano",
        "ano",
        "anual",
        "ano actual",
    }:
        start = date(today.year, 1, 1)

        return Periodo(
            start,
            today,
            f"Año {today.year}",
        )

    if query in {
        "ano pasado",
        "ultimo ano",
        "el ano pasado",
    }:
        year = today.year - 1

        return Periodo(
            date(year, 1, 1),
            date(year, 12, 31),
            f"Año {year}",
        )

    last_days = re.fullmatch(
        r"(?:ultimos|ultimas) (\d+) dias",
        query,
    )

    if last_days:
        days = max(int(last_days.group(1)), 1)
        start = today - timedelta(days=days - 1)

        return Periodo(
            start,
            today,
            f"Últimos {days} días",
        )

    year_only = re.fullmatch(r"(?:ano )?(\d{4})", query)

    if year_only:
        year = int(year_only.group(1))

        return Periodo(
            date(year, 1, 1),
            date(year, 12, 31),
            f"Año {year}",
        )

    month_match = re.fullmatch(
        r"(?:mes de )?([a-z]+)(?: de)?(?: (\d{4}))?",
        query,
    )

    if month_match:
        month = MESES.get(month_match.group(1))

        if month:
            year = int(month_match.group(2) or today.year)
            start = date(year, month, 1)
            end = ultimo_dia_mes(start)

            if year == today.year and month == today.month:
                end = min(end, today)

            return Periodo(
                start,
                end,
                f"{NOMBRES_MESES[month]} {year}",
            )

    range_match = re.fullmatch(
        r"(?:del )?(.+?) (?:al|hasta) (.+)",
        query,
    )

    if range_match:
        first_text = range_match.group(1)
        second_text = range_match.group(2)

        second_date = parsear_fecha_textual(
            second_text,
            year_default=today.year,
        )

        if second_date:
            first_date = parsear_fecha_textual(
                first_text,
                year_default=second_date.year,
            )

            if not first_date:
                first_day_only = re.fullmatch(
                    r"(?:el )?(\d{1,2})",
                    normalizar(first_text),
                )

                month_in_second = re.search(
                    r"([a-z]+)",
                    normalizar(second_text),
                )

                if first_day_only and month_in_second:
                    month = MESES.get(month_in_second.group(1))

                    if month:
                        try:
                            first_date = date(
                                second_date.year,
                                month,
                                int(first_day_only.group(1)),
                            )
                        except ValueError:
                            first_date = None

            if first_date:
                start = min(first_date, second_date)
                end = max(first_date, second_date)

                return Periodo(
                    start,
                    end,
                    f"{start.strftime('%d/%m/%Y')} al "
                    f"{end.strftime('%d/%m/%Y')}",
                )

    raise ValueError(
        "No pude interpretar el período. Ejemplos válidos: "
        "'hoy', 'esta semana', 'este mes', 'junio 2026', "
        "'2025', 'últimos 30 días' o "
        "'01/07/2026 al 15/07/2026'."
    )


# ============================================================
# GOOGLE SHEETS
# ============================================================

def resolver_columnas(headers: list[str]) -> dict[str, int]:
    normalized_headers = [
        normalizar(header)
        for header in headers
    ]

    result: dict[str, int] = {}

    for logical_name, aliases in COLUMN_ALIASES.items():
        normalized_aliases = {
            normalizar(alias)
            for alias in aliases
        }

        for index, header in enumerate(normalized_headers):
            if header in normalized_aliases:
                result[logical_name] = index
                break

    return result


def valor_fila(
    row: list[Any],
    columns: dict[str, int],
    logical_name: str,
) -> str:
    index = columns.get(logical_name)

    if index is None or index >= len(row):
        return ""

    return texto(row[index])


def nombres_hojas_ventas(book: Any) -> list[Any]:
    worksheets = []

    for worksheet in book.worksheets():
        if normalizar(worksheet.title).startswith("ventas "):
            worksheets.append(worksheet)

    return worksheets


def leer_ventas() -> list[Venta]:
    book = spreadsheet()
    ventas: list[Venta] = []

    for worksheet in nombres_hojas_ventas(book):
        values = worksheet.get_all_values()

        if not values:
            continue

        header_row_index = None
        columns: dict[str, int] = {}

        for index, row in enumerate(values[:12]):
            candidate = resolver_columnas(row)

            if (
                "fecha" in candidate
                and "precio" in candidate
                and "producto" in candidate
            ):
                header_row_index = index
                columns = candidate
                break

        if header_row_index is None:
            continue

        for row in values[header_row_index + 1:]:
            fecha = parsear_fecha(
                valor_fila(row, columns, "fecha")
            )

            if fecha is None:
                continue

            producto = valor_fila(
                row,
                columns,
                "producto",
            )

            precio_unitario = parsear_numero(
                valor_fila(row, columns, "precio")
            )

            cantidad = parsear_entero(
                valor_fila(row, columns, "cantidad"),
                default=1,
            )

            if cantidad <= 0:
                cantidad = 1

            ganancia_unitaria = parsear_numero(
                valor_fila(row, columns, "ganancia")
            )

            estado = valor_fila(
                row,
                columns,
                "estado",
            )

            if normalizar(estado) in ESTADOS_NO_VALIDOS:
                continue

            if not producto and precio_unitario == 0:
                continue

            ventas.append(
                Venta(
                    orden=valor_fila(
                        row,
                        columns,
                        "orden",
                    ),
                    cantidad=cantidad,
                    cliente=valor_fila(
                        row,
                        columns,
                        "cliente",
                    ),
                    producto=producto or "Sin producto",
                    sabor=valor_fila(
                        row,
                        columns,
                        "sabor",
                    ) or "Sin sabor",
                    estado=estado,
                    fecha=fecha,
                    facturacion=precio_unitario * cantidad,
                    ganancia=ganancia_unitaria * cantidad,
                    plataforma=valor_fila(
                        row,
                        columns,
                        "plataforma",
                    ) or "Sin especificar",
                    forma_pago=valor_fila(
                        row,
                        columns,
                        "pago",
                    ) or "Sin especificar",
                )
            )

    return ventas


def buscar_hoja(
    book: Any,
    title: str,
) -> Any | None:
    normalized_title = normalizar(title)

    for worksheet in book.worksheets():
        if normalizar(worksheet.title) == normalized_title:
            return worksheet

    return None


def gastos_fijos_del_periodo(
    periodo: Periodo,
) -> tuple[float, str]:
    """
    Lee la hoja 'Gastos Fijos'.

    La estructura esperada es:
      - una celda con el nombre del mes;
      - importes debajo del encabezado 'Gastos Fijos'.

    Si el período cubre solamente una parte del mes,
    prorratea los gastos según la cantidad de días incluidos.
    """
    book = spreadsheet()
    worksheet = buscar_hoja(book, "Gastos Fijos")

    if worksheet is None:
        return 0.0, "No se encontró la hoja Gastos Fijos"

    values = worksheet.get_all_values()

    if not values:
        return 0.0, "La hoja Gastos Fijos está vacía"

    expense_column = None
    header_row = None
    month = None
    year = periodo.fin.year

    for row_index, row in enumerate(values[:15]):
        for column_index, value in enumerate(row):
            normalized = normalizar(value)

            if normalized == "gastos fijos":
                expense_column = column_index
                header_row = row_index

            for month_name, month_number in MESES.items():
                if re.search(
                    rf"\b{re.escape(month_name)}\b",
                    normalized,
                ):
                    month = month_number

                    year_match = re.search(
                        r"\b(20\d{2})\b",
                        normalized,
                    )

                    if year_match:
                        year = int(year_match.group(1))

    if expense_column is None or header_row is None:
        return 0.0, "No se encontró la columna Gastos Fijos"

    if month is None:
        month = periodo.fin.month

    total = 0.0

    for row in values[header_row + 1:]:
        if expense_column >= len(row):
            continue

        amount = parsear_numero(row[expense_column])

        if amount > 0:
            total += amount

    if total <= 0:
        return 0.0, "No hay gastos fijos cargados"

    month_start = date(year, month, 1)
    month_end = ultimo_dia_mes(month_start)

    overlap_start = max(periodo.inicio, month_start)
    overlap_end = min(periodo.fin, month_end)

    if overlap_start > overlap_end:
        return (
            0.0,
            f"Los gastos cargados corresponden a "
            f"{NOMBRES_MESES[month]} {year}",
        )

    month_days = (month_end - month_start).days + 1
    overlap_days = (overlap_end - overlap_start).days + 1

    if overlap_days < month_days:
        prorated = total * overlap_days / month_days

        return (
            prorated,
            f"Prorrateados: {overlap_days} de {month_days} días",
        )

    return total, f"{NOMBRES_MESES[month]} {year}"


# ============================================================
# CÁLCULOS
# ============================================================

def calcular_metricas(
    ventas: list[Venta],
    inicio: date,
    fin: date,
) -> Metricas:
    selected = [
        venta
        for venta in ventas
        if inicio <= venta.fecha <= fin
    ]

    orders_with_id: dict[str, float] = defaultdict(float)
    anonymous_orders = 0

    productos: Counter = Counter()
    sabores: Counter = Counter()
    plataformas: Counter = Counter()
    pagos: Counter = Counter()
    clientes: set[str] = set()

    facturacion = 0.0
    ganancia = 0.0
    unidades = 0

    for index, venta in enumerate(selected):
        facturacion += venta.facturacion
        ganancia += venta.ganancia
        unidades += venta.cantidad

        product_key = venta.producto

        if normalizar(venta.sabor) not in {
            "",
            "sin sabor",
            "sabor",
        }:
            product_key = (
                f"{venta.producto} - {venta.sabor}"
            )

        productos[product_key] += venta.cantidad
        sabores[venta.sabor] += venta.cantidad
        plataformas[venta.plataforma] += venta.facturacion
        pagos[venta.forma_pago] += venta.facturacion

        if venta.cliente:
            clientes.add(normalizar(venta.cliente))

        if venta.orden:
            orders_with_id[normalizar(venta.orden)] += (
                venta.facturacion
            )
        else:
            anonymous_orders += 1

    number_of_sales = len(orders_with_id) + anonymous_orders

    if number_of_sales == 0 and selected:
        number_of_sales = len(selected)

    cost = facturacion - ganancia

    ticket = (
        facturacion / number_of_sales
        if number_of_sales
        else 0.0
    )

    gain_average = (
        ganancia / number_of_sales
        if number_of_sales
        else 0.0
    )

    margin = (
        ganancia / facturacion * 100
        if facturacion
        else 0.0
    )

    return Metricas(
        inicio=inicio,
        fin=fin,
        ventas=number_of_sales,
        unidades=unidades,
        facturacion=facturacion,
        ganancia_bruta=ganancia,
        costo=cost,
        ticket_promedio=ticket,
        ganancia_promedio=gain_average,
        margen=margin,
        clientes_unicos=len(clientes),
        productos=productos,
        sabores=sabores,
        plataformas=plataformas,
        pagos=pagos,
    )


def periodo_anterior(periodo: Periodo) -> Periodo:
    days = (periodo.fin - periodo.inicio).days + 1
    previous_end = periodo.inicio - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)

    return Periodo(
        previous_start,
        previous_end,
        f"{previous_start.strftime('%d/%m/%Y')} al "
        f"{previous_end.strftime('%d/%m/%Y')}",
    )


def variacion_porcentual(
    actual: float,
    anterior: float,
) -> float | None:
    if anterior == 0:
        return None

    return (actual - anterior) / anterior * 100


def linea_variacion(
    nombre: str,
    actual: float,
    anterior: float,
    *,
    money: bool = False,
) -> str:
    variation = variacion_porcentual(actual, anterior)

    if variation is None:
        if actual == 0:
            return f"• {nombre}: sin cambios"

        return f"• {nombre}: sin datos anteriores"

    if variation > 0:
        arrow = "▲"
        sign = "+"
    elif variation < 0:
        arrow = "▼"
        sign = ""
    else:
        arrow = "•"
        sign = ""

    current_text = moneda(actual) if money else str(round(actual))

    return (
        f"{arrow} {nombre}: {sign}"
        f"{porcentaje(variation)} "
        f"(actual: {current_text})"
    )


# ============================================================
# FORMATO DEL MENSAJE
# ============================================================

def agregar_ranking(
    lines: list[str],
    title: str,
    counter: Counter,
    *,
    limit: int = 5,
    values_are_money: bool = False,
) -> None:
    lines.append("")
    lines.append(title)

    filtered = [
        (key, value)
        for key, value in counter.most_common(limit)
        if normalizar(key) not in {
            "",
            "sin especificar",
            "sin sabor",
            "sabor",
        }
    ]

    if not filtered:
        lines.append("• Sin datos")
        return

    medals = ["🥇", "🥈", "🥉"]

    for index, (key, value) in enumerate(filtered):
        prefix = (
            medals[index]
            if index < len(medals)
            else f"{index + 1}."
        )

        value_text = (
            moneda(float(value))
            if values_are_money
            else f"{int(value)} unidades"
        )

        lines.append(
            f"{prefix} {key}: {value_text}"
        )


def generar_resumen_ventas(
    consulta: str = "este mes",
) -> str:
    periodo = resolver_periodo(consulta)
    all_sales = leer_ventas()

    current = calcular_metricas(
        all_sales,
        periodo.inicio,
        periodo.fin,
    )

    previous_period = periodo_anterior(periodo)
    previous = calcular_metricas(
        all_sales,
        previous_period.inicio,
        previous_period.fin,
    )

    fixed_expenses, fixed_note = gastos_fijos_del_periodo(
        periodo
    )

    net_gain = current.ganancia_bruta - fixed_expenses
    net_margin = (
        net_gain / current.facturacion * 100
        if current.facturacion
        else 0.0
    )

    lines = [
        "📊 RESUMEN DE VENTAS",
        f"📅 {periodo.descripcion}",
        (
            f"🗓️ {periodo.inicio.strftime('%d/%m/%Y')} "
            f"al {periodo.fin.strftime('%d/%m/%Y')}"
        ),
        "",
        "💰 RESULTADOS",
        f"• Facturación total: {moneda(current.facturacion)}",
        f"• Costo de mercadería: {moneda(current.costo)}",
        f"• Ganancia bruta: {moneda(current.ganancia_bruta)}",
        f"• Margen bruto: {porcentaje(current.margen)}",
        f"• Gastos fijos: {moneda(fixed_expenses)}",
        f"• Ganancia neta: {moneda(net_gain)}",
        f"• Margen neto: {porcentaje(net_margin)}",
        "",
        "🛒 ACTIVIDAD",
        f"• Cantidad de ventas: {current.ventas}",
        f"• Unidades vendidas: {current.unidades}",
        f"• Clientes únicos: {current.clientes_unicos}",
        f"• Ticket promedio: {moneda(current.ticket_promedio)}",
        (
            "• Ganancia promedio por venta: "
            f"{moneda(current.ganancia_promedio)}"
        ),
    ]

    agregar_ranking(
        lines,
        "🏆 PRODUCTOS MÁS VENDIDOS",
        current.productos,
    )

    agregar_ranking(
        lines,
        "🍓 SABORES MÁS VENDIDOS",
        current.sabores,
    )

    agregar_ranking(
        lines,
        "📱 FACTURACIÓN POR PLATAFORMA",
        current.plataformas,
        values_are_money=True,
    )

    agregar_ranking(
        lines,
        "💳 FACTURACIÓN POR FORMA DE PAGO",
        current.pagos,
        values_are_money=True,
    )

    lines.extend(
        [
            "",
            "📈 COMPARACIÓN CON EL PERÍODO ANTERIOR",
            (
                f"📅 {previous_period.inicio.strftime('%d/%m/%Y')} "
                f"al {previous_period.fin.strftime('%d/%m/%Y')}"
            ),
            linea_variacion(
                "Ventas",
                current.ventas,
                previous.ventas,
            ),
            linea_variacion(
                "Unidades",
                current.unidades,
                previous.unidades,
            ),
            linea_variacion(
                "Facturación",
                current.facturacion,
                previous.facturacion,
                money=True,
            ),
            linea_variacion(
                "Ganancia bruta",
                current.ganancia_bruta,
                previous.ganancia_bruta,
                money=True,
            ),
            linea_variacion(
                "Ticket promedio",
                current.ticket_promedio,
                previous.ticket_promedio,
                money=True,
            ),
        ]
    )

    if fixed_note:
        lines.extend(
            [
                "",
                f"ℹ️ Gastos fijos: {fixed_note}.",
            ]
        )

    if current.ventas == 0:
        lines.extend(
            [
                "",
                "No se encontraron ventas dentro de este período.",
            ]
        )

    return "\n".join(lines)


# Alias corto para que otro módulo pueda importarlo.
generate_sales_report = generar_resumen_ventas


# ============================================================
# TELEGRAM OPCIONAL
# ============================================================

def enviar_telegram(
    mensaje: str,
    *,
    token: str,
    chat_id: str,
) -> None:
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": mensaje,
            "disable_web_page_preview": True,
        },
        timeout=30,
    )

    response.raise_for_status()


# ============================================================
# CLI
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Genera un resumen de ventas desde Google Sheets."
        )
    )

    parser.add_argument(
        "periodo",
        nargs="*",
        help=(
            "Ejemplo: este mes, esta semana, junio 2026, "
            "últimos 30 días o 01/07/2026 al 15/07/2026."
        ),
    )

    parser.add_argument(
        "--telegram",
        action="store_true",
        help=(
            "Envía el resumen a Telegram. Usa las variables "
            "TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID."
        ),
    )

    parser.add_argument(
        "--token",
        help="Token de Telegram. Tiene prioridad sobre la variable de entorno.",
    )

    parser.add_argument(
        "--chat-id",
        help="Chat ID de Telegram. Tiene prioridad sobre la variable de entorno.",
    )

    args = parser.parse_args()

    query = " ".join(args.periodo).strip() or "este mes"

    try:
        message = generar_resumen_ventas(query)
        print(message)

        if args.telegram:
            token = (
                args.token
                or os.environ.get("TELEGRAM_BOT_TOKEN")
                or os.environ.get("TELEGRAM_TOKEN")
            )

            chat_id = (
                args.chat_id
                or os.environ.get("TELEGRAM_CHAT_ID")
            )

            if not token or not chat_id:
                raise RuntimeError(
                    "Para enviar a Telegram necesitás definir "
                    "TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID, "
                    "o pasar --token y --chat-id."
                )

            enviar_telegram(
                message,
                token=token,
                chat_id=chat_id,
            )

            print("\n✅ Resumen enviado a Telegram.")

        return 0

    except Exception as error:
        print(
            f"❌ No se pudo generar el resumen: {error}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
