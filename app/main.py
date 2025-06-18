from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import pathlib
import os
import signal
import asyncio
from datetime import datetime

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    print("🛑 FastAPI server is shutting down...")

app = FastAPI(lifespan=lifespan)

BASE_DIR = pathlib.Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse("base.html", {"request": request})

@app.get("/join", response_class=HTMLResponse)
async def join(request: Request):
    return templates.TemplateResponse("join.html", {"request": request})

@app.get("/shutdown")
async def shutdown_route():
    print("🔻 Shutdown triggered via route at", datetime.now())
    asyncio.create_task(delayed_shutdown())
    return {"message": "Shutting down..."}

async def delayed_shutdown():
    await asyncio.sleep(1)
    os.kill(os.getpid(), signal.SIGINT)
