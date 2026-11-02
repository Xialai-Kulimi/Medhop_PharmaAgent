"""Hide only model-incomplete display card; preserve backend review records."""
from datetime import datetime, timezone
from pathlib import Path
import shutil

root=Path.cwd().resolve()
target=root/'static/workspace.js'
if not target.is_file():
    raise SystemExit('請先cd到Medhop_PharmaAgent專案根目錄。')
text=target.read_text(encoding='utf-8-sig')
old='const items=result.safety_alerts;'
new="const items=(result.safety_alerts||[]).filter(item=>item.id!=='model-incomplete');"
if new in text:
    print('已套用：模型核對未完成提示卡不顯示。瀏覽器請按Ctrl+Shift+R。')
elif text.count(old)==1:
    backup=root/'runtime/code-backups'/('hide-model-notice-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))/'workspace.js'
    backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,backup)
    target.write_text(text.replace(old,new),encoding='utf-8')
    print('已隱藏模型核對未完成提示卡。瀏覽器請按Ctrl+Shift+R，不需要重啟後端。')
    print('原前端備份：'+str(backup))
else:
    raise SystemExit('找不到對應的警示區版本；未修改任何檔案，請提供workspace.js核對。')
