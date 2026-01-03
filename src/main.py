import os, webbrowser, sqlite3, csv, io
from datetime import datetime
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from threading import Timer
from dotenv import load_dotenv
import uvicorn
from typing import Optional
from excel_sync import sync_to_excel

load_dotenv()
app = FastAPI()
DB_FILE = os.getenv("DATABASE_URL", "pharmacy_data.db")
ADMIN_PASSWORD = os.getenv("APP_PASSWORD", "1234")

# Ensure assets directory exists for serving static files if needed, 
# though we are using single file index.html mostly.
os.makedirs("assets", exist_ok=True)
app.mount("/assets", StaticFiles(directory="assets"), name="assets")

class Transaction(BaseModel):
    id: Optional[int] = None
    date: str
    total_sale: float
    cash_sale: float
    card_sale: float
    talabat_sale: float
    insurance_sale: float
    credit_sale: float
    med_purchase: float
    other_exp: float
    owner_collection: float
    curr_reading: float
    prev_reading: float
    opening_petty: float
    closing_petty: float
    discrepancy: float

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        # Create table if it doesn't exist
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                total_sale REAL DEFAULT 0,
                cash_sale REAL DEFAULT 0,
                card_sale REAL DEFAULT 0,
                talabat_sale REAL DEFAULT 0,
                insurance_sale REAL DEFAULT 0,
                credit_sale REAL DEFAULT 0,
                med_purchase REAL DEFAULT 0,
                other_exp REAL DEFAULT 0,
                collection REAL DEFAULT 0,
                curr_reading REAL DEFAULT 0,
                prev_reading REAL DEFAULT 0,
                opening_petty REAL DEFAULT 0,
                closing_petty REAL DEFAULT 0,
                discrepancy REAL DEFAULT 0
            )
        """)
        cursor.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.commit()
        conn.commit()
init_db()

@app.get("/", response_class=HTMLResponse)
async def read_root():
    if os.path.exists("src/index.html"):
        with open("src/index.html", "r") as f: return f.read()
    return "Index file not found."

@app.post("/add")
async def add_transaction(t: Transaction, background_tasks: BackgroundTasks):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('''INSERT INTO transactions 
            (date, total_sale, cash_sale, card_sale, talabat_sale, insurance_sale, credit_sale, med_purchase, other_exp, 
             collection, curr_reading, prev_reading, opening_petty, closing_petty, discrepancy)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (t.date, t.total_sale, t.cash_sale, t.card_sale, t.talabat_sale, t.insurance_sale, t.credit_sale, t.med_purchase, t.other_exp,
             t.owner_collection, t.curr_reading, t.prev_reading, t.opening_petty, t.closing_petty, t.discrepancy))
        conn.commit()
    
    # Trigger Local Excel Sync
    background_tasks.add_task(sync_to_excel, t.dict())

    return {"message": "Transaction added successfully"}

@app.delete("/delete/{id}")
async def delete_transaction(id: int):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM transactions WHERE id = ?", (id,))
        conn.commit()
    return {"message": "Deleted successfully"}

@app.put("/update/{id}")
async def update_transaction(id: int, t: Transaction, background_tasks: BackgroundTasks):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('''UPDATE transactions SET
            date=?, total_sale=?, cash_sale=?, card_sale=?, talabat_sale=?, 
            insurance_sale=?, credit_sale=?, med_purchase=?, other_exp=?, 
            collection=?, curr_reading=?, prev_reading=?, opening_petty=?, 
            closing_petty=?, discrepancy=?
            WHERE id = ?''',
            (t.date, t.total_sale, t.cash_sale, t.card_sale, t.talabat_sale, 
             t.insurance_sale, t.credit_sale, t.med_purchase, t.other_exp,
             t.owner_collection, t.curr_reading, t.prev_reading, t.opening_petty, 
             t.closing_petty, t.discrepancy, id))
        conn.commit()
    
    # Sync updated data (Note: Simplistic append, real sync would need update logic or just append correction)
    # For now, we will NOT sync updates to Excel automatically to avoid complexity/duplication, 
    # or we could append a correction row. 
    # Let's just append the updated version as a new row reference for now, or skip. Warning log?
    # User requested edit. Realistically, Excel is a log. 
    # Let's keeping it simple: The database is the source of truth. Excel is a backup log.
    
    return {"message": "Transaction updated successfully"}

@app.get("/history")
async def get_history():
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions ORDER BY date DESC LIMIT 50")
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

class AdminAuth(BaseModel):
    password: str

@app.post("/validate-admin")
async def validate_admin(auth: AdminAuth):
    # Fetch from DB first
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key='admin_password'")
        row = cursor.fetchone()
        
        # If set in DB, use it. Else fallback to env/default
        correct_password = row[0] if row else os.getenv("APP_PASSWORD", "1234")
            
    return {"valid": auth.password == correct_password}

class SetupRequest(BaseModel):
    password: str
    opening_petty: float

@app.get("/check-setup")
async def check_setup():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key='admin_password'")
        return {"is_setup": cursor.fetchone() is not None}

@app.post("/setup")
async def perform_setup(req: SetupRequest):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        # Save Password
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_password', ?)", (req.password,))
        
        # Create Initial Report if table is empty, or just rely on user input?
        # User requested "first petty cash". Let's create an empty transaction for TODAY with that opening cash.
        today = datetime.now().strftime("%Y-%m-%d")
        
        # Check if today exists
        cursor.execute("SELECT id FROM transactions WHERE date = ?", (today,))
        existing = cursor.fetchone()
        
        if not existing:
            # Create fresh entry
            cursor.execute("""
                INSERT INTO transactions (date, opening_petty, total_sale) 
                VALUES (?, ?, 0)
            """, (today, req.opening_petty))
        else:
            # Update existing opening petty if specifically setting up now
            cursor.execute("UPDATE transactions SET opening_petty = ? WHERE id = ?", (req.opening_petty, existing[0]))
            
        conn.commit()
    return {"success": True}

@app.get("/latest")
async def get_latest():
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        return dict(row) if row else None

@app.get("/export")
async def export_data():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM transactions")
        rows = cursor.fetchall()
        
        output = io.StringIO()
        writer = csv.writer(output)
        # Write headers
        writer.writerow(['ID', 'Date', 'Total Sale', 'Cash Sale', 'Card Sale', 'Talabat Sale', 'Insurance Sale', 'Credit Sale',
                        'Med Purchase', 'Other Exp', 'Collection', 'Curr Reading', 'Prev Reading', 
                        'Opening Petty', 'Closing Petty', 'Discrepancy'])
        writer.writerows(rows)
        output.seek(0)
        
        # Encode to UTF-8 bytes
        csv_data = output.getvalue().encode('utf-8')
        
        headers = {
            "Content-Disposition": 'attachment; filename="transactions.csv"'
        }
        
        return StreamingResponse(iter([csv_data]), media_type="text/csv", headers=headers)

def launch():
    # Only open if in a real environment, but user requested it.
    webbrowser.open("http://127.0.0.1:8000")

if __name__ == "__main__":
    Timer(1.5, launch).start()
    uvicorn.run(app, host="127.0.0.1", port=8000)
