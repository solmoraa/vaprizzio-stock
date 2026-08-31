#!/usr/bin/env python3
"""Valida en una sola lectura los modelos y sabores del registrador de ventas."""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from agente_vaprizzio import normalizar_modelo  # noqa: E402
from tiendanube.app.business.products import consultar_stock  # noqa: E402


def key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join(
        "".join(char for char in text if not unicodedata.combining(char))
        .casefold()
        .split()
    )


def first(row: dict[str, Any], *names: str) -> Any:
    normalized = {key(name).replace(" ", ""): value for name, value in row.items()}
    for name in names:
        value = normalized.get(key(name).replace(" ", ""))
        if value not in (None, ""):
            return value
    return None


def combine_model(row: dict[str, Any]) -> Any:
    brand = first(row, "marca", "brand")
    model = first(row, "modelo", "model")
    product = first(
        row,
        "producto",
        "product",
        "nombre producto",
        "product name",
    )
    if product not in (None, ""):
        return product
    if brand not in (None, "") and model not in (None, ""):
        brand_text = str(brand).strip()
        model_text = str(model).strip()
        if key(model_text) in key(brand_text):
            return brand_text
        if key(brand_text) in key(model_text):
            return model_text
        return f"{brand_text} {model_text}"
    return model or brand


def collect_rows(
    value: Any,
    found: list[tuple[str, str]],
    inherited_model: str | None = None,
) -> None:
    if isinstance(value, dict):
        explicit_model = combine_model(value)
        generic_name = first(value, "nombre", "name")
        flavor = first(
            value,
            "sabor",
            "gusto",
            "variante",
            "variant name",
            "nombre variante",
        )
        model = explicit_model or inherited_model
        if model in (None, "") and flavor not in (None, ""):
            model = generic_name
        elif inherited_model and explicit_model in (None, "") and flavor in (None, ""):
            # En algunas respuestas de Tiendanube la variante solo trae `name`.
            flavor = generic_name
        if model not in (None, "") and flavor not in (None, ""):
            found.append((str(model).strip(), str(flavor).strip()))
        child_model = (
            str(explicit_model).strip()
            if explicit_model not in (None, "")
            else (
                str(generic_name).strip()
                if inherited_model is None
                and generic_name not in (None, "")
                and flavor in (None, "")
                else inherited_model
            )
        )
        for child in value.values():
            collect_rows(child, found, child_model)
    elif isinstance(value, (list, tuple)):
        for child in value:
            collect_rows(child, found, inherited_model)


def read_catalog_once() -> Any:
    # La funcion del sistema acepta los mismos tres argumentos que utiliza el
    # dispatcher. None solicita el listado general sin filtrar por producto.
    return consultar_stock(marca=None, sabor=None, solo_disponibles=False)


def resolve_requested(
    rows: list[tuple[str, str]],
    model: str,
    flavors: list[str],
) -> dict[str, Any]:
    normalized_model = str(normalizar_modelo(model) or "").strip()
    model_rows = [row for row in rows if key(row[0]) == key(normalized_model)]
    available_models = sorted({row[0] for row in rows}, key=key)
    if not model_rows:
        return {
            "ok": False,
            "modelo_solicitado": model,
            "modelo_normalizado": normalized_model,
            "error": "modelo_no_encontrado",
            "modelos_disponibles": available_models,
        }

    canonical_models = sorted({row[0] for row in model_rows}, key=key)
    if len({key(value) for value in canonical_models}) != 1:
        return {
            "ok": False,
            "modelo_solicitado": model,
            "error": "modelo_ambiguo",
            "coincidencias": canonical_models,
        }

    available_flavors = sorted({row[1] for row in model_rows}, key=key)
    requested_results = []
    missing = []
    for flavor in flavors:
        matches = [value for value in available_flavors if key(value) == key(flavor)]
        if not matches:
            missing.append(flavor)
        else:
            requested_results.append(
                {"sabor_solicitado": flavor, "sabor_catalogo": matches[0]}
            )

    return {
        "ok": not missing,
        "modelo_solicitado": model,
        "modelo_normalizado": normalized_model,
        "modelo_catalogo": canonical_models[0],
        "sabores": requested_results,
        "sabores_no_encontrados": missing,
        "sabores_disponibles_del_modelo": available_flavors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Auditoria de solo lectura del catalogo administrativo."
    )
    parser.add_argument("--modelo")
    parser.add_argument("--sabor", action="append", default=[])
    args = parser.parse_args()
    if args.sabor and not args.modelo:
        parser.error("--sabor requiere --modelo")

    try:
        raw = read_catalog_once()
        rows: list[tuple[str, str]] = []
        collect_rows(raw, rows)
        unique_rows = sorted(set(rows), key=lambda item: (key(item[0]), key(item[1])))
        if not unique_rows:
            raise RuntimeError(
                "La consulta general no devolvio filas de modelo y sabor; no se modifico nada."
            )

        altered = [
            {
                "modelo_en_hoja": model,
                "modelo_normalizado": normalizar_modelo(model),
                "sabor": flavor,
            }
            for model, flavor in unique_rows
            if key(normalizar_modelo(model)) != key(model)
        ]

        duplicate_keys: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for model, flavor in unique_rows:
            duplicate_keys.setdefault((key(model), key(flavor)), []).append((model, flavor))
        duplicates = [items for items in duplicate_keys.values() if len(items) > 1]

        requested = (
            resolve_requested(unique_rows, args.modelo, args.sabor)
            if args.modelo
            else None
        )
        agents_path = ROOT / "AGENTS.md"
        agents_text = agents_path.read_text(encoding="utf-8")
        prompt = {
            "caracteres": len(agents_text),
            "bytes_utf8": len(agents_text.encode("utf-8")),
            "menor_a_20000": (
                len(agents_text) < 20_000
                and len(agents_text.encode("utf-8")) < 20_000
            ),
        }
        result = {
            "ok": (
                not altered
                and not duplicates
                and prompt["menor_a_20000"]
                and (requested is None or requested["ok"])
            ),
            "solo_lectura": True,
            "filas_revisadas": len(unique_rows),
            "modelos_revisados": len({key(model) for model, _ in unique_rows}),
            "sabores_revisados": len({key(flavor) for _, flavor in unique_rows}),
            "modelos_alterados_por_alias": altered,
            "combinaciones_duplicadas": duplicates,
            "agents_md": prompt,
            "prueba_solicitada": requested,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "solo_lectura": True,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
