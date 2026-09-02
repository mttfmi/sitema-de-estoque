"""
Teste direto e SEM thread — mostra na hora qualquer erro real de rede,
token ou chat_id, sem o risco de a thread ser encerrada antes de imprimir.
Rode com: python teste_telegram_direto.py (na raiz do projeto, venv ativado)
"""
import os
import sys
import urllib.parse
import urllib.request
import urllib.error

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from dotenv import load_dotenv
load_dotenv(override=True)

token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

print("TELEGRAM_BOT_TOKEN carregado:", "sim, com " + str(len(token)) + " caracteres" if token else "NÃO — está vazio!")
print("TELEGRAM_CHAT_ID carregado:", chat_id if chat_id else "NÃO — está vazio!")

if not token or not chat_id:
    print("\n>> Corrija o .env antes de continuar — uma das duas variáveis não foi lida.")
    sys.exit(1)

texto_encoded = urllib.parse.quote("Teste direto (sem thread) do sistema de estoque")
url = f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat_id}&text={texto_encoded}"

print("\nEnviando requisição para o Telegram (até 20s)...")
try:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as response:
        print("Status HTTP:", response.status)
        print("Resposta:", response.read().decode())
except urllib.error.HTTPError as e:
    print("ERRO HTTP", e.code, "-", e.read().decode())
except urllib.error.URLError as e:
    print("ERRO DE CONEXÃO:", e.reason)
except Exception as e:
    print("ERRO INESPERADO:", type(e).__name__, "-", e)