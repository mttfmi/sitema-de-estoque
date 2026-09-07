"""Validação segura de valores vindos de request.form.

Evita que um valor inválido (texto, vazio, NaN, infinito, negativo onde não
faz sentido) derrube a aplicação com ValueError/500 — em vez disso, retorna
uma mensagem amigável para ser exibida via flash().
"""
import math


def parse_float(valor, campo="Valor", minimo=None, obrigatorio=False):
    """Converte para float com segurança.
    Retorna (valor_convertido, mensagem_erro). Se mensagem_erro is not None,
    valor_convertido é None e a conversão falhou.
    """
    if valor is None or str(valor).strip() == "":
        if obrigatorio:
            return None, f"{campo} é obrigatório."
        return 0.0, None

    try:
        v = float(str(valor).strip().replace(",", "."))
    except (ValueError, TypeError):
        return None, f"{campo} inválido. Informe um número."

    if math.isnan(v) or math.isinf(v):
        return None, f"{campo} inválido."

    if minimo is not None and v < minimo:
        return None, f"{campo} não pode ser menor que {minimo}."

    return v, None


def parse_int(valor, campo="Valor", minimo=None, obrigatorio=False):
    """Converte para int com segurança (usa parse_float por baixo)."""
    v, erro = parse_float(valor, campo, minimo, obrigatorio)
    if erro:
        return None, erro
    if v is None:
        return None, None
    return int(v), None
