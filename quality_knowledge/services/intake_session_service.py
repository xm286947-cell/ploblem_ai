from __future__ import annotations
import json, shutil, uuid
from datetime import datetime, timezone
from pathlib import Path
from openpyxl import load_workbook

class IntakeSessionError(ValueError): pass

class IntakeSessionService:
    """Filesystem-backed temporary intake sessions. Preview never writes Knowledge."""
    def __init__(self, root=None):
        self.root=Path(root or (Path(__import__('tempfile').gettempdir())/'quality_intake_sessions'))
        self.root.mkdir(parents=True,exist_ok=True)

    def _dir(self,token):
        if not token or any(x in token for x in ('/','\\','..')): raise IntakeSessionError('INVALID_INTAKE_SESSION')
        return self.root/token
    def create(self, upload_name, content):
        token=uuid.uuid4().hex; d=self._dir(token); d.mkdir(parents=True,exist_ok=False)
        name=Path(upload_name or 'upload.xlsx').name; p=d/name; p.write_bytes(content)
        meta={'intake_session_id':token,'source_file':name,'path':str(p),'created_at':datetime.now(timezone.utc).isoformat(),'status':'PREVIEW'}
        self.save(meta); return meta
    def save(self,meta):
        self._dir(meta['intake_session_id']).joinpath('session.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    def get(self,token):
        p=self._dir(token)/'session.json'
        if not p.exists(): raise IntakeSessionError('INTAKE_SESSION_NOT_FOUND')
        return json.loads(p.read_text(encoding='utf-8'))
    def mark_committed(self,token,batch_id):
        m=self.get(token);m['status']='COMMITTED';m['batch_id']=batch_id;m['committed_at']=datetime.now(timezone.utc).isoformat();self.save(m);return m
    def discard(self,token):
        shutil.rmtree(self._dir(token),ignore_errors=True)
