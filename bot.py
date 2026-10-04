"""GlowUp Club: Telegram Stars, verified join requests, Supabase, private admin panel.
Python 3.10+; crypto checkout dependencies in requirements.txt. Run: py bot.py setup / check / run.
"""
import os,json,time,uuid,math,html,pathlib,sys,getpass,urllib.request,urllib.parse,urllib.error,threading,logging
ROOT=pathlib.Path(__file__).resolve().parent
log=logging.getLogger('glowup')
class RemoteError(Exception):
 def __init__(self,msg,uncertain=False,retry_after=0):super().__init__(msg);self.uncertain=uncertain;self.retry_after=retry_after

def http(url,payload,headers,timeout=15,method='POST'):
 data=json.dumps(payload,ensure_ascii=False).encode() if payload is not None else None
 req=urllib.request.Request(url,data=data,headers={'Content-Type':'application/json',**headers},method=method)
 try:
  with urllib.request.urlopen(req,timeout=timeout) as r:raw=r.read()
 except urllib.error.HTTPError as e:
  try:body=json.loads(e.read());msg=body.get('description') or body.get('message') or 'HTTP '+str(e.code)
  except Exception:msg='HTTP '+str(e.code)
  raise RemoteError(msg,e.code>=500,int(body.get('parameters',{}).get('retry_after',0)) if isinstance(locals().get('body'),dict) else 0) from None
 except (urllib.error.URLError,TimeoutError,OSError):raise RemoteError('Сетевая ошибка: результат запроса неизвестен',True) from None
 if not raw:return None
 try:return json.loads(raw)
 except ValueError:raise RemoteError('Не удалось прочитать ответ сервера',True) from None

class Telegram:
 def __init__(self,token):self.base='https://api.telegram.org/bot'+token+'/';self.token=token
 def call(self,method,**payload):
  try:r=http(self.base+method,payload,{},30 if method=='getUpdates' else 15)
  except RemoteError as e:raise RemoteError(str(e).replace(self.token,'[token]'),e.uncertain,e.retry_after) from None
  if not r.get('ok'):raise RemoteError(r.get('description','Ошибка Telegram'),False,r.get('parameters',{}).get('retry_after',0))
  return r['result']

 def photo_file(self,path,**fields):
  boundary='glow-'+uuid.uuid4().hex;body=bytearray()
  for name,value in fields.items():
   if isinstance(value,dict):value=json.dumps(value,ensure_ascii=False)
   body.extend(('--'+boundary+'\r\nContent-Disposition: form-data; name="'+name+'"\r\n\r\n'+str(value)+'\r\n').encode())
  body.extend(('--'+boundary+'\r\nContent-Disposition: form-data; name="photo"; filename="cover.png"\r\nContent-Type: image/png\r\n\r\n').encode());body.extend(path.read_bytes());body.extend(('\r\n--'+boundary+'--\r\n').encode())
  req=urllib.request.Request(self.base+'sendPhoto',data=bytes(body),headers={'Content-Type':'multipart/form-data; boundary='+boundary})
  try:
   with urllib.request.urlopen(req,timeout=30) as response:r=json.loads(response.read())
  except urllib.error.HTTPError as e:
   try:msg=json.loads(e.read()).get('description','Ошибка загрузки обложки')
   except Exception:msg='Ошибка загрузки обложки'
   raise RemoteError(msg.replace(self.token,'[token]'),e.code>=500) from None
  except (urllib.error.URLError,TimeoutError,OSError,ValueError):raise RemoteError('Не удалось загрузить обложку',True) from None
  if not r.get('ok'):raise RemoteError(r.get('description','Ошибка загрузки обложки'))
  return r['result']

class Database:
 def __init__(self,url,key):
  if not url.startswith('https://') or not urllib.parse.urlsplit(url).hostname:raise ValueError('Нужен HTTPS URL Supabase')
  self.base=url.rstrip('/')+'/rest/v1/';self.headers={'apikey':key,'Authorization':'Bearer '+key};self.key=key
 def request(self,path,payload=None,method='GET',prefer=None):
  headers=dict(self.headers)
  if prefer:headers['Prefer']=prefer
  try:return http(self.base+path,payload,headers,15,method)
  except RemoteError as e:raise RemoteError(str(e).replace(self.key,'[key]'),e.uncertain,e.retry_after) from None
 def get(self,table,**filters):return self.request(table+'?'+urllib.parse.urlencode(filters))
 def one(self,table,**filters):
  rows=self.get(table,**filters,limit=1);return rows[0] if rows else None
 def put(self,table,data,conflict=None,ignore=False):
  path=table+('?on_conflict='+conflict if conflict else '')
  prefer='return=representation'+(',resolution=ignore-duplicates' if ignore else ',resolution=merge-duplicates' if conflict else '')
  return self.request(path,data,'POST',prefer)
 def patch(self,table,data,**filters):return self.request(table+'?'+urllib.parse.urlencode(filters),data,'PATCH','return=representation')
 def action(self,action,**args):return self.request('rpc/glow_action',{'action':action,'args':args},'POST')
 def lease(self,owner):return self.request('rpc/glow_lease',{'owner':owner},'POST')

