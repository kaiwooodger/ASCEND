"""Optional localhost HTTP adapter over the ASCEND application controller."""

from __future__ import annotations

import json
import subprocess
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ascend import __version__
from ascend.app.controller import ApplicationController
from ascend.models.case import ASCENDCase
from ascend.models.config import CaseConfiguration


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = Path(__file__).resolve().parent / "static"


class Workstation:
    """Represent workstation state and behavior."""
    def __init__(self) -> None:
        self.controller = ApplicationController()
        self.lock = threading.RLock()

    def state(self) -> dict[str, Any]:
        """Handle state for the enclosing ASCEND workflow."""
        case = self.controller.case
        return {
            "case": case.to_dict(include_results=False) if case else None,
            "message": self.controller.state.message,
            "project_root": str(PROJECT_ROOT),
        }


WORKSTATION = Workstation()


def _choose(kind: str) -> str:
    if kind == "case_file":
        script = 'POSIX path of (choose file with prompt "Open ascend_case.json")'
    else:
        script = 'POSIX path of (choose folder with prompt "Select DICOM case directory")'
    completed = subprocess.run(["/usr/bin/osascript", "-e", script], text=True, capture_output=True)
    if completed.returncode:
        return ""
    return completed.stdout.strip()


class Handler(BaseHTTPRequestHandler):
    """Represent handler state and behavior."""
    server_version = f"ASCEND/{__version__}"

    def log_message(self, format: str, *args: Any) -> None:
        """Handle log message for the enclosing ASCEND workflow."""
        return

    def _json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        """Handle do g e t for the enclosing ASCEND workflow."""
        path = urlparse(self.path).path
        if path == "/":
            self._file(STATIC_ROOT / "index.html", "text/html; charset=utf-8")
        elif path == "/app.js":
            self._file(STATIC_ROOT / "app.js", "text/javascript; charset=utf-8")
        elif path == "/styles.css":
            self._file(STATIC_ROOT / "styles.css", "text/css; charset=utf-8")
        elif path == "/api/state":
            with WORKSTATION.lock:
                self._json(WORKSTATION.state())
        elif path.startswith("/api/result/"):
            layer = path.rsplit("/", 1)[-1]
            with WORKSTATION.lock:
                case = WORKSTATION.controller.case
                record = getattr(case, layer, None) if case else None
                self._json({"result": record.result if record else None, "error": record.error if record else None})
        elif path.startswith("/api/choose/"):
            self._json({"path": _choose(path.rsplit("/", 1)[-1])})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        """Handle do p o s t for the enclosing ASCEND workflow."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length) or b"{}")
            path = urlparse(self.path).path
            with WORKSTATION.lock:
                controller = WORKSTATION.controller
                if path == "/api/import":
                    source = Path(data["source_directory"]).expanduser().resolve()
                    case_root = Path(data.get("case_root") or PROJECT_ROOT / "runs" / source.name)
                    controller.import_case(source, case_root)
                elif path == "/api/open":
                    WORKSTATION.controller = ApplicationController(ASCENDCase.load(data["case_file"]))
                elif path == "/api/configure":
                    controller.configure(CaseConfiguration.from_dict(data))
                elif path == "/api/select-chain":
                    controller.select_dicom_chain(
                        data["chain_id"], bool(data.get("allow_incomplete_chain")), data.get("override_reason")
                    )
                elif path == "/api/cache/inspect":
                    self._json({"ok": True, "entries": controller.inspect_layer1_cache(), **WORKSTATION.state()})
                    return
                elif path == "/api/cache/clear":
                    removed = controller.clear_layer1_cache(confirmed=bool(data.get("confirmed")))
                    self._json({"ok": True, "removed_entries": removed, **WORKSTATION.state()})
                    return
                elif path == "/api/run/layer1":
                    controller.run_layer1()
                elif path == "/api/run/layer2_1":
                    controller.run_layer21()
                elif path == "/api/run/layer2_2":
                    controller.run_layer22()
                elif path == "/api/run/layer3_1":
                    controller.run_layer31()
                elif path == "/api/run/layer3_2":
                    controller.run_layer32()
                elif path == "/api/export":
                    files = controller.export()
                    self._json({"ok": True, "files": [str(item) for item in files], **WORKSTATION.state()})
                    return
                else:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self._json({"ok": True, **WORKSTATION.state()})
        except Exception as exc:
            self._json({"ok": False, "error": str(exc), **WORKSTATION.state()}, HTTPStatus.BAD_REQUEST)


def launch(host: str = "127.0.0.1", port: int = 0, open_browser: bool = True) -> None:
    """Handle launch for the enclosing ASCEND workflow."""
    server = ThreadingHTTPServer((host, port), Handler)
    address = f"http://{host}:{server.server_address[1]}/"
    print(f"ASCEND workstation: {address}", flush=True)
    if open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(address, new=1)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
