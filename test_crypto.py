import copy,time,unittest
from tonsdk.boc import begin_cell
from tonsdk.utils import Address
from crypto import Crypto,address,micro,amount,crc16,USDT_MASTER,payment_links
W='0:'+'11'*32;J='0:'+'22'*32;S='0:'+'33'*32;ORDER='GLOW'+'a'*32
class Tests(unittest.TestCase):
 def fixture(self,referenced=True,comment=ORDER):
  note=begin_cell().store_uint(0,32).store_string(comment).end_cell()
  builder=begin_cell().store_uint(0x7362d09c,32).store_uint(1,64).store_coins(15500000).store_address(Address(S)).store_bit(int(referenced))
  if referenced:builder.store_ref(note)
  else:builder.store_cell(note)
  body=builder.end_cell().to_boc(False).hex()
  return {'hash':'a'*64,'account':{'address':W},'success':True,'aborted':False,'destroyed':False,'out_msgs':[],'utime':int(time.time()),'in_msg':{'source':{'address':J},'destination':{'address':W},'bounced':False,'raw_body':body}}
 def test_real_boc_inline_and_reference(self):
  c=Crypto(None,{})
  for referenced in (False,True):
   receipt=c.match(self.fixture(referenced),W,J)[0]
   self.assertEqual((receipt['amount'],receipt['sender'],receipt['order']),(15500000,S,ORDER));self.assertEqual(receipt['id'],'a'*64)
 def test_invalid_transfers_do_not_credit(self):
  c=Crypto(None,{})
  for key,value in [('success',False),('aborted',True),('destroyed',True),('out_msgs',[{}]),('hash','bad')]:
   tx=self.fixture();tx[key]=value;self.assertEqual(c.match(tx,W,J),[])
  for key,value in [('bounced',True),('source',{'address':S}),('destination',{'address':S}),('raw_body','bad'),('raw_body','00')]:
   tx=self.fixture();tx['in_msg'][key]=value;self.assertEqual(c.match(tx,W,J),[])
  self.assertEqual(c.match(self.fixture(),W,S),[])
 def test_unmatched_comment_is_recorded_without_order(self):self.assertIsNone(Crypto(None,{}).match(self.fixture(comment='hello'),W,J)[0]['order'])
 def test_master_and_friendly(self):
  self.assertEqual(address(USDT_MASTER),'0:b113a994b5024a16719f69139328eb759596c38a25f59028b146fecdc3621dfe')
  self.assertEqual(address(Address(W).to_string(True,True,False,False)),W)
  with self.assertRaises(ValueError):address(Address(W).to_string(True,True,False,True))
  with self.assertRaises(ValueError):address(USDT_MASTER[:-1]+'a')
 def test_prices_exact(self):
  self.assertEqual(micro('15.5'),15500000);self.assertEqual(amount(15000000),'15');self.assertEqual(amount(1),'0.000001')
  for bad in ('NaN','Infinity','-1','0','1.0000001','foo'):
   with self.assertRaises(ValueError):micro(bad)
 def test_links_use_micro_units_and_comment(self):
  link=payment_links(W,15500000,ORDER)['tonkeeper'];self.assertIn('amount=15500000',link);self.assertIn('text='+ORDER,link)
 def test_crypto_admin_requires_admin(self):
  from test_bot import DB,API
  import bot,json
  cfg=json.loads(bot.ROOT.joinpath('config.example.json').read_text());cfg['admin_ids']=[9]
  club=bot.Club(API(),DB(),cfg)
  with self.assertRaises(ValueError):club.admin_callback(1,'a:crypto:0',None)
 def test_qr_svg(self):
  import qrcode,qrcode.image.svg,io
  qr=qrcode.make(payment_links(W,1,ORDER)['ton'],image_factory=qrcode.image.svg.SvgPathImage);out=io.BytesIO();qr.save(out);self.assertIn(b'<svg',out.getvalue())
if __name__=='__main__':unittest.main()
