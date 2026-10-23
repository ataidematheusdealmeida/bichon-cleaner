import io,json,runpy,tempfile,time,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
import test_dashboard
import test_worker

d=test_dashboard.d
w=test_worker.w

class RegressionTests(unittest.TestCase):
 def test_review_safety_regressions(self):
  # The original five reproductions now require safe outcomes, with no real IMAP.
  runpy.run_path(str(Path(__file__).parent/'tools/review_probes.py'),run_name='__main__')

 def handler(self,path,form,sid='regression'):
  from urllib.parse import urlencode
  body=urlencode(form).encode()
  h=object.__new__(d.Handler);h.path=path
  h.headers={'Content-Length':str(len(body)),'Cookie':'session='+sid}
  h.rfile=io.BytesIO(body);h.respond=MagicMock();h.redirect=MagicMock()
  return h

 def test_authenticated_form_uses_session_bound_csrf_and_redirects(self):
  d.SESSIONS['regression']=(time.time()+60,'test-token','admin')
  try:
   for token in (d.CSRF,d.session_csrf('another-session')):
    h=self.handler('/toggle',{'csrf':token,'enabled':'true','rule_id':'r'})
    with patch.object(d,'toggle_rule') as toggle:
     h.do_POST();toggle.assert_not_called();self.assertEqual(h.respond.call_args.args[1],403)
   h=self.handler('/toggle',{'csrf':d.session_csrf('regression'),'enabled':'true','rule_id':'r'})
   with patch.object(d,'api',return_value={'global_permissions':['system:root']}),patch.object(d,'toggle_rule') as toggle:
    h.do_POST();toggle.assert_called_once();h.redirect.assert_called_once_with('/')
  finally:d.SESSIONS.pop('regression',None);d.NOTICES.pop('regression',None)

 def test_authenticated_page_receives_its_own_csrf_token(self):
  d.SESSIONS['regression']=(time.time()+60,'token','admin')
  try:
   h=self.handler('/',{});h.wfile=io.BytesIO();h.send_response=MagicMock();h.send_header=MagicMock();h.end_headers=MagicMock()
   d.Handler.respond(h,f'<input value="{d.CSRF}">')
   self.assertIn(d.session_csrf('regression').encode(),h.wfile.getvalue())
   self.assertNotIn(d.CSRF.encode(),h.wfile.getvalue())
   h.wfile=io.BytesIO();d.Handler.respond(h,d.login_page())
   self.assertIn(('value="'+d.CSRF+'"').encode(),h.wfile.getvalue())
  finally:d.SESSIONS.pop('regression',None)

 def test_invalid_content_length_never_reads_body(self):
  for value in ('-1','not-an-integer','65537'):
   h=self.handler('/login',{});h.headers['Content-Length']=value;h.rfile=MagicMock()
   h.do_POST();h.rfile.read.assert_not_called()
   self.assertIn(h.respond.call_args.args[1],(400,413))

 def test_legacy_override_cannot_bypass_gap_or_batch(self):
  import contextlib
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);(root/'config').mkdir();(root/'state').mkdir()
   rule={'id':'r','account_id':1,'enabled':True,'dry_run':False,'older_than_months':12,'batch_size':1,'minimum_verifications':2,'verification_gap_hours':24}
   (root/'config/rules.json').write_text(json.dumps([rule]));(root/'config/execution.json').write_text('{"allow_remote_delete":true}')
   (root/'state/run-request.json').write_text('{"rule_id":"r","recheck_existing_now":true}')
   identity='r:0:1:10:7:42:hash'
   (root/'state/worker-state.json').write_text(json.dumps({'runs':{},'verified':{identity:{'count':1,'last':time.time()}}}))
   client=MagicMock();client.select.return_value=('OK',[b'2']);client.response.return_value=('UIDVALIDITY',[b'7']);client.uid.return_value=('OK',[b'42 43'])
   data={'accounts':{'1':{'id':1,'email':'test@example.com','enabled':True}},'mailboxes':{'10':{'id':10,'account_id':1,'name':'INBOX'}}}
   with contextlib.ExitStack() as stack:
    for name,value in [('ROOT',root),('database',lambda:data),('connect',lambda _:client),('login',lambda:'token'),('verify',lambda *a:{'content_hash':'hash'})]:stack.enter_context(patch.object(w,name,value))
    stack.enter_context(patch.object(w.os,'geteuid',return_value=1000,create=True))
    for name,value in [('flock',lambda *a:None),('LOCK_EX',2),('LOCK_NB',4)]:stack.enter_context(patch.object(w.fcntl,name,value,create=True))
    erase=stack.enter_context(patch.object(w,'erase'));w.run();erase.assert_not_called()
   state=json.loads((root/'state/worker-state.json').read_text());self.assertEqual(state['verified'][identity]['count'],1)
   report=json.loads((root/'state/latest-report.json').read_text());self.assertEqual(report['mailboxes'][0]['checked'],1)

 def test_revoked_admin_cannot_load_home(self):
  d.SESSIONS['regression']=(time.time()+60,'token','former-admin')
  try:
   h=self.handler('/',{})
   with patch.object(d,'api',return_value={'global_permissions':[]}),patch.object(d,'page') as page:
    h.do_GET();page.assert_not_called();self.assertEqual(h.respond.call_args.args[1],401)
  finally:d.SESSIONS.pop('regression',None)

 def test_session_expiration_is_not_extended_by_polling(self):
  expiry=time.time()+60;d.SESSIONS['regression']=(expiry,'token','admin')
  try:
   h=self.handler('/report',{});self.assertEqual(h.session(),('token','admin'))
   self.assertEqual(d.SESSIONS['regression'][0],expiry)
  finally:d.SESSIONS.pop('regression',None)

 def test_http_enable_redirect_and_replay(self):
  import http.client,threading
  from urllib.parse import urlencode
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp)
   accounts=[{'id':1,'email':'test@example.com'}];boxes={1:[{'id':10,'name':'INBOX'}]}
   with patch.object(d,'ROOT',root),patch.object(d,'RULES',root/'rules.json'),patch.object(d,'STATE',root/'state'),patch.object(d,'api',return_value={'global_permissions':['system:root']}),patch.object(d,'account_data',return_value=(accounts,boxes,'')):
    form={'account_id':['1'],'months':['12'],'receipt_days':['1'],'verifications':['2'],'gap_hours':['24'],'batch':['50'],'schedule_hours':['24']}
    d.save_rule(form,accounts,boxes);rid=d.load_rules()[0]['id']
    d.SESSIONS['regression']=(time.time()+60,'token','admin')
    server=d.ThreadingHTTPServer(('127.0.0.1',0),d.Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
    headers={'Cookie':'session=regression','Content-Type':'application/x-www-form-urlencoded'}
    try:
     client.request('GET','/',headers=headers);response=client.getresponse();body=response.read().decode()
     self.assertEqual(response.status,200);self.assertIn(d.session_csrf('regression'),body)
     encoded=urlencode({'csrf':d.session_csrf('regression'),'rule_id':rid,'enabled':'true'})
     for _ in range(2):
      client.request('POST','/toggle',encoded,headers);response=client.getresponse();response.read()
      self.assertEqual(response.status,303);self.assertEqual(response.getheader('Location'),'/')
      self.assertTrue(d.load_rules()[0]['enabled'])
     client.request('GET','/',headers=headers);response=client.getresponse();body=response.read().decode()
     self.assertIn('Regra ativada.',body);self.assertIn('name="enabled" value="false"',body)
    finally:
     client.close();server.shutdown();server.server_close();thread.join(timeout=3)
     d.SESSIONS.pop('regression',None);d.NOTICES.pop('regression',None)

 def test_legacy_conflict_does_not_abort_other_accounts(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);(root/'config').mkdir();(root/'state').mkdir()
   rules=[{'id':'a','enabled':True,'account_id':1,'mailbox_names':['INBOX']},
          {'id':'b','enabled':True,'account_id':1,'mailbox_names':['INBOX','Sent']}]
   (root/'config/rules.json').write_text(json.dumps(rules));(root/'config/execution.json').write_text('{"allow_remote_delete":false}')
   data={'accounts':{'1':{'id':1,'email':'one@example.com','enabled':True},'2':{'id':2,'email':'two@example.com','enabled':True}},
         'mailboxes':{'10':{'id':10,'account_id':1,'name':'INBOX'},'20':{'id':20,'account_id':2,'name':'INBOX'}}}
   visited=[]
   original=w.policy
   def policy(*args):visited.append(args[1]);return original(*args)
   with patch.object(w,'ROOT',root),patch.object(w,'database',return_value=data),patch.object(w,'policy',side_effect=policy),patch.object(w.os,'geteuid',return_value=1000,create=True),patch.object(w.fcntl,'flock',create=True),patch.object(w.fcntl,'LOCK_EX',2,create=True),patch.object(w.fcntl,'LOCK_NB',4,create=True):w.run()
   self.assertEqual(visited,[1,2])
   report=json.loads((root/'state/latest-report.json').read_text())
   self.assertIn('conflitantes',report['mailboxes'][0]['error'])

if __name__=='__main__':unittest.main()
