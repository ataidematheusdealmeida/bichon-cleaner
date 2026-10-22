#!/usr/bin/env python3
"""Admin-only web console for mailbox-specific Bichon cleanup rules."""
import csv,html,io,json,os,secrets,threading,time,urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

ROOT=Path(os.environ.get("CLEANER_ROOT","/config")); RULES=ROOT/"rules.json"
STATE=Path(os.environ.get("CLEANER_STATE","/state"))
BICHON_URL=os.environ.get("BICHON_URL","http://127.0.0.1:15630").rstrip("/")
CSRF=secrets.token_urlsafe(32); SESSIONS={}; SESSION_TTL=43200; RULE_LOCK=threading.Lock()
CSS="""*{box-sizing:border-box}body{font:15px system-ui;background:#0a0f1d;color:#eef3ff;margin:0}.center{min-height:100vh;display:grid;place-items:center;background:radial-gradient(circle at top,#1a2b55,#080c17 58%)}main{max-width:1100px;margin:auto;padding:28px}.login{width:min(430px,calc(100% - 32px));background:#151d31;border:1px solid #35415e;border-radius:20px;padding:32px;box-shadow:0 24px 70px #0008}.mark{width:48px;height:48px;display:grid;place-items:center;border-radius:14px;background:#4c79f6;font-size:24px}h1{margin-bottom:5px}.muted{color:#9caac2}.card{background:#151c2f;border:1px solid #29334b;border-radius:14px;padding:20px;margin:18px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px}label{display:grid;gap:7px;font-weight:650}input,select{width:100%;padding:11px;border:1px solid #40506e;border-radius:9px;background:#0d1528;color:white}button{padding:11px 16px;border:0;border-radius:9px;background:#4c79f6;color:white;font-weight:750;cursor:pointer}.secondary{background:#2a3550}.notice{padding:12px;background:#133d2d;border-radius:9px;margin:12px 0}.bad{background:#562630}.badge{display:inline-block;padding:4px 8px;border-radius:99px;background:#413817;color:#ffe48a;font-size:12px}.rules{display:grid;gap:12px}.rule{border:1px solid #34405b;border-radius:11px;padding:15px;display:flex;justify-content:space-between;gap:15px;align-items:center}.rule p{margin:5px 0}.safe{color:#7fe2a7}small{display:block;color:#8593ad;margin-top:16px}header{display:flex;justify-content:space-between;align-items:start;gap:15px}@media(max-width:650px){.rule,header{display:block}.rule form,header form{margin-top:12px}}"""

CSS+=".tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.tag{display:inline-flex;align-items:center;gap:7px;padding:5px 9px;border-radius:16px;background:#2a3550}.tag button{padding:0 4px;background:transparent}.except{display:flex;align-items:center;gap:8px}.except input{width:auto}[hidden]{display:none!important}"
TAG_SCRIPT="\nconst folders=JSON.parse(document.getElementById('folder-data').textContent);\nconst account=document.getElementById('account'), toggle=document.getElementById('except-toggle');\nconst selected={include:new Set(),exclude:new Set()};\nfunction render(kind){\n const host=document.getElementById(kind+'-tags');host.replaceChildren();\n for(const name of selected[kind]){\n  const tag=document.createElement('span');tag.className='tag';\n  const text=document.createElement('span');text.textContent=name;\n  const input=document.createElement('input');input.type='hidden';input.name=kind==='include'?'mailbox_names':'excluded_mailboxes';input.value=name;\n  const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.setAttribute('aria-label','Remover '+name);remove.onclick=()=>{selected[kind].delete(name);render(kind);populate();};\n  tag.append(text,input,remove);host.append(tag);\n }\n}\nfunction populate(){\n for(const kind of ['include','exclude']){\n  const picker=document.getElementById(kind+'-picker');picker.replaceChildren(new Option('Adicionar pasta…',''));\n  for(const name of folders[account.value]||[]){\n   if(!selected.include.has(name)&&!selected.exclude.has(name))picker.add(new Option(name,name));\n  }\n }\n}\nfor(const kind of ['include','exclude'])document.getElementById(kind+'-picker').onchange=e=>{\n if(e.target.value){selected[kind].add(e.target.value);render(kind);populate();}\n};\naccount.onchange=()=>{selected.include.clear();selected.exclude.clear();render('include');render('exclude');populate();};\ntoggle.onchange=()=>{document.getElementById('except-fields').hidden=!toggle.checked;if(!toggle.checked){selected.exclude.clear();render('exclude');populate();}};\npopulate();\n"

