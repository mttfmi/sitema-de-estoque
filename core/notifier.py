import urllib.parse
import urllib.request
import urllib.error
import threading
import os

try:
    from dotenv import load_dotenv
    load_dotenv()  # garante que o .env carregue mesmo se este arquivo rodar sozinho
except ImportError:
    pass

# 📱 Autenticação do Telegram — lida das variáveis de ambiente (.env), nunca
# mais hardcoded direto no código-fonte.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

def _enviar_http_background(mensagem):
    """Função interna que faz o envio pela rede em segundo plano."""
    try:
        texto_encoded = urllib.parse.quote(mensagem)
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage?chat_id={TELEGRAM_CHAT_ID}&text={texto_encoded}"
        
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status == 200:
                print("📱 [Telegram]: Notificação enviada com sucesso!")
    except urllib.error.HTTPError as e:
        print(f"❌ [Telegram Erro HTTP {e.code}]: {e.read().decode()}")
    except Exception as e:
        print(f"❌ [Telegram Erro]: {e}")

def enviar_alerta_telegram(mensagem):
    """
    Envia alertas em SEGUNDO PLANO para a interface do sistema nunca travar.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"⚠️ [Telegram não configurado — defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env] {mensagem}")
        return None

    # Dispara numa thread em segundo plano
    t = threading.Thread(target=_enviar_http_background, args=(mensagem,), daemon=True)
    t.start()
    return t

if __name__ == "__main__":
    print("⏳ Testando envio ao Telegram...")
    t = enviar_alerta_telegram("🔔 TESTE DE SISTEMA!\n\nBot configurado e funcionando perfeitamente sem travar o sistema!")
    if t:
        t.join(timeout=10)  # Aguarda o envio terminar durante o teste direto