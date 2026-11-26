# Enriquecimento automático de leads (HubSpot)

Serviço que recebe eventos de criação de contato do HubSpot, enriquece o
contato com fontes externas e grava o resultado de volta no CRM, com auditoria
no PostgreSQL.

**Estado atual: Fase 2 (worker e fonte CNPJ).** O webhook grava cada contato
criado como `pendente`; o worker lê o contato no HubSpot, consulta a BrasilAPI
pelo CNPJ, aplica as regras de confiança, grava as propriedades e uma nota e
registra cada campo na auditoria. A fonte por domínio e as métricas chegam na
Fase 3.

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

### Limites de API

O cliente do HubSpot limita a 90 requisições a cada 10 s (o Starter permite
100 por app) e repete 429 com backoff. A BrasilAPI é limitada a 3 por segundo.

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

## LGPD

- Logs só têm IDs; a nota no HubSpot não contém valores.
- Sócios (QSA) e telefones da resposta da BrasilAPI são descartados.
- Atenção: a razão social de MEI costuma conter o nome (e às vezes o CPF) do
  titular, ou seja, é dado pessoal.
- **Pendente:** definir o responsável e a base legal do tratamento antes de
  rodar em produção.

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
