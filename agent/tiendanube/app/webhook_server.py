#!/usr/bin/env python3

from __future__ import annotations

import hmac
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


WORKSPACE = Path(
    "/home/openclaw/.openclaw/workspace/vaprizziobot"
)

TN_DIR = WORKSPACE / "tiendanube"
ENV_FILE = TN_DIR / "credentials" / ".env"

load_dotenv(ENV_FILE)

sys.path.insert(0, str(TN_DIR))

from app.order_sync_v2 import process_webhook
from app.sync_core import store_credentials


HOST = os.environ.get(
    "TN_WEBHOOK_HOST",
    "127.0.0.1",
)

PORT = int(
    os.environ.get(
        "TN_WEBHOOK_PORT",
        "8787",
    )
)

SECRET = os.environ.get(
    "TN_WEBHOOK_SECRET",
    "",
)


class WebhookHandler(BaseHTTPRequestHandler):
    server_version = "VaprizzioWebhook/1.0"

    def log_message(
        self,
        format_string: str,
        *args,
    ) -> None:
        print(
            (
                f"{self.address_string()} "
                f"{format_string % args}"
            ),
            flush=True,
        )

    def send_json(
        self,
        status: int,
        data: dict,
    ) -> None:
        body = json.dumps(
            data,
            ensure_ascii=False,
            default=str,
        ).encode("utf-8")

        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header(
            "Content-Length",
            str(len(body)),
        )
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path

        if path in {
            "/health",
            "/tiendanube/health",
        }:
            self.send_json(
                200,
                {
                    "ok": True,
                    "service": "vaprizzio-webhook",
                },
            )
            return

        self.send_json(
            404,
            {
                "ok": False,
                "error": "Ruta inexistente.",
            },
        )

    def do_POST(self) -> None:
        path = urlparse(self.path).path

        if path != "/tiendanube/webhook":
            self.send_json(
                404,
                {
                    "ok": False,
                    "error": "Ruta inexistente.",
                },
            )
            return

        # Tiendanube no está enviando de forma consistente el
        # encabezado personalizado configurado en el webhook.
        #
        # La solicitud se valida más abajo comprobando que store_id
        # coincida con la tienda conectada. Luego order_sync consulta
        # la orden mediante la API autenticada antes de procesarla.
        try:
            length = int(
                self.headers.get(
                    "Content-Length",
                    "0",
                )
            )

            if length <= 0 or length > 1_000_000:
                raise ValueError(
                    "Tamaño de payload inválido."
                )

            body = self.rfile.read(length)
            payload = json.loads(
                body.decode("utf-8")
            )

            if not isinstance(payload, dict):
                raise ValueError(
                    "El payload debe ser un objeto JSON."
                )

            store_id = payload.get("store_id")
            event = payload.get("event")
            resource_id = payload.get("id")

            if not store_id or not event or not resource_id:
                raise ValueError(
                    "Faltan store_id, event o id."
                )

            expected_store_id, _ = store_credentials()

            if str(store_id) != str(expected_store_id):
                self.send_json(
                    403,
                    {
                        "ok": False,
                        "error": "Store ID incorrecto.",
                    },
                )
                return

            result = process_webhook(
                store_id,
                event,
                resource_id,
            )

            self.send_json(
                200,
                result,
            )

        except json.JSONDecodeError:
            self.send_json(
                400,
                {
                    "ok": False,
                    "error": "JSON inválido.",
                },
            )

        except ValueError as error:
            self.send_json(
                400,
                {
                    "ok": False,
                    "error": str(error),
                },
            )

        except Exception as error:
            traceback.print_exc()

            self.send_json(
                500,
                {
                    "ok": False,
                    "error": (
                        f"{type(error).__name__}: {error}"
                    ),
                },
            )


def main() -> None:
    server = HTTPServer(
        (HOST, PORT),
        WebhookHandler,
    )

    print(
        (
            "Webhook de Vaprizzio escuchando en "
            f"http://{HOST}:{PORT}"
        ),
        flush=True,
    )

    server.serve_forever()


if __name__ == "__main__":
    main()
