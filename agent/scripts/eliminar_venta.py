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
        description=(
            "Cancelar una venta manual y reponer el stock "
            "en Tiendanube y Google Sheets."
        )
    )

    parser.add_argument(
        "--orden",
        required=True,
    )

    parser.add_argument(
        "--hoja",
    )

    # Se conservan estos argumentos para mantener compatibilidad
    # con las instrucciones antiguas del agente.
    parser.add_argument(
        "--mes",
    )

    parser.add_argument(
        "--anio",
        type=int,
    )

    return parser


def main() -> int:
    args = build_parser().parse_args()

    payload = {
        "orden": args.orden,
        "confirmar": True,
    }

    if args.hoja:
        payload["hoja"] = args.hoja
    elif args.mes and args.anio:
        payload["hoja"] = f"{args.mes} {args.anio}"
    elif args.mes:
        payload["hoja"] = args.mes

    process = subprocess.run(
        [
            str(PYTHON),
            str(AGENT_BACKEND),
            "cancelar-manual",
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