class Club:
 def __init__(self,api,db,cfg):self.api=api;self.db=db;self.cfg=cfg;self.chat=cfg['chat_id'];self.bot_id=None
 def user(self,uid):return self.db.one('glow_users',id='eq.'+str(uid))
 def remember(self,person):
  self.db.put('glow_users',{'id':person['id'],'trial_seconds':self.cfg['trial_minutes']*60},conflict='id',ignore=True)
  self.db.patch('glow_users',{'name':person.get('first_name',''),'username':person.get('username','')},id='eq.'+str(person['id']))
 def admin(self,uid):return uid in self.cfg['admin_ids']
 def active(self,u,now=None):
  now=int(time.time()) if now is None else now
  return bool(u and not u['blocked'] and (u['lifetime'] or u['access_until']>now or (u['trial_started'] is not None and u['trial_started']+u['trial_seconds']>now)))
 def paid(self,u):return bool(u and not u['blocked'] and (u['lifetime'] or u['access_until']>int(time.time())))
 def button(self,text,data=None,url=None,style=None,icon=None):
  b={'text':text};b['url' if url else 'callback_data']=url or data
  if style and self.cfg.get('colored_buttons'):b['style']=style
  eid=self.cfg.get('custom_emoji_ids',{}).get(icon)
  if eid:b['icon_custom_emoji_id']=eid
  return b
 def keyboard(self,rows):return {'inline_keyboard':rows}
 def menu(self,uid):
  b=self.button;rows=[[b('💎 Оформить доступ','plans',style='primary',icon='buy')],[b('⏳ Посмотреть форум · '+str(self.cfg['trial_minutes'])+' мин','trial',icon='trial')],[b('📚 Что внутри','catalog')],[b('🗓 Мой доступ','account'),b('💬 Поддержка','support')]]
  if self.admin(uid):rows.append([b('⚙️ Админ-панель','admin',style='primary')])
  return rows
 def send(self,uid,text,rows=None):
  return self.api.call('sendMessage',chat_id=uid,text=text,parse_mode='HTML',link_preview_options={'is_disabled':True},**({'reply_markup':self.keyboard(rows)} if rows else {}))
 def show(self,uid,text,rows=None,mid=None,screen=None):
  if screen and len(text.encode('utf-16-le'))<=1900:
   ids=self.cfg.setdefault('screen_photo_file_ids',{});fid=ids.get(screen)
   if screen=='home' and self.cfg.get('welcome_photo_file_id'):fid=self.cfg['welcome_photo_file_id']
   asset=ROOT/'assets'/('plans.png' if screen in ['plans','account'] else 'home.png')
   fields={'chat_id':uid,'caption':text,'parse_mode':'HTML','reply_markup':self.keyboard(rows or [])}
   if not fid and asset.exists():
    result=self.api.photo_file(asset,**fields);ids[screen]=result['photo'][-1]['file_id'];save_config(self.cfg);return result
   if fid:
    if mid:
     try:return self.api.call('editMessageMedia',chat_id=uid,message_id=mid,media={'type':'photo','media':fid,'caption':text,'parse_mode':'HTML'},reply_markup=self.keyboard(rows or []))
     except RemoteError as e:
      if 'message is not modified' in str(e).lower():return
      if e.uncertain:raise
    return self.api.call('sendPhoto',photo=fid,**fields)
  if mid:
   try:return self.api.call('editMessageText',chat_id=uid,message_id=mid,text=text,parse_mode='HTML',reply_markup=self.keyboard(rows or []),link_preview_options={'is_disabled':True})
   except RemoteError as e:
    if 'message is not modified' in str(e).lower():return
    if e.uncertain or ('message to edit not found' not in str(e).lower() and 'no text' not in str(e).lower()):raise
  return self.send(uid,text,rows)
 def flow(self,uid,**data):self.db.patch('glow_users',{'flow':data},id='eq.'+str(uid))
 def back(self):return [[self.button('← Главное меню','home')]]
 def home(self,uid,mid=None):
  text='<b>GlowUp Club</b>\n\n'+html.escape(self.cfg['description'])+'\n\nПосмотри состав материалов или используй пробный вход перед покупкой.'
  self.show(uid,text,self.menu(uid),mid,screen='home')
 def plans(self,uid,mid=None):
  b=self.button;rows=[]
  for key,p in self.cfg['plans'].items():
   if self.cfg['sales_enabled'] and isinstance(p['stars'],int) and p['stars']>0:rows.append([b(p['name']+' · '+str(p['stars'])+' ⭐','buy:'+key,style='primary')])
  text='<b>Доступ в GlowUp Club</b>\n\nВесь опубликованный контент форума на выбранный срок. Продление добавляет дни к оставшемуся сроку.\n\n'+html.escape(self.cfg['terms'])
  if not rows:text+='\n\nПродажи пока не открыты. Можно посмотреть форум через пробный доступ.'
  rows+=[[b('🎟 У меня есть промокод','promo')],*self.back()];self.show(uid,text,rows,mid,screen='plans')
 def account(self,uid,mid=None):
  u=self.user(uid);b=self.button;rows=[]
  if u['blocked']:text='Доступ заблокирован. Напиши в поддержку, чтобы уточнить причину.'
  elif u['lifetime']:text='Доступ без срока действия.'
  elif u['access_until']>int(time.time()):text='Доступ до <b>'+self.date(u['access_until'])+'</b>.'
  elif self.active(u):text='Пробный доступ до <b>'+self.date(u['trial_started']+u['trial_seconds'])+'</b>.'
  else:text='Сейчас активного доступа нет.'
  if self.active(u):rows.append([b('🚪 Войти в форум','enter',style='success')])
  rows+=[[b('💎 Купить / продлить','plans')],[b('💬 Поддержка','support')],*self.back()]
  self.show(uid,'<b>Мой доступ</b>\n\n'+text,rows,mid,screen='account')
 @staticmethod
 def date(ts):return time.strftime('%d.%m.%Y %H:%M',time.gmtime(ts+5*3600))+' (Екатеринбург)'
 def invoice(self,uid,key,promo=None):
  p=self.cfg['plans'].get(key);u=self.user(uid)
  if not p or not self.cfg['sales_enabled'] or not isinstance(p['stars'],int) or p['stars']<=0:raise ValueError('Тариф пока недоступен')
  if u['blocked']:raise ValueError('Сначала напиши в поддержку')
  # Precheckout and successful_payment verify the immutable database order, never callback prices.
  oid=uuid.uuid4().hex
  data={'id':oid,'user_id':uid,'plan':key,'stars':p['stars'],'days':p['days']}
  if promo:data=self.db.action('promo_order',uid=uid,id=oid,plan=key,stars=p['stars'],days=p['days'],code=promo)
  else:self.db.put('glow_orders',data)
  self.api.call('sendInvoice',chat_id=uid,title='GlowUp · '+p['name'],description='Доступ к опубликованным материалам форума. '+('Без срока.' if p['days'] is None else str(p['days'])+' дней. Без автоматического списания.'),payload=oid,provider_token='',currency='XTR',prices=[{'label':p['name'],'amount':data['stars']}],start_parameter='glowup')
 def precheckout(self,q):
  o=self.db.one('glow_orders',id='eq.'+q['invoice_payload']);u=self.user(q['from']['id'])
  ok=bool(o and o['user_id']==q['from']['id'] and o['stars']==q['total_amount'] and q['currency']=='XTR' and o['state']=='new' and o['created_at']+3600>int(time.time()) and u and not u['blocked'])
  self.api.call('answerPreCheckoutQuery',pre_checkout_query_id=q['id'],ok=ok,**({} if ok else {'error_message':'Этот счёт недоступен. Открой тарифы и создай новый.'}))
 def payment(self,msg):
  p=msg['successful_payment'];uid=msg['from']['id']
  r=self.db.action('payment',uid=uid,order=p['invoice_payload'],stars=p['total_amount'],currency=p['currency'],charge=p['telegram_payment_charge_id'])
  if r.get('duplicate'):return
  log.info('Оплата учтена: user=%s',uid)
  self.send(uid,'<b>Оплата получена</b>\n\nДоступ активирован. Нажми кнопку ниже, чтобы войти.',[[self.button('🚪 Войти в GlowUp','enter',style='success')],[self.button('🗓 Мой доступ','account')]])
 def invite(self,uid,trial=False):
  u=self.user(uid)
  if u['blocked']:raise ValueError('Доступ заблокирован. Напиши в поддержку.')
  if trial and not self.paid(u) and u['trial_started'] is None:
   member=self.api.call('getChatMember',chat_id=self.chat,user_id=uid)
   if member['status'] in ['member','administrator','creator']:
    raise ValueError('Ты уже находишься в форуме. Для действующих участников пробный вход не запускается.')
  if trial and self.paid(u):trial=False
  if trial and u['trial_started'] is not None and not self.active(u):raise ValueError('Пробный доступ уже использован. Выбери тариф.')
  if not trial and not self.active(u):raise ValueError('Нет активного доступа. Выбери тариф.')
  kind='trial' if trial else 'paid';ts=int(time.time())
  old=self.db.one('glow_invites',user_id='eq.'+str(uid),kind='eq.'+kind,consumed='eq.false',expires='gt.'+str(ts+30),order='expires.desc')
  if old:link=old['link']
  else:
   self.api.call('unbanChatMember',chat_id=self.chat,user_id=uid,only_if_banned=True)
   r=self.api.call('createChatInviteLink',chat_id=self.chat,name='glow:'+str(uid),expire_date=ts+600,creates_join_request=True)
   link=r['invite_link'];self.db.put('glow_invites',{'link':link,'user_id':uid,'kind':kind,'expires':ts+600})
  self.send(uid,('<b>Пробный вход</b>\n\nПробный период начнётся после вступления. Пробный доступ доступен один раз.' if trial else '<b>Вход в GlowUp Club</b>')+'\n\nСсылка действует десять минут. Подай заявку с этого аккаунта — бот проверит доступ.',[[self.button('Открыть форум',url=link,style='success')],[self.button('🗓 Мой доступ','account')]])
 def join(self,q):
  if q['chat']['id']!=self.chat:return
  uid=q['from']['id'];link=q.get('invite_link',{}).get('invite_link','');u=self.user(uid)
  row=self.db.one('glow_invites',link='eq.'+link)
  # Leave unrelated admin-created links alone; never admit a different user via a personal link.
  if not row:return
  valid=bool(row['user_id']==uid and row['expires']>=int(time.time()) and u and not u['blocked'] and (self.active(u) or (row['kind']=='trial' and u['trial_started'] is None)))
  if not valid:
   self.api.call('declineChatJoinRequest',chat_id=self.chat,user_id=uid);return
  try:self.api.call('approveChatJoinRequest',chat_id=self.chat,user_id=uid)
  except RemoteError:
   member=self.api.call('getChatMember',chat_id=self.chat,user_id=uid)
   if member['status'] not in ['member','restricted']:raise
  self.db.action('joined',uid=uid,link=link)
  try:self.api.call('revokeChatInviteLink',chat_id=self.chat,invite_link=link)
  except RemoteError as e:
   if e.uncertain:raise
  self.send(uid,'Ты в GlowUp Club. Начни с закреплённой навигации.',[[self.button('🗓 Мой доступ','account')]])
 def membership(self,event):
  if event['chat']['id']!=self.chat:return
  member=event['new_chat_member'];uid=member['user']['id'];status=member['status']
  if status in ['left','kicked']:
   self.db.patch('glow_users',{'managed':False},id='eq.'+str(uid));return
  if status not in ['member','restricted']:return
  link=event.get('invite_link',{}).get('invite_link')
  if link and self.db.one('glow_invites',link='eq.'+link,user_id='eq.'+str(uid)):self.db.action('joined',uid=uid,link=link)
 def expire(self):
  ts=int(time.time())
  users=self.db.action('expired')
  for u in users:
   if self.active(u,ts):continue
   if self.admin(u['id']):continue
   member=self.api.call('getChatMember',chat_id=self.chat,user_id=u['id'])
   if member['status'] in ['administrator','creator']:
    self.db.patch('glow_users',{'managed':False},id='eq.'+str(u['id']));continue
   if member['status'] not in ['left','kicked']:
    self.api.call('banChatMember',chat_id=self.chat,user_id=u['id'],revoke_messages=False)
    # Keep banned until renewal; invite() explicitly unbans only an entitled user.
   self.db.patch('glow_users',{'managed':False},id='eq.'+str(u['id']))
   try:self.send(u['id'],'Срок доступа закончился. Можно продлить его здесь.',[[self.button('💎 Продлить доступ','plans',style='primary')]])
   except RemoteError:pass
   log.info('Срок закончился: user=%s',u['id'])
 def notify_admins(self,text,rows=None):
  for uid in self.cfg['admin_ids']:
   try:self.send(uid,text,rows)
   except RemoteError:log.warning('Не удалось доставить уведомление администратору %s',uid)
 def admin_panel(self,uid,mid=None):
  if not self.admin(uid):raise ValueError('Нет доступа к админке')
  s=self.db.action('stats');b=self.button
  text=f'<b>GlowUp · админ-панель</b>\n\nПользователи: {s["users"]}\nПлатный доступ: {s["active"]}\nПробный доступ сейчас: {s["trials"]}\nОплаты без возвратов: {s["payments"]}\nПолучено без возвратов: {s["stars"]} ⭐\nОткрытые обращения: {s["tickets"]}'
  rows=[[b('👥 Пользователи','a:users:0'),b('🔎 Найти по ID','a:find')],[b('⭐ Оплаты и возвраты','a:payments:0')],[b('💎 USDT · оплаты','a:crypto:0')],[b('💬 Обращения','a:tickets:0')],[b('🎟 Промокоды','a:promos')],[b('📣 Рассылка','a:broadcast')],[b('📊 Журнал действий','a:audit:0')],[b('⚙️ Тарифы и настройки','a:settings')],*self.back()]
  self.show(uid,text,rows,mid)
 def admin_user(self,uid,target,mid=None):
  u=self.user(target)
  if not u:raise ValueError('Пользователь не найден. Он должен сначала нажать /start.')
  b=self.button;text='<b>'+html.escape(u['name'])+'</b> · <code>'+str(target)+'</code>\n@'+html.escape(u['username'] or '—')+'\n\n'
  text+='Доступ: '+('без срока' if u['lifetime'] else self.date(u['access_until']) if u['access_until'] else 'нет')+'\nБлокировка: '+('да' if u['blocked'] else 'нет')
  text+='\nПробный вход: '+('использован' if u['trial_started'] is not None else 'не использован')
  rows=[[b('Выдать / продлить','a:grant:'+str(target),style='success')],[b('Отозвать доступ','a:revoke:'+str(target),style='danger')],[b('Разблокировать' if u['blocked'] else 'Заблокировать','a:block:'+str(target),style='danger')],[b('← Админка','admin')]]
  self.show(uid,text,rows,mid)
 def confirmation(self,uid,action,message,**data):
  ident=uuid.uuid4().hex[:16];self.flow(uid,mode='confirm',action=action,id=ident,**data)
  self.send(uid,'<b>Подтверждение</b>\n\n'+html.escape(message),[[self.button('Подтвердить','a:confirm:'+ident,style='danger')],[self.button('Отмена','a:cancel')]])
 def admin_callback(self,uid,data,mid):
  if not self.admin(uid):raise ValueError('Нет доступа к админке')
  parts=data.split(':');act=parts[1];arg=parts[2] if len(parts)>2 else '';b=self.button
  if act in ('crypto','cryptoorder','cryptoreview','cryptoon','cryptoprice'):
   from crypto import Crypto,amount,address,micro
   crypto=Crypto(self.db,self.cfg)
   if act=='crypto':
    page=max(0,int(arg or 0));orders=self.db.get('glow_crypto_orders',order='created_at.desc',offset=page*8,limit=8)
    rows=[[b(str(o['user_id'])+' · '+amount(o['received'])+'/'+amount(o['amount'])+' USDT · '+o['state'],'a:cryptoorder:'+o['id'])] for o in orders]
    if page:rows.append([b('←','a:crypto:'+str(page-1))])
    if len(orders)==8:rows.append([b('→','a:crypto:'+str(page+1))])
    rows.append([b('Включить / выключить','a:cryptoon')])
    for key,p in self.cfg['plans'].items():rows.append([b(p['name']+' · '+str(self.cfg.get('crypto',{}).get('usdt_prices',{}).get(key) or 'не задано')+' USDT','a:cryptoprice:'+key)])
    unmatched=self.db.get('glow_crypto_receipts',order_id='is.null',order='created_at.desc',limit=5)
    detail='\n\nПереводы без счёта: '+str(len(unmatched))+' последних.'
    for r in unmatched:detail+='\n<code>'+r['id']+'</code> · '+amount(r['amount'])+' USDT'
    detail+='\nДля привязки: /crypto_assign ХЕШ GLOWномер. Для уже выполненного возврата: /crypto_refund GLOWномер ХЕШ. Эти команды доступны только админам.'
    rows.append([b('← Админка','admin')]);self.show(uid,'<b>USDT · TON</b>\n'+('Включено' if crypto.enabled() else 'Выключено')+detail,rows,mid);return
   if act=='cryptoorder':
    o=self.db.one('glow_crypto_orders',id='eq.'+arg)
    if not o:raise ValueError('Счёт не найден')
    receipts=self.db.get('glow_crypto_receipts',order_id='eq.'+arg,limit=5)
    text='<b>Счёт USDT</b>\n<code>'+o['id']+'</code>\nПользователь: '+str(o['user_id'])+'\nСтатус: '+o['state']+'\nПолучено: '+amount(o['received'])+' / '+amount(o['amount'])+' USDT'
    rows=[[b('Пользователь','a:user:'+str(o['user_id']))]]
    for r in receipts:rows.append([b('Перевод '+amount(r['amount'])+' USDT',url='https://tonviewer.com/transaction/'+r['id'])])
    if not o['credited'] and o['received']>=o['amount']:rows.append([b('Подтвердить доступ','a:cryptoreview:'+o['id'])])
    rows.append([b('← USDT','a:crypto:0')]);self.show(uid,text,rows,mid);return
   if act=='cryptoreview':self.confirmation(uid,'crypto_approve','Подтвердить этот счёт по полученным переводам?',order=arg);return
   if act=='cryptoprice':self.flow(uid,mode='crypto_price',plan=arg);self.send(uid,'Отправь цену USDT, например 15.5. /cancel — отмена.');return
   if act=='cryptoon':
    enabled=not self.cfg.get('crypto',{}).get('enabled',False)
    if enabled:
     import os
     address(os.environ.get('CRYPTO_WALLET_ADDRESS',''))
     if not os.environ.get('CHECKOUT_BASE_URL','').startswith('https://'):raise ValueError('Сначала настрой HTTPS адрес сайта в .env')
     prices=self.cfg.get('crypto',{}).get('usdt_prices',{})
     if not any(v is not None for v in prices.values()):raise ValueError('Сначала задай цены USDT')
     for v in prices.values():
      if v is not None:micro(v)
    self.confirmation(uid,'crypto_enable','Включить криптооплату на отдельном сайте?' if enabled else 'Остановить создание новых счетов USDT?',enabled=enabled);return
  if act=='cancel':self.flow(uid);self.admin_panel(uid,mid);return
  if act=='user':self.admin_user(uid,int(arg),mid);return
  if act in ['users','payments','tickets','audit']:
   page=max(0,int(arg or 0));table={'users':'glow_users','payments':'glow_payments','tickets':'glow_tickets','audit':'glow_audit'}[act]
   filters={'order':'id.desc' if act in ['users','tickets','audit'] else 'created_at.desc','offset':page*8,'limit':8}
   if act=='tickets':filters['state']='eq.open'
   rows=self.db.get(table,**filters);buttons=[];lines=[]
   for row in rows:
    if act=='users':buttons.append([b(str(row['id'])+' · '+row['name'][:25],'a:user:'+str(row['id']))])
    elif act=='payments':
     token=row['order_id'];state='возвращено' if row['refunded'] else 'ожидает сверки возврата' if row.get('refund_pending') else 'оплачено'
     buttons.append([b(str(row['user_id'])+' · '+str(row['stars'])+' ⭐ · '+state,'a:payment:'+token)])
    elif act=='tickets':buttons.append([b('#'+str(row['id'])+' · '+row['text'][:35],'a:ticket:'+str(row['id']))])
    else:lines.append(self.date(row['created_at'])+' · '+html.escape(row['action'])+' · '+str(row['actor'] or 'бот'))
   nav=[]
   if page:nav.append(b('←','a:'+act+':'+str(page-1)))
   if len(rows)==8:nav.append(b('→','a:'+act+':'+str(page+1)))
   if nav:buttons.append(nav)
   buttons.append([b('← Админка','admin')]);self.show(uid,'<b>'+{'users':'Пользователи','payments':'Оплаты','tickets':'Обращения','audit':'Журнал'}[act]+'</b>\n\n'+('\n'.join(lines) if lines else 'Выбери запись.' if rows else 'Пока пусто.'),buttons,mid);return
  if act=='find':self.flow(uid,mode='find');self.send(uid,'Отправь Telegram ID пользователя. /cancel — отмена.');return
  if act=='grant':self.flow(uid,mode='grant',target=int(arg));self.send(uid,'Сколько дней добавить? Целое число от 1 до 3650. Для доступа без срока отправь forever.');return
  if act=='revoke':self.confirmation(uid,'revoke','Отозвать весь доступ у '+arg+'? Оплаты не возвращаются автоматически.',target=int(arg));return
  if act=='block':
   u=self.user(int(arg));self.confirmation(uid,'block',('Разблокировать ' if u['blocked'] else 'Заблокировать ')+arg+'?',target=int(arg),blocked=not u['blocked']);return
  if act=='payment':
   p=self.db.one('glow_payments',order_id='eq.'+arg)
   if not p:raise ValueError('Оплата не найдена')
   text='<b>Оплата</b>\n\nПользователь: <code>'+str(p['user_id'])+'</code>\nСумма: '+str(p['stars'])+' ⭐\nДата: '+self.date(p['created_at'])+'\nЗаказ: <code>'+p['order_id']+'</code>'
   rows=[[b('Пользователь','a:user:'+str(p['user_id']))]]
   if not p['refunded']:
    rows.append([b('Вернуть Stars','a:refund:'+arg,style='danger')])
    if p.get('refund_pending'):text+='\n\nВозврат ждёт сверки. Проверь историю Stars; повторный возврат не создаст нового платежа.'
   else:text+='\n\nВозвращено.'
   rows.append([b('← Оплаты','a:payments:0')]);self.show(uid,text,rows,mid);return
  if act=='refund':self.confirmation(uid,'refund','Вернуть всю сумму по заказу '+arg+' и пересчитать доступ?',order=arg);return
  if act=='ticket':
   t=self.db.one('glow_tickets',id='eq.'+arg)
   if not t:raise ValueError('Обращение не найдено')
   msgs=self.db.get('glow_ticket_messages',ticket_id='eq.'+arg,order='created_at.desc',limit=2)
   text='<b>Обращение #'+arg+'</b> · <code>'+str(t['user_id'])+'</code>\n\n'+html.escape(short(t['text'],2200))
   for m in reversed(msgs):
    if m['actor']!=t['user_id']:text+='\n\nОтвет: '+html.escape(short(m['text'],500))
   self.show(uid,text,[[b('Ответить','a:reply:'+arg)],[b('Закрыть','a:close:'+arg)],[b('Пользователь','a:user:'+str(t['user_id']))],[b('← Обращения','a:tickets:0')]],mid);return
  if act=='reply':self.flow(uid,mode='reply',ticket=int(arg));self.send(uid,'Напиши ответ пользователю. До 2500 символов.');return
  if act=='close':self.db.patch('glow_tickets',{'state':'closed'},id='eq.'+arg);self.db.put('glow_audit',{'actor':uid,'action':'ticket_closed','details':{'ticket':arg}});self.admin_callback(uid,'a:tickets:0',mid);return
  if act=='promos':
   rows=self.db.get('glow_promos',order='code',limit=30);text='<b>Промокоды</b>\n\n'+'\n'.join(html.escape(p['code'])+' · −'+str(p['percent'])+'% · '+str(p['used'])+'/'+str(p['max_uses'])+(' · выключен' if not p['enabled'] else '') for p in rows)
   buttons=[[b('Создать промокод','a:newpromo')]]+[[b(('Выключить ' if p['enabled'] else 'Включить ')+p['code'],'a:togglepromo:'+p['code'])] for p in rows]+[[b('← Админка','admin')]]
   self.show(uid,text,buttons,mid);return
  if act=='newpromo':self.flow(uid,mode='newpromo');self.send(uid,'Отправь: КОД ПРОЦЕНТ ЛИМИТ\nНапример: GLOW10 10 50\nОдин аккаунт использует код один раз. Место резервируется при создании счёта.');return
  if act=='togglepromo':
   p=self.db.one('glow_promos',code='eq.'+arg);self.db.patch('glow_promos',{'enabled':not p['enabled']},code='eq.'+arg);self.db.put('glow_audit',{'actor':uid,'action':'promo_toggle','details':{'code':arg}});self.admin_callback(uid,'a:promos',mid);return
  if act=='broadcast':
   jobs=self.db.get('glow_campaigns',order='created_at.desc',limit=5);rows=[[b('Новая рассылка','a:newbroadcast')]]
   for j in jobs:
    label=j['state']+' · '+str(j['sent'])+' доставлено / '+str(j['failed'])+' ошибок'
    rows.append([b(label,'a:campaign:'+j['id'])])
   rows.append([b('← Админка','admin')]);self.show(uid,'<b>Рассылки</b>\n\nОтправляются пользователям, которые запускали бота. Заблокировавшие бота будут пропущены.',rows,mid);return
  if act=='newbroadcast':self.flow(uid,mode='broadcast');self.send(uid,'Напиши текст рассылки до 2500 символов. Сначала покажу предпросмотр, отправка только после подтверждения.');return
  if act=='campaign':
   j=self.db.one('glow_campaigns',id='eq.'+arg);rows=[]
   if j['state']=='running':rows.append([b('Остановить','a:stopcampaign:'+arg,style='danger')])
   rows.append([b('← Рассылки','a:broadcast')]);self.show(uid,'<b>Рассылка</b>\n\n'+html.escape(j['text'])+'\n\nСтатус: '+j['state']+'\nДоставлено: '+str(j['sent'])+'\nОшибок: '+str(j['failed']),rows,mid);return
  if act=='stopcampaign':self.db.patch('glow_campaigns',{'state':'cancelled'},id='eq.'+arg,state='eq.running');self.admin_callback(uid,'a:broadcast',mid);return
  if act=='settings':
   rows=[]
   for key,p in self.cfg['plans'].items():rows.append([b(p['name']+' · '+str(p['stars'] or 'не задано')+' ⭐','a:price:'+key)])
   rows+=[[b('Остановить продажи' if self.cfg['sales_enabled'] else 'Открыть продажи','a:sales',style='danger' if self.cfg['sales_enabled'] else 'success')],[b('Настроить обложку','a:cover')],[b('← Админка','admin')]]
   self.show(uid,'<b>Настройки</b>\n\nПробный период: '+str(self.cfg['trial_minutes'])+' минут.\nГруппа: <code>'+str(self.chat)+'</code>\nЦены указаны в Stars. Автоматического списания нет.\nОстальные тексты и эмодзи настраиваются в config.json.',rows,mid);return
  if act=='price':self.flow(uid,mode='price',plan=arg);self.send(uid,'Отправь цену в Stars: целое число от 1 до 1000000.');return
  if act=='sales':
   if not self.cfg['sales_enabled'] and not any(isinstance(p['stars'],int) and p['stars']>0 for p in self.cfg['plans'].values()):raise ValueError('Сначала задай цену хотя бы одного тарифа.')
   self.confirmation(uid,'sales','Остановить продажи?' if self.cfg['sales_enabled'] else 'Открыть продажи с текущими ценами?',enabled=not self.cfg['sales_enabled']);return
  if act=='cover':self.flow(uid,mode='cover');self.send(uid,'Пришли свою картинку как фото. Она станет обложкой главного меню.');return
  if act=='confirm':
   f=self.user(uid)['flow']
   if f.get('mode')!='confirm' or f.get('id')!=arg:raise ValueError('Подтверждение устарело.')
   op=f['action']
   if op.startswith('crypto_'):
    from crypto import Crypto
    if op=='crypto_enable':
     self.cfg.setdefault('crypto',{})['enabled']=f['enabled'];save_config(self.cfg)
    else:
     action={'crypto_approve':'approve','crypto_assign':'assign','crypto_refund':'refund_record'}[op]
     result=Crypto(self.db,self.cfg).rpc(action,actor=uid,**{k:v for k,v in f.items() if k in ('order','receipt','tx')})
     if result.get('paid'):self.send(result['uid'],'Оплата подтверждена. Доступ в клуб добавлен.',[[b('Мой доступ','account')]])
   elif op=='grant':self.db.action('grant',uid=f['target'],actor=uid,id='admin:'+arg,days=f['days'])
   elif op=='revoke':self.db.action('revoke',uid=f['target'],actor=uid)
   elif op=='block':self.db.action('block',uid=f['target'],actor=uid,blocked=f['blocked'])
   elif op=='refund':
    p=self.db.one('glow_payments',order_id='eq.'+f['order'])
    if not p or p['refunded']:raise ValueError('Оплата отсутствует или уже возвращена.')
    self.db.patch('glow_payments',{'refund_pending':True},order_id='eq.'+f['order'])
    try:self.api.call('refundStarPayment',user_id=p['user_id'],telegram_payment_charge_id=p['charge'])
    except RemoteError as e:
     if 'already refunded' not in str(e).lower() and 'charge_already_refunded' not in str(e).lower():
      if not e.uncertain:self.db.patch('glow_payments',{'refund_pending':False},order_id='eq.'+f['order'])
      raise
    self.db.action('refund',uid=p['user_id'],actor=uid,charge=p['charge'])
   elif op=='broadcast':
    self.db.put('glow_campaigns',{'id':arg,'actor':uid,'text':f['text'],'state':'running'},conflict='id',ignore=True)
   elif op=='sales':
    self.cfg['sales_enabled']=f['enabled'];save_config(self.cfg);self.db.put('glow_audit',{'actor':uid,'action':'sales','details':{'enabled':f['enabled']}})
   self.flow(uid);self.send(uid,'Готово.',[[b('⚙️ Админка','admin')]]);return
  raise ValueError('Неизвестное действие')
 def text(self,msg):
  uid=msg['from']['id'];text=msg.get('text','').strip();u=self.user(uid);f=u['flow'];mode=f.get('mode')
  if text.startswith('/start web_'):
   from crypto import Crypto,digest
   challenge=text[len('/start web_'):].strip()
   if not challenge or len(challenge)>64:raise ValueError('Неверный код подключения')
   Crypto(self.db,self.cfg).rpc('link',uid=uid,challenge=digest(challenge))
   self.send(uid,'Telegram подключён. Вернись на страницу, которую ты открыл.',self.back());return
  if self.admin(uid) and text.startswith(('/crypto_assign ','/crypto_refund ')):
   import re
   parts=text.split()
   if len(parts)!=3:raise ValueError('Нужны номер счёта и хеш перевода')
   if parts[0]=='/crypto_assign':receipt,order=parts[1:];op='crypto_assign';args={'receipt':receipt,'order':order}
   else:order,tx=parts[1:];receipt=tx;op='crypto_refund';args={'order':order,'tx':tx}
   if not re.fullmatch('GLOW[0-9a-f]{32}',order) or not re.fullmatch('[0-9a-fA-F]{64}',receipt):raise ValueError('Проверь номер счёта и хеш')
   self.confirmation(uid,op,'Привязать фактически полученный перевод к счёту?' if op=='crypto_assign' else 'Отметить уже отправленный возврат и отозвать доступ? Деньги эта команда не отправляет.',**args);return
  if text in ['/cancel','/start','/menu']:
   self.flow(uid);self.home(uid);return
  if text=='/admin':self.admin_panel(uid);return
  if text in ['/terms','/paysupport']:
   if text=='/terms':self.send(uid,html.escape(self.cfg['terms']),self.back())
   else:self.flow(uid,mode='support');self.send(uid,'Опиши вопрос по оплате. Укажи дату и сумму. Токены, пароли и данные карты не присылай.')
   return
  if text=='/id':self.send(uid,'Твой ID: <code>'+str(uid)+'</code>');return
  if text=='/privacy':self.send(uid,'Бот хранит Telegram ID, имя, username, историю оплат, срок доступа и сообщения в поддержку. Данные нужны для выдачи доступа и решения обращений. Они хранятся в закрытой базе Supabase. По вопросам данных напиши в поддержку.',self.back());return
  if mode=='support':
   if not text or len(text.encode('utf-16-le'))>5000:raise ValueError('Опиши вопрос текстом до 2500 символов.')
   r=self.db.action('ticket',uid=uid,text=text,id='ticket:'+str(uid)+':'+str(msg['message_id']))
   self.flow(uid)
   if r.get('id'):
    self.notify_admins('Новое обращение #'+str(r['id'])+' · <code>'+str(uid)+'</code>\n\n'+html.escape(text),[[self.button('Открыть','a:ticket:'+str(r['id']))]])
   self.send(uid,'Обращение сохранено. Ответ придёт сюда.',self.back());return
  if mode=='promo':
   code=text.upper()
   import re
   if not re.fullmatch('[A-Z0-9_-]{3,24}',code):raise ValueError('Код: 3–24 латинские буквы, цифры, дефис или подчёркивание.')
   self.flow(uid,mode='promo_ready',code=code)
   rows=[[self.button(p['name'],'discount:'+key)] for key,p in self.cfg['plans'].items() if isinstance(p['stars'],int) and p['stars']>0]
   self.send(uid,'Выбери тариф. Проверка кода и окончательная цена появятся при создании счёта.',rows+self.back());return
  if not self.admin(uid):self.home(uid);return
  if mode=='crypto_price':
   from crypto import micro,amount
   price=amount(micro(text.replace(',','.')))
   self.cfg.setdefault('crypto',{}).setdefault('usdt_prices',{})[f['plan']]=price;save_config(self.cfg);self.flow(uid)
   self.db.put('glow_audit',{'actor':uid,'action':'crypto_price','details':{'plan':f['plan'],'price':price}})
   self.admin_callback(uid,'a:crypto:0',None);return
  if mode=='find':
   if not text.isdigit():raise ValueError('Нужен числовой Telegram ID.')
   self.flow(uid);self.admin_user(uid,int(text));return
  if mode=='grant':
   days=None if text.lower()=='forever' else int(text)
   if days is not None and not 1<=days<=3650:raise ValueError('От 1 до 3650 дней.')
   self.confirmation(uid,'grant','Добавить '+('доступ без срока' if days is None else str(days)+' дней')+' пользователю '+str(f['target'])+'?',target=f['target'],days=days);return
  if mode=='reply':
   if not text or len(text.encode('utf-16-le'))>5000:raise ValueError('До 2500 символов.')
   ticket=self.db.one('glow_tickets',id='eq.'+str(f['ticket']))
   mid='reply:'+str(uid)+':'+str(msg['message_id'])
   if not self.db.one('glow_ticket_messages',id='eq.'+mid):
    self.send(ticket['user_id'],'<b>Ответ поддержки · #'+str(ticket['id'])+'</b>\n\n'+html.escape(text))
    self.db.put('glow_ticket_messages',{'id':mid,'ticket_id':ticket['id'],'actor':uid,'text':text})
   self.flow(uid);self.send(uid,'Ответ отправлен. Обращение можно закрыть отдельно.',[[self.button('Обращение','a:ticket:'+str(ticket['id']))]]);return
  if mode=='newpromo':
   import re
   bits=text.upper().split()
   if len(bits)!=3 or not re.fullmatch('[A-Z0-9_-]{3,24}',bits[0]):raise ValueError('Формат: GLOW10 10 50')
   percent,limit=map(int,bits[1:])
   if not 1<=percent<=90 or not 1<=limit<=100000:raise ValueError('Процент 1–90; лимит 1–100000.')
   self.db.put('glow_promos',{'code':bits[0],'percent':percent,'max_uses':limit});self.db.put('glow_audit',{'actor':uid,'action':'promo_created','details':{'code':bits[0]}})
   self.flow(uid);self.admin_callback(uid,'a:promos',None);return
  if mode=='broadcast':
   if not text or len(text.encode('utf-16-le'))>5000:raise ValueError('От 1 до 2500 символов.')
   self.confirmation(uid,'broadcast','Рассылка всем пользователям бота:\n\n'+text,text=text);return
  if mode=='price':
   price=int(text)
   if not 1<=price<=1000000:raise ValueError('Цена от 1 до 1000000 Stars.')
   self.cfg['plans'][f['plan']]['stars']=price;save_config(self.cfg);self.flow(uid)
   self.db.put('glow_audit',{'actor':uid,'action':'price_changed','details':{'plan':f['plan'],'stars':price}});self.admin_callback(uid,'a:settings',None);return
  if mode=='cover' and msg.get('photo'):
   self.cfg['welcome_photo_file_id']=msg['photo'][-1]['file_id'];save_config(self.cfg);self.flow(uid);self.home(uid);return
  self.admin_panel(uid)
 def callback(self,q):
  uid=q['from']['id'];data=q['data'];mid=q.get('message',{}).get('message_id')
  if q.get('message',{}).get('chat',{}).get('type')!='private':return
  self.api.call('answerCallbackQuery',callback_query_id=q['id'])
  self.remember(q['from'])
  if data=='home':self.flow(uid);self.home(uid,mid)
  elif data=='plans':self.plans(uid,mid)
  elif data=='account':self.account(uid,mid)
  elif data=='trial':self.invite(uid,True)
  elif data=='enter':self.invite(uid)
  elif data=='catalog':
   text='<b>Что внутри GlowUp Club</b>'
   for x in self.cfg['catalog']:text+='\n\n<b>'+html.escape(x['title'])+'</b>\n'+html.escape(x['text'])
   self.show(uid,text,self.back(),mid)
  elif data=='support':self.flow(uid,mode='support');self.send(uid,'Напиши одним сообщением, что случилось. По оплате укажи дату и сумму. /cancel — отмена.')
  elif data=='promo':self.flow(uid,mode='promo');self.send(uid,'Отправь промокод. /cancel — отмена.')
  elif data.startswith('buy:'):self.invoice(uid,data[4:])
  elif data.startswith('discount:'):
   flow=self.user(uid)['flow']
   if flow.get('mode')!='promo_ready':raise ValueError('Введи промокод заново.')
   self.invoice(uid,data.split(':',1)[1],flow['code']);self.flow(uid)
  elif data=='admin':self.admin_panel(uid,mid)
  elif data.startswith('a:'):self.admin_callback(uid,data,mid)
 def update(self,event):
  if event.get('pre_checkout_query'):self.precheckout(event['pre_checkout_query']);return
  if event.get('chat_join_request'):self.join(event['chat_join_request']);return
  if event.get('chat_member'):self.membership(event['chat_member']);return
  if event.get('callback_query'):self.callback(event['callback_query']);return
  msg=event.get('message',{})
  if msg.get('chat',{}).get('type')!='private' or not msg.get('from'):return
  self.remember(msg['from'])
  if msg.get('successful_payment'):self.payment(msg)
  elif msg.get('refunded_payment'):
   p=msg['refunded_payment'];self.db.action('refund',uid=msg['from']['id'],charge=p['telegram_payment_charge_id'])
  else:self.text(msg)
 def campaign_tick(self):
  j=self.db.one('glow_campaigns',state='eq.running',order='created_at')
  if not j:return
  users=self.db.get('glow_users',id='gt.'+str(j['cursor']),blocked='eq.false',order='id',limit=1)
  if not users:self.db.patch('glow_campaigns',{'state':'done'},id='eq.'+j['id']);return
  uid=users[0]['id'];success=True
  # Record cursor before delivery: broadcast retries do not spam the same user after a crash.
  self.db.patch('glow_campaigns',{'cursor':uid},id='eq.'+j['id'],state='eq.running')
  try:self.send(uid,html.escape(j['text']))
  except RemoteError:success=False
  self.db.patch('glow_campaigns',{'sent':j['sent']+int(success),'failed':j['failed']+int(not success)},id='eq.'+j['id'])

