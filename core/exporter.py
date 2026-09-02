from datetime import datetime
import pandas as pd
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from core.database import get_todos_produtos, get_produtos_baixo_estoque

def exportar_excel(caminho):
    produtos = get_todos_produtos()
    cols = ["ID", "Código", "Nome", "Descrição", "Preço (R$)", "Quantidade", "Estoque Mínimo"]
    df = pd.DataFrame(produtos, columns=cols)
    df.to_excel(caminho, index=False)

def exportar_pdf(caminho):
    produtos = get_todos_produtos()
    c = canvas.Canvas(caminho, pagesize=letter)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, 750, "Relatório Geral de Estoque")
    
    c.setFont("Helvetica", 9)
    c.drawString(50, 735, f"Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    
    y = 700
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "ID | Código | Nome | Preço | Qtd | Est. Mín")
    c.line(50, y-5, 550, y-5)
    y -= 25

    c.setFont("Helvetica", 10)
    for p in produtos:
        if y < 50:
            c.showPage()
            y = 750
        preco = p[4] or 0.0
        qtd = p[5] or 0
        est_min = p[6] or 5
        linha = f"#{p[0]} | {p[1] or 'N/A'} | {p[2]} | R$ {preco:.2f} | {qtd} un | Mín: {est_min}"
        c.drawString(50, y, linha)
        y -= 20
    
    c.save()

def exportar_ordem_compra_pdf(caminho):
    produtos_criticos = get_produtos_baixo_estoque()
    c = canvas.Canvas(caminho, pagesize=letter)
    
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, 750, "ORDEM DE COMPRA / REABASTECIMENTO")
    
    c.setFont("Helvetica", 9)
    c.drawString(50, 735, f"Data da Solicitação: {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    c.drawString(50, 720, "Status: Produtos com Estoque Crítico (Igual ou Abaixo do Mínimo)")
    
    y = 680
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "ID | Produto | Est. Atual | Est. Mín | Sugestão de Compra")
    c.line(50, y-5, 550, y-5)
    y -= 25

    c.setFont("Helvetica", 10)
    if not produtos_criticos:
        c.drawString(50, y, "Nenhum produto precisa de reabastecimento no momento!")
    else:
        for p in produtos_criticos:
            if y < 50:
                c.showPage()
                y = 750
            p_id, nome, qtd, est_min = p[0], p[2], p[5] or 0, p[6] or 5
            sugestao = max(10, (est_min * 2) - qtd)
            linha = f"#{p_id} | {nome} | Atual: {qtd} un | Mín: {est_min} un | COMPRAR: +{sugestao} un"
            c.drawString(50, y, linha)
            y -= 20

    c.save()

def exportar_comprovante_venda_pdf(caminho, venda_id, data_hora, itens, total, pagamento):
    """Gera um comprovante de venda não fiscal em formato de recibo PDF"""
    c = canvas.Canvas(caminho, pagesize=letter)
    
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, 750, "COMPROVANTE DE VENDA")
    
    c.setFont("Helvetica", 10)
    c.drawString(50, 730, f"Venda nº: #{venda_id}")
    c.drawString(50, 715, f"Data/Hora: {data_hora}")
    c.drawString(50, 700, f"Forma de Pagamento: {pagamento}")
    
    y = 665
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "Produto | Qtd | Preço Unit. | Subtotal")
    c.line(50, y-5, 550, y-5)
    y -= 20

    c.setFont("Helvetica", 10)
    for item in itens:
        if y < 50:
            c.showPage()
            y = 750
        linha = f"{item['nome']} | {item['qtd']}x | R$ {item['preco']:.2f} | R$ {item['subtotal']:.2f}"
        c.drawString(50, y, linha)
        y -= 18

    c.line(50, y-5, 550, y-5)
    y -= 25
    c.setFont("Helvetica-Bold", 12)
    c.drawString(50, y, f"TOTAL: R$ {total:.2f}")

    c.save()