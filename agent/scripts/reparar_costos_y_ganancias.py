#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT = Path('/home/openclaw/.openclaw/workspace/vaprizziobot')
TIENDANUBE = PROJECT / 'tiendanube'
if str(TIENDANUBE) not in sys.path:
    sys.path.insert(0, str(TIENDANUBE))

from app.business.costs import recalcular_catalogo_desde_tabla_usdt  # noqa: E402


def main() -> int:
    try:
        result = recalcular_catalogo_desde_tabla_usdt()
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except Exception as exc:
        print(json.dumps({'ok': False, 'error': f'{type(exc).__name__}: {exc}'}, ensure_ascii=False, indent=2))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
