"""Contador de consumo mensal das fontes pagas.

`reservar` soma 1 ao contador do mês corrente só se ele ainda estiver abaixo
do limite, num único `INSERT ... ON CONFLICT DO UPDATE ... WHERE`. Assim vários
workers em paralelo nunca passam do limite. Cada consulta ao provedor conta uma
unidade, encontre ele dados ou não (é assim que provedores costumam cobrar).
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import ConsumoFonte


def mes_corrente(agora: datetime | None = None) -> str:
    return (agora or datetime.now(UTC)).strftime("%Y-%m")


def reservar(sessao: Session, fonte: str, limite: int, mes: str | None = None) -> bool:
    """True se a consulta cabe no limite do mês (e já foi contada)."""
    if limite <= 0:
        return False
    stmt = insert(ConsumoFonte).values(fonte=fonte, mes=mes or mes_corrente(), consultas=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=["fonte", "mes"],
        set_={"consultas": ConsumoFonte.consultas + 1, "atualizado_em": datetime.now(UTC)},
        where=ConsumoFonte.consultas < limite,
    ).returning(ConsumoFonte.consultas)
    return sessao.execute(stmt).first() is not None


def consumo_do_mes(sessao: Session, mes: str | None = None) -> dict[str, int]:
    stmt = select(ConsumoFonte.fonte, ConsumoFonte.consultas).where(
        ConsumoFonte.mes == (mes or mes_corrente())
    )
    return {fonte: consultas for fonte, consultas in sessao.execute(stmt)}
