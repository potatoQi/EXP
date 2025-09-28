"""FastAPI 应用提供 REST & WebSocket 接口。"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .service import SchedulerUISession


def create_app(session: SchedulerUISession) -> FastAPI:
    app = FastAPI(title="EXP UI", version="0.1.0")

    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    router = APIRouter(prefix="/api")

    def get_session() -> SchedulerUISession:
        return session

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        index_path = static_dir / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="index.html missing")
        return HTMLResponse(index_path.read_text(encoding="utf-8"))

    @router.get("/state")
    async def state(current: SchedulerUISession = Depends(get_session)) -> JSONResponse:
        return JSONResponse(current.get_state())

    @router.get("/tasks/{task_id}")
    async def task_details(task_id: str, current: SchedulerUISession = Depends(get_session)) -> JSONResponse:
        try:
            return JSONResponse(current.get_task_details(task_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/tasks/{task_id}/logs")
    async def task_log(
        task_id: str,
        run_id: Optional[str] = Query(default=None),
        tail: int = Query(default=200, ge=10, le=5000),
        current: SchedulerUISession = Depends(get_session),
    ) -> JSONResponse:
        try:
            data = current.read_log(task_id, run_id=run_id, tail=tail)
            return JSONResponse(data)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/tasks/{task_id}/metrics/{filename}")
    async def metric_file(task_id: str, filename: str, current: SchedulerUISession = Depends(get_session)) -> JSONResponse:
        try:
            return JSONResponse(current.read_metric(task_id, filename))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/commands")
    async def enqueue_command(request: Request, current: SchedulerUISession = Depends(get_session)) -> JSONResponse:
        payload = await request.json()
        action = payload.get("action")
        if not action:
            raise HTTPException(status_code=400, detail="action required")
        command_payload = payload.get("payload") or {}
        command = current.send_command(action, command_payload)
        return JSONResponse({"status": "accepted", "command": command})

    app.include_router(router)

    @app.websocket("/ws/logs/{task_id}")
    async def websocket_logs(websocket: WebSocket, task_id: str) -> None:
        run_id = websocket.query_params.get("run_id") if websocket.query_params else None
        await websocket.accept()

        async def send_message(message):
            await websocket.send_json(message)

        try:
            await session.stream_log(task_id, run_id, send_message)
        except KeyError as exc:
            await websocket.send_json({"event": "error", "message": str(exc)})
        except WebSocketDisconnect:
            return
        except Exception as exc:  # pragma: no cover
            await websocket.send_json({"event": "error", "message": str(exc)})
        finally:
            try:
                await websocket.close()
            except Exception:
                pass

    return app


__all__ = ["create_app"]
