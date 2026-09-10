import io
import os
import secrets

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
from werkzeug.middleware.proxy_fix import ProxyFix

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
from core.validators import parse_float, parse_int
from core.ficha_tecnica import (
    init_ficha_tecnica_db, listar_fichas_tecnicas, get_ficha_tecnica,
    get_ficha_tecnica_por_produto, criar_ficha_tecnica, atualizar_ficha_tecnica,
    deletar_ficha_tecnica, produto_tem_ficha_tecnica
)
from core.pcp import (
    init_pcp_db, listar_ops, get_op, criar_op, iniciar_op, concluir_op,
    cancelar_op, calcular_necessidades, op_em_risco,
    contar_ops_em_andamento, listar_insumos_em_risco, contar_insumos_em_risco
)
from core.lotes import init_lotes_db, listar_lotes, get_lote_por_op

# FLASK_ENV=production nas variáveis de ambiente do host (Render/Railway) —
# em desenvolvimento local, deixe sem definir (ou defina como "development")
PRODUCAO = os.environ.get("FLASK_ENV") == "production"

app = Flask(__name__)

# O Render (e qualquer host atrás de proxy reverso) entrega a requisição pro
# gunicorn já "internamente", então sem isso o Flask acha que toda requisição
# veio de HTTP puro e do mesmo IP interno do proxy — quebra a detecção de
# HTTPS (cookie Secure) e o remetente real (request.remote_addr, usado no
# bloqueio de força bruta do login logo abaixo). x_for=1/x_proto=1 confiam
# em exatamente um "salto" de proxy na frente, que é o caso do Render.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# Em produção, defina a variável de ambiente SECRET_KEY com um valor
# aleatório e forte (ex: python -c "import secrets; print(secrets.token_hex(32))").
# Se isso não for feito, o app cairia no valor fixo abaixo — que qualquer
# pessoa pode ver no repositório público do GitHub e usar pra forjar sessões
# e tokens CSRF de qualquer usuário. Por isso, em produção sem SECRET_KEY
# definida, gera uma chave aleatória a cada início do processo em vez de
# usar o valor público: fecha o buraco de segurança sem exigir configuração
# extra pra o site continuar no ar. O único efeito colateral é que sessões
# abertas não sobrevivem a um reinício do servidor enquanto SECRET_KEY não
# for configurada — vale configurar assim que possível.
_SECRET_KEY_PADRAO_DEV = "troque-esta-chave-antes-de-hospedar"
_secret_key_env = os.environ.get("SECRET_KEY")
if _secret_key_env:
    app.secret_key = _secret_key_env
elif PRODUCAO:
    app.secret_key = secrets.token_hex(32)
else:
    app.secret_key = _SECRET_KEY_PADRAO_DEV

# Flags de segurança do cookie de sessão — em produção (HTTPS), o cookie só
# trafega criptografado e nunca é acessível via JavaScript (mitiga roubo de
# sessão por XSS ou rede insegura). Em desenvolvimento local (HTTP puro),
# SESSION_COOKIE_SECURE=True bloquearia o próprio login, por isso só ativa
# quando FLASK_ENV=production estiver definido no host.
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=PRODUCAO,
    # Limite de tamanho do corpo da requisição — sem isso, alguém poderia
    # mandar um corpo de requisição enorme (ex: um campo de texto gigante)
    # e estourar a memória do processo, que no plano gratuito do Render é
    # de só 512MB. Nenhum formulário do sistema precisa de mais que isso.
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,  # 2 MB
)

# Proteção CSRF: gera um token único por sessão que todo formulário POST
# precisa enviar de volta. Sem isso, um site malicioso poderia forjar uma
# página que faz o navegador da vítima excluir produtos/usuários sem ela
# perceber, aproveitando a sessão já autenticada.
csrf = CSRFProtect(app)


