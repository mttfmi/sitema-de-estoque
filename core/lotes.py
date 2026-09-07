from datetime import datetime

from core.database import get_connection


def init_lotes_db():
    """Cria a tabela de lotes. Depende de produtos e ordens_producao já
    existirem — chamar depois de init_db() e init_pcp_db()."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS lotes (
            id SERIAL PRIMARY KEY,
            codigo TEXT NOT NULL UNIQUE,
            produto_id INTEGER NOT NULL REFERENCES produtos(id),
            quantidade REAL NOT NULL,
            data_fabricacao TEXT NOT NULL,
            data_validade TEXT,
            ordem_producao_id INTEGER REFERENCES ordens_producao(id),
            status TEXT NOT NULL DEFAULT 'Ativo'
        )
    ''')
    conn.commit()
    cursor.close()
    conn.close()


def gerar_codigo_lote(cursor):
    """Gera um código sequencial no formato LOTE-AAAAMMDD-NNN, único por dia.
    Recebe o cursor de uma transação já aberta para contar os lotes do dia
    de forma consistente."""
    hoje = datetime.now().strftime("%Y%m%d")
    cursor.execute("SELECT COUNT(*) FROM lotes WHERE codigo LIKE %s", (f"LOTE-{hoje}-%",))
    total_hoje = cursor.fetchone()[0]
    return f"LOTE-{hoje}-{total_hoje + 1:03d}"


def listar_lotes(codigo=None, produto=None, op_id=None, data=None):
    """Lista lotes com filtros opcionais (todos combináveis)."""
    conn = get_connection()
    cursor = conn.cursor()

    condicoes = []
    params = []

    if codigo:
        condicoes.append("l.codigo ILIKE %s")
        params.append(f"%{codigo}%")
    if produto:
        condicoes.append("p.nome ILIKE %s")
        params.append(f"%{produto}%")
    if op_id:
        condicoes.append("l.ordem_producao_id = %s")
        params.append(op_id)
    if data:
        condicoes.append("l.data_fabricacao LIKE %s")
        params.append(f"{data}%")

    where = f"WHERE {' AND '.join(condicoes)}" if condicoes else ""

    cursor.execute(f'''
        SELECT l.id, l.codigo, l.produto_id, p.nome, l.quantidade,
               l.data_fabricacao, l.data_validade, l.ordem_producao_id, l.status
        FROM lotes l
        JOIN produtos p ON p.id = l.produto_id
        {where}
        ORDER BY l.id DESC
    ''', params)
    lotes = cursor.fetchall()
    cursor.close()
    conn.close()
    return lotes


def get_lote_por_op(op_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT l.id, l.codigo, l.produto_id, p.nome, l.quantidade,
               l.data_fabricacao, l.data_validade, l.ordem_producao_id, l.status
        FROM lotes l JOIN produtos p ON p.id = l.produto_id
        WHERE l.ordem_producao_id = %s
    ''', (op_id,))
    lote = cursor.fetchone()
    cursor.close()
    conn.close()
    return lote
