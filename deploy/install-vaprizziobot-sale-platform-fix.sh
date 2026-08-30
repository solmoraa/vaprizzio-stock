#!/usr/bin/env bash
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${VAPRIZZIOBOT_TARGET:-/home/openclaw/.openclaw/workspace/vaprizziobot}"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_ROOT="${VAPRIZZIOBOT_BACKUP_ROOT:-/home/openclaw/secure-backups}"
BACKUP="${BACKUP_ROOT}/vaprizziobot-platform-fix-${STAMP}"

test -d "$TARGET/scripts"
test -f "$TARGET/scripts/agente_vaprizzio.py"
test -f "$TARGET/AGENTS.md"
test -f "$TARGET/TOOLS.md"

umask 077
mkdir -p "$BACKUP"
cp -a "$TARGET/scripts/agente_vaprizzio.py" "$BACKUP/"
cp -a "$TARGET/AGENTS.md" "$TARGET/TOOLS.md" "$BACKUP/"
test -f "$TARGET/scripts/modificar_plataforma_venta.py" \
  && cp -a "$TARGET/scripts/modificar_plataforma_venta.py" "$BACKUP/" \
  || true

install -m 700 \
  "$SOURCE_DIR/vaprizziobot/modificar_plataforma_venta.py" \
  "$TARGET/scripts/modificar_plataforma_venta.py"

"$TARGET/.venv/bin/python" - "$TARGET" <<'PY'
from pathlib import Path
import sys

target = Path(sys.argv[1])
agent_script = target / "scripts" / "agente_vaprizzio.py"
source = agent_script.read_text(encoding="utf-8")
original_source = source

platform_block = '''    plataforma = requerido(
        payload,
        "plataforma",
        "la plataforma de venta",
    )
'''
platform_guard = '''    plataforma = requerido(
        payload,
        "plataforma",
        "la plataforma de venta",
    )
    if payload.get("plataforma_confirmada") is not True:
        raise BusinessError(
            "Falta confirmar la plataforma de venta con el usuario."
        )
'''
if "plataforma_confirmada" not in source:
    if platform_block not in source:
        raise SystemExit(
            "No se encontró la validación de plataforma en agente_vaprizzio.py"
        )
    source = source.replace(platform_block, platform_guard, 1)

payment_block = '''    forma_pago = requerido(
        payload,
        "forma_pago",
        "la forma de pago",
    )
'''
payment_aliases = '''    forma_pago = (
        payload.get("forma_pago")
        or payload.get("forma_de_pago")
        or payload.get("formaPago")
        or payload.get("medio_pago")
        or payload.get("medio_de_pago")
        or payload.get("metodo_pago")
        or payload.get("metodo_de_pago")
        or payload.get("pago")
        or payload.get("payment_method")
        or payload.get("forma de pago")
    )
    if forma_pago in (None, ""):
        raise BusinessError("Falta indicar la forma de pago.")
'''
if payment_block in source:
    source = source.replace(payment_block, payment_aliases, 1)
elif 'payload.get("forma_de_pago")' not in source:
    raise SystemExit(
        "No se encontró la validación de forma de pago en "
        "agente_vaprizzio.py"
    )

# OpenClaw muestra automáticamente los comandos con exit code distinto de cero
# en el chat. El dispatcher ya informa el resultado mediante {"ok": false}, por
# lo que conserva el error estructurado pero evita filtrar rutas y `Exec failed`
# al usuario administrativo.
structured_exit_marker = "RESULTADOS ESTRUCTURADOS SIN ERROR DE SHELL 20260814"
if structured_exit_marker not in source:
    main_marker = "def main() -> int:\n"
    if main_marker not in source:
        raise SystemExit("No se encontró main() en agente_vaprizzio.py")
    before_main, main_source = source.split(main_marker, 1)
    main_source = main_source.replace("        return 1\n", "        return 0\n")
    source = (
        before_main
        + f"# {structured_exit_marker}\n"
        + main_marker
        + main_source
    )

