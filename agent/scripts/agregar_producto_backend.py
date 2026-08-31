#!/usr/bin/env python3

import argparse
import fcntl
import os
import calendar
import json
import re
import sys
import time
import unicodedata
from datetime import datetime
from contextlib import contextmanager
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

MESES = {
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

MESES_NUMERO = {
    nombre.lower(): numero
    for numero, nombre in MESES.items()
}


def responder(ok: bool, mensaje: str, **datos: Any) -> None:
    salida = {
        "ok": ok,
        "mensaje": mensaje,
        **datos,
    }

    print(
        json.dumps(
            salida,
            ensure_ascii=False,
            default=str,
        )
    )


def normalizar(valor: Any) -> str:
    texto = str(valor or "").strip().lower()
    texto = unicodedata.normalize("NFD", texto)

    texto = "".join(
        caracter
        for caracter in texto
        if unicodedata.category(caracter) != "Mn"
    )

    # Ignorar guiones, barras y signos.
    texto = re.sub(r"[-_/\\]+", " ", texto)
    texto = re.sub(r"[^a-z0-9 ]+", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()

    return texto
def numero(valor: Any) -> float | None:
    if isinstance(valor, (int, float)):
        return float(valor)

    texto = str(valor or "").strip()

    if not texto:
        return None

    if texto.startswith("="):
        return None

    texto = (
        texto
        .replace("$", "")
        .replace("ARS", "")
        .replace(" ", "")
    )

    if "," in texto and "." in texto:
        if texto.rfind(".") > texto.rfind(","):
            texto = texto.replace(",", "")
        else:
            texto = texto.replace(".", "").replace(",", ".")

    elif "," in texto:
        partes = texto.split(",")

        if len(partes[-1]) <= 2:
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")

    elif texto.count(".") == 1:
        izquierda, derecha = texto.split(".")

        if len(derecha) == 3:
            texto = izquierda + derecha

    try:
        return float(texto)
    except ValueError:
        return None


def entero(valor: Any) -> int:
    resultado = numero(valor)

    if resultado is None:
        raise ValueError(f"No se pudo interpretar como número: {valor}")

    return int(resultado)



LOCK_FILE = Path("/tmp/vaprizzio_google_sheets.lock")
LAST_CALL_FILE = Path("/tmp/vaprizzio_google_sheets_last_call.txt")

# Google permite varias solicitudes, pero usamos un margen conservador
# para evitar el error 429.
MIN_SECONDS_BETWEEN_CALLS = 1.25


@contextmanager
def bloqueo_google_sheets():
    LOCK_FILE.touch(exist_ok=True)

    with LOCK_FILE.open("r+") as archivo_lock:
        fcntl.flock(archivo_lock.fileno(), fcntl.LOCK_EX)

        try:
            yield
        finally:
            fcntl.flock(archivo_lock.fileno(), fcntl.LOCK_UN)


def esperar_turno_google() -> None:
    ahora = time.time()
    ultima_llamada = 0.0

    try:
        ultima_llamada = float(
            LAST_CALL_FILE.read_text(encoding="utf-8").strip()
        )
    except (FileNotFoundError, ValueError):
        pass

    diferencia = ahora - ultima_llamada
    espera = MIN_SECONDS_BETWEEN_CALLS - diferencia

    if espera > 0:
        time.sleep(espera)


def registrar_llamada_google() -> None:
    LAST_CALL_FILE.write_text(
        str(time.time()),
        encoding="utf-8",
    )


def ejecutar(
    funcion: Callable,
    *args,
    **kwargs,
):
    """
    Serializa las solicitudes a Google Sheets, limita su velocidad
    y reintenta automáticamente los errores temporales.
    """

    esperas_por_cuota = [65, 90, 120]
    esperas_temporales = [3, 8, 15]

    intento_cuota = 0
    intento_temporal = 0

    while True:
        try:
            with bloqueo_google_sheets():
                esperar_turno_google()

                try:
                    resultado = funcion(*args, **kwargs)
                finally:
                    registrar_llamada_google()

            return resultado

        except APIError as error:
            codigo = getattr(
                getattr(error, "response", None),
                "status_code",
                None,
            )

            mensaje = str(error)

            es_limite = (
                codigo == 429
                or "429" in mensaje
                or "Quota exceeded" in mensaje
                or "RESOURCE_EXHAUSTED" in mensaje
                or "rateLimitExceeded" in mensaje
            )

            es_temporal = (
                codigo in {500, 502, 503, 504}
                or "backendError" in mensaje
                or "internalError" in mensaje
            )

            if es_limite:
                if intento_cuota >= len(esperas_por_cuota):
                    raise

                espera = esperas_por_cuota[intento_cuota]
                intento_cuota += 1

                print(
                    "Google Sheets alcanzó temporalmente su límite. "
                    f"Esperando {espera} segundos antes de reintentar "
                    f"({intento_cuota}/{len(esperas_por_cuota)})...",
                    file=sys.stderr,
                    flush=True,
                )

                time.sleep(espera)
                continue

            if es_temporal:
                if intento_temporal >= len(esperas_temporales):
                    raise

                espera = esperas_temporales[intento_temporal]
                intento_temporal += 1

                print(
                    "Google Sheets presentó un error temporal. "
                    f"Reintentando en {espera} segundos...",
                    file=sys.stderr,
                    flush=True,
                )

                time.sleep(espera)
                continue

            raise


def conectar() -> gspread.Spreadsheet:
    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError(
            f"No existe {CREDENTIALS_FILE}"
        )

    if not SHEET_ID_FILE.exists():
        raise FileNotFoundError(
            f"No existe {SHEET_ID_FILE}"
        )

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


def buscar_fila_encabezados(
    valores: list[list[Any]],
    requeridos: dict[str, set[str]],
    max_filas: int = 30,
) -> tuple[int, dict[str, int]]:
    for numero_fila, fila in enumerate(
        valores[:max_filas],
        start=1,
    ):
        columnas: dict[str, int] = {}

        for clave, nombres in requeridos.items():
            nombres_normalizados = {
                normalizar(nombre)
                for nombre in nombres
            }

            for numero_columna, valor in enumerate(
                fila,
                start=1,
            ):
                if normalizar(valor) in nombres_normalizados:
                    columnas[clave] = numero_columna
                    break

        if len(columnas) == len(requeridos):
            return numero_fila, columnas

    encontrados = []

    for fila in valores[:max_filas]:
        encontrados.extend(
            str(valor)
            for valor in fila
            if str(valor).strip()
        )

    raise RuntimeError(
        "No se encontraron los encabezados esperados. "
        f"Valores detectados: {encontrados[:50]}"
    )


def valor_fila(
    fila: list[Any],
    columna: int,
) -> Any:
    if columna <= 0 or len(fila) < columna:
        return ""

    return fila[columna - 1]


def formato_pesos(
    hoja: gspread.Worksheet,
    columnas: list[int],
    fila_inicial: int,
    fila_final: int = 1000,
) -> list[dict]:
    solicitudes = []

    for columna in columnas:
        solicitudes.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": hoja.id,
                        "startRowIndex": fila_inicial - 1,
                        "endRowIndex": max(
                            fila_final,
                            fila_inicial,
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


PRODUCTOS_HEADERS = {
    "marca": {"Marca", "Producto", "Vape"},
    "sabor": {"Sabor", "Gusto"},
    "stock": {"Stock", "Cantidad"},
    "costo": {"Costo", "Precio compra"},
    "precio": {
        "Precio venta",
        "Precio de venta",
        "Precio",
    },
    "ganancia": {
        "Ganancia",
        "Ganancia unitaria",
    },
}


VENTAS_HEADERS = {
    "orden": {"Orden", "N° Orden", "Numero de orden"},
    "cantidad": {"Cantidad"},
    "cliente": {"Cliente"},
    "marca": {"Vape", "Marca", "Producto"},
    "sabor": {"Sabor", "Gusto"},
    "estado": {"Estado"},
    "fecha": {
        "Fecha del pedido",
        "Fecha",
    },
    "precio": {
        "Precio Venta",
        "Precio venta",
        "Precio",
    },
    "plataforma": {
        "Plataforma de ventas",
        "Plataforma",
    },
    "ganancia": {"Ganancia"},
    "pago": {
        "Forma de pago",
        "Pago",
    },
}


def obtener_productos(
    libro: gspread.Spreadsheet,
):
    hoja = ejecutar(libro.worksheet, "Productos")
    valores = ejecutar(hoja.get_all_values)

    fila_header, columnas = buscar_fila_encabezados(
        valores,
        PRODUCTOS_HEADERS,
    )

    return hoja, valores, fila_header, columnas


def buscar_producto(
    valores: list[list[Any]],
    fila_header: int,
    columnas: dict[str, int],
    marca: str,
    sabor: str,
) -> tuple[int, list[Any]] | None:
    marca_buscada = normalizar(marca)
    sabor_buscado = normalizar(sabor)

    coincidencias_exactas = []
    coincidencias_flexibles = []

    for numero_fila in range(
        fila_header + 1,
        len(valores) + 1,
    ):
        fila = valores[numero_fila - 1]

        marca_actual = normalizar(
            valor_fila(
                fila,
                columnas["marca"],
            )
        )

        sabor_actual = normalizar(
            valor_fila(
                fila,
                columnas["sabor"],
            )
        )

        if not marca_actual and not sabor_actual:
            continue

        if (
            marca_actual == marca_buscada
            and sabor_actual == sabor_buscado
        ):
            coincidencias_exactas.append(
                (numero_fila, fila)
            )
            continue

        marca_coincide = (
            marca_buscada in marca_actual
            or marca_actual in marca_buscada
        )

        sabor_coincide = (
            sabor_buscado in sabor_actual
            or sabor_actual in sabor_buscado
        )

        if marca_coincide and sabor_coincide:
            coincidencias_flexibles.append(
                (numero_fila, fila)
            )

    if coincidencias_exactas:
        return coincidencias_exactas[0]

    if len(coincidencias_flexibles) == 1:
        return coincidencias_flexibles[0]

    if len(coincidencias_flexibles) > 1:
        raise RuntimeError(
            "Se encontraron varios productos parecidos. "
            "Indicá el modelo y sabor con más precisión."
        )

    return None
def primera_fila_vacia_productos(
    valores: list[list[Any]],
    fila_header: int,
    columnas: dict[str, int],
) -> int:
    for numero_fila in range(
        fila_header + 1,
        max(len(valores) + 2, fila_header + 2),
    ):
        fila = (
            valores[numero_fila - 1]
            if numero_fila <= len(valores)
            else []
        )

        marca = valor_fila(fila, columnas["marca"])
        sabor = valor_fila(fila, columnas["sabor"])

        if not str(marca).strip() and not str(sabor).strip():
            return numero_fila

    return len(valores) + 1


def ordenar_productos(
    libro: gspread.Spreadsheet,
    hoja: gspread.Worksheet,
    fila_header: int,
    columnas: dict[str, int],
    ultima_fila: int,
) -> None:
    primera_columna = min(columnas.values())
    ultima_columna = max(columnas.values())

    solicitudes = formato_pesos(
        hoja,
        [
            columnas["costo"],
            columnas["precio"],
            columnas["ganancia"],
        ],
        fila_header + 1,
    )

    if ultima_fila >= fila_header + 1:
        solicitudes.append(
            {
                "sortRange": {
                    "range": {
                        "sheetId": hoja.id,
                        "startRowIndex": fila_header,
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

    ejecutar(
        libro.batch_update,
        {"requests": solicitudes},
    )


def agregar_producto(args, libro) -> None:
    hoja, valores, fila_header, columnas = obtener_productos(
        libro
    )

    existente = buscar_producto(
        valores,
        fila_header,
        columnas,
        args.marca,
        args.sabor,
    )

    if existente:
        responder(
            False,
            "El producto ya existe.",
            marca=args.marca,
            sabor=args.sabor,
        )
        raise SystemExit(1)

    fila = primera_fila_vacia_productos(
        valores,
        fila_header,
        columnas,
    )

    costo = float(args.costo)
    precio = float(args.precio)
    ganancia = precio - costo

    actualizaciones = [
        {
            "range": rowcol_to_a1(
                fila,
                columnas["marca"],
            ),
            "values": [[args.marca]],
        },
        {
            "range": rowcol_to_a1(
                fila,
                columnas["sabor"],
            ),
            "values": [[args.sabor]],
        },
        {
            "range": rowcol_to_a1(
                fila,
                columnas["stock"],
            ),
            "values": [[int(args.stock)]],
        },
        {
            "range": rowcol_to_a1(
                fila,
                columnas["costo"],
            ),
            "values": [[costo]],
        },
        {
            "range": rowcol_to_a1(
                fila,
                columnas["precio"],
            ),
            "values": [[precio]],
        },
        {
            "range": rowcol_to_a1(
                fila,
                columnas["ganancia"],
            ),
            "values": [[ganancia]],
        },
    ]

    ejecutar(
        hoja.batch_update,
        actualizaciones,
        value_input_option="USER_ENTERED",
    )

    ultima_fila = max(
        fila,
        len(valores),
    )

    ordenar_productos(
        libro,
        hoja,
        fila_header,
        columnas,
        ultima_fila,
    )

    responder(
        True,
        "Producto agregado correctamente.",
        marca=args.marca,
        sabor=args.sabor,
        stock=int(args.stock),
        costo=costo,
        precio=precio,
        ganancia=ganancia,
    )


def modificar_producto(args, libro) -> None:
    """
    Modifica un producto existente.

    Permite:
    - reemplazar el stock con --stock;
    - sumar stock con --sumar-stock;
    - restar stock con --restar-stock;
    - modificar costo, precio, marca o sabor.

    Esta función no utiliza datos de ventas ni formas de pago.
    """

    hoja, valores, fila_header, columnas = obtener_productos(libro)

    encontrado = buscar_producto(
        valores,
        fila_header,
        columnas,
        args.marca,
        args.sabor,
    )

    if not encontrado:
        responder(
            False,
            "No se encontró el producto.",
            marca=args.marca,
            sabor=args.sabor,
        )
        raise SystemExit(1)

    fila_numero, fila_actual = encontrado

    stock_actual = entero(
        valor_fila(
            fila_actual,
            columnas["stock"],
        ) or 0
    )

    costo_actual = numero(
        valor_fila(
            fila_actual,
            columnas["costo"],
        )
    ) or 0.0

    precio_actual = numero(
        valor_fila(
            fila_actual,
            columnas["precio"],
        )
    ) or 0.0

    stock_nuevo = stock_actual

    if getattr(args, "stock", None) is not None:
        stock_nuevo = int(args.stock)

    if getattr(args, "sumar_stock", None) is not None:
        stock_nuevo += int(args.sumar_stock)

    if getattr(args, "restar_stock", None) is not None:
        stock_nuevo -= int(args.restar_stock)

    if stock_nuevo < 0:
        responder(
            False,
            "La modificación dejaría el stock en un valor negativo.",
            stock_actual=stock_actual,
            stock_resultante=stock_nuevo,
        )
        raise SystemExit(1)

    costo_nuevo = (
        float(args.costo)
        if getattr(args, "costo", None) is not None
        else costo_actual
    )

    precio_nuevo = (
        float(args.precio)
        if getattr(args, "precio", None) is not None
        else precio_actual
    )

    marca_nueva = (
        args.nueva_marca
        if getattr(args, "nueva_marca", None)
        else valor_fila(fila_actual, columnas["marca"])
    )

    sabor_nuevo = (
        args.nuevo_sabor
        if getattr(args, "nuevo_sabor", None)
        else valor_fila(fila_actual, columnas["sabor"])
    )

    hubo_cambio = any(
        [
            getattr(args, "stock", None) is not None,
            getattr(args, "sumar_stock", None) is not None,
            getattr(args, "restar_stock", None) is not None,
            getattr(args, "costo", None) is not None,
            getattr(args, "precio", None) is not None,
            getattr(args, "nueva_marca", None) is not None,
            getattr(args, "nuevo_sabor", None) is not None,
        ]
    )

    if not hubo_cambio:
        responder(
            False,
            "No se indicó ningún dato para modificar.",
        )
        raise SystemExit(1)

    ganancia_nueva = precio_nuevo - costo_nuevo

    actualizaciones = [
        {
            "range": rowcol_to_a1(
                fila_numero,
                columnas["marca"],
            ),
            "values": [[marca_nueva]],
        },
        {
            "range": rowcol_to_a1(
                fila_numero,
                columnas["sabor"],
            ),
            "values": [[sabor_nuevo]],
        },
        {
            "range": rowcol_to_a1(
                fila_numero,
                columnas["stock"],
            ),
            "values": [[stock_nuevo]],
        },
        {
            "range": rowcol_to_a1(
                fila_numero,
                columnas["costo"],
            ),
            "values": [[costo_nuevo]],
        },
        {
            "range": rowcol_to_a1(
                fila_numero,
                columnas["precio"],
            ),
            "values": [[precio_nuevo]],
        },
        {
            "range": rowcol_to_a1(
                fila_numero,
                columnas["ganancia"],
            ),
            "values": [[ganancia_nueva]],
        },
    ]

    ejecutar(
        hoja.batch_update,
        actualizaciones,
        value_input_option="USER_ENTERED",
    )

    ordenar_productos(
        libro,
        hoja,
        fila_header,
        columnas,
        max(len(valores), fila_numero),
    )

    responder(
        True,
        "Producto modificado correctamente.",
        marca=str(marca_nueva),
        sabor=str(sabor_nuevo),
        stock_anterior=stock_actual,
        stock=stock_nuevo,
        costo=costo_nuevo,
        precio=precio_nuevo,
        ganancia=ganancia_nueva,
    )
def consultar_stock(args, libro) -> None:
    _, valores, fila_header, columnas = obtener_productos(
        libro
    )

    resultados = []

    for numero_fila in range(
        fila_header + 1,
        len(valores) + 1,
    ):
        fila = valores[numero_fila - 1]

        marca = str(
            valor_fila(fila, columnas["marca"])
        ).strip()

        sabor = str(
            valor_fila(fila, columnas["sabor"])
        ).strip()

        if not marca and not sabor:
            continue

        if args.marca and normalizar(args.marca) not in normalizar(marca):
            continue

        if args.sabor and normalizar(args.sabor) not in normalizar(sabor):
            continue

        resultados.append(
            {
                "marca": marca,
                "sabor": sabor,
                "stock": entero(
                    valor_fila(
                        fila,
                        columnas["stock"],
                    )
                    or 0
                ),
                "costo": numero(
                    valor_fila(
                        fila,
                        columnas["costo"],
                    )
                ),
                "precio": numero(
                    valor_fila(
                        fila,
                        columnas["precio"],
                    )
                ),
            }
        )

    responder(
        True,
        "Consulta de stock realizada.",
        productos=resultados,
    )


def nombre_hoja_ventas(
    mes: int,
    anio: int,
) -> str:
    nombre_mes = MESES[mes]

    if anio <= 2026:
        return f"Ventas {nombre_mes}"

    return f"Ventas {nombre_mes} {anio}"


def obtener_hoja_ventas(
    libro: gspread.Spreadsheet,
    mes: int,
    anio: int,
):
    nombre = nombre_hoja_ventas(mes, anio)

    try:
        hoja = ejecutar(libro.worksheet, nombre)
    except gspread.WorksheetNotFound:
        raise RuntimeError(
            f"No existe la hoja {nombre}. "
            "Ejecutá crear-mes primero."
        )

    valores = ejecutar(hoja.get_all_values)

    fila_header, columnas = buscar_fila_encabezados(
        valores,
        VENTAS_HEADERS,
    )

    return hoja, valores, fila_header, columnas


def primera_fila_venta(
    valores: list[list[Any]],
    fila_header: int,
    columnas: dict[str, int],
) -> int:
    """
    Devuelve la fila inmediatamente posterior a la última venta real.

    Solamente considera ventas reales a las filas cuyo campo Orden
    sea un número. Ignora filas de plantilla que contengan textos como
    'Orden', 'Sabor', 'd/m/yyyy' o '$xx'.
    """

    ultima_fila_real = fila_header

    for numero_fila in range(
        fila_header + 1,
        len(valores) + 1,
    ):
        fila = valores[numero_fila - 1]

        orden = numero(
            valor_fila(
                fila,
                columnas["orden"],
            )
        )

        if orden is not None:
            ultima_fila_real = numero_fila

    return ultima_fila_real + 1


def proxima_orden(
    valores: list[list[Any]],
    fila_header: int,
    columnas: dict[str, int],
) -> int:
    ordenes = []

    for fila in valores[fila_header:]:
        valor = numero(
            valor_fila(
                fila,
                columnas["orden"],
            )
        )

        if valor is not None:
            ordenes.append(int(valor))

    return max(ordenes, default=0) + 1



def color_forma_pago(forma_pago: str) -> dict:
    """
    Devuelve el color que corresponde a la celda Forma de pago.

    - Mercado Pago Fabri: verde
    - Efectivo: amarillo
    - Falta pagar: rojo
    - Pago combinado: blanco
    """

    pago = normalizar(forma_pago)

    indicadores_combinados = [
        "+",
        " y ",
        "mitad",
        "50 50",
        "dos medios",
        "parte efectivo",
        "parte mercado",
    ]

    es_combinado = any(
        indicador in f" {pago} "
        for indicador in indicadores_combinados
    )

    tiene_efectivo = "efectivo" in pago
    tiene_mercado_pago = "mercado pago" in pago

    if tiene_efectivo and tiene_mercado_pago:
        es_combinado = True

    # Pago combinado: blanco.
    if es_combinado:
        return {
            "red": 1.0,
            "green": 1.0,
            "blue": 1.0,
        }

    # Falta pagar: rojo.
    if (
        "falta pagar" in pago
        or "pendiente de pago" in pago
        or "debe pagar" in pago
    ):
        return {
            "red": 0.85,
            "green": 0.05,
            "blue": 0.05,
        }

    # Efectivo: amarillo.
    if pago == "efectivo" or tiene_efectivo:
        return {
            "red": 1.0,
            "green": 0.9,
            "blue": 0.0,
        }

    # Mercado Pago Fabri/Favri: verde.
    if (
        "mercado pago fabri" in pago
        or "mercado pago favri" in pago
        or pago == "mercado pago"
    ):
        return {
            "red": 0.0,
            "green": 0.9,
            "blue": 0.0,
        }

    # Cualquier otro medio queda blanco.
    return {
        "red": 1.0,
        "green": 1.0,
        "blue": 1.0,
    }

def opcion_vape_existente(
    valores: list[list[Any]],
    fila_header: int,
    columnas: dict[str, int],
    marca: str,
) -> str:
    """
    Devuelve la escritura exacta que ya usa el desplegable de Vape.

    Ejemplo:
    recibe: Elfbar Ice King
    devuelve: ElfBar Ice king
    """

    buscada = normalizar(marca)
    variantes: dict[str, int] = {}

    for fila in valores[fila_header:]:
        orden = numero(
            valor_fila(
                fila,
                columnas["orden"],
            )
        )

        if orden is None:
            continue

        vape = str(
            valor_fila(
                fila,
                columnas["marca"],
            )
        ).strip()

        if not vape:
            continue

        if normalizar(vape) == buscada:
            variantes[vape] = variantes.get(vape, 0) + 1

    if not variantes:
        return marca

    # Elegir la variante más utilizada en las ventas anteriores.
    return max(
        variantes,
        key=lambda valor: variantes[valor],
    )
def registrar_venta(args, libro) -> None:
    forma_pago_normalizada = re.sub(
        r"\s+",
        " ",
        str(args.forma_pago or "").strip(),
    ).upper()

    # Unificar nombres habituales.
    pago_normalizado = normalizar(forma_pago_normalizada)

    if pago_normalizado == "mercado pago":
        forma_pago_normalizada = "MERCADO PAGO FABRI"

    elif pago_normalizado in {
        "falta pagar",
        "pendiente",
        "pendiente de pago",
    }:
        forma_pago_normalizada = "FALTA PAGAR"

    elif pago_normalizado == "efectivo":
        forma_pago_normalizada = "EFECTIVO"

    # La plataforma nunca debe inventarse.
    if not getattr(args, "plataforma", None):
        responder(
            False,
            "Falta indicar el medio por el que se realizó la venta.",
            dato_faltante="plataforma",
        )
        raise SystemExit(1)

    productos, valores_productos, fila_productos, columnas_productos = (
        obtener_productos(libro)
    )

    producto = buscar_producto(
        valores_productos,
        fila_productos,
        columnas_productos,
        args.marca,
        args.sabor,
    )

    if not producto:
        responder(
            False,
            "No se encontró el producto en Productos.",
            marca=args.marca,
            sabor=args.sabor,
        )
        raise SystemExit(1)

    fila_producto, datos_producto = producto

    # Utilizar exactamente los valores guardados en Productos.
    # Esto permite que Vape coincida con una opción válida del desplegable.
    marca_canonica = str(
        valor_fila(
            datos_producto,
            columnas_productos["marca"],
        )
    ).strip()

    sabor_canonico = str(
        valor_fila(
            datos_producto,
            columnas_productos["sabor"],
        )
    ).strip()

    if not marca_canonica:
        marca_canonica = args.marca

    if not sabor_canonico:
        sabor_canonico = args.sabor

    stock_actual = entero(
        valor_fila(
            datos_producto,
            columnas_productos["stock"],
        )
    )

    cantidad = int(args.cantidad)

    if cantidad <= 0:
        responder(
            False,
            "La cantidad debe ser mayor que cero.",
        )
        raise SystemExit(1)

    if stock_actual < cantidad:
        responder(
            False,
            "Stock insuficiente.",
            stock=stock_actual,
            cantidad=cantidad,
        )
        raise SystemExit(1)

    costo_unitario = numero(
        valor_fila(
            datos_producto,
            columnas_productos["costo"],
        )
    ) or 0

    precio_unitario = numero(
        valor_fila(
            datos_producto,
            columnas_productos["precio"],
        )
    ) or 0

    precio_total = (
        float(args.precio)
        if args.precio is not None
        else precio_unitario * cantidad
    )

    ganancia = precio_total - costo_unitario * cantidad

    fecha = (
        args.fecha
        if args.fecha
        else datetime.now().strftime("%d/%m/%Y")
    )

    try:
        fecha_objeto = datetime.strptime(
            fecha,
            "%d/%m/%Y",
        )
    except ValueError:
        responder(
            False,
            "La fecha debe tener formato día/mes/año.",
            fecha=fecha,
        )
        raise SystemExit(1)

    hoja, valores, fila_header, columnas = obtener_hoja_ventas(
        libro,
        fecha_objeto.month,
        fecha_objeto.year,
    )

    # Usar exactamente una opción existente del desplegable Vape.
    marca_para_venta = opcion_vape_existente(
        valores,
        fila_header,
        columnas,
        marca_canonica,
    )

    # Fila siguiente a la última venta con orden numérico.
    fila_venta = primera_fila_venta(
        valores,
        fila_header,
        columnas,
    )

    orden = proxima_orden(
        valores,
        fila_header,
        columnas,
    )

    estado = (
        args.estado.strip()
        if getattr(args, "estado", None)
        and args.estado.strip()
        else "Entregado"
    )

    cliente_limpio = re.sub(
        r"\s+",
        " ",
        str(args.cliente or "").strip(),
    )

    datos = {
        "orden": orden,
        "cantidad": cantidad,
        "cliente": cliente_limpio,
        "marca": marca_para_venta,
        "sabor": sabor_canonico,
        "estado": estado,
        "fecha": fecha,
        "precio": precio_total,
        "plataforma": args.plataforma,
        "ganancia": ganancia,
        "pago": forma_pago_normalizada,
    }

    # Escribir toda la venta en una única fila y una única solicitud.
    primera_columna = min(columnas.values())
    ultima_columna = max(columnas.values())

    fila_completa = [
        ""
        for _ in range(
            ultima_columna - primera_columna + 1
        )
    ]

    for clave, valor in datos.items():
        posicion = columnas[clave] - primera_columna
        fila_completa[posicion] = valor

    rango_fila = (
        f"{rowcol_to_a1(fila_venta, primera_columna)}:"
        f"{rowcol_to_a1(fila_venta, ultima_columna)}"
    )

    ejecutar(
        hoja.update,
        range_name=rango_fila,
        values=[fila_completa],
        value_input_option="USER_ENTERED",
    )

    # Descontar stock.
    ejecutar(
        productos.update,
        range_name=rowcol_to_a1(
            fila_producto,
            columnas_productos["stock"],
        ),
        values=[[stock_actual - cantidad]],
        value_input_option="USER_ENTERED",
    )

    # Aplicar moneda solamente a las columnas necesarias.
    solicitudes = formato_pesos(
        hoja,
        [
            columnas["precio"],
            columnas["ganancia"],
        ],
        fila_header + 1,
    )

    # Aplicar color exclusivamente a la celda Forma de pago.
    # No se modifica el formato del cliente ni de las demás columnas.
    solicitudes.append(
        {
            "repeatCell": {
                "range": {
                    "sheetId": hoja.id,
                    "startRowIndex": fila_venta - 1,
                    "endRowIndex": fila_venta,
                    "startColumnIndex": columnas["pago"] - 1,
                    "endColumnIndex": columnas["pago"],
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": color_forma_pago(
                            forma_pago_normalizada
                        )
                    }
                },
                "fields": (
                    "userEnteredFormat.backgroundColor"
                ),
            }
        }
    )

    ejecutar(
        libro.batch_update,
        {"requests": solicitudes},
    )

    # Actualizar únicamente la ganancia del mes afectado.
    actualizar_mes_ganancias(
        libro,
        fecha_objeto.month,
        fecha_objeto.year,
    )

    responder(
        True,
        "Venta registrada correctamente.",
        orden=orden,
        cliente=cliente_limpio,
        cantidad=cantidad,
        marca=marca_para_venta,
        sabor=sabor_canonico,
        estado=estado,
        fecha=fecha,
        precio=precio_total,
        plataforma=args.plataforma,
        ganancia=ganancia,
        forma_pago=forma_pago_normalizada,
        stock_restante=stock_actual - cantidad,
        fila=fila_venta,
    )
def total_gastos(libro: gspread.Spreadsheet) -> float:
    hoja = ejecutar(libro.worksheet, "Gastos Fijos")
    valores = ejecutar(hoja.get_all_values)

    fila_header, columnas = buscar_fila_encabezados(
        valores,
        {
            "monto": {
                "Gastos Fijos",
                "Gasto Fijo",
                "Monto",
                "Importe",
            },
            "motivo": {
                "Motivo",
                "Descripción",
                "Descripcion",
            },
        },
    )

    numeros = []

    for fila in valores[fila_header:]:
        motivo = str(
            valor_fila(
                fila,
                columnas["motivo"],
            )
        ).strip()

        monto = numero(
            valor_fila(
                fila,
                columnas["monto"],
            )
        )

        # El total amarillo no tiene motivo.
        if motivo and monto is not None:
            numeros.append(monto)

    return sum(numeros)


def suma_ganancias_ventas(
    libro: gspread.Spreadsheet,
    mes: int,
    anio: int,
) -> float:
    _, valores, fila_header, columnas = obtener_hoja_ventas(
        libro,
        mes,
        anio,
    )

    total = 0.0

    for fila in valores[fila_header:]:
        orden = numero(
            valor_fila(
                fila,
                columnas["orden"],
            )
        )

        ganancia = numero(
            valor_fila(
                fila,
                columnas["ganancia"],
            )
        )

        # Solo suma filas de ventas reales.
        if orden is not None and ganancia is not None:
            total += ganancia

    return total


def actualizar_mes_ganancias(
    libro: gspread.Spreadsheet,
    mes: int,
    anio: int,
) -> float:
    hoja = ejecutar(libro.worksheet, "Ganancias")
    valores = ejecutar(hoja.get_all_values)

    encabezado_esperado = normalizar(
        f"Ganancia {MESES[mes]} {anio}"
    )

    fila_titulo = None
    columna_mes = None

    for numero_fila, fila in enumerate(
        valores,
        start=1,
    ):
        for numero_columna, valor in enumerate(
            fila,
            start=1,
        ):
            if normalizar(valor) == encabezado_esperado:
                fila_titulo = numero_fila
                columna_mes = numero_columna
                break

        if columna_mes:
            break

    if columna_mes is None and anio == 2026:
        encabezado_sin_anio = normalizar(
            f"Ganancia {MESES[mes]}"
        )

        for numero_fila, fila in enumerate(
            valores,
            start=1,
        ):
            for numero_columna, valor in enumerate(
                fila,
                start=1,
            ):
                if normalizar(valor) == encabezado_sin_anio:
                    fila_titulo = numero_fila
                    columna_mes = numero_columna
                    break

            if columna_mes:
                break

    if columna_mes is None:
        raise RuntimeError(
            f"No se encontró Ganancia {MESES[mes]} {anio} "
            "en la hoja Ganancias."
        )

    ganancia_ventas = suma_ganancias_ventas(
        libro,
        mes,
        anio,
    )

    gastos = total_gastos(libro)
    ganancia_neta = ganancia_ventas - gastos
    fila_resultado = fila_titulo + 1

    ejecutar(
        hoja.update,
        range_name=rowcol_to_a1(
            fila_resultado,
            columna_mes,
        ),
        values=[[ganancia_neta]],
        value_input_option="USER_ENTERED",
    )

    solicitudes = formato_pesos(
        hoja,
        [columna_mes],
        fila_resultado,
        fila_resultado,
    )

    ejecutar(
        libro.batch_update,
        {"requests": solicitudes},
    )

    return ganancia_neta


def actualizar_ganancias(args, libro) -> None:
    ahora = datetime.now()

    mes = (
        MESES_NUMERO[normalizar(args.mes)]
        if args.mes
        else ahora.month
    )

    anio = args.anio or ahora.year

    resultado = actualizar_mes_ganancias(
        libro,
        mes,
        anio,
    )

    responder(
        True,
        "Ganancia mensual actualizada.",
        mes=MESES[mes],
        anio=anio,
        ganancia_neta=resultado,
    )


def actualizar_todas_ganancias(args, libro) -> None:
    hojas = {
        hoja.title
        for hoja in libro.worksheets()
    }

    resultados = {}

    for anio in range(2026, datetime.now().year + 1):
        for mes in range(1, 13):
            nombre = nombre_hoja_ventas(mes, anio)

            if nombre not in hojas:
                continue

            try:
                resultados[f"{MESES[mes]} {anio}"] = (
                    actualizar_mes_ganancias(
                        libro,
                        mes,
                        anio,
                    )
                )
            except Exception as error:
                resultados[f"{MESES[mes]} {anio}"] = (
                    f"ERROR: {error}"
                )

    responder(
        True,
        "Ganancias existentes actualizadas.",
        resultados=resultados,
    )


def agregar_gasto(args, libro) -> None:
    hoja = ejecutar(libro.worksheet, "Gastos Fijos")
    valores = ejecutar(hoja.get_all_values)

    fila_header, columnas = buscar_fila_encabezados(
        valores,
        {
            "monto": {
                "Gastos Fijos",
                "Gasto Fijo",
                "Monto",
                "Importe",
            },
            "motivo": {
                "Motivo",
                "Descripción",
                "Descripcion",
            },
        },
    )

    fila_destino = None
    fila_total = None

    for numero_fila in range(
        fila_header + 1,
        len(valores) + 1,
    ):
        fila = valores[numero_fila - 1]

        monto = valor_fila(
            fila,
            columnas["monto"],
        )
        motivo = valor_fila(
            fila,
            columnas["motivo"],
        )

        if numero(monto) is not None and not str(motivo).strip():
            fila_total = numero_fila
            break

        if (
            fila_destino is None
            and not str(monto).strip()
            and not str(motivo).strip()
        ):
            fila_destino = numero_fila

    if fila_destino is None:
        fila_destino = (
            fila_total - 1
            if fila_total
            else len(valores) + 1
        )

    ejecutar(
        hoja.batch_update,
        [
            {
                "range": rowcol_to_a1(
                    fila_destino,
                    columnas["monto"],
                ),
                "values": [[float(args.monto)]],
            },
            {
                "range": rowcol_to_a1(
                    fila_destino,
                    columnas["motivo"],
                ),
                "values": [[args.nombre]],
            },
        ],
        value_input_option="USER_ENTERED",
    )

    # Actualizar la celda amarilla del total.
    if fila_total:
        inicio = rowcol_to_a1(
            fila_header + 1,
            columnas["monto"],
        )
        fin = rowcol_to_a1(
            fila_total - 1,
            columnas["monto"],
        )

        ejecutar(
            hoja.update,
            range_name=rowcol_to_a1(
                fila_total,
                columnas["monto"],
            ),
            values=[[f"=SUM({inicio}:{fin})"]],
            value_input_option="USER_ENTERED",
        )

    solicitudes = formato_pesos(
        hoja,
        [columnas["monto"]],
        fila_header + 1,
    )

    ejecutar(
        libro.batch_update,
        {"requests": solicitudes},
    )

    responder(
        True,
        "Gasto fijo agregado.",
        nombre=args.nombre,
        monto=float(args.monto),
    )


def consultar_gastos(args, libro) -> None:
    hoja = ejecutar(libro.worksheet, "Gastos Fijos")
    valores = ejecutar(hoja.get_all_values)

    fila_header, columnas = buscar_fila_encabezados(
        valores,
        {
            "monto": {
                "Gastos Fijos",
                "Gasto Fijo",
                "Monto",
                "Importe",
            },
            "motivo": {
                "Motivo",
                "Descripción",
                "Descripcion",
            },
        },
    )

    gastos = []

    for fila in valores[fila_header:]:
        motivo = str(
            valor_fila(
                fila,
                columnas["motivo"],
            )
        ).strip()

        monto = numero(
            valor_fila(
                fila,
                columnas["monto"],
            )
        )

        if motivo and monto is not None:
            gastos.append(
                {
                    "motivo": motivo,
                    "monto": monto,
                }
            )

    responder(
        True,
        "Gastos consultados.",
        gastos=gastos,
        total=sum(
            gasto["monto"]
            for gasto in gastos
        ),
    )


def crear_mes(args, libro) -> None:
    ahora = datetime.now()

    mes = (
        MESES_NUMERO[normalizar(args.mes)]
        if args.mes
        else ahora.month
    )

    anio = args.anio or ahora.year
    nombre_nuevo = nombre_hoja_ventas(mes, anio)

    existentes = {
        hoja.title
        for hoja in libro.worksheets()
    }

    if nombre_nuevo in existentes:
        responder(
            True,
            "La hoja del mes ya existe.",
            hoja=nombre_nuevo,
        )
        return

    fecha_anterior = datetime(
        anio if mes > 1 else anio - 1,
        mes - 1 if mes > 1 else 12,
        1,
    )

    nombre_anterior = nombre_hoja_ventas(
        fecha_anterior.month,
        fecha_anterior.year,
    )

    if nombre_anterior not in existentes:
        raise RuntimeError(
            f"No existe {nombre_anterior} para usar como plantilla."
        )

    anterior = ejecutar(
        libro.worksheet,
        nombre_anterior,
    )

    respuesta = ejecutar(
        libro.duplicate_sheet,
        source_sheet_id=anterior.id,
        new_sheet_name=nombre_nuevo,
    )

    nueva = ejecutar(
        libro.worksheet,
        nombre_nuevo,
    )

    valores = ejecutar(nueva.get_all_values)

    fila_header, columnas = buscar_fila_encabezados(
        valores,
        VENTAS_HEADERS,
    )

    # Limpiar solamente las filas de ventas, conservando formato.
    rangos = []

    for clave, columna in columnas.items():
        rangos.append(
            f"{rowcol_to_a1(fila_header + 1, columna)}:"
            f"{rowcol_to_a1(nueva.row_count, columna)}"
        )

    ejecutar(nueva.batch_clear, rangos)

    responder(
        True,
        "Hoja mensual creada correctamente.",
        hoja=nombre_nuevo,
    )


def modificar_venta(args, libro) -> None:
    ahora = datetime.now()
    mes = (
        MESES_NUMERO[normalizar(args.mes)]
        if args.mes
        else ahora.month
    )
    anio = args.anio or ahora.year

    hoja, valores, fila_header, columnas = obtener_hoja_ventas(
        libro,
        mes,
        anio,
    )

    fila_encontrada = None

    for numero_fila in range(
        fila_header + 1,
        len(valores) + 1,
    ):
        fila = valores[numero_fila - 1]

        orden = numero(
            valor_fila(
                fila,
                columnas["orden"],
            )
        )

        if orden is not None and int(orden) == args.orden:
            fila_encontrada = numero_fila
            break

    if fila_encontrada is None:
        responder(
            False,
            "No se encontró la venta.",
            orden=args.orden,
        )
        raise SystemExit(1)

    actualizaciones = []

    mapa = {
        "cantidad": args.cantidad,
        "cliente": args.cliente,
        "marca": args.marca,
        "sabor": args.sabor,
        "estado": args.estado,
        "fecha": args.fecha,
        "precio": args.precio,
        "plataforma": args.plataforma,
        "pago": args.forma_pago,
    }

    for clave, valor in mapa.items():
        if valor is None:
            continue

        actualizaciones.append(
            {
                "range": rowcol_to_a1(
                    fila_encontrada,
                    columnas[clave],
                ),
                "values": [[valor]],
            }
        )

    if actualizaciones:
        ejecutar(
            hoja.batch_update,
            actualizaciones,
            value_input_option="USER_ENTERED",
        )

    actualizar_mes_ganancias(
        libro,
        mes,
        anio,
    )

    responder(
        True,
        "Venta modificada correctamente.",
        orden=args.orden,
    )


def eliminar_venta(args, libro) -> None:
    ahora = datetime.now()
    mes = (
        MESES_NUMERO[normalizar(args.mes)]
        if args.mes
        else ahora.month
    )
    anio = args.anio or ahora.year

    hoja, valores, fila_header, columnas = obtener_hoja_ventas(
        libro,
        mes,
        anio,
    )

    venta = None

    for numero_fila in range(
        fila_header + 1,
        len(valores) + 1,
    ):
        fila = valores[numero_fila - 1]

        orden = numero(
            valor_fila(
                fila,
                columnas["orden"],
            )
        )

        if orden is not None and int(orden) == args.orden:
            venta = numero_fila, fila
            break

    if not venta:
        responder(
            False,
            "No se encontró la venta.",
            orden=args.orden,
        )
        raise SystemExit(1)

    fila_venta, datos_venta = venta
    cantidad = entero(
        valor_fila(
            datos_venta,
            columnas["cantidad"],
        )
    )

    marca = str(
        valor_fila(
            datos_venta,
            columnas["marca"],
        )
    )

    sabor = str(
        valor_fila(
            datos_venta,
            columnas["sabor"],
        )
    )

    productos, valores_productos, fila_productos, columnas_productos = (
        obtener_productos(libro)
    )

    producto = buscar_producto(
        valores_productos,
        fila_productos,
        columnas_productos,
        marca,
        sabor,
    )

    if producto:
        fila_producto, datos_producto = producto

        stock = entero(
            valor_fila(
                datos_producto,
                columnas_productos["stock"],
            )
        )

        ejecutar(
            productos.update,
            range_name=rowcol_to_a1(
                fila_producto,
                columnas_productos["stock"],
            ),
            values=[[stock + cantidad]],
            value_input_option="USER_ENTERED",
        )

    primera_columna = min(columnas.values())
    ultima_columna = max(columnas.values())

    ejecutar(
        hoja.batch_clear,
        [
            f"{rowcol_to_a1(fila_venta, primera_columna)}:"
            f"{rowcol_to_a1(fila_venta, ultima_columna)}"
        ],
    )

    actualizar_mes_ganancias(
        libro,
        mes,
        anio,
    )

    responder(
        True,
        "Venta eliminada y stock restaurado.",
        orden=args.orden,
        marca=marca,
        sabor=sabor,
        cantidad_restaurada=cantidad,
    )


def crear_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(
        dest="comando",
        required=True,
    )

    p = sub.add_parser("agregar-producto")
    p.add_argument("--marca", required=True)
    p.add_argument("--sabor", required=True)
    p.add_argument("--stock", type=int, required=True)
    p.add_argument("--costo", type=float, required=True)
    p.add_argument("--precio", type=float, required=True)

    p = sub.add_parser("modificar-producto")
    p.add_argument("--marca", required=True)
    p.add_argument("--sabor", required=True)
    p.add_argument("--nueva-marca")
    p.add_argument("--nuevo-sabor")
    p.add_argument("--stock", type=int)
    p.add_argument("--costo", type=float)
    p.add_argument("--precio", type=float)
    p.add_argument(
        "--sumar-stock",
        dest="sumar_stock",
        type=int,
    )
    p.add_argument(
        "--restar-stock",
        dest="restar_stock",
        type=int,
    )

    p = sub.add_parser("consultar-stock")
    p.add_argument("--marca")
    p.add_argument("--sabor")

    p = sub.add_parser("registrar-venta")
    p.add_argument("--cantidad", type=int, required=True)
    p.add_argument("--cliente", required=True)
    p.add_argument("--marca", required=True)
    p.add_argument("--sabor", required=True)
    p.add_argument("--estado", default="Entregado")
    p.add_argument("--fecha")
    p.add_argument("--precio", type=float)
    p.add_argument("--plataforma", required=True)
    p.add_argument(
        "--forma-pago",
        "--pago",
        dest="forma_pago",
        required=True,
    )

    p = sub.add_parser("modificar-venta")
    p.add_argument("--orden", type=int, required=True)
    p.add_argument("--mes")
    p.add_argument("--anio", type=int)
    p.add_argument("--cantidad", type=int)
    p.add_argument("--cliente")
    p.add_argument("--marca")
    p.add_argument("--sabor")
    p.add_argument("--estado")
    p.add_argument("--fecha")
    p.add_argument("--precio", type=float)
    p.add_argument("--plataforma")
    p.add_argument(
        "--forma-pago",
        "--pago",
        dest="forma_pago",
    )

    p = sub.add_parser("eliminar-venta")
    p.add_argument("--orden", type=int, required=True)
    p.add_argument("--mes")
    p.add_argument("--anio", type=int)

    p = sub.add_parser("agregar-gasto")
    p.add_argument(
        "--nombre",
        "--motivo",
        dest="nombre",
        required=True,
    )
    p.add_argument("--monto", type=float, required=True)

    p = sub.add_parser("modificar-gasto")
    p.add_argument(
        "--nombre",
        "--motivo",
        dest="nombre",
        required=True,
    )
    p.add_argument("--nuevo-nombre")
    p.add_argument("--monto", type=float)

    sub.add_parser("consultar-gastos")

    p = sub.add_parser("actualizar-ganancias")
    p.add_argument("--mes")
    p.add_argument("--anio", type=int)

    sub.add_parser("actualizar-todas-ganancias")

    p = sub.add_parser("crear-mes")
    p.add_argument("--mes")
    p.add_argument("--anio", type=int)

    return parser


def main() -> int:
    parser = crear_parser()
    args = parser.parse_args()
    libro = conectar()

    funciones = {
        "agregar-producto": agregar_producto,
        "modificar-producto": modificar_producto,
        "consultar-stock": consultar_stock,
        "registrar-venta": registrar_venta,
        "modificar-venta": modificar_venta,
        "eliminar-venta": eliminar_venta,
        "agregar-gasto": agregar_gasto,
        "consultar-gastos": consultar_gastos,
        "actualizar-ganancias": actualizar_ganancias,
        "actualizar-todas-ganancias": actualizar_todas_ganancias,
        "crear-mes": crear_mes,
    }

    if args.comando == "modificar-gasto":
        responder(
            False,
            "La modificación de gastos se agregará después "
            "de comprobar el resto del sistema."
        )
        return 1

    try:
        funciones[args.comando](args, libro)
        return 0

    except Exception as error:
        responder(
            False,
            str(error),
            tipo=type(error).__name__,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
