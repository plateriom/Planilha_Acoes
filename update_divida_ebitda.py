import yfinance as yf
import gspread
import random
import time
from datetime import datetime
from google.oauth2.service_account import Credentials

# ================== CONFIGURAÇÕES ==================
SHEET_ID = "1saHSvkcUV7FUbYaJWJUtC6LBH2svMBOs-5kd8TMGpFU"
DATA_SHEET = "Dados"

print("🚀 Atualizando coluna Dívida/EBITDA...")

SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(SHEET_ID)
sheet = spreadsheet.worksheet(DATA_SHEET)

# Pegar todos os dados
data = sheet.get_all_values()
tickers = [row[0].strip().upper() for row in data[1:] if len(row) > 0 and row[0].strip()]

print(f"✅ Encontrados {len(tickers)} tickers")

def get_debt_ebitda(ticker):
    try:
        t = yf.Ticker(f"{ticker}.SA")
        bal = t.balance_sheet
        fin = t.financials
        
        debt = None
        ebitda = None
        
        if not bal.empty and 'Total Debt' in bal.index:
            debt = bal.loc['Total Debt'].iloc[0]
        if not fin.empty and 'EBITDA' in fin.index:
            ebitda = fin.loc['EBITDA'].iloc[0]
        
        if debt is not None and ebitda and float(ebitda) != 0:
            return round(float(debt) / float(ebitda), 2)
        return None
    except:
        return None

# Atualiza apenas a coluna F (Dívida/EBITDA)
for i, ticker in enumerate(tickers):
    if ticker:
        print(f"[{i+1}/{len(tickers)}] {ticker}")
        value = get_debt_ebitda(ticker)
        sheet.update_cell(i + 2, 6, value)   # Coluna F = 6
        time.sleep(4 + random.uniform(0, 2))

print("✅ Coluna Dívida/EBITDA atualizada!")
