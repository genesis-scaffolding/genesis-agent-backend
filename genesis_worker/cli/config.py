"""CLI: regenerate one service's config from the current catalog."""

from .. import GenesisWorker


def main() -> int:
    worker = GenesisWorker()
    ok = worker.regenerate_service_config("llama_swap")
    print("regenerated" if ok else "up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())