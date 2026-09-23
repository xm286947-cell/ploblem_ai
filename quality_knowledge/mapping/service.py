from __future__ import annotations
import json, uuid
import re
from difflib import SequenceMatcher
from openpyxl import load_workbook
from quality_knowledge.config_loader import normalize_header
from quality_knowledge.excel_utils import repair_read_only_dimensions
from .runtime import runtime_mapping_diagnostics
from pathlib import Path
import yaml
from .repository import MappingConfigurationRepository
from .migration import LegacyYamlMappingMigrator
from .models import MappingItem

class MappingConfigurationService:
    def __init__(self, repository): self.repository=repository
    def list_configs(self,business_type=None): return self.repository.list_configs(business_type)
    def get_effective_config(self,business_type): return self.repository.get_effective_config(business_type)
    def get_config_version(self,config_id): return self.repository.get_config(config_id)
    def create_draft(self,business_type,**kwargs): return self.repository.create_draft(business_type,**kwargs)
    def create_product_starter_draft(self, business_type, created_by='web-product-bootstrap'):
        """Create an editable Mapping seeded from the shared field catalog.

        HMI/PLC/IFA ACTIVE mappings are the first trusted vocabulary.  Their target
        fields and aliases are reused as suggestions, while source headers remain
        empty so a new product cannot accidentally match another product's column.
        """
        bt = str(business_type or '').strip().upper()
        if not bt:
            raise ValueError('PRODUCT_CODE_REQUIRED')
        if self.get_effective_config(bt):
            raise ValueError('MAPPING_ALREADY_INITIALIZED')
        existing = self.list_configs(bt)
        existing_draft = next((x for x in existing if x['status'] == 'DRAFT'), None)
        if existing and not existing_draft:
            return existing[0]
        # Merge by semantic destination, not by source header.  This preserves one
        # canonical field when HMI/PLC/IFA use different names for the same concept.
        catalog = {}
        catalog_products = {}
        for source_product in ('HMI', 'PLC', 'IFA'):
            sources = []
            cfg = self.get_effective_config(source_product)
            if cfg:
                sources.extend(cfg.get('mappings') or [])
            # A fresh SQLite database may not yet have the legacy mappings
            # migrated.  The shipped YAML remains the authoritative seed for
            # the shared catalog; it is read here without changing ACTIVE state.
            yaml_path = Path(__file__).resolve().parent.parent / 'config' / f'{source_product.lower()}_fields.yaml'
            if yaml_path.exists():
                try:
                    _, _, yaml_items = LegacyYamlMappingMigrator(self.repository).load(yaml_path, source_product)
                    sources.extend(item.to_dict() for item in yaml_items)
                except (OSError, ValueError):
                    pass
            for source in sources:
                if not source.get('enabled', True):
                    continue
                key = (source.get('target_domain') or 'PRODUCT_EXTENSION', source.get('target_field') or source.get('canonical_field'))
                entry = catalog.setdefault(key, {
                    'canonical_field': source.get('canonical_field') or source.get('target_field'),
                    'target_domain': key[0], 'target_field': key[1], 'required': False,
                    'aliases': set(), 'products': set(), 'description': source.get('description') or '',
                })
                entry['required'] = entry['required'] or bool(source.get('required'))
                entry['aliases'].update(x for x in (source.get('source_headers') or []) + (source.get('aliases') or []) if x)
                entry['products'].add(source_product)
                if not entry['description'] and source.get('description'):
                    entry['description'] = source['description']
        specs = [
            ('business_issue_id', ['问题编号'], ['问题单号', '缺陷编号', 'TRC单号', 'ITR单号', 'ID'], 'ISSUE_FACT', 'business_issue_id', True, '业务问题唯一编号'),
            ('title', ['问题标题'], ['标题', '问题名称', '故障标题'], 'ISSUE_FACT', 'title', False, '问题标题'),
            ('description', ['问题描述'], ['描述', '故障现象', '问题现象', '异常描述'], 'ISSUE_FACT', 'description', False, '问题描述'),
            ('product', ['产品'], ['产品名称', '产品型号', '设备型号'], 'ISSUE_FACT', 'product', False, '涉及产品'),
            ('platform', ['平台'], ['软件平台', '系统平台'], 'ISSUE_FACT', 'platform', False, '涉及平台'),
            ('severity', ['严重度'], ['严重程度', '优先级', '风险等级'], 'ISSUE_FACT', 'severity', False, '问题严重度'),
            ('issue_type', ['问题类型'], ['缺陷类型', '问题分类'], 'ISSUE_FACT', 'issue_type', False, '问题类型'),
        ]
        # Keep the small generic baseline when no legacy mapping is present, and use
        # it only to fill semantic destinations absent from the shared catalog.
        for canonical, source, aliases, domain, target, required, description in specs:
            key = (domain, target)
            if key not in catalog:
                catalog[key] = {'canonical_field': canonical, 'target_domain': domain, 'target_field': target,
                                'required': required, 'aliases': set(aliases), 'products': set(), 'description': description}
        items = []
        for order, entry in enumerate(catalog.values()):
            products = ','.join(sorted(entry['products']))
            description = entry['description'] or '公共标准字段'
            if products:
                description = f'公共字段（来源：{products}）' + (f'；{description}' if description else '')
            items.append(MappingItem('MI-' + uuid.uuid4().hex, entry['canonical_field'], [], sorted(entry['aliases']),
                                     entry['target_domain'], entry['target_field'], entry['required'], True, description, order))
        if existing_draft and existing_draft.get('source_type') == 'BOOTSTRAP':
            # Make re-running the bootstrap action safe: preserve user edits and
            # append any shared fields that were unavailable on the first run.
            existing_keys = {(x.get('target_domain'), x.get('target_field')) for x in existing_draft.get('mappings') or []}
            merged = [MappingItem(x['mapping_id'], x['canonical_field'], list(x.get('source_headers') or []),
                                  list(x.get('aliases') or []), x['target_domain'], x['target_field'],
                                  x.get('required', False), x.get('enabled', True), x.get('description') or '', i)
                      for i, x in enumerate(existing_draft.get('mappings') or [])]
            for item in items:
                if (item.target_domain, item.target_field) not in existing_keys:
                    item.display_order = len(merged)
                    merged.append(item)
            return self.update_draft(existing_draft['config_id'], merged)
        return self.repository.create_draft(
            bt, source_type='BOOTSTRAP', created_by=created_by,
            metadata={'template': 'SHARED_FIELD_CATALOG', 'bootstrap_for_product': bt,
                      'catalog_products': sorted({p for x in catalog.values() for p in x['products']}),
                      'catalog_field_count': len(items)}, mappings=items,
        )
    def create_draft_from(self,config_id,created_by='web'):
        src=self.repository.get_config(config_id)
        if not src: raise KeyError(config_id)
        items=[MappingItem('MI-'+uuid.uuid4().hex,x['canonical_field'],list(x['source_headers']),list(x['aliases']),x['target_domain'],x['target_field'],x['required'],x['enabled'],x.get('description') or '',x.get('display_order') or i) for i,x in enumerate(src['mappings'])]
        return self.repository.create_draft(src['business_type'],source_type='WEB',created_by=created_by,metadata={'based_on_config_id':config_id,'based_on_version':src['version']},mappings=items)
    def update_draft(self,config_id,mappings): return self.repository.replace_draft_mappings(config_id,mappings)
    def validate_config(self,config_id):
        cfg=self.repository.get_config(config_id)
        if not cfg: raise KeyError(config_id)
        items=[MappingItem(**{k:x[k] for k in ['mapping_id','canonical_field','source_headers','aliases','target_domain','target_field','required','enabled','description','display_order']}) for x in cfg['mappings']]
        results=LegacyYamlMappingMigrator(self.repository).validate_items(cfg['business_type'],items)
        self.repository.clear_validation_results(config_id)
        self.repository.save_validation_results(config_id,results)
        return {'status':'ERROR' if any(x['level']=='ERROR' for x in results) else ('WARNING' if any(x['level']=='WARNING' for x in results) else 'VALID'),'results':results}
    def activate_config(self,config_id): return self.repository.activate(config_id)
    def migrate_yaml(self,path,business_type=None,dry_run=True): return LegacyYamlMappingMigrator(self.repository).migrate(path,business_type,dry_run)
    def validation_results(self,config_id): return self.repository.get_validation_results(config_id)
    def migration_info(self,config_id): return self.repository.latest_migration_for_config(config_id)

    def preview_file(self,path,business_type,sheet=None,header_row=1,config_id=None):
        business_type=business_type.upper()
        cfg=self.get_config_version(config_id) if config_id else self.get_effective_config(business_type)
        if cfg and cfg['business_type'] != business_type:
            raise ValueError('MAPPING_PRODUCT_MISMATCH')
        if not cfg: raise RuntimeError(f'MAPPING_NOT_INITIALIZED: {business_type}')
        wb=load_workbook(path,read_only=True,data_only=False)
        sheet_name=sheet or wb.sheetnames[0]
        ws=wb[sheet_name]
        repair_read_only_dimensions(ws)
        row=next(ws.iter_rows(min_row=header_row,max_row=header_row,values_only=True), ())
        headers=[str(x).strip() if x is not None else '' for x in row]
        d=runtime_mapping_diagnostics(cfg,headers)
        # Required missing is a configuration/header relationship, not a row-data check.
        normalized={normalize_header(x) for x in headers if normalize_header(x)}
        required_missing=[]
        for item in cfg.get('mappings') or []:
            if not item.get('enabled',True) or not item.get('required'): continue
            aliases={normalize_header(x) for x in (item.get('source_headers') or [])+(item.get('aliases') or []) if normalize_header(x)}
            if not (normalized & aliases):
                required_missing.append({'canonical_field':item['canonical_field'],'target_domain':item['target_domain'],'target_field':item['target_field'],'mapping_status':'REQUIRED_MISSING'})
        conflict=sum(1 for x in d['fields'] if x['mapping_status']=='CONFLICT')
        d.update({'source_file':Path(path).name,'source_sheet':sheet_name,'conflict':conflict,'required_missing':len(required_missing),'required_missing_fields':required_missing})
        matched_keys={normalize_header(x.get('source_header')) for x in d['fields'] if x.get('mapping_status','').startswith('MATCHED')}
        choices=[]
        for item in cfg.get('mappings') or []:
            if not item.get('enabled',True): continue
            labels=[x for x in (item.get('source_headers') or []) + (item.get('aliases') or []) if x]
            chinese=next((x for x in labels if re.search(r'[\u4e00-\u9fff]', str(x))), None)
            display_name=chinese or item.get('description') or item['canonical_field']
            choices.append({'mapping_id':item['mapping_id'],'canonical_field':item['canonical_field'],'target_domain':item['target_domain'],'target_field':item['target_field'],'display_name':display_name,'aliases_preview':'、'.join(labels[:5]),'search_text':' '.join([str(display_name),str(item['canonical_field']),str(item['target_field'])]+[str(x) for x in labels])})
        for row in d['fields']:
            if row['mapping_status'] not in {'UNMATCHED','CONFLICT'}: continue
            needle=normalize_header(row['source_header']); scored=[]
            for item in cfg.get('mappings') or []:
                names=[item.get('canonical_field',''),item.get('target_field','')]+(item.get('source_headers') or [])+(item.get('aliases') or [])
                score=max((SequenceMatcher(None,needle,normalize_header(x)).ratio() for x in names if normalize_header(x)),default=0)
                if score>=.35: scored.append((score,item))
            scored.sort(key=lambda x:x[0],reverse=True)
            row['suggestions']=[{'mapping_id':x['mapping_id'],'canonical_field':x['canonical_field'],'target_domain':x['target_domain'],'target_field':x['target_field'],'score':round(score*100)} for score,x in scored[:3]]
        source_norm={normalize_header(x) for x in headers if normalize_header(x)}
        missing=[]
        for item in cfg.get('mappings') or []:
            aliases={normalize_header(x) for x in (item.get('source_headers') or [])+(item.get('aliases') or []) if normalize_header(x)}
            if item.get('enabled',True) and not (aliases & source_norm):
                missing.append({'mapping_id':item['mapping_id'],'canonical_field':item['canonical_field'],'target_domain':item['target_domain'],'target_field':item['target_field'],'required':item.get('required',False)})
        d.update({'mapping_choices':choices,'missing_mapping_fields':missing,'difference_count':sum(x['mapping_status'] in {'UNMATCHED','CONFLICT'} for x in d['fields'])+len(missing)})
        return d

    def get_coverage(self,path,business_type,sheet=None,header_row=1):
        return self.preview_file(path,business_type,sheet,header_row)

    def add_unmatched_to_draft(self,base_config_id,source_header,target_domain='PRODUCT_EXTENSION',target_field=None,mode='mapping'):
        base=self.get_config_version(base_config_id)
        if not base: raise KeyError(base_config_id)
        draft=base if base['status']=='DRAFT' else self.create_draft_from(base_config_id)
        items=[]
        for i,x in enumerate(draft['mappings']):
            items.append(MappingItem(x['mapping_id'],x['canonical_field'],list(x['source_headers']),list(x['aliases']),x['target_domain'],x['target_field'],x['required'],x['enabled'],x.get('description') or '',x.get('display_order') or i))
        if mode=='alias':
            # Alias must be attached explicitly to a canonical target.
            target_field=target_field or ''
            found=False
            for item in items:
                if item.target_field==target_field or item.canonical_field==target_field:
                    if source_header not in item.aliases: item.aliases.append(source_header)
                    found=True; break
            if not found: raise ValueError('ALIAS_TARGET_NOT_FOUND')
        else:
            leaf=(target_field or normalize_header(source_header) or 'custom_field').strip()
            items.append(MappingItem('MI-'+uuid.uuid4().hex,leaf,[source_header],[],target_domain,leaf,False,True,'Created from Mapping Preview',len(items)))
        return self.update_draft(draft['config_id'],items)

    def apply_difference_actions(self,base_config_id,source_actions,disable_mapping_ids,created_by='web-difference-workbench'):
        base=self.get_config_version(base_config_id)
        if not base: raise KeyError(base_config_id)
        draft=self.create_draft_from(base_config_id,created_by)
        items=[MappingItem(x['mapping_id'],x['canonical_field'],list(x['source_headers']),list(x['aliases']),x['target_domain'],x['target_field'],x['required'],x['enabled'],x.get('description') or '',x.get('display_order') or i) for i,x in enumerate(draft['mappings'])]
        by_id={x.mapping_id:x for x in items}
        base_by_id={x['mapping_id']:x for x in base['mappings']}; by_canonical={x.canonical_field:x for x in items}
        for row in source_actions:
            action=row.get('action'); source=str(row.get('source_header') or '').strip()
            if not source or action in {'RAW_ONLY','IGNORE',''}: continue
            if action=='ALIAS':
                token=str(row.get('target_mapping_id') or '').strip()
                original=base_by_id.get(token)
                if not original and token:
                    # Older pages submitted canonical/target keys instead of the
                    # generated mapping id. Accept both representations.
                    original=next((x for x in base['mappings'] if token in {x.get('canonical_field'),x.get('target_field')}),None)
                target=by_canonical.get(original['canonical_field']) if original else by_id.get(row.get('target_mapping_id'))
                if not target: raise ValueError(f'ALIAS_TARGET_NOT_FOUND: target={token or "(empty)"}; 请先选择已有标准字段')
                if source not in target.aliases: target.aliases.append(source)
            elif action=='EXTENSION':
                leaf=str(row.get('target_field') or normalize_header(source) or 'custom_field').strip()
                items.append(MappingItem('MI-'+uuid.uuid4().hex,leaf,[source],[],'PRODUCT_EXTENSION',leaf,False,True,'Created from field difference workbench',len(items)))
            else: raise ValueError('INVALID_DIFFERENCE_ACTION')
        for mapping_id in disable_mapping_ids:
            original=base_by_id.get(mapping_id); target=by_canonical.get(original['canonical_field']) if original else by_id.get(mapping_id)
            if target and not target.required: target.enabled=False
        return self.update_draft(draft['config_id'],items)

    def export_config(self,config_id,path,format='yaml'):
        cfg=self.repository.get_config(config_id)
        if not cfg: raise KeyError(config_id)
        data={'business_type':cfg['business_type'],'version':cfg['version'],'status':cfg['status'],'source_type':cfg['source_type'],'mappings':cfg['mappings']}
        p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
        if format=='json': p.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        else: p.write_text(yaml.safe_dump(data,allow_unicode=True,sort_keys=False),encoding='utf-8')
        return p
