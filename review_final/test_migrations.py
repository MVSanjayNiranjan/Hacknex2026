"""Check preserved rows/FTS and partially migrated legacy DBs in temp storage."""
import sys,re,sqlite3
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import storage

@pytest.mark.parametrize('version,partial',[(0,False),(1,False),(1,True)])
def test_preserves_all_legacy_rows(tmp_path,version,partial):
    path=tmp_path/'legacy.db'
    schema=re.sub(r'^\s*(?:image_width|image_height|page_width_pts|page_height_pts|rotation|has_drawings|owner|job_id)\s+[^\n]*\n','',storage.SCHEMA,flags=re.M)
    conn=sqlite3.connect(path); conn.executescript(schema)
    conn.execute("INSERT INTO notebooks VALUES ('n','Legacy',1)")
    conn.execute("INSERT INTO documents(id,notebook_id,name,stored_path,sha256,size_bytes,created_at,status) VALUES ('d','n','legacy.pdf','not-opened','hash',5,1,'ready')")
    conn.execute("INSERT INTO pages(id,document_id,page_number,char_count) VALUES ('p','d',1,25)")
    conn.execute("INSERT INTO elements(id,document_id,page_id,page_number,content_type,element_index,text,extraction_method) VALUES ('e','d','p',1,'text',0,'legacy efficiency 72 percent','pymupdf_text')")
    conn.execute("INSERT INTO jobs(id,kind,status,created_at,updated_at) VALUES ('j','answer','done',1,1)")
    conn.execute("INSERT INTO chat_messages VALUES ('c','n','assistant','legacy answer',1)")
    conn.execute(f'PRAGMA user_version={version}')
    if partial: conn.execute('ALTER TABLE pages ADD COLUMN image_width INTEGER')
    conn.commit()
    before={table:[dict(zip([c[0] for c in cur.description],row)) for row in cur.fetchall()] for table in ['notebooks','documents','pages','elements','jobs','chat_messages'] for cur in [conn.execute('SELECT * FROM '+table)]}
    conn.close(); storage.init_db(path); storage.init_db(path)
    with storage.db(path) as conn:
        for table,rows in before.items():
            now=dict(conn.execute('SELECT * FROM '+table).fetchone())
            assert {k:now[k] for k in rows[0]}==rows[0]
        assert conn.execute("SELECT rowid FROM elements_fts WHERE elements_fts MATCH 'efficiency'").fetchone()
        assert conn.execute('PRAGMA user_version').fetchone()[0]==3
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='idx_chat_job_role'").fetchone()
    path.unlink() # verifies connections closed on Windows
