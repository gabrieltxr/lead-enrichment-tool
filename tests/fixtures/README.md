# Fixtures

Respostas das APIs externas usadas nos testes unitários, no formato
documentado de cada API.

- `brasilapi/cnpj_19131243000197.json`: formato de `GET /api/cnpj/v1/{cnpj}`.
  CNPJ público usado nos exemplos da BrasilAPI. Os dados do sócio (QSA) foram
  substituídos por valores fictícios.
- `hubspot/*.json`: formato das respostas da API CRM v3 (contato, nota e
  lista de propriedades), com dados fictícios.

Para regravar a fixture da BrasilAPI com a resposta real:

    curl -s https://brasilapi.com.br/api/cnpj/v1/19131243000197 \
      | python -m json.tool > tests/fixtures/brasilapi/cnpj_19131243000197.json

Não grave aqui respostas com dados pessoais reais (nomes, e-mails, telefones).
