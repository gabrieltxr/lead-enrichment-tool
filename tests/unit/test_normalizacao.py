import pytest

from app.servicos.normalizacao import dominio_do_email, normalizar_cnpj, normalizar_telefone


@pytest.mark.parametrize(
    ("email", "esperado"),
    [
        ("ana@Empresa.com.br", "empresa.com.br"),
        ("ana@www.empresa.com", "empresa.com"),
        (" ana@empresa.com. ", "empresa.com"),
        ("ana@gmail.com", None),
        ("ana@HOTMAIL.com", None),
        ("ana@outlook.com", None),
        ("ana@yahoo.com.br", None),
        ("sem-arroba", None),
        ("a@b@c.com", None),
        ("ana@localhost", None),
        ("ana@empr esa.com", None),
        ("", None),
        (None, None),
    ],
)
def test_dominio_do_email(email, esperado):
    assert dominio_do_email(email) == esperado


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("19.131.243/0001-97", "19131243000197"),
        ("19131243000197", "19131243000197"),
        (" 11.222.333/0001-81 ", "11222333000181"),
        ("19.131.243/0001-98", None),  # dígito verificador errado
        ("11111111111111", None),  # todos iguais
        ("1913124300019", None),  # 13 dígitos
        ("abc", None),
        ("", None),
        (None, None),
    ],
)
def test_normalizar_cnpj(valor, esperado):
    assert normalizar_cnpj(valor) == esperado


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("(11) 98765-4321", "+5511987654321"),
        ("+55 11 98765-4321", "+5511987654321"),
        ("0055 11 3385-1939", "+551133851939"),
        ("011 3385-1939", "+551133851939"),
        ("1133851939", "+551133851939"),
        ("11 88765-4321", None),  # celular sem 9
        ("0133851939", None),  # DDD inválido
        ("12345", None),
        ("", None),
        (None, None),
    ],
)
def test_normalizar_telefone(valor, esperado):
    assert normalizar_telefone(valor) == esperado
