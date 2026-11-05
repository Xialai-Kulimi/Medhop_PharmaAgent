from pathlib import Path
import sys,json,tempfile,threading
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from server import Corpus
from workflow import pair_evidence
from agent_harness import stored
from pair_integration import PairIntegration,INTERMEDIATES
from governance import PrivateStore,ReviewGate,drafts
c=Corpus();joiner=PairIntegration(c);checks=[]
def patient(names):
    return {'id':'isolated-source-test','version':1,'medications':[{'id':i+1,'name':name,'name_key':name.casefold(),'dose':'','frequency':'','body':name,'status':'active','kind':'current','class':'unknown','acquisition':'unknown'} for i,name in enumerate(names)]}
p=patient(['DB00203','DB00820']);a=pair_evidence(p,c.pages);a=joiner.join(p,a)
pair=a['pairs'][0];assert pair['status']=='insufficient' and pair['medhop']['status']=='mechanism_candidate'
assert pair['integration']['status']=='research_candidate' and not pair['integration']['automatic_dispensing'];checks.append('research_path_never_upgraded_to_clinical_interaction')
byid={s['id']:s for s in a['sources']};assert len(byid)==len(a['sources'])
for path in pair['medhop']['paths']:
    assert {path['nodes'][0],path['nodes'][-1]}=={'DB00203','DB00820'}
    assert all(c.graph.by_title[n]['type'] in INTERMEDIATES for n in path['nodes'][1:-1])
    for edge in path['edges']:
        assert edge['source_ids']
        from workflow import literal_positions
        assert all(all(literal_positions(byid[sid]['full_text'],n) for n in [edge['source'],edge['target']]) for sid in edge['source_ids'])
assert any(e['traversed_reverse'] for path in pair['medhop']['paths'] for e in path['edges']);checks.append('actual_MedHop_pair_paths_raw_sources_and_original_directions')
r=drafts(p,a);assert r['state']=='handoff' and any(i['id'].startswith('research-') for i in r['reasons']);checks.append('research_findings_require_pharmacist_resolution')
p=patient(['Amlodipine','Tacrolimus']);b=joiner.join(p,pair_evidence(p,c.pages));assert b['pairs'][0]['status']=='interaction' and b['pairs'][0]['integration']['status']=='label_warning';assert b['pairs'][0]['quote'] and b['pairs'][0]['medhop']['status']=='unmapped';checks.append('original_PDF_warning_preserved_despite_missing_research_mapping')
p=patient(['Cimetidine','Warfarin']);b=joiner.join(p,pair_evidence(p,c.pages));assert b['pairs'][0]['status'] not in {'avoid','interaction'};checks.append('third_product_drug_list_does_not_create_DDI')
p=patient(['SERTRALINE','Warfarin']);b=joiner.join(p,pair_evidence(p,c.pages));assert b['pairs'][0]['medhop']['status']=='unmapped';checks.append('unmapped_names_never_guessed')
p=patient(['Amlodipine','Warfarin']);b=joiner.join(p,pair_evidence(p,c.pages));assert b['pairs'][0]['status']=='no_effect_reported';assert b['pairs'][0]['integration']['status']=='review_required';checks.append('label_specific_no_effect_is_not_global_safe')
with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
    store=PrivateStore(Path(folder)/'records.sqlite3');gate=ReviewGate(store)
    p=stored(store,'test-session','配對測試',['DB00203','DB00820']);a=joiner.join(p,pair_evidence(p,c.pages));store.save_review('pair-review',p,{'assessment':a,'answer':'待藥師確認'})
    report=gate.report('test-session','pair-review');assert 'DB00203' in report['pharmacist'] and '研究路徑' in report['pharmacist']
    try:gate.delivery('test-session','pair-review')
    except ValueError:pass
    else:raise AssertionError('unapproved research report delivered')
    with store.connect() as db:
        med=p['medications'][0]
        db.execute('INSERT INTO medication_identifiers VALUES(?,?,?,?,?)',(med['id'],'DB00203','data/medhop/entities.parquet','同一識別碼的隔離流程驗證','test'))
        db.execute('UPDATE patients SET version=version+1 WHERE id=?',(p['id'],))
    assert store.active('test-session')['medications'][0]['medhop_entity']=='DB00203'
    try:gate.report('test-session','pair-review')
    except ValueError:pass
    else:raise AssertionError('stale mapping review allowed')
checks+=['integrated_report_preserved_with_sources','identity_mapping_version_invalidates_old_report','no_unapproved_delivery']
(ROOT/'runtime/pair-integration-validation.json').write_text(json.dumps({'all_passed':True,'checks':checks,'actual_research_pair':{'entities':['DB00203','DB00820'],'paths':len(a['pairs'][0]['medhop']['paths']),'clinical_status':a['pairs'][0]['status']},'live_patient_records_created':0},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(checks,ensure_ascii=False,indent=2))
