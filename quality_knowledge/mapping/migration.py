from __future__ import annotations
import hashlib, uuid
from pathlib import Path
from typing import Any
import yaml
from .models import MappingItem
from .repository import MappingConfigurationRepository

TOOL_VERSION='A2-RC1'
DOMAIN_MAP={
 'identity':'ISSUE_FACT','issue_fact':'ISSUE_FACT','product_context':'PRODUCT_CONTEXT',
 'occurrence':'OCCURRENCE','escape':'ESCAPE','solution':'SOLUTION','verification':'VERIFICATION',
 'recurrence':'RECURRENCE','product_extension':'PRODUCT_EXTENSION','extension':'PRODUCT_EXTENSION'
}
SKIP_TOP={'version','business_type','status','detection'}

class LegacyYamlMappingMigrator:
    def __init__(self, repository:MappingConfigurationRepository): self.repository=repository
    @staticmethod
    def source_hash(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    def load(self,path,business_type=None):
        p=Path(path); data=yaml.safe_load(p.read_text(encoding='utf-8')) or {}
        bt=(business_type or data.get('business_type') or '').upper()
        if not bt or not bt.replace('_','').isalnum(): raise ValueError(f'invalid/missing business_type: {bt!r}')
        items=[]; order=0
        for section,fields in data.items():
            if section in SKIP_TOP or not isinstance(fields,dict): continue
            domain=DOMAIN_MAP.get(section)
            if not domain: continue
            for key,spec in fields.items():
                if not isinstance(spec,dict): spec={'aliases':[str(spec)] if spec is not None else []}
                aliases=[]; seen_aliases=set()
                for value in (spec.get('aliases') or spec.get('source_headers') or []):
                    alias=str(value).strip()
                    if not alias or alias in seen_aliases: continue
                    seen_aliases.add(alias); aliases.append(alias)
                canonical=str(spec.get('canonical_field') or key)
                target=str(spec.get('target_field') or key)
                item=MappingItem(
                    mapping_id='MI-'+uuid.uuid4().hex, canonical_field=canonical,
                    source_headers=aliases[:], aliases=aliases[:], target_domain=domain,target_field=target,
                    required=bool(spec.get('required',False)), enabled=bool(spec.get('enabled',True)),
                    description=str(spec.get('description') or ''),display_order=order)
                items.append(item); order+=1
        return data,bt,items
    def validate_items(self,bt,items):
        results=[]
        if not any(i.canonical_field=='business_issue_id' and i.enabled for i in items):
            results.append({'level':'ERROR','code':'REQUIRED_BUSINESS_KEY_MISSING','message':'business_issue_id mapping is required'})
        seen={}
        for i in items:
            if not i.target_field:
                results.append({'level':'ERROR','code':'TARGET_FIELD_MISSING','message':f'{i.canonical_field}: target_field missing','mapping_id':i.mapping_id})
            item_aliases=[]; item_seen=set()
            for a in i.source_headers+i.aliases:
                k=' '.join(a.split()).casefold()
                if k in item_seen: continue
                item_seen.add(k); item_aliases.append((a,k))
            for a,k in item_aliases:
                prev=seen.get(k)
                if prev and prev!=i.canonical_field:
                    results.append({'level':'WARNING','code':'ALIAS_AMBIGUITY','message':f'alias {a!r} maps to {prev} and {i.canonical_field}; review before activation','mapping_id':i.mapping_id})
                else: seen[k]=i.canonical_field
        if not results: results.append({'level':'VALID','code':'MAPPING_VALID','message':f'{bt} legacy YAML mapping validated'})
        return results
    def migrate(self,path,business_type=None,dry_run=True,created_by='knowledge-mapping-migrate'):
        p=Path(path); data,bt,items=self.load(p,business_type); h=self.source_hash(p)
        prior=self.repository.find_successful_migration(bt,h)
        validations=self.validate_items(bt,items)
        counts={
          'business_type':bt,'canonical_field_count':len(items),
          'source_header_count':sum(len(i.source_headers) for i in items),
          'alias_count':sum(len(i.aliases) for i in items),
          'target_field_count':len({(i.target_domain,i.target_field) for i in items}),
          'valid_count':sum(r['level']=='VALID' for r in validations),
          'warning_count':sum(r['level']=='WARNING' for r in validations),
          'conflict_count':sum(r['code'] in {'ALIAS_CONFLICT','ALIAS_AMBIGUITY'} for r in validations),
          'invalid_count':sum(r['level']=='ERROR' for r in validations),
          'source_file':str(p),'source_hash':h,'dry_run':bool(dry_run)
        }
        if prior:
            counts.update({'migration_result':'SKIPPED','reason':'same business_type + source_hash already migrated'})
            return counts
        if dry_run:
            counts['migration_result']='VALID' if counts['invalid_count']==0 else 'INVALID'; counts['validation']=validations
            return counts
        run=self.repository.start_migration_run(business_type=bt,source_file=str(p),source_hash=h,tool_version=TOOL_VERSION,dry_run=False)
        try:
            if counts['invalid_count']:
                counts['migration_result']='INVALID'; counts['validation']=validations
                self.repository.finish_migration_run(run,'FAILED',counts); return counts
            existing_active=self.repository.get_effective_config(bt)
            cfg=self.repository.create_draft(bt,source_type='YAML_MIGRATION',source_file=str(p),source_hash=h,created_by=created_by,
                metadata={'legacy_yaml_version':data.get('version'),'legacy_yaml_status':data.get('status'),'migration_tool_version':TOOL_VERSION},mappings=items)
            self.repository.save_validation_results(cfg['config_id'],validations)
            activated=False
            if existing_active is None:
                cfg=self.repository.activate(cfg['config_id']); activated=True
            counts.update({'migration_result':'APPLIED','config_id':cfg['config_id'],'version':cfg['version'],'status':cfg['status'],'activated':activated})
            self.repository.finish_migration_run(run,'COMPLETED',counts); return counts
        except Exception as e:
            self.repository.finish_migration_run(run,'FAILED',{'error':str(e),**counts}); raise
