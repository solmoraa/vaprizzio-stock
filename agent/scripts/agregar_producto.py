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


WORKSPACE = Path(
    "/home/openclaw/.openclaw/workspace/vaprizziobot"
)

TIENDANUBE_DIR = WORKSPACE / "tiendanube"

if str(TIENDANUBE_DIR) not in sys.path:
    sys.path.insert(0, str(TIENDANUBE_DIR))


from app.business.costs import (  # noqa: E402
    asegurar_costo_modelo,
    obtener_costo_modelo,
)
from app.sync_core import (  # noqa: E402
    SyncError,
    api_request,
    create_product,
    create_variant,
    find_product_by_name,
)


EXTENSIONES_PERMITIDAS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
}

MIME_PERMITIDOS = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
}

MAX_IMAGE_SIZE = 10 * 1024 * 1024


def responder(
    ok: bool,
    *,
    mensaje: str,
    **datos: Any,
) -> None:
    resultado = {
        "ok": ok,
        "mensaje": mensaje,
        **datos,
    }

    print(
        json.dumps(
            resultado,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )


def limpiar_texto(valor: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        str(valor or "").strip(),
    )


def separar_sabores(texto: str) -> list[str]:
    texto = limpiar_texto(texto)

    sabores: list[str] = []
    sabores_normalizados: set[str] = set()

    # Acepta comas, punto y coma o "|" como separadores.
    # OpenClaw debe usar "|" internamente para evitar que se
    # pierdan las comas al construir el comando.
    for parte in re.split(r"\s*[|,;]\s*", texto):
        sabor = limpiar_texto(parte)
        normalizado = sabor.casefold()

        if sabor and normalizado not in sabores_normalizados:
            sabores.append(sabor.upper())
            sabores_normalizados.add(normalizado)

    return sabores


def normalizar_ruta_imagen(valor: str) -> Path | None:
    """
    OpenClaw entrega la imagen de Telegram como una ruta local.

    También limpia casos donde la ruta pudiera venir:
    - entre comillas;
    - con prefijo file://;
    - acompañada por espacios.
    """

    valor = str(valor or "").strip()

    if not valor:
        return None

    valor = valor.strip("'\"")

    if valor.startswith("file://"):
        valor = valor[7:]

    return Path(valor).expanduser().resolve()


def validar_imagen(ruta: Path | None) -> Path:
    if ruta is None:
        raise SyncError(
            "Para crear un modelo nuevo tenés que adjuntar "
            "una imagen del producto."
        )

    if not ruta.exists():
        raise SyncError(
            f"No se encontró la imagen adjunta: {ruta}"
        )

    if not ruta.is_file():
        raise SyncError(
            "La ruta recibida no corresponde a una imagen."
        )

    extension = ruta.suffix.lower()

    if extension not in EXTENSIONES_PERMITIDAS:
        raise SyncError(
            "La imagen debe estar en formato JPG, PNG, GIF "
            "o WEBP."
        )

    mime_type, _ = mimetypes.guess_type(str(ruta))

    if mime_type not in MIME_PERMITIDOS:
        raise SyncError(
            "El archivo adjunto no parece ser una imagen válida."
        )

    tamanio = ruta.stat().st_size

    if tamanio <= 0:
        raise SyncError(
            "La imagen adjunta está vacía."
        )

    if tamanio >= MAX_IMAGE_SIZE:
        raise SyncError(
            "La imagen debe pesar menos de 10 MB."
        )

    return ruta


def subir_imagen_principal(
    *,
    product_id: str,
    ruta: Path,
    modelo: str,
) -> dict[str, Any]:
    """
    Sube la imagen a Tiendanube como primera imagen del producto.
    La posición 1 corresponde a la imagen principal.
    """

    contenido = ruta.read_bytes()
    attachment = base64.b64encode(contenido).decode("ascii")

    nombre_archivo = ruta.name

    if not nombre_archivo:
        extension = ruta.suffix.lower() or ".jpg"

        nombre_seguro = re.sub(
            r"[^a-zA-Z0-9_-]+",
            "-",
            modelo,
        ).strip("-")

        nombre_archivo = (
            f"{nombre_seguro or 'producto'}{extension}"
        )

    payload = {
        "filename": nombre_archivo,
        "position": 1,
        "attachment": attachment,
        "alt": modelo,
    }

    imagen = api_request(
        "POST",
        f"products/{product_id}/images",
        payload=payload,
    )

    image_id = str(imagen.get("id") or "").strip()
    image_url = str(imagen.get("src") or "").strip()

    if not image_id:
        raise SyncError(
            "Tiendanube no devolvió el ID de la imagen."
        )

    return {
        "image_id": image_id,
        "image_url": image_url,
        "image_position": imagen.get("position", 1),
        "image_filename": nombre_archivo,
    }


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Crear un modelo en Tiendanube, crear sus sabores "
            "como variantes y sincronizarlo con Google Sheets."
        )
    )

    parser.add_argument(
        "--marca",
        required=True,
        help="Nombre del modelo.",
    )

    parser.add_argument(
        "--sabor",
        required=True,
        help=(
            "Uno o más sabores. Para varios sabores, "
            "separarlos con comas."
        ),
    )

    parser.add_argument(
        "--stock",
        type=int,
        required=True,
        help="Stock inicial para cada sabor.",
    )

    parser.add_argument(
        "--costo",
        type=float,
        required=True,
    )

    parser.add_argument(
        "--precio",
        type=float,
        required=True,
    )

    parser.add_argument(
        "--sku",
        default="",
    )

    parser.add_argument(
        "--imagen",
        default="",
        help=(
            "Ruta local de la imagen adjunta. Es obligatoria "
            "cuando se crea un modelo nuevo."
        ),
    )

    parser.add_argument(
        "--no-publicar",
        action="store_true",
    )

    return parser


