import yfinance as yf
import gspread
import random
import time
from datetime import datetime
from google.oauth2.service_account import Credentials

# ================== CONFIGURAÇÕES ==================
SHEET_ID = "1saHSvkcUV7FUbYaJWJUtC6LBH2svMBOs-5kd8TMGpFU"
DATA_SHEET = "Dados"

print("🚀 Tentando atualizar Dívida/EBITDA (versão debug)...")

SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
client = gspread.authorize(creds)
spreadsheet = client.open_by_key(SHEET_ID)
sheet = spreadsheet.worksheet(DATA_SHEET)

# Debug: Ver estrutura da planilha
data = sheet.get_all_values()
print(f"Total de linhas: {len(data)}")
print(f"Cabeçalhos: {data[0] if data else 'Vazio'}")

if len(data) < 2:
    print("❌ Planilha sem dados")
    exit()

tickers = [row[0].strip().upper() for row in data[1:] if len(row) > 0 and row[0].strip()]

print(f"✅ {len(tickers)} tickers encontrados")

def get_debt_ebitda(ticker):
    try:
        t = yf.Ticker(f"{ticker}.SA")
        time.sleep(3)
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
    except Exception as e:
        print(f"  Erro em {ticker}: {type(e).__name__}")
        return None

# Atualização em batch
valores = []
for i, ticker in enumerate(tickers):
    print(f"[{i+1}/{len(tickers)}] {ticker}")
    value = get_debt_ebitda(ticker)
    valores.append([value])
    time.sleep(4)

# Atualiza a coluna F inteira de uma vez
if valores:
    sheet.update(range_name=f"F2:F{len(valores)+1}", values=valores)
    print(f"✅ Atualizados {len(valores)} valores na coluna F!")
else:
    print("Nenhum valor gerado")

print("Finalizado!")
