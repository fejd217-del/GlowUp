import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import bot
class Tests(unittest.TestCase):
 def test_no_local_config_uses_env_defaults(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);root.joinpath('config.example.json').write_text(bot.ROOT.joinpath('config.example.json').read_text())
   with patch.object(bot,'ROOT',root),patch.dict(os.environ,{'CHAT_ID':'-1004358476982','ADMIN_IDS':'123, 456','TRIAL_MINUTES':'7'},clear=True):
    cfg=bot.config();self.assertEqual(cfg['admin_ids'],[123,456]);self.assertEqual(cfg['trial_minutes'],7);self.assertFalse(cfg['sales_enabled'])
 def test_env_admin_overrides_saved_admin(self):
  with patch.dict(os.environ,{'ADMIN_IDS':'987'},clear=True):self.assertEqual(bot.environment_config({'admin_ids':[123]})['admin_ids'],[987])
 def test_invalid_admin_rejected(self):
  with patch.dict(os.environ,{'ADMIN_IDS':'oops'},clear=True):
   with self.assertRaises(ValueError):bot.config()
if __name__=='__main__':unittest.main()
