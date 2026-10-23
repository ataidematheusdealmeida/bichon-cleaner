import importlib.util,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('dashboard',Path(__file__).with_name('dashboard.py'))
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
class RuleTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();d.ROOT=Path(self.tmp.name);d.RULES=d.ROOT/'rules.json';d.STATE=d.ROOT/'state'
  self.accounts=[{'id':1,'email':'one@example.com'},{'id':2,'email':'two@example.com'}]
  self.boxes={1:[{'id':10,'name':'INBOX'},{'id':11,'name':'Sent'},{'id':12,'name':'Protected'}],2:[{'id':20,'name':'Other'}]}
  self.form={'account_id':['1'],'months':['12'],'receipt_days':['14'],'verifications':['2'],'gap_hours':['24'],'batch':['50'],'schedule_hours':['24']}
 def tearDown(self):self.tmp.cleanup()
 def test_all_folders_except_tags(self):
  f={**self.form,'use_exclusions':['on'],'excluded_mailboxes':['Protected']}
  d.save_rule(f,self.accounts,self.boxes);r=d.load_rules()[0]
  self.assertEqual(r['mailbox_names'],[]);self.assertEqual(r['excluded_mailboxes'],['Protected']);self.assertFalse(r['enabled']);self.assertTrue(r['dry_run'])
 def test_multiple_tags_and_validation(self):
  f={**self.form,'mailbox_names':['INBOX','Sent','Sent']}
  d.save_rule(f,self.accounts,self.boxes);self.assertEqual(d.load_rules()[0]['mailbox_names'],['INBOX','Sent'])
  with self.assertRaises(ValueError):d.save_rule({**f,'mailbox_names':['Other']},self.accounts,self.boxes)
  with self.assertRaises(ValueError):d.save_rule({**f,'use_exclusions':['on'],'excluded_mailboxes':['Sent']},self.accounts,self.boxes)
 def test_one_account_option_and_tag_controls(self):
  with patch.object(d,'account_data',return_value=(self.accounts,self.boxes,'')):
   h=d.page('token','admin').decode()
   self.assertEqual(h.count('<option value="1">'),1)
   self.assertIn('id="include-tags"',h);self.assertIn('id="exclude-tags"',h);self.assertIn('id="except-fields" hidden',h)
   self.assertNotIn('name="target"',h)
 def test_edit_pause_toggle_delete(self):
  d.save_rule(self.form,self.accounts,self.boxes);r=d.load_rules()[0];rid=r['id']
  d.toggle_rule({'rule_id':[rid],'enabled':['true']});self.assertTrue(d.load_rules()[0]['enabled'])
  d.save_rule({**self.form,'rule_id':[rid],'receipt_days':['1']},self.accounts,self.boxes)
  edited=d.load_rules()[0];self.assertEqual(edited['id'],rid);self.assertEqual(edited['revision'],1);self.assertEqual(edited['minimum_receipt_age_days'],1);self.assertFalse(edited['enabled'])
  with patch.object(d,'account_data',return_value=(self.accounts,self.boxes,'')):
   h=d.page('token','admin').decode();self.assertIn('data-edit=',h);self.assertIn('action="/delete-rule"',h)
  d.delete_rule({'rule_id':[rid]});self.assertEqual(d.load_rules(),[])
  with self.assertRaises(ValueError):d.delete_rule({'rule_id':[rid]})
 def test_execute_now_requires_active_and_queues_once(self):
  d.save_rule(self.form,self.accounts,self.boxes);rid=d.load_rules()[0]['id']
  with self.assertRaises(ValueError):d.request_run({'rule_id':[rid]},'admin')
  d.toggle_rule({'rule_id':[rid],'enabled':['true']});d.request_run({'rule_id':[rid]},'admin')
  import json
  request=json.loads((d.STATE/'run-request.json').read_text());self.assertEqual(request['rule_id'],rid)
  self.assertFalse(d.request_run({},'admin'))
 def test_report_http_utf8_is_a_single_response(self):
  import threading,time,urllib.request
  server=d.ThreadingHTTPServer(('127.0.0.1',0),d.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  d.SESSIONS['regression']=(time.time()+60,'token','admin')
  try:
   with patch.object(d,'api',return_value={'global_permissions':['system:root']}),patch.object(d,'report_panel',return_value='<p>Execução — café</p>'):
    req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}/report',headers={'Cookie':'session=regression'})
    with urllib.request.urlopen(req) as res:
     body=res.read();self.assertEqual(res.status,200);self.assertEqual(int(res.headers['Content-Length']),len(body));self.assertEqual(body.decode(),'<p>Execução — café</p>');self.assertNotIn(b'HTTP/1.0',body)
  finally:server.shutdown();server.server_close();d.SESSIONS.pop('regression',None)
 def test_history_counts_only_confirmed_and_csv_is_safe(self):
  import json
  d.STATE.mkdir();events=[{'action':'delete_requested','email':'one@example.com','uid':1},{'action':'remote_deleted','email':'one@example.com','uid':1,'subject':'<script>alert(1)</script>','at':1000},{'action':'remote_deleted','email':'one@example.com','uid':2,'subject':'=danger','at':1000}]
  (d.STATE/'audit.jsonl').write_text('\n'.join(json.dumps(e) for e in events)+'\ninvalid tail')
  total,recent=d.deletion_history();self.assertEqual(total,2);self.assertEqual(recent[0]['uid'],2)
  h=d.report_panel();self.assertIn('&lt;script&gt;',h);self.assertNotIn('<script>alert',h)
  self.assertIn("'=danger",d.deletion_csv().decode('utf-8'))
 def test_real_deletion_requires_password_and_admin(self):
  d.save_rule(self.form,self.accounts,self.boxes);rid=d.load_rules()[0]['id'];d.toggle_rule({'rule_id':[rid],'enabled':['true']})
  form={'mode':['enable'],'confirm_delete':['on'],'password':['wrong']}
  with patch.object(d,'authenticate',side_effect=ValueError('Senha inválida')):
   with self.assertRaises(ValueError):d.set_deletion_mode(form,'admin')
  self.assertFalse((d.STATE/'execution-control.json').exists());self.assertTrue(d.load_rules()[0]['dry_run'])
  with patch.object(d,'authenticate',return_value=('new-token','admin')):
   self.assertEqual(d.set_deletion_mode(form,'admin'),'new-token')
  self.assertTrue(d.deletion_enabled());self.assertFalse(d.load_rules()[0]['dry_run'])
  d.set_deletion_mode({'mode':['disable']},'admin');self.assertFalse(d.deletion_enabled());self.assertTrue(d.load_rules()[0]['dry_run'])
 def test_non_admin_cannot_authenticate_in_cleaner(self):
  from unittest.mock import MagicMock
  response=MagicMock();response.__enter__.return_value=response
  with patch.object(d.urllib.request,'urlopen',return_value=response),patch.object(d.json,'load',return_value={'success':True,'access_token':'viewer'}),patch.object(d,'api',return_value={'global_permissions':['data:read']}):
   with self.assertRaises(PermissionError):d.authenticate('viewer','password')
if __name__=='__main__':unittest.main()