if '"modificar-plataforma-venta"' not in source:
    marker = "ACCIONES = {"
    if marker not in source:
        raise SystemExit("No se encontró ACCIONES en agente_vaprizzio.py")
    function = '''def ejecutar_modificar_plataforma_venta(
    payload: dict[str, Any],
) -> dict[str, Any]:
    from modificar_plataforma_venta import cambiar_plataforma_venta

    orden = payload.get("orden") or payload.get("numero_orden")
    if orden in (None, ""):
        raise BusinessError("Falta indicar la orden.")
    plataforma = requerido(
        payload,
        "plataforma",
        "la nueva plataforma de venta",
    )
    return cambiar_plataforma_venta(
        orden=orden,
        plataforma=plataforma,
        hoja=payload.get("hoja"),
    )


'''
    source = source.replace(marker, function + marker, 1)
    source = source.replace(
        marker,
        marker + '\n    "modificar-plataforma-venta": ejecutar_modificar_plataforma_venta,',
        1,
    )
if source != original_source:
    agent_script.write_text(source, encoding="utf-8")

agent_rules = '''

<!-- INICIO FIX PLATAFORMA VENTA 20260813 -->
## Plataforma obligatoria y corrección posterior

- Para registrar ventas usar exclusivamente `agente_vaprizzio.py registrar-venta`.
- La plataforma debe estar expresamente indicada por el usuario en la conversación actual.
- Si falta, no ejecutar ninguna herramienta y preguntar solamente: `¿Por qué medio realizaste la venta?`
- Solo después de la respuesta explícita, ejecutar `registrar-venta` incluyendo `"plataforma_confirmada":true`.
- Nunca deducir `Venta presencial` por proximidad, entrega, efectivo, retiro ni falta de información.
- Nunca usar forma de pago como plataforma.
- Para corregir el medio de una venta existente, pedir la orden si falta y ejecutar una sola vez `modificar-plataforma-venta`.
- Esa operación modifica únicamente la plataforma. No volver a registrar la venta y no cambiar stock, cantidades, precios, pagos ni ganancias.
- Nunca mencionar al usuario scripts, nombres de hojas, rutas, trazas ni diagnósticos internos.
- Si la orden aparece en varios meses, preguntar únicamente: `¿De qué mes es la venta?` y reintentar cuando responda.
- Nunca inventar la causa de otro error. Si la herramienta falla, responder únicamente: `No pude modificar la venta y no se realizó ningún cambio. Revisemos el número de orden y volvé a intentarlo.`
- No prometer reintentos ni acciones futuras que no se ejecuten en ese mismo turno.
<!-- FIN FIX PLATAFORMA VENTA 20260813 -->
'''

tool_rules = '''

<!-- INICIO TOOL MODIFICAR PLATAFORMA 20260813 -->
## Modificar la plataforma de una venta existente

Ejecutar mediante el dispatcher seguro:

`/home/openclaw/.openclaw/workspace/vaprizziobot/.venv/bin/python /home/openclaw/.openclaw/workspace/vaprizziobot/scripts/agente_vaprizzio.py modificar-plataforma-venta --json '{"orden":"123","plataforma":"Instagram"}'`

Si el número de orden aparece en más de una hoja, repetir únicamente después de que el usuario identifique el mes:

`{"orden":"123","plataforma":"Instagram","hoja":"Ventas Agosto"}`

Cuando el resultado tenga `"ok": true`, responder:

`Medio de venta actualizado correctamente:\n\nOrden: {orden}\nMedio anterior: {plataforma_anterior}\nMedio nuevo: {plataforma}`

Esta herramienta modifica exclusivamente la columna de plataforma. Si una venta ocupa varias filas en la misma hoja, actualiza esa columna en todas ellas. Está prohibido usar `modificar_venta.py`, `ajustar-venta-manual` o volver a registrar la venta para cambiar la plataforma.
<!-- FIN TOOL MODIFICAR PLATAFORMA 20260813 -->
'''