CRUD_SCRIPT="\nconst rules=JSON.parse(document.getElementById('rules-data').textContent), ruleForm=document.getElementById('rule-form');\nfunction editRule(id){\n const r=rules.find(x=>x.id===id);if(!r)return;\n ruleForm.elements.rule_id.value=r.id;account.value=r.account_id===null?'*':String(r.account_id);\n selected.include=new Set(r.mailbox_names|| (r.mailbox_id!==null?[r.mailbox_name]:[]));\n selected.exclude=new Set(r.excluded_mailboxes||[]);toggle.checked=selected.exclude.size>0;\n document.getElementById('except-fields').hidden=!toggle.checked;\n render('include');render('exclude');populate();\n for(const [field,key] of Object.entries({months:'older_than_months',receipt_days:'minimum_receipt_age_days',verifications:'minimum_verifications',gap_hours:'verification_gap_hours',batch:'batch_size',schedule_hours:'schedule_hours'}))ruleForm.elements[field].value=r[key];\n document.getElementById('form-title').textContent='Editar regra';document.getElementById('save-rule').textContent='Salvar e pausar regra';document.getElementById('cancel-edit').hidden=false;ruleForm.scrollIntoView({behavior:'smooth'});\n}\ndocument.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>editRule(b.dataset.edit));\ndocument.getElementById('cancel-edit').onclick=()=>{\n ruleForm.reset();ruleForm.elements.rule_id.value='';selected.include.clear();selected.exclude.clear();render('include');render('exclude');populate();document.getElementById('except-fields').hidden=true;document.getElementById('form-title').textContent='Nova regra';document.getElementById('save-rule').textContent='Criar regra pausada';document.getElementById('cancel-edit').hidden=true;\n};\n"

CSS+='\n:root{color-scheme:dark;--surface:#151c2b;--border:#2a3547;--muted:#a2b0c5;--accent:#71e2c3}body{background:#0c1220;color:#eaf0f9;font:14px/1.55 system-ui,-apple-system,Segoe UI,sans-serif}main{max-width:1380px;padding:32px 40px;margin:auto}header{align-items:center;padding:0 0 26px;margin-bottom:24px;border-bottom:1px solid var(--border)}h1{font-size:28px;letter-spacing:-.7px;margin:0 0 6px}h2{font-size:19px;letter-spacing:-.3px;margin:0 0 16px}p{margin:7px 0 14px}.muted,small{color:var(--muted)}.card{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:24px;margin:20px 0;box-shadow:0 8px 25px #0001}.grid{gap:18px;grid-template-columns:repeat(auto-fit,minmax(225px,1fr));align-items:start}.grid>.card{margin:0;background:#0e1727;padding:18px;border-color:#253447}.grid>.card strong{font-size:32px;letter-spacing:-1px;color:var(--accent);font-variant-numeric:tabular-nums}.grid>.card p{font-size:12px;margin:4px 0 0;color:var(--muted)}label{font-size:12px;letter-spacing:.15px;gap:8px}input,select{font:14px system-ui;padding:12px 13px;background:#0d1524;border-color:#364258;border-radius:10px;height:44px}input:focus,select:focus{outline:2px solid #71e2c344;border-color:var(--accent)}button{font:600 13px system-ui;padding:11px 16px;background:#5bd3b0;color:#06271e;border-radius:9px;white-space:nowrap}button:hover{filter:brightness(1.08)}button.secondary{background:#243247;color:#dde7f4;border:1px solid #37465c}button:disabled{opacity:.4;cursor:not-allowed}.notice{border:1px solid #31745e;background:#163d33;color:#b9f7dc;padding:14px 18px;margin:18px 0}.notice.bad{background:#402630;border-color:#774154;color:#ffc5d1}.rules{gap:14px}.rule{display:flex;flex-wrap:wrap;justify-content:flex-start;align-items:center;gap:9px;border-color:var(--border);background:#101827;padding:20px;border-radius:13px}.rule>div:first-child{flex:1 0 100%;padding-bottom:6px}.rule strong{font-size:15px}.rule .muted{font-size:12px;line-height:1.8;max-width:1050px;margin:8px 0}.rule form{margin:0}.rule form[action="/delete-rule"]{margin-left:auto}.rule form[action="/delete-rule"] button{color:#ffb0bd;background:#3a2330;border-color:#623748}.badge{font-size:11px;letter-spacing:.3px;background:#3c331e;color:#f4dba2;padding:5px 10px}.tags{margin-top:8px}.tag{font-size:12px;background:#1e3c3c;border:1px solid #315d56;color:#b5f5e3}.tag button{color:inherit}.except{min-height:44px;padding-top:18px}small{font-size:11px;line-height:1.7;margin-top:10px}table{width:100%;border-collapse:collapse;font-size:12px;margin-top:18px;text-align:left}th{color:#a9b9ce;font-size:11px;font-weight:650;text-transform:uppercase;letter-spacing:.6px;background:#111a29;white-space:nowrap;border-bottom:1px solid #384459;padding:12px}td{padding:13px 12px;border-bottom:1px solid #263145;vertical-align:top;max-width:320px;overflow-wrap:anywhere}tr:last-child td{border-bottom:0}tbody tr:hover{background:#ffffff03}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}#execution-report>.card:first-child>.grid{grid-template-columns:repeat(5,minmax(0,1fr));margin:20px 0 24px}#execution-report div[style="overflow:auto"]{max-height:340px;border:1px solid #273449;border-radius:10px;margin-top:16px}#execution-report .card>form{display:inline-block;margin-right:10px}#execution-report .card>p:has(button){display:inline-block;margin:0}.center{background:radial-gradient(ellipse at top,#1c3142,#0c1220 65%)}.login{width:min(420px,calc(100% - 40px));border-color:#324358;background:#151f30;padding:32px}.login label{margin-top:16px}.login button{width:100%;margin-top:22px}.login .mark{background:#174c42;color:#84f0cd}.navlinks{display:flex;gap:20px;margin:8px 0 24px;font-size:12px}.navlinks a{color:var(--muted)}@media(max-width:1000px){#execution-report>.card:first-child>.grid{grid-template-columns:repeat(3,minmax(0,1fr))}}@media(max-width:650px){main{padding:22px 16px}.card{padding:18px}.rule form[action="/delete-rule"]{margin-left:0}#execution-report>.card:first-child>.grid{grid-template-columns:repeat(2,minmax(0,1fr))}.grid{grid-template-columns:1fr}header{display:flex}.rule strong{overflow-wrap:anywhere}}\n'

