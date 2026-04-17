"""AutoFeed Sidecar — FastAPI application."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.discovery.cascade import run_discovery
from app.discovery.network_intercept import close_browser
from app.llm import LLMError
from app.llm.analyzer import recommend_strategy
from app.models.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    DiscoverRequest,
    DiscoverResponse,
    HealthResponse,
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    yield
    await close_browser()


app = FastAPI(
    title="AutoFeed Sidecar",
    description="Discovery and scraping sidecar for the FreshRSS AutoFeed extension.",
    version="0.3.0",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@app.post("/discover", response_model=DiscoverResponse)
async def discover(req: DiscoverRequest) -> DiscoverResponse:
    return await run_discovery(req)


@app.exception_handler(LLMError)
async def _llm_error_handler(request, exc: LLMError):
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=502,
        content={"url": "", "errors": [str(exc)], "recommendation": None},
    )


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    return await recommend_strategy(req)
