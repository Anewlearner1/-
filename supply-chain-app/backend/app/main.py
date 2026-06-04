from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import engine
from app.models.analysis import Base as AnalysisBase
from app.models.user import Base as UserBase
from app.routers import analysis, analyses, stocks


@asynccontextmanager
async def lifespan(app: FastAPI):
    UserBase.metadata.create_all(bind=engine)
    AnalysisBase.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Supply Chain Intelligence API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analysis.router)
app.include_router(analyses.router)
app.include_router(stocks.router)


@app.get("/health")
def health():
    return {"status": "ok", "env": settings.app_env}
