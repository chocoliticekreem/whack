#!/usr/bin/env python3
"""Verify the committed backend, without untracked files, local data, .env or PYTHONPATH.

Run from any directory: python3 /path/to/whack/scripts/check-backend-checkout.py
--ref selects a commit (default HEAD); --skip-tests runs only both real server startup checks.
All subprocesses and temporary databases are isolated from the developer's running services.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def smoke(checkout: Path, env: dict[str, str], script: str, port_var: str, endpoint: str, key: str) -> None:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    # If a runner stalls during startup, retain Python thread stacks rather than reporting only a timeout.
    launcher = ("import faulthandler, os, runpy, sys; "
                "faulthandler.dump_traceback_later(10, repeat=True); "
                "sys.argv = sys.argv[1:]; sys.path.insert(0, os.path.dirname(sys.argv[0])); "
                "runpy.run_path(sys.argv[0], run_name='__main__')")
    cmd = [sys.executable, '-c', launcher, str(checkout / 'services' / 'city' / script),
           '--replay-from', '2026-11-07T15:00:00Z']
    if script == 'venue_server.py':
        cmd += ['--retail', '2', '--market-makers', '1']
    # Empty replay data avoids all external feed calls. Local HTTP goes directly to loopback, never a proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with tempfile.TemporaryFile(mode='w+') as log:
        proc = subprocess.Popen(cmd, cwd=checkout, env=env | {port_var: str(port)}, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 60
            last_error = None
            while time.monotonic() < deadline and proc.poll() is None:
                try:
                    with opener.open(f'http://127.0.0.1:{port}/health', timeout=.5) as response:
                        health = json.load(response)
                    if not health.get('ok'):
                        raise RuntimeError(f'{script}: unhealthy response {health}')
                    with opener.open(f'http://127.0.0.1:{port}{endpoint}', timeout=2) as response:
                        payload = json.load(response)
                    if not isinstance(payload.get(key), list):
                        raise RuntimeError(f'{script}: {endpoint} missing list {key}')
                    print(f'PASS {script}: /health and {endpoint} return valid JSON', flush=True)
                    return
                except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                    last_error = repr(exc)
                    time.sleep(.1)
            log.seek(0)
            raise RuntimeError(f'{script} failed startup (exit {proc.poll()}, last HTTP error {last_error}):\n{log.read()}')
        finally:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', default='HEAD')
    parser.add_argument('--skip-tests', action='store_true')
    args = parser.parse_args()
    revision = subprocess.check_output(['git', 'rev-parse', '--verify', args.ref + '^{commit}'], cwd=ROOT, text=True).strip()
    archive = subprocess.check_output(['git', 'archive', revision], cwd=ROOT)
    with tempfile.TemporaryDirectory(prefix='whack-checkout-') as tmp:
        checkout = Path(tmp)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            # Only a local git commit is exported, never an externally supplied tarball.
            tar.extractall(checkout)
        for required in ('urban.py', 'test_urban.py'):
            if not (checkout / 'services' / 'city' / required).is_file():
                raise RuntimeError(f'{revision[:12]} is missing tracked services/city/{required}; '
                                   'local/untracked copies do not ship to other machines')
        raw = checkout / 'empty-raw'
        raw.mkdir()
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')
               and not k.startswith(('CITY_', 'MULTIVERSE_', 'SIM_', 'TOPS_'))}
        env.update(CITY_RAW=str(raw), CITY_HOST='127.0.0.1', CITY_VENUE_HOST='127.0.0.1',
                   CITY_LEDGER_DB=str(checkout / 'isolated-ledger.db'), PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1')
        print(f'Checking committed snapshot {revision[:12]} with Python {sys.version.split()[0]}', flush=True)
        smoke(checkout, env, 'server.py', 'CITY_PORT', '/markets', 'markets')
        smoke(checkout, env, 'venue_server.py', 'CITY_VENUE_PORT', '/contracts', 'contracts')
        if not args.skip_tests:
            subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'services/city'],
                           cwd=checkout, env=env, check=True)
        print('PASS clean committed checkout; no local files or live ledger used', flush=True)


if __name__ == '__main__':
    main()
