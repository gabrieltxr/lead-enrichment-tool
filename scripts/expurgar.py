"""Apaga da auditoria os eventos antigos ou de um contato (LGPD).

Uso:
    python -m scripts.expurgar --dias 365 --dry-run   # quantos seriam apagados
    python -m scripts.expurgar --dias 365             # concluídos/com erro há mais de 365 dias
    python -m scripts.expurgar --contatos 123 456     # pedido de eliminação do titular

Apagar um evento apaga em cascata seus campos (`enriquecimento_campo`) e
consultas (`enriquecimento_consulta`). Eventos `pendente` ou `processando`
nunca são apagados por idade. `consumo_fonte` não tem dado do lead e fica.

O prazo de retenção é decisão do controlador dos dados: o script não tem
padrão e precisa ser agendado (cron, job do orquestrador) para valer.
Os dados já gravados no HubSpot não são afetados.
"""

import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import EnriquecimentoEvento


def _filtro(dias: int | None, contatos: Sequence[str] | None) -> list:
    if contatos is not None:
        return [EnriquecimentoEvento.hubspot_contact_id.in_([str(c) for c in contatos])]
    limite = datetime.now(UTC) - timedelta(days=dias)
    return [
        EnriquecimentoEvento.status.in_(("concluido", "erro")),
        EnriquecimentoEvento.processado_em < limite,
    ]


def expurgar(
    sessao: Session,
    *,
    dias: int | None = None,
    contatos: Sequence[str] | None = None,
    dry_run: bool = False,
) -> list[int]:
    if (dias is None) == (contatos is None):
        raise ValueError("informe dias ou contatos")
    if dias is not None and dias < 1:
        raise ValueError("dias deve ser 1 ou mais")
    filtro = _filtro(dias, contatos)
    if dry_run:
        return list(sessao.scalars(select(EnriquecimentoEvento.id).where(*filtro)))
    stmt = delete(EnriquecimentoEvento).where(*filtro).returning(EnriquecimentoEvento.id)
    apagados = list(sessao.scalars(stmt))
    sessao.commit()
    return apagados


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    alvo = parser.add_mutually_exclusive_group(required=True)
    alvo.add_argument("--dias", type=int, help="apaga eventos finalizados há mais de N dias")
    alvo.add_argument("--contatos", nargs="+", help="IDs de contato do HubSpot")
    parser.add_argument("--dry-run", action="store_true", help="não apaga nada")
    args = parser.parse_args(argv)
    if args.dias is not None and args.dias < 1:
        parser.error("--dias deve ser 1 ou mais")

    from app.db.session import get_sessionmaker

    with get_sessionmaker()() as sessao:
        apagados = expurgar(
            sessao, dias=args.dias, contatos=args.contatos, dry_run=args.dry_run
        )
    acao = "seriam apagados" if args.dry_run else "apagados"
    # Só a contagem: IDs de contato não vão para a saída.
    print(f"{len(apagados)} evento(s) {acao}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
