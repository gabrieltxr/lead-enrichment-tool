"""Tabelas de auditoria do enriquecimento."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

STATUS_EVENTO = ("pendente", "processando", "concluido", "erro")


class Base(DeclarativeBase):
    pass


class EnriquecimentoEvento(Base):
    __tablename__ = "enriquecimento_evento"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pendente', 'processando', 'concluido', 'erro')",
            name="ck_enriquecimento_evento_status",
        ),
        Index("ix_enriquecimento_evento_status_recebido_em", "status", "recebido_em"),
        UniqueConstraint(
            "hubspot_contact_id", name="uq_enriquecimento_evento_hubspot_contact_id"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Um contato só é criado uma vez, então o ID do contato é a chave de
    # idempotência da ingestão: reenvios do HubSpot não criam outra linha.
    # (O eventId do HubSpot não tem garantia de unicidade, por isso não é usado.)
    hubspot_contact_id: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'pendente'")
    )
    tentativas: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    recebido_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    erro: Mapped[str | None] = mapped_column(Text)


class EnriquecimentoCampo(Base):
    __tablename__ = "enriquecimento_campo"
    __table_args__ = (
        CheckConstraint(
            "confianca >= 0 AND confianca <= 1", name="ck_enriquecimento_campo_confianca"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    evento_id: Mapped[int] = mapped_column(
        ForeignKey("enriquecimento_evento.id", ondelete="CASCADE"), nullable=False, index=True
    )
    campo: Mapped[str] = mapped_column(String(100), nullable=False)
    valor: Mapped[str | None] = mapped_column(Text)
    fonte: Mapped[str] = mapped_column(String(50), nullable=False)
    confianca: Mapped[Decimal] = mapped_column(Numeric(3, 2), nullable=False)
    aplicado: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
