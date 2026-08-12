"""CLI: start or stop a service."""

import argparse

from .. import GenesisWorker


def main() -> int:
    p = argparse.ArgumentParser(description="Start or stop a worker service.")
    p.add_argument("action", choices=["start", "stop"])
    p.add_argument("--service", default="llama_swap")
    args = p.parse_args()

    worker = GenesisWorker()
    if args.action == "start":
        result = worker.start_service(args.service)
        print(f"start: ok={result.ok} pid={result.pid} {result.message}")
        return 0 if result.ok else 1
    else:
        result = worker.stop_service(args.service)
        print(f"stop: ok={result.ok} {result.message}")
        return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())