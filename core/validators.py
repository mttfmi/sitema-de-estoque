"""Validação segura de valores vindos de request.form.

Evita que um valor inválido (texto, vazio, NaN, infinito, negativo onde não
faz sentido, gigante demais) derrube a aplicação com ValueError/500 — em vez
disso, retorna uma mensagem amigável para ser exibida via flash().
"""
import math

# Teto padrão para números — evita estouro de INTEGER/REAL no Postgres (que
# viraria erro 500) e valores absurdos vindos de requisições forjadas.
MAXIMO_PADRAO = 1_000_000_000


def parse_float(valor, campo="Valor", minimo=None, obrigatorio=False, maximo=MAXIMO_PADRAO):
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

    if maximo is not None and v > maximo:
        return None, f"{campo} não pode ser maior que {maximo:,.0f}.".replace(",", ".")

    return v, None


def parse_int(valor, campo="Valor", minimo=None, obrigatorio=False, maximo=MAXIMO_PADRAO):
    """Converte para int com segurança (usa parse_float por baixo)."""
    v, erro = parse_float(valor, campo, minimo, obrigatorio, maximo)
    if erro:
        return None, erro
    if v is None:
        return None, None
    return int(v), None


def validar_texto(valor, campo, max_len, obrigatorio=False):
    """Limpa e limita o tamanho de um campo de texto.
    Retorna (texto, mensagem_erro). Sem isso, um único campo poderia guardar
    megabytes no banco (o limite global da requisição é de 2 MB)."""
    texto = (valor or "").strip()
    if obrigatorio and not texto:
        return "", f"{campo} é obrigatório."
    if len(texto) > max_len:
        return texto, f"{campo} pode ter no máximo {max_len} caracteres."
    return texto, None
