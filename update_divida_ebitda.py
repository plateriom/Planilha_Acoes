import yfinance as yf
import gspread
import random
import time
from datetime import datetime
from google.oauth2.service_account import Credentials

# ================== CONFIGURAÇÕES ==================
SHEET_ID = "1saHSvkcUV7FUbYaJWJUtC6LBH2svMBOs-5kd8TMGpFU"
DATA_SHEET = "Dados"

print("🚀 Atualizando Dívida/EBITDA (versão batch)...")

SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(SHEET_ID)
sheet = spreadsheet.worksheet(DATA_SHEET)

# Ler todos os dados da aba
data = sheet.get_all_values()

if len(data) < 2:
    print("❌ Planilha vazia ou sem dados")
    exit()

# Pegar tickers da coluna A
tickers = []
for row in data[1:]:
    if row and len(row) > 0 and row[0].strip():
        tickers.append(row[0].strip().upper())

print(f"✅ Encontrados {len(tickers)} tickers")

def get_debt_ebitda(ticker):
    try:
        t = yf.Ticker(f"{ticker}.SA")
        bal = t.balance_sheet
        fin = t.financials
        
        debt = bal.loc['Total Debt'].iloc[0] if not bal.empty and 'Total Debt' in bal.index else None
        ebitda = fin.loc['EBITDA'].iloc[0] if not fin.empty and 'EBITDA' in fin.index else None
        
        if debt is not None and ebitda and float(ebitda) != 0:
            return round(float(debt) / float(ebitda), 2)
        return None
    except:
        return None

# Buscar valores
valores = []
for i, ticker in enumerate(tickers):
    print(f"[{i+1}/{len(tickers)}] {ticker}")
    value = get_debt_ebitda(ticker)
    valores.append([value])
    time.sleep(4 + random.uniform(0, 2))

# Atualização em batch (muito mais confiável)
if valores:
    # Coluna F = índice 5 (0-based)
    sheet.update(range_name=f"F2:F{len(valores)+1}", values=valores)
    print(f"✅ {len(valores)} valores de Dívida/EBITDA atualizados com sucesso!")
else:
    print("Nenhum valor encontrado")

print("Finalizado!")