def short(text,units):return text.encode('utf-16-le')[:units*2].decode('utf-16-le','ignore')

def load_env():
 p=ROOT/'.env'
 if p.exists():
  for line in p.read_text(encoding='utf-8-sig').splitlines():
   line=line.strip()
   if not line or line.startswith('#') or '=' not in line:continue
   key,value=line.split('=',1);os.environ.setdefault(key.strip(),value.strip().strip('\"').strip("'"))

def save_config(cfg):
 cfg.setdefault('screen_photo_file_ids',{})
 p=ROOT/'config.json';tmp=ROOT/'config.json.tmp';tmp.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(tmp,p)
 if GLOBAL_DB is not None:
  GLOBAL_DB.put('glow_runtime',{'key':'settings','value':{k:cfg[k] for k in ['plans','sales_enabled','welcome_photo_file_id','screen_photo_file_ids','crypto','admin_ids','trial_minutes','chat_id','description','terms','catalog'] if k in cfg}},conflict='key')
GLOBAL_DB=None

def environment_config(cfg):
 if os.environ.get('CHAT_ID'):cfg['chat_id']=int(os.environ['CHAT_ID'])
 if os.environ.get('ADMIN_IDS'):
  cfg['admin_ids']=[int(x.strip()) for x in os.environ['ADMIN_IDS'].split(',') if x.strip()]
 if os.environ.get('TRIAL_MINUTES'):cfg['trial_minutes']=int(os.environ['TRIAL_MINUTES'])
 return cfg

