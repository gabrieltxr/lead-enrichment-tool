"""cria tabelas enriquecimento_evento e enriquecimento_campo

Revision ID: 0001
Revises:
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _criar_evento()
    _criar_campo()


def _criar_evento() -> None:
    op.create_table(
        "enriquecimento_evento",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("hubspot_contact_id", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pendente"),
        sa.Column("tentativas", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "recebido_em",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("processado_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("erro", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pendente', 'processando', 'concluido', 'erro')",
            name="ck_enriquecimento_evento_status",
        ),
        sa.UniqueConstraint(
            "hubspot_contact_id", name="uq_enriquecimento_evento_hubspot_contact_id"
        ),
    )
    op.create_index(
        "ix_enriquecimento_evento_status_recebido_em",
        "enriquecimento_evento",
        ["status", "recebido_em"],
    )


def _criar_campo() -> None:
    op.create_table(
        "enriquecimento_campo",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "evento_id",
            sa.BigInteger(),
            sa.ForeignKey("enriquecimento_evento.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("campo", sa.String(100), nullable=False),
        sa.Column("valor", sa.Text(), nullable=True),
        sa.Column("fonte", sa.String(50), nullable=False),
        sa.Column("confianca", sa.Numeric(3, 2), nullable=False),
        sa.Column("aplicado", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint(
            "confianca >= 0 AND confianca <= 1", name="ck_enriquecimento_campo_confianca"
        ),
    )
    op.create_index(
        "ix_enriquecimento_campo_evento_id", "enriquecimento_campo", ["evento_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_enriquecimento_campo_evento_id", table_name="enriquecimento_campo")
    op.drop_table("enriquecimento_campo")
    op.drop_index(
        "ix_enriquecimento_evento_status_recebido_em", table_name="enriquecimento_evento"
    )
    op.drop_table("enriquecimento_evento")
