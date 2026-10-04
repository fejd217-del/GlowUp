"""USDT-on-TON checkout. TonAPI is the trusted read-only blockchain indexer.
No private wallet key is used. Native TON or other tokens are never accepted as USDT.
"""
import base64,binascii,hashlib,json,os,re,time,urllib.parse,uuid
from decimal import Decimal,InvalidOperation
from bot import http,RemoteError
USDT_MASTER='EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs'

def crc16(data):
 crc=0
 for byte in data:
  crc^=byte<<8
  for _ in range(8):crc=((crc<<1)^0x1021 if crc&0x8000 else crc<<1)&0xffff
 return crc.to_bytes(2,'big')

def address(value):
 if re.fullmatch(r'-?\d+:[0-9a-fA-F]{64}',value):
  wc,h=value.split(':');wc=int(wc)
  if wc!=0:raise ValueError('Поддерживается адрес TON mainnet workchain 0')
  return str(wc)+':'+h.lower()
 try:data=base64.urlsafe_b64decode(value+'='*((-len(value))%4))
 except (ValueError,binascii.Error):raise ValueError('Неверный TON-адрес') from None
 if len(data)!=36 or crc16(data[:34])!=data[34:]:raise ValueError('Неверный TON-адрес или контрольная сумма')
 if data[0]&0x80:raise ValueError('Нужен mainnet-адрес, не testnet')
 if data[0] not in (0x11,0x51) or data[1]!=0:raise ValueError('Неподдерживаемый TON-адрес')
 return '0:'+data[2:34].hex()

def micro(value):
 try:n=Decimal(str(value))*1000000
 except InvalidOperation:raise ValueError('Неверная цена USDT') from None
 if not n.is_finite() or n!=n.to_integral_value() or not 0<n<=1000000*1000000:raise ValueError('Цена USDT: положительное число, максимум 6 знаков после точки')
 return int(n)

