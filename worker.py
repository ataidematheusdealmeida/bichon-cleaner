#!/usr/bin/env python3
"""Remote IMAP retention. No Bichon delete endpoint is ever called."""
import argparse, base64, calendar, datetime as dt, fcntl, hashlib, imaplib
import json, os, re, ssl, time, urllib.request
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from blake3 import blake3

ROOT = Path(os.environ.get('CLEANER_ROOT', '/opt/bichon-cleaner'))
DATA = Path(os.environ.get('BICHON_DATA', '/opt/bichon/data'))
URL = os.environ.get('BICHON_URL', 'http://127.0.0.1:15630').rstrip('/')

def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush(); os.fsync(f.fileno())
    os.chmod(tmp, 0o600)
    if os.geteuid() == 0: os.chown(tmp, 10001, 10001)
    os.replace(tmp, path)

def database():
    snap = json.loads((DATA / 'memdb/snapshot.json').read_text())
    data = snap['data']
    with (DATA / 'memdb/wal.jsonl').open() as f:
        for line in f:
            w = json.loads(line)  # Fail closed on truncated records.
            if w['seq'] <= snap['last_seq']: continue
            for op in w['ops']:
                col = op['collection']
                if col not in ('accounts', 'mailboxes'): continue
                if op['op'] == 'Upsert': data[col][op['key']] = op['value']
                elif op['op'] == 'Delete': data[col].pop(op['key'], None)
    return data

def cutoff(months, now=None):
    if not isinstance(months, int) or not 1 <= months <= 240:
        raise ValueError('Retenção deve estar entre 1 e 240 meses')
    now = now or dt.datetime.now(dt.timezone.utc)
    year, month0 = divmod(now.year * 12 + now.month - 1 - months, 12)
    return now.replace(year=year, month=month0 + 1,
                       day=min(now.day, calendar.monthrange(year, month0 + 1)[1]))

def policy(rules, aid, mid, name=None):
    scoped = [r for r in rules if r.get('account_id') in (None, aid)]
    if name is not None and any(r.get('enabled') and name in r.get('excluded_mailboxes', []) for r in scoped):
        return None
    matches = [r for r in rules if r.get('account_id') in (None, aid)
               and r.get('mailbox_id') in (None, mid)
               and (not r.get('mailbox_names') or name in r['mailbox_names'])]
    if not matches: return None
    rank = lambda r: (r.get('mailbox_id') is not None or bool(r.get('mailbox_names')), r.get('account_id') is not None)
    best = max(rank(r) for r in matches)
    selected = [r for r in matches if rank(r) == best]
    if len(selected) != 1: raise ValueError('Regras conflitantes para a mesma caixa/pasta')
    return selected[0]  # A paused specific rule overrides an enabled global rule.