def esc(v): return html.escape(str(v),quote=True)
def api(path,token,method="GET",payload=None):
 data=json.dumps(payload).encode() if payload is not None else None
 req=urllib.request.Request(BICHON_URL+path,data=data,method=method,headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
 with urllib.request.urlopen(req,timeout=15) as res: return json.load(res) if res.length!=0 else None
def authenticate(username,password):
 req=urllib.request.Request(BICHON_URL+"/api/login",data=json.dumps({"username":username,"password":password}).encode(),headers={"Content-Type":"application/json"},method="POST")
 with urllib.request.urlopen(req,timeout=10) as res: result=json.load(res)
 token=result.get("access_token") if result.get("success") else None
 if not token: raise ValueError("Usuário ou senha inválidos.")
 user=api("/api/v1/current-user",token)
 if "system:root" not in user.get("global_permissions",[]): raise PermissionError("Somente administradores do Bichon podem acessar este painel.")
 return token,str(user.get("username",username))
def load_rules():
 if not RULES.exists(): return []
 data=json.loads(RULES.read_text(encoding="utf-8")); return data if isinstance(data,list) else []
def write_rules(rules):
 ROOT.mkdir(parents=True,exist_ok=True); tmp=RULES.with_suffix(".tmp")
 tmp.write_text(json.dumps(rules,ensure_ascii=False,indent=2),encoding="utf-8"); os.chmod(tmp,0o600); os.replace(tmp,RULES)
def integer(form,name,low,high):
 value=int(form.get(name,[""])[0])
 if not low<=value<=high: raise ValueError(f"{name} deve estar entre {low} e {high}")
 return value
def account_data(token):
 accounts=api("/api/v1/minimal-account-list",token) or []; mailboxes={}; errors=[]
 for account in accounts:
  aid=int(account["id"])
  try: mailboxes[aid]=(api(f"/api/v1/list-mailboxes/{aid}?remote=false",token) or {}).get("mailboxes",[])
  except Exception: mailboxes[aid]=[]; errors.append(str(account.get("email") or aid))
 return accounts,mailboxes,("Não foi possível listar pastas de: "+", ".join(errors) if errors else "")
def save_rule(form,accounts,mailboxes):
 aid=None if form.get("account_id")==["*"] else integer(form,"account_id",1,2**63-1)
 mid=None
 account=next((x for x in accounts if int(x["id"])==aid),None)
 if aid is not None and not account: raise ValueError("Conta não pertence ao Bichon.")
 available={m["name"] for group in (mailboxes.values() if aid is None else [mailboxes.get(aid,[])]) for m in group}
 included=sorted(set(form.get("mailbox_names",[])))
 excluded=sorted(set(form.get("excluded_mailboxes",[]))) if form.get("use_exclusions")==["on"] else []
 if not set(included+excluded)<=available: raise ValueError("Pasta não pertence à conta selecionada.")
 if set(included)&set(excluded): raise ValueError("Uma pasta não pode estar selecionada e excluída ao mesmo tempo.")
 mailbox=None
 rule={"id":secrets.token_hex(8),"account_id":aid,"account_label":(account.get("name") or account.get("email") or str(aid)) if account else "Todas as contas","mailbox_id":mid,"mailbox_name":", ".join(included) if included else "Todas as pastas","mailbox_names":included,"excluded_mailboxes":excluded,"older_than_months":integer(form,"months",1,240),"minimum_receipt_age_days":integer(form,"receipt_days",1,365),"minimum_verifications":integer(form,"verifications",2,10),"verification_gap_hours":integer(form,"gap_hours",1,720),"batch_size":integer(form,"batch",1,500),"schedule_hours":integer(form,"schedule_hours",1,168),"enabled":False,"dry_run":True,"created_at":int(time.time())}
 with RULE_LOCK:
  rules=load_rules();rid=form.get("rule_id",[""])[0]
  existing=next((r for r in rules if r["id"]==rid),None) if rid else None
  if rid and not existing: raise ValueError("Regra não encontrada.")
  target=(aid,mid,tuple(included))
  if any(r["id"]!=rid and (r.get("account_id"),r.get("mailbox_id"),tuple(r.get("mailbox_names",[])))==target for r in rules): raise ValueError("Já existe uma regra para esse alvo. Use Editar.")
  if existing:
   rule["id"]=existing["id"];rule["created_at"]=existing.get("created_at",rule["created_at"])
   rule["revision"]=existing.get("revision",0)+1
  rule["updated_at"]=int(time.time())
  rules=[r for r in rules if r["id"]!=rid];rules.append(rule);write_rules(rules)
def toggle_rule(form):
 rid=form.get("rule_id",[""])[0]
 with RULE_LOCK:
  rules=load_rules(); rule=next((x for x in rules if x.get("id")==rid),None)
  if not rule: raise ValueError("Regra não encontrada.")
  rule["enabled"]=not bool(rule.get("enabled")); rule["dry_run"]=not deletion_enabled(); write_rules(rules)
def delete_rule(form):
 rid=form.get("rule_id",[""])[0]
 with RULE_LOCK:
  rules=load_rules()
  if not any(r["id"]==rid for r in rules): raise ValueError("Regra não encontrada.")
  write_rules([r for r in rules if r["id"]!=rid])

def login_page(message=""):
 notice=f'<div class="notice bad">{esc(message)}</div>' if message else ""
 return f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Entrar · Bichon Cleaner</title><style>{CSS}</style></head><body class="center"><form class="login" method="post" action="/login"><div class="mark">✉</div><h1>Bichon Cleaner</h1><p class="muted">Use seu usuário administrador do Bichon.</p>{notice}<input type="hidden" name="csrf" value="{CSRF}"><label>Usuário<input name="username" autocomplete="username" autofocus></label><label>Senha<input type="password" name="password" autocomplete="current-password"></label><button>Entrar</button><small>A senha é enviada ao Bichon e não é armazenada neste painel.</small></form></body></html>'''.encode()
def deletion_history():
 total=0;recent=deque(maxlen=100);path=STATE/"audit.jsonl"
 if path.exists():
  with path.open() as f:
   for line in f:
    try:event=json.loads(line)
    except ValueError:continue
    if event.get("action")=="remote_deleted":total+=1;recent.append(event)
 return total,list(reversed(recent))

def deletion_csv():
 out=io.StringIO();writer=csv.writer(out);writer.writerow(["Excluído em","Conta","Pasta","Assunto","Recebido em","UID"])
 path=STATE/"audit.jsonl"
 if path.exists():
  with path.open() as f:
   for line in f:
    try:r=json.loads(line)
    except ValueError:continue
    if r.get("action")!="remote_deleted":continue
    cells=[stamp(r.get("at",0)),r.get("email",""),r.get("folder",r.get("mailbox_id","")),r.get("subject",""),r.get("received_at",""),r.get("uid","")]
    writer.writerow([("'"+str(v)) if str(v).startswith(("=","+","-","@")) else v for v in cells])
 return ("\ufeff"+out.getvalue()).encode("utf-8")

def stamp(value):return time.strftime("%d/%m/%Y %H:%M",time.gmtime(value-10800)) if value else "—"

def request_run(form,username):
 rid=form.get("rule_id",[""])[0]
 with RULE_LOCK:
  rules=load_rules();active=[r for r in rules if r.get("enabled") and (not rid or r["id"]==rid)]
  if not active:raise ValueError("Ative uma regra em simulação antes de executar.")
  STATE.mkdir(parents=True,exist_ok=True);path=STATE/"run-request.json"
  if path.exists():return False
  tmp=STATE/"run-request.tmp";tmp.write_text(json.dumps({"rule_id":rid or None,"requested_at":int(time.time()),"requested_by":username}));os.chmod(tmp,0o600);os.replace(tmp,path)
  return True

def deletion_enabled():
 path=STATE/"execution-control.json"
 return json.loads(path.read_text()).get("allow_remote_delete") is True if path.exists() else False

def set_deletion_mode(form,username):
 enabled=form.get("mode")==["enable"];token=None
 if form.get("mode") not in (["enable"],["disable"]):raise ValueError("Modo inválido.")
 if enabled:
  if form.get("confirm_delete")!=["on"]:raise ValueError("Confirme a exclusão definitiva no servidor de e-mail.")
  password=form.get("password",[""])[0]
  if not password:raise ValueError("Digite sua senha de administrador do Bichon.")
  token,user=authenticate(username,password)
  if user!=username:raise PermissionError("Confirme com o administrador da sessão atual.")
 with RULE_LOCK:
  rules=load_rules()
  for rule in rules:
   if not enabled or rule.get("enabled"):rule["dry_run"]=not enabled
  write_rules(rules);STATE.mkdir(parents=True,exist_ok=True)
  path=STATE/"execution-control.json";tmp=path.with_suffix(".tmp")
  tmp.write_text(json.dumps({"allow_remote_delete":enabled,"confirmed_by":username,"confirmed_at":int(time.time())}));os.chmod(tmp,0o600);os.replace(tmp,path)
 return token

def mode_panel():
 if deletion_enabled():
  return f'<section class="card"><h2>Exclusão no servidor liberada</h2><p>As regras ativas podem excluir mensagens do servidor IMAP após as verificações. O arquivo do Bichon permanece preservado.</p><form method="post" action="/deletion-mode"><input type="hidden" name="csrf" value="{CSRF}"><input type="hidden" name="mode" value="disable"><button class="secondary">Desligar exclusão · voltar à simulação</button></form></section>'
 return f'<section class="card"><h2>Modo de execução <span class="badge">Simulação</span></h2><p>As execuções verificam as mensagens. Nenhum e-mail é apagado.</p><details><summary style="cursor:pointer;color:var(--accent);font-weight:650">Ligar exclusão real</summary><form method="post" action="/deletion-mode" style="max-width:620px;margin-top:18px"><input type="hidden" name="csrf" value="{CSRF}"><input type="hidden" name="mode" value="enable"><p>Ao liberar, as próximas execuções das regras ativas poderão apagar definitivamente as mensagens do servidor de e-mail. As cópias no Bichon serão mantidas.</p><label>Senha do administrador conectado<input type="password" name="password" required autocomplete="current-password"></label><label class="except"><input type="checkbox" name="confirm_delete" required> Confirmo a exclusão definitiva no servidor conforme as regras ativas.</label><button style="margin-top:16px">Confirmar senha e ligar exclusão</button></form></details></section>'

def report_panel():
 path=STATE/"latest-report.json";status_path=STATE/"execution-status.json"
 report=json.loads(path.read_text()) if path.exists() else {};status=json.loads(status_path.read_text()) if status_path.exists() else {}
 total,recent=deletion_history();rows=[];deleted_rows=[];totals={k:sum(r.get(k,0) for r in report.get("mailboxes",[])) for k in ("old_messages","checked","verified","ready","blocked","deleted")}
 for r in report.get("mailboxes",[]):
  issues=r.get("error") or r.get("interrupted") or "; ".join(x.get("reason","") for x in r.get("examples",[])[:2])
  rows.append(f'<tr><td>{esc(r["email"])}</td><td>{esc(r["folder"])}</td><td>{r["old_messages"]}</td><td>{r["checked"]}</td><td>{r["verified"]}</td><td>{r["ready"]}</td><td>{r["deleted"]}</td><td>{esc(issues)}</td></tr>')
 for r in recent:
  deleted_rows.append(f'<tr><td>{stamp(r.get("at",0))}</td><td>{esc(r.get("email",""))}</td><td>{esc(r.get("folder",r.get("mailbox_id","")))}</td><td>{esc(r.get("subject") or "Assunto não registrado")}</td><td>{esc(r.get("received_at",""))}</td><td>{esc(r.get("uid",""))}</td></tr>')
 state="Na fila" if (STATE/"run-request.json").exists() else {"running":"Executando","finished":"Concluída","failed":"Falhou"}.get(status.get("state"),"Aguardando execução")
 mode="Simulação · nenhum e-mail é apagado" if report.get("mode","simulation")=="simulation" else "Execução real no servidor IMAP"
 counts=''.join(f'<div class="card"><strong>{value}</strong><p>{label}</p></div>' for label,value in [("Excluídos no histórico",total),("Excluídos nesta execução",totals["deleted"]),("Conferidos nesta execução",totals["checked"]),("Elegíveis após verificações",totals["ready"]),("Bloqueados nesta execução",totals["blocked"])])
 empty='<p>Nenhuma exclusão confirmada. Mensagens apenas simuladas não entram nesta lista.</p>' if not recent else ""
 error=f'<p class="notice bad">{esc(status["error"])}</p>' if status.get("error") else ""
 return f'<section class="card"><h2>Execução e contadores</h2><p>{state} · {mode}</p><p>Última atualização: {stamp(report.get("updated_at") or report.get("finished_at") or report.get("started_at"))} (Brasília)</p>{error}<div class="grid">{counts}</div><form method="post" action="/run-now"><input type="hidden" name="csrf" value="{CSRF}"><button>Executar agora · regras ativas</button></form><p><button type="button" class="secondary" onclick="refreshReport()">Atualizar contadores</button></p><div style="overflow:auto"><table cellpadding="8"><tr><th>Conta</th><th>Pasta</th><th>Antigas</th><th>Conferidas</th><th>Verificadas</th><th>Elegíveis</th><th>Excluídas</th><th>Impedimentos</th></tr>{"".join(rows)}</table></div><small>Executar agora ignora o agendamento, mas mantém os intervalos de segurança e o modo de simulação.</small></section><section class="card" id="history-section"><h2>E-mails excluídos do servidor</h2><p>Últimas 100 exclusões confirmadas. <a href="/deleted.csv">Baixar histórico completo (CSV)</a></p>{empty}<div style="overflow:auto"><table cellpadding="8"><tr><th>Excluído em (Brasília)</th><th>Conta</th><th>Pasta</th><th>Assunto</th><th>Recebido em</th><th>UID</th></tr>{"".join(deleted_rows)}</table></div></section>'

REPORT_SCRIPT="""
async function refreshReport(){
 try{const r=await fetch('/report',{cache:'no-store'});if(r.status===401){location.href='/login';return;}if(!r.ok)return;document.getElementById('execution-report').innerHTML=await r.text();}catch(e){}
}
setInterval(()=>{if(!document.hidden)refreshReport();},10000);
"""

def page(token,username,message="",error=False):
 accounts,mailboxes,warning=account_data(token); options=['<option value="*">Todas as contas</option>']
 for account in accounts:
  aid=int(account["id"]); label=account.get("email") or account.get("name") or aid
  options.append(f'<option value="{aid}">{esc(label)}</option>')
 folder_data={str(aid):sorted({m["name"] for m in group}) for aid,group in mailboxes.items()}
 folder_data["*"]=sorted({m["name"] for group in mailboxes.values() for m in group})
 folder_json=json.dumps(folder_data,ensure_ascii=False).replace("<",chr(92)+"u003c")
 notice_text=message or warning; notice=f'<div class="notice {"bad" if error or warning else ""}">{esc(notice_text)}</div>' if notice_text else ""; cards=[]
 rules=load_rules();rules_json=json.dumps(rules,ensure_ascii=False).replace("<",chr(92)+"u003c")
 for r in rules:
  state=("exclusão agendada" if deletion_enabled() and not r.get("dry_run",True) else "simulação agendada") if r.get("enabled") else "pausada"
  cards.append(f'''<article class="rule"><div><strong>{esc(r["account_label"])} · {esc(r["mailbox_name"])}</strong><p class="muted">Recebidos há mais de {r["older_than_months"]} meses · Exceto: {esc(", ".join(r.get("excluded_mailboxes",[])) or "nenhuma")} · arquivo há {r["minimum_receipt_age_days"]} dia(s) · {r["minimum_verifications"]} verificações · lote {r["batch_size"]} · a cada {r["schedule_hours"]}h</p><span class="badge">{state}</span></div><form method="post" action="/toggle"><input type="hidden" name="csrf" value="{CSRF}"><input type="hidden" name="rule_id" value="{esc(r["id"])}"><button class="secondary">{"Pausar" if r.get("enabled") else ("Ativar exclusão" if deletion_enabled() else "Ativar simulação")}</button></form><form method="post" action="/run-now"><input type="hidden" name="csrf" value="{CSRF}"><input type="hidden" name="rule_id" value="{esc(r['id'])}"><button class="secondary" {"disabled" if not r.get("enabled") else ""}>Executar agora</button></form><button type="button" class="secondary" data-edit="{esc(r['id'])}">Editar</button><form method="post" action="/delete-rule" onsubmit="return confirm('Excluir esta regra? Nenhum e-mail será apagado.');"><input type="hidden" name="csrf" value="{CSRF}"><input type="hidden" name="rule_id" value="{esc(r['id'])}"><button class="secondary">Excluir regra</button></form></article>''')
 disabled="" if options else " disabled"; empty='<p class="muted">Nenhuma regra criada.</p>' if not cards else ""
 return f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Bichon Cleaner</title><style>{CSS}</style></head><body><main><header><div><h1>Bichon Cleaner</h1><p class="muted">Retenção das caixas de e-mail · Arquivo preservado no Bichon</p></div><form method="post" action="/logout"><input type="hidden" name="csrf" value="{CSRF}"><button class="secondary">Sair</button></form></header><nav class="navlinks"><a href="#execution-report">Execução</a><a href="#rules-section">Regras</a><a href="#rule-form">Nova regra</a><a href="#history-section">Histórico</a></nav>{notice}{mode_panel()}<div id="execution-report">{report_panel()}</div><section class="card" id="rules-section"><h2>Regras de retenção</h2><div class="rules">{''.join(cards)}{empty}</div></section><form id="rule-form" method="post" action="/rules"><input type="hidden" name="rule_id" value=""><input type="hidden" name="csrf" value="{CSRF}"><section class="card"><h2 id="form-title">Nova regra</h2><p class="safe">O worker usa as credenciais já cadastradas, sem exibi-las. O arquivo no Bichon é preservado.</p><div class="grid"><label>Conta<select name="account_id" id="account"{disabled}>{''.join(options)}</select></label><div><label>Pastas<select id="include-picker"><option value="">Adicionar pasta…</option></select></label><div id="include-tags" class="tags"></div><small>Sem seleção: todas as pastas da conta.</small></div><div><label class="except"><input type="checkbox" name="use_exclusions" id="except-toggle"> Exceto</label><div id="except-fields" hidden><label>Pastas protegidas<select id="exclude-picker"><option value="">Adicionar pasta…</option></select></label><div id="exclude-tags" class="tags"></div><small>Estas pastas não serão processadas.</small></div></div><label>Mais antigos que (meses)<input type="number" name="months" min="1" max="240" value="12"></label><label>Recibo arquivado há (dias)<input type="number" name="receipt_days" min="1" value="14"></label><label>Verificações mínimas<input type="number" name="verifications" min="2" value="2"></label><label>Intervalo entre verificações (h)<input type="number" name="gap_hours" min="1" value="24"></label><label>Mensagens por lote<input type="number" name="batch" min="1" max="500" value="50"></label><label>Executar a cada (h)<input type="number" name="schedule_hours" min="1" max="168" value="24"></label></div><p><button id="save-rule"{disabled}>Criar regra pausada</button> <button type="button" id="cancel-edit" class="secondary" hidden>Cancelar edição</button></p><small>Regras novas ou editadas ficam pausadas e em simulação. Ative após conferir os parâmetros. A exclusão depende do modo liberado pelo administrador com senha. Uma regra por pasta prevalece sobre a regra da conta, que prevalece sobre a global. Uma exceção pausada protege essa caixa/pasta.</small></section></form></main><script type="application/json" id="folder-data">{folder_json}</script><script type="application/json" id="rules-data">{rules_json}</script><script>{TAG_SCRIPT}{CRUD_SCRIPT}{REPORT_SCRIPT}</script></body></html>'''.encode()
class Handler(BaseHTTPRequestHandler):
 def sid(self): return next((p.strip()[8:] for p in self.headers.get("Cookie","").split(";") if p.strip().startswith("session=")),"")
 def session(self):
  sid=self.sid(); r=SESSIONS.get(sid)
  if not r or r[0]<=time.time(): SESSIONS.pop(sid,None); return None
  SESSIONS[sid]=(time.time()+SESSION_TTL,r[1],r[2]); return r[1],r[2]
 def respond(self,content,status=200,content_type="text/html; charset=utf-8"):
  if isinstance(content,str):content=content.encode('utf-8')
  self.send_response(status); self.send_header("Content-Type",content_type); self.send_header("Content-Length",str(len(content))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(content)
 def redirect(self,path,cookie=None):
  self.send_response(303); self.send_header("Location",path)
  if cookie:self.send_header("Set-Cookie",cookie)
  self.end_headers()
 def do_GET(self):
  s=self.session()
  if not s:self.respond(login_page(),401 if self.path in ("/report","/deleted.csv") else 200);return
  try:
   if self.path in ("/report","/deleted.csv"):
    if "system:root" not in api("/api/v1/current-user",s[0]).get("global_permissions",[]):raise PermissionError("Acesso administrativo necessário.")
    if self.path=="/report":self.respond(report_panel())
    else:self.respond(deletion_csv(),content_type="text/csv; charset=utf-8")
   else:self.respond(page(*s))
  except (BrokenPipeError,ConnectionResetError):return
  except Exception:self.respond(login_page("Não foi possível carregar o painel. Entre novamente ou tente atualizar."),401)
 def do_POST(self):
  size=int(self.headers.get("Content-Length","0")); form=parse_qs(self.rfile.read(size).decode(),keep_blank_values=True)
  if size>65536:self.respond(b"Requisicao muito grande",413);return
  if not secrets.compare_digest(form.get("csrf",[""])[0],CSRF):self.respond(login_page("Sessão expirada. Recarregue a página."),403);return
  if self.path=="/login":
   try:token,user=authenticate(form.get("username",[""])[0],form.get("password",[""])[0])
   except (ValueError,PermissionError) as e:time.sleep(.4);self.respond(login_page(str(e)),401);return
   except Exception:self.respond(login_page("Não foi possível autenticar no Bichon."),502);return
   sid=secrets.token_urlsafe(32);SESSIONS[sid]=(time.time()+SESSION_TTL,token,user);self.redirect("/",f"session={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL}");return
  s=self.session()
  if not s:self.redirect("/login");return
  if self.path=="/logout":SESSIONS.pop(self.sid(),None);self.redirect("/login","session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0");return
  try:
   if "system:root" not in api("/api/v1/current-user",s[0]).get("global_permissions",[]):raise PermissionError("Acesso administrativo necessário.")
   if self.path=="/deletion-mode":
    token=set_deletion_mode(form,s[1])
    if token:SESSIONS[self.sid()]=(time.time()+SESSION_TTL,token,s[1]);s=(token,s[1])
    msg="Exclusão liberada com confirmação de senha. Nenhuma execução foi iniciada por este botão." if deletion_enabled() else "Exclusão desativada. As execuções voltam à simulação."
   elif self.path=="/run-now":msg="Execução solicitada. Acompanhe os contadores abaixo." if request_run(form,s[1]) else "Sua solicitação já está na fila. Não é necessário clicar novamente."
   elif self.path=="/rules":
    accounts,mailboxes,_=account_data(s[0]);save_rule(form,accounts,mailboxes);msg="Regra atualizada e pausada." if form.get("rule_id",[""])[0] else "Regra criada pausada e em modo de simulação."
   elif self.path=="/delete-rule":delete_rule(form);msg="Regra excluída. Nenhum e-mail foi apagado."
   elif self.path=="/toggle":toggle_rule(form);msg="Estado da simulação atualizado."
   else:raise ValueError("Ação desconhecida.")
   self.respond(page(*s,msg))
  except Exception as e:self.respond(page(*s,str(e),True),400)
 def log_message(self,fmt,*args):print(fmt%args,flush=True)
if __name__=="__main__":ThreadingHTTPServer(("0.0.0.0",int(os.environ.get("PORT","15632"))),Handler).serve_forever()
