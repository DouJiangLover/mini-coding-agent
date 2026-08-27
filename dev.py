"""Start the local API and visual console together for development."""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def main() -> int:
    processes = [
        subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--reload", "--port", "8000"], cwd=ROOT),
        subprocess.Popen(["npm", "run", "dev"], cwd=ROOT),
    ]

    def stop_all(_signal=None, _frame=None):
        for process in processes:
            if process.poll() is None:
                process.terminate()

    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)
    try:
        return max(process.wait() for process in processes)
    finally:
        stop_all()


if __name__ == "__main__":
    raise SystemExit(main())
