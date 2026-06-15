import yfinance as yf
import gspread
import pandas as pd
import numpy as np
import time
import random
import json
import os
import requests
from datetime import datetime
from google.oauth2.service_account import Credentials

# ================== CONFIG ==================
SHEET_ID = "1saHSvkcUV7FUbYaJWJUtC6LBH2svMBOs-5kd8TMGpFU"
TICKERS_SHEET = "AÇÕES"
DATA_SHEET = "Dados"

MAX_RETRIES = 3
CACHE_FILE = "cache_fundamentals.json"
CACHE_TTL = 60 * 60 * 6  # 6h

SCOPES = [
    'https://googleapis.com',
    'https://googleapis.com'
]

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

# ================== FUNÇÃO PARA CONEXÃO COM O GOOGLE ==================
def conectar_google_sheets(nome_aba):
    """Autentica no Google usando a string limpa direto da memória ou arquivo local."""
    json_env = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    
    if not json_env:
        # Se rodar localmente, tenta ler o arquivo físico
        if os.path.exists('credentials.json'):
            with open('credentials.json', 'r') as f:
                info_credenciais = json.load(f)
        else:
            raise ValueError("Credenciais do Google não encontradas no ambiente e nem em 'credentials.json'")
    else:
        info_credenciais = json.loads(json_env)

    # Autentica direto pela memória para evitar erros de encoding no GitHub Actions
    creds = Credentials.from_service_account_info(info_credenciais, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SHEET_ID)
    return spreadsheet.worksheet(nome_aba)

# ================== FUNÇÃO PARA GERAR SESSÃO ISOLADA ==================
def criar_sessao_yahoo():
    """Gera uma sessão nova e limpa simulando um navegador real."""
    sessao = requests.Session()
    sessao.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Connection': 'keep-alive'
    })
    return sessao

# ================== SISTEMA DE CACHE ==================
def load_cache():
    if not os.path.exists(CACHE_FILE): return {}
    with open(CACHE_FILE, "r") as f: return json.load(f)

def save_cache(cache):
    with open(CACHE_FILE, "w") as f: json.dump(cache, f)

cache = load_cache()

def get_cached(ticker):
    data = cache.get(ticker)
    if not data: return None
    if time.time() - data.get("timestamp", 0) > CACHE_TTL: return None
    return data.get("payload")

def set_cache(ticker, payload):
    cache[ticker] = {"timestamp": time.time(), "payload": payload}
    save_cache(cache)

# ================== BUSCA DE DADOS ==================
def fetch_ticker_data(ticker):
    cached_data = get_cached(ticker)
    if cached_data:
        log(f"[{ticker}] Dados recuperados do cache.")
        return cached_data

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Pausa curta e dinâmica entre 2 e 4 segundos para evitar bloqueios por IP
            wait_time = random.uniform(2.0, 4.0)
            time.sleep(wait_time)
            
            log(f"[{ticker}] Buscando... (Tentativa {attempt}/{MAX_RETRIES})")
            
            sessao_exclusiva = criar_sessao_yahoo()
            t = yf.Ticker(ticker, session=sessao_exclusiva)
            info = t.info
            
            if not info or len(info) <= 1:
                raise ValueError("Resposta vazia da API.")

            payload = {
                "Ticker": ticker,
                "Preço": info.get("currentPrice", np.nan),
                "P/L": info.get("trailingPE", np.nan),
                "P/VP": info.get("priceToBook", np.nan),
                "DY (%)": (info.get("dividendYield", 0) or 0) * 100 if info.get("dividendYield") else np.nan,
                "ROE (%)": (info.get("returnOnEquity", 0) or 0) * 100 if info.get("returnOnEquity") else np.nan,
                "Margem Líq (%)": (info.get("profitMargins", 0) or 0) * 100 if info.get("profitMargins") else np.nan,
                "EV/EBITDA": info.get("enterpriseToEbitda", np.nan)
            }
            
            set_cache(ticker, payload)
            return payload

        except Exception as e:
            log(f"[{ticker}] Erro: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(4 * attempt)
            else:
                log(f"[{ticker}] Falha definitiva.")
                
    return {"Ticker": ticker, "Preço": np.nan, "P/L": np.nan, "P/VP": np.nan, "DY (%)": np.nan, "ROE (%)": np.nan, "Margem Líq (%)": np.nan, "EV/EBITDA": np.nan}

# ================== EXECUÇÃO PRINCIPAL ==================
def main():
    start_time = time.time()
    log("Iniciando atualização diária...")

    # Conexão 1: Lê os tickers de forma isolada e fecha a sessão do Google
    log("Lendo tickers da planilha...")
    sheet_acoes = conectar_google_sheets(TICKERS_SHEET)
    coluna_tickers = sheet_acoes.col_values(1)
    tickers = [t.strip().upper() for t in coluna_tickers[1:] if t.strip()]
    
    if not tickers:
        log("Nenhum ticker encontrado na aba AÇÕES.")
        return

    log(f"Processando {len(tickers)} ativos sequencialmente fora da conexão do Sheets...")

    # Realiza toda a extração de forma sequencial com intervalos anti-bloqueio
    results = []
    for ticker in tickers:
        res = fetch_ticker_data(ticker)
        results.append(res)

    # Processamento dos resultados no DataFrame
    df_resultado = pd.DataFrame(results)
    df_resultado['Ticker'] = pd.Categorical(df_resultado['Ticker'], categories=tickers, ordered=True)
    df_resultado = df_resultado.sort_values('Ticker').reset_index(drop=True)
    df_resultado = df_resultado.replace([np.inf, -np.inf], np.nan).fillna("")

    # Formata a matriz para envio ao Google Sheets
    header = df_resultado.columns.tolist()
    rows = df_resultado.values.tolist()
    data_to_write = [header] + rows

    # Conexão 2: Abre uma nova conexão fresca apenas para despejar a matriz final
    log(f"Abrindo nova conexão para gravar dados na aba '{DATA_SHEET}'...")
    sheet_dados = conectar_google_sheets(DATA_SHEET)
    sheet_dados.clear()
    
    end_col = chr(64 + len(header)) if len(header) <= 26 else "Z"
    cell_range = f"A1:{end_col}{len(data_to_write)}"
    sheet_dados.update(range_name=cell_range, values=data_to_write)
    
    log(f"Finalizado em {time.time() - start_time:.2f} segundos!")

if __name__ == "__main__":
    main()
