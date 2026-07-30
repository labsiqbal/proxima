from __future__ import annotations

import base64
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
API_PYTHON = ROOT / "apps" / "api" / ".venv" / "bin" / "python"
WEB_DIR = ROOT / "apps" / "web"
PROBE_ROOT = ROOT / "trusted-probes" / "safe-update"
PASSWORD = "file-target-browser-password"


def _request(
    url: str,
    *,
    body: dict | None = None,
    token: str | None = None,
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
    with urlopen(request, timeout=10) as response:
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
    (container / "brief.md").write_text(
        "# Container shadow\n\nWRONG CONTAINER MARKDOWN\n",
        encoding="utf-8",
    )
    (container / "visual.png").write_bytes(b"not an image")
    (container / "handout.pdf").write_bytes(b"not a pdf")
    (ops / "brief.md").write_text(
        "# Ops direct Markdown\n\nOPS DIRECT MARKDOWN\n",
        encoding="utf-8",
    )
    (ops / "ops-only.md").write_text(
        "# Ops only\n\nOPS ROOT FILE\n",
        encoding="utf-8",
    )
    (ops / "visual.png").write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFElEQVR42mP8z8AARMA"
            "gYGBgAAARAAH+VLfGAAAAAElFTkSuQmCC"
        )
    )
    (ops / "handout.pdf").write_bytes(_pdf_fixture())


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
        connection.execute(
            "UPDATE project_areas SET rel_path = '.' "
            "WHERE project_id = ? AND kind = 'ops'",
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
            ],
        )
        connection.commit()
    finally:
        connection.close()

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
  const jsonFetch = async path => {
    const response = await fetch(path);
    const body = await response.json();
    if (!response.ok) throw new Error(`${path}: ${response.status} ${JSON.stringify(body)}`);
    return body;
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
  const checks = [];

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
    `/api/preview/canonical-browser/handout.pdf?${queryFor("handout.pdf", archivedByName["handout.pdf"].target)}`
  );
  if (String.fromCharCode(...pdfBytes.slice(0, 5)) !== "%PDF-") {
    throw new Error("PDF target resolved to the Container shadow");
  }
  checks.push("raw-image-and-preview-pdf");

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
  checks.push("legacy-layout");

  document.querySelector('[aria-label="Close tool panel"]')?.click();
  const archive = await until("Archive navigation", () => exactButton("Archive"));
  archive.click();
  await until("Archive records", () => document.querySelectorAll(".archive-row").length === 3);

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
      await until(`${name} Markdown preview`, () =>
        (overlay.querySelector(".av-doc")?.textContent || "").includes("OPS DIRECT MARKDOWN")
      );
    } else if (kind === "image") {
      await until(`${name} image preview`, () => {
        const image = overlay.querySelector("img.av-img");
        return image?.src.includes("target=") && image.complete && image.naturalWidth === 2;
      });
    } else {
      await until(`${name} PDF preview`, () => {
        const frame = overlay.querySelector("iframe.av-frame");
        return frame?.src.includes("target=");
      });
    }
    if (close) {
      overlay.querySelector('[aria-label="Close artifact review"]')?.click();
      await until(`${name} viewer close`, () => !document.querySelector(".av-overlay"));
    }
  };

  await openRecord("brief.md", "markdown");
  await openRecord("visual.png", "image");
  await openRecord("handout.pdf", "pdf", false);
  checks.push("archive-to-viewer-markdown-image-pdf");

  return {ok: true, checks};
})()
"""


def main() -> None:
    if not API_PYTHON.is_file():
        raise RuntimeError(f"API Python is unavailable: {API_PYTHON}")
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
        base_url = f"http://127.0.0.1:{port}"
        database = fixture / "candidate.db"
        environment = {
            "HOME": str(home),
            "LANG": "C.UTF-8",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "PROXIMA_CLAUDE_LIVE_HOME": "0",
            "PROXIMA_DB_PATH": str(database),
            "PROXIMA_FEATURE_MASTER_ORCHESTRATOR": "0",
            "PROXIMA_FEATURE_SAFE_SELF_UPDATE": "0",
            "PROXIMA_HERMES_PROFILES_ROOT": str(runner_home),
            "PROXIMA_LINK_ROOTS": str(workspace),
            "PROXIMA_PORT": str(port),
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
                [str(API_PYTHON), str(ROOT / "apps" / "api" / "scripts" / "serve.py")],
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
                        _request(f"{base_url}/api/health")
                        break
                    except Exception:
                        if time.monotonic() >= deadline:
                            raise RuntimeError("disposable server readiness timed out")
                        time.sleep(0.1)
                token = str(
                    _request(
                        f"{base_url}/auth/set-password",
                        body={"password": PASSWORD},
                    )["token"]
                )
                for path, name, slug in (
                    (legacy, "Legacy browser", "legacy-browser"),
                    (canonical, "Canonical browser", "canonical-browser"),
                ):
                    _request(
                        f"{base_url}/api/projects/link",
                        body={"name": name, "path": str(path), "slug": slug},
                        token=token,
                    )
                _write_fixture_files(canonical)
                _seed_registry(database, canonical, legacy)
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
                    executable=_browser(),
                    base_url=base_url,
                    scenario=scenario,
                    profile=fixture / "browser-profile",
                    auth_token=token,
                    drop_prefix=[],
                )
                print(
                    json.dumps(
                        {
                            "fixture": "disposable",
                            "ok": True,
                            "scenario": scenario["name"],
                            "transcript": json.loads(transcript),
                        },
                        sort_keys=True,
                    )
                )
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
