#!/usr/bin/env python3

import argparse
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable

import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError
from gspread.utils import rowcol_to_a1


WORKSPACE = Path("/home/openclaw/.openclaw/workspace/vaprizziobot")
CREDENTIALS_FILE = (
    WORKSPACE / "credentials/vaprizzio-service-account.json"
)
SHEET_ID_FILE = WORKSPACE / "google_sheet_id.txt"


def normalizar(valor: Any) -> str:
    texto = str(valor or "").strip().lower()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(
        caracter
        for caracter in texto
        if unicodedata.category(caracter) != "Mn"
    )
    return re.sub(r"\s+", " ", texto)


def ejecutar_con_reintentos(
    funcion: Callable,
    *args,
    **kwargs,
):
    esperas = [65, 90, 120]

    for intento in range(len(esperas) + 1):
        try:
            return funcion(*args, **kwargs)

        except APIError as error:
            codigo = getattr(error.response, "status_code", None)
            mensaje = str(error)

            es_limite = (
                codigo == 429
                or "429" in mensaje
                or "Quota exceeded" in mensaje
                or "RESOURCE_EXHAUSTED" in mensaje
            )

            if not es_limite or intento >= len(esperas):
                raise

            espera = esperas[intento]

            print(
                f"Google Sheets alcanzó temporalmente su límite. "
                f"Esperando {espera} segundos antes de continuar...",
                file=sys.stderr,
                flush=True,
            )

            time.sleep(espera)


def buscar_columna(
    encabezados: list[Any],
    nombres_aceptados: set[str],
) -> int | None:
    aceptados = {normalizar(nombre) for nombre in nombres_aceptados}

    for indice, valor in enumerate(encabezados, start=1):
        if normalizar(valor) in aceptados:
            return indice

    return None


def buscar_encabezados(
    valores: list[list[Any]],
) -> tuple[int, dict[str, int]]:
    for numero_fila, fila in enumerate(valores[:15], start=1):
        columnas = {
            "marca": buscar_columna(
                fila,
                {"Marca", "Producto", "Vape"},
            ),
            "sabor": buscar_columna(
                fila,
                {"Sabor"},
            ),
            "stock": buscar_columna(
                fila,
                {"Stock"},
            ),
            "costo": buscar_columna(
                fila,
                {"Costo"},
            ),
            "precio": buscar_columna(
                fila,
                {
                    "Precio venta",
                    "Precio de venta",
                    "Precio",
                },
            ),
            "ganancia": buscar_columna(
                fila,
                {
                    "Ganancia",
                    "Ganancia unitaria",
                },
            ),
        }

        if all(columnas.values()):
            return numero_fila, columnas

    raise RuntimeError(
        "No se encontraron los encabezados Marca, Sabor, Stock, "
        "Costo, Precio venta y Ganancia en la hoja Productos."
    )


