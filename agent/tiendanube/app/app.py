import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request

from app.order_sync_v2 import process_webhook


BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / "credentials" / ".env"

load_dotenv(ENV_FILE)

TN_APP_ID = os.environ["TN_APP_ID"]
TN_CLIENT_SECRET = os.environ["TN_CLIENT_SECRET"]
TN_TOKEN_URL = os.environ.get(
    "TN_TOKEN_URL",
    "https://www.tiendanube.com/apps/authorize/token",
)
TN_API_BASE = os.environ.get(
    "TN_API_BASE",
    "https://api.tiendanube.com/v1",
)
TN_USER_AGENT = os.environ["TN_USER_AGENT"]
DATABASE_PATH = Path(os.environ["TN_DATABASE"])

DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

app = Flask(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def database_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database() -> None:
    with database_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS stores (
                store_id INTEGER PRIMARY KEY,
                access_token TEXT NOT NULL,
                token_type TEXT,
                scope TEXT,
                installed_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_webhooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT,
                store_id INTEGER,
                payload TEXT NOT NULL,
                received_at TEXT NOT NULL
            )
            """
        )


def save_store(
    store_id: int,
    access_token: str,
    token_type: str,
    scope: str,
) -> None:
    timestamp = utc_now()

    with database_connection() as connection:
        connection.execute(
            """
            INSERT INTO stores (
                store_id,
                access_token,
                token_type,
                scope,
                installed_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(store_id) DO UPDATE SET
                access_token = excluded.access_token,
                token_type = excluded.token_type,
                scope = excluded.scope,
                updated_at = excluded.updated_at
            """,
            (
                store_id,
                access_token,
                token_type,
                scope,
                timestamp,
                timestamp,
            ),
        )


def exchange_code_for_token(code: str) -> dict:
    payload = {
        "client_id": TN_APP_ID,
        "client_secret": TN_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
    }

    response = requests.post(
        TN_TOKEN_URL,
        json=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": TN_USER_AGENT,
        },
        timeout=30,
    )

    if not response.ok:
        logging.error(
            "Error OAuth HTTP %s: %s",
            response.status_code,
            response.text[:1000],
        )
        raise RuntimeError(
            f"Tiendanube respondió HTTP {response.status_code}"
        )

    data = response.json()

    if not data.get("access_token"):
        raise RuntimeError(
            "La respuesta no contiene access_token."
        )

    store_id = data.get("user_id") or data.get("store_id")

    if not store_id:
        raise RuntimeError(
            "La respuesta no contiene user_id/store_id."
        )

    return data


@app.get("/")
def index():
    return jsonify(
        {
            "ok": True,
            "servicio": "Vaprizzio Tiendanube Sync",
            "estado": "activo",
        }
    )


@app.get("/health")
def health():
    return jsonify(
        {
            "ok": True,
            "status": "healthy",
            "database": str(DATABASE_PATH),
        }
    )


@app.get("/tiendanube/oauth/callback")
def oauth_callback():
    error = request.args.get("error")
    code = request.args.get("code")

    if error:
        return jsonify(
            {
                "ok": False,
                "error": error,
                "descripcion": request.args.get("error_description"),
            }
        ), 400

    if not code:
        return jsonify(
            {
                "ok": False,
                "error": "Tiendanube no envió el parámetro code.",
            }
        ), 400

    try:
        token_data = exchange_code_for_token(code)

        store_id = int(
            token_data.get("user_id")
            or token_data.get("store_id")
        )

        save_store(
            store_id=store_id,
            access_token=token_data["access_token"],
            token_type=token_data.get("token_type", "bearer"),
            scope=token_data.get("scope", ""),
        )

        return jsonify(
            {
                "ok": True,
                "mensaje": "Tienda conectada correctamente.",
                "store_id": store_id,
                "scope": token_data.get("scope", ""),
            }
        )

    except requests.RequestException:
        logging.exception("No se pudo conectar con Tiendanube.")

        return jsonify(
            {
                "ok": False,
                "error": "No se pudo conectar con Tiendanube.",
            }
        ), 502

    except Exception as error:
        logging.exception("Falló el callback OAuth.")

        return jsonify(
            {
                "ok": False,
                "error": str(error),
            }
        ), 500


@app.post("/tiendanube/webhook")
def webhook():
    """
    Recibe el webhook de Tiendanube y procesa realmente la orden.

    La venta y el stock solamente se modifican cuando
    order_sync_v2 confirma que el pago está aprobado.
    """
    raw_payload = request.get_data(as_text=True)

    try:
        payload = request.get_json(silent=True) or {}

        if not isinstance(payload, dict):
            payload = {}

        event = str(
            payload.get("event")
            or request.headers.get(
                "X-Linkedstore-Event",
                "",
            )
            or ""
        ).strip().lower()

        store_id = (
            payload.get("store_id")
            or payload.get("user_id")
            or request.headers.get(
                "X-Linkedstore-Store-Id",
                "",
            )
        )

        resource_id = (
            payload.get("id")
            or payload.get("resource_id")
            or payload.get("order_id")
        )

        app.logger.info(
            "Webhook recibido: event=%s store_id=%s id=%s payload=%s",
            event,
            store_id,
            resource_id,
            raw_payload[:2000],
        )

        # Guardamos una copia del webhook recibido para auditoría.
        with database_connection() as connection:
            connection.execute(
                """
                INSERT INTO processed_webhooks (
                    event,
                    store_id,
                    payload,
                    received_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    event,
                    store_id,
                    raw_payload or json.dumps(payload),
                    utc_now(),
                ),
            )

        if not event:
            app.logger.warning(
                "Webhook ignorado porque no contiene event."
            )

            return jsonify(
                {
                    "ok": False,
                    "error": "El webhook no contiene event.",
                }
            ), 400

        if not store_id:
            app.logger.warning(
                "Webhook ignorado porque no contiene store_id."
            )

            return jsonify(
                {
                    "ok": False,
                    "error": "El webhook no contiene store_id.",
                }
            ), 400

        if not resource_id:
            app.logger.warning(
                "Webhook ignorado porque no contiene el ID "
                "de la orden."
            )

            return jsonify(
                {
                    "ok": False,
                    "error": (
                        "El webhook no contiene el ID "
                        "de la orden."
                    ),
                }
            ), 400

        result = process_webhook(
            store_id,
            event,
            resource_id,
        )

        app.logger.info(
            "Webhook procesado: event=%s id=%s resultado=%s",
            event,
            resource_id,
            json.dumps(
                result,
                ensure_ascii=False,
                default=str,
            ),
        )

        return jsonify(result), 200

    except Exception as error:
        app.logger.exception(
            "Falló el procesamiento del webhook de Tiendanube."
        )

        return jsonify(
            {
                "ok": False,
                "error": (
                    f"{type(error).__name__}: {error}"
                ),
            }
        ), 500


initialize_database()


@app.post("/tiendanube/webhooks/store-redact")
def webhook_store_redact():
    app.logger.info(
        "Store redact: %s",
        request.get_json(silent=True),
    )
    return jsonify({"ok": True}), 200


@app.post("/tiendanube/webhooks/customers-redact")
def webhook_customers_redact():
    app.logger.info(
        "Customers redact: %s",
        request.get_json(silent=True),
    )
    return jsonify({"ok": True}), 200


@app.post("/tiendanube/webhooks/customers-data-request")
def webhook_customers_data_request():
    app.logger.info(
        "Customers data request: %s",
        request.get_json(silent=True),
    )
    return jsonify({"ok": True}), 200
