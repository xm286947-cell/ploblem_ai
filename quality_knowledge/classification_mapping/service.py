from __future__ import annotations
import hashlib,json,uuid
class ClassificationMappingService:
 def __init__(self,repo):self.repo=repo
 def create_draft(self,business_type,side,items):
  self.repo.ensure_quality_capability_p0(); h=hashlib.sha256(json.dumps(items,sort_keys=True).encode()).hexdigest()
  with self.repo.connect() as c:
   version=c.execute('SELECT COALESCE(MAX(version),0)+1 FROM classification_mapping_config WHERE business_type=? AND classification_side=?',(business_type,side)).fetchone()[0]; cid='CMC-'+uuid.uuid4().hex
   c.execute('INSERT INTO classification_mapping_config(config_id,business_type,classification_side,version,status,content_hash) VALUES(?,?,?,?,?,?)',(cid,business_type,side,version,'DRAFT',h))
   for item in items:c.execute('INSERT INTO classification_mapping_item(item_id,config_id,source_l1,source_l2,source_l3,source_l4,target_tag_code,target_mrc_code,target_engineering_capability_code,target_management_capability_code,enabled) VALUES(?,?,?,?,?,?,?,?,?,?,?)',('CMI-'+uuid.uuid4().hex,cid,item.get('source_l1'),item.get('source_l2'),item.get('source_l3'),item.get('source_l4'),item.get('target_tag_code'),item.get('target_mrc_code'),item.get('target_engineering_capability_code'),item.get('target_management_capability_code'),int(item.get('enabled',True))))
  return {'config_id':cid,'status':'DRAFT','version':version}
 def validate(self,cid):
  with self.repo.connect() as c:
   bad=c.execute("SELECT COUNT(*) FROM classification_mapping_item WHERE config_id=? AND enabled=1 AND COALESCE(target_mrc_code,'')=''",(cid,)).fetchone()[0]
  return {'valid':not bad,'errors':bad}
 def activate(self,cid):
  if not self.validate(cid)['valid']:raise ValueError('INVALID_CLASSIFICATION_MAPPING')
  with self.repo.connect() as c:
   r=c.execute('SELECT business_type,classification_side FROM classification_mapping_config WHERE config_id=?',(cid,)).fetchone();c.execute("UPDATE classification_mapping_config SET status='RETIRED' WHERE business_type=? AND classification_side=? AND status='ACTIVE'",tuple(r));c.execute("UPDATE classification_mapping_config SET status='ACTIVE',activated_at=CURRENT_TIMESTAMP WHERE config_id=?",(cid,))
  return {'config_id':cid,'status':'ACTIVE'}
