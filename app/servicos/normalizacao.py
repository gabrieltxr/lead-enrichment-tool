"""Normalização de domínio de e-mail, CNPJ e telefone."""

import re

# Provedores de e-mail pessoais: o domínio não identifica a empresa do lead.
PROVEDORES_GENERICOS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "outlook.com.br", "hotmail.com",
    "hotmail.com.br", "live.com", "msn.com", "yahoo.com", "yahoo.com.br", "icloud.com",
    "me.com", "aol.com", "bol.com.br", "uol.com.br", "terra.com.br", "ig.com.br",
    "protonmail.com", "proton.me", "zoho.com", "gmx.com",
})

_PESOS_CNPJ_1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_PESOS_CNPJ_2 = (6, *_PESOS_CNPJ_1)


def dominio_do_email(email: str | None) -> str | None:
    """Domínio corporativo do e-mail, ou None se vazio, inválido ou genérico."""
    if not email or email.count("@") != 1:
        return None
    dominio = email.rsplit("@", 1)[1].strip().lower().rstrip(".")
    if dominio.startswith("www."):
        dominio = dominio[4:]
    if "." not in dominio or not re.fullmatch(r"[a-z0-9.-]+", dominio):
        return None
    rotulos = dominio.split(".")
    # Rótulos vazios ou com hífen na borda, e TLD sem letras (IP literal como
    # 10.0.0.1) não são domínio de empresa e só gastariam consulta paga.
    if any(not r or r[0] == "-" or r[-1] == "-" or len(r) > 63 for r in rotulos):
        return None
    if not re.fullmatch(r"[a-z][a-z0-9-]*", rotulos[-1]) or len(dominio) > 253:
        return None
    if dominio in PROVEDORES_GENERICOS:
        return None
    return dominio


def _digito_cnpj(base: str, pesos: tuple[int, ...]) -> str:
    resto = sum(int(d) * p for d, p in zip(base, pesos, strict=True)) % 11
    return "0" if resto < 2 else str(11 - resto)


def normalizar_cnpj(valor: str | None) -> str | None:
    """CNPJ só com 14 dígitos e dígitos verificadores válidos, ou None."""
    if not valor:
        return None
    digitos = re.sub(r"\D", "", valor)
    if len(digitos) != 14 or len(set(digitos)) == 1:
        return None
    dv1 = _digito_cnpj(digitos[:12], _PESOS_CNPJ_1)
    dv2 = _digito_cnpj(digitos[:12] + dv1, _PESOS_CNPJ_2)
    return digitos if digitos[12:] == dv1 + dv2 else None


def normalizar_telefone(valor: str | None) -> str | None:
    """Telefone brasileiro em E.164 (+55DDNNNNNNNN[N]), ou None se inválido."""
    if not valor:
        return None
    digitos = re.sub(r"\D", "", valor)
    if digitos.startswith("00"):
        digitos = digitos[2:]
    if len(digitos) in (12, 13) and digitos.startswith("55"):
        digitos = digitos[2:]
    if len(digitos) == 11 and digitos.startswith("0"):
        digitos = digitos[1:]
    if len(digitos) not in (10, 11) or digitos[0] == "0":
        return None
    if len(digitos) == 11 and digitos[2] != "9":
        return None
    return f"+55{digitos}"
