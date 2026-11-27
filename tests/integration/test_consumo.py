from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import sessionmaker

from app.servicos.consumo import consumo_do_mes, mes_corrente, reservar


def test_reserva_ate_o_limite(sessao):
    resultados = [reservar(sessao, "prov", 3) for _ in range(5)]
    sessao.commit()
    assert resultados == [True, True, True, False, False]
    assert consumo_do_mes(sessao) == {"prov": 3}


def test_limite_zero_nunca_consulta(sessao):
    assert reservar(sessao, "prov", 0) is False
    assert consumo_do_mes(sessao) == {}


def test_contador_reinicia_a_cada_mes_e_separa_fontes(sessao):
    assert reservar(sessao, "prov", 1, mes="2026-10")
    assert not reservar(sessao, "prov", 1, mes="2026-10")
    assert reservar(sessao, "prov", 1, mes="2026-11")
    assert reservar(sessao, "outra", 1, mes="2026-11")
    sessao.commit()
    assert consumo_do_mes(sessao, "2026-11") == {"prov": 1, "outra": 1}


def test_limite_vale_entre_workers_em_paralelo(engine, sessao):
    fabrica = sessionmaker(bind=engine)

    def tentar(_):
        with fabrica() as s, s.begin():
            return reservar(s, "prov", 7)

    with ThreadPoolExecutor(max_workers=10) as pool:
        resultados = list(pool.map(tentar, range(40)))
    assert sum(resultados) == 7
    assert consumo_do_mes(sessao) == {"prov": 7}


def test_mes_corrente_formato():
    assert len(mes_corrente()) == 7 and mes_corrente()[4] == "-"
