import http.client,json,threading,time,unittest
from http.server import ThreadingHTTPServer
import checkout
from crypto import digest
class DB:
 def __init__(self):self.calls=[]
 def one(self,table,**filters):
  self.calls.append((table,filters))
  if table=='glow_web_sessions' and filters['id']=='eq.'+digest('valid'):return {'id':digest('valid'),'user_id':123,'expires':int(time.time())+60}
  if table=='glow_crypto_orders' and filters['id']=='eq.own' and filters['user_id']=='eq.123':return dict(id='own',user_id=123,plan='month',amount=15000000,received=5000000,recipient='0:'+'11'*32,state='underpaid',expires=int(time.time())+60,credited=False)
  return None
class Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  checkout.DB=DB();checkout.BASE='https://pay.example.com'
  cls.server=ThreadingHTTPServer(('127.0.0.1',0),checkout.Handler);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
 @classmethod
 def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join()
 def request(self,path,method='GET',headers=None,data=None):
  c=http.client.HTTPConnection('127.0.0.1',self.server.server_port);c.request(method,path,body=data,headers=headers or {});r=c.getresponse();body=r.read();c.close();return r.status,body
 def test_orders_need_authenticated_session(self):
  status,_=self.request('/api/order?id=own');self.assertEqual(status,400)
 def test_other_users_order_not_disclosed(self):
  status,_=self.request('/api/order?id=someone-else',headers={'Cookie':'glow_session=valid'});self.assertEqual(status,400)
 def test_owned_order_and_remaining_topup(self):
  status,body=self.request('/api/order?id=own',headers={'Cookie':'glow_session=valid'});self.assertEqual(status,200);row=json.loads(body);self.assertEqual(row['remaining_text'],'10');self.assertIn('amount=10000000',row['links']['tonkeeper'])
 def test_cross_site_post_rejected(self):
  status,_=self.request('/api/order','POST',{'Origin':'https://evil.example','Cookie':'glow_session=valid','Content-Type':'application/json'},'{"plan":"month"}');self.assertEqual(status,403)
 def test_secret_files_not_served(self):
  status,body=self.request('/.env');self.assertNotEqual(status,200);self.assertNotIn(b'SUPABASE_SERVICE_ROLE_KEY',body)
if __name__=='__main__':unittest.main()
