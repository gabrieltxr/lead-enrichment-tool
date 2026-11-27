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
    # Quando o evento pode ser (re)tentado: backoff entre tentativas e reserva
    # enquanto está em processamento.
    proxima_tentativa_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Nota criada no HubSpot; evita nota duplicada ao reprocessar.
    hubspot_nota_id: Mapped[str | None] = mapped_column(String(32))


class EnriquecimentoCampo(Base):
    __tablename__ = "enriquecimento_campo"
    __table_args__ = (
        CheckConstraint(
            "confianca >= 0 AND confianca <= 1", name="ck_enriquecimento_campo_confianca"
        ),
        UniqueConstraint(
            "evento_id", "campo", "fonte", name="uq_enriquecimento_campo_evento_campo_fonte"
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
    # Por que foi ou não aplicado (ex.: campo_vazio, campo_manual, confianca_baixa).
    motivo: Mapped[str | None] = mapped_column(String(50))


RESULTADOS_CONSULTA = ("com_dados", "sem_dados", "erro", "limite_atingido")


class EnriquecimentoConsulta(Base):
    """Resultado da última consulta de cada fonte para cada evento.

    Base da taxa de sucesso por fonte em GET /metricas. Fica fora da transação
    da auditoria: uma consulta que falhou também precisa ser contada.
    """

    __tablename__ = "enriquecimento_consulta"
    __table_args__ = (
        CheckConstraint(
            "resultado IN ('com_dados', 'sem_dados', 'erro', 'limite_atingido')",
            name="ck_enriquecimento_consulta_resultado",
        ),
        UniqueConstraint("evento_id", "fonte", name="uq_enriquecimento_consulta_evento_fonte"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    evento_id: Mapped[int] = mapped_column(
        ForeignKey("enriquecimento_evento.id", ondelete="CASCADE"), nullable=False
    )
    fonte: Mapped[str] = mapped_column(String(50), nullable=False)
    resultado: Mapped[str] = mapped_column(String(20), nullable=False)
    # Classe do erro ou motivo do pulo; nunca dados do lead.
    motivo: Mapped[str | None] = mapped_column(String(200))
    consultado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConsumoFonte(Base):
    """Contador mensal de consultas por fonte paga (mês no formato AAAA-MM, UTC)."""

    __tablename__ = "consumo_fonte"
    __table_args__ = (
        CheckConstraint("consultas >= 0", name="ck_consumo_fonte_consultas"),
    )

    fonte: Mapped[str] = mapped_column(String(50), primary_key=True)
    mes: Mapped[str] = mapped_column(String(7), primary_key=True)
    consultas: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
