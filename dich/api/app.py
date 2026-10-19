# The web API: reads and writes the database, never runs a model (that is the workers' job, started
# alongside by `dich serve`). Live updates go out as Server-Sent Events read from the events table.
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from dich import settings
from dich.queue.sqlite import Store

WEB_DIST = settings.ROOT / "web" / "dist"


@dataclass
class Context:
    store: Store
    root: Path
    data_dir: Path
    supervisor: object = None   # the worker processes started with the app, if any

    @property
    def uploads(self):
        return self.data_dir / "uploads"

    @property
    def voices(self):
        return self.data_dir / "voices"

    def inside(self, path, extra=()):
        """path resolved, if it is under the UI root, the uploads or `extra` folders; 403 otherwise."""
        path = Path(path).expanduser()
        path = (path if path.is_absolute() else self.root / path).resolve()
        allowed = [self.root, self.uploads.resolve(), *[Path(p).resolve() for p in extra]]
        if not any(path == base or base in path.parents for base in allowed):
            raise HTTPException(403, f"Chỉ được dùng file trong {self.root}")
        return path

    def existing(self, path, extra=()):
        path = self.inside(path, extra)
        if not path.exists():
            raise HTTPException(404, f"Không tìm thấy {path}")
        return path


def create_app(root=None, db_path=None, data_dir=None, workers=True):
    """workers: start the worker processes with the app (False in tests)."""
    from dich.api import bulk_downloads, jobs, presets, projects, system, voices

    data_dir = Path(data_dir or settings.DATA_DIR)
    db_path = Path(db_path or data_dir / "dich.sqlite")
    root = Path(root or settings.UI_ROOT).expanduser().resolve()
    store = Store(db_path, data_dir)
    ctx = Context(store, root, data_dir)

    @asynccontextmanager
    async def lifespan(_app):
        system.prune_uploads(store, ctx.uploads)
        supervisor = None
        if workers:
            from dich.runtime.supervisor import Supervisor
            supervisor = Supervisor(db_path, data_dir, counts_file=data_dir / "workers.json").start()
            ctx.supervisor = supervisor
        yield
        if supervisor:
            supervisor.stop()

    app = FastAPI(title="dich", lifespan=lifespan, generate_unique_id_function=lambda route: route.name)
    app.state.ctx = ctx
    app.include_router(jobs.router(ctx), prefix="/api")
    app.include_router(system.router(ctx), prefix="/api")
    app.include_router(presets.router(ctx), prefix="/api")
    app.include_router(projects.router(ctx), prefix="/api")
    app.include_router(voices.router(ctx), prefix="/api")
    app.include_router(bulk_downloads.router(ctx), prefix="/api")

    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    else:
        @app.get("/", response_class=PlainTextResponse, include_in_schema=False)
        def missing_web():
            return "Chưa build giao diện: cd web && npm install && npm run build"
    return app


def is_loopback(host):
    """True for an address only this machine can reach (localhost, 127.x, ::1)."""
    import ipaddress
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def launch(root=None, port=None, host=None, open_browser=True):
    import threading
    import webbrowser

    import uvicorn

    port = port or settings.UI_PORT
    host = host or settings.UI_HOST
    app = create_app(root)
    url = f"http://127.0.0.1:{port}"
    print(f"Dịch video: {url}  (thư mục: {app.state.ctx.root}, dữ liệu: {app.state.ctx.data_dir})")
    if not is_loopback(host):
        print(f"⚠ Đang lắng nghe trên {host}:{port}: máy khác truy cập được và API không có xác thực"
              f" (đọc/ghi được {app.state.ctx.root}). Chỉ nên dùng trong mạng riêng như Tailscale.")
    if open_browser:
        threading.Timer(1.5, webbrowser.open, [url]).start()
    uvicorn.run(app, host=host, port=port, log_level="warning", timeout_graceful_shutdown=2)


def openapi_json():
    """`python -m dich.api.app` prints the OpenAPI schema the web client is generated from."""
    import json
    import tempfile
    folder = tempfile.mkdtemp()
    print(json.dumps(create_app(Path.home(), data_dir=folder, workers=False).openapi(), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    os.environ.setdefault("DICH_DRIVERS", "")
    openapi_json()
