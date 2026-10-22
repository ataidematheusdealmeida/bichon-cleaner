"""Read-only review reproductions; all IMAP and Bichon calls are mocked."""
import contextlib, io, json, sys, tempfile, time
from pathlib import Path
from unittest.mock import patch, MagicMock

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo))
import test_worker
import dashboard as d
w = test_worker.w
findings = {}

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp); (root/'config').mkdir(); (root/'state').mkdir()
    rule = {'id':'r','account_id':1,'mailbox_id':None,'enabled':True,'dry_run':False,
            'older_than_months':12,'batch_size':50,'minimum_verifications':2}
    rules_path = root/'config/rules.json'
    rules_path.write_text(json.dumps([rule]))
    (root/'config/execution.json').write_text('{"allow_remote_delete":true}')
    state = {'runs':{},'verified':{'r:0:1:10:7:42:hash':{'count':2,'last':time.time()}}}
    (root/'state/worker-state.json').write_text(json.dumps(state))
    client = MagicMock()
    client.select.return_value = ('OK',[b'1'])
    client.response.return_value = ('UIDVALIDITY',[b'7'])
    client.uid.return_value = ('OK',[b'42'])
    receipt = {'content_hash':'hash','envelope_id':'test'}
    calls = []
    def verify(*args):
        calls.append(1)
        if len(calls)==2:
            rules_path.write_text(json.dumps([{**rule,'enabled':False}]))
        return receipt
    with contextlib.ExitStack() as stack:
        for name,value in [('ROOT',root),('database',lambda:{'accounts':{'1':{'id':1,'email':'test@example.com','enabled':True}},'mailboxes':{'10':{'id':10,'account_id':1,'name':'INBOX'}}}),('connect',lambda _:client),('login',lambda:'test'),('verify',verify),('api',lambda *a,**k:{'subject':'Test'}),('metadata',lambda *a:(w.dt.datetime.now(w.dt.timezone.utc),1,False))]:
            stack.enter_context(patch.object(w,name,value))
        stack.enter_context(patch.object(w.os,'geteuid',return_value=1000,create=True))
        for name,value in [('flock',lambda *a:None),('LOCK_EX',2),('LOCK_NB',4)]:
            stack.enter_context(patch.object(w.fcntl,name,value,create=True))
        erase = stack.enter_context(patch.object(w,'erase'))
        with contextlib.redirect_stdout(io.StringIO()):w.run(force=True)
        findings['pause_during_final_verify_still_calls_erase'] = erase.call_count == 1

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp); (root/'config').mkdir(); (root/'state').mkdir()
    (root/'config/rules.json').write_text('[]')
    (root/'config/execution.json').write_text('{"allow_remote_delete":false}')
    request = root/'state/run-request.json'; request.write_text('{"rule_id":null}')
    with patch.object(w,'ROOT',root),patch.object(w,'database',side_effect=ValueError('WAL incomplete')),patch.object(w.os,'geteuid',return_value=1000,create=True),patch.object(w.fcntl,'flock',create=True),patch.object(w.fcntl,'LOCK_EX',2,create=True),patch.object(w.fcntl,'LOCK_NB',4,create=True):
        try:w.run()
        except ValueError:pass
    findings['queued_request_lost_on_database_failure'] = not request.exists()

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    with patch.object(d,'ROOT',root),patch.object(d,'RULES',root/'rules.json'),patch.object(d,'STATE',root/'state'):
        accounts=[{'id':1,'email':'test@example.com'}]
        boxes={1:[{'id':10,'name':'INBOX'},{'id':11,'name':'Sent'},{'id':12,'name':'Protected'}]}
        form={'account_id':['1'],'months':['12'],'receipt_days':['1'],'verifications':['2'],'gap_hours':['24'],'batch':['50'],'schedule_hours':['24']}
        d.save_rule({**form,'mailbox_names':['INBOX','Sent']},accounts,boxes)
        d.save_rule({**form,'mailbox_names':['INBOX','Protected']},accounts,boxes)
        try:w.policy(d.load_rules(),1,10,'INBOX')
        except ValueError:findings['overlapping_rules_accepted_then_policy_fails'] = True
        rid=d.load_rules()[0]['id']
        d.toggle_rule({'rule_id':[rid]}); first=d.load_rules()[0]['enabled']
        d.toggle_rule({'rule_id':[rid]}); second=d.load_rules()[0]['enabled']
        findings['replayed_toggle_reverses_previous_action'] = first and not second

class Body:
    def __init__(self):self.read_sizes=[]
    def read(self,size):self.read_sizes.append(size);return b'x=1'
handler=object.__new__(d.Handler);handler.headers={'Content-Length':'1000000000'}
handler.rfile=Body();handler.respond=MagicMock()
handler.do_POST()
findings['oversized_body_read_before_413'] = handler.rfile.read_sizes == [1000000000] and handler.respond.call_args.args[1]==413
print(json.dumps(findings,indent=2))
assert len(findings)==5 and all(findings.values())
