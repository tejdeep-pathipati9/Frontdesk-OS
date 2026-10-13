"""Maintain one temporary webhook tunnel and reconnect saved Vapi assistants."""
import fcntl
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path
import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'services/api'))


def webhook_ready(base, secret):
    from app.voice_readiness import check_webhook
    from app.domain import DomainError
    try:
        return check_webhook(base, secret)['status'] == 'ready'
    except DomainError:
        return False


def maintain_tunnel(binary, secret, stopped):
    while not stopped.is_set():
        tunnel = subprocess.Popen([binary, 'tunnel', '--url', 'http://127.0.0.1:8010', '--no-autoupdate', '--protocol', 'http2'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=ROOT)
        ready, address = threading.Event(), []
        def read_output(stream=tunnel.stdout, found=address, signal_ready=ready):
            for line in stream:
                match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', line)
                if match and not found:
                    found.append(match.group())
                    signal_ready.set()
            signal_ready.set()
        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        try:
            # Wait in short intervals so Ctrl+C/SIGTERM always interrupts startup.
            for _ in range(45):
                if ready.wait(1) or stopped.is_set():
                    break
            if stopped.is_set():
                break
            if not address or tunnel.poll() is not None:
                print('Tunnel startup failed. Retrying in 5 seconds.', flush=True)
                stopped.wait(5)
                continue
            print('Checking public webhook: ' + address[0], flush=True)
            public_ready = False
            for _ in range(12):
                if stopped.is_set() or tunnel.poll() is not None:
                    break
                if webhook_ready(address[0], secret):
                    public_ready = True
                    break
                stopped.wait(2)
            if not public_ready:
                print('Public webhook did not become reachable. Reconnecting.', flush=True)
                continue
            print('Temporary webhook: ' + address[0] + '/api/v1/voice/vapi', flush=True)
            # A provider failure exits instead of repeatedly making external writes.
            subprocess.run([sys.executable, str(ROOT / 'scripts/setup_vapi.py'), '--server-url', address[0], '--vertical', 'both', '--apply'], cwd=ROOT, check=True)
            # Native uvicorn reload reads the new server URL from .env.
            (ROOT / 'services/api/app/config.py').touch()
            print('Voice connection ready. This process monitors/reconnects the tunnel; Ctrl+C stops it.', flush=True)
            failures = 0
            while not stopped.wait(15) and tunnel.poll() is None:
                if webhook_ready(address[0], secret):
                    failures = 0
                else:
                    failures += 1
                    if failures >= 2:
                        print('Webhook connection lost. Creating a fresh tunnel and updating saved assistants.', flush=True)
                        break
        finally:
            tunnel.terminate()
            try:
                tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:
                tunnel.kill()
                tunnel.wait()
            reader.join(timeout=1)
            tunnel.stdout.close()


def main():
    values = dotenv_values(ROOT / '.env')
    if any(not values.get(key) for key in ['VAPI_PUBLIC_KEY', 'VAPI_PRIVATE_KEY', 'VAPI_WEBHOOK_SECRET']):
        raise SystemExit('Configure your Vapi keys and webhook secret in the ignored .env first.')
    binary = shutil.which('cloudflared') or str(ROOT / '.runtime/cloudflared')
    if not Path(binary).exists():
        raise SystemExit('Install cloudflared first; see the Vapi section in README.md.')
    try:
        httpx.get('http://127.0.0.1:8000/api/v1/health', timeout=3).raise_for_status()
    except httpx.HTTPError:
        raise SystemExit('Start the app first with python3 scripts/dev.py.')
    runtime = ROOT / '.runtime'
    runtime.mkdir(exist_ok=True)
    with (runtime / 'vapi-demo.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('The Vapi connection manager is already running.')
        stopped = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        children = []
        try:
            try:
                gateway_ready = httpx.post('http://127.0.0.1:8010/api/v1/voice/vapi', json={}, timeout=2).status_code == 401
            except httpx.HTTPError:
                gateway_ready = False
            if not gateway_ready:
                children.append(subprocess.Popen([sys.executable, str(ROOT / 'scripts/vapi_gateway.py')], cwd=ROOT))
            # macOS: avoid idle sleep invalidating a demo connection overnight.
            caffeinate = shutil.which('caffeinate')
            if caffeinate:
                children.append(subprocess.Popen([caffeinate, '-i', '-w', str(os.getpid())]))
            maintain_tunnel(binary, values['VAPI_WEBHOOK_SECRET'], stopped)
        except KeyboardInterrupt:
            stopped.set()
            print('Stopping voice connection.', flush=True)
        finally:
            for child in reversed(children):
                child.terminate()
            for child in reversed(children):
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


if __name__ == '__main__':
    main()
