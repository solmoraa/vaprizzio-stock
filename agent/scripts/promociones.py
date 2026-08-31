#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any


WORKSPACE = Path(
    "/home/openclaw/.openclaw/workspace/vaprizziobot"
)

TIENDANUBE_DIR = WORKSPACE / "tiendanube"

if str(TIENDANUBE_DIR) not in sys.path:
    sys.path.insert(0, str(TIENDANUBE_DIR))


from app.sync_core import (  # noqa: E402
    SyncError,
    api_request,
    find_product_by_name,
    localized_text,
    normalize,
)


def emitir(
    ok: bool,
    *,
    mensaje: str,
    codigo: int = 0,
    **datos: Any,
) -> None:
    print(
        json.dumps(
            {
                "ok": ok,
                "mensaje": mensaje,
                **datos,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )

    raise SystemExit(codigo)


def decimal_positivo(
    valor: Any,
    *,
    campo: str,
) -> Decimal:
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError):
        raise SyncError(
            f"El valor de {campo} no es válido."
        )

    if numero <= 0:
        raise SyncError(
            f"El valor de {campo} debe ser mayor que cero."
        )

    return numero


def formato_precio(valor: Decimal | str | float | int) -> str:
    numero = Decimal(str(valor)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )

    if numero == numero.to_integral():
        return str(int(numero))

    return format(numero, "f")


def nombre_producto(producto: dict[str, Any]) -> str:
    return localized_text(
        producto.get("name")
    ).strip()


def nombre_variante(variante: dict[str, Any]) -> str:
    valores = variante.get("values") or []
    nombres: list[str] = []

    for valor in valores:
        texto = localized_text(valor).strip()

        if texto:
            nombres.append(texto)

    return " / ".join(nombres) or "Variante única"


def buscar_variante(
    producto: dict[str, Any],
    sabor: str,
) -> dict[str, Any]:
    variantes = producto.get("variants") or []

    objetivo = normalize(sabor)

    exactas = [
        variante
        for variante in variantes
        if normalize(nombre_variante(variante)) == objetivo
    ]

    if len(exactas) == 1:
        return exactas[0]

    flexibles = [
        variante
        for variante in variantes
        if (
            objetivo in normalize(nombre_variante(variante))
            or normalize(nombre_variante(variante)) in objetivo
        )
    ]

    if len(flexibles) == 1:
        return flexibles[0]

    disponibles = [
        nombre_variante(variante)
        for variante in variantes
    ]

    if not flexibles:
        raise SyncError(
            f"No encontré el sabor '{sabor}'. "
            f"Sabores disponibles: {', '.join(disponibles)}"
        )

    raise SyncError(
        f"El sabor '{sabor}' coincide con más de una variante: "
        f"{', '.join(nombre_variante(v) for v in flexibles)}"
    )


def obtener_productos_tienda() -> list[dict[str, Any]]:
    productos: list[dict[str, Any]] = []
    pagina = 1

    while True:
        lote = api_request(
            "GET",
            "products",
            params={
                "page": pagina,
                "per_page": 100,
            },
        )

        if not lote:
            break

        productos.extend(lote)

        if len(lote) < 100:
            break

        pagina += 1

    return productos


def seleccionar_variantes(
    *,
    modelo: str | None,
    sabor: str | None,
    toda_tienda: bool,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    if toda_tienda:
        productos = obtener_productos_tienda()

        seleccionadas: list[
            tuple[dict[str, Any], dict[str, Any]]
        ] = []

        for producto in productos:
            for variante in producto.get("variants") or []:
                seleccionadas.append(
                    (
                        producto,
                        variante,
                    )
                )

        if not seleccionadas:
            raise SyncError(
                "La tienda no contiene variantes para modificar."
            )

        return seleccionadas

    if not modelo:
        raise SyncError(
            "Tenés que indicar --modelo o usar --toda-tienda."
        )

    producto = find_product_by_name(modelo)

    if not producto:
        raise SyncError(
            f"No encontré el modelo '{modelo}' en Tiendanube."
        )

    # Volvemos a consultar el producto para asegurarnos de
    # tener todas sus variantes y datos actualizados.
    producto = api_request(
        "GET",
        f"products/{producto['id']}",
    )

    variantes = producto.get("variants") or []

    if not variantes:
        raise SyncError(
            f"El modelo '{modelo}' no tiene variantes."
        )

    if sabor:
        variante = buscar_variante(
            producto,
            sabor,
        )

        return [
            (
                producto,
                variante,
            )
        ]

    return [
        (
            producto,
            variante,
        )
        for variante in variantes
    ]


def calcular_promocional(
    *,
    precio_normal: Decimal,
    porcentaje: Decimal | None,
    precio_directo: Decimal | None,
) -> Decimal:
    if porcentaje is not None:
        if porcentaje >= 100:
            raise SyncError(
                "El porcentaje debe ser menor que 100."
            )

        descuento = (
            precio_normal
            * porcentaje
            / Decimal("100")
        )

        promocional = precio_normal - descuento

    elif precio_directo is not None:
        promocional = precio_directo

    else:
        raise SyncError(
            "Falta indicar el porcentaje o el precio promocional."
        )

    promocional = promocional.quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    )

    if promocional <= 0:
        raise SyncError(
            "El precio promocional calculado no es válido."
        )

    if promocional >= precio_normal:
        raise SyncError(
            "El precio promocional debe ser menor "
            "que el precio normal."
        )

    return promocional


def modificar_variante(
    *,
    producto: dict[str, Any],
    variante: dict[str, Any],
    promocional: Decimal | None,
    simular: bool,
) -> dict[str, Any]:
    producto_id = str(producto.get("id") or "")
    variante_id = str(variante.get("id") or "")

    if not producto_id or not variante_id:
        raise SyncError(
            "Tiendanube devolvió una variante sin IDs."
        )

    precio_normal = decimal_positivo(
        variante.get("price"),
        campo="precio normal",
    )

    precio_promocional_anterior = (
        variante.get("promotional_price")
    )

    payload = {
        "promotional_price": (
            formato_precio(promocional)
            if promocional is not None
            else None
        )
    }

    if not simular:
        actualizada = api_request(
            "PUT",
            (
                f"products/{producto_id}/"
                f"variants/{variante_id}"
            ),
            payload=payload,
        )
    else:
        actualizada = {
            **variante,
            **payload,
        }

    return {
        "modelo": nombre_producto(producto),
        "sabor": nombre_variante(variante),
        "product_id": producto_id,
        "variant_id": variante_id,
        "precio_normal": formato_precio(precio_normal),
        "precio_promocional_anterior": (
            precio_promocional_anterior
        ),
        "precio_promocional": (
            actualizada.get("promotional_price")
        ),
        "simulado": simular,
    }


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Administrar promociones de Tiendanube."
        )
    )

    comandos = parser.add_subparsers(
        dest="comando",
        required=True,
    )

    aplicar = comandos.add_parser(
        "aplicar",
        help="Aplicar una promoción.",
    )

    aplicar.add_argument(
        "--modelo",
        help="Modelo al que se aplicará la promoción.",
    )

    aplicar.add_argument(
        "--sabor",
        help=(
            "Sabor específico. Si se omite, se aplica "
            "a todas las variantes del modelo."
        ),
    )

    aplicar.add_argument(
        "--toda-tienda",
        action="store_true",
        help="Aplicar a todos los productos de la tienda.",
    )

    grupo_precio = aplicar.add_mutually_exclusive_group(
        required=True,
    )

    grupo_precio.add_argument(
        "--porcentaje",
        type=str,
        help="Porcentaje de descuento, por ejemplo 20.",
    )

    grupo_precio.add_argument(
        "--precio-promocional",
        type=str,
        help="Precio promocional directo, por ejemplo 15000.",
    )

    aplicar.add_argument(
        "--simular",
        action="store_true",
        help="Mostrar cambios sin realizarlos.",
    )

    quitar = comandos.add_parser(
        "quitar",
        help="Quitar una promoción.",
    )

    quitar.add_argument(
        "--modelo",
        help="Modelo al que se quitará la promoción.",
    )

    quitar.add_argument(
        "--sabor",
        help=(
            "Sabor específico. Si se omite, se quita "
            "de todas las variantes del modelo."
        ),
    )

    quitar.add_argument(
        "--toda-tienda",
        action="store_true",
        help="Quitar todas las promociones de la tienda.",
    )

    quitar.add_argument(
        "--simular",
        action="store_true",
        help="Mostrar cambios sin realizarlos.",
    )

    listar = comandos.add_parser(
        "listar",
        help="Listar promociones activas.",
    )

    listar.add_argument(
        "--modelo",
        help="Limitar la consulta a un modelo.",
    )

    listar.add_argument(
        "--sabor",
        help="Limitar la consulta a un sabor.",
    )

    listar.add_argument(
        "--toda-tienda",
        action="store_true",
        help="Consultar toda la tienda.",
    )

    return parser


