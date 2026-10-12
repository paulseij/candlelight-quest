"""Exact, branch-aware memory. Campaign knowledge never depends on vectors."""
from __future__ import annotations
import json
import uuid


def lineage(conn, game_id):
    game = conn.execute('SELECT timeline_id FROM games WHERE id=?', (game_id,)).fetchone()
    branch = game[0] if game else 'prime'
    limit = 2**63 - 1
    result = {}
    while branch and branch not in result:
        result[branch] = limit
        row = conn.execute('SELECT parent_id,fork_watermark FROM timelines WHERE game_id=? AND id=?', (game_id,branch)).fetchone()
        if not row:
            break
        branch, fork = row
        limit = min(limit, fork)
    return result


def visible(conn, game_id, branch, watermark):
    return watermark <= lineage(conn, game_id).get(branch, -1)


class MemoryStore:
    def __init__(self, db):
        self.db = db

    def conversation(self, game_id, npc_id, limit=10, topic=''):
        with self.db.get_connection() as conn:
            branches = lineage(conn, game_id)
            rows = conn.execute('SELECT rowid,* FROM utterances WHERE game_id=? AND npc_id=? ORDER BY rowid DESC', (game_id,npc_id)).fetchall()
            rows = [dict(r) for r in rows if r['event_watermark'] <= branches.get(r['timeline_id'],-1)]
            # Recent turns are exact; add older lexical matches without exposing
            # another NPC's conversation. Preserve whole utterances.
            recent = rows[:limit]
            words = {w.lower().strip('?.!,') for w in topic.split() if len(w)>3}
            matches = set()
            if words:
                query = ' OR '.join('"'+w.replace('"','""')+'"' for w in sorted(words))
                matches = {r[0] for r in conn.execute('SELECT rowid FROM utterance_search WHERE utterance_search MATCH ? ORDER BY rank LIMIT 100',(query,))}
            relevant = [r for r in rows[limit:] if r['rowid'] in matches]
            recent.extend(relevant[:4])
            return sorted(recent,key=lambda r:r['rowid'])

    def record_exchange(self, game_id, npc_id, player_id, player_text, npc_text, turn_id):
        with self.db.get_connection() as conn:
            branch = conn.execute('SELECT timeline_id FROM games WHERE id=?',(game_id,)).fetchone()[0]
            watermark = conn.execute('SELECT COALESCE(MAX(rowid),0) FROM events WHERE game_id=?',(game_id,)).fetchone()[0]
            receipt=conn.execute('SELECT timeline_id,result FROM turn_receipts WHERE id=? AND game_id=?',(turn_id,game_id)).fetchone()
            if receipt:
                branch=receipt['timeline_id']
                packet=json.loads(receipt['result']).get('context') or {}
                watermark=max((e.get('watermark',0) for e in packet.get('recent_events',[])),default=watermark)
            for role, speaker, text in [('player',player_id,player_text),('npc',npc_id,npc_text)]:
                conn.execute('INSERT OR IGNORE INTO utterances(id,game_id,timeline_id,event_watermark,npc_id,speaker_id,text,turn_id) VALUES(?,?,?,?,?,?,?,?)',
                    (f'{turn_id}:{role}',game_id,branch,watermark,npc_id,speaker,text,turn_id))

    def commitments(self, game_id, npc_id):
        with self.db.get_connection() as conn:
            branches = lineage(conn,game_id)
            rows = conn.execute("SELECT * FROM commitments WHERE game_id=? AND npc_id=? ORDER BY event_watermark DESC",(game_id,npc_id)).fetchall()
            latest={}
            for r in rows:
                if r['event_watermark'] <= branches.get(r['timeline_id'],-1):
                    latest.setdefault(r['subject_id'] or r['id'],dict(r))
            return [r for r in latest.values() if r['status']=='active']

    def record_commitment(self, game_id, npc_id, source_id, text, commitment_id=None, subject_id=None):
        """Only engine-accepted events may create binding commitments."""
        with self.db.get_connection() as conn:
            source=conn.execute('SELECT rowid,timeline_id,event_type,mechanical_details FROM events WHERE id=? AND game_id=?',(source_id,game_id)).fetchone()
            if not source or source['event_type'] not in {'QUEST_START','QUEST_NEGOTIATION','QUEST_COMPLETE','COMMITMENT_ACCEPTED'}:
                raise ValueError('A commitment requires an accepted engine event')
            if source['event_type']=='QUEST_NEGOTIATION' and not json.loads(source['mechanical_details'] or '{}').get('accepted'):
                raise ValueError('Rejected negotiation cannot create a commitment')
            conn.execute("INSERT INTO commitments(id,game_id,timeline_id,event_watermark,npc_id,source_id,text,status,subject_id) VALUES(?,?,?,?,?,?,?,?,?)",
                         (commitment_id or source_id,game_id,source['timeline_id'],source['rowid'],npc_id,source_id,text,'fulfilled' if source['event_type']=='QUEST_COMPLETE' else 'active',subject_id))
