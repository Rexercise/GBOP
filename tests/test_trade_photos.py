import base64
import contextlib
import json
import sqlite3
import unittest
from unittest.mock import patch, MagicMock
from gbop_voice_web import trade_photos as photos


class PhotoTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.executescript('''
        CREATE TABLE gbop_watch_runtime(id TEXT PRIMARY KEY,owner TEXT,lease_until BIGINT,last_tick BIGINT,state TEXT);
        CREATE TABLE theses(id INTEGER PRIMARY KEY,guild_id INTEGER,user_id INTEGER,asset TEXT,play TEXT,status TEXT);
        INSERT INTO theses VALUES(41,10,20,'XAUUSD','9ate8','CLOSED'),(42,10,30,'NAS100','GCT','OPEN'),(43,11,20,'BTCUSD','Other','OPEN');
        CREATE TABLE journals(id INTEGER PRIMARY KEY,thesis_id INTEGER,guild_id INTEGER,user_id INTEGER,description TEXT,rule_adherence TEXT,result_r REAL,study_note TEXT);
        INSERT INTO journals VALUES(1,41,10,20,'Gold trade','Yes',3,'Waited');
        CREATE TABLE members(guild_id INTEGER,user_id INTEGER,activated INTEGER,revoked INTEGER,PRIMARY KEY(guild_id,user_id));
        INSERT INTO members VALUES(10,20,1,0),(10,30,1,1);
        ''')
        conn = self.conn
        class Adapter:
            def execute(_,sql,params=()):
                if any(x in sql for x in ('ENABLE ROW LEVEL SECURITY','REVOKE ALL','pg_advisory_xact_lock')):
                    return conn.execute('SELECT 1')
                return conn.execute(sql,params)
        @contextlib.contextmanager
        def db():
            with conn:
                yield Adapter()
        self.db = db
        self.upload = photos.save_upload(db,10,20,'attachment-1',b'\x89PNG\r\n\x1a\nexample')
    def tearDown(self):
        self.conn.close()
    def tag(self,**changes):
        args=dict(photo_id=self.upload['photo_id'],trade_number=1,analysis='Visible sweep; execution confirmed by member.',tier=1,entry_model='Super Soup',play='9ate8',asset='XAUUSD')
        args.update(changes)
        return photos.annotate(self.db,10,20,args)
    def test_durable_upload_and_duplicate_delivery(self):
        again=photos.save_upload(self.db,10,20,'attachment-1',b'\x89PNG\r\n\x1a\nexample')
        self.assertEqual(again['photo_id'],self.upload['photo_id'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM trade_photos').fetchone()[0],1)
        self.assertEqual(photos.search(self.db,10,20,{})['photos'][0]['analysis'],'')
    def test_member_number_tags_and_journal(self):
        self.assertTrue(self.tag()['ok'])
        result=photos.search(self.db,10,20,{'trade_number':1,'tier':1,'entry_model':'super soup','play':'9ate8'})
        self.assertEqual(result['photos'][0]['journal']['result_r'],3)
        self.assertNotIn('image_base64',result['photos'][0])
        self.assertEqual(photos.search(self.db,10,20,{'tier':2})['photos'],[])
    def test_cross_member_and_cross_guild_denied(self):
        self.tag()
        self.assertEqual(photos.search(self.db,10,30,{})['photos'],[])
        self.assertEqual(photos.search(self.db,11,20,{})['photos'],[])
        self.assertFalse(photos.annotate(self.db,10,30,{'photo_id':self.upload['photo_id']})['ok'])
        self.assertFalse(self.tag(trade_number=41)['ok'])
    def test_pending_link_corrections_and_trade_deletion(self):
        self.tag(trade_number=None,tier=None)
        self.assertEqual(len(photos.search(self.db,10,20,{'unlinked_only':True})['photos']),1)
        self.tag()
        self.tag(trade_number=None,tier=2)
        self.assertEqual(photos.search(self.db,10,20,{})['photos'][0]['trade_number'],1)
        self.conn.execute('DELETE FROM theses WHERE id=41')
        self.assertEqual(photos.search(self.db,10,20,{})['photos'],[])
    def test_bad_files_and_limits(self):
        for value in (b'',b'not an image',b'x'*(photos.MAX_IMAGE_BYTES+1)):
            with self.assertRaises(ValueError):
                photos.save_upload(self.db,10,20,'bad',value)
    def test_revoked_member_cannot_retrieve(self):
        self.assertFalse(photos.photo_tool(self.db,10,30,'send_trade_photos',{})['ok'])
    @patch.dict('os.environ',{'DISCORD_TOKEN':'test'})
    def test_dm_fixed_to_requester_and_delivery_failure(self):
        self.tag()
        with patch('httpx.Client') as cls:
            client=cls.return_value.__enter__.return_value
            channel=MagicMock(status_code=200); channel.json.return_value={'id':'private'}
            client.post.side_effect=[channel,MagicMock(status_code=200)]
            self.assertEqual(photos.send_photos(self.db,10,20,{})['sent_count'],1)
            self.assertEqual(client.post.call_args_list[0].kwargs['json'],{'recipient_id':'20'})
            self.assertIn('files[0]',client.post.call_args_list[1].kwargs['files'])
            payload=json.loads(client.post.call_args_list[1].kwargs['data']['payload_json'])
            self.assertIn('**Final R:** +3R',payload['embeds'][0]['description'])
            self.assertNotIn('result_r',payload['embeds'][0]['description'])
            client.post.side_effect=[channel,MagicMock(status_code=403)]
            result=photos.send_photos(self.db,10,20,{'delivery_action':'resend'})
            self.assertFalse(result['ok']); self.assertEqual(result['sent_count'],0)
    @patch.dict('os.environ',{'DISCORD_TOKEN':'test'})
    def test_grouped_delivery_keeps_requester_bytes_and_partial_count(self):
        self.tag()
        second=photos.save_upload(self.db,10,20,'attachment-2',b'\xff\xd8\xffsecond')
        photos.annotate(self.db,10,20,dict(photo_id=second['photo_id'],trade_number=1,
                       analysis='Second chart',tier=None,play='Custom',asset='XAUUSD'))
        photos.save_upload(self.db,10,30,'foreign',b'\xff\xd8\xffother-member')
        with patch('httpx.Client') as cls:
            client=cls.return_value.__enter__.return_value
            channel=MagicMock(status_code=200); channel.json.return_value={'id':'private'}
            client.post.side_effect=[channel,MagicMock(status_code=200),MagicMock(status_code=200)]
            result=photos.send_photos(self.db,10,20,{'trade_number':1})
            self.assertEqual(result['sent_count'],2)
            calls=client.post.call_args_list[1:]
            descriptions=[]
            for call in calls:
                self.assertEqual(call.args[0],'/channels/private/messages')
                payload=json.loads(call.kwargs['data']['payload_json'])
                filename,data,mime=call.kwargs['files']['files[0]']
                self.assertEqual(payload['embeds'][0]['image']['url'],'attachment://'+filename)
                self.assertNotIn(b'other-member',data)
                descriptions.append(payload['embeds'][0]['description'])
            self.assertEqual(sum('**Final R:**' in value for value in descriptions),1)
            client.post.side_effect=[channel,MagicMock(status_code=200),MagicMock(status_code=429)]
            result=photos.send_photos(self.db,10,20,{'trade_number':1,'delivery_action':'resend'})
            self.assertFalse(result['ok'])
            self.assertEqual(result['sent_count'],1)
    def test_pagination(self):
        for i in range(6):
            photos.save_upload(self.db,10,20,str(i),b'\xff\xd8\xffexample')
        first=photos.search(self.db,10,20,{})
        second=photos.search(self.db,10,20,{'offset':first['next_offset']})
        self.assertTrue(first['has_more'])
        self.assertEqual(len(second['photos']),2)
        self.assertFalse(set(p['id'] for p in first['photos']) & set(p['id'] for p in second['photos']))
