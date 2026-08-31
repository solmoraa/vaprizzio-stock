#!/usr/bin/env python3

import subprocess
import sys
from pathlib import Path

WORKSPACE = Path("/home/openclaw/.openclaw/workspace/vaprizziobot")
PYTHON = WORKSPACE / ".venv/bin/python"
BACKEND = WORKSPACE / "scripts/agregar_producto_backend.py"
SUBCOMANDO = 'consultar-stock'


def main() -> int:
    resultado = subprocess.run(
        [
            str(PYTHON),
            str(BACKEND),
            SUBCOMANDO,
            *sys.argv[1:],
        ],
        text=True,
    )

    return resultado.returncode


if __name__ == "__main__":
    raise SystemExit(main())
