import csv
import io
import json
import logging

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, ollama, progress, taxonomy, tutor
from .config import STATIC_DIR

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(message)s")
app = FastAPI(title="themaths")
db.init()


@app.middleware("http")
async def revalidate_static(request, call_next):
    # A local app: always revalidate, so an updated script is never served stale.
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def sse(gen):
    async def body():
        try:
            async for event in gen:
                yield f"data: {json.dumps(event)}\n\n"
        except ollama.OllamaError as exc:
            yield f"data: {json.dumps({'type': 'error', 'text': str(exc)})}\n\n"
        except LookupError as exc:
            yield f"data: {json.dumps({'type': 'error', 'text': str(exc)})}\n\n"

    return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


# Requests ---------------------------------------------------------------

class GenerateIn(BaseModel):
    request: str = ""
    track: str | None = None
    topic: str | None = None
    difficulty: str | None = None
    related_attempt: int | None = None


class ImportIn(BaseModel):
    statement: str
    track: str | None = None


class ImageIn(BaseModel):
    image: str  # base64, no data: prefix


class WorkingIn(BaseModel):
    working: str = ""
    answer: str = ""
    question: str = ""
    reason: str = ""


# Status and settings ----------------------------------------------------

@app.get("/api/status")
async def status():
    settings = db.get_settings()
    try:
        models = await ollama.list_models()
        running = True
    except Exception:
        models, running = [], False
    wanted = {settings["reasoning_model"], settings["vision_model"]}
    return {"ollama": running, "models": models, "missing": sorted(m for m in wanted if running and m not in models), "settings": settings}


@app.get("/api/settings")
def get_settings():
    return db.get_settings()


@app.put("/api/settings")
def put_settings(values: dict):
    return db.put_settings(values)


@app.get("/api/taxonomy")
def get_taxonomy():
    return taxonomy.load()


# Problems ---------------------------------------------------------------

@app.post("/api/problems/generate")
def generate(body: GenerateIn):
    return sse(tutor.create_problem(**body.model_dump()))


@app.post("/api/problems/import")
def import_problem(body: ImportIn):
    if not body.statement.strip():
        raise HTTPException(400, "The problem is empty.")
    return sse(tutor.create_problem(statement=body.statement.strip(), track=body.track))


@app.post("/api/transcribe")
def transcribe(body: ImageIn):
    return sse(tutor.transcribe(body.image))


# Attempts ---------------------------------------------------------------

def _state_or_404(attempt_id: int) -> dict:
    state = tutor.attempt_state(attempt_id)
    if not state:
        raise HTTPException(404, "No such attempt.")
    return state


@app.get("/api/attempts/{attempt_id}")
def get_attempt(attempt_id: int):
    return _state_or_404(attempt_id)


@app.put("/api/attempts/{attempt_id}/working")
def save_working(attempt_id: int, body: WorkingIn):
    _state_or_404(attempt_id)
    db.update_attempt(attempt_id, working=body.working, final_answer=body.answer)
    return {"ok": True}


@app.post("/api/attempts/{attempt_id}/check")
def check(attempt_id: int, body: WorkingIn):
    _state_or_404(attempt_id)
    return tutor.check_answer(attempt_id, body.answer, body.working)


@app.post("/api/attempts/{attempt_id}/analyse")
def analyse(attempt_id: int, body: WorkingIn):
    return sse(tutor.analyse(attempt_id, body.working, body.answer))


@app.post("/api/attempts/{attempt_id}/hint")
def hint(attempt_id: int, body: WorkingIn):
    return sse(tutor.hint(attempt_id, body.working))


@app.post("/api/attempts/{attempt_id}/solution")
def solution(attempt_id: int):
    return sse(tutor.solution(attempt_id))


@app.post("/api/attempts/{attempt_id}/explain")
def explain(attempt_id: int, body: WorkingIn):
    return sse(tutor.explain(attempt_id, body.working, body.question))


@app.post("/api/attempts/{attempt_id}/flag")
def flag(attempt_id: int, body: WorkingIn):
    return sse(tutor.flag(attempt_id, body.reason))


# Progress and history ---------------------------------------------------

@app.get("/api/progress")
def get_progress():
    return progress.summary()


@app.get("/api/history")
def get_history():
    return db.history()


@app.get("/api/export.json")
def export_json():
    return JSONResponse(db.export_rows(), headers={"Content-Disposition": "attachment; filename=themaths.json"})


@app.get("/api/export.csv")
def export_csv():
    rows = db.export_rows()
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=themaths.csv"})


# Frontend ---------------------------------------------------------------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")
