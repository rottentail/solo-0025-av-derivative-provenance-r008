"""Threaded HTTP boundary for media assets and derivative provenance."""

from __future__ import annotations

import logging
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .application import MediaProvenanceApplication
from .errors import ApplicationError, InternalFailure, ValidationFailure
from .serialization import new_identifier
from .transport import read_json_body, write_json
from .validation import decode_media_payload


LOGGER = logging.getLogger(__name__)
ASSET_PATH = re.compile(r"^/v1/assets/([A-Za-z0-9_]+)$")
RENDITION_PATH = re.compile(r"^/v1/renditions/([A-Za-z0-9_]+)$")
LINEAGE_PATH = re.compile(r"^/v1/renditions/([A-Za-z0-9_]+)/lineage$")
CHECK_PATH = re.compile(
    r"^/v1/renditions/([A-Za-z0-9_]+)/integrity-checks$"
)


class ProvenanceHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        application: MediaProvenanceApplication,
    ) -> None:
        self.application = application
        super().__init__(server_address, RequestHandler)

    def server_close(self) -> None:
        super().server_close()
        self.application.close()


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "AVProvenance/1.0"

    @property
    def application(self) -> MediaProvenanceApplication:
        return self.server.application  # type: ignore[attr-defined]

    def log_message(self, format_string: str, *args: Any) -> None:
        LOGGER.info(
            "%s - %s",
            self.address_string(),
            format_string % args,
        )

    def _request_id(self) -> str:
        supplied = (self.headers.get("X-Request-ID") or "").strip()
        if supplied and len(supplied) <= 96 and all(
            character.isalnum() or character in "._-" for character in supplied
        ):
            return supplied
        return new_identifier("req")

    def _path(self) -> str:
        return urlsplit(self.path).path.rstrip("/") or "/"

    def _send_application_error(
        self,
        error: ApplicationError,
        request_id: str,
    ) -> None:
        write_json(
            self,
            error.status,
            error.to_body(),
            request_id=request_id,
        )

    def _send_unexpected(self, request_id: str) -> None:
        error = InternalFailure()
        write_json(
            self,
            error.status,
            error.to_body(),
            request_id=request_id,
        )

    def do_GET(self) -> None:  # noqa: N802
        request_id = self._request_id()
        path = self._path()
        try:
            if path == "/health":
                write_json(
                    self,
                    200,
                    {
                        "status": "ok",
                        "application": "av-derivative-provenance",
                        "version": "1.0.0",
                    },
                    request_id=request_id,
                )
                return
            asset_match = ASSET_PATH.match(path)
            if asset_match:
                write_json(
                    self,
                    200,
                    self.application.get_asset(asset_match.group(1)),
                    request_id=request_id,
                )
                return
            lineage_match = LINEAGE_PATH.match(path)
            if lineage_match:
                write_json(
                    self,
                    200,
                    self.application.get_lineage(lineage_match.group(1)),
                    request_id=request_id,
                )
                return
            rendition_match = RENDITION_PATH.match(path)
            if rendition_match:
                write_json(
                    self,
                    200,
                    self.application.get_rendition(rendition_match.group(1)),
                    request_id=request_id,
                )
                return
            raise ApplicationError(
                code="ROUTE_NOT_FOUND",
                message="No route matches the requested path.",
                status=404,
            )
        except ApplicationError as exc:
            self._send_application_error(exc, request_id)
        except Exception:
            LOGGER.exception("unexpected GET failure request_id=%s", request_id)
            self._send_unexpected(request_id)

    def do_POST(self) -> None:  # noqa: N802
        request_id = self._request_id()
        path = self._path()
        try:
            if path == "/v1/assets":
                body = read_json_body(self)
                if body is None:
                    raise ValidationFailure("A JSON body is required.")
                data = decode_media_payload(body.get("data_base64"))
                created = self.application.ingest_asset(
                    body.get("filename"),
                    data,
                )
                write_json(
                    self,
                    201,
                    created,
                    request_id=request_id,
                )
                return
            if path == "/v1/renditions":
                body = read_json_body(self)
                created = self.application.create_rendition(body)
                write_json(
                    self,
                    201,
                    created,
                    request_id=request_id,
                )
                return
            check_match = CHECK_PATH.match(path)
            if check_match:
                read_json_body(self, required=False)
                result = self.application.verify_rendition(check_match.group(1))
                write_json(
                    self,
                    201,
                    result,
                    request_id=request_id,
                )
                return
            raise ApplicationError(
                code="ROUTE_NOT_FOUND",
                message="No route matches the requested path.",
                status=404,
            )
        except ApplicationError as exc:
            self._send_application_error(exc, request_id)
        except Exception:
            LOGGER.exception("unexpected POST failure request_id=%s", request_id)
            self._send_unexpected(request_id)


def build_http_server(
    host: str,
    port: int,
    data_dir: str | Path,
) -> ProvenanceHTTPServer:
    application = MediaProvenanceApplication(data_dir)
    return ProvenanceHTTPServer((host, int(port)), application)
