from __future__ import annotations
import sqlite3
from pathlib import Path

DEFAULT_PRODUCTS = [
    ('IFA', 'IFA 平台', '软件平台', 'SOFTWARE', 1),
    ('PLC', 'PLC', '嵌入式产品', 'EMBEDDED', 2),
    ('HMI', 'HMI', '嵌入式产品', 'SOFTWARE', 3),
]

class ProductConfigRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS product_config(
                product_code TEXT PRIMARY KEY, product_name TEXT NOT NULL,
                product_kind TEXT NOT NULL DEFAULT 'PRODUCT', default_issue_domain TEXT NOT NULL DEFAULT 'AUTO',
                enabled INTEGER NOT NULL DEFAULT 1, sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
            for row in DEFAULT_PRODUCTS:
                c.execute('INSERT OR IGNORE INTO product_config(product_code,product_name,product_kind,default_issue_domain,sort_order) VALUES(?,?,?,?,?)', row)
    def connect(self):
        c = sqlite3.connect(self.db_path); c.row_factory = sqlite3.Row; return c
    def list(self, enabled_only=True):
        q = 'SELECT * FROM product_config' + (' WHERE enabled=1' if enabled_only else '') + ' ORDER BY sort_order,product_name'
        with self.connect() as c: return [dict(x) for x in c.execute(q).fetchall()]
    def get(self, code):
        with self.connect() as c:
            row = c.execute('SELECT * FROM product_config WHERE product_code=?', (str(code).upper(),)).fetchone()
            return dict(row) if row else None
    def upsert(self, code, name, kind='PRODUCT', default_issue_domain='AUTO', enabled=True, sort_order=0):
        code = str(code).strip().upper()
        if not code or not name: raise ValueError('PRODUCT_CODE_AND_NAME_REQUIRED')
        with self.connect() as c:
            c.execute('''INSERT INTO product_config(product_code,product_name,product_kind,default_issue_domain,enabled,sort_order)
                VALUES(?,?,?,?,?,?) ON CONFLICT(product_code) DO UPDATE SET product_name=excluded.product_name,product_kind=excluded.product_kind,default_issue_domain=excluded.default_issue_domain,enabled=excluded.enabled,sort_order=excluded.sort_order,updated_at=CURRENT_TIMESTAMP''',
                (code, str(name).strip(), str(kind).strip() or 'PRODUCT', str(default_issue_domain).upper(), int(bool(enabled)), int(sort_order)))
        return self.get(code)
    def set_enabled(self, code, enabled):
        with self.connect() as c:
            c.execute('UPDATE product_config SET enabled=?,updated_at=CURRENT_TIMESTAMP WHERE product_code=?',(int(bool(enabled)),str(code).upper()))
        return self.get(code)
