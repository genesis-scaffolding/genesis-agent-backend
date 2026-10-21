"""CLI: regenerate one service's config from the current catalog."""

import argparse

from .. import GenesisWorker


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate the llama-swap service config.")
    parser.parse_args()  # exits 0 on --help before any side effects
    worker = GenesisWorker()
    ok = worker.regenerate_service_config("llama_swap")
    print("regenerated" if ok else "up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
