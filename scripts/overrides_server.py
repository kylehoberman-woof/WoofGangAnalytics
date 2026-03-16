#!/usr/bin/env python3
"""
Overrides server for WoofGang Commission Dashboard.
Run: python3 scripts/overrides_server.py
Then open the dashboard - overrides save automatically.
Press Ctrl+C to stop.
"""

import json
import subprocess
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import get_store

_store = get_store("port-washington")
REPO_DIR = Path(__file__).parent.resolve().parent
OVERRIDES_FILE = _store.data_dir / "overrides.json"
PORT = 5678

class OverridesHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200); self.send_cors(); self.end_headers()

    def do_GET(self):
        if self.path == "/overrides":
            try:
                data = json.loads(OVERRIDES_FILE.read_text()) if OVERRIDES_FILE.exists() else {}
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_cors(); self.end_headers(); self.wfile.write(body)
            except Exception as e: self._error(str(e))
        else: self.send_response(404); self.end_headers()

    def do_POST(self):
        if self.path == "/overrides":
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length))
                OVERRIDES_FILE.write_text(json.dumps(data, indent=2))
                print(f"  ✓ Saved {len(data)} override(s)")
                try:
                    subprocess.run(["git", "add", str(OVERRIDES_FILE)], cwd=REPO_DIR, check=True, capture_output=True)
                    r = subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=REPO_DIR, capture_output=True)
                    if r.returncode != 0:
                        subprocess.run(["git", "commit", "-m", "Update guarantee overrides"], cwd=REPO_DIR, check=True, capture_output=True)
                        subprocess.run(["git", "push"], cwd=REPO_DIR, check=True, capture_output=True)
                        print("  ✓ Pushed to GitHub")
                    else: print("  ✓ No changes to commit")
                except subprocess.CalledProcessError as e: print(f"  ⚠ Git error: {e.stderr.decode().strip()}")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_cors(); self.end_headers(); self.wfile.write(b'{"ok":true}')
            except Exception as e: self._error(str(e))
        else: self.send_response(404); self.end_headers()

    def _error(self, msg):
        print(f"  ✗ Error: {msg}")
        self.send_response(500); self.send_cors(); self.end_headers()
        self.wfile.write(json.dumps({"error": msg}).encode())

if __name__ == "__main__":
    server = HTTPServer(("localhost", PORT), OverridesHandler)
    print(f"🐾 WoofGang Overrides Server running on port {PORT}")
    print(f"   Overrides file: {OVERRIDES_FILE}")
    print(f"   Open the dashboard and overrides will save automatically.")
    print(f"   Press Ctrl+C to stop.\n")
    try: server.serve_forever()
    except KeyboardInterrupt: print("\n  Server stopped."); sys.exit(0)
