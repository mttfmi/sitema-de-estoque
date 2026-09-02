import hashlib
import os
import re
from datetime import datetime

from core.database import get_connection

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

def _gerar_hash(senha, salt=None):
    if salt is None:
        salt = os.urandom(16).hex()
    hash_senha = hashlib.pbkdf2_hmac(
        'sha256',
        senha.encode('utf-8'),
        bytes.fromhex(salt),
        200_000  # iterações — custo computacional propositalmente alto
    ).hex()
    return hash_senha, salt


def validar_forca_senha(senha):
    # Validação simplificada para uso em protótipo — aceita qualquer senha não vazia.
    if not senha:
        return False, "A senha não pode ficar em branco."
    return True, ""


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

def criar_usuario(usuario, nome_completo, senha, nivel_acesso='operador'):
    usuario = usuario.strip().lower()

    if not usuario or not senha:
        return False, "Usuário e senha são obrigatórios."

    if usuario_existe(usuario):
        return False, "Este nome de usuário já está em uso."

    ok, msg = validar_forca_senha(senha)
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
        'SELECT id, usuario, nome_completo, nivel_acesso, ativo FROM usuarios WHERE id = %s',
        (usuario_id,)
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not row:
        return None

    p_id, uname, nome, nivel, ativo = row
    if not ativo:
        return None

    return {
        "id": p_id,
        "usuario": uname,
        "nome_completo": nome or uname,
        "nivel_acesso": nivel
    }


def verificar_login(usuario, senha):
    """
    Retorna (sucesso: bool, dados_usuario: dict|None, mensagem: str)
    """
    usuario = usuario.strip().lower()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, usuario, nome_completo, senha_hash, salt, nivel_acesso, ativo
        FROM usuarios WHERE usuario = %s
    ''', (usuario,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not row:
        return False, None, "Usuário não encontrado."

    p_id, uname, nome, senha_hash_salva, salt, nivel, ativo = row

    if not ativo:
        return False, None, "Este usuário está desativado. Contate um administrador."

    if not senha_hash_salva or not salt:
        return False, None, "Esta conta não possui senha configurada corretamente. Contate um administrador."

    hash_calculado, _ = _gerar_hash(senha, salt)

    if hash_calculado != senha_hash_salva:
        return False, None, "Senha incorreta."

    dados_usuario = {
        "id": p_id,
        "usuario": uname,
        "nome_completo": nome or uname,
        "nivel_acesso": nivel
    }
    return True, dados_usuario, "Login realizado com sucesso."


# ---------------------------------------------------------------------
# GESTÃO DE CONTA
# ---------------------------------------------------------------------

def alterar_senha(usuario_id, senha_atual, senha_nova):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT senha_hash, salt FROM usuarios WHERE id = %s', (usuario_id,))
    row = cursor.fetchone()

    if not row:
        cursor.close()
        conn.close()
        return False, "Usuário não encontrado."

    senha_hash_salva, salt = row
    hash_atual, _ = _gerar_hash(senha_atual, salt)

    if hash_atual != senha_hash_salva:
        cursor.close()
        conn.close()
        return False, "Senha atual incorreta."

    ok, msg = validar_forca_senha(senha_nova)
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