@app.after_request
def _adicionar_cabecalhos_seguranca(resposta):
    """Cabeçalhos básicos de segurança em toda resposta — mitigam MIME
    sniffing, clickjacking (a tela nunca precisa ser embutida num iframe
    de outro site) e vazamento de URL completa via Referer entre sites."""
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    resposta.headers["X-Frame-Options"] = "DENY"
    resposta.headers["Referrer-Policy"] = "same-origin"
    if PRODUCAO:
        resposta.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resposta

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

    ops_em_andamento = contar_ops_em_andamento()
    insumos_em_risco = contar_insumos_em_risco()

    return render_template(
        "dashboard.html",
        total_produtos=total_produtos,
        valor_total=valor_total,
        criticos=criticos,
        baixos=baixos,
        proximos_ruptura=proximos_ruptura,
        ops_em_andamento=ops_em_andamento,
        insumos_em_risco=insumos_em_risco,
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

        preco, erro_preco = parse_float(request.form.get("preco"), "Preço", minimo=0)
        quantidade, erro_qtd = parse_float(request.form.get("quantidade"), "Quantidade", minimo=0)
        estoque_minimo, erro_min = parse_int(request.form.get("estoque_minimo") or 5, "Estoque mínimo", minimo=0)

        erro = erro_preco or erro_qtd or erro_min
        if erro:
            flash(erro, "erro")
            return render_template("produto_form.html", modo="novo", produto=None)

        if not nome:
            flash("O nome do produto é obrigatório.", "erro")
            return render_template("produto_form.html", modo="novo", produto=None)

        cadastrar_produto(codigo, nome, descricao, preco, quantidade, estoque_minimo)
        flash(f"Produto '{nome}' cadastrado com sucesso!", "sucesso")
        return redirect(url_for("produtos"))

    codigo_prepreenchido = request.args.get("codigo", "")
    nome_prepreenchido = request.args.get("nome", "")
    return render_template(
        "produto_form.html", modo="novo", produto=None,
        codigo_prepreenchido=codigo_prepreenchido, nome_prepreenchido=nome_prepreenchido
    )


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

        preco, erro_preco = parse_float(request.form.get("preco"), "Preço", minimo=0)
        quantidade, erro_qtd = parse_float(request.form.get("quantidade"), "Quantidade", minimo=0)
        estoque_minimo, erro_min = parse_int(request.form.get("estoque_minimo") or 5, "Estoque mínimo", minimo=0)

        erro = erro_preco or erro_qtd or erro_min
        if erro:
            flash(erro, "erro")
            return render_template("produto_form.html", modo="editar", produto=produto)

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
        ok, msg = deletar_produto(produto_id)
        flash(msg, "sucesso" if ok else "erro")
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
    produto_id, erro_id = parse_int(request.form.get("produto_id"), "Produto", obrigatorio=True)
    quantidade, erro_qtd = parse_float(request.form.get("quantidade") or 1, "Quantidade", minimo=0)

    if erro_id or erro_qtd:
        flash(erro_id or erro_qtd, "erro")
        return redirect(url_for("pdv"))

    produto = next((p for p in get_todos_produtos() if p[0] == produto_id), None)
    if not produto:
        flash("Produto não encontrado.", "erro")
        return redirect(url_for("pdv"))

    if quantidade <= 0:
        flash("Quantidade inválida.", "erro")
        return redirect(url_for("pdv"))

    estoque_disponivel = produto[5] or 0

    # Corrige o bug de carrinho: soma a quantidade já existente no carrinho
    # (consolidada no mesmo produto) com a nova solicitação, e só então
    # compara com o estoque — evita que duas adições pequenas somadas
    # ultrapassem o estoque real.
    carrinho = session.get("carrinho", [])
    item_existente = next((item for item in carrinho if item["id"] == produto_id), None)
    qtd_ja_no_carrinho = item_existente["qtd"] if item_existente else 0
    total_solicitado = qtd_ja_no_carrinho + quantidade

    if total_solicitado > estoque_disponivel:
        flash(
            f"Quantidade inválida. Você já possui {qtd_ja_no_carrinho} unidade(s) deste "
            f"produto no carrinho e existem apenas {estoque_disponivel} unidade(s) "
            "disponíveis em estoque.",
            "erro"
        )
        return redirect(url_for("pdv"))

    preco = produto[4] or 0.0
    if item_existente:
        item_existente["qtd"] = total_solicitado
        item_existente["subtotal"] = round(preco * total_solicitado, 2)
    else:
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

    # Revalida o estoque no momento de finalizar — o carrinho pode ter
    # ficado guardado na sessão por um tempo, e nesse intervalo o estoque
    # pode ter mudado (outra venda, edição de produto etc.). Sem isso, uma
    # venda podia ser registrada com uma quantidade maior do que o disponível.
    produtos_atuais = {p[0]: p for p in get_todos_produtos()}
    for item in carrinho:
        produto_atual = produtos_atuais.get(item["id"])
        estoque_disponivel = (produto_atual[5] if produto_atual else 0) or 0
        if not produto_atual or item["qtd"] > estoque_disponivel:
            flash(
                f"Estoque insuficiente para '{item['nome']}'. Disponível: {estoque_disponivel} "
                "unidade(s). Ajuste o carrinho antes de finalizar.",
                "erro"
            )
            return redirect(url_for("pdv"))

    venda_id, data_hora, total = registrar_venda(carrinho, forma_pagamento)
    session["carrinho"] = []
    flash(f"Venda #{venda_id} finalizada! Total: R$ {total:.2f}", "sucesso")
    return redirect(url_for("pdv"))


# ---------------------------------------------------------------------
# FICHA TÉCNICA
# ---------------------------------------------------------------------
@app.route("/ficha-tecnica")
@login_required
def ficha_tecnica_lista():
    fichas = listar_fichas_tecnicas()
    return render_template("ficha_tecnica_lista.html", fichas=fichas)


def _produtos_disponiveis_para_ficha(produto_atual_id=None):
    """Produtos que ainda não têm ficha técnica (mais o produto atual, se
    estivermos editando)."""
    produtos = get_todos_produtos()
    com_ficha = {f[1] for f in listar_fichas_tecnicas()}
    return [p for p in produtos if p[0] not in com_ficha or p[0] == produto_atual_id]


def _ler_itens_ficha_do_form():
    """Lê os arrays paralelos insumo_id[]/quantidade[]/unidade[] do form e
    retorna (itens, erro). itens: lista de (insumo_id, quantidade, unidade)."""
    insumo_ids = request.form.getlist("insumo_id[]")
    quantidades = request.form.getlist("quantidade[]")
    unidades = request.form.getlist("unidade[]")

    itens = []
    vistos = set()
    for i in range(len(insumo_ids)):
        if not insumo_ids[i]:
            continue
        insumo_id, erro_id = parse_int(insumo_ids[i], "Insumo", obrigatorio=True)
        quantidade, erro_qtd = parse_float(
            quantidades[i] if i < len(quantidades) else None,
            "Quantidade do insumo", minimo=0.0001, obrigatorio=True
        )
        if erro_id or erro_qtd:
            return None, erro_id or erro_qtd
        if insumo_id in vistos:
            return None, "Não é possível repetir o mesmo insumo na ficha técnica."
        vistos.add(insumo_id)
        unidade = (unidades[i] if i < len(unidades) else "un") or "un"
        itens.append((insumo_id, quantidade, unidade))

    return itens, None


@app.route("/ficha-tecnica/nova", methods=["GET", "POST"])
@login_required
def ficha_tecnica_nova():
    if request.method == "POST":
        produto_id, erro_id = parse_int(request.form.get("produto_id"), "Produto", obrigatorio=True)
        if erro_id:
            flash(erro_id, "erro")
            return redirect(url_for("ficha_tecnica_nova"))

        itens, erro_itens = _ler_itens_ficha_do_form()
        if erro_itens:
            flash(erro_itens, "erro")
            return render_template(
                "ficha_tecnica_form.html", modo="novo", ficha=None, itens=[],
                produtos=_produtos_disponiveis_para_ficha(), insumos=get_todos_produtos()
            )

        ok, msg = criar_ficha_tecnica(produto_id, itens)
        flash(msg, "sucesso" if ok else "erro")
        if ok:
            return redirect(url_for("ficha_tecnica_lista"))

    return render_template(
        "ficha_tecnica_form.html", modo="novo", ficha=None, itens=[],
        produtos=_produtos_disponiveis_para_ficha(), insumos=get_todos_produtos()
    )


@app.route("/ficha-tecnica/<int:ficha_id>/editar", methods=["GET", "POST"])
@login_required
def ficha_tecnica_editar(ficha_id):
    cabecalho, itens_atuais = get_ficha_tecnica(ficha_id)
    if not cabecalho:
        flash("Ficha técnica não encontrada.", "erro")
        return redirect(url_for("ficha_tecnica_lista"))

    if request.method == "POST":
        itens, erro_itens = _ler_itens_ficha_do_form()
        if erro_itens:
            flash(erro_itens, "erro")
        else:
            ok, msg = atualizar_ficha_tecnica(ficha_id, itens)
            flash(msg, "sucesso" if ok else "erro")
            if ok:
                return redirect(url_for("ficha_tecnica_lista"))

    return render_template(
        "ficha_tecnica_form.html", modo="editar", ficha=cabecalho, itens=itens_atuais,
        produtos=_produtos_disponiveis_para_ficha(cabecalho[1]), insumos=get_todos_produtos()
    )


@app.route("/ficha-tecnica/<int:ficha_id>/deletar", methods=["POST"])
@login_required
def ficha_tecnica_deletar(ficha_id):
    deletar_ficha_tecnica(ficha_id)
    flash("Ficha técnica excluída.", "sucesso")
    return redirect(url_for("ficha_tecnica_lista"))


# ---------------------------------------------------------------------
# PCP / ORDENS DE PRODUÇÃO
# ---------------------------------------------------------------------
@app.route("/pcp")
@login_required
def pcp_lista():
    status_filtro = request.args.get("status") or None
    ops = listar_ops(status_filtro)
    return render_template("pcp_lista.html", ops=ops, status_filtro=status_filtro)


@app.route("/pcp/insumos-em-risco")
@login_required
def pcp_insumos_risco():
    insumos = listar_insumos_em_risco()
    return render_template("pcp_insumos_risco.html", insumos=insumos)


@app.route("/pcp/nova", methods=["GET", "POST"])
@login_required
def pcp_nova():
    produtos_com_ficha = [
        p for p in get_todos_produtos()
        if produto_tem_ficha_tecnica(p[0])
    ]

    if request.method == "POST":
        produto_id, erro_id = parse_int(request.form.get("produto_id"), "Produto", obrigatorio=True)
        quantidade, erro_qtd = parse_float(request.form.get("quantidade"), "Quantidade planejada", minimo=0.0001, obrigatorio=True)
        data_prevista = request.form.get("data_prevista", "").strip()
        observacao = request.form.get("observacao", "").strip()

        erro = erro_id or erro_qtd
        if erro:
            flash(erro, "erro")
        elif not produto_tem_ficha_tecnica(produto_id):
            flash("Este produto não possui Ficha Técnica cadastrada.", "erro")
        else:
            op_id, em_risco = criar_op(
                produto_id, quantidade, data_prevista, observacao,
                current_user.id, current_user.nome_completo
            )
            if em_risco:
                flash(f"OP #{op_id} criada, porém em risco por falta de insumos.", "erro")
            else:
                flash(f"OP #{op_id} criada com sucesso.", "sucesso")
            return redirect(url_for("pcp_detalhe", op_id=op_id))

    return render_template("pcp_form.html", produtos=produtos_com_ficha)


@app.route("/pcp/<int:op_id>")
@login_required
def pcp_detalhe(op_id):
    op = get_op(op_id)
    if not op:
        flash("Ordem de Produção não encontrada.", "erro")
        return redirect(url_for("pcp_lista"))

    necessidades = calcular_necessidades(op[1], op[3])
    lote = get_lote_por_op(op_id) if op[6] == "Concluída" else None

    return render_template("pcp_detalhe.html", op=op, necessidades=necessidades, lote=lote)


@app.route("/pcp/<int:op_id>/iniciar", methods=["POST"])
@login_required
def pcp_iniciar(op_id):
    ok, msg = iniciar_op(op_id)
    flash(msg, "sucesso" if ok else "erro")
    return redirect(url_for("pcp_detalhe", op_id=op_id))


@app.route("/pcp/<int:op_id>/concluir", methods=["POST"])
@login_required
def pcp_concluir(op_id):
    ok, msg, lote_id = concluir_op(op_id)
    flash(msg, "sucesso" if ok else "erro")
    return redirect(url_for("pcp_detalhe", op_id=op_id))


@app.route("/pcp/<int:op_id>/cancelar", methods=["POST"])
@login_required
def pcp_cancelar(op_id):
    ok, msg = cancelar_op(op_id)
    flash(msg, "sucesso" if ok else "erro")
    return redirect(url_for("pcp_detalhe", op_id=op_id))


# ---------------------------------------------------------------------
# LOTES
# ---------------------------------------------------------------------
@app.route("/lotes")
@login_required
def lotes_lista():
    codigo = request.args.get("codigo") or None
    produto = request.args.get("produto") or None
    op_id = request.args.get("op_id") or None
    data = request.args.get("data") or None

    op_id_val = None
    if op_id:
        op_id_val, _ = parse_int(op_id, "OP")

    lotes = listar_lotes(codigo=codigo, produto=produto, op_id=op_id_val, data=data)
    return render_template(
        "lotes_lista.html", lotes=lotes,
        filtros={"codigo": codigo or "", "produto": produto or "", "op_id": op_id or "", "data": data or ""}
    )


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
# Tabelas do PCP — precisam ser criadas depois de init_db() porque têm
# chaves estrangeiras para a tabela produtos. Ordem importa: ficha técnica
# e OPs referenciam produtos; lotes referenciam produtos e ordens_producao.
init_ficha_tecnica_db()
init_pcp_db()
init_lotes_db()

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
