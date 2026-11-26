# Enriquecimento automático de leads (HubSpot)

Serviço que recebe eventos de criação de contato do HubSpot, enriquece o
contato com fontes externas e grava o resultado de volta no CRM, com auditoria
no PostgreSQL.

**Estado atual: Fase 1 (base e ingestão).** O webhook valida a assinatura e
grava cada contato criado em `enriquecimento_evento` com status `pendente`.
O worker e as fontes chegam na Fase 2.

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
