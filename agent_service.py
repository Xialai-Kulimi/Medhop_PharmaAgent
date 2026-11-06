"""Research GraphRAG worker. The pharmacist agent lives in pharmacist_agent.py."""
from pathlib import Path
import json, os, subprocess, tempfile, time

ROOT=Path(__file__).resolve().parent
# The worker's own system prompt sets no language; without this, Chinese-capable models answer in simplified characters.
RESPONSE_TYPE='one concise paragraph, at most 150 words, written in Traditional Chinese as used in Taiwan (繁體中文); keep drug, gene and protein names and [Data: ...] citations unchanged'

def graphrag(query,stop=None):
    python=os.environ.get('MEDHOP_PYTHON','')
    root=os.environ.get('MEDHOP_ROOT',str(ROOT/'data'/'medhop-worker'))
    if not python or not root:return {'status':'unavailable','reason':'尚未設定完整 MedHop GraphRAG worker；圖譜路徑檢索不等於完整 GraphRAG 模型推理。'}
    with tempfile.TemporaryDirectory(prefix='medhop-',dir=ROOT/'runtime') as folder:
        output=Path(folder)/'result.json';log=Path(folder)/'worker.log'
        env={**os.environ,'MEDLINK_RESULT_FILE':str(output),'PYTHONIOENCODING':'utf-8','GRAPHRAG_API_KEY':'local-only','MODEL_URL':os.environ.get('MODEL_URL','http://127.0.0.1:13305/api/v1')}
        with log.open('w',encoding='utf-8') as stream:
            proc=subprocess.Popen([python,str(ROOT/'vendor'/'medhop'/'medhop_query.py'),'--root',root,'--method','local','--response-type',RESPONSE_TYPE,query],cwd=ROOT,stdout=stream,stderr=stream,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            deadline=time.monotonic()+150
            while proc.poll() is None:
                if (stop and stop.is_set()) or time.monotonic()>deadline:
                    proc.terminate();proc.wait(timeout=10)
                    return {'status':'cancelled' if stop and stop.is_set() else 'timeout','reason':'研究推理未完成，轉交藥師；不據此推論無交互作用。'}
                time.sleep(.2)
        if proc.returncode or not output.exists():
            diagnostic=log.read_text(encoding='utf-8',errors='replace').strip().splitlines()
            return {'status':'error','reason':'MedHop GraphRAG worker 未產生完整結果，保留原文檢索供人工確認。','worker_exit_code':proc.returncode,'result_file_created':output.exists(),'diagnostic':diagnostic[-1][:700] if diagnostic else 'worker returned no result file'}
        result=json.loads(output.read_text(encoding='utf-8'))
        return {'status':'completed','kind':'research_only','engine':'original MedHop GraphRAG local search','answer':result.get('answer',''),'sources':result.get('sources',[]),'scope':result.get('scope',{})}
