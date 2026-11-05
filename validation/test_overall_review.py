from pathlib import Path
import sys,tempfile,json,gc
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from governance import PrivateStore,ReviewGate
from workflow import pair_evidence
from agent_harness import stored
import record_tools
pages=json.loads((ROOT/'data/pharma_pages.json').read_text(encoding='utf-8'))
def blocked(call):
 try:call()
 except ValueError:return
 raise AssertionError('Expected gate rejection')
with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
 s=PrivateStore(Path(folder)/'test.sqlite');g=ReviewGate(s)
 p=stored(s,'test','測試甲',['Carbamazepine','Phenytoin','Lamotrigine'],age=25,sex='女',pregnancy='not_pregnant')
 a=pair_evidence(p,pages);s.save_review('r',p,{'assessment':a,'answer':'待藥師確認'})
 report=g.report('test','r');ids=[r['id'] for r in report['reasons']];assert len(ids)>5
 note='隔離軟體測試：核對範圍與處理紀錄完整保留，此內容不是臨床判斷或真實交付。'
 blocked(lambda:g.approve('test','r','test',{},True,'已核對',ids))
 blocked(lambda:g.approve('test','r','test',{},True,note,ids[:-1]))
 blocked(lambda:g.approve('test','r','test',{},False,note,ids))
 blocked(lambda:g.approve('test','r','test',{},True,note,ids))
 g.save_draft('test','r',report['pharmacist'],report['patient']+'\n隔離測試用，並非交付給真實病人。')
 s.disposition('test','r','contact_prescriber','隔離測試，聯繫結果尚未確認')
 blocked(lambda:g.approve('test','r','test',{},True,note,ids))
 s.disposition('test','r','reviewed','隔離測試核對完成，並非臨床紀錄')
 approved=g.approve('test','r','test',{},True,note,ids);assert approved['approval']
 with s.connect() as db:
  resolution=json.loads(db.execute('SELECT resolutions FROM approvals WHERE review_id=?',('r',)).fetchone()[0])
  assert resolution['mode']=='overall' and resolution['note']==note and {r['id'] for r in resolution['covered_reasons']}==set(ids)
 assert 'test' in g.delivery('test','r')
 record_tools.edit_patient(s,'test',{'allergies':'待補充核對'});blocked(lambda:g.delivery('test','r'))
 gc.collect()
print('PASS: one overall note retains exact review scope; short/mismatched/unacknowledged/unreviewed/pending-contact/stale cases rejected; no automatic per-item resolution')