def main() -> None:
    args = construir_parser().parse_args()

    try:
        if (
            getattr(args, "toda_tienda", False)
            and getattr(args, "modelo", None)
        ):
            raise SyncError(
                "No combines --modelo con --toda-tienda."
            )

        if (
            getattr(args, "sabor", None)
            and not getattr(args, "modelo", None)
        ):
            raise SyncError(
                "Para usar --sabor también tenés que "
                "indicar --modelo."
            )

        seleccionadas = seleccionar_variantes(
            modelo=getattr(args, "modelo", None),
            sabor=getattr(args, "sabor", None),
            toda_tienda=getattr(
                args,
                "toda_tienda",
                False,
            ),
        )

        if args.comando == "listar":
            promociones: list[dict[str, Any]] = []

            for producto, variante in seleccionadas:
                promocional = variante.get(
                    "promotional_price"
                )

                if promocional in (
                    None,
                    "",
                    "0",
                    "0.00",
                ):
                    continue

                precio_normal = decimal_positivo(
                    variante.get("price"),
                    campo="precio normal",
                )

                precio_promo = decimal_positivo(
                    promocional,
                    campo="precio promocional",
                )

                porcentaje = (
                    (
                        precio_normal - precio_promo
                    )
                    * Decimal("100")
                    / precio_normal
                ).quantize(
                    Decimal("0.01"),
                    rounding=ROUND_HALF_UP,
                )

                promociones.append(
                    {
                        "modelo": nombre_producto(
                            producto
                        ),
                        "sabor": nombre_variante(
                            variante
                        ),
                        "precio_normal": formato_precio(
                            precio_normal
                        ),
                        "precio_promocional": (
                            formato_precio(precio_promo)
                        ),
                        "descuento_porcentaje": (
                            formato(porcentaje, "f")
                        ),
                    }
                )

            emitir(
                True,
                mensaje=(
                    "Promociones activas consultadas."
                ),
                cantidad=len(promociones),
                promociones=promociones,
            )

        resultados: list[dict[str, Any]] = []
        errores: list[dict[str, str]] = []

        porcentaje: Decimal | None = None
        precio_directo: Decimal | None = None

        if args.comando == "aplicar":
            if args.porcentaje is not None:
                porcentaje = decimal_positivo(
                    args.porcentaje,
                    campo="porcentaje",
                )

            if args.precio_promocional is not None:
                precio_directo = decimal_positivo(
                    args.precio_promocional,
                    campo="precio promocional",
                )

        for producto, variante in seleccionadas:
            try:
                if args.comando == "aplicar":
                    precio_normal = decimal_positivo(
                        variante.get("price"),
                        campo="precio normal",
                    )

                    promocional = calcular_promocional(
                        precio_normal=precio_normal,
                        porcentaje=porcentaje,
                        precio_directo=precio_directo,
                    )

                elif args.comando == "quitar":
                    promocional = None

                else:
                    raise SyncError(
                        "Comando no implementado."
                    )

                resultado = modificar_variante(
                    producto=producto,
                    variante=variante,
                    promocional=promocional,
                    simular=args.simular,
                )

                resultados.append(resultado)

            except Exception as error:
                errores.append(
                    {
                        "modelo": nombre_producto(
                            producto
                        ),
                        "sabor": nombre_variante(
                            variante
                        ),
                        "error": str(error),
                    }
                )

        if not resultados:
            emitir(
                False,
                mensaje=(
                    "No se pudo modificar ninguna variante."
                ),
                codigo=1,
                errores=errores,
            )

        operacion = (
            "Promoción aplicada"
            if args.comando == "aplicar"
            else "Promoción eliminada"
        )

        emitir(
            len(errores) == 0,
            mensaje=(
                f"{operacion} correctamente."
                if not errores
                else (
                    f"{operacion} parcialmente. "
                    "Algunas variantes tuvieron errores."
                )
            ),
            codigo=0 if not errores else 1,
            cantidad_modificada=len(resultados),
            modificaciones=resultados,
            errores=errores,
            simulado=args.simular,
        )

    except SyncError as error:
        emitir(
            False,
            mensaje=str(error),
            codigo=1,
        )

    except Exception as error:
        emitir(
            False,
            mensaje=(
                f"Error inesperado: "
                f"{type(error).__name__}: {error}"
            ),
            codigo=1,
        )


if __name__ == "__main__":
    main()
