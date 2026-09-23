"""Read-only SQLite size, table and index health report."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def inspect_database(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    connection=sqlite3.connect(f"file:{path.resolve()}?mode=ro",uri=True)
    connection.row_factory=sqlite3.Row
    try:
        page_size=connection.execute('PRAGMA page_size').fetchone()[0]
        page_count=connection.execute('PRAGMA page_count').fetchone()[0]
        freelist=connection.execute('PRAGMA freelist_count').fetchone()[0]
        integrity=connection.execute('PRAGMA quick_check').fetchone()[0]
        tables=[]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            name=row['name'].replace('"','""')
            count=connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            indexes=[r['name'] for r in connection.execute(f'PRAGMA index_list("{name}")')]
            tables.append({'table':row['name'],'rows':count,'indexes':indexes})
        return {'database':str(path.resolve()),'file_bytes':path.stat().st_size,'page_bytes':page_size*page_count,
                'free_bytes':page_size*freelist,'free_percent':round(freelist/page_count*100,2) if page_count else 0,
                'journal_mode':connection.execute('PRAGMA journal_mode').fetchone()[0],
                'integrity':integrity,'tables':tables}
    finally:
        connection.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='只读检查 SQLite 数据库体量、空闲页、表记录数和索引')
    parser.add_argument('database',type=Path)
    args=parser.parse_args()
    print(json.dumps(inspect_database(args.database),ensure_ascii=False,indent=2))
