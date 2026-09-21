import hashlib
import hmac
import logging
import os
import re
import threading
import time
from collections import defaultdict
from datetime import datetime

from core.database import get_connection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# INICIALIZAÇÃO DA TABELA DE USUÁRIOS
# ---------------------------------------------------------------------

def init_auth_db():
    """Cria a tabela de usuários caso não exista, e migra/recria a tabela
    caso ela já exista com uma estrutura antiga e incompatível (ex: sobra
    de algum teste anterior, com colunas obrigatórias diferentes das nossas)."""
    conn = get_connection()
    cursor = conn.cursor()

    colunas_esperadas = {
        "id", "usuario", "nome_completo", "senha_hash",
        "salt", "nivel_acesso", "ativo", "data_criacao"
    }

    cursor.execute('''
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'usuarios'
        )
    ''')
    tabela_existe = cursor.fetchone()[0]

    if tabela_existe:
        cursor.execute('''
            SELECT column_name, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'usuarios'
        ''')
        info = cursor.fetchall()  # (column_name, is_nullable, column_default)

        # Se existir alguma coluna que a gente não conhece e que é obrigatória
        # (NOT NULL, sem valor padrão), a tabela é incompatível com o sistema
        # de login atual — provavelmente é sobra de outra versão/teste.
        colunas_incompativeis = [
            col[0] for col in info
            if col[0] not in colunas_esperadas and col[1] == 'NO' and col[2] is None
        ]

        if colunas_incompativeis:
            cursor.execute("DROP TABLE usuarios")
            conn.commit()
            tabela_existe = False

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id SERIAL PRIMARY KEY,
            usuario TEXT NOT NULL UNIQUE,
            nome_completo TEXT,
            senha_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            nivel_acesso TEXT NOT NULL DEFAULT 'operador',
            ativo INTEGER NOT NULL DEFAULT 1,
            data_criacao TEXT NOT NULL
        )
    ''')
    conn.commit()

    # Migração preventiva: garante que todas as colunas existem
    # (cobre o caso de a tabela já existir com uma estrutura incompleta,
    # mas ainda compatível — sem colunas extras obrigatórias)
    cursor.execute('''
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'usuarios'
    ''')
    colunas = [col[0] for col in cursor.fetchall()]

    migracoes = {
        "nome_completo": "ALTER TABLE usuarios ADD COLUMN nome_completo TEXT",
        "senha_hash": "ALTER TABLE usuarios ADD COLUMN senha_hash TEXT",
        "salt": "ALTER TABLE usuarios ADD COLUMN salt TEXT",
        "nivel_acesso": "ALTER TABLE usuarios ADD COLUMN nivel_acesso TEXT NOT NULL DEFAULT 'operador'",
        "ativo": "ALTER TABLE usuarios ADD COLUMN ativo INTEGER NOT NULL DEFAULT 1",
        "data_criacao": "ALTER TABLE usuarios ADD COLUMN data_criacao TEXT",
    }

    for coluna, comando_sql in migracoes.items():
        if coluna not in colunas:
            cursor.execute(comando_sql)

    conn.commit()
    cursor.close()
    conn.close()


# ---------------------------------------------------------------------
# HASH DE SENHA (PBKDF2-HMAC-SHA256 + salt aleatório por usuário)
# ---------------------------------------------------------------------

# Contas antigas guardam só o hash em hex, calculado com 200 mil iterações.
# As novas guardam "pbkdf2_sha256$<iterações>$<hex>" com 600 mil (mínimo
# atual recomendado pela OWASP). Quem entra com um hash antigo tem o hash
# regravado no formato novo automaticamente, sem precisar trocar a senha.
ITERACOES_ATUAIS = 600_000
ITERACOES_LEGADO = 200_000
_PREFIXO_HASH = "pbkdf2_sha256"


def _pbkdf2(senha, salt_hex, iteracoes):
    return hashlib.pbkdf2_hmac(
        'sha256', senha.encode('utf-8'), bytes.fromhex(salt_hex), iteracoes
    ).hex()


def _gerar_hash(senha, salt=None):
    """Retorna (hash_para_guardar, salt)."""
    if salt is None:
        salt = os.urandom(16).hex()
    return f"{_PREFIXO_HASH}${ITERACOES_ATUAIS}${_pbkdf2(senha, salt, ITERACOES_ATUAIS)}", salt


def _conferir_senha(senha, salt, hash_salvo):
    """Retorna (senha_confere, precisa_atualizar_o_hash). Comparação em
    tempo constante (hmac.compare_digest)."""
    if hash_salvo.startswith(_PREFIXO_HASH + "$"):
        try:
            _, iteracoes, hash_hex = hash_salvo.split("$")
            calculado = _pbkdf2(senha, salt, int(iteracoes))
        except ValueError:
            return False, False
        ok = hmac.compare_digest(calculado, hash_hex)
        return ok, ok and int(iteracoes) < ITERACOES_ATUAIS
    ok = hmac.compare_digest(_pbkdf2(senha, salt, ITERACOES_LEGADO), hash_salvo)
    return ok, ok


def assinatura_sessao(senha_hash, salt):
    """Impressão digital (irreversível) da senha atual, guardada na sessão.
    Quando a senha muda, ela muda — e todas as sessões abertas antes da troca
    (ex: de alguém que roubou o cookie) deixam de valer."""
    return hashlib.sha256(f"{senha_hash}:{salt}".encode("utf-8")).hexdigest()[:24]


_SENHAS_COMUNS = {
    "12345678", "123456789", "1234567890", "password", "password1", "senha123", "senha1234",
    "qwerty123", "qwertyuiop", "abcd1234", "admin123", "admin1234", "11111111", "00000000",
    "iloveyou", "mudar123", "trocar123", "brasil123", "senhasenha",
}


def validar_forca_senha(senha, usuario=None):
    if not senha:
        return False, "A senha não pode ficar em branco."
    if len(senha) < 8:
        return False, "A senha precisa ter pelo menos 8 caracteres."
    if senha.lower() in _SENHAS_COMUNS or len(set(senha)) < 3:
        return False, "Essa senha é muito comum. Escolha outra, mais difícil de adivinhar."
    if usuario and senha.lower() == usuario.lower():
        return False, "A senha não pode ser igual ao nome de usuário."
    return True, ""


def verificar_chave_setup(chave_informada, ip):
    """Chave de instalação (variável SETUP_KEY) exigida em produção para criar
    o PRIMEIRO administrador — sem ela, se o banco ficar vazio por qualquer
    motivo, o primeiro visitante da página de login viraria administrador."""
    esperada = os.environ.get("SETUP_KEY", "")
    if not esperada:
        return False, "SETUP_KEY não configurada no servidor."
    if _login_temporariamente_bloqueado("__setup__", ip):
        return False, MSG_LOGIN_BLOQUEADO
    if not hmac.compare_digest((chave_informada or "").encode("utf-8"), esperada.encode("utf-8")):
        _registrar_tentativa_falha("__setup__", ip)
        return False, "Chave de instalação incorreta."
    return True, ""


# ---------------------------------------------------------------------
# PROTEÇÃO CONTRA FORÇA BRUTA NO LOGIN
# ---------------------------------------------------------------------
# Guarda em memória (por processo) os horários das tentativas de login
# malsucedidas. Não sobrevive a um reinício do processo nem é compartilhada
# entre múltiplas instâncias — limitação aceitável no plano gratuito do
# Render, que roda uma única instância.
#
# Três contadores independentes (um bloqueio em qualquer um já barra o login):
#  - (IP, usuário): 5 erros em 5 min — barra quem está adivinhando a senha de
#    uma conta, SEM permitir que um atacante qualquer tranque o dono da conta
#    do lado de fora (o dono, de outro IP, continua entrando normalmente);
#  - IP: 20 erros em 15 min — barra quem testa muitos usuários de uma vez;
#  - usuário: 50 erros em 15 min — teto para ataque distribuído em vários IPs.
_TENTATIVAS_FALHAS = defaultdict(list)
_LIMITES = {
    "usuario_ip": (5, 5 * 60),
    "ip": (20, 15 * 60),
    "usuario": (50, 15 * 60),
}
_JANELA_MAXIMA = 15 * 60
_MAX_CHAVES = 5000  # teto de memória: chaves vêm de dados do atacante
_lock_tentativas = threading.Lock()

# Salt fixo (aleatório por processo) usado só para gastar o mesmo tempo de
# CPU quando o usuário não existe — sem isso, o login de um usuário
# inexistente responde bem mais rápido e revela quais contas existem.
_SALT_FALSO = os.urandom(16).hex()

MSG_LOGIN_INVALIDO = "Usuário ou senha incorretos."
MSG_LOGIN_BLOQUEADO = "Muitas tentativas de login. Aguarde alguns minutos e tente novamente."


def _chaves(usuario, ip):
    return {
        "usuario_ip": ("usuario_ip", ip, usuario),
        "ip": ("ip", ip),
        "usuario": ("usuario", usuario),
    }


def _contar_recentes(chave, janela, agora):
    recentes = [t for t in _TENTATIVAS_FALHAS.get(chave, ()) if agora - t < janela]
    if recentes:
        _TENTATIVAS_FALHAS[chave] = recentes
    else:
        _TENTATIVAS_FALHAS.pop(chave, None)
    return len(recentes)


def _podar_tentativas(agora):
    for chave in list(_TENTATIVAS_FALHAS):
        if not _TENTATIVAS_FALHAS[chave] or agora - _TENTATIVAS_FALHAS[chave][-1] > _JANELA_MAXIMA:
            _TENTATIVAS_FALHAS.pop(chave, None)
    if len(_TENTATIVAS_FALHAS) > _MAX_CHAVES:
        _TENTATIVAS_FALHAS.clear()


def _login_temporariamente_bloqueado(usuario, ip):
    agora = time.time()
    with _lock_tentativas:
        for nome, chave in _chaves(usuario, ip).items():
            maximo, janela = _LIMITES[nome]
            if _contar_recentes(chave, janela, agora) >= maximo:
                return True
    return False


def _registrar_tentativa_falha(usuario, ip):
    agora = time.time()
    with _lock_tentativas:
        if len(_TENTATIVAS_FALHAS) > _MAX_CHAVES:
            _podar_tentativas(agora)
        for chave in _chaves(usuario, ip).values():
            _TENTATIVAS_FALHAS[chave].append(agora)


def _limpar_tentativas_falhas(usuario, ip):
    chaves = _chaves(usuario, ip)
    with _lock_tentativas:
        _TENTATIVAS_FALHAS.pop(chaves["usuario_ip"], None)
        _TENTATIVAS_FALHAS.pop(chaves["usuario"], None)


# ---------------------------------------------------------------------
# CONSULTAS
# ---------------------------------------------------------------------

def existe_algum_usuario():
    """
    Considera apenas usuários com hash de senha válido (criados pelo próprio
    sistema de login). Ignora linhas 'legadas' que porventura já existissem
    numa tabela 'usuarios' antiga com outra estrutura.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM usuarios WHERE senha_hash IS NOT NULL AND salt IS NOT NULL "
        "AND senha_hash != '' AND salt != ''"
    )
    total = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return total > 0


def usuario_existe(usuario):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM usuarios WHERE usuario = %s', (usuario.strip().lower(),))
    res = cursor.fetchone()
    cursor.close()
    conn.close()
    return res is not None


def listar_usuarios():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, usuario, nome_completo, nivel_acesso, ativo, data_criacao FROM usuarios ORDER BY id')
    usuarios = cursor.fetchall()
    cursor.close()
    conn.close()
    return usuarios


# ---------------------------------------------------------------------
# CRIAÇÃO DE USUÁRIO
# ---------------------------------------------------------------------

NIVEIS_VALIDOS = ("operador", "administrador")
_REGEX_LOGIN = re.compile(r"^[a-z0-9._@-]{3,64}$")
SENHA_TAMANHO_MAXIMO = 256


def criar_usuario(usuario, nome_completo, senha, nivel_acesso='operador'):
    usuario = usuario.strip().lower()
    nome_completo = (nome_completo or "").strip()

    if not usuario or not senha:
        return False, "Usuário e senha são obrigatórios."

    if not _REGEX_LOGIN.match(usuario):
        return False, "O usuário deve ter de 3 a 64 caracteres: letras minúsculas, números, ponto, hífen, underline ou @."

    if len(nome_completo) > 120:
        return False, "O nome pode ter no máximo 120 caracteres."

    if len(senha) > SENHA_TAMANHO_MAXIMO:
        return False, f"A senha pode ter no máximo {SENHA_TAMANHO_MAXIMO} caracteres."

    if nivel_acesso not in NIVEIS_VALIDOS:
        return False, "Nível de acesso inválido."

    if usuario_existe(usuario):
        return False, "Este nome de usuário já está em uso."

    ok, msg = validar_forca_senha(senha, usuario)
    if not ok:
        return False, msg

    senha_hash, salt = _gerar_hash(senha)

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO usuarios (usuario, nome_completo, senha_hash, salt, nivel_acesso, ativo, data_criacao)
        VALUES (%s, %s, %s, %s, %s, 1, %s)
    ''', (usuario, nome_completo.strip(), senha_hash, salt, nivel_acesso,
          datetime.now().strftime("%d/%m/%Y %H:%M:%S")))
    conn.commit()
    cursor.close()
    conn.close()

    return True, "Usuário criado com sucesso."