def config():
 p=ROOT/'config.json'
 cfg=json.loads((p if p.exists() else ROOT/'config.example.json').read_text(encoding='utf-8'))
 cfg=environment_config(cfg)
 if not isinstance(cfg.get('chat_id'),int) or not str(cfg['chat_id']).startswith('-100'):raise ValueError('Укажи chat_id супергруппы')
 if not isinstance(cfg.get('admin_ids'),list) or any(not isinstance(x,int) or x<=0 for x in cfg['admin_ids']):raise ValueError('admin_ids — список положительных числовых ID')
 if not 1<=cfg['trial_minutes']<=60:raise ValueError('trial_minutes: от 1 до 60')
 for key,p in cfg['plans'].items():
  if p['days'] is not None and (not isinstance(p['days'],int) or not 1<=p['days']<=3650):raise ValueError('Неверный срок тарифа '+key)
  if p['stars'] is not None and (not isinstance(p['stars'],int) or not 1<=p['stars']<=1000000):raise ValueError('Неверная цена '+key)
 return cfg

def setup():
 if (ROOT/'config.json').exists():print('config.json уже существует. Настройки меняются в админке или в этом файле.');return
 cfg=json.loads((ROOT/'config.example.json').read_text(encoding='utf-8'))
 print('GlowUp Club. Цены указываются в Stars; пустая цена отключает тариф.')
 raw=input('Твой Telegram ID (пусто = привязать себя через код после запуска): ').strip()
 if raw:cfg['admin_ids']=[int(raw)]
 for key,p in cfg['plans'].items():
  value=input('Цена '+p['name']+' в Stars (пусто = позже): ').strip()
  if value:p['stars']=int(value)
 cfg['sales_enabled']=False;save_config(cfg)
 if not (ROOT/'.env').exists():
  url=input('Project URL Supabase: ').strip();service=getpass.getpass('Supabase service_role key (скрыт): ').strip();token=getpass.getpass('Токен нового платёжного бота (скрыт): ').strip()
  if any('\n' in x or '\r' in x for x in [url,service,token]):raise ValueError('Неверное значение')
  (ROOT/'.env').write_text('SUPABASE_URL='+url+'\nSUPABASE_SERVICE_ROLE_KEY='+service+'\nBOT_TOKEN='+token+'\n',encoding='utf-8')
  try:os.chmod(ROOT/'.env',0o600)
  except OSError:pass
 print('Настройки сохранены. Выполни schema.sql в Supabase SQL Editor. Потом py bot.py check и py bot.py run. Продажи пока закрыты.')

