import os, json, time, base64, sqlite3, threading, secrets
from urllib.parse import urlencode
from flask import Flask, request, redirect, jsonify, send_file, Response
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

app = Flask(__name__)
BASE = 'https://api.kick.com/public/v1'
DB = os.getenv('DATABASE_PATH', '/tmp/mechax-widget.sqlite3')
LOCK = threading.RLock()

def connect():
    conn = sqlite3.connect(DB, timeout=20)
    conn.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    conn.execute('CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, created_at INTEGER NOT NULL)')
    conn.commit()
    return conn

def get(key, default=None):
    with LOCK, connect() as db:
        row = db.execute('SELECT value FROM state WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default

def put(key, value):
    with LOCK, connect() as db:
        db.execute('INSERT OR REPLACE INTO state(key,value) VALUES(?,?)',(key,json.dumps(value)))
        db.commit()

def refresh_token():
    token = get('tokens')
    if not token: return None
    if time.time() < token.get('expires_at',0)-120: return token['access_token']
    response = requests.post('https://id.kick.com/oauth/token', data={
        'grant_type':'refresh_token','refresh_token':token['refresh_token'],
        'client_id':os.environ['KICK_CLIENT_ID'],'client_secret':os.environ['KICK_CLIENT_SECRET']
    },timeout=12)
    response.raise_for_status()
    new = response.json()
    new['refresh_token'] = new.get('refresh_token',token['refresh_token'])
    new['expires_at'] = time.time()+int(new.get('expires_in',3600))
    put('tokens',new)
    return new['access_token']

def api(method, path, **kwargs):
    token = refresh_token()
    if not token: raise RuntimeError('Autoriza Kick primero')
    resp=requests.request(method, BASE+path,headers={'Authorization':'Bearer '+token,'Accept':'application/json','Content-Type':'application/json'},timeout=15,**kwargs)
    resp.raise_for_status()
    return resp.json() if resp.content else {}

def update_active():
    data=api('GET','/channels')
    channels=data.get('data',[]) if isinstance(data,dict) else []
    if isinstance(channels,dict): channels=[channels]
    if channels and channels[0].get('active_subscribers_count') is not None:
        put('active',int(channels[0]['active_subscribers_count']))
    return get('active')

@app.get('/')
def home():
    return 'MECHAX GTA VI Widget · servidor funcionando. Visita /overlay para OBS.'

@app.get('/health')
def health():
    return jsonify(ok=True,connected=bool(get('tokens')),database=DB)

@app.get('/overlay')
def overlay():
    return send_file('overlay.html')

@app.get('/api/state')
def state():
    # Actualización periódica del total activo sin llamar a Kick en cada frame
    if get('tokens') and time.time()-get('active_updated',0)>90:
        try:
            update_active(); put('active_updated',time.time())
        except Exception as exc: app.logger.warning('No se pudo actualizar subs activas: %s',exc)
    return jsonify(progress=get('progress',0), activeSubscribers=get('active'), completed=get('completed',0))

@app.get('/auth/start')
def auth_start():
    if request.args.get('key') != os.getenv('ADMIN_KEY') or not os.getenv('ADMIN_KEY'):
        return 'Acceso denegado',403
    state=secrets.token_urlsafe(24); put('oauth_state',state)
    url='https://id.kick.com/oauth/authorize?'+urlencode({
        'response_type':'code','client_id':os.environ['KICK_CLIENT_ID'],
        'redirect_uri':os.environ['PUBLIC_URL'].rstrip('/')+'/auth/callback',
        'scope':'user:read channel:read events:subscribe','state':state,
        'code_challenge_method':'S256', 'code_challenge': _pkce()[1]
    })
    return redirect(url)

def _pkce():
    import hashlib
    verifier=secrets.token_urlsafe(48)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    put('pkce_verifier',verifier)
    return verifier,challenge

@app.get('/auth/callback')
def callback():
    if not request.args.get('state') or request.args['state']!=get('oauth_state'):
        return 'OAuth state inválido',400
    code=request.args.get('code')
    if not code: return 'Falta el código OAuth',400
    resp=requests.post('https://id.kick.com/oauth/token',data={
        'grant_type':'authorization_code','code':code,
        'redirect_uri':os.environ['PUBLIC_URL'].rstrip('/')+'/auth/callback',
        'client_id':os.environ['KICK_CLIENT_ID'],'client_secret':os.environ['KICK_CLIENT_SECRET'],
        'code_verifier':get('pkce_verifier')},timeout=15)
    if not resp.ok: return 'Error de autorización de Kick: '+str(resp.status_code),502
    token=resp.json();token['expires_at']=time.time()+int(token.get('expires_in',3600));put('tokens',token)
    put('oauth_state',None);put('pkce_verifier',None)
    try:
        api('POST','/events/subscriptions',json={'events':[
            {'name':'channel.subscription.new','version':1},
            {'name':'channel.subscription.gifts','version':1}
        ],'method':'webhook'})
    except Exception as exc:
        app.logger.warning('Suscripción a eventos pendiente: %s',exc)
        return 'Kick autorizado. Revisa los logs de Render: no se pudo registrar la suscripción de eventos.',200
    return '¡Kick autorizado y eventos registrados! Abre /overlay en OBS.'

_public_key = None
def verify_webhook(body):
    global _public_key
    signature=request.headers.get('Kick-Event-Signature','')
    msg_id=request.headers.get('Kick-Event-Message-Id','')
    timestamp=request.headers.get('Kick-Event-Message-Timestamp','')
    if not signature or not msg_id or not timestamp: return False
    try:
        if _public_key is None:
            r=requests.get(BASE+'/public-key',timeout=12);r.raise_for_status()
            payload=r.json();pem=payload.get('data',{}).get('public_key') if isinstance(payload.get('data'),dict) else payload.get('data')
            pem=pem or payload.get('public_key')
            _public_key=serialization.load_pem_public_key(pem.encode())
        signed=msg_id.encode()+b'.'+timestamp.encode()+b'.'+body
        _public_key.verify(base64.b64decode(signature),signed,padding.PKCS1v15(),hashes.SHA256())
        return True
    except Exception as exc:
        app.logger.warning('Firma inválida o clave pública inaccesible: %s',exc)
        return False

@app.post('/webhooks/kick')
def webhook():
    raw=request.get_data()
    if not verify_webhook(raw): return 'Firma inválida',401
    event=request.headers.get('Kick-Event-Type','')
    mid=request.headers.get('Kick-Event-Message-Id','')
    if event not in ('channel.subscription.new','channel.subscription.gifts'):
        return '',204
    payload=request.get_json(silent=True) or {}
    # Kick envía gifts como un evento con múltiples receptores.
    if event=='channel.subscription.gifts':
        gifts=payload.get('giftees') or payload.get('gifted_users') or []
        count=len(gifts) if isinstance(gifts,list) and gifts else payload.get('gift_count',payload.get('amount',0))
    else: count=1
    try: count=max(0,min(1000,int(count)))
    except (ValueError,TypeError): count=0
    with LOCK, connect() as db:
        if db.execute('SELECT 1 FROM seen WHERE id=?',(mid,)).fetchone(): return '',204
        db.execute('INSERT INTO seen VALUES(?,?)',(mid,int(time.time())))
        current=get('progress',0)+count
        completed=get('completed',0)+current//30
        db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',('progress',json.dumps(current%30)))
        db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',('completed',json.dumps(completed)))
        db.commit()
    put('active_updated',0)
    return '',204

if __name__=='__main__':
    app.run(host='0.0.0.0',port=int(os.getenv('PORT','10000')))
