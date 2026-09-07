from datetime import datetime

from core.database import get_connection, registrar_log
from core.ficha_tecnica import get_ficha_tecnica_por_produto
from core.lotes import gerar_codigo_lote
from core.notifier import notificar_op_risco, notificar_op_concluida

STATUS_VALIDOS = ["Planejada", "Em Andamento", "Concluída", "Cancelada"]


def init_pcp_db():
    """Cria a tabela de Ordens de Produção. Depende de produtos já existir
    — chamar depois de init_db()."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ordens_producao (
            id SERIAL PRIMARY KEY,
            produto_id INTEGER NOT NULL REFERENCES produtos(id),
            quantidade_planejada REAL NOT NULL,
            data_criacao TEXT NOT NULL,
            data_prevista TEXT,
            status TEXT NOT NULL DEFAULT 'Planejada',
            observacao TEXT,
            usuario_id INTEGER,
            usuario_nome TEXT,
            em_risco INTEGER NOT NULL DEFAULT 0,
            data_conclusao TEXT,
            lote_id INTEGER
        )
    ''')
    conn.commit()
    cursor.close()
    conn.close()


# ---------------------------------------------------------------------
# CÁLCULO DE NECESSIDADE DE INSUMOS
# ---------------------------------------------------------------------

def calcular_necessidades(produto_id, quantidade_planejada):
    """Consulta a ficha técnica do produto e calcula, para a quantidade
    informada, quanto de cada insumo é necessário x quanto está disponível.
    Retorna lista de dicts: insumo_id, nome, necessario, disponivel, unidade, situacao.
    Retorna lista vazia se o produto não tiver ficha técnica cadastrada."""
    _, itens = get_ficha_tecnica_por_produto(produto_id)
    if not itens:
        return []

    conn = get_connection()
    cursor = conn.cursor()
    resultado = []
    for insumo_id, nome, qtd_por_unidade, unidade in itens:
        necessario = qtd_por_unidade * quantidade_planejada
        cursor.execute('SELECT quantidade FROM produtos WHERE id = %s', (insumo_id,))
        row = cursor.fetchone()
        disponivel = (row[0] if row else 0) or 0
        resultado.append({
            "insumo_id": insumo_id,
            "nome": nome,
            "necessario": necessario,
            "disponivel": disponivel,
            "unidade": unidade,
            "situacao": "Disponível" if disponivel >= necessario else "Insuficiente",
        })
    cursor.close()
    conn.close()
    return resultado


def op_em_risco(necessidades):
    return any(item["situacao"] == "Insuficiente" for item in necessidades)


# ---------------------------------------------------------------------
# CRUD / FLUXO DA OP
# ---------------------------------------------------------------------

def listar_ops(status=None):
    conn = get_connection()
    cursor = conn.cursor()
    if status:
        cursor.execute('''
            SELECT o.id, o.produto_id, p.nome, o.quantidade_planejada, o.data_criacao,
                   o.data_prevista, o.status, o.observacao, o.usuario_nome, o.em_risco,
                   o.data_conclusao, o.lote_id
            FROM ordens_producao o JOIN produtos p ON p.id = o.produto_id
            WHERE o.status = %s ORDER BY o.id DESC
        ''', (status,))
    else:
        cursor.execute('''
            SELECT o.id, o.produto_id, p.nome, o.quantidade_planejada, o.data_criacao,
                   o.data_prevista, o.status, o.observacao, o.usuario_nome, o.em_risco,
                   o.data_conclusao, o.lote_id
            FROM ordens_producao o JOIN produtos p ON p.id = o.produto_id
            ORDER BY o.id DESC
        ''')
    ops = cursor.fetchall()
    cursor.close()
    conn.close()
    return ops


def get_op(op_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT o.id, o.produto_id, p.nome, o.quantidade_planejada, o.data_criacao,
               o.data_prevista, o.status, o.observacao, o.usuario_nome, o.em_risco,
               o.data_conclusao, o.lote_id
        FROM ordens_producao o JOIN produtos p ON p.id = o.produto_id
        WHERE o.id = %s
    ''', (op_id,))
    op = cursor.fetchone()
    cursor.close()
    conn.close()
    return op


def criar_op(produto_id, quantidade_planejada, data_prevista, observacao, usuario_id, usuario_nome):
    """Cria a OP. Nunca bloqueia por falta de insumo — apenas marca em_risco
    e dispara UMA notificação consolidada no Telegram, se aplicável."""
    necessidades = calcular_necessidades(produto_id, quantidade_planejada)
    em_risco = op_em_risco(necessidades)

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO ordens_producao
            (produto_id, quantidade_planejada, data_criacao, data_prevista,
             status, observacao, usuario_id, usuario_nome, em_risco)
        VALUES (%s, %s, %s, %s, 'Planejada', %s, %s, %s, %s)
        RETURNING id
    ''', (
        produto_id, quantidade_planejada,
        datetime.now().strftime("%d/%m/%Y %H:%M:%S"), data_prevista,
        observacao, usuario_id, usuario_nome, 1 if em_risco else 0
    ))
    op_id = cursor.fetchone()[0]
    conn.commit()
    cursor.close()
    conn.close()

    registrar_log(f"OP #{op_id}", "🏭 OP CRIADA", quantidade_planejada)

    if em_risco:
        insuficientes = [i for i in necessidades if i["situacao"] == "Insuficiente"]
        conn2 = get_connection()
        c2 = conn2.cursor()
        c2.execute('SELECT nome FROM produtos WHERE id = %s', (produto_id,))
        produto_nome = c2.fetchone()[0]
        c2.close()
        conn2.close()
        try:
            notificar_op_risco(op_id, produto_nome, quantidade_planejada, insuficientes)
        except Exception as e:
            print(f"Aviso: falha ao notificar OP em risco no Telegram ({e})")

    return op_id, em_risco


def iniciar_op(op_id):
    """Move a OP de 'Planejada' para 'Em Andamento'. Reavalia o risco de
    insumos, mas não bloqueia a transição."""
    op = get_op(op_id)
    if not op:
        return False, "Ordem de Produção não encontrada."
    if op[6] != "Planejada":
        return False, "Só é possível iniciar uma OP que esteja Planejada."

    necessidades = calcular_necessidades(op[1], op[3])
    em_risco = op_em_risco(necessidades)

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE ordens_producao SET status = 'Em Andamento', em_risco = %s WHERE id = %s",
        (1 if em_risco else 0, op_id)
    )
    conn.commit()
    cursor.close()
    conn.close()
    return True, "OP em andamento."


def cancelar_op(op_id):
    op = get_op(op_id)
    if not op:
        return False, "Ordem de Produção não encontrada."
    if op[6] == "Concluída":
        return False, "Uma OP concluída não pode ser cancelada."

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE ordens_producao SET status = 'Cancelada' WHERE id = %s", (op_id,))
    conn.commit()
    cursor.close()
    conn.close()
    return True, "OP cancelada."


def concluir_op(op_id):
    """Conclui a OP em uma única transação:
    1. Baixa os insumos conforme a ficha técnica (nunca deixa estoque negativo).
    2. Acrescenta ao estoque a quantidade produzida do produto final.
    3. Gera um lote para o produto.
    4. Marca a OP como Concluída, com data/hora.
    Só envia a notificação do Telegram depois que tudo foi persistido com sucesso.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT status, produto_id, quantidade_planejada FROM ordens_producao WHERE id = %s FOR UPDATE",
            (op_id,)
        )
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            cursor.close()
            conn.close()
            return False, "Ordem de Produção não encontrada.", None

        status, produto_id, quantidade = row
        if status == "Concluída":
            conn.rollback()
            cursor.close()
            conn.close()
            return False, "Esta OP já foi concluída anteriormente.", None
        if status == "Cancelada":
            conn.rollback()
            cursor.close()
            conn.close()
            return False, "Uma OP cancelada não pode ser concluída.", None

        # Busca a ficha técnica do produto dentro da mesma transação
        cursor.execute('SELECT id FROM fichas_tecnicas WHERE produto_id = %s', (produto_id,))
        ficha = cursor.fetchone()
        itens_baixa = []
        if ficha:
            cursor.execute('''
                SELECT insumo_id, quantidade_necessaria FROM ficha_tecnica_itens
                WHERE ficha_tecnica_id = %s
            ''', (ficha[0],))
            for insumo_id, qtd_unidade in cursor.fetchall():
                necessario = qtd_unidade * quantidade
                cursor.execute(
                    'UPDATE produtos SET quantidade = GREATEST(0, quantidade - %s) WHERE id = %s',
                    (necessario, insumo_id)
                )
                itens_baixa.append((insumo_id, necessario))

        # Acrescenta ao estoque a quantidade produzida
        cursor.execute(
            'UPDATE produtos SET quantidade = quantidade + %s WHERE id = %s',
            (quantidade, produto_id)
        )

        # Gera o lote
        codigo_lote = gerar_codigo_lote(cursor)
        data_fabricacao = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        cursor.execute('''
            INSERT INTO lotes (codigo, produto_id, quantidade, data_fabricacao,
                                ordem_producao_id, status)
            VALUES (%s, %s, %s, %s, %s, 'Ativo')
            RETURNING id
        ''', (codigo_lote, produto_id, quantidade, data_fabricacao, op_id))
        lote_id = cursor.fetchone()[0]

        data_conclusao = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        cursor.execute('''
            UPDATE ordens_producao
            SET status = 'Concluída', data_conclusao = %s, lote_id = %s, em_risco = 0
            WHERE id = %s
        ''', (data_conclusao, lote_id, op_id))

        cursor.execute('SELECT nome FROM produtos WHERE id = %s', (produto_id,))
        produto_nome = cursor.fetchone()[0]

        conn.commit()
    except Exception as e:
        conn.rollback()
        cursor.close()
        conn.close()
        return False, f"Erro ao concluir a OP: {e}", None

    cursor.close()
    conn.close()

    # Histórico (fora da transação principal, mesmo padrão já usado em
    # registrar_venda — o commit crítico já aconteceu com sucesso acima)
    for insumo_id, necessario in itens_baixa:
        registrar_log(f"Insumo #{insumo_id}", f"🏭 BAIXA OP #{op_id}", necessario)
    registrar_log(produto_nome, f"🏭 PRODUÇÃO OP #{op_id}", quantidade)

    try:
        notificar_op_concluida(op_id, produto_nome, quantidade, codigo_lote)
    except Exception as e:
        print(f"Aviso: falha ao notificar OP concluída no Telegram ({e})")

    return True, "Ordem de Produção concluída com sucesso.", lote_id