def check(club):
 me=club.api.call('getMe');club.bot_id=me['id']
 if club.api.call('getWebhookInfo').get('url'):raise ValueError('У бота настроен webhook. Используй отдельного бота для оплаты; webhook не удаляется.')
 chat=club.api.call('getChat',chat_id=club.chat);member=club.api.call('getChatMember',chat_id=club.chat,user_id=me['id'])
 if chat.get('type')!='supergroup' or not chat.get('is_forum'):raise ValueError('Нужна супергруппа с темами')
 if member.get('status')!='administrator' or not member.get('can_invite_users') or not member.get('can_restrict_members'):raise ValueError('Боту нужны права администратора: приглашение и блокировка участников')
 if chat.get('username'):raise ValueError('Платный форум должен быть приватным. Публичный адрес позволяет входить без проверки бота.')
 stats=club.db.action('stats');print('Проверено:',chat['title'],'/ @'+me['username'],'· Supabase подключён · пользователей:',stats['users'],flush=True)

def run(club):
 owner=uuid.uuid4().hex
 if not club.db.lease(owner):
  if not os.environ.get('RAILWAY_ENVIRONMENT_ID'):raise ValueError('Уже работает другой экземпляр бота. Если он остановлен, подожди 90 секунд.')
  print('Жду освобождения предыдущего процесса после перезапуска Railway…',flush=True)
  deadline=time.monotonic()+120
  while not club.db.lease(owner):
   if time.monotonic()>deadline:raise ValueError('Другой экземпляр всё ещё работает. Оставь одну реплику Railway и выключи локальный запуск.')
   time.sleep(2)
 lease_lost=threading.Event();stop=threading.Event()
 def heartbeat():
  while not stop.wait(20):
   try:
    if not club.db.lease(owner):lease_lost.set();return
   except RemoteError:lease_lost.set();return
 thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
 runtime=club.db.one('glow_runtime',key='eq.offset');offset=int(runtime['value']) if runtime else 0
 claim_code=uuid.uuid4().hex[:16];claim_expiry=int(time.time())+600
 if not club.cfg['admin_ids']:print('Для привязки админа отправь боту в личку в течение 10 минут: /claim '+claim_code,flush=True)
 club.api.call('setMyCommands',commands=[{'command':'start','description':'Главное меню'},{'command':'id','description':'Мой Telegram ID'},{'command':'paysupport','description':'Помощь с оплатой'},{'command':'terms','description':'Условия доступа'},{'command':'privacy','description':'Данные и приватность'},{'command':'cancel','description':'Отменить ввод'}])
 print('Бот работает. Для пробного доступа и удаления по сроку процесс должен оставаться включённым.',flush=True)
 last_expire=0;last_campaign=0;last_crypto=0
 try:
  while not lease_lost.is_set():
   try:
    events=club.api.call('getUpdates',offset=offset,timeout=10,limit=30,allowed_updates=['message','callback_query','pre_checkout_query','chat_join_request','chat_member'])
    for event in events:
     if lease_lost.is_set():break
     msg=event.get('message',{});sender=msg.get('from',{});uid=sender.get('id');text=msg.get('text','')
     try:
      if not club.cfg['admin_ids'] and msg.get('chat',{}).get('type')=='private' and text.startswith('/claim '):
       import secrets
       if int(time.time())>claim_expiry or not secrets.compare_digest(text[7:].strip(),claim_code):raise ValueError('Код неверный или истёк.')
       member=club.api.call('getChatMember',chat_id=club.chat,user_id=uid)
       if member['status'] not in ['creator','administrator']:raise ValueError('Привязать админку может администратор GlowUp Club.')
       club.remember(sender);club.cfg['admin_ids']=[uid];save_config(club.cfg);club.admin_panel(uid)
      else:club.update(event)
     except (ValueError,TypeError) as e:
      target=uid or event.get('callback_query',{}).get('from',{}).get('id')
      if target:
       try:club.send(target,'Не получилось: '+html.escape(str(e))[:700])
       except RemoteError:pass
     except RemoteError as e:
      log.warning('Не удалось обработать update=%s: %s',event['update_id'],str(e))
      # Durable financial/membership events must be retried, never silently acknowledged.
      critical=bool(event.get('pre_checkout_query') or event.get('chat_join_request') or event.get('chat_member') or msg.get('successful_payment') or msg.get('refunded_payment'))
      if critical:raise
      target=uid or event.get('callback_query',{}).get('from',{}).get('id')
      if target:
       try:club.send(target,'Не удалось выполнить действие. Если оплата уже прошла, открой «Мой доступ». По вопросам возврата напиши в поддержку.')
       except RemoteError:pass
     offset=event['update_id']+1;club.db.put('glow_runtime',{'key':'offset','value':offset},conflict='key')
    now=time.monotonic()
    if now-last_expire>15:club.expire();last_expire=now
    if now-last_campaign>3:club.campaign_tick();last_campaign=now
    if now-last_crypto>30:
     last_crypto=now
     try:
      from crypto import Crypto
      for receipt in Crypto(club.db,club.cfg).scan():club.send(receipt['uid'],'Оплата подтверждена. Доступ в клуб добавлен.',[[club.button('Мой доступ','account')]])
     except Exception as e:log.warning('Проверка USDT: %s',str(e))
   except RemoteError as e:
    log.warning('Сбой связи: %s',str(e));stop.wait(min(30,max(3,e.retry_after)))
  raise ValueError('Связь с Supabase потеряна или аренда процесса перехвачена. Бот остановлен; после проверки сети перезапусти его.')
 finally:stop.set();thread.join(timeout=1)

def main():
 global GLOBAL_DB
 logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
 load_env();command=sys.argv[1] if len(sys.argv)>1 else 'run'
 if command=='setup':setup();return
 if command not in ['run','check']:raise ValueError('Команды: setup, check, run')
 cfg=config();token=os.environ.get('BOT_TOKEN') or getpass.getpass('Токен бота: ')
 url=os.environ.get('SUPABASE_URL','');key=os.environ.get('SUPABASE_SERVICE_ROLE_KEY','')
 if not url or not key or not token:raise ValueError('Заполни .env: BOT_TOKEN, SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY')
 db=Database(url,key);GLOBAL_DB=db
 remote=db.one('glow_runtime',key='eq.settings')
 if remote:cfg.update(remote['value'])
 cfg=environment_config(cfg)
 club=Club(Telegram(token),db,cfg);check(club)
 if command=='run':run(club)
if __name__=='__main__':
 try:main()
 except KeyboardInterrupt:print('\nБот остановлен. База Supabase сохранена.')
 except (ValueError,RemoteError,OSError) as e:print('Ошибка:',str(e));sys.exit(1)
