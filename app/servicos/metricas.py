"""Métricas operacionais calculadas a partir das tabelas de auditoria.

- eventos por status;
- taxa de sucesso por fonte: consultas que trouxeram ao menos um campo, sobre
  as consultas feitas (com_dados + sem_dados + erro). Pulos por limite mensal
  aparecem à parte e não entram na taxa;
- percentual de leads (eventos concluídos) com pelo menos três campos
  preenchidos pelo enriquecimento (`aplicado = true`, campos distintos);
- consumo do mês das fontes pagas.

Só contagens: nenhuma métrica expõe valores ou dados do lead.
"""

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    RESULTADOS_CONSULTA,
    STATUS_EVENTO,
    EnriquecimentoCampo,
    EnriquecimentoConsulta,
    EnriquecimentoEvento,
)
from app.servicos.consumo import consumo_do_mes, mes_corrente

MINIMO_CAMPOS = 3


def _razao(parte: int, total: int, casas: str = "0.0001") -> float | None:
    if total == 0:
        return None
    return float((Decimal(parte) / Decimal(total)).quantize(Decimal(casas), ROUND_HALF_UP))


def eventos_por_status(sessao: Session) -> dict[str, int]:
    stmt = select(EnriquecimentoEvento.status, func.count()).group_by(EnriquecimentoEvento.status)
    contagem = dict.fromkeys(STATUS_EVENTO, 0)
    contagem.update({status: total for status, total in sessao.execute(stmt)})
    return contagem


def sucesso_por_fonte(sessao: Session) -> dict[str, dict]:
    stmt = select(
        EnriquecimentoConsulta.fonte, EnriquecimentoConsulta.resultado, func.count()
    ).group_by(EnriquecimentoConsulta.fonte, EnriquecimentoConsulta.resultado)
    fontes: dict[str, dict] = {}
    for fonte, resultado, total in sessao.execute(stmt):
        fontes.setdefault(fonte, dict.fromkeys(RESULTADOS_CONSULTA, 0))[resultado] = total
    for contagem in fontes.values():
        consultas = contagem["com_dados"] + contagem["sem_dados"] + contagem["erro"]
        contagem["consultas"] = consultas
        contagem["taxa_sucesso"] = _razao(contagem["com_dados"], consultas)
    return dict(sorted(fontes.items()))


def leads_com_campos(sessao: Session, minimo: int = MINIMO_CAMPOS) -> dict:
    campos_por_evento = (
        select(
            EnriquecimentoCampo.evento_id,
            func.count(func.distinct(EnriquecimentoCampo.campo)).label("campos"),
        )
        .where(EnriquecimentoCampo.aplicado.is_(True))
        .group_by(EnriquecimentoCampo.evento_id)
        .subquery()
    )
    stmt = (
        select(
            func.count(),
            func.count().filter(func.coalesce(campos_por_evento.c.campos, 0) >= minimo),
        )
        .select_from(EnriquecimentoEvento)
        .outerjoin(campos_por_evento, campos_por_evento.c.evento_id == EnriquecimentoEvento.id)
        .where(EnriquecimentoEvento.status == "concluido")
    )
    concluidos, com_minimo = sessao.execute(stmt).one()
    percentual = _razao(com_minimo * 100, concluidos, "0.01")
    return {
        "concluidos": concluidos,
        f"com_{minimo}_campos_ou_mais": com_minimo,
        "percentual": percentual,
    }


def consumo_mensal(sessao: Session, limites: dict[str, int]) -> dict:
    mes = mes_corrente()
    usado = consumo_do_mes(sessao, mes)
    fontes = {}
    for fonte in sorted(set(limites) | set(usado)):
        limite = limites.get(fonte)
        consultas = usado.get(fonte, 0)
        fontes[fonte] = {
            "consultas": consultas,
            "limite": limite,
            "restante": max(limite - consultas, 0) if limite is not None else None,
        }
    return {"mes": mes, "fontes": fontes}


def calcular_metricas(sessao: Session, limites: dict[str, int]) -> dict:
    return {
        "eventos_por_status": eventos_por_status(sessao),
        "fontes": sucesso_por_fonte(sessao),
        "leads_com_pelo_menos_3_campos": leads_com_campos(sessao),
        "consumo_mensal": consumo_mensal(sessao, limites),
    }
