#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


WORKSPACE = Path(
    "/home/openclaw/.openclaw/workspace/vaprizziobot"
)

PYTHON = WORKSPACE / ".venv" / "bin" / "python"
AGENT_BACKEND = (
    WORKSPACE / "scripts" / "agente_vaprizzio.py"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Registrar una venta de Vaprizzio."
    )

    parser.add_argument(
        "--cantidad",
        type=int,
        required=True,
    )
    parser.add_argument(
        "--cliente",
        required=True,
    )
    parser.add_argument(
        "--marca",
        required=True,
    )
    parser.add_argument(
        "--sabor",
        required=True,
    )
    parser.add_argument(
        "--estado",
        default="Entregado",
    )
    parser.add_argument(
        "--fecha",
    )
    parser.add_argument(
        "--precio",
        type=float,
    )
    parser.add_argument(
        "--plataforma",
        required=True,
    )
    parser.add_argument(
        "--forma-pago",
        "--pago",
        dest="forma_pago",
        required=True,
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()

    producto = {
        "marca": args.marca.strip(),
        "sabor": args.sabor.strip().upper(),
        "cantidad": args.cantidad,
    }

    if args.precio is not None:
        producto["precio"] = args.precio

    payload = {
        "cliente": args.cliente,
        "productos": [producto],
        "plataforma": args.plataforma,
        "forma_pago": args.forma_pago,
        "estado": args.estado,
    }

    if args.fecha:
        payload["fecha"] = args.fecha

    process = subprocess.run(
        [
            str(PYTHON),
            str(AGENT_BACKEND),
            "registrar-venta",
            "--json",
            json.dumps(
                payload,
                ensure_ascii=False,
            ),
        ],
        text=True,
    )

    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
