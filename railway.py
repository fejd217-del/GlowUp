"""One Railway service: bot + optional checkout. Container stops if either child fails."""
import os,signal,subprocess,sys,time,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
ROOT=Path(__file__).resolve().parent
children=[];stopping=threading.Event()
def stop(*args):stopping.set()
class Health(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_GET(self):
  healthy=all(p.poll() is None for p in children)
  self.send_response(200 if healthy else 503);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(b'{"ok":true}' if healthy else b'{"ok":false}')
def main():
 from bot import load_env
 load_env();os.chdir(ROOT)
 for name in ('BOT_TOKEN','SUPABASE_URL','SUPABASE_SERVICE_ROLE_KEY'):
  if not os.environ.get(name):raise SystemExit('Не задана переменная '+name)
 signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
 children.append(subprocess.Popen([sys.executable,'-u','bot.py','run']))
 server=None
 try:
  if os.environ.get('CHECKOUT_BASE_URL'):
   env=dict(os.environ);env['CHECKOUT_HOST']='0.0.0.0'
   children.append(subprocess.Popen([sys.executable,'-u','checkout.py'],env=env))
  else:
   server=ThreadingHTTPServer(('0.0.0.0',int(os.environ.get('PORT','8080'))),Health)
   threading.Thread(target=server.serve_forever,daemon=True).start()
   print('Запущен бот. Для сайта оплаты добавь CHECKOUT_BASE_URL и настройки USDT.',flush=True)
  while not stopping.wait(1):
   failed=next((p for p in children if p.poll() is not None),None)
   if failed is not None:raise SystemExit(failed.returncode or 1)
 finally:
  for p in children:
   if p.poll() is None:p.terminate()
  for p in children:
   try:p.wait(timeout=10)
   except subprocess.TimeoutExpired:p.kill();p.wait()
  if server:server.shutdown();server.server_close()
if __name__=='__main__':main()