def api(path, token=None, payload=None, binary=False):
    headers = {'Content-Type': 'application/json'}
    if token: headers['Authorization'] = 'Bearer ' + token
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(URL + path, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as res:
        if binary:
            size = 0
            while chunk := res.read(1024 * 1024): size += len(chunk)
            if not size: raise ValueError('Arquivo Bichon vazio')
            return size
        return json.load(res)

def login():
    token_file = ROOT / 'secrets/api-token'
    if token_file.exists():
        token = token_file.read_text().strip()
        api('/api/v1/current-user', token)
        return token
    secret = (ROOT / 'secrets/admin-password').read_text().strip()
    result = api('/api/login', payload={'username': 'admin', 'password': secret})
    if not result.get('success') or not result.get('access_token'):
        raise ValueError('Falha na autenticação do Bichon')
    return result['access_token']

def connect(account):
    cfg = account['imap']
    if cfg.get('use_proxy') or cfg['auth']['auth_type'] != 'Password':
        raise ValueError('Proxy/OAuth não suportado; nenhuma exclusão')
    encrypted = base64.urlsafe_b64decode(cfg['auth']['password'])
    secret = Path('/opt/bichon/secrets/encrypt-password').read_text().strip().encode()
    key = hashlib.pbkdf2_hmac('sha256', secret, encrypted[:32], 100000, 32)
    password = AESGCM(key).decrypt(encrypted[32:44], encrypted[44:], None).decode()
    if cfg['encryption'] == 'Ssl':
        client = imaplib.IMAP4_SSL(cfg['host'], cfg['port'], ssl_context=ssl.create_default_context(), timeout=60)
    elif cfg['encryption'] == 'StartTls':
        client = imaplib.IMAP4(cfg['host'], cfg['port'], timeout=60)
        client.starttls(ssl.create_default_context())
    else: raise ValueError('IMAP sem TLS recusado')
    try:
        client.login(account.get('login_name') or account['email'], password)
        # Servers may advertise UIDPLUS only after authentication.
        client.capabilities = tuple(b' '.join(check(client.capability())).upper().split())
        return client
    except Exception:
        try: client.logout()
        except Exception: pass
        raise ValueError('Falha de autenticação/conexão IMAP') from None

def check(status_data):
    status, data = status_data
    if status != 'OK': raise ValueError('Comando IMAP recusado')
    return data

def validity(client):
    data = client.response('UIDVALIDITY')[1]
    if not data or not data[0]: raise ValueError('UIDVALIDITY ausente')
    return int(data[0])

def metadata(client, uid):
    data = check(client.uid('FETCH', str(uid), '(UID INTERNALDATE RFC822.SIZE FLAGS)'))
    raw = b' '.join(x for x in data if isinstance(x, bytes))
    date = re.search(rb'INTERNALDATE "([^"]+)"', raw)
    size = re.search(rb'RFC822.SIZE (\d+)', raw)
    identity = re.search(rb'UID (\d+)', raw)
    if not date or not size or not identity or int(identity[1]) != uid:
        raise ValueError('Metadados IMAP ausentes ou UID diferente')
    when = dt.datetime.strptime(date[1].decode(), '%d-%b-%Y %H:%M:%S %z')
    return when, int(size[1]), b'\\Deleted' in raw

def verify(client, token, account, mailbox, uid, rule, limit, uid_validity):
    aid, mid = account['id'], mailbox['id']
    when, size, deleted = metadata(client, uid)
    if when >= limit or deleted: raise ValueError('Mensagem recente ou já marcada para exclusão')
    result = api(f'/api/v1/archive-receipt/{aid}/{mid}/{uid}/verify', token)
    r = result['receipt']
    if not all(result.get(k) is True for k in ('verified', 'envelope_indexed', 'email_blob_present', 'attachment_blobs_present')):
        raise ValueError('Cópia arquivada não confirmada')
    if (r['account_id'], r['mailbox_id'], r['uid'], r['uid_validity'], r['mailbox_name']) != (aid, mid, uid, uid_validity, mailbox['name']):
        raise ValueError('Recibo não corresponde à identidade IMAP atual')
    if r['original_size'] != size: raise ValueError('Tamanho diferente do recibo')
    age = max(1, int(rule.get('minimum_receipt_age_days', 14)))
    if time.time() * 1000 - r['ingested_at'] < age * 86400000:
        raise ValueError('Arquivo ainda não atingiu o período de segurança')
    if size > 100 * 1024 * 1024: raise ValueError('Mensagem excede limite de verificação')
    parts = check(client.uid('FETCH', str(uid), '(UID BODY.PEEK[])'))
    bodies = [p[1] for p in parts if isinstance(p, tuple)]
    if len(bodies) != 1 or len(bodies[0]) != size or blake3(bodies[0]).hexdigest() != r['content_hash']:
        raise ValueError('Hash da mensagem remota diferente do arquivo')
    # Download reconstructs the archive and reads/decompresses email and attachment blobs.
    api(f'/api/v1/download-message/{aid}/{r["envelope_id"]}', token, binary=True)
    return r

def imap_name(name):
    # IMAP modified UTF-7 for folders containing accented characters.
    result = ''; pending = ''
    def encoded(value):
        return '&' + base64.b64encode(value.encode('utf-16be')).decode().rstrip('=').replace('/', ',') + '-'
    for char in name:
        if ' ' <= char <= '~':
            if pending: result += encoded(pending); pending = ''
            result += '&-' if char == '&' else char
        else: pending += char
    if pending: result += encoded(pending)
    return '"' + result.replace('\\', '\\\\').replace('"', '\\"') + '"'

def erase(client, uid, expected_validity, mailbox_name):
    if b'UIDPLUS' not in client.capabilities: raise ValueError('Servidor sem UIDPLUS; exclusão bloqueada')
    status = check(client.status(imap_name(mailbox_name), '(UIDVALIDITY)'))
    match = re.search(rb'UIDVALIDITY (\d+)', b' '.join(status))
    if not match or int(match[1]) != expected_validity: raise ValueError('UIDVALIDITY alterado')
    check(client.uid('STORE', str(uid), '+FLAGS.SILENT', '(\\Deleted)'))
    try: check(client.uid('EXPUNGE', str(uid)))
    except Exception:
        # Never use broad EXPUNGE or CLOSE: they could delete unrelated messages.
        try: client.uid('STORE', str(uid), '-FLAGS.SILENT', '(\\Deleted)')
        except Exception: pass
        raise

def deletion_enabled():
    path = ROOT / 'state/execution-control.json'
    if not path.exists(): path = ROOT / 'config/execution.json'
    return json.loads(path.read_text()).get('allow_remote_delete') is True

def ordered_candidates(uids, state, key, uv, rule, now=None):
    now = time.time() if now is None else now
    available = set(uids)
    due = set()
    prefix = f'{key}:{uv}:'
    gap = max(1, int(rule.get('verification_gap_hours', 24))) * 3600
    minimum = max(2, int(rule.get('minimum_verifications', 2)))
    for identity, record in state.get('verified', {}).items():
        if not identity.startswith(prefix): continue
        uid = int(identity[len(prefix):].split(':', 1)[0])
        if uid in available and (record['count'] >= minimum or now - record['last'] >= gap):
            due.add(uid)
    cursor = state.setdefault('cursors', {}).get(key, 0)
    rotated = [u for u in uids if u > cursor] + [u for u in uids if u <= cursor]
    return sorted(due) + [u for u in rotated if u not in due]

def run(force=False, apply=False):
    state_dir = ROOT / 'state'; state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / 'worker.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        request_path = state_dir / 'run-request.json'
        request = json.loads(request_path.read_text()) if request_path.exists() else {}
        processing_path = state_dir / 'processing-request.json'
        if processing_path.exists():
            os.replace(processing_path, state_dir / 'failed-request.json')
        if request:
            force = True
            os.replace(request_path, processing_path)
        only_rule = request.get('rule_id')
        rules = json.loads((ROOT / 'config/rules.json').read_text())
        apply = apply or deletion_enabled()
        if apply and not deletion_enabled():
            raise ValueError('Exclusão real não autorizada em execution.json')
        state_path = state_dir / 'worker-state.json'
        state = json.loads(state_path.read_text()) if state_path.exists() else {'runs': {}, 'verified': {}}
        report = {'started_at': int(time.time()), 'mode': 'apply' if apply else 'simulation', 'mailboxes': []}
        atomic(state_dir / 'execution-status.json', {'state': 'running', 'started_at': report['started_at'], 'mode': report['mode']})
        if force: atomic(state_dir / 'latest-report.json', report)
        data = database(); token = None
        for mailbox in sorted(data['mailboxes'].values(), key=lambda m: (m['account_id'], m['name'])):
            account = data['accounts'].get(str(mailbox['account_id']))
            if not account or not account.get('enabled'): continue
            aid, mid = account['id'], mailbox['id']
            try: rule = policy(rules, aid, mid, mailbox['name'])
            except ValueError as error:
                report['mailboxes'].append({'email': account['email'], 'folder': mailbox['name'],
                    'old_messages': 0, 'checked': 0, 'verified': 0, 'ready': 0,
                    'deleted': 0, 'blocked': 0, 'examples': [], 'error': str(error)})
                atomic(state_dir / 'latest-report.json', report)
                continue
            if not rule or not rule.get('enabled'): continue
            if only_rule and rule['id'] != only_rule: continue
            key = f'{rule["id"]}:{rule.get("revision", 0)}:{aid}:{mid}'
            if not force and time.time() - state['runs'].get(key, 0) < int(rule.get('schedule_hours', 24)) * 3600: continue
            row = {'email': account['email'], 'folder': mailbox['name'], 'rule_id': rule['id'], 'old_messages': 0, 'checked': 0, 'verified': 0, 'ready': 0, 'deleted': 0, 'blocked': 0, 'examples': []}
            report['mailboxes'].append(row); client = None
            atomic(state_dir / 'latest-report.json', report)
            try:
                limit = cutoff(rule['older_than_months']); client = connect(account)
                check(client.select(imap_name(mailbox['name']), readonly=not apply))
                uv = validity(client)
                uids = check(client.uid('SEARCH', None, 'BEFORE', limit.strftime('%d-%b-%Y')))[0].split()
                row['old_messages'] = len(uids)
                candidates = ordered_candidates([int(uid) for uid in uids], state, key, uv, rule)
                batch = int(rule.get('batch_size', 50))
                if not 1 <= batch <= 500: raise ValueError('Lote inválido')
                for uid in candidates[:batch]:
                    if apply and not deletion_enabled():
                        row['interrupted'] = 'Exclusão desativada pelo administrador'; break
                    current = policy(json.loads((ROOT / 'config/rules.json').read_text()), aid, mid, mailbox['name'])
                    if not current or not current.get('enabled') or (current['id'], current.get('revision', 0)) != (rule['id'], rule.get('revision', 0)):
                        row['interrupted'] = 'Regra pausada, editada ou excluída'; break
                    row['checked'] += 1
                    try:
                        token = token or login()
                        receipt = verify(client, token, account, mailbox, uid, rule, limit, uv)
                        vk = f'{key}:{uv}:{uid}:{receipt["content_hash"]}'
                        record = state['verified'].get(vk, {'count': 0, 'last': 0})
                        if time.time() - record['last'] >= max(1, int(rule.get('verification_gap_hours', 24))) * 3600:
                            record = {'count': record['count'] + 1, 'last': time.time()}
                            state['verified'][vk] = record
                        row['verified'] += 1
                        ready = record['count'] >= max(2, int(rule.get('minimum_verifications', 2)))
                        if ready: row['ready'] += 1
                        if apply and ready and rule.get('dry_run') is False:
                            if not deletion_enabled(): raise ValueError('Exclusão desativada pelo administrador')
                            current = policy(json.loads((ROOT / 'config/rules.json').read_text()), aid, mid, mailbox['name'])
                            if not current or not current.get('enabled') or current.get('dry_run') is not False or (current['id'], current.get('revision', 0)) != (rule['id'], rule.get('revision', 0)):
                                raise ValueError('Regra alterada antes da exclusão')
                            # Recheck immediately before deletion; only this UID can be expunged.
                            verify(client, token, account, mailbox, uid, rule, limit, uv)
                            envelope = api(f'/api/v1/envelope/{aid}/{receipt["envelope_id"]}', token)
                            received_at = metadata(client, uid)[0].isoformat()
                            with (state_dir / 'audit.jsonl').open('a') as audit:
                                os.chmod(state_dir / 'audit.jsonl', 0o600)
                                if os.geteuid() == 0: os.chown(state_dir / 'audit.jsonl', 10001, 10001)
                                event = {'at': int(time.time()), 'email': account['email'], 'folder': mailbox['name'], 'mailbox_id': mid, 'uid': uid, 'uid_validity': uv, 'hash': receipt['content_hash'], 'rule_id': rule['id'], 'subject': envelope.get('subject') or '(sem assunto)', 'received_at': received_at, 'action': 'delete_requested'}
                                audit.write(json.dumps(event) + '\n'); audit.flush(); os.fsync(audit.fileno())
                                if not deletion_enabled(): raise ValueError('Exclusão desativada antes do expurgo')
                                current = policy(json.loads((ROOT / 'config/rules.json').read_text()), aid, mid, mailbox['name'])
                                if not current or not current.get('enabled') or current.get('dry_run') is not False or (current['id'], current.get('revision', 0)) != (rule['id'], rule.get('revision', 0)):
                                    raise ValueError('Regra alterada durante a verificação final; exclusão cancelada')
                                erase(client, uid, uv, mailbox['name'])
                                event['action'] = 'remote_deleted'; audit.write(json.dumps(event) + '\n'); audit.flush(); os.fsync(audit.fileno())
                            row['deleted'] += 1
                    except Exception as error:
                        row['blocked'] += 1
                        if len(row['examples']) < 5: row['examples'].append({'uid': uid, 'reason': type(error).__name__ + ': ' + str(error)[:180]})
                    state['cursors'][key] = uid
                    report['updated_at'] = int(time.time())
                    atomic(state_dir / 'latest-report.json', report)
                state['runs'][key] = time.time()
            except Exception as error: row['error'] = type(error).__name__ + ': ' + str(error)[:180]
            finally:
                if client:
                    try: client.logout()
                    except Exception: pass
                atomic(state_path, state)
                report['finished_at'] = int(time.time()); atomic(state_dir / 'latest-report.json', report)
        report['finished_at'] = int(time.time())
        if report['mailboxes'] or not (state_dir / 'latest-report.json').exists():
            atomic(state_dir / 'latest-report.json', report)
        print(json.dumps({'mode': report['mode'], 'mailboxes': len(report['mailboxes']), 'deleted': sum(m['deleted'] for m in report['mailboxes'])}))
        atomic(state_dir / 'execution-status.json', {'state': 'finished', 'finished_at': int(time.time()), 'mode': report['mode']})
        if request: processing_path.unlink()

if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--force', action='store_true'); p.add_argument('--apply', action='store_true')
    a = p.parse_args()
    try: run(a.force, a.apply)
    except BlockingIOError:
        print('Worker já em execução; solicitação mantida na fila')
    except BaseException as error:
        processing = ROOT / 'state/processing-request.json'
        if processing.exists(): os.replace(processing, ROOT / 'state/failed-request.json')
        atomic(ROOT / 'state/execution-status.json', {'state': 'failed', 'finished_at': int(time.time()), 'error': type(error).__name__ + ': ' + str(error)[:180]})
        raise
