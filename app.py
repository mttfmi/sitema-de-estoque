import io
import os

from dotenv import load_dotenv
load_dotenv()  # carrega variáveis do arquivo .env automaticamente (uso local)

from flask import (
    Flask, render_template, redirect, url_for, request, flash,
    session, send_file, jsonify
)
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from flask_wtf.csrf import CSRFProtect

from core.database import (
    init_db, get_todos_produtos, get_produtos_baixo_estoque,
    get_analise_preditiva_estoque, cadastrar_produto, atualizar_produto,
    deletar_produto, registrar_venda, get_historico
)
from core.auth import (
    verificar_login, get_usuario_por_id, existe_algum_usuario, criar_usuario,
    listar_usuarios, alternar_status_usuario, deletar_usuario
)
from core.ai_chat import responder_chat_ia
from core.ai_search import buscar_produtos_ia
from core.exporter import exportar_excel, exportar_pdf, exportar_ordem_compra_pdf

# FLASK_ENV=production nas variáveis de ambiente do host (Render/Railway) —
# em desenvolvimento local, deixe sem definir (ou defina como "development")
PRODUCAO = os.environ.get("FLASK_ENV") == "production"

app = Flask(__name__)

# Em produção, defina a variável de ambiente SECRET_KEY com um valor
# aleatório e forte (ex: python -c "import secrets; print(secrets.token_hex(32))")
app.secret_key = os.environ.get("SECRET_KEY", "troque-esta-chave-antes-de-hospedar")

# Flags de segurança do cookie de sessão — em produção (HTTPS), o cookie só
# trafega criptografado e nunca é acessível via JavaScript (mitiga roubo de
# sessão por XSS ou rede insegura). Em desenvolvimento local (HTTP puro),
# SESSION_COOKIE_SECURE=True bloquearia o próprio login, por isso só ativa
# quando FLASK_ENV=production estiver definido no host.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=PRODUCAO,
)

