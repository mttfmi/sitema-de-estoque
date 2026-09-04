import logging
from datetime import datetime

from core.database import (
    get_todos_produtos,
    get_produtos_baixo_estoque,
    get_historico,
    get_analise_preditiva_estoque,
    get_connection,
)
from core.ai_search import buscar_produtos_ia

logger = logging.getLogger(__name__)


def responder_chat_ia(mensagem: str) -> str:
    try:
        msg = mensagem.lower().strip()
        produtos = get_todos_produtos()

        # ------------------------------------------------------------------
        # 0. SAUDAÇÕES E CONVERSA BÁSICA
        # ------------------------------------------------------------------
        termos_saudacao = [
            "oi", "ola", "olá", "bom dia", "boa tarde", "boa noite",
            "e ai", "eae", "opa", "tudo bem", "tudo bom"
        ]
        if any(msg == t or msg.startswith(t) for t in termos_saudacao):
            return ("🤖 Assistente: Olá! Posso te ajudar com informações do estoque. "
                    "Pergunte, por exemplo: 'quais produtos estão acabando', 'valor total do estoque' "
                    "ou 'o que você faz' para ver todos os comandos.")

        termos_ajuda = [
            "ajuda", "o que voce faz", "o que você faz", "como funciona",
            "comandos", "o que posso perguntar", "menu", "help"
        ]
        if any(term in msg for term in termos_ajuda):
            return (
                "🤖 Assistente: Posso te ajudar com:\n\n"
                "📦 **Estoque**: 'quais produtos', 'estoque baixo', 'quantos produtos tenho'\n"
                "💰 **Preços**: 'produto mais caro', 'produto mais barato', 'valor total do estoque'\n"
                "📊 **Quantidades**: 'maior estoque', 'menor estoque', 'produtos parados'\n"
                "📜 **Histórico**: 'histórico', 'últimas movimentações'\n"
                "🛒 **Vendas**: 'vendas de hoje', 'última venda'\n"
                "🔮 **Previsão**: 'previsão de ruptura', 'quando vai acabar'\n"
                "🔍 **Busca**: pergunte pelo nome de qualquer produto que eu busco pra você!"
            )

        termos_agradecimento = ["obrigado", "obrigada", "valeu", "vlw", "brigado", "thanks"]
        if any(term in msg for term in termos_agradecimento):
            return "🤖 Assistente: Disponha! Qualquer dúvida sobre o estoque é só chamar. 😊"

        termos_despedida = ["tchau", "até logo", "ate logo", "falou", "flw", "adeus"]
        if any(term in msg for term in termos_despedida):
            return "🤖 Assistente: Até mais! 👋"

        if not produtos:
            return "🤖 Assistente: O estoque está totalmente vazio no momento. Cadastre produtos primeiro!"

        # ------------------------------------------------------------------
        # 1. PRODUTOS ACABANDO / ESTOQUE BAIXO
        # ------------------------------------------------------------------
        termos_baixo_estoque = [
            "acabando", "baixo estoque", "estoque baixo", "recomprar", "falta",
            "reabastecer", "precisa comprar", "precisa repor", "reposicao",
            "reposição", "esgotando"
        ]
        if any(term in msg for term in termos_baixo_estoque):
            baixos = get_produtos_baixo_estoque()
            if not baixos:
                return "🤖 Assistente: Boas notícias! Todos os produtos estão com níveis de estoque saudáveis."

            res = f"⚠️ **Atenção! {len(baixos)} produto(s) precisam de reabastecimento:**\n\n"
            for p in baixos:
                res += f"• **{p[2]}** (Cód: `{p[1] or 'S/C'}`) — Restam: **{p[5] or 0} unidades** (Mínimo: {p[6] or 5})\n"
            return res.strip()

        # ------------------------------------------------------------------
        # 2. LISTAR TODOS OS PRODUTOS
        # ------------------------------------------------------------------
        termos_listagem = [
            "quais produtos", "quais sao os produtos", "quais são os produtos",
            "listar produtos", "listar estoque", "mostrar tudo", "o que tem",
            "produtos cadastrados", "catalogo", "catálogo", "ver produtos"
        ]
        if any(term in msg for term in termos_listagem):
            res = f"📦 **Produtos Cadastrados ({len(produtos)} no total):**\n\n"
            for p in produtos:
                cod = p[1] or 'S/C'
                nome = p[2] or 'Sem Nome'
                preco = p[4] if p[4] is not None else 0.0
                qtd = p[5] or 0
                est_min = p[6] or 5
                alerta = " ⚠️ [ESTOQUE BAIXO]" if qtd <= est_min else ""
                res += f"• **{nome}** (Cód: `{cod}`) — R$ {preco:.2f} | Est: **{qtd} un**{alerta}\n"
            return res.strip()

        # ------------------------------------------------------------------
        # 3. PRODUTO MAIS CARO / MAIS BARATO
        # ------------------------------------------------------------------
        termos_mais_caro = ["produto mais caro", "item mais caro", "maior preco", "maior preço", "mais caro"]
        if any(term in msg for term in termos_mais_caro):
            p = max(produtos, key=lambda x: x[4] or 0.0)
            return (f"🤖 Assistente: O produto mais caro é **{p[2]}** (Cód: `{p[1] or 'S/C'}`), "
                    f"custando **R$ {(p[4] or 0.0):.2f}**.")

        termos_mais_barato = ["produto mais barato", "item mais barato", "menor preco", "menor preço", "mais barato"]
        if any(term in msg for term in termos_mais_barato):
            p = min(produtos, key=lambda x: x[4] if x[4] is not None else float('inf'))
            return (f"🤖 Assistente: O produto mais barato é **{p[2]}** (Cód: `{p[1] or 'S/C'}`), "
                    f"custando **R$ {(p[4] or 0.0):.2f}**.")

        # ------------------------------------------------------------------
        # 4. PRODUTO COM MAIOR / MENOR ESTOQUE
        # ------------------------------------------------------------------
        termos_maior_estoque = ["produto com mais estoque", "maior estoque", "produto com maior quantidade"]
        if any(term in msg for term in termos_maior_estoque):
            p = max(produtos, key=lambda x: x[5] or 0)
            return f"🤖 Assistente: O produto com mais unidades em estoque é **{p[2]}**, com **{p[5] or 0} unidades**."

        termos_menor_estoque = ["produto com menos estoque", "menor estoque", "produto com menor quantidade"]
        if any(term in msg for term in termos_menor_estoque):
            p = min(produtos, key=lambda x: x[5] if x[5] is not None else float('inf'))
            return f"🤖 Assistente: O produto com menos unidades em estoque é **{p[2]}**, com **{p[5] or 0} unidades**."

        # ------------------------------------------------------------------
        # 5. VALOR TOTAL DO ESTOQUE (checar antes do termo genérico "total")
        # ------------------------------------------------------------------
        termos_valor_total = [
            "valor total do estoque", "valor investido", "quanto vale o estoque",
            "quanto tenho investido", "valor em estoque", "valor do estoque",
            "patrimonio em estoque", "patrimônio em estoque"
        ]
        if any(term in msg for term in termos_valor_total):
            valor_total = sum((p[4] or 0.0) * (p[5] or 0) for p in produtos)
            return f"🤖 Assistente: O valor total investido no estoque atual é de **R$ {valor_total:.2f}**."

        # ------------------------------------------------------------------
        # 6. QUANTOS PRODUTOS ESTÃO EM ESTADO CRÍTICO (checar antes do "quantos" genérico)
        # ------------------------------------------------------------------
        termos_criticos_qtd = [
            "quantos produtos criticos", "quantos produtos críticos", "quantos itens criticos",
            "quantos estao criticos", "quantos estão críticos", "produtos em estado critico",
            "produtos em estado crítico"
        ]
        if any(term in msg for term in termos_criticos_qtd):
            criticos = sum(1 for p in produtos if (p[5] or 0) <= (p[6] or 5))
            return f"🤖 Assistente: Atualmente **{criticos}** produto(s) estão em estado crítico de estoque."

        # ------------------------------------------------------------------
        # 7. QUANTIDADE TOTAL DE PRODUTOS (genérico)
        # ------------------------------------------------------------------
        termos_quantidade = ["quantos", "quantidade", "total"]
        if any(term in msg for term in termos_quantidade):
            total_itens = sum(p[5] or 0 for p in produtos)
            return (f"🤖 Assistente: Existem {len(produtos)} tipos de produtos cadastrados, "
                    f"totalizando **{total_itens} unidades** físicas no estoque.")

        # ------------------------------------------------------------------
        # 8. HISTÓRICO / ÚLTIMAS MOVIMENTAÇÕES
        # ------------------------------------------------------------------
        termos_historico = [
            "historico", "histórico", "ultimas movimentacoes", "últimas movimentações",
            "movimentacoes recentes", "movimentações recentes", "o que aconteceu recentemente",
            "log", "logs"
        ]
        if any(term in msg for term in termos_historico):
            logs = get_historico()[:5]
            if not logs:
                return "🤖 Assistente: Ainda não há nenhuma movimentação registrada."
            res = "📜 **Últimas 5 movimentações:**\n\n"
            for log in logs:
                _, data_hora, nome_produto, tipo, qtd = log
                res += f"• {data_hora} — {tipo}: **{nome_produto}** ({qtd} un)\n"
            return res.strip()

        # ------------------------------------------------------------------
        # 9. VENDAS DE HOJE
        # ------------------------------------------------------------------
        termos_vendas_hoje = [
            "vendas de hoje", "quanto vendi hoje", "faturamento de hoje",
            "vendas do dia", "quanto faturei hoje", "vendas"
        ]
        if any(term in msg for term in termos_vendas_hoje):
            hoje = datetime.now().strftime("%d/%m/%Y")
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*), COALESCE(SUM(total_venda), 0) FROM vendas WHERE data_hora LIKE %s", (f"{hoje}%",))
            qtd_vendas, total_faturado = cursor.fetchone()
            conn.close()

            if qtd_vendas == 0:
                return "🤖 Assistente: Ainda não houve nenhuma venda registrada hoje."
            return (f"🤖 Assistente: Hoje foram registradas **{qtd_vendas} venda(s)**, "
                    f"totalizando **R$ {total_faturado:.2f}** em faturamento.")

        # ------------------------------------------------------------------
        # 10. ÚLTIMA VENDA
        # ------------------------------------------------------------------
        termos_ultima_venda = ["ultima venda", "última venda", "venda mais recente", "venda recente"]
        if any(term in msg for term in termos_ultima_venda):
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT id, data_hora, total_venda, forma_pagamento FROM vendas ORDER BY id DESC LIMIT 1")
            venda = cursor.fetchone()
            conn.close()

            if not venda:
                return "🤖 Assistente: Ainda não há nenhuma venda registrada no sistema."

            venda_id, data_hora, total_venda, forma_pagamento = venda
            return (f"🤖 Assistente: A última venda (#{venda_id}) foi em **{data_hora}**, "
                    f"no valor de **R$ {total_venda:.2f}**, paga via **{forma_pagamento}**.")

        # ------------------------------------------------------------------
        # 11. PREVISÃO DE RUPTURA DE ESTOQUE (IA preditiva)
        # ------------------------------------------------------------------
        termos_previsao = [
            "previsao", "previsão", "quando vai acabar", "dias restantes",
            "vai zerar", "ruptura de estoque", "vai faltar", "previsao de ruptura",
            "previsão de ruptura"
        ]
        if any(term in msg for term in termos_previsao):
            analise = get_analise_preditiva_estoque()
            criticos = sorted(
                [a for a in analise if a["dias_restantes"] <= 10],
                key=lambda x: x["dias_restantes"]
            )[:5]

            if not criticos:
                return "🤖 Assistente: Nenhum produto com previsão de ruptura próxima. Tudo sob controle!"

            res = "🔮 **Previsão de Ruptura de Estoque (próximos a esgotar):**\n\n"
            for a in criticos:
                res += f"• {a['status']} **{a['nome']}** — {a['qtd_atual']} un restantes, esgota em ~**{a['dias_restantes']} dias**\n"
            return res.strip()

        # ------------------------------------------------------------------
        # 12. PRODUTOS PARADOS (sem vendas registradas)
        # ------------------------------------------------------------------
        termos_parados = [
            "produto parado", "produtos sem venda", "o que nao vende",
            "o que não vende", "produtos parados", "produtos encalhados"
        ]
        if any(term in msg for term in termos_parados):
            analise = get_analise_preditiva_estoque()
            parados = [a for a in analise if a["vendas_30d"] == 0][:5]

            if not parados:
                return "🤖 Assistente: Todos os produtos tiveram alguma venda nos últimos 30 dias!"

            res = "🐌 **Produtos sem vendas nos últimos 30 dias:**\n\n"
            for a in parados:
                res += f"• **{a['nome']}** — {a['qtd_atual']} un em estoque, parado\n"
            return res.strip()

        # ------------------------------------------------------------------
        # 13. BUSCA HÍBRIDA (fallback com IA semântica)
        # ------------------------------------------------------------------
        resultados = buscar_produtos_ia(mensagem, top_k=4)
        if resultados:
            res = "🤖 Assistente: Encontrei estas correspondências no estoque:\n\n"
            for r in resultados:
                p = r['produto']
                preco = p[4] if p[4] is not None else 0.0
                qtd = p[5] or 0
                res += f"📦 **{p[2]}** (Cód: `{p[1]}`)\n"
                res += f"   💰 R$ {preco:.2f} | 📊 Estoque: {qtd} un | Relevância: {r['confianca']}%\n\n"
            return res.strip()

        # ------------------------------------------------------------------
        # 14. NADA ENCONTRADO — fallback final "não trava" com sugestões
        # ------------------------------------------------------------------
        return (
            "🤖 Assistente: Não entendi essa pergunta e não encontrei nenhum produto relacionado no estoque. 🤔\n\n"
            "Algumas coisas que posso responder agora:\n"
            "• 'quais produtos estão acabando'\n"
            "• 'valor total do estoque'\n"
            "• 'vendas de hoje'\n"
            "• ou o **nome de um produto** que você quer consultar\n\n"
            "Digite 'ajuda' para ver a lista completa de comandos."
        )

    except Exception:
        # Loga o erro completo internamente (não expõe detalhes técnicos ao usuário)
        logger.exception("Falha ao processar mensagem do chat IA: %r", mensagem)
        return (
            "🤖 Assistente: Tive um problema ao processar sua pergunta agora. "
            "Pode tentar reformular ou digitar 'ajuda' para ver os comandos disponíveis."
        )
