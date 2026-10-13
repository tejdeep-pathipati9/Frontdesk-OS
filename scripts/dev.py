"""One-command native development launcher; no Docker daemon required."""
import os
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
python = ROOT / ".venv/bin/python"
if not python.exists():
    subprocess.run([sys.executable, "-m", "venv", ".venv"], check=True)
    subprocess.run([str(python), "-m", "pip", "install", "-r", "services/api/requirements.txt"], check=True)
if not (ROOT / "node_modules/next").exists():
    subprocess.run(["npm", "ci"], check=True)
subprocess.run([str(python), "scripts/setup_local.py"], check=True)
env = {**os.environ, "NEXT_TELEMETRY_DISABLED": "1"}
api = subprocess.Popen([str(python), "-m", "uvicorn", "app.main:app", "--app-dir", "services/api", "--reload", "--reload-dir", "services/api/app", "--timeout-graceful-shutdown", "3", "--host", "127.0.0.1", "--port", "8000"], env=env)
web = subprocess.Popen(["npm", "run", "dev"], env=env)

def stop(*_):
    for child in [api, web]:
        child.terminate()
    for child in [api, web]:
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()

signal.signal(signal.SIGTERM, stop)
try:
    print("\nFrontDesk OS: http://127.0.0.1:3000 · API docs: http://127.0.0.1:8000/docs\n", flush=True)
    while api.poll() is None and web.poll() is None:
        try:
            api.wait(timeout=1)
        except subprocess.TimeoutExpired:
            continue
except KeyboardInterrupt:
    pass
finally:
    stop()
