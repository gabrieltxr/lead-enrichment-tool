# Enriquecimento automático de leads (HubSpot)

Serviço que recebe eventos de criação de contato do HubSpot, enriquece o
contato com fontes externas e grava o resultado de volta no CRM, com auditoria
no PostgreSQL.

**Estado atual: Fase 3 (segunda fonte e observabilidade).** O webhook grava
cada contato criado como `pendente`; o worker lê o contato no HubSpot, consulta
a BrasilAPI pelo CNPJ e, se ligada, uma fonte paga por domínio (com limite
mensal), aplica as regras de confiança, grava as propriedades e uma nota e
registra cada campo e cada consulta na auditoria. `GET /metricas` expõe os
indicadores de operação.

Este repositório é um **blueprint**: nada depende de uma conta específica. Os
nomes das propriedades e o mapeamento de campos são configuráveis
(`PROPRIEDADE_*`, `MAPA_PROPRIEDADES`), e as propriedades de controle são
criadas pelo worker se não existirem.

## Como funciona a ingestão

`POST /webhooks/hubspot`

1. Valida a assinatura do HubSpot pela versão que a requisição traz (ver
   [Assinatura e plano do HubSpot](#assinatura-e-plano-do-hubspot)). Falha: **401**.
2. Valida o corpo (lista de 1 a 1000 eventos com `eventId`, `subscriptionType`
   e `objectId`). Falha: **422**. A assinatura é checada antes, então um corpo
   inválido sem assinatura recebe 401.
3. Grava só eventos `contact.creation`, um por contato
   (`INSERT ... ON CONFLICT DO NOTHING` sobre `hubspot_contact_id`). Reenvios do
   HubSpot não duplicam linhas. Outros tipos de evento são ignorados.
4. Responde 200 com `{"recebidos", "gravados", "duplicados", "ignorados"}`.
   Nada é processado de forma síncrona.

`GET /health` responde 200 quando o banco responde, 503 caso contrário.

> **Atrás de proxy (Cloud Run, load balancer):** a assinatura inclui a URL que
> o HubSpot chamou. Defina `WEBHOOK_URL_PUBLICA` com a URL exata cadastrada no
> app, senão o esquema/host vistos pelo serviço podem diferir e tudo vira 401.

## Assinatura e plano do HubSpot

| Versão | Headers | Cálculo | Quem envia |
| --- | --- | --- | --- |
| v3 | `X-HubSpot-Signature-v3`, `X-HubSpot-Request-Timestamp` | HMAC-SHA256 base64 de método + URI + corpo + timestamp; timestamp com mais de `JANELA_ASSINATURA_SEGUNDOS` é rejeitado | Versão atual documentada |
| v1 | `X-HubSpot-Signature` (versão `v1` ou ausente) | SHA-256 hex de secret + corpo | Assinaturas de webhook de apps |
| v2 | `X-HubSpot-Signature`, versão `v2` | SHA-256 hex de secret + método + URI + corpo | Ação "Send a webhook" de workflows |

`ASSINATURAS_ACEITAS` (padrão `v3,v1`) define quais valem. Se a requisição traz
v3, só a v3 é verificada. A v1 não tem timestamp e não protege contra replay;
a idempotência por contato limita o efeito. Quando você confirmar qual versão sua
conta envia, restrinja a lista a ela.

**Plano Starter:** a ação de webhook de workflow exige Data Hub Professional ou
Enterprise, então no Starter use um app com assinatura de webhook para criação
de contato (`contact.creation`, ou `object.creation` com `objectType: contact`).
Desde 26/10/2026 o HubSpot não cria mais apps privados legados; crie o app na
nova plataforma de desenvolvedor (projects). O secret de validação é o client
secret do app. Limites da API no Starter: 100 requisições a cada 10 s por app e
250.000 por dia por conta.

## Worker

```bash
python -m app.servicos.worker      # ou o serviço `worker` do docker compose
```

Ao iniciar, cria no HubSpot as propriedades `origem_enriquecimento` (texto) e
`data_enriquecimento` (data) se faltarem, e avisa se alguma propriedade de
`MAPA_PROPRIEDADES` não existe na conta. Depois, em loop:

1. Reserva até `WORKER_TAMANHO_LOTE` eventos com `SELECT ... FOR UPDATE SKIP
   LOCKED`, marca `processando`, soma 1 em `tentativas` e reserva o evento por
   `WORKER_RESERVA_SEGUNDOS`. Se o worker cair, o evento volta para a fila
   quando a reserva vence. Vários workers podem rodar juntos.
2. Lê o contato, normaliza o domínio do e-mail (ignorando provedores como
   gmail.com e hotmail.com) e o CNPJ da propriedade `PROPRIEDADE_CNPJ`
   (dígitos verificadores conferidos).
3. Consulta cada fonte aplicável. A BrasilAPI só roda com CNPJ válido, tem
   timeout de 10 s e até 4 tentativas com backoff exponencial em timeout, falha
   de rede, 429 e 5xx. CNPJ não encontrado (404) não é erro.
4. Aplica as regras (abaixo), grava as propriedades e a auditoria na mesma
   transação: se a escrita no HubSpot falhar, a auditoria é desfeita.
5. Cria uma nota no contato listando campos e fontes, **sem os valores**.
6. Marca `concluido`.

Em caso de falha, o evento volta para `pendente` com espera de 1, 2, 4... min
(até 1 h). Ao atingir `MAX_TENTATIVAS`, ou num erro permanente (ex.: contato
não existe, HTTP 4xx), vira `erro` com a mensagem.

### Regras de gravação

| Situação | Grava? | `motivo` na auditoria |
| --- | --- | --- |
| Confiança abaixo de `CONFIANCA_MINIMA` (0.70) | não | `confianca_baixa` |
| Campo sem propriedade em `MAPA_PROPRIEDADES` | não | `sem_mapeamento` |
| Propriedade vazia no HubSpot | sim | `campo_vazio` |
| Valor atual não foi gravado pelo serviço (manual) | **nunca** | `campo_manual` |
| Valor atual é a última gravação do serviço e a nova confiança é maior | sim | `confianca_maior` |
| Idem, com confiança igual ou menor | não | `confianca_nao_maior` |
| Valor atual já é o novo valor | não | `ja_aplicado` / `valor_igual_existente` |

CNPJ exato tem confiança 1.00. `origem_enriquecimento` recebe as fontes que
gravaram algo e `data_enriquecimento` a data da gravação.

### Idempotência

Processar o mesmo evento de novo não duplica nada: a auditoria tem chave única
`(evento_id, campo, fonte)` com upsert, a nota só é criada se
`hubspot_nota_id` estiver vazio, e valores já gravados não são regravados.

### Reprocessar eventos com erro

```bash
python -m scripts.reprocessar --todos --dry-run
python -m scripts.reprocessar --ids 12 15
```

Volta os eventos para `pendente` com `tentativas` zerado.

## Fonte por domínio (paga, opcional)

Desligada por padrão (`FONTE_DOMINIO_ATIVA=false`). O provedor padrão é a
**Apollo** ([Organization Enrichment](https://docs.apollo.io/reference/organization-enrichment)),
que cobre empresas de qualquer país. O código não depende dela: a fonte parte
de um preset (`PROVEDOR_DOMINIO_PRESET`) e cada `PROVEDOR_DOMINIO_*` definido
sobrescreve o valor do preset. Trocar de provedor é trocar variáveis de
ambiente.

**Ligar com a Apollo:**

```bash
FONTE_DOMINIO_ATIVA=true
PROVEDOR_DOMINIO_API_KEY=...            # chave com acesso a organizations/enrich
LIMITE_CONSUMO_MENSAL_PROVEDOR=1000     # créditos por mês que você aceita gastar
```

O preset `apollo` chama `GET https://api.apollo.io/api/v1/organizations/enrich?domain={dominio}`
com o header `x-api-key` (1 crédito por consulta), limita a 200 chamadas por
hora e extrai os campos abaixo. A conta de teste mostrou, nos headers
`x-rate-limit-*`, 50 por minuto, 200 por hora e 600 por dia; picos acima de 50
por minuto recebem 429 e são repetidos com backoff. Ajuste ao seu plano com
`PROVEDOR_DOMINIO_LIMITE_TAXA`/`PROVEDOR_DOMINIO_JANELA_TAXA_SEGUNDOS`, e
mantenha `LIMITE_CONSUMO_MENSAL_PROVEDOR` dentro dos créditos do plano.

| Campo interno | Caminho na resposta | Propriedade padrão no HubSpot |
| --- | --- | --- |
| `nome_empresa` | `organization.name` | `company` |
| `site` | `organization.website_url` | `website` |
| `setor` | `organization.industry` | `industry` |
| `pais` | `organization.country` | `country` |
| `municipio`, `uf`, `cep`, `endereco` | `organization.city`, `.state`, `.postal_code`, `.street_address` | `city`, `state`, `zip`, `address` |
| `linkedin_empresa`, `num_funcionarios`, `receita_anual`, `ano_fundacao`, `descricao` | `organization.linkedin_url`, `.estimated_num_employees`, `.annual_revenue`, `.founded_year`, `.short_description` | só auditoria (mapeie em `MAPA_PROPRIEDADES` se quiser) |

Telefones da resposta são ignorados. Testado com chave real em 27/11/2026:
um domínio que a Apollo não conhece responde **200 com corpo `{}`**, tratado
como "sem dados", e também conta no uso (por isso o contador mensal conta toda
consulta). `annual_revenue` vem como número decimal (`150000000.0`) e é gravado
como inteiro. 401 (chave inválida) e 403 (chave sem acesso ao endpoint) viram erro
permanente do evento, para o problema aparecer logo.

**Outro provedor** (`PROVEDOR_DOMINIO_PRESET=personalizado`):

| Variável | Para quê |
| --- | --- |
| `PROVEDOR_DOMINIO_URL` | URL de consulta com o marcador `{dominio}` (obrigatória) |
| `PROVEDOR_DOMINIO_API_KEY` | Chave do provedor (obrigatória) |
| `PROVEDOR_DOMINIO_HEADER_AUTH` / `PROVEDOR_DOMINIO_PREFIXO_AUTH` | Header e prefixo da chave. Padrão `Authorization: Bearer <chave>` |
| `PROVEDOR_DOMINIO_MAPA_CAMPOS` | JSON campo interno → caminho na resposta, ex. `{"setor": "empresa.setor", "cidade_sede": "enderecos.0.cidade"}` (obrigatório) |
| `PROVEDOR_DOMINIO_NOME` | Nome da fonte na auditoria, em `origem_enriquecimento` e nas métricas |
| `PROVEDOR_DOMINIO_LIMITE_TAXA` / `PROVEDOR_DOMINIO_JANELA_TAXA_SEGUNDOS` | Limite de taxa (padrão 2 por segundo) |

Valem para os dois: `PROVEDOR_DOMINIO_CONFIANCA` (padrão 0.85: domínio exato,
abaixo do CNPJ exato), `PROVEDOR_DOMINIO_CAMINHO_CONFIANCA` (opcional: caminho
de um score 0 a 1 da resposta; vale o menor) e `LIMITE_CONSUMO_MENSAL_PROVEDOR`.

Para adicionar um preset para outro provedor, inclua uma entrada em `PRESETS`
(`app/fontes/provedor_dominio.py`) e no `Literal` de `provedor_dominio_preset`.

Para um campo do provedor ir para o HubSpot, ele também precisa estar em
`MAPA_PROPRIEDADES` (ex.: `"num_funcionarios": "numberofemployees"`). Sem isso
ele só vai para a auditoria (`sem_mapeamento`). Quando a BrasilAPI e o provedor
gravam a mesma propriedade (ex.: `razao_social` e `nome_empresa` em `company`),
vence a maior confiança (CNPJ exato, 1.00). A proteção de campo manual compara
por propriedade: um valor que o próprio serviço gravou, por qualquer fonte, não
conta como manual.

- Só é consultada quando o e-mail tem domínio corporativo (gmail.com e
  similares são ignorados).
- Timeout de 10 s, até 3 tentativas em timeout, falha de rede, 429 e 5xx. 404 ou
  204 é "domínio desconhecido", não erro. 401/403 é erro permanente.
- Roda depois da BrasilAPI: se a fonte gratuita falhar, o evento volta para a
  fila sem gastar consulta paga.

### Controle de consumo mensal

A tabela `consumo_fonte` guarda um contador por fonte e mês (AAAA-MM, UTC). Antes
de cada consulta o worker soma 1 com `INSERT ... ON CONFLICT DO UPDATE ... WHERE
consultas < limite`, numa única instrução: vários workers em paralelo nunca
passam do limite. Cada consulta feita conta uma unidade, com ou sem dados (é
como os provedores costumam cobrar), inclusive novas tentativas de um evento
que falhou. Ao atingir o limite, a fonte é pulada, o motivo vai para
`enriquecimento_consulta` (`limite_atingido`) e para o log, e o evento segue
com as outras fontes. O contador zera sozinho na virada do mês.

## Métricas

`GET /metricas` (com `METRICAS_TOKEN` definido, exige
`Authorization: Bearer <token>`). Só contagens, nenhum dado de lead:

```json
{
  "eventos_por_status": {"pendente": 1, "processando": 0, "concluido": 3, "erro": 1},
  "fontes": {
    "brasilapi": {"consultas": 4, "com_dados": 2, "sem_dados": 1, "erro": 1,
                  "limite_atingido": 0, "taxa_sucesso": 0.5}
  },
  "leads_com_pelo_menos_3_campos": {"concluidos": 3, "com_3_campos_ou_mais": 1, "percentual": 33.33},
  "consumo_mensal": {"mes": "2026-11", "fontes": {"apollo": {"consultas": 1, "limite": 1000, "restante": 999}}}
}
```

- **Taxa de sucesso por fonte:** consultas que trouxeram pelo menos um campo,
  dividido por consultas feitas (`com_dados + sem_dados + erro`). Pulos por
  limite aparecem em `limite_atingido` e não entram na conta. Vale o último
  resultado de cada fonte em cada evento (tabela `enriquecimento_consulta`).
- **Leads com pelo menos três campos:** eventos `concluido` com três ou mais
  campos distintos preenchidos pelo enriquecimento (`aplicado = true` na
  auditoria), sobre o total de concluídos. `null` quando ainda não há base.

## Logs

JSON em uma linha por evento, com `evento_id`, `hubspot_contact_id`, `fonte`,
resultado e contagens. Nunca nome, e-mail ou telefone. Como proteção extra, o
formatador troca por `[removido]` campos com nomes de dados pessoais ou de
segredos (`email`, `nome`, `telefone`, `token`, `api_key`...) e mascara, na
mensagem, nos valores e no traceback, qualquer coisa com cara de e-mail,
telefone, CPF/CNPJ ou token `Bearer`. Os logs do uvicorn passam pelo mesmo
formatador, e os do httpx (que trazem URLs) só aparecem a partir de WARNING.

### Limites de API

O cliente do HubSpot limita a 90 requisições a cada 10 s (o Starter permite
100 por app) e repete 429 com backoff. A BrasilAPI é limitada a 3 por segundo
e o provedor por domínio a `PROVEDOR_DOMINIO_LIMITE_TAXA` chamadas por
`PROVEDOR_DOMINIO_JANELA_TAXA_SEGUNDOS` (Apollo: 200 por hora).

## Execução local

Pré-requisitos: Docker com Compose, ou Python 3.12 e um PostgreSQL 16.

```bash
cp .env.example .env          # preencha HUBSPOT_WEBHOOK_SECRET e HUBSPOT_TOKEN
docker compose up --build     # sobe db, roda as migrações e a API em :8080
curl localhost:8080/health
```

Sem Docker:

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
alembic upgrade head          # usa DATABASE_URL
uvicorn app.main:app --reload --port 8080
```

## Testes

Testes unitários não precisam de banco. Os de integração usam um PostgreSQL
real apontado por `TEST_DATABASE_URL` e recriam o schema a cada execução, então
**use um banco só para testes**. Sem essa variável eles são ignorados (skip).

```bash
docker compose up -d db
TEST_DATABASE_URL=postgresql+psycopg2://leads:leads@localhost:5432/leads_test pytest
# ou tudo em container:
docker compose --profile tests run --rm tests
```

O compose cria o banco `leads_test` na primeira subida do volume
(`docker/initdb/`).

## Migrações

```bash
alembic upgrade head
alembic revision -m "descrição"   # nova migração em app/db/migrations/versions/
```

Um teste compara os modelos SQLAlchemy com o schema gerado pelas migrações e
falha se divergirem.

## Variáveis de ambiente

Ver `.env.example`. Obrigatórias: `HUBSPOT_TOKEN`, `HUBSPOT_WEBHOOK_SECRET`,
`DATABASE_URL`. As migrações só precisam de `DATABASE_URL`.

## Dependências fora da lista da especificação

| Pacote | Motivo |
| --- | --- |
| `alembic` | Exigido pela própria especificação para as migrações (seção 6). |
| `pydantic-settings` | Desde o Pydantic v2, `BaseSettings` vive neste pacote oficial. |
| `psycopg2-binary` | Mesmo driver `psycopg2`, distribuído com wheels; evita compilar libpq na imagem. Trocar por `psycopg2` se a política de deploy exigir build a partir do fonte. |
| `pytest-cov` (dev) | Medir a cobertura mínima de 80% pedida na seção 9. |

## Tabelas além das duas da especificação

| Tabela | Motivo |
| --- | --- |
| `enriquecimento_consulta` | Resultado de cada fonte por evento (`com_dados`, `sem_dados`, `erro`, `limite_atingido` e o motivo). Necessária para a taxa de sucesso por fonte, porque `enriquecimento_campo` só tem os campos encontrados, não as consultas vazias ou com falha. |
| `consumo_fonte` | Contador mensal atômico da fonte paga. Contar a partir de `enriquecimento_consulta` não serve: ela guarda só o último resultado por evento (subcontaria novas tentativas) e não permite reservar a consulta sem condição de corrida. |

## LGPD

- Logs só têm IDs; a nota no HubSpot não contém valores.
- Sócios (QSA) e telefones da resposta da BrasilAPI são descartados.
- O provedor por domínio recebe só o domínio corporativo, nunca o e-mail. Não
  mapeie em `PROVEDOR_DOMINIO_MAPA_CAMPOS` campos de pessoas (contatos,
  executivos): só dados da empresa.
- Atenção: a razão social de MEI costuma conter o nome (e às vezes o CPF) do
  titular, ou seja, é dado pessoal.
- Retenção: `python -m scripts.expurgar --dias N` apaga eventos finalizados há
  mais de N dias (com seus campos e consultas, em cascata) e
  `python -m scripts.expurgar --contatos ID...` atende um pedido de eliminação do
  titular. Ambos aceitam `--dry-run`. O prazo é decisão do controlador: o script
  não tem padrão e precisa ser agendado para valer. Os dados já gravados no
  HubSpot não são afetados.
- **Pendente:** definir o responsável e a base legal do tratamento antes de
  rodar em produção, e o contrato (operador de dados) com a Apollo antes de
  ligar `FONTE_DOMINIO_ATIVA`. Só a organização é consultada; o serviço não usa
  os endpoints de pessoas da Apollo.

## Segurança

- O webhook só aceita requisições assinadas pelo HubSpot (versões em
  `ASSINATURAS_ACEITAS`), com corpo de até 1 MiB (acima disso, 413).
  `HUBSPOT_WEBHOOK_SECRET` e `HUBSPOT_TOKEN` vazios impedem a inicialização.
- A v1 não tem timestamp e não protege contra replay; a idempotência por
  contato limita o efeito. Se o seu app envia `X-HubSpot-Signature-v3`, use
  `ASSINATURAS_ACEITAS=v3`.
- `PROVEDOR_DOMINIO_URL` precisa ser `https://` e não pode ter `{dominio}` no
  host: o domínio vem do e-mail do lead, e a chave da API vai em cada chamada.
- `/docs`, `/redoc` e `/openapi.json` estão desligados. Defina `METRICAS_TOKEN`
  em produção; sem ele, `GET /metricas` (só contagens) fica aberto.
- No docker compose, o Postgres só escuta em `127.0.0.1` e a senha vem de
  `POSTGRES_PASSWORD` (padrão `leads`, só para desenvolvimento). A API escuta
  em HTTP na porta 8080: em produção, exponha só por um proxy com TLS.

## Decisões da Fase 1

- **Idempotência da ingestão por contato.** A tabela tem restrição única em
  `hubspot_contact_id`. O `eventId` do HubSpot não tem garantia de unicidade,
  e um contato só é criado uma vez. Se no futuro for preciso re-enriquecer um
  contato por outro tipo de evento, essa restrição precisa ser revista.
- **`status` com CHECK** (`pendente`, `processando`, `concluido`, `erro`) e
  **`confianca` NUMERIC(3,2) entre 0 e 1**, validados no banco.
- **Logs em JSON** só com IDs; campos como `email`, `nome` e `telefone` são
  substituídos por `[removido]` se alguém os passar por engano.
- O container roda como usuário não root (uid 10001) e respeita `$PORT`.
