import unittest,time,copy,json
from unittest.mock import patch
import bot
class API:
 def __init__(self):self.calls=[];self.member_status='member';self.approve_fail=False
 def call(self,method,**data):
  self.calls.append((method,data))
  if method=='getChatMember':return {'status':self.member_status}
  if method=='approveChatJoinRequest' and self.approve_fail:raise bot.RemoteError('already approved')
  if method=='createChatInviteLink':return {'invite_link':'https://t.me/+personal'}
  return {'message_id':1}
class DB:
 def __init__(self):
  self.users={1:{'id':1,'name':'User','username':'u','trial_started':None,'trial_seconds':300,'access_until':0,'lifetime':False,'blocked':False,'managed':False,'flow':{}}}
  self.invites=[];self.orders=[];self.calls=[];self.effects=set();self.ticket=0
 def one(self,t,**f):
  if t=='glow_users':return self.users.get(int(f['id'][3:]))
  rows=self.invites if t=='glow_invites' else self.orders if t=='glow_orders' else []
  for r in rows:
   if all(k not in r or str(r[k])==str(v[3:]) for k,v in f.items() if k not in ['order','limit'] and v.startswith('eq.')):return r
  return None
 def get(self,*args,**kwargs):return []
 def put(self,t,d,**kw):
  self.calls.append(('put',t,d))
  if t=='glow_orders':self.orders.append(dict(d,created_at=int(time.time()),state='new'))
  if t=='glow_invites':self.invites.append(d)
  return [d]
 def patch(self,t,d,**kw):
  self.calls.append(('patch',t,d))
  if t=='glow_users':self.users[int(kw['id'][3:])].update(d)
 def action(self,action,**kw):
  self.calls.append((action,kw))
  if action=='expired':return list(self.users.values())
  if action=='payment':
   duplicate=kw['charge'] in self.effects;self.effects.add(kw['charge']);return {'duplicate':duplicate}
  if action=='joined':self.users[kw['uid']]['managed']=True;self.users[kw['uid']]['trial_started']=int(time.time())
  if action=='stats':return dict(users=1,active=0,trials=0,stars=0,payments=0,tickets=0)
  return {}
