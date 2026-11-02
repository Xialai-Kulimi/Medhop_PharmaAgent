"""Restore unfiltered patient choices after selecting/resetting. Frontend only."""
from datetime import datetime,timezone
from pathlib import Path
import argparse,shutil

FIX='''
// patient-picker-restore-list-20261103
const pickerPreviousChoices=renderPatientChoices;
renderPatientChoices=function(){
  // A selected older patient can be outside the recent-list server limit.
  if(!$('#patientSearch').value.trim()&&currentPatient&&!workflowPatients.some(p=>p.id===currentPatient.id)){
    workflowPatients=[...workflowPatients,{id:currentPatient.id,name:currentPatient.name,age:currentPatient.age,sex:currentPatient.sex,updated:currentPatient.updated}];
  }
  pickerPreviousChoices();
};
const pickerPreviousSelect=selectExistingPatient;
selectExistingPatient=async function(id){
  if(busy||active)return pickerPreviousSelect(id);
  await pickerPreviousSelect(id);
  // Selection clears the input, but the old code left the filtered array in memory.
  $('#patientSearch').value='';
  await refreshPatients();
};
const pickerPreviousReset=resetWorkflowPatient;
resetWorkflowPatient=async function(){
  if(busy||active)return pickerPreviousReset();
  await pickerPreviousReset();
  $('#patientSearch').value='';
  await refreshPatients();
};
'''


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',default='.');parser.add_argument('--check',action='store_true');args=parser.parse_args()
    root=Path(args.root).resolve();target=root/'static/workspace.js'
    if not target.is_file():raise SystemExit('請在Medhop_PharmaAgent專案根目錄執行。')
    text=target.read_text(encoding='utf-8-sig')
    if '// patient-picker-restore-list-20261103' in text:
        print('已套用病歷選單修正，瀏覽器請Ctrl+Shift+R。');return
    if '// workspace-reading-search-20261103' not in text:
        raise SystemExit('請先套用前一版病歷欄位與查詢介面整合更新，未修改檔案。')
    if args.check:print('檢查通過；未修改檔案。');return
    backup=root/'runtime/code-backups'/('patient-picker-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))/'workspace.js'
    backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,backup)
    target.write_text(text+'\n'+FIX,encoding='utf-8')
    print('已修正：選取病人或換下一位後，重新載入未篩選的病歷清單。')
    print('目前選取病人保留；沒有刪除或更改任何病歷。')
    print('原前端備份：'+str(backup))
    print('不需重啟後端，瀏覽器Ctrl+Shift+R即可。')


if __name__=='__main__':main()
