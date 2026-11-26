"""Worker: processa a fila de eventos pendentes.

Uso: python -m app.servicos.worker

Cada ciclo reserva um lote com SELECT ... FOR UPDATE SKIP LOCKED (vários
workers podem rodar em paralelo), marca como `processando`, incrementa
`tentativas` e processa um evento por vez.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.db.models import EnriquecimentoCampo, EnriquecimentoEvento
from app.fontes.base import CampoEncontrado, DadosLead, Fonte
from app.fontes.brasilapi import FonteBrasilAPI
from app.hubspot.client import ClienteHubSpot
from app.hubspot.schemas import definicoes_propriedades_controle
from app.servicos.normalizacao import dominio_do_email, normalizar_cnpj
from app.servicos.regras import Decisao, GravacaoAnterior, decidir
from app.utils.logging import configurar_logging
from app.utils.retry import ErroPermanente, backoff_evento

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EventoReservado:
    id: int
    hubspot_contact_id: str
    tentativas: int
    hubspot_nota_id: str | None


@dataclass(frozen=True)
class ResultadoCampo:
    fonte: str
    campo: CampoEncontrado
    propriedade: str | None
    decisao: Decisao


def _agora() -> datetime:
    return datetime.now(UTC)


class Worker:
    def __init__(
        self,
        sessoes: sessionmaker[Session],
        hubspot: ClienteHubSpot,
        fontes: list[Fonte],
        settings: Settings,
    ) -> None:
        self.sessoes = sessoes
        self.hubspot = hubspot
        self.fontes = fontes
        self.settings = settings

    # ---- preparação -------------------------------------------------------

    async def preparar(self) -> None:
        """Cria as propriedades de controle que faltarem e avisa sobre o mapa."""
        s = self.settings
        existentes = await self.hubspot.listar_propriedades_contato()
        for definicao in definicoes_propriedades_controle(s.propriedade_origem, s.propriedade_data):
            if definicao["name"] not in existentes:
                await self.hubspot.criar_propriedade_contato(definicao)
                logger.info("propriedade criada", extra={"propriedade": definicao["name"]})
        faltando = sorted(set(s.mapa_propriedades.values()) - existentes)
        if faltando:
            logger.warning("propriedades mapeadas inexistentes", extra={"propriedades": faltando})

    # ---- fila ---------------------------------------------------------------

    def reservar_lote(self) -> list[EventoReservado]:
        agora = _agora()
        reserva = agora + timedelta(seconds=self.settings.worker_reserva_segundos)
        with self.sessoes() as sessao, sessao.begin():
            stmt = (
                select(EnriquecimentoEvento)
                .where(
                    or_(
                        EnriquecimentoEvento.status == "pendente",
                        # Reserva vencida: o worker anterior caiu no meio.
                        EnriquecimentoEvento.status == "processando",
                    ),
                    EnriquecimentoEvento.proxima_tentativa_em <= agora,
                )
                .order_by(EnriquecimentoEvento.recebido_em)
                .limit(self.settings.worker_tamanho_lote)
                .with_for_update(skip_locked=True)
            )
            eventos = sessao.scalars(stmt).all()
            for ev in eventos:
                ev.status = "processando"
                ev.tentativas += 1
                ev.proxima_tentativa_em = reserva
            return [
                EventoReservado(e.id, e.hubspot_contact_id, e.tentativas, e.hubspot_nota_id)
                for e in eventos
            ]

    def _finalizar(self, evento_id: int, status: str, erro: str | None, espera: int = 0) -> None:
        with self.sessoes() as sessao, sessao.begin():
            ev = sessao.get(EnriquecimentoEvento, evento_id)
            ev.status = status
            ev.erro = erro
            ev.proxima_tentativa_em = _agora() + timedelta(seconds=espera)
            if status in ("concluido", "erro"):
                ev.processado_em = _agora()

    def registrar_falha(self, evento: EventoReservado, exc: Exception) -> None:
        mensagem = f"{type(exc).__name__}: {exc}"[:1000]
        definitivo = (
            isinstance(exc, ErroPermanente) or evento.tentativas >= self.settings.max_tentativas
        )
        extra = {"hubspot_contact_id": evento.hubspot_contact_id, "tentativa": evento.tentativas}
        if definitivo:
            self._finalizar(evento.id, "erro", mensagem)
            logger.error("evento com erro definitivo", extra=extra)
        else:
            espera = backoff_evento(evento.tentativas)
            self._finalizar(evento.id, "pendente", mensagem, espera)
            logger.warning("evento sera tentado de novo", extra={**extra, "espera_s": espera})

    async def executar_ciclo(self) -> int:
        eventos = self.reservar_lote()
        for evento in eventos:
            try:
                await self.processar_evento(evento)
            except Exception as exc:  # noqa: BLE001 - toda falha vira retry ou erro
                self.registrar_falha(evento, exc)
            else:
                self._finalizar(evento.id, "concluido", None)
                logger.info("evento concluido", extra={"hubspot_contact_id": evento.hubspot_contact_id})
        return len(eventos)

    # ---- processamento --------------------------------------------------------

    async def _ler_lead(self, contact_id: str) -> tuple[DadosLead, dict[str, str | None]]:
        s = self.settings
        props = [s.propriedade_email, s.propriedade_cnpj, *s.mapa_propriedades.values()]
        contato = await self.hubspot.ler_contato(contact_id, props)
        dados = DadosLead(
            hubspot_contact_id=contact_id,
            dominio=dominio_do_email(contato.get(s.propriedade_email)),
            cnpj=normalizar_cnpj(contato.get(s.propriedade_cnpj)),
        )
        return dados, contato

    async def _consultar_fontes(self, dados: DadosLead) -> list[tuple[str, CampoEncontrado]]:
        encontrados: list[tuple[str, CampoEncontrado]] = []
        for fonte in self.fontes:
            if fonte.aplicavel(dados):
                encontrados += [(fonte.nome, c) for c in await fonte.consultar(dados)]
        return encontrados

    def _gravacoes_anteriores(self, sessao: Session, contact_id: str) -> dict[str, GravacaoAnterior]:
        """Última gravação aplicada por campo interno, para este contato."""
        stmt = (
            select(EnriquecimentoCampo)
            .join(EnriquecimentoEvento, EnriquecimentoCampo.evento_id == EnriquecimentoEvento.id)
            .where(
                EnriquecimentoEvento.hubspot_contact_id == contact_id,
                EnriquecimentoCampo.aplicado.is_(True),
            )
            .order_by(EnriquecimentoCampo.id)
        )
        return {
            c.campo: GravacaoAnterior(c.valor or "", Decimal(c.confianca))
            for c in sessao.scalars(stmt)
        }

    def _decidir_todos(
        self,
        encontrados: list[tuple[str, CampoEncontrado]],
        contato: dict[str, str | None],
        anteriores: dict[str, GravacaoAnterior],
    ) -> list[ResultadoCampo]:
        """Aplica as regras, da maior confiança para a menor, campo a campo."""
        atuais = dict(contato)
        resultados = []
        for fonte, campo in sorted(encontrados, key=lambda fc: fc[1].confianca, reverse=True):
            prop = self.settings.mapa_propriedades.get(campo.campo)
            decisao = decidir(
                campo, prop, atuais.get(prop) if prop else None,
                anteriores.get(campo.campo), self.settings.confianca_minima,
            )
            if decisao.gravar:
                atuais[prop] = campo.valor
                anteriores[campo.campo] = GravacaoAnterior(campo.valor, campo.confianca)
            resultados.append(ResultadoCampo(fonte, campo, prop, decisao))
        return resultados

    def _propriedades_para_gravar(self, resultados: list[ResultadoCampo]) -> dict[str, str]:
        gravar = {r.propriedade: r.campo.valor for r in resultados if r.decisao.gravar}
        if gravar:
            fontes = sorted({r.fonte for r in resultados if r.decisao.gravar})
            gravar[self.settings.propriedade_origem] = ",".join(fontes)
            gravar[self.settings.propriedade_data] = _agora().date().isoformat()
        return gravar

    @staticmethod
    def _gravar_auditoria(sessao: Session, evento_id: int, resultados: list[ResultadoCampo]) -> None:
        if not resultados:
            return
        linhas = [
            {
                "evento_id": evento_id, "campo": r.campo.campo, "valor": r.campo.valor,
                "fonte": r.fonte, "confianca": r.campo.confianca,
                "aplicado": r.decisao.aplicado, "motivo": r.decisao.motivo,
            }
            for r in resultados
        ]
        stmt = insert(EnriquecimentoCampo).values(linhas)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_enriquecimento_campo_evento_campo_fonte",
            set_={c: stmt.excluded[c] for c in ("valor", "confianca", "aplicado", "motivo")},
        )
        sessao.execute(stmt)

    async def processar_evento(self, evento: EventoReservado) -> None:
        dados, contato = await self._ler_lead(evento.hubspot_contact_id)
        encontrados = await self._consultar_fontes(dados)
        with self.sessoes() as sessao, sessao.begin():
            anteriores = self._gravacoes_anteriores(sessao, evento.hubspot_contact_id)
            resultados = self._decidir_todos(encontrados, contato, anteriores)
            # Auditoria e escrita no HubSpot na mesma transação: se o HubSpot
            # falhar, a auditoria é desfeita e o evento volta para a fila.
            self._gravar_auditoria(sessao, evento.id, resultados)
            gravar = self._propriedades_para_gravar(resultados)
            if gravar:
                await self.hubspot.atualizar_contato(evento.hubspot_contact_id, gravar)
        if resultados and evento.hubspot_nota_id is None:
            await self._criar_nota(evento, resultados)

    async def _criar_nota(self, evento: EventoReservado, resultados: list[ResultadoCampo]) -> None:
        nota_id = await self.hubspot.criar_nota(evento.hubspot_contact_id, corpo_nota(resultados))
        with self.sessoes() as sessao, sessao.begin():
            sessao.get(EnriquecimentoEvento, evento.id).hubspot_nota_id = nota_id


def corpo_nota(resultados: list[ResultadoCampo]) -> str:
    """Resumo da nota. Lista campos e fontes, nunca os valores."""
    aplicados = [r for r in resultados if r.decisao.gravar]
    linhas = [f"<li>{r.propriedade} ({r.fonte}, confiança {r.campo.confianca})</li>" for r in aplicados]
    preservados = sum(r.decisao.motivo == "campo_manual" for r in resultados)
    partes = ["<p><strong>Enriquecimento automático</strong></p>"]
    partes.append(f"<ul>{''.join(linhas)}</ul>" if linhas else "<p>Nenhum campo gravado.</p>")
    if preservados:
        partes.append(f"<p>{preservados} campo(s) preenchido(s) manualmente foram preservados.</p>")
    return "".join(partes)


async def main() -> None:
    from app.db.session import get_sessionmaker

    settings = get_settings()
    configurar_logging(settings.log_level)
    async with httpx.AsyncClient() as http:
        hubspot = ClienteHubSpot(
            http, settings.hubspot_token.get_secret_value(), settings.hubspot_url_base
        )
        fontes: list[Fonte] = [FonteBrasilAPI(http, settings.brasilapi_url_base)]
        worker = Worker(get_sessionmaker(), hubspot, fontes, settings)
        await worker.preparar()
        logger.info("worker iniciado", extra={"fontes": [f.nome for f in fontes]})
        while True:
            if await worker.executar_ciclo() == 0:
                await asyncio.sleep(settings.worker_intervalo_segundos)


if __name__ == "__main__":
    asyncio.run(main())