# ---------------------------------------------------------------------
# LOGIN
# ---------------------------------------------------------------------

def get_usuario_por_id(usuario_id):
    """Busca um usuário pelo ID — usado pelo Flask-Login para recarregar a
    sessão a cada requisição na versão web."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT id, usuario, nome_completo, nivel_acesso, ativo, senha_hash, salt '
        'FROM usuarios WHERE id = %s',
        (usuario_id,)
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not row:
        return None

    p_id, uname, nome, nivel, ativo, senha_hash, salt = row
    if not ativo:
        return None

    return {
        "id": p_id,
        "usuario": uname,
        "nome_completo": nome or uname,
        "nivel_acesso": nivel,
        "assinatura": assinatura_sessao(senha_hash, salt),
    }


def verificar_login(usuario, senha, ip="desconhecido"):
    """
    Retorna (sucesso: bool, dados_usuario: dict|None, mensagem: str)

    A mensagem de erro é SEMPRE a mesma (usuário inexistente, senha errada,
    conta sem senha) e o tempo de resposta também — assim quem tenta entrar
    não consegue descobrir quais usuários existem no sistema. Só depois de
    acertar a senha é que se informa que a conta está desativada.
    """
    usuario = (usuario or "").strip().lower()[:64]
    senha = senha or ""

    if _login_temporariamente_bloqueado(usuario, ip):
        return False, None, MSG_LOGIN_BLOQUEADO

    if len(senha) > SENHA_TAMANHO_MAXIMO:
        _registrar_tentativa_falha(usuario, ip)
        return False, None, MSG_LOGIN_INVALIDO

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, usuario, nome_completo, senha_hash, salt, nivel_acesso, ativo
        FROM usuarios WHERE usuario = %s
    ''', (usuario,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not row or not row[3] or not row[4]:
        _gerar_hash(senha, _SALT_FALSO)  # gasta o mesmo tempo de CPU de um login real
        _registrar_tentativa_falha(usuario, ip)
        return False, None, MSG_LOGIN_INVALIDO

    p_id, uname, nome, senha_hash_salva, salt, nivel, ativo = row

    confere, precisa_atualizar = _conferir_senha(senha, salt, senha_hash_salva)

    if not confere:
        _registrar_tentativa_falha(usuario, ip)
        return False, None, MSG_LOGIN_INVALIDO

    if not ativo:
        return False, None, "Este usuário está desativado. Contate um administrador."

    if precisa_atualizar:
        # Regrava o hash no formato/custo atual. Se falhar por qualquer motivo,
        # o login segue normalmente com o hash antigo (que continua válido).
        try:
            novo_hash, novo_salt = _gerar_hash(senha)
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute('UPDATE usuarios SET senha_hash = %s, salt = %s WHERE id = %s',
                           (novo_hash, novo_salt, p_id))
            conn.commit()
            cursor.close()
            conn.close()
            senha_hash_salva, salt = novo_hash, novo_salt
        except Exception:
            logger.exception("Falha ao atualizar o hash de senha do usuário %s", p_id)

    _limpar_tentativas_falhas(usuario, ip)
    dados_usuario = {
        "id": p_id,
        "usuario": uname,
        "nome_completo": nome or uname,
        "nivel_acesso": nivel,
        "assinatura": assinatura_sessao(senha_hash_salva, salt),
    }
    return True, dados_usuario, "Login realizado com sucesso."


# ---------------------------------------------------------------------
# GESTÃO DE CONTA
# ---------------------------------------------------------------------

def alterar_senha(usuario_id, senha_atual, senha_nova, ip="desconhecido"):
    """Troca a senha do próprio usuário. Usa o mesmo bloqueio por tentativas
    do login — sem isso, quem roubasse uma sessão aberta poderia adivinhar a
    senha atual sem limite por esta tela."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT usuario, senha_hash, salt FROM usuarios WHERE id = %s', (usuario_id,))
    row = cursor.fetchone()

    if not row:
        cursor.close()
        conn.close()
        return False, "Usuário não encontrado."

    usuario, senha_hash_salva, salt = row

    if _login_temporariamente_bloqueado(usuario, ip):
        cursor.close()
        conn.close()
        return False, MSG_LOGIN_BLOQUEADO

    if len(senha_atual or "") > SENHA_TAMANHO_MAXIMO:
        confere = False
    else:
        confere, _ = _conferir_senha(senha_atual or "", salt, senha_hash_salva)

    if not confere:
        _registrar_tentativa_falha(usuario, ip)
        cursor.close()
        conn.close()
        return False, "Senha atual incorreta."

    if len(senha_nova or "") > SENHA_TAMANHO_MAXIMO:
        cursor.close()
        conn.close()
        return False, f"A senha pode ter no máximo {SENHA_TAMANHO_MAXIMO} caracteres."

    ok, msg = validar_forca_senha(senha_nova, usuario)
    if not ok:
        cursor.close()
        conn.close()
        return False, msg

    novo_hash, novo_salt = _gerar_hash(senha_nova)
    cursor.execute('UPDATE usuarios SET senha_hash = %s, salt = %s WHERE id = %s',
                    (novo_hash, novo_salt, usuario_id))
    conn.commit()
    cursor.close()
    conn.close()
    _limpar_tentativas_falhas(usuario, ip)
    return True, "Senha alterada com sucesso."


def alternar_status_usuario(usuario_id, ativo):
    """Ativa ou desativa um usuário (bloqueio de acesso sem excluir o registro)"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('UPDATE usuarios SET ativo = %s WHERE id = %s', (1 if ativo else 0, usuario_id))
    conn.commit()
    cursor.close()
    conn.close()


def deletar_usuario(usuario_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM usuarios WHERE id = %s', (usuario_id,))
    conn.commit()
    cursor.close()
    conn.close()


# Garante que a tabela existe assim que o módulo é importado
init_auth_db()
