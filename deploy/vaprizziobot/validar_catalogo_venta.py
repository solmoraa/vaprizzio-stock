#!/usr/bin/env python3
"""Valida en una sola lectura los modelos y sabores del registrador de ventas."""

from __future__ import annotations

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


def collect_rows(value: Any, found: list[tuple[str, str]]) -> None:
    if isinstance(value, dict):
        model = first(value, "modelo", "marca", "producto", "nombre")
        flavor = first(value, "sabor", "gusto", "variante")
        if model not in (None, "") and flavor not in (None, ""):
            found.append((str(model).strip(), str(flavor).strip()))
        for child in value.values():
            collect_rows(child, found)
    elif isinstance(value, (list, tuple)):
        for child in value:
            collect_rows(child, found)


def read_catalog_once() -> Any:
    # La funcion del sistema acepta los mismos tres argumentos que utiliza el
    # dispatcher. None solicita el listado general sin filtrar por producto.
    return consultar_stock(marca=None, sabor=None, solo_disponibles=False)


def main() -> int:
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

        result = {
            "ok": not altered and not duplicates,
            "solo_lectura": True,
            "filas_revisadas": len(unique_rows),
            "modelos_revisados": len({key(model) for model, _ in unique_rows}),
            "sabores_revisados": len({key(flavor) for _, flavor in unique_rows}),
            "modelos_alterados_por_alias": altered,
            "combinaciones_duplicadas": duplicates,
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
