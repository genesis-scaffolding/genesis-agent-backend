"""Console-script entry point: launches the FastAPI server.

Uses ``uvicorn.run`` directly (no subprocess) — uvicorn is designed to
be imported, unlike streamlit which only really runs as
``streamlit run``.

``GENESIS_API_HOST`` and ``GENESIS_API_PORT`` override the defaults
(``0.0.0.0`` and ``20985``) so the API can run beside the Streamlit UI
on the standard Tailscale-reachable addresses without colliding with
llama-swap's ``8080`` or Streamlit's ``8501``.
"""

import os

import uvicorn


def main() -> int:
    host = os.environ.get("GENESIS_API_HOST", "0.0.0.0")
    port = int(os.environ.get("GENESIS_API_PORT", "20985"))
    from ..api.app import app

    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
