from datetime import datetime
import os
import shutil

import psycopg2

# Importação direta e garantida do Notificador do Telegram
try:
    from core.notifier import enviar_alerta_telegram
except ImportError:
    try:
        from notifier import enviar_alerta_telegram
    except ImportError:
        def enviar_alerta_telegram(msg):
            print("⚠️ [AVISO]: Módulo de notificação não encontrado.")

# String de conexão do Supabase (Postgres). Defina a variável de ambiente
# DATABASE_URL com a "Connection string" do seu projeto Supabase
# (Project Settings > Database > Connection string > Session pooler,
# recomendada para aplicações web de longa duração como esta).
#
# Formato esperado (exemplo):
# postgresql://postgres.xxxxxxxxxx:SUA_SENHA@aws-0-sa-east-1.pooler.supabase.com:5432/postgres
DATABASE_URL = os.environ.get("DATABASE_URL", "")


def get_connection():
    """Abre uma nova conexão com o banco Postgres (Supabase).
    SSL é obrigatório para conexões externas ao Supabase."""
    if not DATABASE_URL:
        raise RuntimeError(
            "Variável de ambiente DATABASE_URL não definida! "
            "Configure com a connection string do seu projeto Supabase "
            "antes de rodar o sistema."
        )
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def init_db():
    """Cria as tabelas e colunas necessárias no banco de dados"""
    conn = get_connection()
    cursor = conn.cursor()

    # 1. Tabela de Produtos
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS produtos (
            id SERIAL PRIMARY KEY,
            codigo TEXT,
            nome TEXT NOT NULL,
            descricao TEXT,
            preco REAL,
            quantidade INTEGER DEFAULT 0,
            estoque_minimo INTEGER DEFAULT 5
        )
    ''')

    # 2. Tabela de Histórico de Movimentações
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS historico (
            id SERIAL PRIMARY KEY,
            produto_nome TEXT NOT NULL,
            tipo TEXT NOT NULL,
            quantidade INTEGER,
            data_hora TEXT NOT NULL
        )
    ''')

    # 3. Tabela de Vendas (Cabeçalho da Venda)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vendas (
            id SERIAL PRIMARY KEY,
            data_hora TEXT NOT NULL,
            total_venda REAL NOT NULL,
            forma_pagamento TEXT NOT NULL
        )
    ''')

    # 4. Tabela de Itens da Venda
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS itens_venda (
            id SERIAL PRIMARY KEY,
            venda_id INTEGER NOT NULL,
            produto_nome TEXT NOT NULL,
            quantidade INTEGER NOT NULL,
            preco_unitario REAL NOT NULL,
            subtotal REAL NOT NULL,
            FOREIGN KEY (venda_id) REFERENCES vendas (id)
        )
    ''')

    conn.commit()

    # Migrações preventivas (garante compatibilidade com bancos criados
    # antes de alguma coluna existir — equivalente ao PRAGMA table_info do SQLite)
    cursor.execute('''
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'produtos'
    ''')
    colunas = [col[0] for col in cursor.fetchall()]

    if "quantidade" not in colunas:
        cursor.execute("ALTER TABLE produtos ADD COLUMN quantidade INTEGER DEFAULT 0")
    if "estoque_minimo" not in colunas:
        cursor.execute("ALTER TABLE produtos ADD COLUMN estoque_minimo INTEGER DEFAULT 5")

    conn.commit()
    cursor.close()
    conn.close()

# ---------------------------------------------------------------------
# LOGS E HISTÓRICO
# ---------------------------------------------------------------------

def registrar_log(produto_nome, tipo, quantidade):
    """Registra uma ação no histórico de movimentações"""
    conn = get_connection()
    cursor = conn.cursor()
    data_hora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    cursor.execute('''
        INSERT INTO historico (produto_nome, tipo, quantidade, data_hora)
        VALUES (%s, %s, %s, %s)
    ''', (produto_nome, tipo, quantidade, data_hora))
    conn.commit()
    cursor.close()
    conn.close()

