from datetime import datetime

from core.database import get_connection


def init_ficha_tecnica_db():
    """Cria as tabelas de Ficha Técnica caso não existam.
    Precisa ser chamada DEPOIS de init_db() (depende da tabela produtos)."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fichas_tecnicas (
            id SERIAL PRIMARY KEY,
            produto_id INTEGER NOT NULL UNIQUE REFERENCES produtos(id) ON DELETE CASCADE,
            criado_em TEXT NOT NULL
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ficha_tecnica_itens (
            id SERIAL PRIMARY KEY,
            ficha_tecnica_id INTEGER NOT NULL REFERENCES fichas_tecnicas(id) ON DELETE CASCADE,
            insumo_id INTEGER NOT NULL REFERENCES produtos(id),
            quantidade_necessaria REAL NOT NULL,
            unidade TEXT NOT NULL DEFAULT 'un'
        )
    ''')

    conn.commit()
    cursor.close()
    conn.close()


# ---------------------------------------------------------------------
# CONSULTAS
# ---------------------------------------------------------------------

def listar_fichas_tecnicas():
    """Retorna todas as fichas técnicas com o nome do produto final e a
    quantidade de insumos cadastrados em cada uma."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT ft.id, ft.produto_id, p.nome, ft.criado_em,
               (SELECT COUNT(*) FROM ficha_tecnica_itens WHERE ficha_tecnica_id = ft.id)
        FROM fichas_tecnicas ft
        JOIN produtos p ON p.id = ft.produto_id
        ORDER BY p.nome
    ''')
    fichas = cursor.fetchall()
    cursor.close()
    conn.close()
    return fichas


def get_ficha_tecnica(ficha_id):
    """Retorna (cabecalho, itens) de uma ficha técnica pelo seu ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT ft.id, ft.produto_id, p.nome, ft.criado_em
        FROM fichas_tecnicas ft JOIN produtos p ON p.id = ft.produto_id
        WHERE ft.id = %s
    ''', (ficha_id,))
    cabecalho = cursor.fetchone()

    itens = []
    if cabecalho:
        cursor.execute('''
            SELECT fti.id, fti.insumo_id, p.nome, fti.quantidade_necessaria, fti.unidade
            FROM ficha_tecnica_itens fti
            JOIN produtos p ON p.id = fti.insumo_id
            WHERE fti.ficha_tecnica_id = %s
            ORDER BY p.nome
        ''', (ficha_id,))
        itens = cursor.fetchall()

    cursor.close()
    conn.close()
    return cabecalho, itens


def get_ficha_tecnica_por_produto(produto_id):
    """Retorna (ficha_id, itens) para um produto, ou (None, []) se não existir.
    itens: lista de tuplas (insumo_id, nome_insumo, quantidade_necessaria, unidade)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM fichas_tecnicas WHERE produto_id = %s', (produto_id,))
    row = cursor.fetchone()

    if not row:
        cursor.close()
        conn.close()
        return None, []

    ficha_id = row[0]
    cursor.execute('''
        SELECT fti.insumo_id, p.nome, fti.quantidade_necessaria, fti.unidade
        FROM ficha_tecnica_itens fti
        JOIN produtos p ON p.id = fti.insumo_id
        WHERE fti.ficha_tecnica_id = %s
    ''', (ficha_id,))
    itens = cursor.fetchall()

    cursor.close()
    conn.close()
    return ficha_id, itens


def produto_tem_ficha_tecnica(produto_id, ignorar_ficha_id=None):
    conn = get_connection()
    cursor = conn.cursor()
    if ignorar_ficha_id:
        cursor.execute(
            'SELECT id FROM fichas_tecnicas WHERE produto_id = %s AND id != %s',
            (produto_id, ignorar_ficha_id)
        )
    else:
        cursor.execute('SELECT id FROM fichas_tecnicas WHERE produto_id = %s', (produto_id,))
    existe = cursor.fetchone() is not None
    cursor.close()
    conn.close()
    return existe


# ---------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------

def criar_ficha_tecnica(produto_id, itens):
    """itens: lista de (insumo_id, quantidade_necessaria, unidade).
    Retorna (ok, mensagem)."""
    if not itens:
        return False, "Adicione ao menos um insumo à ficha técnica."

    if produto_tem_ficha_tecnica(produto_id):
        return False, "Este produto já possui uma ficha técnica cadastrada."

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT INTO fichas_tecnicas (produto_id, criado_em) VALUES (%s, %s) RETURNING id',
            (produto_id, datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
        )
        ficha_id = cursor.fetchone()[0]

        for insumo_id, quantidade, unidade in itens:
            cursor.execute('''
                INSERT INTO ficha_tecnica_itens (ficha_tecnica_id, insumo_id, quantidade_necessaria, unidade)
                VALUES (%s, %s, %s, %s)
            ''', (ficha_id, insumo_id, quantidade, unidade))

        conn.commit()
    except Exception as e:
        conn.rollback()
        cursor.close()
        conn.close()
        return False, f"Erro ao salvar ficha técnica: {e}"

    cursor.close()
    conn.close()
    return True, "Ficha técnica cadastrada com sucesso."


def atualizar_ficha_tecnica(ficha_id, itens):
    """Substitui os itens de uma ficha técnica existente."""
    if not itens:
        return False, "Adicione ao menos um insumo à ficha técnica."

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute('DELETE FROM ficha_tecnica_itens WHERE ficha_tecnica_id = %s', (ficha_id,))
        for insumo_id, quantidade, unidade in itens:
            cursor.execute('''
                INSERT INTO ficha_tecnica_itens (ficha_tecnica_id, insumo_id, quantidade_necessaria, unidade)
                VALUES (%s, %s, %s, %s)
            ''', (ficha_id, insumo_id, quantidade, unidade))
        conn.commit()
    except Exception as e:
        conn.rollback()
        cursor.close()
        conn.close()
        return False, f"Erro ao atualizar ficha técnica: {e}"

    cursor.close()
    conn.close()
    return True, "Ficha técnica atualizada com sucesso."


def deletar_ficha_tecnica(ficha_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM fichas_tecnicas WHERE id = %s', (ficha_id,))
    conn.commit()
    cursor.close()
    conn.close()