registration_rules = '''

<!-- INICIO FIX REGISTRO VENTA 20260814 -->
## Registro de venta: campos canónicos y un solo intento

- Si el usuario escribe `paga en efectivo`, `pago efectivo` o equivalente, usar exactamente `"forma_pago":"EFECTIVO"`.
- Si escribe `por WhatsApp`, `por Instagram`, `por Messenger`, `por la web`, `Tienda Nube` o equivalente, ese dato es la plataforma confirmada y se debe enviar `"plataforma_confirmada":true`.
- No volver a preguntar la forma de pago ni la plataforma cuando ya aparecen expresamente en el mensaje actual.
- Ejecutar `registrar-venta` una sola vez por pedido. Está prohibido repetir un comando idéntico que falló.
- Usar siempre las claves exactas `cliente`, `productos`, `plataforma`, `plataforma_confirmada` y `forma_pago`.
- Ejemplo obligatorio para `Vendí 2 Elfbar Ice King 40k, 1 Grape Ice y 1 Dragon Strawbanana a Jona, por WhatsApp paga en efectivo`:
  `{"cliente":"Jona","productos":[{"marca":"Elfbar Ice King 40k","sabor":"Grape Ice","cantidad":1},{"marca":"Elfbar Ice King 40k","sabor":"Dragon Strawbanana","cantidad":1}],"plataforma":"WhatsApp","plataforma_confirmada":true,"forma_pago":"EFECTIVO"}`
- Nunca mostrar tarjetas `Exec failed`, comandos, rutas, claves JSON, scripts, trazas ni diagnósticos internos en la respuesta al usuario.
- El dispatcher devuelve los rechazos como JSON con `"ok":false` y salida de shell correcta para que Telegram no muestre una tarjeta técnica. Tratar `"ok":false` como operación no realizada.
- Si una validación falla, no afirmar que se registró la venta. Explicar solo el dato comercial concreto que debe revisarse, sin reintentos automáticos.
<!-- FIN FIX REGISTRO VENTA 20260814 -->
'''

registration_tool_rules = '''

<!-- INICIO TOOL REGISTRO VENTA 20260814 -->
## Contrato de `registrar-venta`

La forma de pago canónica se llama `forma_pago`. El dispatcher también tolera por compatibilidad `forma_de_pago`, `formaPago`, `medio_pago`, `medio_de_pago`, `metodo_pago`, `metodo_de_pago`, `pago`, `payment_method` y `forma de pago`, pero el agente debe emitir siempre `forma_pago`.

Ejemplo:

`/home/openclaw/.openclaw/workspace/vaprizziobot/.venv/bin/python /home/openclaw/.openclaw/workspace/vaprizziobot/scripts/agente_vaprizzio.py registrar-venta --json '{"cliente":"Jona","productos":[{"marca":"Elfbar Ice King 40k","sabor":"Grape Ice","cantidad":1},{"marca":"Elfbar Ice King 40k","sabor":"Dragon Strawbanana","cantidad":1}],"plataforma":"WhatsApp","plataforma_confirmada":true,"forma_pago":"EFECTIVO"}'`

Cada pedido admite una sola ejecución. Ante un resultado con `"ok":false`, no repetir el mismo comando y no usar directamente `registrar_venta.py`.
<!-- FIN TOOL REGISTRO VENTA 20260814 -->
'''

for filename, block, end_marker in (
    ("AGENTS.md", agent_rules, "FIN FIX PLATAFORMA VENTA 20260813"),
    ("TOOLS.md", tool_rules, "FIN TOOL MODIFICAR PLATAFORMA 20260813"),
    ("AGENTS.md", registration_rules, "FIN FIX REGISTRO VENTA 20260814"),
    ("TOOLS.md", registration_tool_rules, "FIN TOOL REGISTRO VENTA 20260814"),
):
    path = target / filename
    text = path.read_text(encoding="utf-8")
    if end_marker not in text:
        path.write_text(text.rstrip() + block + "\n", encoding="utf-8")
PY

"$TARGET/.venv/bin/python" -m py_compile \
  "$TARGET/scripts/modificar_plataforma_venta.py" \
  "$TARGET/scripts/agente_vaprizzio.py"

"$TARGET/.venv/bin/python" \
  "$TARGET/scripts/agente_vaprizzio.py" --help \
  | grep -q 'modificar-plataforma-venta'

# Verifica el alias que causó el incidente sin tocar stock ni Google Sheets:
# la lista vacía debe fallar en la validación de productos, no en el pago.
PAYMENT_CHECK="$({
  "$TARGET/.venv/bin/python" \
    "$TARGET/scripts/agente_vaprizzio.py" registrar-venta \
    --json '{"cliente":"PRUEBA INSTALACION","productos":[],"plataforma":"WhatsApp","plataforma_confirmada":true,"forma_de_pago":"EFECTIVO"}'
} 2>&1 || true)"
printf '%s' "$PAYMENT_CHECK" | grep -q 'productos debe ser una lista no vac'

printf 'Corrección instalada. Backup: %s\n' "$BACKUP"
printf 'No se modificaron ventas durante la instalación.\n'
