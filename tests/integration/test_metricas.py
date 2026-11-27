from sqlalchemy import insert

from app.config import Settings, get_settings
from app.db.models import EnriquecimentoCampo, EnriquecimentoConsulta, EnriquecimentoEvento
from app.main import app
from app.servicos.consumo import mes_corrente, reservar
from app.servicos.metricas import calcular_metricas


def _evento(sessao, contact_id, status, campos_aplicados=0, campos_nao_aplicados=0):
    ev = EnriquecimentoEvento(hubspot_contact_id=contact_id, status=status)
    sessao.add(ev)
    sessao.flush()
    linhas = [
        {"evento_id": ev.id, "campo": f"c{i}", "valor": "v", "fonte": "brasilapi",
         "confianca": 1, "aplicado": i < campos_aplicados}
        for i in range(campos_aplicados + campos_nao_aplicados)
    ]
    if linhas:
        sessao.execute(insert(EnriquecimentoCampo), linhas)
    return ev.id


def _consulta(sessao, evento_id, fonte, resultado):
    sessao.execute(insert(EnriquecimentoConsulta).values(
        evento_id=evento_id, fonte=fonte, resultado=resultado
    ))


def _popular(sessao):
    a = _evento(sessao, "1", "concluido", campos_aplicados=4)
    b = _evento(sessao, "2", "concluido", campos_aplicados=2, campos_nao_aplicados=5)
    c = _evento(sessao, "3", "concluido")
    d = _evento(sessao, "4", "erro")
    _evento(sessao, "5", "pendente")
    for ev, resultado in ((a, "com_dados"), (b, "com_dados"), (c, "sem_dados"), (d, "erro")):
        _consulta(sessao, ev, "brasilapi", resultado)
    _consulta(sessao, a, "prov", "com_dados")
    _consulta(sessao, b, "prov", "limite_atingido")
    reservar(sessao, "prov", 100)
    sessao.commit()


def test_metricas_vazias(sessao):
    m = calcular_metricas(sessao, {"prov": 50})
    assert m["eventos_por_status"] == {"pendente": 0, "processando": 0, "concluido": 0, "erro": 0}
    assert m["fontes"] == {}
    assert m["leads_com_pelo_menos_3_campos"]["percentual"] is None
    assert m["consumo_mensal"]["fontes"] == {"prov": {"consultas": 0, "limite": 50, "restante": 50}}


def test_metricas_calculadas(sessao):
    _popular(sessao)
    m = calcular_metricas(sessao, {"prov": 50})
    assert m["eventos_por_status"] == {"pendente": 1, "processando": 0, "concluido": 3, "erro": 1}
    brasilapi = m["fontes"]["brasilapi"]
    assert (brasilapi["consultas"], brasilapi["com_dados"], brasilapi["erro"]) == (4, 2, 1)
    assert brasilapi["taxa_sucesso"] == 0.5
    prov = m["fontes"]["prov"]
    assert (prov["consultas"], prov["limite_atingido"], prov["taxa_sucesso"]) == (1, 1, 1.0)
    leads = m["leads_com_pelo_menos_3_campos"]
    assert leads == {"concluidos": 3, "com_3_campos_ou_mais": 1, "percentual": 33.33}
    assert m["consumo_mensal"] == {
        "mes": mes_corrente(),
        "fontes": {"prov": {"consultas": 1, "limite": 50, "restante": 49}},
    }


def _settings(**kwargs):
    return Settings(hubspot_token="t", hubspot_webhook_secret="s", database_url="x", **kwargs)


def test_endpoint_metricas(client, sessao):
    _popular(sessao)
    app.dependency_overrides[get_settings] = lambda: _settings(
        fonte_dominio_ativa=True, provedor_dominio_nome="prov",
        provedor_dominio_url="https://p.test/{dominio}", provedor_dominio_api_key="k",
        provedor_dominio_mapa_campos={"setor": "s"}, limite_consumo_mensal_provedor=10,
    )
    resp = client.get("/metricas")
    assert resp.status_code == 200
    corpo = resp.json()
    assert corpo["eventos_por_status"]["concluido"] == 3
    assert corpo["fontes"]["brasilapi"]["taxa_sucesso"] == 0.5
    assert corpo["leads_com_pelo_menos_3_campos"]["percentual"] == 33.33
    assert corpo["consumo_mensal"]["fontes"]["prov"] == {"consultas": 1, "limite": 10, "restante": 9}


def test_endpoint_metricas_com_token(client, sessao):
    app.dependency_overrides[get_settings] = lambda: _settings(metricas_token="segredo-metricas")
    assert client.get("/metricas").status_code == 401
    assert client.get("/metricas", headers={"Authorization": "Bearer errado"}).status_code == 401
    ok = client.get("/metricas", headers={"Authorization": "Bearer segredo-metricas"})
    assert ok.status_code == 200
    assert ok.json()["consumo_mensal"]["fontes"] == {}
