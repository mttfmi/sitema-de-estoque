import threading
import logging

from core.database import get_todos_produtos

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Estado do modelo (carregado em background, sem bloquear requests)
# ---------------------------------------------------------------------------
_model = None
_model_lock = threading.Lock()
_model_loading_started = False

# Cache de embeddings dos produtos, para não recalcular tudo a cada pergunta
_cache_embeddings = None       # numpy array
_cache_produto_ids = None      # tupla de ids/códigos usados para detectar mudança no catálogo


def _carregar_modelo_em_background():
    """Faz o download/carregamento pesado do modelo fora da thread da request."""
    global _model
    try:
        from sentence_transformers import SentenceTransformer
        logger.info("Carregando modelo de busca semântica em background...")
        modelo = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
        with _model_lock:
            _model = modelo
        logger.info("Modelo de busca semântica carregado com sucesso.")
    except Exception:
        logger.exception("Falha ao carregar o modelo de busca semântica.")


def iniciar_carregamento_modelo():
    """
    Dispara o carregamento do modelo em background assim que o app sobe
    (chamar isso uma vez no startup, ex: no main.py / app factory).
    Não bloqueia — a thread roda em paralelo enquanto o app já aceita requests.
    """
    global _model_loading_started
    with _model_lock:
        if _model_loading_started or _model is not None:
            return
        _model_loading_started = True
    thread = threading.Thread(target=_carregar_modelo_em_background, daemon=True)
    thread.start()


def get_model():
    """
    Retorna o modelo se já estiver pronto, ou None se ainda estiver
    carregando/não foi iniciado. NUNCA bloqueia a thread chamadora.
    """
    with _model_lock:
        modelo_pronto = _model is not None
        ja_iniciou = _model_loading_started

    if not ja_iniciou:
        # Ninguém chamou iniciar_carregamento_modelo() no startup ainda
        # (ex: primeiro uso em dev) — dispara o carregamento em background
        # agora, mas ainda assim retorna None nesta chamada para não travar
        # a request atual.
        iniciar_carregamento_modelo()

    return _model if modelo_pronto else None


def _atualizar_cache_embeddings(produtos, model):
    """Recalcula embeddings só se o catálogo mudou desde a última busca."""
    global _cache_embeddings, _cache_produto_ids

    ids_atuais = tuple(p[0] for p in produtos)  # assume p[0] = id único do produto

    if _cache_produto_ids == ids_atuais and _cache_embeddings is not None:
        return _cache_embeddings

    textos_produtos = [f"{p[2]} {p[3]}" for p in produtos]
    _cache_embeddings = model.encode(textos_produtos)
    _cache_produto_ids = ids_atuais
    return _cache_embeddings


def buscar_produtos_ia(query, top_k=3):
    produtos = get_todos_produtos()
    if not produtos:
        return []

    model = get_model()
    if model is None:
        # Modelo ainda não carregou (ou falhou ao carregar) — não trava a
        # request; simplesmente não há resultados de IA desta vez, e quem
        # chamou essa função cai no fallback de texto normalmente.
        logger.info("Busca por IA solicitada mas o modelo ainda não está pronto.")
        return []

    from sklearn.metrics.pairwise import cosine_similarity
    import numpy as np

    try:
        embeddings_produtos = _atualizar_cache_embeddings(produtos, model)
        embedding_query = model.encode([query])

        similaridades = cosine_similarity(embedding_query, embeddings_produtos)[0]
        indices_top = np.argsort(similaridades)[::-1][:top_k]

        resultados = []
        for idx in indices_top:
            if similaridades[idx] > 0.3:  # Limiar mínimo de confiança
                resultados.append({
                    'produto': produtos[idx],
                    'confianca': round(similaridades[idx] * 100, 2)
                })

        return resultados
    except Exception:
        logger.exception("Erro ao executar busca semântica para query: %r", query)
        return []
