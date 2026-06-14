import yfinance as yf
import gspread
import pandas as pd
import random
import time
from datetime import datetime
import numpy as np
from google.oauth2.service_account import Credentials

# ================== CONFIGURAÇÕES ==================
SHEET_ID = "1saHSvkcUV7FUbYaJWJUtC6LBH2svMBOs-5kd8TMGpFU"
TICKERS_SHEET = "AÇÕES"
DATA_SHEET = "Dados"

print("🚀 Iniciando atualização (versão anti-erro de injeção)...")

SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(SHEET_ID)
sheet = spreadsheet.worksheet(DATA_SHEET)

# ================== LER TICKERS ==================
tickers_data = sheet_acoes = spreadsheet.worksheet(TICKERS_SHEET).get_all_values()
tickers = [str(row[0]).strip().upper() for row in tickers_data[1:] if row and str(row[0]).strip()]
tickers = list(dict.fromkeys([t for t in tickers if len(t) > 1]))
print(f"✅ {len(tickers)} tickers encontrados")

# ================== BUSCA ==================
def get_fundamentals(ticker):
    try:
        t = yf.Ticker(f"{ticker}.SA")
        info = t.info
        div_yield = info.get('trailingAnnualDividendYield') or info.get('dividendYield')
        
        return {
            "Ticker": ticker,
            "Margem Líquida (%)": round(info.get('profitMargins', 0) * 100, 2),
            "ROE (%)": round(info.get('returnOnEquity', 0) * 100, 2),
            "P/VP": round(info.get('priceToBook', 0), 2),
            "Div Yield 12M (%)": round(div_yield * 100, 2) if div_yield else None,
            "Fonte": "Yahoo",
            "Atualizado em": datetime.now().strftime("%d/%m/%Y %H:%M")
        }
    except:
        return {"Ticker": ticker, "Erro": "Falha na busca"}

# Busca dos dados
dados = []
for i, ticker in enumerate(tickers):
    print(f"[{i+1}/{len(tickers)}] {ticker}")
    dados.append(get_fundamentals(ticker))
    time.sleep(2.8 + random.uniform(0, 1.5))

# ================== LIMPEZA FORÇADA E INJEÇÃO ==================
df = pd.DataFrame(dados)
df = df.replace([np.inf, -np.inf, float('nan')], None)

# LIMPA TUDO ANTES DE INJETAR (mais confiável)
sheet.clear()

# Insere cabeçalho + dados de uma vez
header = [df.columns.tolist()]
values = df.values.tolist()

sheet.update(range_name="A1", values=header + values, value_input_option='RAW')

print("✅ Atualização concluída com sucesso (limpeza + injeção forçada)")
