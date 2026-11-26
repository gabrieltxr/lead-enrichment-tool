"""API: webhook do HubSpot e healthcheck.

O endpoint do webhook só valida e grava o evento como `pendente`. Todo o
processamento acontece no worker (Fase 2).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import EnriquecimentoEvento
from app.db.session import get_session
from app.hubspot.assinatura import requisicao_autentica
from app.hubspot.schemas import TIPO_CONTATO_CRIADO, EventoWebhook, LoteWebhook
from app.utils.logging import configurar_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configurar_logging(get_settings().log_level)
    yield


app = FastAPI(title="Enriquecimento de leads", lifespan=lifespan)

SessaoDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _uri_assinada(request: Request, settings: Settings) -> str:
    """URI que o HubSpot usou ao assinar. Atrás de proxy, use WEBHOOK_URL_PUBLICA."""
    if not settings.webhook_url_publica:
        return str(request.url)
    query = request.url.query
    return settings.webhook_url_publica + (f"?{query}" if query else "")


def _gravar_eventos(sessao: Session, eventos: list[EventoWebhook]) -> int:
    """Grava eventos pendentes; reenvios do mesmo contato são ignorados."""
    if not eventos:
        return 0
    linhas = [{"hubspot_contact_id": str(e.object_id)} for e in eventos]
    stmt = (
        insert(EnriquecimentoEvento)
        .values(linhas)
        .on_conflict_do_nothing(index_elements=["hubspot_contact_id"])
        .returning(EnriquecimentoEvento.id)
    )
    novos = len(sessao.execute(stmt).all())
    sessao.commit()
    return novos


@app.post("/webhooks/hubspot")
async def receber_webhook(request: Request, sessao: SessaoDep, settings: SettingsDep) -> dict:
    corpo = await request.body()
    if not requisicao_autentica(
        segredo=settings.hubspot_webhook_secret.get_secret_value(),
        metodo=request.method,
        uri=_uri_assinada(request, settings),
        corpo=corpo,
        headers=request.headers,
        janela_segundos=settings.janela_assinatura_segundos,
        versoes_aceitas=settings.versoes_assinatura,
    ):
        logger.warning("webhook com assinatura invalida")
        raise HTTPException(status_code=401, detail="assinatura inválida")

    try:
        lote = LoteWebhook.model_validate_json(corpo)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_input=False)) from exc

    criacoes = [e for e in lote.root if e.subscription_type == TIPO_CONTATO_CRIADO]
    novos = await run_in_threadpool(_gravar_eventos, sessao, criacoes)
    for evento in criacoes:
        logger.info("evento recebido", extra={"hubspot_contact_id": str(evento.object_id)})
    return {
        "recebidos": len(lote.root),
        "gravados": novos,
        "duplicados": len(criacoes) - novos,
        "ignorados": len(lote.root) - len(criacoes),
    }


@app.get("/health")
def health(sessao: SessaoDep) -> JSONResponse:
    try:
        sessao.execute(text("SELECT 1"))
    except Exception:
        logger.exception("healthcheck: banco indisponivel")
        return JSONResponse(status_code=503, content={"status": "erro", "banco": "indisponivel"})
    return JSONResponse(content={"status": "ok", "banco": "ok"})
