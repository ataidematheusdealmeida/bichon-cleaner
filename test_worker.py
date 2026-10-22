import importlib.util, sys, types, unittest, datetime as dt
from unittest.mock import patch
import tempfile,json
from pathlib import Path
# Policy tests do not require Unix or cryptography dependencies on Windows.
for name in ('fcntl', 'blake3', 'cryptography.hazmat.primitives.ciphers.aead'):
    if name not in sys.modules:
        mod = types.ModuleType(name); sys.modules[name] = mod
        if name == 'blake3': mod.blake3 = lambda x: None
        if name.endswith('aead'): mod.AESGCM = object
spec = importlib.util.spec_from_file_location('worker', __file__.replace('test_worker.py', 'worker.py'))
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)

class SafetyTests(unittest.TestCase):
    def test_due_second_verification_precedes_new_batch(self):
        key='rule:0:1:2';state={'cursors':{key:50},'verified':{
            key+':7:1:hash':{'count':1,'last':0},
            key+':7:2:hash':{'count':1,'last':90000},
            key+':8:3:hash':{'count':1,'last':0}}}
        ordered=w.ordered_candidates([1,2,3,51,52],state,key,7,{'verification_gap_hours':24},now=100000)
        self.assertEqual(ordered,[1,51,52,2,3])
        self.assertEqual(w.ordered_candidates([1,2,3,51,52],state,key,7,{'verification_gap_hours':24},now=90000)[0],1)
    def test_queued_run_forces_simulation_and_consumes_request(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'config').mkdir();(root/'state').mkdir()
            (root/'config/rules.json').write_text('[]');(root/'config/execution.json').write_text('{"allow_remote_delete":false}')
            (root/'state/run-request.json').write_text('{"rule_id":null,"apply":true}')
            with patch.object(w,'ROOT',root),patch.object(w,'database',return_value={'mailboxes':{},'accounts':{}}),patch.object(w.os,'geteuid',return_value=1000,create=True),patch.object(w.fcntl,'flock',create=True),patch.object(w.fcntl,'LOCK_EX',2,create=True),patch.object(w.fcntl,'LOCK_NB',4,create=True),patch.object(w,'api') as api:
                w.run();api.assert_not_called()
            self.assertFalse((root/'state/run-request.json').exists())
            self.assertEqual(json.loads((root/'state/latest-report.json').read_text())['mode'],'simulation')
            self.assertEqual(json.loads((root/'state/execution-status.json').read_text())['state'],'finished')
    def test_precedence_and_disabled_exception(self):
        global_rule = {'id': 'g', 'account_id': None, 'mailbox_id': None, 'enabled': True}
        account = {'id': 'a', 'account_id': 1, 'mailbox_id': None, 'enabled': False}
        folder = {'id': 'f', 'account_id': 1, 'mailbox_id': 2, 'enabled': True}
        self.assertEqual(w.policy([global_rule, account, folder], 1, 2), folder)
        self.assertEqual(w.policy([global_rule, account, folder], 1, 3), account)
        self.assertEqual(w.policy([global_rule, account, folder], 3, 3), global_rule)
        with self.assertRaises(ValueError): w.policy([global_rule, global_rule], 1, 1)

    def test_multiple_folders_and_exclusions(self):
        g={'id':'g','account_id':None,'mailbox_id':None,'enabled':True}
        r={'id':'a','account_id':1,'mailbox_id':None,'enabled':True,'mailbox_names':['INBOX','Sent'],'excluded_mailboxes':['Protected']}
        self.assertEqual(w.policy([g,r],1,2,'INBOX'),r)
        self.assertEqual(w.policy([g,r],1,3,'Sent'),r)
        self.assertEqual(w.policy([g,r],1,4,'Other'),g)
        self.assertIsNone(w.policy([g,r],1,5,'Protected'))
        r['mailbox_names']=[]
        self.assertEqual(w.policy([g,r],1,4,'Other'),r)
        self.assertIsNone(w.policy([g,r],1,5,'Protected'))
        self.assertEqual(w.policy([g,r],2,5,'Protected'),g)

    def test_calendar_retention(self):
        now = dt.datetime(2024, 3, 31, tzinfo=dt.timezone.utc)
        self.assertEqual(w.cutoff(1, now).day, 29)
        self.assertEqual(w.cutoff(12, now).year, 2023)
        with self.assertRaises(ValueError): w.cutoff(0, now)

    def test_targeted_expunge_only_and_validity(self):
        class Client:
            capabilities = (b'UIDPLUS',)
            def __init__(self): self.calls = []
            def status(self, folder, fields): return 'OK', [b'INBOX (UIDVALIDITY 7)']
            def uid(self, *args): self.calls.append(args); return 'OK', [b'']
        c = Client(); w.erase(c, 42, 7, 'INBOX')
        self.assertEqual(c.calls, [('STORE', '42', '+FLAGS.SILENT', '(\\Deleted)'), ('EXPUNGE', '42')])
        c = Client()
        with self.assertRaises(ValueError): w.erase(c, 42, 8, 'INBOX')
        self.assertEqual(c.calls, [])
        c.capabilities = ()
        with self.assertRaises(ValueError): w.erase(c, 42, 7, 'INBOX')
        self.assertEqual(c.calls, [])

    def test_imap_folder_encoding(self):
        self.assertEqual(w.imap_name('INBOX'), '"INBOX"')
        self.assertEqual(w.imap_name('A&B'), '"A&-B"')
        self.assertTrue(w.imap_name('COTAÇÃO').isascii())

    def test_archive_or_identity_mismatch_blocks_before_body(self):
        now = dt.datetime.now(dt.timezone.utc)
        receipt = {'account_id': 1, 'mailbox_id': 2, 'uid': 42, 'uid_validity': 7,
                   'mailbox_name': 'INBOX', 'original_size': 3, 'ingested_at': 0, 'content_hash': 'abc'}
        result = {'verified': True, 'envelope_indexed': True, 'email_blob_present': True,
                  'attachment_blobs_present': True, 'receipt': receipt}
        with patch.object(w, 'metadata', return_value=(now - dt.timedelta(days=400), 3, False)):
            with patch.object(w, 'api', return_value=result):
                with self.assertRaisesRegex(ValueError, 'identidade'):
                    w.verify(None, 'token', {'id': 1}, {'id': 2, 'name': 'INBOX'}, 42, {}, now, 8)
                result['email_blob_present'] = False
                with self.assertRaisesRegex(ValueError, 'confirmada'):
                    w.verify(None, 'token', {'id': 1}, {'id': 2, 'name': 'INBOX'}, 42, {}, now, 7)

    def test_expunge_failure_unmarks_only_target(self):
        class Client:
            capabilities = (b'UIDPLUS',)
            def __init__(self): self.calls = []
            def status(self, *args): return 'OK', [b'INBOX (UIDVALIDITY 7)']
            def uid(self, *args):
                self.calls.append(args)
                return ('NO', []) if args[0] == 'EXPUNGE' else ('OK', [])
        c = Client()
        with self.assertRaises(ValueError): w.erase(c, 42, 7, 'INBOX')
        self.assertEqual(c.calls[-1], ('STORE', '42', '-FLAGS.SILENT', '(\\Deleted)'))

    def test_hash_and_archive_read_are_required(self):
        now = dt.datetime.now(dt.timezone.utc)
        receipt = {'account_id': 1, 'mailbox_id': 2, 'uid': 42, 'uid_validity': 7,
                   'mailbox_name': 'INBOX', 'original_size': 3, 'ingested_at': 0,
                   'content_hash': 'abc', 'envelope_id': 'envelope'}
        result = {'verified': True, 'envelope_indexed': True, 'email_blob_present': True,
                  'attachment_blobs_present': True, 'receipt': receipt}
        class Client:
            def uid(self, *args): return 'OK', [(b'UID 42', b'eml'), b')']
        class Hash:
            def hexdigest(self): return 'abc'
        args = (Client(), 'token', {'id': 1}, {'id': 2, 'name': 'INBOX'}, 42, {}, now, 7)
        with patch.object(w, 'metadata', return_value=(now - dt.timedelta(days=400), 3, False)), patch.object(w, 'blake3', return_value=Hash()):
            with patch.object(w, 'api', side_effect=[result, 3]) as api:
                self.assertEqual(w.verify(*args), receipt)
                self.assertTrue(api.call_args.kwargs['binary'])
            with patch.object(w, 'api', side_effect=[result, ValueError('unreadable archive')]):
                with self.assertRaisesRegex(ValueError, 'unreadable'): w.verify(*args)
            receipt['content_hash'] = 'different'
            with patch.object(w, 'api', return_value=result) as api:
                with self.assertRaisesRegex(ValueError, 'Hash'): w.verify(*args)
                self.assertEqual(api.call_count, 1)

if __name__ == '__main__': unittest.main()
