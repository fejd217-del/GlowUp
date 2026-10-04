"""Independent checkout. Put behind an HTTPS reverse proxy. Never expose service-role key."""
import io,json,os,secrets,time,urllib.parse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from bot import Database,Telegram,config,load_env,RemoteError
from crypto import Crypto,digest,amount,payment_links
ROOT=Path(__file__).parent
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass # Request URLs may contain invoice identifiers.
 def respond(self,status,data,kind='application/json',cookie=None):
  raw=json.dumps(data,ensure_ascii=False).encode() if kind=='application/json' else data
  self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)))
  self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
  self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
  if cookie:self.send_header('Set-Cookie',cookie)
  self.end_headers();self.wfile.write(raw)
 def session(self,required=False):
  cookies=SimpleCookie()
  try:cookies.load(self.headers.get('Cookie',''))
  except Exception:pass
  token=cookies['glow_session'].value if 'glow_session' in cookies else ''
  row=DB.one('glow_web_sessions',id='eq.'+digest(token),expires='gt.'+str(int(time.time()))) if token else None
  if required and (not row or not row.get('user_id')):raise ValueError('Сначала подключи Telegram')
  return row
 def refresh(self):
  row=DB.one('glow_runtime',key='eq.settings')
  if row:CFG.update(row['value'])
 def public_order(self,row):
  result={k:row[k] for k in ('id','plan','amount','received','recipient','state','expires','credited')}
  if result['state']=='pending' and result['expires']<time.time():result['state']='expired'
  result['amount_text']=amount(row['amount']);result['received_text']=amount(row['received'])
  result['remaining_text']=amount(max(0,row['amount']-row['received']))
  result['links']=payment_links(row['recipient'],max(1,row['amount']-row['received']),row['id']);return result
 def owned_order(self,session,oid):
  row=DB.one('glow_crypto_orders',id='eq.'+oid,user_id='eq.'+str(session['user_id']))
  if not row:raise ValueError('Счёт не найден')
  return row
 def do_GET(self):
  try:
   url=urllib.parse.urlsplit(self.path);path=url.path;query=urllib.parse.parse_qs(url.query)
   assets={'/':('checkout/index.html','text/html; charset=utf-8'),'/app.js':('checkout/app.js','text/javascript; charset=utf-8'),'/style.css':('checkout/style.css','text/css; charset=utf-8'),'/home.png':('assets/home.png','image/png')}
   if path in assets:
    name,kind=assets[path];self.respond(200,(ROOT/name).read_bytes(),kind);return
   if path=='/health':self.respond(200,{'ok':True});return
   if path=='/api/session':
    self.refresh();row=self.session();cookie=None;challenge=None
    if not row:
     token=secrets.token_urlsafe(32);challenge=secrets.token_urlsafe(24)
     row=DB.put('glow_web_sessions',{'id':digest(token),'challenge':digest(challenge),'expires':int(time.time())+900})[0]
     cookie='glow_session='+token+'; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=3600'
    user=DB.one('glow_users',id='eq.'+str(row['user_id'])) if row.get('user_id') else None
    plans=[{'id':k,'name':v['name'],'price':str(CFG.get('crypto',{}).get('usdt_prices',{}).get(k) or '')} for k,v in CFG['plans'].items()]
    self.respond(200,{'user':{'id':user['id'],'name':user['name']} if user else None,'login':'https://t.me/'+BOT_NAME+'?start=web_'+challenge if challenge else None,'enabled':CRYPTO.enabled(),'plans':plans},cookie=cookie);return
   session=self.session(True)
   if path=='/api/orders':
    rows=DB.get('glow_crypto_orders',user_id='eq.'+str(session['user_id']),order='created_at.desc',limit=10)
    self.respond(200,[self.public_order(r) for r in rows]);return
   if path in ('/api/order','/api/qr'):
    row=self.owned_order(session,query.get('id',[''])[0])
    if path=='/api/order':self.respond(200,self.public_order(row));return
    import qrcode,qrcode.image.svg
    qr=qrcode.make(payment_links(row['recipient'],max(1,row['amount']-row['received']),row['id'])['ton'],image_factory=qrcode.image.svg.SvgPathImage)
    output=io.BytesIO();qr.save(output);self.respond(200,output.getvalue(),'image/svg+xml');return
   self.respond(404,{'error':'Страница не найдена'})
  except (ValueError,RemoteError) as e:self.respond(400,{'error':str(e)})
  except Exception:self.respond(500,{'error':'Сервис временно недоступен. Попробуй позже.'})
 def do_POST(self):
  try:
   if self.headers.get('Origin')!=BASE: self.respond(403,{'error':'Неверный источник запроса'});return
   length=int(self.headers.get('Content-Length','0'))
   if not 0<length<=1024:raise ValueError('Неверный запрос')
   body=json.loads(self.rfile.read(length));session=self.session(True);self.refresh()
   if self.path=='/api/order':self.respond(200,self.public_order(CRYPTO.order(session['user_id'],body.get('plan',''))));return
   if self.path=='/api/logout':
    DB.patch('glow_web_sessions',{'expires':0},id='eq.'+session['id']);self.respond(200,{'ok':True},cookie='glow_session=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0');return
   self.respond(404,{'error':'Не найдено'})
  except (ValueError,RemoteError,KeyError,TypeError):self.respond(400,{'error':'Не удалось выполнить запрос. Проверь вход и настройки оплаты.'})
  except Exception:self.respond(500,{'error':'Сервис временно недоступен'})
if __name__=='__main__':
 load_env();CFG=config();BASE=os.environ.get('CHECKOUT_BASE_URL','').rstrip('/')
 parsed=urllib.parse.urlsplit(BASE)
 if parsed.scheme!='https' or not parsed.netloc or parsed.path:raise SystemExit('CHECKOUT_BASE_URL: нужен HTTPS адрес сайта без пути')
 DB=Database(os.environ['SUPABASE_URL'],os.environ['SUPABASE_SERVICE_ROLE_KEY']);CRYPTO=Crypto(DB,CFG)
 BOT_NAME=Telegram(os.environ['BOT_TOKEN']).call('getMe')['username']
 DB.get('glow_web_sessions',limit=1) # Fail early if crypto_schema.sql has not been applied.
 server=ThreadingHTTPServer((os.environ.get('CHECKOUT_HOST','0.0.0.0' if os.environ.get('RAILWAY_ENVIRONMENT_ID') else '127.0.0.1'),int(os.environ.get('PORT') or os.environ.get('CHECKOUT_PORT','8080'))),Handler)
 print('Сайт оплаты запущен. HTTPS должен обслуживаться прокси.',flush=True)
 try:server.serve_forever()
 except KeyboardInterrupt:server.server_close()
