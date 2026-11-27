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
from app.db.models import EnriquecimentoCampo, EnriquecimentoConsulta, EnriquecimentoEvento
from app.fontes.base import CampoEncontrado, DadosLead, Fonte
from app.fontes.brasilapi import FonteBrasilAPI
from app.fontes.provedor_dominio import FonteProvedorDominio
from app.hubspot.client import ClienteHubSpot
from app.hubspot.schemas import definicoes_propriedades_controle
from app.servicos import consumo
from app.servicos.normalizacao import dominio_do_email, normalizar_cnpj
from app.servicos.regras import Decisao, GravacaoAnterior, decidir
from app.utils.logging import configurar_logging
from app.utils.retry import ErroPermanente, LimitadorTaxa, backoff_evento

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
        extra = {
            "evento_id": evento.id, "hubspot_contact_id": evento.hubspot_contact_id,
            "tentativa": evento.tentativas, "erro": type(exc).__name__,
        }
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
                logger.info("evento concluido", extra={
                    "evento_id": evento.id, "hubspot_contact_id": evento.hubspot_contact_id,
                })
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

    async def _consultar_fontes(
        self, evento: EventoReservado, dados: DadosLead
    ) -> list[tuple[str, CampoEncontrado]]:
        encontrados: list[tuple[str, CampoEncontrado]] = []
        for fonte in self.fontes:
            if fonte.aplicavel(dados):
                campos = await self._consultar_fonte(evento, fonte, dados)
                encontrados += [(fonte.nome, c) for c in campos]
        return encontrados

    async def _consultar_fonte(
        self, evento: EventoReservado, fonte: Fonte, dados: DadosLead
    ) -> list[CampoEncontrado]:
        """Consulta uma fonte, respeitando o limite mensal, e registra o resultado."""
        extra = {"evento_id": evento.id, "hubspot_contact_id": evento.hubspot_contact_id,
                 "fonte": fonte.nome}
        if fonte.limite_mensal is not None and not self._reservar_consumo(fonte):
            motivo = f"limite mensal de {fonte.limite_mensal} consultas atingido"
            self._registrar_consulta(evento.id, fonte.nome, "limite_atingido", motivo)
            logger.warning("fonte pulada: limite mensal atingido", extra=extra)
            return []
        try:
            campos = await fonte.consultar(dados)
        except Exception as exc:
            self._registrar_consulta(evento.id, fonte.nome, "erro", type(exc).__name__)
            logger.warning("fonte falhou", extra={**extra, "erro": type(exc).__name__})
            raise
        resultado = "com_dados" if campos else "sem_dados"
        self._registrar_consulta(evento.id, fonte.nome, resultado, None)
        logger.info("fonte consultada", extra={**extra, "resultado": resultado,
                                                "campos": len(campos)})
        return campos

    def _reservar_consumo(self, fonte: Fonte) -> bool:
        with self.sessoes() as sessao, sessao.begin():
            return consumo.reservar(sessao, fonte.nome, fonte.limite_mensal or 0)

    def _registrar_consulta(
        self, evento_id: int, fonte: str, resultado: str, motivo: str | None
    ) -> None:
        """Último resultado de cada fonte por evento (upsert: reprocessar não duplica)."""
        stmt = insert(EnriquecimentoConsulta).values(
            evento_id=evento_id, fonte=fonte, resultado=resultado, motivo=motivo
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_enriquecimento_consulta_evento_fonte",
            set_={"resultado": resultado, "motivo": motivo, "consultado_em": _agora()},
        )
        with self.sessoes() as sessao, sessao.begin():
            sessao.execute(stmt)

    def _gravacoes_anteriores(self, sessao: Session, contact_id: str) -> dict[str, GravacaoAnterior]:
        """Última gravação aplicada por propriedade do HubSpot, para este contato.

        Por propriedade, não por campo interno: duas fontes podem gravar a mesma
        propriedade com nomes internos diferentes (ex.: razao_social e
        nome_empresa em `company`), e o valor gravado por uma não é "manual"
        para a outra.
        """
        stmt = (
            select(EnriquecimentoCampo)
            .join(EnriquecimentoEvento, EnriquecimentoCampo.evento_id == EnriquecimentoEvento.id)
            .where(
                EnriquecimentoEvento.hubspot_contact_id == contact_id,
                EnriquecimentoCampo.aplicado.is_(True),
            )
            .order_by(EnriquecimentoCampo.id)
        )
        mapa = self.settings.mapa_propriedades
        return {
            mapa[c.campo]: GravacaoAnterior(c.valor or "", Decimal(c.confianca))
            for c in sessao.scalars(stmt)
            if c.campo in mapa
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
                anteriores.get(prop) if prop else None, self.settings.confianca_minima,
            )
            if decisao.gravar:
                atuais[prop] = campo.valor
                anteriores[prop] = GravacaoAnterior(campo.valor, campo.confianca)
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
        encontrados = await self._consultar_fontes(evento, dados)
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


def montar_fontes(http: httpx.AsyncClient, settings: Settings) -> list[Fonte]:
    """Fontes habilitadas. As gratuitas vêm antes: se uma delas falhar, o evento
    volta para a fila sem gastar consulta da fonte paga."""
    fontes: list[Fonte] = [FonteBrasilAPI(http, settings.brasilapi_url_base)]
    if settings.fonte_dominio_ativa:
        fontes.append(FonteProvedorDominio(
            http,
            nome=settings.provedor_dominio_nome,
            url=settings.provedor_dominio_url,
            api_key=settings.provedor_dominio_api_key.get_secret_value(),
            mapa_campos=settings.provedor_dominio_mapa_campos,
            limite_mensal=settings.limite_consumo_mensal_provedor,
            confianca=settings.provedor_dominio_confianca,
            caminho_confianca=settings.provedor_dominio_caminho_confianca,
            header_auth=settings.provedor_dominio_header_auth,
            prefixo_auth=settings.provedor_dominio_prefixo_auth,
            limitador=LimitadorTaxa(
                settings.provedor_dominio_limite_taxa, settings.provedor_dominio_janela_taxa_segundos
            ),
        ))
    return fontes


async def main() -> None:
    from app.db.session import get_sessionmaker

    settings = get_settings()
    configurar_logging(settings.log_level)
    async with httpx.AsyncClient() as http:
        hubspot = ClienteHubSpot(
            http, settings.hubspot_token.get_secret_value(), settings.hubspot_url_base
        )
        fontes = montar_fontes(http, settings)
        worker = Worker(get_sessionmaker(), hubspot, fontes, settings)
        await worker.preparar()
        logger.info("worker iniciado", extra={"fontes": [f.nome for f in fontes]})
        while True:
            if await worker.executar_ciclo() == 0:
                await asyncio.sleep(settings.worker_intervalo_segundos)


if __name__ == "__main__":
    asyncio.run(main())
