from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import sessionmaker

from app.db.models import EnriquecimentoCampo, EnriquecimentoConsulta, EnriquecimentoEvento
from scripts.expurgar import expurgar, main


def _evento(sessao, contato, status="concluido", dias_atras=0):
    ev = EnriquecimentoEvento(hubspot_contact_id=contato, status=status)
    sessao.add(ev)
    sessao.flush()
    if status in ("concluido", "erro"):
        ev.processado_em = datetime.now(UTC) - timedelta(days=dias_atras)
    sessao.add(EnriquecimentoCampo(
        evento_id=ev.id, campo="razao_social", valor="X", fonte="brasilapi",
        confianca=Decimal("1.00"),
    ))
    sessao.add(EnriquecimentoConsulta(evento_id=ev.id, fonte="brasilapi", resultado="com_dados"))
    sessao.commit()
    return ev.id


def _contar(sessao, modelo):
    return sessao.scalar(select(func.count()).select_from(modelo))


def test_expurgo_por_idade_apaga_so_finalizados_antigos_e_em_cascata(sessao):
    antigo = _evento(sessao, "1", dias_atras=400)
    antigo_erro = _evento(sessao, "2", status="erro", dias_atras=400)
    recente = _evento(sessao, "3", dias_atras=10)
    pendente = _evento(sessao, "4", status="pendente")
    sessao.execute(update(EnriquecimentoEvento).where(EnriquecimentoEvento.id == pendente)
                   .values(recebido_em=datetime.now(UTC) - timedelta(days=900)))
    sessao.commit()

    assert sorted(expurgar(sessao, dias=365, dry_run=True)) == [antigo, antigo_erro]
    assert _contar(sessao, EnriquecimentoEvento) == 4
    assert sorted(expurgar(sessao, dias=365)) == [antigo, antigo_erro]
    restantes = set(sessao.scalars(select(EnriquecimentoEvento.id)))
    assert restantes == {recente, pendente}
    assert _contar(sessao, EnriquecimentoCampo) == 2
    assert _contar(sessao, EnriquecimentoConsulta) == 2


def test_expurgo_por_contato(sessao):
    _evento(sessao, "10")
    outro = _evento(sessao, "11")
    assert len(expurgar(sessao, contatos=["10"])) == 1
    assert list(sessao.scalars(select(EnriquecimentoEvento.id))) == [outro]


def test_expurgo_exige_um_criterio(sessao):
    with pytest.raises(ValueError):
        expurgar(sessao)
    with pytest.raises(ValueError):
        expurgar(sessao, dias=0)


def test_cli_expurgar(sessao, engine, monkeypatch, capsys):
    import app.db.session as db_session

    _evento(sessao, "20", dias_atras=40)
    monkeypatch.setattr(db_session, "get_sessionmaker", lambda: sessionmaker(bind=engine))
    assert main(["--dias", "30", "--dry-run"]) == 0
    assert "1 evento(s) seriam apagados" in capsys.readouterr().out
    assert main(["--contatos", "20"]) == 0
    assert capsys.readouterr().out.strip() == "1 evento(s) apagados"  # sem IDs de contato
    with pytest.raises(SystemExit):
        main(["--dias", "0"])