# Proteção CSRF: gera um token único por sessão que todo formulário POST
# precisa enviar de volta. Sem isso, um site malicioso poderia forjar uma
# página que faz o navegador da vítima excluir produtos/usuários sem ela
# perceber, aproveitando a sessão já autenticada.
csrf = CSRFProtect(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
login_manager.login_message = "Faça login para acessar o sistema."


class Usuario(UserMixin):
    """Wrapper simples que adapta o dicionário retornado pelo core/auth.py
    para o formato que o Flask-Login espera (precisa de um atributo .id)."""
    def __init__(self, dados):
        self.id = dados["id"]
        self.usuario = dados["usuario"]
        self.nome_completo = dados["nome_completo"]
        self.nivel_acesso = dados["nivel_acesso"]


@login_manager.user_loader
def load_user(user_id):
    dados = get_usuario_por_id(int(user_id))
    if not dados:
        return None
    return Usuario(dados)


def admin_required(f):
    """Decorator simples para restringir rotas a administradores."""
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.nivel_acesso != "administrador":
            flash("Acesso restrito a administradores.", "erro")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------
# LOGIN / LOGOUT
# ---------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    primeiro_acesso = not existe_algum_usuario()

    if request.method == "POST":
        if primeiro_acesso:
            nome = request.form.get("nome", "").strip()
            usuario = request.form.get("usuario", "").strip()
            senha = request.form.get("senha", "")
            confirmar = request.form.get("confirmar_senha", "")

            if senha != confirmar:
                flash("As senhas não coincidem.", "erro")
                return render_template("login.html", primeiro_acesso=True)

            ok, msg = criar_usuario(usuario, nome, senha, nivel_acesso="administrador")
            if not ok:
                flash(msg, "erro")
                return render_template("login.html", primeiro_acesso=True)

            flash("Administrador criado com sucesso! Faça login para continuar.", "sucesso")
            return redirect(url_for("login"))

        usuario = request.form.get("usuario", "").strip()
        senha = request.form.get("senha", "")
        ok, dados, msg = verificar_login(usuario, senha)
        if ok:
            login_user(Usuario(dados))
            return redirect(url_for("dashboard"))
        flash(msg, "erro")

    return render_template("login.html", primeiro_acesso=primeiro_acesso)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------
# DASHBOARD
# ---------------------------------------------------------------------
@app.route("/")
@login_required
def dashboard():
    produtos = get_todos_produtos()
    baixos = get_produtos_baixo_estoque()
    total_produtos = len(produtos)
    valor_total = sum((p[4] or 0.0) * (p[5] or 0) for p in produtos)
    criticos = len(baixos)

    analise = get_analise_preditiva_estoque()
    proximos_ruptura = sorted(
        [a for a in analise if a["dias_restantes"] <= 10],
        key=lambda x: x["dias_restantes"]
    )[:5]

    return render_template(
        "dashboard.html",
        total_produtos=total_produtos,
        valor_total=valor_total,
        criticos=criticos,
        baixos=baixos,
        proximos_ruptura=proximos_ruptura,
    )


# ---------------------------------------------------------------------
# PRODUTOS
# ---------------------------------------------------------------------
@app.route("/produtos")
@login_required
def produtos():
    lista = get_todos_produtos()
    return render_template("produtos.html", produtos=lista)


@app.route("/produtos/novo", methods=["GET", "POST"])
@login_required
def produto_novo():
    if request.method == "POST":
        codigo = request.form.get("codigo", "").strip()
        nome = request.form.get("nome", "").strip()
        descricao = request.form.get("descricao", "").strip()
        preco = float(request.form.get("preco") or 0)
        quantidade = int(request.form.get("quantidade") or 0)
        estoque_minimo = int(request.form.get("estoque_minimo") or 5)

        if not nome:
            flash("O nome do produto é obrigatório.", "erro")
            return render_template("produto_form.html", modo="novo", produto=None)

        cadastrar_produto(codigo, nome, descricao, preco, quantidade, estoque_minimo)
        flash(f"Produto '{nome}' cadastrado com sucesso!", "sucesso")
        return redirect(url_for("produtos"))

    return render_template("produto_form.html", modo="novo", produto=None)


@app.route("/produtos/<int:produto_id>/editar", methods=["GET", "POST"])
@login_required
def produto_editar(produto_id):
    produto = next((p for p in get_todos_produtos() if p[0] == produto_id), None)
    if not produto:
        flash("Produto não encontrado.", "erro")
        return redirect(url_for("produtos"))

    if request.method == "POST":
        codigo = request.form.get("codigo", "").strip()
        nome = request.form.get("nome", "").strip()
        descricao = request.form.get("descricao", "").strip()
        preco = float(request.form.get("preco") or 0)
        quantidade = int(request.form.get("quantidade") or 0)
        estoque_minimo = int(request.form.get("estoque_minimo") or 5)

        if not nome:
            flash("O nome do produto é obrigatório.", "erro")
            return render_template("produto_form.html", modo="editar", produto=produto)

        atualizar_produto(produto_id, codigo, nome, descricao, preco, quantidade, estoque_minimo)
        flash(f"Produto '{nome}' atualizado com sucesso!", "sucesso")
        return redirect(url_for("produtos"))

    return render_template("produto_form.html", modo="editar", produto=produto)


@app.route("/produtos/<int:produto_id>/deletar", methods=["POST"])
@login_required
def produto_deletar(produto_id):
    produto = next((p for p in get_todos_produtos() if p[0] == produto_id), None)
    if produto:
        deletar_produto(produto_id)
        flash(f"Produto '{produto[2]}' excluído.", "sucesso")
    return redirect(url_for("produtos"))


# ---------------------------------------------------------------------
# PDV (FRENTE DE CAIXA) — carrinho fica guardado na sessão do usuário
# ---------------------------------------------------------------------
@app.route("/pdv")
@login_required
def pdv():
    lista = get_todos_produtos()
    carrinho = session.get("carrinho", [])
    total = sum(item["subtotal"] for item in carrinho)
    return render_template("pdv.html", produtos=lista, carrinho=carrinho, total=total)


@app.route("/pdv/adicionar", methods=["POST"])
@login_required
def pdv_adicionar():
    produto_id = int(request.form.get("produto_id"))
    quantidade = int(request.form.get("quantidade") or 1)

    produto = next((p for p in get_todos_produtos() if p[0] == produto_id), None)
    if not produto:
        flash("Produto não encontrado.", "erro")
        return redirect(url_for("pdv"))

    if quantidade <= 0 or quantidade > (produto[5] or 0):
        flash(f"Quantidade inválida. Estoque disponível: {produto[5] or 0} un.", "erro")
        return redirect(url_for("pdv"))

    carrinho = session.get("carrinho", [])
    preco = produto[4] or 0.0
    carrinho.append({
        "id": produto_id,
        "nome": produto[2],
        "qtd": quantidade,
        "preco": preco,
        "subtotal": round(preco * quantidade, 2),
    })
    session["carrinho"] = carrinho
    return redirect(url_for("pdv"))


@app.route("/pdv/remover/<int:indice>", methods=["POST"])
@login_required
def pdv_remover(indice):
    carrinho = session.get("carrinho", [])
    if 0 <= indice < len(carrinho):
        carrinho.pop(indice)
        session["carrinho"] = carrinho
    return redirect(url_for("pdv"))


@app.route("/pdv/finalizar", methods=["POST"])
@login_required
def pdv_finalizar():
    carrinho = session.get("carrinho", [])
    forma_pagamento = request.form.get("forma_pagamento", "Dinheiro")

    if not carrinho:
        flash("Carrinho vazio.", "erro")
        return redirect(url_for("pdv"))

    venda_id, data_hora, total = registrar_venda(carrinho, forma_pagamento)
    session["carrinho"] = []
    flash(f"Venda #{venda_id} finalizada! Total: R$ {total:.2f}", "sucesso")
    return redirect(url_for("pdv"))


# ---------------------------------------------------------------------
# CHAT IA
# ---------------------------------------------------------------------
@app.route("/chat", methods=["GET", "POST"])
@login_required
def chat():
    historico_chat = session.get("historico_chat", [])

    if request.method == "POST":
        mensagem = request.form.get("mensagem", "").strip()
        if mensagem:
            resposta = responder_chat_ia(mensagem)
            historico_chat.append({"autor": "usuario", "texto": mensagem})
            historico_chat.append({"autor": "ia", "texto": resposta})
            session["historico_chat"] = historico_chat[-20:]  # guarda só as últimas 20 mensagens

    return render_template("chat.html", historico=session.get("historico_chat", []))


@app.route("/chat/limpar", methods=["POST"])
@login_required
def chat_limpar():
    session["historico_chat"] = []
    return redirect(url_for("chat"))


# ---------------------------------------------------------------------
# BUSCA IA
# ---------------------------------------------------------------------
@app.route("/busca", methods=["GET", "POST"])
@login_required
def busca():
    resultados = []
    termo = ""
    if request.method == "POST":
        termo = request.form.get("termo", "").strip()
        if termo:
            resultados = buscar_produtos_ia(termo, top_k=6)
    return render_template("busca.html", resultados=resultados, termo=termo)


import tempfile

# ---------------------------------------------------------------------
# EXPORTAÇÃO (PDF / EXCEL)
# ---------------------------------------------------------------------
@app.route("/exportar/excel")
@login_required
def exportar_excel_rota():
    caminho = os.path.join(tempfile.gettempdir(), "relatorio_estoque.xlsx")
    exportar_excel(caminho)
    return send_file(caminho, as_attachment=True, download_name="relatorio_estoque.xlsx")


@app.route("/exportar/pdf")
@login_required
def exportar_pdf_rota():
    caminho = os.path.join(tempfile.gettempdir(), "relatorio_estoque.pdf")
    exportar_pdf(caminho)
    return send_file(caminho, as_attachment=True, download_name="relatorio_estoque.pdf")


@app.route("/exportar/ordem-compra")
@login_required
def exportar_ordem_compra_rota():
    caminho = os.path.join(tempfile.gettempdir(), "ordem_compra.pdf")
    exportar_ordem_compra_pdf(caminho)
    return send_file(caminho, as_attachment=True, download_name="ordem_compra.pdf")


# ---------------------------------------------------------------------
# HISTÓRICO
# ---------------------------------------------------------------------
@app.route("/historico")
@login_required
def historico():
    logs = get_historico()[:100]  # limita às 100 movimentações mais recentes
    return render_template("historico.html", logs=logs)


# ---------------------------------------------------------------------
# GERENCIAR USUÁRIOS (somente administrador)
# ---------------------------------------------------------------------
@app.route("/usuarios")
@login_required
@admin_required
def usuarios():
    lista = listar_usuarios()
    return render_template("usuarios.html", usuarios=lista)


@app.route("/usuarios/novo", methods=["POST"])
@login_required
@admin_required
def usuario_novo():
    nome = request.form.get("nome", "").strip()
    usuario_login = request.form.get("usuario", "").strip()
    senha = request.form.get("senha", "")
    nivel = request.form.get("nivel_acesso", "operador")

    ok, msg = criar_usuario(usuario_login, nome, senha, nivel_acesso=nivel)
    flash(msg, "sucesso" if ok else "erro")
    return redirect(url_for("usuarios"))


@app.route("/usuarios/<int:usuario_id>/status", methods=["POST"])
@login_required
@admin_required
def usuario_status(usuario_id):
    ativo = request.form.get("ativo") == "1"
    alvo = next((u for u in listar_usuarios() if u[0] == usuario_id), None)
    if alvo and alvo[1] == current_user.usuario and not ativo:
        flash("Você não pode desativar o próprio usuário logado!", "erro")
        return redirect(url_for("usuarios"))
    alternar_status_usuario(usuario_id, ativo)
    return redirect(url_for("usuarios"))


@app.route("/usuarios/<int:usuario_id>/deletar", methods=["POST"])
@login_required
@admin_required
def usuario_deletar(usuario_id):
    alvo = next((u for u in listar_usuarios() if u[0] == usuario_id), None)
    if alvo and alvo[1] == current_user.usuario:
        flash("Você não pode excluir o próprio usuário logado!", "erro")
        return redirect(url_for("usuarios"))
    deletar_usuario(usuario_id)
    flash("Usuário excluído.", "sucesso")
    return redirect(url_for("usuarios"))


# ---------------------------------------------------------------------
# INICIALIZAÇÃO
# ---------------------------------------------------------------------
init_db()
# O modelo de busca por IA (sentence_transformers) NÃO é carregado aqui no
# boot: no free tier do Render (512MB de RAM) isso estoura a memória e o
# processo é reiniciado silenciosamente. Ele é carregado sob demanda, em
# background, na primeira vez que alguém faz uma pergunta fora do
# vocabulário fixo (ver core/ai_search.py) — a request nesse momento não
# trava, ela recebe o fallback de texto imediatamente enquanto o modelo
# carrega para as próximas buscas.

if __name__ == "__main__":
    # debug=True só em desenvolvimento local. Em produção (FLASK_ENV=production
    # definido no host), o modo debug fica desligado — deixá-lo ligado num
    # servidor público permite que qualquer visitante execute código no seu
    # servidor através da tela de erro do Werkzeug.
    app.run(debug=not PRODUCAO, host="0.0.0.0", port=5000)