def get_historico():
    """Retorna todo o histórico de alterações ordenado do mais recente para o mais antigo"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, data_hora, produto_nome, tipo, quantidade FROM historico ORDER BY id DESC')
    logs = cursor.fetchall()
    cursor.close()
    conn.close()
    return logs

# ---------------------------------------------------------------------
# GESTÃO DE PRODUTOS (CRUD)
# ---------------------------------------------------------------------

def cadastrar_produto(codigo, nome, descricao, preco, quantidade=0, estoque_minimo=5):
    """Cadastra um novo produto e registra no log"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO produtos (codigo, nome, descricao, preco, quantidade, estoque_minimo)
        VALUES (%s, %s, %s, %s, %s, %s)
    ''', (codigo, nome, descricao, preco, quantidade, estoque_minimo))
    conn.commit()
    cursor.close()
    conn.close()

    registrar_log(nome, "🆕 CADASTRADO", quantidade)

def get_produto_por_id(p_id):
    """Busca um único produto pelo ID — usado na tela de edição."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT id, codigo, nome, descricao, preco, quantidade, estoque_minimo '
        'FROM produtos WHERE id = %s',
        (p_id,)
    )
    produto = cursor.fetchone()
    cursor.close()
    conn.close()
    return produto


def get_todos_produtos():
    """Retorna a lista completa de produtos cadastrados"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, codigo, nome, descricao, preco, quantidade, estoque_minimo FROM produtos')
    produtos = cursor.fetchall()
    cursor.close()
    conn.close()
    return produtos

def get_produtos_baixo_estoque():
    """Retorna os produtos que estão com estoque igual ou abaixo do mínimo estabelecido"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, codigo, nome, descricao, preco, quantidade, estoque_minimo FROM produtos WHERE quantidade <= estoque_minimo')
    produtos = cursor.fetchall()
    cursor.close()
    conn.close()
    return produtos

def alterar_quantidade(p_id, quantidade_delta, motivo_log=None):
    """Altera a quantidade em estoque de um produto (soma ou subtrai)"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT nome FROM produtos WHERE id = %s', (p_id,))
    res = cursor.fetchone()
    nome = res[0] if res else "Produto Desconhecido"

    cursor.execute('''
        UPDATE produtos
        SET quantidade = GREATEST(0, quantidade + %s)
        WHERE id = %s
    ''', (quantidade_delta, p_id))
    conn.commit()
    cursor.close()
    conn.close()

    if not motivo_log:
        motivo_log = "🟢 ENTRADA" if quantidade_delta > 0 else "🔴 SAÍDA"

    registrar_log(nome, motivo_log, abs(quantidade_delta))

def atualizar_produto(p_id, codigo, nome, descricao, preco, quantidade, estoque_minimo):
    """Atualiza as informações de um produto existente"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE produtos
        SET codigo=%s, nome=%s, descricao=%s, preco=%s, quantidade=%s, estoque_minimo=%s
        WHERE id=%s
    ''', (codigo, nome, descricao, preco, quantidade, estoque_minimo, p_id))
    conn.commit()
    cursor.close()
    conn.close()

    registrar_log(nome, "✏️ EDIÇÃO", quantidade)

