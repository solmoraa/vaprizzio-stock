# vaprizzio-stock

Herramientas versionadas del agente administrativo de Vaprizzio que funciona
por Telegram y administra ventas, stock y datos relacionados en Google Sheets.

Este repositorio está separado del agente comercial `chat-vaprizzio`. Ningún
instalador de este proyecto modifica el backend que responde automáticamente
mensajes de WhatsApp, Instagram o Messenger.

## Estructura

- `deploy/install-vaprizziobot-sale-platform-fix.sh`: valida plataforma y forma
  de pago, evita intentos duplicados y agrega la corrección de plataforma.
- `deploy/install-vaprizziobot-dynamic-catalog-fix.sh`: evita listas estáticas
  de modelos y permite modelos y sabores nuevos del Excel.
- `deploy/vaprizziobot/`: scripts que se instalan o aplican sobre el workspace
  administrativo del servidor.
- `deploy/openclaw-telegram-watchdog.*`: vigilancia y recuperación del canal de
  Telegram del agente de stock.
- `tests/`: pruebas locales de regresión que no modifican Google Sheets.

El workspace activo continúa en:

```text
/home/openclaw/.openclaw/workspace/vaprizziobot
```

Los instaladores crean respaldos privados en
`/home/openclaw/secure-backups` antes de modificar ese workspace.

## Verificación local

Las pruebas usan únicamente la biblioteca estándar de Python:

```bash
python3 -m unittest discover -s tests -v
```

## Instalación o actualización en el servidor

Primera instalación del checkout:

```bash
cd /home/openclaw/apps
git clone https://github.com/solmoraa/vaprizzio-stock.git
cd /home/openclaw/apps/vaprizzio-stock
```

Actualizaciones posteriores:

```bash
cd /home/openclaw/apps/vaprizzio-stock
git pull --ff-only origin main
python3 -m unittest discover -s tests -v
```

Aplicar las correcciones del agente administrativo:

```bash
bash deploy/install-vaprizziobot-sale-platform-fix.sh
bash deploy/install-vaprizziobot-dynamic-catalog-fix.sh
```

Auditar todos los modelos y sabores visibles en Google Sheets con una sola
lectura y sin modificar ventas ni stock:

```bash
/home/openclaw/.openclaw/workspace/vaprizziobot/.venv/bin/python \
  /home/openclaw/.openclaw/workspace/vaprizziobot/scripts/validar_catalogo_venta.py
```

Verificar específicamente el caso reportado, también en modo de solo lectura:

```bash
/home/openclaw/.openclaw/workspace/vaprizziobot/.venv/bin/python \
  /home/openclaw/.openclaw/workspace/vaprizziobot/scripts/validar_catalogo_venta.py \
  --modelo "Lost Mary Dura" \
  --sabor "Grape Ice" \
  --sabor "Watermelon Ice"
```

La salida confirma el nombre canónico encontrado en la hoja, ambos sabores y
el tamaño de `AGENTS.md`. El instalador consolida las reglas que administra y
se niega a dejar el prompt con 20.000 caracteres/bytes o más.

Reiniciar el runtime compartido para que el agente vuelva a cargar sus reglas:

```bash
sudo systemctl restart openclaw-gateway.service
```

## Watchdog de Telegram

```bash
sudo install -m 755 deploy/openclaw-telegram-watchdog.sh \
  /usr/local/sbin/openclaw-telegram-watchdog
sudo install -m 644 deploy/openclaw-telegram-watchdog.service \
  /etc/systemd/system/openclaw-telegram-watchdog.service
sudo install -m 644 deploy/openclaw-telegram-watchdog.timer \
  /etc/systemd/system/openclaw-telegram-watchdog.timer
sudo systemctl daemon-reload
sudo systemctl enable --now openclaw-telegram-watchdog.timer
```

Verificar:

```bash
systemctl list-timers openclaw-telegram-watchdog.timer
openclaw channels status --probe
```

## Seguridad

- No versionar `.env`, credenciales de Google, tokens de Telegram ni respaldos.
- No ejecutar ventas ficticias para probar: las pruebas locales y la auditoría
  de catálogo son de solo lectura.
- No afirmar que una venta se registró sin recibir `"ok": true`.
