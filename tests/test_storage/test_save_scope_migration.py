"""Upgrade a populated pre-isolation database without losing authored state."""
import importlib

from text_rpg.storage.database import Database, _MIGRATIONS


def test_populated_save_scope_upgrade(tmp_path):
    db = Database(str(tmp_path / 'legacy.db'))
    conn = db._get_raw_connection()
    conn.execute('CREATE TABLE schema_version(version INTEGER PRIMARY KEY)')
    for version, name in enumerate(_MIGRATIONS[:-1], 1):
        importlib.import_module(f'text_rpg.storage.migrations.{name}').upgrade(conn)
        conn.execute('INSERT INTO schema_version VALUES (?)', (version,))
    conn.execute("INSERT INTO games(id,name,character_id,created_at) VALUES ('one','First','hero','2026-09-13')")
    conn.execute("INSERT INTO entities(id,game_id,name,entity_type,hp_current,properties) VALUES ('npc','one','Marta','npc',3,'{\"known\":true}')")
    conn.execute("INSERT INTO locations(id,game_id,name,visited) VALUES ('inn','one','Inn',1)")
    conn.execute("INSERT INTO regions(id,game_id,name) VALUES ('region','one','Region')")
    conn.execute("INSERT INTO quests(id,game_id,name,status) VALUES ('quest','one','Quest','completed')")
    conn.execute("INSERT INTO shops(id,game_id,owner_entity_id,location_id,gold_reserve) VALUES ('shop','one','npc','inn',17)")
    tables = ('entities','locations','regions','quests','shops')
    before = {table: [tuple(row) for row in conn.execute(f'SELECT * FROM {table}')] for table in tables}
    conn.commit()
    db.initialize()
    for table in tables:
        assert [tuple(row) for row in conn.execute(f'SELECT * FROM {table}')] == before[table]
        pk = [row[1] for row in sorted(conn.execute(f'PRAGMA table_info({table})'), key=lambda r:r[5]) if row[5]]
        assert pk == ['game_id','id']
    assert not conn.execute('PRAGMA foreign_key_check').fetchall()
    db.initialize()  # Applied migration must be idempotent.
    db.close()