def main() -> int:
    args = construir_parser().parse_args()

    modelo = limpiar_texto(args.marca)
    sabores = separar_sabores(args.sabor)
    ruta_imagen = normalizar_ruta_imagen(args.imagen)

    if not modelo:
        responder(
            False,
            mensaje="El nombre del modelo está vacío.",
        )
        return 1

    if not sabores:
        responder(
            False,
            mensaje="No se recibió ningún sabor válido.",
        )
        return 1

    if args.stock < 0:
        responder(
            False,
            mensaje="El stock no puede ser negativo.",
        )
        return 1

    if args.costo < 0:
        responder(
            False,
            mensaje="El costo no puede ser negativo.",
        )
        return 1

    if args.precio < 0:
        responder(
            False,
            mensaje="El precio no puede ser negativo.",
        )
        return 1

    try:
        # Si el modelo ya existe en la tabla de costos, se reutiliza
        # su costo USDT. Solo se crea una fila nueva cuando todavía
        # no existe, evitando duplicados y costos diferentes por sabor.
        try:
            costo_info = obtener_costo_modelo(modelo)
        except Exception:
            costo_info = asegurar_costo_modelo(modelo, args.costo)

        costo_ars = float(costo_info["costo_ars"])
    except Exception as error:
        responder(False, mensaje=str(error))
        return 1

    resultados: list[dict[str, Any]] = []
    errores: list[dict[str, str]] = []
    informacion_imagen: dict[str, Any] | None = None

    try:
        producto_existente = find_product_by_name(modelo)
        es_modelo_nuevo = producto_existente is None

        # La imagen solamente es obligatoria cuando se crea
        # un producto/modelo que todavía no existe.
        if es_modelo_nuevo:
            ruta_imagen = validar_imagen(ruta_imagen)

        for indice, sabor in enumerate(sabores):
            try:
                if es_modelo_nuevo and indice == 0:
                    resultado = create_product(
                        model=modelo,
                        flavor=sabor,
                        stock=args.stock,
                        cost=costo_ars,
                        price=args.precio,
                        sku=args.sku,
                        published=not args.no_publicar,
                    )

                    product_id = str(
                        resultado.get("product_id") or ""
                    ).strip()

                    if not product_id:
                        raise SyncError(
                            "No se recibió el Product ID "
                            "del producto creado."
                        )

                    # Se carga una sola imagen para el producto,
                    # no una imagen por cada variante.
                    informacion_imagen = subir_imagen_principal(
                        product_id=product_id,
                        ruta=ruta_imagen,
                        modelo=modelo,
                    )

                    producto_existente = find_product_by_name(
                        modelo
                    )

                else:
                    resultado = create_variant(
                        model=modelo,
                        flavor=sabor,
                        stock=args.stock,
                        cost=costo_ars,
                        price=args.precio,
                        sku=(
                            args.sku
                            if indice == 0
                            and not es_modelo_nuevo
                            else ""
                        ),
                    )

                resultados.append(resultado)

            except SyncError as error:
                errores.append(
                    {
                        "sabor": sabor,
                        "error": str(error),
                    }
                )

                # Si falla la creación o la imagen principal del
                # primer sabor, no seguimos creando variantes.
                if es_modelo_nuevo and indice == 0:
                    break

            except Exception as error:
                errores.append(
                    {
                        "sabor": sabor,
                        "error": (
                            f"{type(error).__name__}: {error}"
                        ),
                    }
                )

                if es_modelo_nuevo and indice == 0:
                    break

        if not resultados:
            responder(
                False,
                mensaje=(
                    "No se pudo crear ninguna variante."
                ),
                modelo=modelo,
                errores=errores,
            )
            return 1

        product_ids = sorted(
            {
                str(resultado.get("product_id", ""))
                for resultado in resultados
                if resultado.get("product_id")
            }
        )

        operacion_completa = len(errores) == 0

        if es_modelo_nuevo and informacion_imagen is None:
            operacion_completa = False

        responder(
            operacion_completa,
            mensaje=(
                "Modelo, sabores e imagen creados "
                "y sincronizados."
                if operacion_completa and es_modelo_nuevo
                else (
                    "Sabores creados y sincronizados."
                    if operacion_completa
                    else (
                        "La operación se completó parcialmente. "
                        "Revisá los errores informados."
                    )
                )
            ),
            modelo=modelo,
            modelo_nuevo=es_modelo_nuevo,
            cantidad_creada=len(resultados),
            sabores_creados=[
                resultado.get("sabor")
                for resultado in resultados
            ],
            product_ids=product_ids,
            variantes=resultados,
            imagen=informacion_imagen,
            errores=errores,
            sincronizado_tiendanube=True,
            sincronizado_google_sheets=True,
        )

        return 0 if operacion_completa else 1

    except SyncError as error:
        responder(
            False,
            mensaje=str(error),
            modelo=modelo,
        )
        return 1

    except Exception as error:
        responder(
            False,
            mensaje=(
                f"Error inesperado: "
                f"{type(error).__name__}: {error}"
            ),
            modelo=modelo,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
