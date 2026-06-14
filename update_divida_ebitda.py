import yfinance as yf
import gspread
import pandas as pd
import random
import time
from datetime import datetime
from google.oauth2.service_account import Credentials

# ================== CONFIGURAÇÕES ==================
SHEET_ID = "1saHSvkcUV7FUbYaJWJUtC6LBH2svMBOs-5kd8TMGpFU"
DATA_SHEET = "Dados"

print("🚀 Atualizando apenas Dívida/EBITDA...")

SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(SHEET_ID)

sheet_dados = spreadsheet.worksheet(DATA_SHEET)
data = sheet_dados.get_all_values()

# Pega os tickers da coluna A
tickers = [row[0].strip().upper() for row in data[1:] if row and row[0].strip()]

def get_debt_ebitda(ticker):
    try:
        t = yf.Ticker(f"{ticker}.SA")
        bal = t.balance_sheet
        fin = t.financials
        
        debt = bal.loc['Total Debt'].iloc[0] if not bal.empty and 'Total Debt' in bal.index else None
        ebitda = fin.loc['EBITDA'].iloc[0] if not fin.empty and 'EBITDA' in fin.index else None
        
        if debt is not None and ebitda and ebitda != 0:
            return round(float(debt) / float(ebitda), 2)
        return None
    except:
        return None

# Atualiza apenas a coluna F (índice 5)
print("Atualizando Dívida/EBITDA...")
for i, ticker in enumerate(tickers):
    if ticker:
        print(f"[{i+1}/{len(tickers)}] {ticker}")
        div_ebitda = get_debt_ebitda(ticker)
        # Atualiza célula na coluna F (coluna 6)
        sheet_dados.update_cell(i+2, 6, div_ebitda)  # linha +2 porque tem cabeçalho
        time.sleep(3.5 + random.uniform(0, 2))

print("✅ Dívida/EBITDA atualizada com sucesso!")
