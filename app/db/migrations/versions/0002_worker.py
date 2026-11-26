"""campos do worker: próxima tentativa, nota, motivo e unicidade de campo

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "enriquecimento_evento",
        sa.Column(
            "proxima_tentativa_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "enriquecimento_evento", sa.Column("hubspot_nota_id", sa.String(32), nullable=True)
    )
    op.add_column("enriquecimento_campo", sa.Column("motivo", sa.String(50), nullable=True))
    op.create_unique_constraint(
        "uq_enriquecimento_campo_evento_campo_fonte",
        "enriquecimento_campo",
        ["evento_id", "campo", "fonte"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_enriquecimento_campo_evento_campo_fonte", "enriquecimento_campo", type_="unique"
    )
    op.drop_column("enriquecimento_campo", "motivo")
    op.drop_column("enriquecimento_evento", "hubspot_nota_id")
    op.drop_column("enriquecimento_evento", "proxima_tentativa_em")