def amount(n):return format(Decimal(n)/1000000,'f').rstrip('0').rstrip('.') if n%1000000 else str(n//1000000)

def digest(value):return hashlib.sha256(value.encode()).hexdigest()

def payment_links(wallet,total,comment):
 query=urllib.parse.urlencode({'jetton':USDT_MASTER,'amount':str(total),'text':comment})
 return {'tonkeeper':'https://app.tonkeeper.com/transfer/'+urllib.parse.quote(wallet,safe=':')+'?'+query,'ton':'ton://transfer/'+urllib.parse.quote(wallet,safe=':')+'?'+query}

class Crypto:
 def __init__(self,db,cfg):
  self.db=db;self.cfg=cfg;self.wallet=os.environ.get('CRYPTO_WALLET_ADDRESS','');self.api_key=os.environ.get('TONAPI_KEY','')
 def enabled(self):
  return bool(self.cfg.get('crypto',{}).get('enabled') and self.wallet and os.environ.get('CHECKOUT_BASE_URL','').startswith('https://'))
 def validate(self):
  if not self.enabled():raise ValueError('Криптооплата пока не включена')
  return address(self.wallet)
 def rpc(self,action,**args):return self.db.request('rpc/glow_crypto_action',{'action':action,'args':args},'POST')
 def api(self,path):
  headers={'Authorization':'Bearer '+self.api_key} if self.api_key else {}
  return http('https://tonapi.io/v2/'+path,None,headers,20,'GET')
 def order(self,uid,plan):
  wallet=self.validate();u=self.db.one('glow_users',id='eq.'+str(uid))
  if not u or u['blocked']:raise ValueError('Нет доступа к покупке. Напиши в поддержку.')
  p=self.cfg['plans'].get(plan);price=self.cfg.get('crypto',{}).get('usdt_prices',{}).get(plan)
  if not p or price is None:raise ValueError('Этот тариф пока недоступен')
  # Reuse the user's open invoice to avoid accidental duplicate transfers.
  old=self.db.one('glow_crypto_orders',user_id='eq.'+str(uid),plan='eq.'+plan,state='in.(pending,underpaid)',expires='gt.'+str(int(time.time())+60),recipient='eq.'+wallet,order='created_at.desc')
  if old:return old
  oid='GLOW'+uuid.uuid4().hex;ts=int(time.time())
  row=dict(id=oid,user_id=uid,plan=plan,days=p['days'],amount=micro(price),recipient=wallet,master=address(USDT_MASTER),expires=ts+1800)
  return self.db.put('glow_crypto_orders',row)[0]
 def match(self,tx,expected_wallet=None,expected_jetton_wallet=None):
  """Accept raw successful USDT notifications from the official recipient jetton wallet."""
  from tonsdk.boc import Cell
  wallet=expected_wallet or address(self.wallet)
  try:
   if not expected_jetton_wallet or tx.get('success') is not True or tx.get('aborted') is not False or tx.get('destroyed') is not False or tx.get('out_msgs'):return []
   if address(tx['account']['address'])!=wallet:return []
   msg=tx['in_msg']
   if msg.get('bounced') is not False or address(msg['source']['address'])!=expected_jetton_wallet or address(msg['destination']['address'])!=wallet:return []
   txid=tx['hash'].lower()
   if not re.fullmatch('[0-9a-f]{64}',txid):return []
   body=Cell.one_from_boc(bytes.fromhex(msg['raw_body'])).begin_parse()
   if body.read_uint(32)!=0x7362d09c:return []
   body.read_uint(64);total=body.read_coins();sender=body.read_msg_addr()
   if total<=0 or sender is None:return []
   sender=address(sender.to_string(False))
   payload=body.read_ref().begin_parse() if body.read_bit() else body
   comment=None
   if len(payload.bits)>=32 and payload.read_uint(32)==0:
    raw=b''
    for _ in range(4):
     if len(payload.bits)%8:break
     raw+=payload.read_bytes(len(payload.bits)//8)
     if len(raw)>128:break
     if payload.ref_offset>=len(payload.refs):
      candidate=raw.decode('utf-8',errors='replace')
      if re.fullmatch('GLOW[0-9a-f]{32}',candidate):comment=candidate
      break
     payload=payload.read_ref().begin_parse()
   return [dict(id=txid,event_id=txid,order=comment,amount=total,recipient=wallet,master=address(USDT_MASTER),sender=sender,chain_time=int(tx['utime']))]
  except Exception:return [] # Untrusted malformed BOC must never grant access or stop the bot.
 def scan(self):
  if not self.wallet or not os.environ.get('CHECKOUT_BASE_URL','').startswith('https://'):return []
  wallet=address(self.wallet);master=address(USDT_MASTER)
  result=self.api('blockchain/accounts/'+urllib.parse.quote(master,safe=':')+'/methods/get_wallet_address?'+urllib.parse.urlencode({'args':wallet}))
  if result.get('success') is not True:raise ValueError('Не удалось проверить USDT-кошелёк')
  from tonsdk.boc import Cell
  stack=result.get('stack',[])
  if len(stack)!=1:raise ValueError('Неверный ответ get_wallet_address')
  encoded=stack[0].get('cell') or stack[0].get('slice')
  if not encoded:raise ValueError('Нет адреса USDT-кошелька в ответе')
  try:raw=bytes.fromhex(encoded)
  except ValueError:raw=base64.b64decode(encoded)
  derived=Cell.one_from_boc(raw).begin_parse().read_msg_addr()
  if derived is None:raise ValueError('Пустой адрес USDT-кошелька')
  jetton_wallet=address(derived.to_string(False))
  checkpoint=self.db.one('glow_runtime',key='eq.crypto_scan');cursor=checkpoint['value'] if checkpoint else {}
  params={'limit':100,'sort_order':'desc'}
  if cursor.get('before'):params['before_lt']=cursor['before']
  txs=self.api('blockchain/accounts/'+urllib.parse.quote(wallet,safe=':')+'/transactions?'+urllib.parse.urlencode(params)).get('transactions',[])
  open_orders=self.db.get('glow_crypto_orders',credited='eq.false',order='created_at.asc',limit=1)
  start=min(int(time.time())-7*86400,int(open_orders[0]['created_at'])-30) if open_orders else int(time.time())-7*86400
  since=int(cursor.get('since') or start);paid=[]
  for tx in txs:
   if int(tx.get('utime',0))<since:continue
   for transfer in self.match(tx,wallet,jetton_wallet):
    r=self.rpc('deposit',**transfer)
    if r.get('paid'):paid.append(r)
  more=len(txs)==100 and int(txs[-1].get('utime',0))>=since
  self.db.put('glow_runtime',{'key':'crypto_scan','value':{'since':since,'before':txs[-1]['lt']} if more else {}},conflict='key')
  return paid
