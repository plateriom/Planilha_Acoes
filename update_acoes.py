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
    """Autentica no Google usando a string limpa direto da memória."""
    # Pega o conteúdo do segredo direto da variável de ambiente configurada no Actions
    json_env = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    
    if not json_env:
        # Fallback para desenvolvimento local caso o arquivo ainda exista
        if os.path.exists('credentials.json'):
            with open('credentials.json', 'r') as f:
                info_credenciais = json.load(f)
        else:
            raise ValueError("Credenciais do Google não encontradas no ambiente e nem em 'credentials.json'")
    else:
        info_credenciais = json.loads(json_env)

    # Autentica usando o dicionário direto na memória (imune a erros de arquivo físico)
    creds = Credentials.from_service_account_info(info_credenciais, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SHEET_ID)
    return spreadsheet.worksheet(nome_aba)

# ================== FUNÇÃO PARA GERAR SESSÃO ISOLADA ==================
def criar_sessao_yahoo():
    sessao = requests.Session()
    sessao.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    })
    return sessao

# O restante do seu código (load_cache, fetch_ticker_data e main) continua EXATAMENTE IGUAL ao envio anterior.