# ---------------------------------------------------------------------
# INDICADORES PARA O DASHBOARD
# ---------------------------------------------------------------------

def contar_ops_em_andamento():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM ordens_producao WHERE status = 'Em Andamento'")
    total = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return total


def listar_insumos_em_risco():
    """Percorre as OPs abertas (Planejada/Em Andamento) e retorna a lista
    de insumos distintos com estoque insuficiente para atendê-las, já
    somando a necessidade entre as OPs que usam o mesmo insumo."""
    ops_abertas = [op for op in listar_ops() if op[6] in ("Planejada", "Em Andamento")]

    necessidade_total = {}  # insumo_id -> {nome, necessario, unidade}
    for op in ops_abertas:
        op_id, produto_id, _, quantidade = op[0], op[1], op[2], op[3]
        for item in calcular_necessidades(produto_id, quantidade):
            chave = item["insumo_id"]
            if chave not in necessidade_total:
                necessidade_total[chave] = {
                    "insumo_id": chave, "nome": item["nome"],
                    "necessario": 0.0, "unidade": item["unidade"],
                    "disponivel": item["disponivel"], "ops": [],
                }
            necessidade_total[chave]["necessario"] += item["necessario"]
            if item["situacao"] == "Insuficiente":
                necessidade_total[chave]["ops"].append(op_id)

    em_risco = [
        v for v in necessidade_total.values()
        if v["disponivel"] < v["necessario"]
    ]
    return em_risco


def contar_insumos_em_risco():
    return len(listar_insumos_em_risco())