def deletar_produto(p_id):
    """Exclui um produto do banco de dados"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT nome FROM produtos WHERE id = %s', (p_id,))
    res = cursor.fetchone()
    nome = res[0] if res else "Produto Desconhecido"

    cursor.execute('DELETE FROM produtos WHERE id=%s', (p_id,))
    conn.commit()
    cursor.close()
    conn.close()

    registrar_log(nome, "🗑️ EXCLUSÃO", 0)

# ---------------------------------------------------------------------
# MÓDULO DE VENDAS E TELEGRAM
# ---------------------------------------------------------------------

def registrar_venda(itens, forma_pagamento):
    """
    Registra a venda no banco, baixa o estoque, registra logs
    e envia alertas no Telegram.
    'itens' deve ser uma lista de dicionários: [{'id', 'nome', 'qtd', 'preco', 'subtotal'}]
    """
    conn = get_connection()
    cursor = conn.cursor()

    total_venda = sum(item['subtotal'] for item in itens)
    data_hora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    # Registrar cabeçalho da venda (RETURNING id substitui o cursor.lastrowid do SQLite)
    cursor.execute('''
        INSERT INTO vendas (data_hora, total_venda, forma_pagamento)
        VALUES (%s, %s, %s)
        RETURNING id
    ''', (data_hora, total_venda, forma_pagamento))

    venda_id = cursor.fetchone()[0]

    # Registrar cada item vendido
    for item in itens:
        cursor.execute('''
            INSERT INTO itens_venda (venda_id, produto_nome, quantidade, preco_unitario, subtotal)
            VALUES (%s, %s, %s, %s, %s)
        ''', (venda_id, item['nome'], item['qtd'], item['preco'], item['subtotal']))

    conn.commit()
    cursor.close()
    conn.close()

    # Dar baixa automática no estoque de cada item
    for item in itens:
        alterar_quantidade(item['id'], -item['qtd'], motivo_log=f"🛒 VENDA #{venda_id}")

    # 📱 DISPARO DE NOTIFICAÇÃO EM TEMPO REAL NO TELEGRAM
    try:
        msg = f"🛒 NOVA VENDA REALIZADA!\n"
        msg += f"🧾 Venda #{venda_id} | Total: R$ {total_venda:.2f}\n"
        msg += f"💳 Forma: {forma_pagamento}\n\n"
        msg += "Itens:\n"
        for item in itens:
            msg += f"• {item['nome']} (x{item['qtd']})\n"

        enviar_alerta_telegram(msg)

        # Alerta se algum item ficou com estoque crítico
        produtos_criticos = get_produtos_baixo_estoque()
        if produtos_criticos:
            alert_msg = "⚠️ ALERTA DE ESTOQUE CRÍTICO!\nOs seguintes itens precisam de reposição urgente:\n"
            for pc in produtos_criticos:
                alert_msg += f"• {pc[2]}: Restam apenas {pc[5]} un\n"
            enviar_alerta_telegram(alert_msg)
    except Exception as e:
        print(f"Aviso: Não foi possível enviar notificação no Telegram ({e})")

    return venda_id, data_hora, total_venda

# ---------------------------------------------------------------------
# INTELIGÊNCIA PREDITIVA
# ---------------------------------------------------------------------

def get_analise_preditiva_estoque():
    """
    Calcula a velocidade média diária de vendas dos últimos 30 dias
    e prevê em quantos dias o estoque atual irá esgotar (Ruptura de Estoque).
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT id, nome, quantidade, preco FROM produtos')
    produtos = cursor.fetchall()

    analise = []

    for p in produtos:
        p_id, nome, qtd_atual, preco = p

        cursor.execute('''
            SELECT SUM(quantidade)
            FROM itens_venda
            WHERE produto_nome = %s
        ''', (nome,))

        res = cursor.fetchone()
        qtd_vendida_30_dias = res[0] if res and res[0] else 0

        vendas_por_dia = qtd_vendida_30_dias / 30.0

        if vendas_por_dia > 0:
            dias_restantes = int(qtd_atual / vendas_por_dia)
        else:
            dias_restantes = 999  # Sem vendas registradas ou produto parado

        analise.append({
            "id": p_id,
            "nome": nome,
            "qtd_atual": qtd_atual,
            "vendas_30d": qtd_vendida_30_dias,
            "media_diaria": round(vendas_por_dia, 2),
            "dias_restantes": dias_restantes,
            "status": "🔴 CRÍTICO" if dias_restantes <= 5 else ("🟡 ATENÇÃO" if dias_restantes <= 10 else "🟢 OK")
        })

    cursor.close()
    conn.close()
    return analise

# ---------------------------------------------------------------------
# BACKUP E RESTAURAÇÃO
# ---------------------------------------------------------------------
#
# No Supabase, o backup é feito pelo próprio painel (Database > Backups)
# ou via `pg_dump` apontando pra sua connection string — não faz mais
# sentido copiar um arquivo local, já que os dados não moram mais aqui.

def fazer_backup_banco(caminho_destino):
    raise NotImplementedError(
        "Com Supabase, use o backup automático do painel (Database > Backups) "
        "ou rode: pg_dump \"$DATABASE_URL\" > backup.sql"
    )

def restaurar_backup_banco(caminho_origem):
    raise NotImplementedError(
        "Com Supabase, restaure pelo painel (Database > Backups) "
        "ou rode: psql \"$DATABASE_URL\" < backup.sql"
    )