class Tests(unittest.TestCase):
 def setUp(self):
  self.cfg=json.loads(bot.ROOT.joinpath('config.example.json').read_text());self.cfg['admin_ids']=[9]
  self.api=API();self.db=DB();self.club=bot.Club(self.api,self.db,self.cfg)
 def test_cached_cover_renders_with_buttons(self):
  self.cfg['screen_photo_file_ids']={'home':'photo-id'}
  self.club.home(1)
  method,data=self.api.calls[-1]
  self.assertEqual(method,'sendPhoto');self.assertEqual(data['photo'],'photo-id')
  self.assertTrue(data['reply_markup']['inline_keyboard'])
 def test_cached_cover_edits_media_on_navigation(self):
  self.cfg['screen_photo_file_ids']={'plans':'photo-id'}
  self.club.plans(1,mid=12)
  self.assertEqual(self.api.calls[-1][0],'editMessageMedia')
  self.assertEqual(self.api.calls[-1][1]['message_id'],12)
 def test_closed_sales_send_no_invoice(self):
  with self.assertRaises(ValueError):self.club.invoice(1,'month')
  self.assertFalse(self.api.calls);self.assertFalse(self.db.orders)
 def test_invoice_price_comes_from_config(self):
  self.cfg['sales_enabled']=True;self.cfg['plans']['month']['stars']=123
  self.club.invoice(1,'month');m,data=self.api.calls[-1]
  self.assertEqual(m,'sendInvoice');self.assertEqual(data['currency'],'XTR');self.assertEqual(data['prices'][0]['amount'],123)
  self.assertEqual(self.db.orders[0]['days'],30)
 def test_wrong_user_cannot_pay_another_invoice(self):
  self.db.orders=[dict(id='o',user_id=2,stars=10,state='new',created_at=int(time.time()))]
  self.club.precheckout(dict(id='q',invoice_payload='o',total_amount=10,currency='XTR',**{'from':{'id':1}}))
  self.assertFalse(self.api.calls[-1][1]['ok'])
 def test_wrong_amount_rejected(self):
  self.db.orders=[dict(id='o',user_id=1,stars=10,state='new',created_at=int(time.time()))]
  self.club.precheckout(dict(id='q',invoice_payload='o',total_amount=11,currency='XTR',**{'from':{'id':1}}))
  self.assertFalse(self.api.calls[-1][1]['ok'])
 def test_payment_replay_has_no_second_delivery(self):
  msg={'from':{'id':1},'successful_payment':dict(invoice_payload='o',total_amount=10,currency='XTR',telegram_payment_charge_id='charge')}
  self.club.payment(msg);n=len(self.api.calls);self.club.payment(msg);self.assertEqual(len(self.api.calls),n)
 def test_forwarded_personal_link_is_declined(self):
  self.db.invites=[dict(link='x',user_id=2,kind='trial',expires=int(time.time())+600)]
  self.club.join(dict(chat={'id':self.cfg['chat_id']},**{'from':{'id':1}},invite_link={'invite_link':'x'}))
  self.assertEqual(self.api.calls[-1][0],'declineChatJoinRequest');self.assertFalse(any(x[0]=='joined' for x in self.db.calls))
 def test_trial_clock_not_started_on_button(self):
  self.api.member_status='left'
  self.club.invite(1,True);self.assertIsNone(self.db.users[1]['trial_started'])
 def test_existing_member_is_not_put_on_trial(self):
  with self.assertRaises(ValueError):self.club.invite(1,True)
  self.assertIsNone(self.db.users[1]['trial_started'])
 def test_used_trial_cannot_restart(self):
  self.db.users[1]['trial_started']=int(time.time())-600
  with self.assertRaises(ValueError):self.club.invite(1,True)
  self.assertFalse(self.api.calls)
 def test_paid_user_is_not_expired(self):
  self.db.users[1]['access_until']=int(time.time())+3600;self.club.expire();self.assertFalse(self.api.calls)
 def test_expired_member_is_banned_without_deleting_history(self):
  self.db.users[1]['managed']=True;self.club.expire();b=[p for m,p in self.api.calls if m=='banChatMember']
  self.assertEqual(len(b),1);self.assertFalse(b[0]['revoke_messages'])
 def test_admin_is_never_removed(self):
  self.api.member_status='administrator';self.club.expire();self.assertFalse(any(m=='banChatMember' for m,p in self.api.calls))
 def test_replayed_join_after_approve_success(self):
  self.db.invites=[dict(link='x',user_id=1,kind='trial',expires=int(time.time())+600)]
  self.api.approve_fail=True
  self.club.join(dict(chat={'id':self.cfg['chat_id']},**{'from':{'id':1}},invite_link={'invite_link':'x'}))
  self.assertTrue(self.db.users[1]['managed'])
 def test_all_admin_callbacks_are_authorized_before_db(self):
  for callback in ['a:revoke:9','a:users:0','a:refund:o','a:sales','a:confirm:abc']:
   with self.assertRaises(ValueError):self.club.admin_callback(1,callback,None)
  self.assertFalse(self.db.calls);self.assertFalse(self.api.calls)
 def test_confirmation_token_required(self):
  self.db.users[9]=dict(self.db.users[1],id=9,flow={'mode':'confirm','id':'right','action':'revoke','target':1})
  with self.assertRaises(ValueError):self.club.admin_callback(9,'a:confirm:wrong',None)
  self.assertFalse(self.db.calls)
 def test_broadcast_has_preview_not_delivery(self):
  self.db.users[9]=dict(self.db.users[1],id=9,flow={'mode':'broadcast'})
  self.club.text({'from':{'id':9},'text':'hello','message_id':9})
  self.assertFalse(any(c[0]=='put' and c[1]=='glow_campaigns' for c in self.db.calls));self.assertEqual(self.api.calls[-1][1]['chat_id'],9)
 def test_plain_text_is_escaped(self):
  self.db.users[9]=dict(self.db.users[1],id=9,flow={'mode':'broadcast'})
  self.club.text({'from':{'id':9},'text':'<b>test</b>','message_id':9})
  self.assertIn('&lt;b&gt;',self.api.calls[-1][1]['text'])
if __name__=='__main__':unittest.main()
