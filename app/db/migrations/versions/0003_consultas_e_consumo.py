"""consultas por fonte e contador de consumo mensal

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "enriquecimento_consulta",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column(
            "evento_id",
            sa.BigInteger,
            sa.ForeignKey("enriquecimento_evento.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fonte", sa.String(50), nullable=False),
        sa.Column("resultado", sa.String(20), nullable=False),
        sa.Column("motivo", sa.String(200), nullable=True),
        sa.Column(
            "consultado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "resultado IN ('com_dados', 'sem_dados', 'erro', 'limite_atingido')",
            name="ck_enriquecimento_consulta_resultado",
        ),
        sa.UniqueConstraint("evento_id", "fonte", name="uq_enriquecimento_consulta_evento_fonte"),
    )
    op.create_table(
        "consumo_fonte",
        sa.Column("fonte", sa.String(50), primary_key=True),
        sa.Column("mes", sa.String(7), primary_key=True),
        sa.Column("consultas", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column(
            "atualizado_em", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("consultas >= 0", name="ck_consumo_fonte_consultas"),
    )


def downgrade() -> None:
    op.drop_table("consumo_fonte")
    op.drop_table("enriquecimento_consulta")
