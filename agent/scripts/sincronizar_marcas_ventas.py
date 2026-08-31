#!/usr/bin/env python3

import json
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable

import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import APIError


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


def ejecutar(funcion: Callable, *args, **kwargs):
    esperas = [65, 90, 120]

    for intento in range(len(esperas) + 1):
        try:
            return funcion(*args, **kwargs)

        except APIError as error:
            codigo = getattr(error.response, "status_code", None)
            mensaje = str(error)

            es_limite = (
                codigo == 429
                or "Quota exceeded" in mensaje
                or "RESOURCE_EXHAUSTED" in mensaje
            )

            if not es_limite or intento >= len(esperas):
                raise

            espera = esperas[intento]
            print(
                f"Límite temporal de Google Sheets. "
                f"Esperando {espera} segundos...",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(espera)


def conectar() -> gspread.Spreadsheet:
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    credenciales = Credentials.from_service_account_file(
        CREDENTIALS_FILE,
        scopes=scopes,
    )

    cliente = gspread.authorize(credenciales)
    sheet_id = SHEET_ID_FILE.read_text(
        encoding="utf-8"
    ).strip()

    return ejecutar(cliente.open_by_key, sheet_id)


def buscar_columna_marca(
    valores: list[list[Any]],
) -> tuple[int, int]:
    nombres = {
        "marca",
        "producto",
        "vape",
    }

    for numero_fila, fila in enumerate(valores[:30], start=1):
        for numero_columna, valor in enumerate(fila, start=1):
            if normalizar(valor) in nombres:
                return numero_fila, numero_columna

    raise RuntimeError(
        "No se encontró la columna Marca en Productos."
    )


def obtener_marcas(
    libro: gspread.Spreadsheet,
) -> list[str]:
    hoja = ejecutar(libro.worksheet, "Productos")
    valores = ejecutar(hoja.get_all_values)

    fila_header, columna_marca = buscar_columna_marca(valores)

    marcas_unicas = {}

    for fila in valores[fila_header:]:
        marca = (
            str(fila[columna_marca - 1]).strip()
            if len(fila) >= columna_marca
            else ""
        )

        if not marca:
            continue

        clave = normalizar(marca)

        if clave not in marcas_unicas:
            marcas_unicas[clave] = marca

    return sorted(
        marcas_unicas.values(),
        key=normalizar,
    )


def obtener_o_crear_listas(
    libro: gspread.Spreadsheet,
) -> gspread.Worksheet:
    try:
        return ejecutar(libro.worksheet, "Listas")
    except gspread.WorksheetNotFound:
        return ejecutar(
            libro.add_worksheet,
            title="Listas",
            rows=500,
            cols=5,
        )


def sincronizar() -> None:
    libro = conectar()
    marcas = obtener_marcas(libro)
    listas = obtener_o_crear_listas(libro)

    ejecutar(listas.batch_clear, ["A1:A500"])

    valores = [["Marcas"]] + [[marca] for marca in marcas]

    ejecutar(
        listas.update,
        range_name=f"A1:A{len(valores)}",
        values=valores,
        value_input_option="USER_ENTERED",
    )

    try:
        ejecutar(listas.hide)
    except Exception:
        pass

    print(
        json.dumps(
            {
                "ok": True,
                "mensaje": (
                    "Lista de marcas actualizada correctamente."
                ),
                "cantidad_marcas": len(marcas),
                "rango": (
                    f"Listas!A2:A{len(marcas) + 1}"
                    if marcas
                    else "Listas!A2:A"
                ),
                "marcas": marcas,
            },
            ensure_ascii=False,
        )
    )


def main() -> int:
    try:
        sincronizar()
        return 0

    except Exception as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "mensaje": str(error),
                    "tipo": type(error).__name__,
                },
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
