"""Run the HTTP backend."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .http_api import build_http_server


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the AV Derivative Provenance HTTP backend."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(".avprovenance-data"),
        help="Directory for state.json and content-addressed media files.",
    )
    args = parser.parse_args()

    server = build_http_server(args.host, args.port, args.data_dir)
    host, port = server.server_address[:2]
    print(
        json.dumps(
            {
                "status": "listening",
                "host": host,
                "port": port,
                "data_dir": str(Path(args.data_dir).resolve()),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
