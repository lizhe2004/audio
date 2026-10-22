import base64, collections, hashlib, json, mimetypes, os, pathlib, re, subprocess, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path('/Users/lizhe/Documents/workspace/audio')
SOUNDS = ROOT / 'sounds'
RAW = pathlib.Path('/private/tmp/qwen_omni_results.jsonl')
USE_CASES = pathlib.Path('/private/tmp/qwen_omni_use_cases.jsonl')
REPORT = ROOT / 'audio_analysis_qwen_omni.md'
API_KEY = os.environ.get('DASHSCOPE_API_KEY')
BASE = os.environ.get('DASHSCOPE_BASE_URL', 'https://llm-coo93ig1knqn5ga1.cn-beijing.maas.aliyuncs.com/compatible-mode/v1').rstrip('/')
ENDPOINT = BASE + '/chat/completions'
MODEL = 'qwen3.8-omni-flash'
PROMPT = '''请只依据随请求提供的音频内容进行分析。不要查看、猜测或利用文件名、文件路径、目录名、元数据来推断声音。

请用中文简洁输出 JSON 对象，字段如下：
- audible_description：客观描述实际可听到的声音及先后变化（音色、节奏、音高走向、层次/尾音等，只写听得到的内容）
- likely_source：最可能的声源或声音类别；如果无法可靠判断，请明确写“无法确定”或“合成/拟音”
- confidence：high / medium / low
- evidence：1-3条支持判断的可听证据
- speech_or_music：是否有人声/可辨语音/音乐，若有只转写确实听清的内容
- uncertainty：可能的其他解释或听不清之处

避免编造精确频率、音符、乐器、真实物件或录制场景。把可听事实与声源推测分开；短音效无法识别具体来源时宁可保守。只返回 JSON，不要 Markdown。'''

USE_CASE_PROMPT = '''请只依据随请求提供的音频内容，给出它适合使用的具体场景。不要查看、猜测或利用文件名、文件路径、目录名、元数据来推断声音。

请用中文简洁输出 JSON 对象，字段如下：
- suitable_scenarios：2-4 个适用场景，每项为对象，含 scene（场景）、fit（为什么适合，需对应音频实际可听特征）、priority（high / medium / low）
- avoid_scenarios：1-3 个不适用或需要谨慎使用的场景，说明原因
- implementation_notes：用于剪辑或交互时的实用建议，如适合用作一次性触发、循环铺底、转场，是否需要压低音量、是否可能干扰对白
- confidence：high / medium / low
- uncertainty：无法仅从声音判断的用途边界

不要把具体影视、游戏、品牌或作品名称当作来源事实。避免夸张和编造。JSON 字符串内部不得出现未转义的英文双引号；可改用中文引号或省略引号。只返回 JSON，不要 Markdown。'''

def decode_audio(path):
    ext = path.suffix.lower()
    if ext == '.flac':
        p = subprocess.run(['ffmpeg','-v','error','-i',str(path),'-f','wav','-acodec','pcm_s16le','pipe:1'],capture_output=True,check=True)
        return p.stdout, 'wav', 'audio/wav'
    mime = {'.mp3':'audio/mpeg','.wav':'audio/wav'}[ext]
    return path.read_bytes(), ext[1:], mime

