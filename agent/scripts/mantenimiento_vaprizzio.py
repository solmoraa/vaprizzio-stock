#!/usr/bin/env python3

import json

print(
    json.dumps(
        {
            "ok": True,
            "mensaje": (
                "Mantenimiento general desactivado. "
                "Cada operación actualiza solamente las hojas necesarias."
            ),
        },
        ensure_ascii=False,
    )
)
