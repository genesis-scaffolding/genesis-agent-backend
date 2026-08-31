"""Console-script entry point: launches the Streamlit UI."""

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    """Launch the Streamlit UI server."""
    # Resolve the app path lazily so importing this module doesn't execute
    # app.py's top-level code outside a Streamlit run context.
    app_path = Path(__file__).resolve().parent.parent / "ui" / "app.py"
    port = os.environ.get("GENESIS_UI_PORT", "8501")
    return subprocess.call(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(app_path),
            "--server.address",
            "0.0.0.0",
            "--server.port",
            port,
            "--server.headless",
            "true",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