def call(path, prompt=PROMPT):
    blob, fmt, mime = decode_audio(path)
    uri = f'data:{mime};base64,' + base64.b64encode(blob).decode('ascii')
    body = {
        'model': MODEL,
        'messages': [{'role':'user','content':[
            {'type':'text','text':prompt},
            {'type':'input_audio','input_audio':{'data':uri,'format':fmt}}
        ]}],
        'reasoning_effort':'minimal',
        'max_tokens':800,
        'stream':False
    }
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body,ensure_ascii=False).encode(), headers={
        'Authorization':'Bearer '+API_KEY, 'Content-Type':'application/json'
    }, method='POST')
    for attempt in range(6):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                payload=json.loads(resp.read())
            msg=payload['choices'][0]['message']
            txt=msg.get('content') or ''
            if isinstance(txt,list): txt=''.join(str(x.get('text','')) for x in txt if isinstance(x,dict))
            # Strip optional fenced JSON wrapper without imposing assumptions on the model's wording.
            clean=txt.strip()
            if clean.startswith('```'):
                clean=re.sub(r'^```(?:json)?\s*|\s*```$','',clean,flags=re.I).strip()
            try: parsed=json.loads(clean)
            except Exception: parsed={'audible_description':clean,'likely_source':'','confidence':'low','evidence':[],'speech_or_music':'','uncertainty':'模型未返回可解析 JSON；保留原回复'}
            usage=payload.get('usage') or {}
            return {'path':str(path.relative_to(ROOT)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'analysis':parsed,'usage':usage}
        except urllib.error.HTTPError as e:
            details=e.read().decode('utf-8','replace')[:1200]
            if e.code in (429,500,502,503,504) and attempt<5:
                time.sleep(min(60,2**attempt)); continue
            raise RuntimeError(f'HTTP {e.code}: {details}')
        except (urllib.error.URLError,TimeoutError) as e:
            if attempt<5:
                time.sleep(min(60,2**attempt)); continue
            raise RuntimeError(str(e))
    raise RuntimeError('retry limit')

def load_jsonl(path):
    data={}
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                x=json.loads(line); data[x['sha256']]=x
            except Exception:
                pass
    return data

def main():
    if not API_KEY:
        raise SystemExit('DASHSCOPE_API_KEY is not set')
    paths=sorted(p for p in SOUNDS.rglob('*') if p.is_file() and p.suffix.lower() in {'.mp3','.wav','.flac'})
    dup=collections.defaultdict(list)
    for p in paths: dup[hashlib.sha256(p.read_bytes()).hexdigest()].append(p)
    representatives=[v[0] for v in dup.values()]
    done=load_jsonl(RAW)
    use_cases=load_jsonl(USE_CASES)
    pending=[p for p in representatives if hashlib.sha256(p.read_bytes()).hexdigest() not in done]
    retry_incomplete=os.environ.get('RETRY_INCOMPLETE_USE_CASES') == '1'
    pending_use_cases=[p for p in representatives if (
        hashlib.sha256(p.read_bytes()).hexdigest() not in use_cases or
        (retry_incomplete and not use_cases[hashlib.sha256(p.read_bytes()).hexdigest()].get('use_cases',{}).get('suitable_scenarios'))
    )]
    print(f'FILES={len(paths)} UNIQUE={len(representatives)} DUPLICATE_COPIES={len(paths)-len(representatives)} ANALYSIS_PENDING={len(pending)} USE_CASES_PENDING={len(pending_use_cases)}',flush=True)
    def within_limit(p):
        # The endpoint caps Base64 payloads at 10 MB. Convert FLAC before measuring.
        if p.suffix.lower()=='.flac':
            size=len(subprocess.run(['ffmpeg','-v','error','-i',str(p),'-f','wav','-acodec','pcm_s16le','pipe:1'],capture_output=True,check=True).stdout)
        else:
            size=p.stat().st_size
        if ((size+2)//3)*4 >= 10*1024*1024:
            print(f'SKIP_TOO_LARGE={p.relative_to(ROOT)} bytes={size}',flush=True)
            return False
        return True

    pending=[p for p in pending if within_limit(p)]
    # Four workers stay below the model's 60-RPM ceiling at typical response times.
    with RAW.open('a') as out, ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(call,p): p for p in pending}
        for i, future in enumerate(as_completed(futures),1):
            p=futures[future]
            try:
                result=future.result()
            except Exception as exc:
                print(f'FAILED={p.relative_to(ROOT)} error={exc}',flush=True)
                continue
            out.write(json.dumps(result,ensure_ascii=False)+'\n'); out.flush(); done[result['sha256']]=result
            if i%10==0 or i==len(pending): print(f'PROGRESS={i}/{len(pending)} TOTAL_DONE={len(done)}',flush=True)
    pending_use_cases=[p for p in pending_use_cases if within_limit(p)]
    with USE_CASES.open('a') as out, ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(call,p,USE_CASE_PROMPT): p for p in pending_use_cases}
        for i, future in enumerate(as_completed(futures),1):
            p=futures[future]
            try:
                result=future.result()
            except Exception as exc:
                print(f'USE_CASE_FAILED={p.relative_to(ROOT)} error={exc}',flush=True)
                continue
            result['use_cases']=result.pop('analysis')
            out.write(json.dumps(result,ensure_ascii=False)+'\n'); out.flush(); use_cases[result['sha256']]=result
            if i%10==0 or i==len(pending_use_cases): print(f'USE_CASE_PROGRESS={i}/{len(pending_use_cases)} TOTAL_DONE={len(use_cases)}',flush=True)
    # Expand each unique result to every exact-duplicate path and write a human-readable report.
    expanded=[]
    for h, group in dup.items():
        result=done.get(h)
        if not result: continue
        for p in group:
            item=dict(result); item['path']=str(p.relative_to(ROOT)); item['aliases']=[str(q.relative_to(ROOT)) for q in group if q!=p]
            item['use_cases']=use_cases.get(h,{}).get('use_cases',{})
            expanded.append(item)
    expanded.sort(key=lambda x:x['path'].casefold())
    def cell(s): return str(s or '').replace('|','\\|').replace('\n','<br>').strip()
    report=['# sounds 音效复核（Qwen3.8-Omni-Flash）','',
      f'- 音频文件：{len(paths)} 个；按 SHA-256 去重后调用：{len(representatives)} 个；重复副本：{len(paths)-len(representatives)} 个。',
      '- 判断依据：只提交音频内容；提示词明确禁止根据文件名、路径和目录推断。模型回复不是客观测量报告；声源判断保留置信度和不确定性。',
      '- 模型：`qwen3.8-omni-flash`；分析字段：可听描述、可能声源、置信度、证据、人声/音乐、疑点。',
      '- 精确重复文件复用同一份模型结果，不重复调用。','',
      '| 音频文件 | 时长 | 可听内容 | 可能声源 | 把握 | 适用场景 | 避免场景/实现建议 | 证据 | 人声/音乐 | 不确定性 |','|---|---:|---|---|---|---|---|---|---|---|']
    for item in expanded:
        p=ROOT/item['path']
        pr=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(p)],capture_output=True,text=True)
        try: duration=f'{float(pr.stdout.strip()):.2f}s'
        except: duration='—'
        a=item['analysis']
        u=item.get('use_cases',{})
        evidence=a.get('evidence',[])
        if isinstance(evidence,list): evidence='；'.join(map(str,evidence))
        scenarios=u.get('suitable_scenarios',[])
        if isinstance(scenarios,list): scenarios='；'.join(f'{x.get("scene","")}（{x.get("fit","")}）' if isinstance(x,dict) else str(x) for x in scenarios)
        avoid=u.get('avoid_scenarios',[])
        if isinstance(avoid,list): avoid='；'.join(f'{x.get("scene","")}（{x.get("reason","")}）' if isinstance(x,dict) else str(x) for x in avoid)
        notes='；'.join(x for x in [str(avoid or ''),str(u.get('implementation_notes','') or '')] if x)
        report.append('| '+' | '.join([f'`{cell(item["path"])}`',duration,cell(a.get('audible_description')),cell(a.get('likely_source')),cell(a.get('confidence')),cell(scenarios),cell(notes),cell(evidence),cell(a.get('speech_or_music')),cell(a.get('uncertainty'))])+' |')
    REPORT.write_text('\n'.join(report)+'\n')
    print(f'REPORT={REPORT}\nRESULTS={len(expanded)}/{len(paths)}',flush=True)

if __name__=='__main__': main()