def formato_moneda(
    worksheet: gspread.Worksheet,
    fila_inicial: int,
    columnas: list[int],
) -> list[dict]:
    solicitudes = []

    for columna in columnas:
        solicitudes.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": fila_inicial - 1,
                        "endRowIndex": max(
                            worksheet.row_count,
                            fila_inicial + 500,
                        ),
                        "startColumnIndex": columna - 1,
                        "endColumnIndex": columna,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "numberFormat": {
                                "type": "CURRENCY",
                                "pattern": '"$"#,##0.00',
                            }
                        }
                    },
                    "fields": (
                        "userEnteredFormat.numberFormat"
                    ),
                }
            }
        )

    return solicitudes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--marca", required=False, default="")
    parser.add_argument("--sabor", required=False, default="")
    args = parser.parse_args()

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    credentials = Credentials.from_service_account_file(
        CREDENTIALS_FILE,
        scopes=scopes,
    )

    cliente = gspread.authorize(credentials)
    sheet_id = SHEET_ID_FILE.read_text(
        encoding="utf-8"
    ).strip()

    libro = ejecutar_con_reintentos(
        cliente.open_by_key,
        sheet_id,
    )

    productos = ejecutar_con_reintentos(
        libro.worksheet,
        "Productos",
    )

    # Una sola lectura de la hoja Productos.
    valores = ejecutar_con_reintentos(
        productos.get_all_values,
    )

    fila_encabezados, columnas = buscar_encabezados(valores)
    fila_inicial = fila_encabezados + 1

    marca_buscada = normalizar(args.marca)
    sabor_buscado = normalizar(args.sabor)

    filas_productos: list[int] = []

    for numero_fila in range(fila_inicial, len(valores) + 1):
        fila = valores[numero_fila - 1]

        marca = (
            fila[columnas["marca"] - 1]
            if len(fila) >= columnas["marca"]
            else ""
        )
        sabor = (
            fila[columnas["sabor"] - 1]
            if len(fila) >= columnas["sabor"]
            else ""
        )

        if str(marca).strip() or str(sabor).strip():
            filas_productos.append(numero_fila)

    if not filas_productos:
        print(
            "No hay productos para postprocesar.",
            flush=True,
        )
        return 0

    # Buscar específicamente el producto recién agregado o modificado.
    fila_objetivo = None

    if marca_buscada and sabor_buscado:
        for numero_fila in filas_productos:
            fila = valores[numero_fila - 1]

            marca = (
                fila[columnas["marca"] - 1]
                if len(fila) >= columnas["marca"]
                else ""
            )
            sabor = (
                fila[columnas["sabor"] - 1]
                if len(fila) >= columnas["sabor"]
                else ""
            )

            if (
                normalizar(marca) == marca_buscada
                and normalizar(sabor) == sabor_buscado
            ):
                fila_objetivo = numero_fila
                break

    # Si no se pudo identificar, calcular las fórmulas faltantes.
    filas_a_actualizar = []

    if fila_objetivo is not None:
        filas_a_actualizar.append(fila_objetivo)
    else:
        for numero_fila in filas_productos:
            fila = valores[numero_fila - 1]

            ganancia_actual = (
                fila[columnas["ganancia"] - 1]
                if len(fila) >= columnas["ganancia"]
                else ""
            )

            if not str(ganancia_actual).strip():
                filas_a_actualizar.append(numero_fila)

    actualizaciones = []

    for numero_fila in filas_a_actualizar:
        celda_costo = rowcol_to_a1(
            numero_fila,
            columnas["costo"],
        )
        celda_precio = rowcol_to_a1(
            numero_fila,
            columnas["precio"],
        )
        celda_ganancia = rowcol_to_a1(
            numero_fila,
            columnas["ganancia"],
        )

        actualizaciones.append(
            {
                "range": celda_ganancia,
                "values": [[f"={celda_precio}-{celda_costo}"]],
            }
        )

    if actualizaciones:
        ejecutar_con_reintentos(
            productos.batch_update,
            actualizaciones,
            value_input_option="USER_ENTERED",
        )

    ultima_fila = max(filas_productos)

    primera_columna = min(columnas.values())
    ultima_columna = max(columnas.values())

    solicitudes = []

    solicitudes.extend(
        formato_moneda(
            productos,
            fila_inicial,
            [
                columnas["costo"],
                columnas["precio"],
                columnas["ganancia"],
            ],
        )
    )

    # Ordenar todos los productos por marca y luego por sabor.
    solicitudes.append(
        {
            "sortRange": {
                "range": {
                    "sheetId": productos.id,
                    "startRowIndex": fila_inicial - 1,
                    "endRowIndex": ultima_fila,
                    "startColumnIndex": primera_columna - 1,
                    "endColumnIndex": ultima_columna,
                },
                "sortSpecs": [
                    {
                        "dimensionIndex": columnas["marca"] - 1,
                        "sortOrder": "ASCENDING",
                    },
                    {
                        "dimensionIndex": columnas["sabor"] - 1,
                        "sortOrder": "ASCENDING",
                    },
                ],
            }
        }
    )

    # Una única escritura para formato y ordenamiento.
    ejecutar_con_reintentos(
        libro.batch_update,
        {"requests": solicitudes},
    )

    print(
        "Productos actualizados: ganancia calculada, "
        "formato de pesos aplicado y orden por marca realizado.",
        flush=True,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
