from core.database import get_todos_produtos

# Variável para armazenar o modelo só quando for usado
_model = None


def get_model():
    global _model
    if _model is None:
        # Importa e carrega o modelo de IA apenas na primeira busca de verdade,
        # evitando que o sistema inteiro trave na tela de login esperando essa
        # importação pesada (sentence-transformers/transformers) terminar.
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
    return _model


def buscar_produtos_ia(query, top_k=3):
    produtos = get_todos_produtos()
    if not produtos:
        return []

    # Importados aqui dentro pelo mesmo motivo do get_model(): não atrasar a
    # abertura do sistema para quem ainda nem usou a busca por IA.
    from sklearn.metrics.pairwise import cosine_similarity
    import numpy as np

    model = get_model()  # Obtém ou carrega o modelo

    # Cria uma lista de textos combinando nome e descrição
    textos_produtos = [f"{p[2]} {p[3]}" for p in produtos]
    
    # Gera os embeddings (vetores)
    embeddings_produtos = model.encode(textos_produtos)
    embedding_query = model.encode([query])

    # Calcula a similaridade
    similaridades = cosine_similarity(embedding_query, embeddings_produtos)[0]
    
    # Pega os índices dos resultados mais relevantes
    indices_top = np.argsort(similaridades)[::-1][:top_k]
    
    resultados = []
    for idx in indices_top:
        if similaridades[idx] > 0.3: # Limiar mínimo de confiança
            resultados.append({
                'produto': produtos[idx],
                'confianca': round(similaridades[idx] * 100, 2)
            })
            
    return resultados