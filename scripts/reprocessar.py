"""Devolve para a fila eventos com status `erro`.

Uso:
    python -m scripts.reprocessar --todos            # todos os eventos com erro
    python -m scripts.reprocessar --ids 12 15 18     # só estes
    python -m scripts.reprocessar --todos --dry-run  # só mostra quantos seriam

O evento volta a `pendente` com `tentativas` zerado. A auditoria e a nota já
criada são mantidas, então o reprocessamento não duplica nada.
"""

import argparse
import sys
from collections.abc import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.models import EnriquecimentoEvento


def reprocessar(sessao: Session, ids: Sequence[int] | None, dry_run: bool = False) -> list[int]:
    filtro = [EnriquecimentoEvento.status == "erro"]
    if ids is not None:
        filtro.append(EnriquecimentoEvento.id.in_(ids))
    if dry_run:
        return list(sessao.scalars(select(EnriquecimentoEvento.id).where(*filtro)))
    stmt = (
        update(EnriquecimentoEvento)
        .where(*filtro)
        .values(
            status="pendente", tentativas=0, erro=None, processado_em=None,
            proxima_tentativa_em=func.now(),
        )
        .returning(EnriquecimentoEvento.id)
    )
    afetados = list(sessao.scalars(stmt))
    sessao.commit()
    return afetados


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    alvo = parser.add_mutually_exclusive_group(required=True)
    alvo.add_argument("--todos", action="store_true", help="todos os eventos com erro")
    alvo.add_argument("--ids", type=int, nargs="+", help="IDs de enriquecimento_evento")
    parser.add_argument("--dry-run", action="store_true", help="não altera nada")
    args = parser.parse_args(argv)

    from app.db.session import get_sessionmaker

    with get_sessionmaker()() as sessao:
        afetados = reprocessar(sessao, None if args.todos else args.ids, args.dry_run)
    acao = "seriam reprocessados" if args.dry_run else "voltaram para a fila"
    print(f"{len(afetados)} evento(s) {acao}: {sorted(afetados)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
