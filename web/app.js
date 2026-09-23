const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const player = $('#player');
const state = {files: [], filter: 'all', current: null, segments: [], selected: 0, duration: 0, history: [], request: 0, revision: 0, analyzed: false, token: '', jobs: [], saving: false, loading: false, busy: false, status: {}, textQuery: '', segmentPlayback: false};
const labels = {know: '知', do: '行', other: '无关', unclassified: '待分类'};
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const clone = value => JSON.parse(JSON.stringify(value));
function time(seconds, precise = false) {const s = Math.max(0, Math.floor(seconds || 0));return `${Math.floor(s / 60).toString().padStart(2,'0')}:${(s % 60).toString().padStart(2,'0')}${precise ? '.' + Math.floor(((seconds || 0) % 1) * 100).toString().padStart(2,'0') : ''}`;}
function toast(text) {$('#toast').textContent = text;$('#toast').hidden = false;clearTimeout(toast.timer);toast.timer = setTimeout(() => $('#toast').hidden = true, 7000);}
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json','X-Workbench-Token':state.token},body:JSON.stringify(data)});
  const result = await response.json();
  if (!response.ok) throw Error(result.error || `请求失败 (${response.status})`);
  return result;
}
function currentJob() {return state.jobs.find(j => j.media_id === state.current?.id && j.kind === 'analyze' && ['queued','running'].includes(j.status));}
function canEdit() {return state.segments.length && !state.saving && !state.loading && !currentJob();}
function visibleFiles() {const search = $('#library-search').value.toLowerCase();return state.files.filter(f => f.name.toLowerCase().includes(search));}
function drawFiles() {
  $('#library-count').textContent = state.files.length;
  $('#files').innerHTML = visibleFiles().map(f => {
    const busy = state.jobs.some(j => j.media_id === f.id && ['queued','running'].includes(j.status));
    return `<button class="file ${state.current?.id === f.id ? 'active' : ''}" data-id="${f.id}" title="${esc(f.name)}"><span class="file-icon">${esc(f.name.split('.').pop().toUpperCase().slice(0,4))}</span><span class="file-text"><span class="file-name">${esc(f.name)}</span><span class="file-meta">${(f.size/1024/1024).toFixed(0)} MB · ${busy ? '处理中' : f.analyzed ? '已分析' : '待分析'}</span></span></button>`;
  }).join('') || '<div class="empty"><p>暂无匹配素材</p></div>';
  $$('#files button').forEach(b => b.onclick = () => selectFile(b.dataset.id));
}
function adopt(project) {
  state.duration = project.duration;state.segments = project.segments;state.revision = project.revision;state.analyzed = project.analyzed;state.project = project;
  state.selected = Math.min(state.selected,state.segments.length-1);state.history=[];
  $('#draft-state').textContent = '工程已保存到本机';$('#draft-state').classList.remove('error');
}
async function selectFile(id) {
  if(state.saving) return toast('正在保存，请稍候');
  const request = ++state.request;
  const file = state.files.find(f => f.id === id);if(!file)return;setLibrary(false);try{localStorage.setItem('zhixing:last-video',id);}catch{}
  player.pause();state.segmentPlayback=false;state.current=file;state.segments=[];state.selected=0;state.history=[];state.duration=0;state.loading=true;
  $('#video-name').textContent=file.name;$('#video-empty').hidden=true;player.src=`/media/${id}`;player.playbackRate=Number($('#speed').value);
  drawFiles();drawSegments();inspect();updateControls();$('#segment-count').textContent='正在载入工程…';
  try {
    let project = await api(`/api/media/${id}`);
    if(request !== state.request)return;
    // Carry forward the user's v0 choices once; retain the original browser copy.
    if(!project.analyzed && project.revision===0) {
      let old;try {
        old=JSON.parse(localStorage.getItem(`zhixing-v0:${id}`));
        const suffix=Array.from(file.name).map(c=>c.codePointAt(0).toString(16)).join('');
        const prefix=`zhixing-v0:local-${file.size}-`;
        for(let n=0;n<localStorage.length;n++){const key=localStorage.key(n);if(key.startsWith(prefix)&&key.endsWith('-'+suffix)){const imported=JSON.parse(localStorage.getItem(key));if(imported?.duration===project.duration)old=imported;}}
      }catch{}
      if(old?.duration===project.duration && Array.isArray(old.segments)) {
        try {project=await api(`/api/save/${id}`,{revision:project.revision,segments:old.segments});toast('已迁移上一版的分类与保留标记');}catch(error){toast('旧草稿未迁移：'+error.message);}
      }
    }
    adopt(project);
  } catch(error) {toast(error.message);$('#segment-count').textContent='读取失败';}
  finally {if(request===state.request){state.loading=false;drawSegments();inspect();updateControls();}}
}
async function commit(mutator, record=true) {
  if(!canEdit()) return;
  const before = clone(state.segments), selected=state.selected;
  try {mutator();}catch(error){state.segments=before;return toast(error.message);}
  state.saving=true;updateControls();drawSegments();$('#draft-state').textContent='正在保存…';
  try {
    const saved=await api(`/api/save/${state.current.id}`,{revision:state.revision,segments:state.segments});
    if(record){state.history.push(before);if(state.history.length>30)state.history.shift();}
    state.segments=saved.segments;state.revision=saved.revision;state.project=saved;
    $('#draft-state').textContent='工程已保存到本机';$('#draft-state').classList.remove('error');
  } catch(error) {state.segments=before;state.selected=selected;$('#draft-state').textContent='修改未保存';$('#draft-state').classList.add('error');toast(error.message);}
  finally {state.saving=false;drawSegments();inspect();updateControls();}
}
function visibleSegments() {return state.segments.map((s,i)=>({...s,i})).filter(s=>(state.filter==='all'||(state.filter==='review'?!s.reviewed:s.category===state.filter))&&(!state.textQuery||s.text.toLowerCase().includes(state.textQuery)));}
function drawSegments() {
  const scroll=$('#segments').scrollTop;
  $('#duration-label').textContent=state.duration?`${time(state.duration)} 原片`:'—';
  $('#segment-count').textContent=`${state.segments.length} 个片段`;
  $('#segments').innerHTML=visibleSegments().map(s=>`<article class="segment ${s.i===state.selected?'active':''} ${!s.keep?'excluded':''}" data-index="${s.i}" role="button" tabindex="0" aria-label="查看 ${time(s.start,true)} 至 ${time(s.end,true)} 片段"><div><div class="segment-meta"><span class="index">${String(s.i+1).padStart(2,'0')}</span><span class="mono">${time(s.start,true)} — ${time(s.end,true)}</span><span class="pill ${s.category}">${labels[s.category]}</span><span class="review-tag">${s.keep?'保留':'已排除'}</span></div>${s.text?`<p class="transcript">${esc(s.text)}</p>`:`<p class="silent"><span class="silent-bars" aria-hidden="true"><b></b><b></b><b></b><b></b><b></b><b></b></span>${state.analyzed?'未识别到文字 · 查看画面决定保留':'尚未提取文字 · 点击「开始分析」'}</p>`}</div><input type="checkbox" ${s.keep?'checked':''} ${canEdit()?'':'disabled'} aria-label="保留第 ${s.i+1} 个片段"></article>`).join('')||'<div class="empty"><h3>暂无匹配片段</h3><p>选择视频后开始分析，或切换筛选条件。</p></div>';
  $('#segments').scrollTop=scroll;
  $$('#segments .segment').forEach(row=>{
    const index=Number(row.dataset.index);
    row.onclick=()=>seek(index);
    row.onkeydown=e=>{if(e.target===row&&(e.key==='Enter'||e.key===' ')){e.preventDefault();seek(index);}};
    row.querySelector('input').onclick=e=>{e.stopPropagation();const keep=e.target.checked;commit(()=>state.segments[index].keep=keep);};
  });
  $('#timeline').innerHTML=state.segments.map((s,i)=>`<button aria-label="${time(s.start)} ${labels[s.category]}${s.keep?'':'，不保留'}" title="${time(s.start,true)} — ${time(s.end,true)} · ${labels[s.category]}" class="${s.category} ${s.keep?'':'excluded'} ${i===state.selected?'active':''}" style="flex-grow:${s.end-s.start}" data-index="${i}"></button>`).join('');
  $$('#timeline button').forEach(b=>b.onclick=()=>seek(Number(b.dataset.index)));
  $('#kept-duration').textContent=time(state.segments.reduce((sum,s)=>sum+(s.keep?s.end-s.start:0),0));$('#total-duration').textContent=time(state.duration);
  $('#ruler').innerHTML=Array.from({length:7},(_,i)=>`<span>${time(state.duration*i/6)}</span>`).join('');
}
function seek(index) {if(!state.segments[index])return;player.pause();state.selected=index;state.segmentPlayback=false;player.currentTime=state.segments[index].start;drawSegments();inspect();}
function gapTime(seconds) {const ms=Math.round(seconds*1000);return `${String(Math.floor(ms/3600000)).padStart(2,'0')}:${String(Math.floor(ms/60000)%60).padStart(2,'0')}:${String(Math.floor(ms/1000)%60).padStart(2,'0')}.${String(ms%1000).padStart(3,'0')}`;}
function drawGapReview() {
  const rows=state.segments.map((s,i)=>({...s,i})).filter(s=>!s.text.trim());
  const total=rows.reduce((sum,s)=>sum+s.end-s.start,0);
  $('#dialog-body').innerHTML=`<p class="gap-summary">共 ${rows.length} 段，合计 ${total.toFixed(3)} 秒。</p><p class="dialog-description">无文字可能包含动作演示、音乐或漏识别语音。自动分析时沿用前段分类并默认保留，可逐段查看后剪掉；修改会自动保存。</p><div class="gap-review-list">${rows.map(s=>`<div class="gap-review-row ${s.keep?'':'excluded'}"><button class="quiet gap-view" data-index="${s.i}">${gapTime(s.start)} — ${gapTime(s.end)}<small>${(s.end-s.start).toFixed(3)} 秒 · ${labels[s.category]} · ${s.keep?'保留':'已剪掉'}</small></button><button class="button gap-cut" data-index="${s.i}" ${canEdit()?'':'disabled'}>${s.keep?'✂ 剪掉':'恢复保留'}</button></div>`).join('')||'<p class="muted">暂无无文字片段</p>'}</div>`;
  $$('.gap-view').forEach(button=>button.onclick=()=>{$('#dialog').close();seek(Number(button.dataset.index));state.segmentPlayback=true;player.play().catch(e=>toast(e.message));});
  $$('.gap-cut').forEach(button=>button.onclick=async()=>{button.disabled=true;const i=Number(button.dataset.index);await commit(()=>state.segments[i].keep=!state.segments[i].keep);if($('#dialog').open)drawGapReview();});
}
$('#gap-review').onclick=()=>{if(!state.analyzed)return toast('请先完成分析，再审查无文字片段');dialog('无文字片段 · 二次审查','',null,null,'gap-review');drawGapReview();};
function inspect() {
  const s=state.segments[state.selected];
  $('#selected-number').textContent=s?`${String(state.selected+1).padStart(2,'0')} / ${state.segments.length}`:'—';
  $('#start').value=s?s.start.toFixed(2):'';$('#end').value=s?s.end.toFixed(2):'';
  $$('.category-options button').forEach(b=>b.classList.toggle('active',b.dataset.category===s?.category));
  $('#classification-hint').textContent=s?(s.reviewed?'人工复核完成':s.reason||'待人工复核'):'选择一个片段';
  $('#reviewed').textContent=s?.reviewed?'取消已复核标记':'标记为已复核';$('#edit-text').value=s?.text||'';
  updateControls();
}
function updateControls() {
  const editable=!!canEdit(), active=currentJob();
  $('#undo').disabled=!editable||!state.history.length;
  for(const id of ['reviewed','apply-boundary','split','add-segment','save-text','mark-filter','drop-filter'])$('#'+id).disabled=!editable;
  for(const el of $$('.category-options button'))el.disabled=!editable;
  $('#start').disabled=!editable||state.selected===0;$('#end').disabled=!editable||state.selected===state.segments.length-1;$('#edit-text').disabled=!editable;
  $('#play-segment').disabled=!state.segments.length;
  $('#analyze').disabled=!state.current||state.loading||state.saving||!!active;
  $('#analyze').textContent=active?(active.status==='queued'?'等待分析…':'分析中…'):state.analyzed?'重新分析':'开始分析';
  $('#export').disabled=!state.files.length||state.loading||state.saving;
  $('#download-project').disabled=!state.current||state.loading||state.saving;$('#download-text').disabled=!state.current||state.loading;
  $('#restore-project').disabled=!editable;
  $('#analysis-title').textContent=state.analyzed?'文字已提取 · 分类建议等待复核':'提取文字，自动区分「知」与「行」';
  $('#analysis-description').textContent=state.analyzed?'点击文字即可跳到对应片段。分类采用可编辑关键词规则；无文字区间默认保留，请结合画面复核。':analysisEngine==='dashscope'?'使用阿里云百炼 paraformer-v2 并行转写；媒体会临时上传至你配置的 OSS。':'使用本地模型处理，首次使用会自动下载模型。';
  $('.notice').hidden=!!active;const box=$('#active-job');box.hidden=!active;
  if(active){box.innerHTML=`<div><span>${esc(active.stage)}${Number.isFinite(active.progress)?` · ${Math.floor(active.progress)}%`:" · 准备中"}</span><button class="quiet" data-cancel="${active.id}">取消</button></div>${Number.isFinite(active.progress)?`<progress max="100" value="${Math.floor(active.progress)}" aria-label="当前阶段进度 ${Math.floor(active.progress)}%"></progress>`:""}`;box.querySelector('button').onclick=()=>cancelJob(active.id);}
}
function jobHtml(job) {
  const active=['queued','running'].includes(job.status), names={queued:'排队',running:'处理中',completed:'已完成',failed:'失败',cancelled:'已取消'};
  const downloads={'video.mp4':'下载视频','subtitles.srt':'成片字幕','transcript-original.srt':'原片字幕','transcript.txt':'文字稿','transcription.json':'统一转写 JSON','project.json':'工程','cuts.json':'剪辑清单'};
  const engine=job.engine==='dashscope'?'阿里云百炼':'本地';
  return `<div class="job ${job.status}"><div class="job-top"><span class="job-name">${job.kind==='analyze'?'分析':'导出'} · ${esc(job.name)}</span><span class="small">${names[job.status]}</span></div><p class="job-stage">${job.kind==='analyze'?engine+' · '+esc(job.model)+' · ':''}${esc(job.stage)}${job.started?' · 耗时 '+time((job.finished||Date.now()/1000)-job.started):''}${active?(Number.isFinite(job.progress)?' · '+Math.floor(job.progress)+'%':' · 准备中'):''}</p>${active?`${Number.isFinite(job.progress)?`<progress max="100" value="${Math.floor(job.progress)}" aria-label="当前阶段进度 ${Math.floor(job.progress)}%"></progress>`:""}<button class="quiet" data-cancel="${job.id}">取消任务</button>`:''}${job.error?`<p class="job-error">${esc(job.error)}</p>`:''}${['failed','cancelled'].includes(job.status)?`<button class="quiet" data-retry="${job.id}">重试</button>`:''}${job.downloads?.length&&job.status==='completed'?`<div class="job-downloads">${job.downloads.map(d=>`<a href="${esc(d.url)}" download>${downloads[d.name]||esc(d.name)}</a>`).join('')}</div>`:''}</div>`;
}
function wireJobs(container) {container.querySelectorAll('[data-cancel]').forEach(b=>b.onclick=()=>cancelJob(b.dataset.cancel));container.querySelectorAll('[data-retry]').forEach(b=>b.onclick=()=>retryJob(b.dataset.retry));}
function drawJobs() {$('#queue-counts').textContent=`处理中 ${state.jobs.filter(j=>j.status==='running').length} · 等待 ${state.jobs.filter(j=>j.status==='queued').length} · 完成 ${state.jobs.filter(j=>j.status==='completed').length}`;const ordered=[...state.jobs].reverse();$('#jobs').innerHTML=ordered.slice(0,5).map(jobHtml).join('')||'<p class="muted">开始分析后，可在这里查看进度。</p>';wireJobs($('#jobs'));if($('#dialog').open&&$('#dialog').dataset.kind==='jobs'){$('#dialog-body').innerHTML=ordered.map(jobHtml).join('')||'<p class="muted">暂无任务</p>';wireJobs($('#dialog-body'));}}
let pollRunning=false;
async function poll() {
  if(pollRunning)return;pollRunning=true;
  try {
    const result=await api('/api/status'), previous=JSON.stringify(state.jobs.map(j=>[j.id,j.status,j.progress,j.stage]));
    const oldBusy=!!currentJob();state.token=result.token;state.status=result;state.jobs=result.jobs;updateDownloadPanel();
    drawJobs();
    if(previous!==JSON.stringify(state.jobs.map(j=>[j.id,j.status,j.progress,j.stage]))){
      drawJobs();state.files=await api('/api/library');drawFiles();
      if(oldBusy&&!currentJob()&&state.current&&!state.loading&&!state.saving){const id=state.current.id;const p=await api(`/api/media/${id}`);if(state.current?.id===id&&p.revision!==state.revision){adopt(p);drawSegments();inspect();toast('分析完成，已载入真实文字与分类建议');}}
    }
    if(oldBusy!==!!currentJob())drawSegments();updateControls();
  } catch(error) {$('#draft-state').textContent='本地服务连接中断';$('#draft-state').classList.add('error');}
  finally {pollRunning=false;}
}
async function cancelJob(id) {try {await api('/api/cancel/'+id,{});await poll();}catch(e){toast(e.message);}}
async function retryJob(id) {const j=state.jobs.find(j=>j.id===id);if(!j)return;try {await api(j.kind==='analyze'?'/api/analyze':'/api/export',{ids:[j.media_id],mode:j.mode,model:j.model||analysisModel,engine:j.engine||'local'});await poll();}catch(e){toast(e.message);}}
function dialog(title, body, submitText, action, kind='form') {
  $('#dialog-title').textContent=title;$('#dialog-body').innerHTML=body;$('#dialog-submit').textContent=submitText||'确认';$('#dialog-submit').hidden=!action;$('#dialog').dataset.kind=kind;
  $('#dialog-submit').onclick=async()=>{const b=$('#dialog-submit');b.disabled=true;try {await action();$('#dialog').close();}catch(error){toast(error.message);}finally{b.disabled=false;}};
  $('#dialog').showModal();
}
const analysisModels={'tiny':'Tiny','base':'Base','small':'Small','medium':'Medium','large-v3':'Large-v3'};
let analysisModel='large-v3';try{const saved=localStorage.getItem('autocut:model');if(Object.hasOwn(analysisModels,saved))analysisModel=saved;}catch{}
let analysisEngine='local';try{if(localStorage.getItem('autocut:engine')==='dashscope')analysisEngine='dashscope';}catch{}
function updateModelSetting(){ $('#analysis-settings').title=analysisEngine==='dashscope'?'当前方式：阿里云百炼 · paraformer-v2':'当前方式：本地 · '+analysisModels[analysisModel]; }
updateModelSetting();
$('#analysis-settings').onclick=()=>{
  const cloud=state.status.dashscope||{},missing=(cloud.missing||[]).join('、');
  dialog('分析设置',`<p class="dialog-description">选择下一次单个或批量分析的转录方式。不会改变正在运行的任务，也不会自动覆盖已有转录。</p><label for="analysis-engine">转录方式</label><select id="analysis-engine" class="export-mode"><option value="local" ${analysisEngine==='local'?'selected':''}>本地转录 · 数据不上传</option><option value="dashscope" ${analysisEngine==='dashscope'?'selected':''}>阿里云百炼 · paraformer-v2</option></select><div id="local-analysis-settings"><label for="analysis-model">本地转录模型</label><select id="analysis-model" class="export-mode">${Object.entries(analysisModels).map(([key,name])=>`<option value="${key}" ${key===analysisModel?'selected':''}>${name}${key==='large-v3'?' · 默认高精度配置':''}</option>`).join('')}</select><label for="analysis-concurrency">本地同时分析数量</label><select id="analysis-concurrency" class="export-mode">${[1,2,3].map(n=>`<option value="${n}" ${n===(state.status.analysis_concurrency||2)?'selected':''}>${n} 个任务</option>`).join('')}</select><div id="model-download-panel" aria-live="polite"></div></div><div id="cloud-analysis-settings"><div class="cloud-status ${cloud.configured?'ready':'missing'}"><strong>${cloud.configured?'云端配置已就绪':'云端配置未完成'}</strong><span>固定模型：paraformer-v2</span>${cloud.error?`<p>${esc(cloud.error)}</p>`:missing?`<p>缺少环境变量：${esc(missing)}</p>`:'<p>凭证只从环境变量读取，不会发送到前端。</p>'}</div><label for="cloud-concurrency">云端同时分析数量</label><input id="cloud-concurrency" class="export-mode" type="number" min="1" max="50" step="1" value="${state.status.cloud_concurrency||30}"><p class="dialog-description">支持 1–50 个任务并行。每个媒体会临时上传至你配置的 OSS，生成 72 小时签名地址；转写结束后自动删除临时对象。</p></div>`,'保存设置',async()=>{
    const engine=$('#analysis-engine').value;if(!['local','dashscope'].includes(engine))throw Error('请选择有效的转录方式');
    const model=$('#analysis-model').value;if(!Object.hasOwn(analysisModels,model))throw Error('请选择有效模型');
    const cloudConcurrency=Number($('#cloud-concurrency').value);if(!Number.isInteger(cloudConcurrency)||cloudConcurrency<1||cloudConcurrency>50)throw Error('云端同时分析数量必须为 1–50');
    if(engine==='dashscope'&&!cloud.configured)throw Error(cloud.error||'云端配置未完成：'+missing);
    await api('/api/settings',{analysis_concurrency:Number($('#analysis-concurrency').value),cloud_concurrency:cloudConcurrency});await poll();analysisModel=model;analysisEngine=engine;try{localStorage.setItem('autocut:model',model);localStorage.setItem('autocut:engine',engine);}catch{}updateModelSetting();updateControls();toast(engine==='dashscope'?'下一次分析使用阿里云百炼 paraformer-v2':'下一次分析使用本地 '+analysisModels[model]);
  });
  const switchPanels=()=>{const cloudSelected=$('#analysis-engine').value==='dashscope';$('#local-analysis-settings').hidden=cloudSelected;$('#cloud-analysis-settings').hidden=!cloudSelected;if(!cloudSelected)updateDownloadPanel();};
  $('#analysis-engine').onchange=switchPanels;$('#analysis-model').onchange=updateDownloadPanel;switchPanels();
};
function updateDownloadPanel(){
  if(!$('#dialog').open||!$('#model-download-panel'))return;
  const model=$('#analysis-model').value;
  const statuses={downloaded:'已下载',missing:'未下载',queued:'等待下载',downloading:'正在下载',cancelling:'正在取消',cancelled:'已取消',failed:'下载失败'};
  for(const option of $('#analysis-model').options){const m=state.status.models?.find(x=>x.model===option.value);option.textContent=analysisModels[option.value]+' · '+(m?statuses[m.status]:'正在检查');}
  const info=state.status.models?.find(x=>x.model===model),box=$('#model-download-panel');
  if(!info){box.textContent='正在检查本地模型…';return;}
  const active=['queued','downloading','cancelling'].includes(info.status);
  const formatBytes=n=>n>=1024**3?(n/1024**3).toFixed(2)+' GB':(n/1024**2).toFixed(1)+' MB';const size=formatBytes(info.bytes);const percentage=Number.isFinite(info.progress)?Math.floor(info.progress):null;
  const markup=`<div class="model-download-top"><strong>${statuses[info.status]}${percentage!==null?` · ${percentage}%`:""}</strong><span class="small">${info.total_bytes?size+' / '+formatBytes(info.total_bytes):info.bytes?'本机已缓存 '+size:''}</span></div>${active&&percentage!==null?`<progress max="100" value="${percentage}" aria-label="模型下载进度 ${percentage}%"></progress>`:''}<p class="dialog-description">${info.ready?'模型文件已就绪，可直接用于本地转写。':active?(percentage===null?'正在获取文件总大小，稍后显示真实百分比。':'进度按已下载字节计算；关闭窗口不会中断下载。'):'可提前下载此模型，准备好后再转写视频。下载不会上传视频。'}</p>${info.error?`<p class="job-error">${esc(info.error)}</p>`:''}${info.ready&&!active?'<button class="button" disabled>已下载</button>':active?`<button class="button" id="cancel-model" ${info.status==='cancelling'?'disabled':''}>取消下载</button>`:'<button class="button primary" id="download-model">'+(['failed','cancelled'].includes(info.status)?'重试下载':'一键下载')+'</button>'}`;
  if(box.innerHTML!==markup)box.innerHTML=markup;
  if($('#download-model'))$('#download-model').onclick=async()=>{const button=$('#download-model');button.disabled=true;try{await api('/api/models/download',{model});await poll();updateDownloadPanel();}catch(e){toast(e.message);button.disabled=false;}};
  if($('#cancel-model'))$('#cancel-model').onclick=async()=>{try{await api('/api/models/cancel',{model});await poll();}catch(e){toast(e.message);}};
}
async function analyze(ids) {const result=await api('/api/analyze',{ids,model:analysisModel,engine:analysisEngine});toast(result.jobs.length?`已加入 ${result.jobs.length} 个${analysisEngine==='dashscope'?'云端':'本地'}分析任务`:'所选媒体已在队列中');await poll();}
$('#analyze').onclick=()=>{if(state.analyzed||state.revision>0){dialog('分析当前视频','<p class="dialog-description">重新转写会替换当前时间轴和人工修改，旧工程会备份到本机 data/backups。若只需调整分类，可直接编辑片段。</p>','重新分析',()=>analyze([state.current.id]));}else analyze([state.current.id]).catch(e=>toast(e.message));};
function batchChoices(files, selected, quickUnanalyzed=false, quickFirst200=false) {return `<div class="batch-controls"><button id="select-all" class="quiet">全选</button>${quickFirst200?'<button id="select-first-200" class="quiet primary">选择 200 个</button>':''}${quickUnanalyzed?'<button id="select-next-200" class="quiet primary">选择最近未分析的 200 个</button>':''}<button id="select-none" class="quiet">清空</button><span id="batch-selection-count" class="small" aria-live="polite"></span></div><div class="batch-list">${files.map(f=>`<label class="batch-row"><input type="checkbox" value="${f.id}" data-analyzed="${f.analyzed?'true':'false'}" ${selected(f)?'checked':''}><span>${esc(f.name)}</span><small>${f.analyzed?'已分析':'未分析'}</small></label>`).join('')}</div>`;}
function wireChoices() {
  const inputs=()=>$$('.batch-list input');
  const updateCount=()=>{const count=inputs().filter(x=>x.checked).length;if($('#batch-selection-count'))$('#batch-selection-count').textContent=`已选择 ${count} 个`;};
  inputs().forEach(x=>x.onchange=updateCount);
  $('#select-all').onclick=()=>{inputs().forEach(x=>x.checked=true);updateCount();};
  $('#select-none').onclick=()=>{inputs().forEach(x=>x.checked=false);updateCount();};
  if($('#select-first-200'))$('#select-first-200').onclick=()=>{
    const all=inputs(),selected=all.slice(0,200);
    all.forEach(x=>x.checked=false);selected.forEach(x=>x.checked=true);updateCount();
    toast(selected.length===200?'已选择列表中的前 200 个视频':`当前只有 ${selected.length} 个视频，已全部选择`);
  };
  if($('#select-next-200'))$('#select-next-200').onclick=()=>{
    const all=inputs(),pending=all.filter(x=>x.dataset.analyzed==='false').slice(0,200);
    all.forEach(x=>x.checked=false);pending.forEach(x=>x.checked=true);updateCount();
    toast(pending.length===200?'已选择列表中最近的 200 个未分析视频':`未分析视频不足 200 个，已选择剩余 ${pending.length} 个`);
  };
  updateCount();
}
function chosen() {const ids=$$('.batch-list input:checked').map(x=>x.value);if(!ids.length)throw Error('请至少选择一个媒体文件');return ids;}
$('#batch').onclick=()=>{const files=visibleFiles(),cloud=analysisEngine==='dashscope';dialog('批量分析',`<p class="dialog-description">${cloud?'使用阿里云百炼并行提交，当前最多同时处理 '+(state.status.cloud_concurrency||30)+' 个任务。':'按本机设置的并行数量处理。'}单次最多选择 200 个；“选择最近未分析的 200 个”会按当前素材列表顺序自动跳过已分析视频。单个失败不影响其余任务；重新分析会先备份旧工程。</p>`+batchChoices(files,f=>!f.analyzed,true),'开始批量分析',()=>analyze(chosen()));wireChoices();};
$('#export').onclick=()=>{
  dialog('导出', '<p class="dialog-description">完整逐字稿包含全部已保存文字，不受勾选影响；成片字幕只包含保留内容并重排时间。文字可直接导出，无需先生成视频。单次最多选择 200 个；“选择 200 个”会按当前列表顺序勾选前 200 个。多选自动打包 ZIP；未分析完成的素材请取消选择。</p><select id="export-format" class="export-mode"><option value="original-srt">完整逐字稿 SRT</option><option value="kept-srt">成片字幕 SRT</option><option value="txt">纯文字稿 TXT</option><option value="video">剪辑视频 MP4</option></select><label id="video-mode-label" hidden>视频范围<select id="export-mode" class="export-mode"><option value="kept">全部保留片段</option><option value="know">仅「知」的保留片段</option><option value="do">仅「行」的保留片段</option></select></label>'+batchChoices(visibleFiles(),f=>f.id===state.current?.id,false,true), '导出', async()=>{
    const ids=chosen(),format=$('#export-format').value;
    if(state.saving)throw Error('文字正在保存，请稍后重试');
    if(format==='video'){const r=await api('/api/export',{ids,mode:$('#export-mode').value});toast(`已加入 ${r.jobs.length} 个视频导出任务`);await poll();return;}
    const response=await fetch('/api/text-export',{method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':state.token},body:JSON.stringify({ids,format})});
    if(!response.ok){const error=await response.json();throw Error(error.error||'导出失败');}
    const header=response.headers.get('Content-Disposition')||'',match=header.match(/filename\*=UTF-8''([^;]+)/i);
    const url=URL.createObjectURL(await response.blob()),link=document.createElement('a');
    link.href=url;link.download=match?decodeURIComponent(match[1]):(ids.length>1?'AutoCut_文字导出.zip':format==='txt'?'文字稿.txt':'逐字稿.srt');document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);toast(`已导出 ${ids.length} 个素材的文字`);
  });wireChoices();$('#export-format').onchange=()=>{$('#video-mode-label').hidden=$('#export-format').value!=='video';};
};
$('#rules').onclick=()=>{const rules=state.status.rules;dialog('课程分类规则','<p class="dialog-description">本地规则根据转写中的词语提出分类建议，不是语义大模型。每行一个关键词，保存后用于下一次分析。说明原因的语句优先归为「知」，所有结果仍需复核。</p>'+['know','do','other'].map(k=>`<label class="rule-field">${labels[k]}<textarea data-rule="${k}">${esc((rules?.[k]||[]).join('\n'))}</textarea></label>`).join(''),'保存规则',async()=>{const obj={};$$('[data-rule]').forEach(el=>obj[el.dataset.rule]=el.value.split('\n').map(x=>x.trim()).filter(Boolean));await api('/api/rules',obj);await poll();toast('规则已保存，下次分析生效');});};
$('#all-jobs').onclick=()=>{dialog('处理队列',[...state.jobs].reverse().map(jobHtml).join('')||'<p class="muted">暂无任务</p>',null,null,'jobs');wireJobs($('#dialog-body'));};
$('#close-dialog').onclick=()=>$('#dialog').close();
function remapText(segment, allCues) {segment.cues=allCues.filter(c=>{const mid=(c.start+c.end)/2;return mid>=segment.start&&mid<segment.end;}).map(c=>({...c,start:Math.max(segment.start,c.start),end:Math.min(segment.end,c.end)}));segment.text=segment.cues.map(c=>c.text).join('').trim();segment.reviewed=false;segment.reason='已手动调整边界，请复核文字与画面';}
$('#split').onclick=()=>commit(()=>{const index=state.selected,s=state.segments[index],at=Number(player.currentTime.toFixed(4));if(at<=s.start+.05||at>=s.end-.05)throw Error('请把播放位置放在当前片段内部，距边界至少 0.05 秒');const left=clone(s),right=clone(s);left.end=at;right.start=at;const cues=s.cues?.length?s.cues:s.text?[{start:s.start,end:s.end,text:s.text}]:[];remapText(left,cues);remapText(right,cues);state.segments.splice(index,1,left,right);});
function insertSegment(start,end) {
  if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||end>state.duration||end-start<.05)throw Error('请输入有效的起止时间，新片段至少 0.05 秒且不能超出视频范围');
  const touched=state.segments.filter(s=>s.end>start&&s.start<end);
  if(!touched.length)throw Error('该时间范围没有可切出的内容');
  const cues=touched.flatMap(s=>s.cues?.length?s.cues:s.text?[{start:s.start,end:s.end,text:s.text}]:[]);
  const result=[];let inserted=-1;
  for(const source of state.segments) {
    if(source.end<=start||source.start>=end){result.push(clone(source));continue;}
    if(source.start<start){const left=clone(source);left.end=start;remapText(left,cues);result.push(left);}
    if(inserted<0){const added={start,end,text:'',category:'unclassified',reason:'人工新增片段，请分类并复核',confidence:'low',keep:true,reviewed:false,cues:[]};remapText(added,cues);added.reason='人工新增片段，请分类并复核';result.push(added);inserted=result.length-1;}
    if(source.end>end){const right=clone(source);right.start=end;remapText(right,cues);result.push(right);}
  }
  if(inserted<0)throw Error('无法增加片段，请检查时间范围');
  state.segments=result;state.selected=inserted;state.filter='all';$$('[data-filter]').forEach(b=>b.classList.toggle('active',b.dataset.filter==='all'));
}
$('#add-segment').onclick=()=>{
  const current=state.segments[state.selected];if(!current)return;
  const at=Math.min(current.end-.05,Math.max(current.start,Number(player.currentTime)||current.start));
  const defaultEnd=Math.min(current.end,at+Math.min(5,Math.max(.05,current.end-at)));
  dialog('增加片段',`<p class="dialog-description">从现有时间轴中切出一段遗漏内容。新片段会设为“待分类”并默认保留，前后片段会自动衔接。</p><div class="add-segment-fields"><label for="add-start">开始时间（秒）<input id="add-start" type="number" min="0" max="${state.duration}" step="0.01" value="${at.toFixed(2)}"></label><span>至</span><label for="add-end">结束时间（秒）<input id="add-end" type="number" min="0" max="${state.duration}" step="0.01" value="${defaultEnd.toFixed(2)}"></label></div><p class="dialog-description">当前默认起点取自播放位置，可直接输入精确时间。</p>`,'增加片段',async()=>{const start=Number($('#add-start').value),end=Number($('#add-end').value);if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||end>state.duration||end-start<.05)throw Error('请输入有效的起止时间，新片段至少 0.05 秒且不能超出视频范围');await commit(()=>insertSegment(start,end));});
};
$('#apply-boundary').onclick=()=>{const a=Number($('#start').value),b=Number($('#end').value);commit(()=>{const i=state.selected,s=state.segments[i],prev=state.segments[i-1],next=state.segments[i+1];if(!Number.isFinite(a)||!Number.isFinite(b)||a>=b||a<0||b>state.duration||(prev?a<=prev.start:a!==0)||(next?b>=next.end:Math.abs(b-state.duration)>.02))throw Error('边界须在相邻片段范围内，且不能使片段长度为零');const affected=[prev,s,next].filter(Boolean),cues=affected.flatMap(x=>x.cues?.length?x.cues:x.text?[{start:x.start,end:x.end,text:x.text}]:[]);s.start=prev?a:0;s.end=next?b:state.duration;if(prev)prev.end=s.start;if(next)next.start=s.end;affected.forEach(x=>remapText(x,cues));});};
$('#save-text').onclick=()=>{const text=$('#edit-text').value.trim();commit(()=>{const s=state.segments[state.selected];s.text=text;s.cues=text?[{start:s.start,end:s.end,text}]:[];s.reviewed=false;s.reason='文字已手动修正';});};
$$('[data-filter]').forEach(b=>b.onclick=()=>{state.filter=b.dataset.filter;$$('[data-filter]').forEach(x=>x.classList.toggle('active',x===b));drawSegments();});
$$('[data-category]').forEach(b=>b.onclick=()=>commit(()=>{const s=state.segments[state.selected];s.category=b.dataset.category;s.keep=s.category!=='other';s.reason='人工分类';s.reviewed=true;}));
$('#reviewed').onclick=()=>{if(state.segments[state.selected]?.category==='unclassified')return toast('请先选择内容分类');commit(()=>state.segments[state.selected].reviewed=!state.segments[state.selected].reviewed);};
$('#undo').onclick=async()=>{const prior=state.history[state.history.length-1];if(!prior)return;const previousRevision=state.revision;await commit(()=>{state.segments=clone(prior);state.selected=Math.min(state.selected,state.segments.length-1);},false);if(state.revision!==previousRevision)state.history.pop();updateControls();};
$('#mark-filter').onclick=()=>{const indexes=visibleSegments().map(s=>s.i);commit(()=>indexes.forEach(i=>state.segments[i].keep=true));};
$('#drop-filter').onclick=()=>{const indexes=visibleSegments().map(s=>s.i);commit(()=>indexes.forEach(i=>state.segments[i].keep=false));};
$('#library-search').oninput=drawFiles;$('#text-search').oninput=e=>{state.textQuery=e.target.value.toLowerCase();drawSegments();};
$('#speed').onchange=e=>player.playbackRate=Number(e.target.value);
$('#play-segment').onclick=()=>{seek(state.selected);state.segmentPlayback=true;player.play().catch(e=>toast(e.message));};
player.onerror=()=>{if(state.current)toast('浏览器无法播放该编码，可尝试导出为 MP4；原片仍可分析。');};
function enforceKeptPreview() {
  if(player.paused || state.segmentPlayback || !state.segments.length)return;
  const at=player.currentTime;
  const next=state.segments.find(s=>s.keep && s.end>at);
  if(!next){player.pause();return;}
  if(at<next.start)player.currentTime=next.start;
}
function previewTick() {
  if(player.paused)return;
  const s=state.segments[state.selected];
  if(state.segmentPlayback && s && player.currentTime>=s.end){
    if($('#loop').checked)player.currentTime=s.start;
    else{player.pause();state.segmentPlayback=false;}
  }else enforceKeptPreview();
  if(!player.paused)state.previewFrame=requestAnimationFrame(previewTick);
}
player.onplay=()=>{$('#preview-mode').textContent=state.segmentPlayback?'查看原片段（含未保留内容）':'仅播放保留片段';cancelAnimationFrame(state.previewFrame);enforceKeptPreview();state.previewFrame=requestAnimationFrame(previewTick);};
player.onpause=()=>{cancelAnimationFrame(state.previewFrame);state.segmentPlayback=false;$('#preview-mode').textContent='仅播放保留片段';};
player.onseeking=()=>enforceKeptPreview();
player.ontimeupdate=()=>{enforceKeptPreview();const at=player.currentTime;$('#clock').textContent=`${time(at,true)} / ${time(state.duration)} 原片位置`;$$('.segment.playing').forEach(x=>x.classList.remove('playing'));const row=$(`.segment[data-index="${state.segments.findIndex(x=>at>=x.start&&at<x.end)}"]`);if(row)row.classList.add('playing');};
player.onended=()=>{const s=state.segments[state.selected];if(state.segmentPlayback&&$('#loop').checked&&s){player.currentTime=s.start;player.play().catch(()=>{});}else state.segmentPlayback=false;};
$('#preview-kept').onclick=()=>{state.segmentPlayback=false;const first=state.segments.find(s=>s.keep);if(!first){player.pause();return toast('尚未勾选任何保留片段');}player.currentTime=first.start;player.play().catch(e=>toast(e.message));};
player.onloadedmetadata=()=>{player.playbackRate=Number($('#speed').value);};
document.addEventListener('keydown',e=>{if(e.code==='Space'&&$('#library-panel').hidden&&!$('#dialog').open&&!['INPUT','SELECT','TEXTAREA','BUTTON','VIDEO'].includes(e.target.tagName)&&e.target.getAttribute('role')!=='button'&&state.current){e.preventDefault();state.segmentPlayback=false;if(player.paused)player.play().catch(err=>toast(err.message));else player.pause();}});
$('#import').onclick=()=>$('#file-input').click();
function upload(file) {return new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open('PUT','/api/upload?name='+encodeURIComponent(file.name));xhr.setRequestHeader('X-Workbench-Token',state.token);xhr.upload.onprogress=e=>{if(e.lengthComputable)$('#import').textContent=`导入 ${Math.round(e.loaded/e.total*100)}%`;};xhr.onload=()=>{try{const data=JSON.parse(xhr.responseText);if(xhr.status>=400)throw Error(data.error);resolve(data);}catch(e){reject(e);}};xhr.onerror=()=>reject(Error('本地导入失败，请检查服务是否运行'));xhr.send(file);});}
$('#file-input').onchange=async e=>{const files=[...e.target.files];$('#import').disabled=true;let first;for(const file of files){try{const result=await upload(file);first ||= result.id;state.files=result.library;drawFiles();}catch(error){toast(file.name+'：'+error.message);}}$('#import').disabled=false;$('#import').textContent='＋ 导入媒体';drawFiles();if(first)await selectFile(first);e.target.value='';};
$('#refresh-library').onclick=async()=>{try{state.files=await api('/api/scan',{});drawFiles();toast('素材库已刷新');}catch(e){toast(e.message);}};
$('#download-project').onclick=()=>{if(state.current)window.location.href='/project/'+state.current.id;};
$('#download-text').onclick=()=>{if(state.current)$('#export').click();};
$('#restore-project').onclick=()=>$('#project-input').click();
$('#project-input').onchange=async e=>{try{const p=JSON.parse(await e.target.files[0].text());if(p.name!==state.current.name||Math.abs(p.duration-state.duration)>.01)throw Error('工程与当前视频名称或时长不匹配，请先选择对应源视频');await commit(()=>{state.segments=p.segments;state.selected=0;});}catch(error){toast('恢复失败：'+error.message);}e.target.value='';};
function setLibrary(open){const panel=$('#library-panel'),wasOpen=!panel.hidden;panel.hidden=!open;$('#library-backdrop').hidden=!open;$('#toggle-library').setAttribute('aria-expanded',String(open));if(open)$('#library-search').focus();else if(wasOpen)$('#toggle-library').focus();}
$('#toggle-library').onclick=()=>setLibrary($('#library-panel').hidden);
$('#close-library').onclick=()=>setLibrary(false);$('#library-backdrop').onclick=()=>setLibrary(false);
document.addEventListener('keydown',e=>{if($('#library-panel').hidden)return;if(e.key==='Escape'){e.preventDefault();setLibrary(false);}else if(e.key==='Tab'){const items=[...$('#library-panel').querySelectorAll('button:not(:disabled),input:not(:disabled)')];const first=items[0],last=items.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}});
function setFontSize(value, persist=true){
  const size=Math.min(32,Math.max(14,Math.round(Number(value)||18)));
  const list=$('#segments'),top=list.getBoundingClientRect().top;
  const anchor=[...list.querySelectorAll('.segment')].find(row=>row.getBoundingClientRect().bottom>top);
  const offset=anchor?anchor.getBoundingClientRect().top-top:0;
  list.style.setProperty('--transcript-size',size+'px');
  $('#font-size').value=size;$('#font-size-value').textContent=size+'px';
  $('#font-smaller').disabled=size===14;$('#font-larger').disabled=size===32;
  if(anchor)list.scrollTop+=anchor.getBoundingClientRect().top-top-offset;
  if(persist){try{localStorage.setItem('zhixing:font-size',String(size));}catch{}}
}
$('#font-size').oninput=e=>setFontSize(e.target.value);
$('#font-smaller').onclick=()=>setFontSize(Number($('#font-size').value)-1);
$('#font-larger').onclick=()=>setFontSize(Number($('#font-size').value)+1);
const fontFamilies={
  songti:'"SimSun", "Songti SC", "STSong", serif',
  heiti:'"SimHei", "Heiti SC", "STHeiti", "PingFang SC", sans-serif',
  yahei:'"Microsoft YaHei", "PingFang SC", "Heiti SC", sans-serif',
  kaiti:'"KaiTi", "Kaiti SC", "STKaiti", "Songti SC", serif',
  fangsong:'"FangSong", "STFangsong", "STFangSong", "Songti SC", "SimSun", serif'
};
function setFontFamily(value,persist=true){
  const name=Object.hasOwn(fontFamilies,value)?value:'songti';
  const list=$('#segments'),top=list.getBoundingClientRect().top;
  const anchor=[...list.querySelectorAll('.segment')].find(row=>row.getBoundingClientRect().bottom>top);
  const offset=anchor?anchor.getBoundingClientRect().top-top:0;
  document.documentElement.style.setProperty('--workbench-font',fontFamilies[name]);
  $('#font-family').value=name;
  if(anchor)list.scrollTop+=anchor.getBoundingClientRect().top-top-offset;
  if(persist){try{localStorage.setItem('zhixing:font-family',name);}catch{}}
}
$('#font-family').onchange=e=>setFontFamily(e.target.value);
let savedFamily='songti';try{savedFamily=localStorage.getItem('zhixing:font-family')||'songti';}catch{}setFontFamily(savedFamily,false);
let savedFont=18;try{savedFont=localStorage.getItem('zhixing:font-size')||18;}catch{}setFontSize(savedFont,false);
function createSky(){
  const stars=document.createDocumentFragment();
  for(let i=0;i<64;i++){
    const star=document.createElement('i');star.className='sky-star';
    star.style.cssText=`left:${((i*61.803)%100).toFixed(2)}%;top:${((i*37.219+7)%100).toFixed(2)}%;--star-size:${i%9===0?2.5:i%3===0?1.8:1}px;--twinkle:${4+i%7}s;--phase:-${i*.71}s;--star-opacity:${i%9===0?.8:.45}`;
    stars.append(star);
  }
  $('#sky-stars').append(stars);
}
createSky();
document.addEventListener('visibilitychange',()=>document.documentElement.classList.toggle('sky-paused',document.hidden));
function setTheme(value,persist=true){
  const dark=value==='dark';document.documentElement.dataset.theme=dark?'dark':'light';
  const button=$('#theme-toggle');button.textContent=dark?'日间模式':'夜间模式';
  button.setAttribute('aria-label',dark?'切换为日间模式':'切换为夜间模式');button.setAttribute('aria-pressed',String(dark));
  if(persist){try{localStorage.setItem('autocut:theme',dark?'dark':'light');}catch{}}
}
$('#theme-toggle').onclick=()=>setTheme(document.documentElement.dataset.theme==='dark'?'light':'dark');
setTheme(document.documentElement.dataset.theme,false);
async function init(){try{state.status=await api('/api/status');state.token=state.status.token;state.jobs=state.status.jobs;state.files=await api('/api/library');drawFiles();drawJobs();let last;try{last=localStorage.getItem('zhixing:last-video');}catch{}const file=state.files.find(f=>f.id===last)||state.files.find(f=>f.analyzed)||state.files[0];if(file)await selectFile(file.id);else updateControls();if(!state.status.asr_installed&&!state.status.dashscope?.configured)toast('尚未配置可用的转写方式：请安装本地组件或配置阿里云环境变量');}catch(e){toast(e.message);$('#files').innerHTML='<p class="muted">本地服务不可用，请重新启动工作台。</p>';}setInterval(poll,1500);}
init();
