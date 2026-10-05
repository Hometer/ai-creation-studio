'use client';

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { ArrowLeft, Check, ClipboardList, Layers, Loader2, Plus, RefreshCw, ShieldCheck, X } from 'lucide-react';
import BrandHeader from './BrandHeader';
import MediaImage from './MediaImage';
import { displayDefinition, mediaUrl, targetOptions } from '@/lib/assets';
import { BLOCKERS, IMPACTS, MODES, ROLES, PlanApiError, buildInput, checkPlan, confirmPlan, eligibleReferences, newDraft, readWorkspace, savePlan, type Binding, type Draft, type Mode, type Options, type Plan, type Role, type Workspace } from '@/lib/generationPlans';
import { errorMessage } from '@/lib/workflowTypes';

const time = (s: string) => new Date(s).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
const label = (p: Plan) => !p.current ? '已受修改影响' : p.confirmation ? '已确认本地输入' : '待核对';
function PinnedPreview({ binding }: { binding: Binding }) {
  const [failed, setFailed] = useState(false), [attempt, setAttempt] = useState(0);
  const v = binding.version;
  return <article className="plan-binding"><div className="plan-binding-heading"><strong>{v.name}</strong><span>{ROLES[binding.role]} · v{v.number}</span></div>
    {v.media ? failed ? <div className="plan-media-error"><p>此版本图片暂时无法读取</p><button className="plan-button" onClick={() => { setFailed(false); setAttempt(n => n + 1); }}>重新加载图片</button></div> : <MediaImage src={mediaUrl(binding.asset_id, v.version_id) + (attempt ? `?retry=${attempt}` : '')} alt={`${v.name}，固定版本 ${v.number}`} width={v.media.width} height={v.media.height} onError={() => setFailed(true)} /> : <p className="plan-definition">{displayDefinition(v)}</p>}
    <small>{binding.target.segment_id ? `片段 ${binding.target.segment_id}${binding.target.shot_number ? ` · 镜头 ${binding.target.shot_number}` : ''}` : '项目级引用'} · 保存时固定</small>
  </article>;
}

export default function ProjectPlans({ projectId }: { projectId: string }) {
  const [data, setData] = useState<Workspace | null>(null), [reading, setReading] = useState(true), [readError, setReadError] = useState('');
  const [selected, setSelected] = useState(''), [draft, setDraft] = useState<Draft | null>(null), [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false), [actionError, setActionError] = useState(''), [needsRead, setNeedsRead] = useState(false), [message, setMessage] = useState('');
  const [preview, setPreview] = useState<{ plan: string; hash: string; count: number; images: number } | null>(null);
  const busyRef = useRef(false), mounted = useRef(false), order = useRef(0), dirtyRef = useRef(false), selectedRef = useRef('');
  const detail = useRef<HTMLElement>(null);
  const editing = draft !== null;
  useEffect(() => {
    if (!editing) return;
    if (window.innerWidth < 1000) detail.current?.scrollIntoView({ block: 'start' });
    detail.current?.querySelector('select')?.focus({ preventScroll: true });
  }, [editing]);
  useEffect(() => { dirtyRef.current = draft !== null; selectedRef.current = selected; }, [draft, selected]);
  const plan = data?.plans.find(p => p.plan_id === selected);
  const ready = Boolean(data && !reading && !readError && !needsRead && !busy);
  const canWrite = ready && Boolean(data?.project);
  const segments = data?.project ? targetOptions(data.project).filter(t => t.segment_id && t.shot_number === null) : [];
  const candidates = data && draft ? eligibleReferences(data, draft.segment) : [];
  const read = useCallback(async () => {
    const sequence = ++order.current;
    setReading(true); setReadError(''); setAck(false); setPreview(null);
    try {
      const fresh = await readWorkspace(projectId);
      if (!mounted.current || sequence !== order.current) return;
      setData(fresh); setNeedsRead(false);
    } catch (e) { if (mounted.current && sequence === order.current) setReadError(errorMessage(e)); }
    finally { if (mounted.current && sequence === order.current) setReading(false); }
  }, [projectId]);
  useEffect(() => {
    mounted.current = true;
    const initial = new URL(window.location.href).searchParams.get('plan') ?? '';
    void Promise.resolve().then(() => { if (mounted.current) { setSelected(initial); void read(); } });
    return () => { mounted.current = false; };
  }, [read]);
  useEffect(() => {
    const before = (e: BeforeUnloadEvent) => { if (dirtyRef.current || busyRef.current) { e.preventDefault(); e.returnValue = ''; } };
    const navigation = (e: MouseEvent) => {
      const link = e.target instanceof Element ? e.target.closest('a[href]') : null;
      if (!link || !dirtyRef.current && !busyRef.current) return;
      if (busyRef.current || !window.confirm('还有未保存的计划，确定离开吗？')) { e.preventDefault(); e.stopPropagation(); }
    };
    const pop = () => {
      if (busyRef.current || dirtyRef.current && !window.confirm('还有未保存的计划，确定离开表单吗？')) {
        const url = new URL(window.location.href); if (selectedRef.current) url.searchParams.set('plan', selectedRef.current); else url.searchParams.delete('plan');
        window.history.pushState(window.history.state, '', url); return;
      }
      setSelected(new URL(window.location.href).searchParams.get('plan') ?? ''); setDraft(null); setAck(false); setPreview(null);
    };
    window.addEventListener('beforeunload', before); document.addEventListener('click', navigation, true); window.addEventListener('popstate', pop);
    return () => { window.removeEventListener('beforeunload', before); document.removeEventListener('click', navigation, true); window.removeEventListener('popstate', pop); };
  }, []);
  function choose(id: string, scroll = true) {
    if (busyRef.current || draft && !window.confirm('还有未保存的计划，确定退出表单吗？')) return;
    setSelected(id); setDraft(null); setAck(false); setPreview(null); setActionError(''); setMessage('');
    const url = new URL(window.location.href); url.searchParams.set('plan', id); window.history.pushState(window.history.state, '', url);
    if (scroll && window.innerWidth < 1000) detail.current?.scrollIntoView({ behavior: 'auto', block: 'start' });
  }
  function begin() { if (!ready || busyRef.current) return false; busyRef.current = true; setBusy(true); setActionError(''); setMessage(''); return true; }
  function end() { busyRef.current = false; if (mounted.current) setBusy(false); }
  async function act(task: () => Promise<void>) {
    if (!begin()) return;
    try { await task(); }
    catch (e) {
      if (mounted.current) { setActionError(errorMessage(e)); if (e instanceof PlanApiError && e.uncertain) setNeedsRead(true); else await read(); }
    } finally { end(); }
  }
  async function submit(e: FormEvent) {
    e.preventDefault(); if (!data || !draft || !canWrite) return;
    let input; try { input = buildInput(data, draft); } catch (e) { setActionError(errorMessage(e)); return; }
    await act(async () => {
      const saved = await savePlan(input); if (!mounted.current) return;
      setData(old => old ? { ...old, plans: [saved, ...old.plans] } : old); setDraft(null); setSelected(saved.plan_id); setAck(false);
      const url = new URL(window.location.href); url.searchParams.set('plan', saved.plan_id); window.history.pushState(window.history.state, '', url);
      setMessage('计划已保存，所用分镜与素材版本已固定。请先核对，再确认本地输入。');
    });
  }
  function startNew() {
    if (!data || !canWrite || busyRef.current || draft && !window.confirm('确定放弃当前未保存的计划吗？')) return;
    setDraft(newDraft(data)); setAck(false); setPreview(null); setMessage(''); setActionError('');
  }
  function setOption<K extends keyof Options>(key: K, value: Options[K]) { setDraft(d => d ? { ...d, options: { ...d.options, [key]: value }, roles: key === 'mode' ? {} : d.roles } : d); }
  const title = data?.project?.meta?.idea ?? data?.project?.idea ?? '项目生成计划';
  return <><BrandHeader /><main className="plan-workspace">
    <nav className="plan-breadcrumb" aria-label="项目导航"><Link href={`/?session=${encodeURIComponent(projectId)}`}><ArrowLeft size={15} />返回项目</Link><span>/</span><Link href={`/projects/${encodeURIComponent(projectId)}/assets`}>素材与版本</Link><span>/</span><span>生成计划</span></nav>
    <header className="plan-heading"><div><span className="studio-eyebrow">BEFORE THE NEXT FRAME</span><h1>先核对计划，<br /><em>再迈向下一帧。</em></h1><p>{title}</p></div><div className="plan-heading-note"><ShieldCheck size={30} /><strong>输入由你确认</strong><span>固定版本 · 保留历史</span><small>本地规划，真实费用待确认</small></div></header>
    <div className="plan-toolbar"><div><ClipboardList size={18} /><strong>生成计划</strong><span>{data ? `${data.plans.length} 份已保存` : '正在读取'}</span></div><div><button className="plan-button" onClick={() => void read()} disabled={reading || busy}><RefreshCw size={15} />重新读取</button><button className="plan-button primary" onClick={startNew} disabled={!canWrite || !segments.length}><Plus size={15} />新建计划</button></div></div>
    {reading && <p role="status" className="plan-help"><Loader2 size={16} className="animate-spin" />正在读取计划与素材…</p>}
    {readError && <div role="alert" className="plan-feedback error"><strong>计划未能重新读取</strong><p>{readError}。{data ? '保留上次数据供核对，操作暂停。' : '请恢复本地服务后重新读取。'}</p></div>}
    {actionError && <div role="alert" className="plan-feedback error"><p>{actionError}</p>{needsRead && <p>表单已保留；重新读取后，先检查是否已有同内容计划，再决定是否提交。</p>}</div>}
    {message && <p role="status" className="plan-feedback success">{message}</p>}
    {data && !data.project && <p role="status" className="plan-feedback warning">项目已不存在。历史计划仍保留，当前仅供查看。</p>}
    {data?.project && !segments.length && <div className="plan-feedback warning"><strong>先准备分镜片段</strong><p>回到项目整理分镜，再到素材页绑定片段需要的图片与文字设定。</p></div>}
    <div className="plan-columns"><section className="plan-list" aria-label="已保存计划" aria-busy={reading}>
      {data && !data.plans.length ? <div className="plan-empty"><ClipboardList size={32} /><h2>把下一帧的准备，先存下来</h2><p>选择分镜与素材引用，保存一份可核对的计划。后续修改也会留下记录。</p><button className="plan-button" disabled={!canWrite || !segments.length} onClick={startNew}>创建第一份计划</button></div> : data?.plans.map(p => <button className="plan-card" key={p.plan_id} aria-pressed={!draft && p.plan_id === selected} disabled={busy} onClick={() => choose(p.plan_id)}><span className={`plan-status ${!p.current ? 'warning' : p.confirmation ? 'success' : ''}`}>{label(p)}</span><strong>{p.content.targets.length === 1 ? `片段 ${p.content.targets[0].segment_id}` : `${p.content.targets.length} 个片段`}</strong><span>{MODES[p.content.options.mode]} · {p.content.targets.reduce((n, t) => n + t.duration, 0)} 秒</span><div><small>{time(p.created_at)}</small><small>计划 {p.plan_id.slice(0, 8)}</small></div>{p.impacts.some(i => i.newer_version_references.length) && <small className="plan-newer">素材有新版，计划仍保留原版本</small>}</button>)}
    </section><section ref={detail} className="plan-detail" aria-label="计划详情">
      {draft ? <form onSubmit={submit}><div className="plan-section-heading"><div><span className="studio-eyebrow">NEW PLAN</span><h2>准备一份新计划</h2></div><button type="button" className="plan-icon-button" aria-label="退出计划表单" disabled={busy} onClick={() => { if (window.confirm('确定退出未保存的计划吗？')) setDraft(null); }}><X size={20} /></button></div><p className="plan-help">保存后固定当前引用版本，后续修改需另建计划。</p>
        <fieldset disabled={!canWrite}><div className="plan-fields"><label>分镜片段<select aria-label="分镜片段" value={draft.segment} onChange={e => setDraft(d => d ? { ...d, segment: e.target.value, roles: {} } : d)}>{segments.map(s => <option key={s.key} value={s.segment_id ?? ''}>{s.label} · {s.segment_id}</option>)}</select></label><label>计划时长（秒）<input aria-label="计划时长" type="number" min="1" max="60" step="1" required value={draft.duration} onChange={e => setDraft(d => d ? { ...d, duration: e.target.value } : d)} /></label><label>输入方式<select aria-label="输入方式" value={draft.options.mode} onChange={e => setOption('mode', e.target.value as Mode)}>{Object.entries(MODES).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label><label>计划使用的模型<input aria-label="计划使用的模型" required maxLength={100} placeholder="沿用项目设置或填写模型名称" value={draft.options.model} onChange={e => setOption('model', e.target.value)} /></label><label>画面比例<select value={draft.options.video_ratio} onChange={e => setOption('video_ratio', e.target.value)}>{['16:9', '9:16', '1:1', '4:3', '3:4', '21:9'].map(v => <option key={v}>{v}</option>)}</select></label><label>分辨率<select value={draft.options.resolution} onChange={e => setOption('resolution', e.target.value)}>{['480P', '720P', '1080P'].map(v => <option key={v}>{v}</option>)}</select></label><label>声音<select value={draft.options.sound} onChange={e => setOption('sound', e.target.value as Options['sound'])}><option value="off">无声</option><option value="on">开启声音</option></select></label><label>镜头组织<select value={draft.options.shot_type} onChange={e => setOption('shot_type', e.target.value as Options['shot_type'])}><option value="multi">多镜头</option><option value="single">单镜头</option></select></label><label className="plan-field-wide">视觉风格<input required maxLength={100} value={draft.options.style} onChange={e => setOption('style', e.target.value)} /></label></div>
        <h3 className="plan-subtitle"><Layers size={16} />选择引用与用途</h3><p className="plan-help">只列出此片段适用的引用。{draft.options.mode === 'reference' ? '选择 1—4 张参考图。' : draft.options.mode === 'first_frame' ? '指定一张首帧图片。' : '分别指定首帧与尾帧图片。'}文字设定可选。</p>
        {!candidates.length && <div className="plan-feedback warning"><p>此片段还没有可用素材引用。</p><Link href={`/projects/${encodeURIComponent(projectId)}/assets`}>去素材页绑定引用</Link></div>}
        {candidates.map(({ ref, asset, version }) => <label className="plan-choice" key={ref.reference_id}><span><strong>{version.name} · v{version.number}</strong><small>{ref.segment_id ? `片段引用${ref.shot_number ? ` · 镜头 ${ref.shot_number}` : ''}` : '项目引用'}{ref.has_newer_version ? ' · 有新版，仍选当前引用' : ''}</small></span><select aria-label={`${version.name} v${version.number} ${ref.segment_id ? ref.shot_number ? `镜头 ${ref.shot_number}` : "片段" : "项目"}引用用途`} value={draft.roles[ref.reference_id] ?? ''} onChange={e => setDraft(d => d ? { ...d, roles: { ...d.roles, [ref.reference_id]: e.target.value as Role | '' } } : d)}><option value="">不使用</option>{asset.kind !== 'image' ? <option value="context">文字设定</option> : draft.options.mode === 'reference' ? <option value="reference_image">参考图</option> : <><option value="first_frame">首帧</option>{draft.options.mode === 'start_end_frame' && <option value="last_frame">尾帧</option>}</>}</select></label>)}
        <p className="plan-help">模型参数是否可实际使用，将在后续模型验证中核对。保存计划不代表费用已确认。</p><button type="submit" className="plan-button primary plan-save" disabled={!canWrite || !candidates.length}>{busy ? <Loader2 size={16} className="animate-spin" /> : <Check size={16} />}保存计划</button></fieldset>
      </form> : plan ? <>
        <div className="plan-section-heading"><div><span className="studio-eyebrow">PLAN {plan.plan_id.slice(0, 8)}</span><h2>核对这份计划</h2></div><span className={`plan-status ${plan.current ? '' : 'warning'}`}>{label(plan)}</span></div>
        <p className="plan-help">保存于 {time(plan.created_at)}{plan.confirmation ? ` · 本地确认于 ${time(plan.confirmation.confirmed_at)}` : ''}</p>
        <div className="plan-summary"><strong>{MODES[plan.content.options.mode]}</strong><span>{plan.content.options.model}</span><span>{plan.content.options.video_ratio} · {plan.content.options.resolution} · {plan.content.options.sound === 'on' ? '有声' : '无声'} · {plan.content.options.shot_type === 'multi' ? '多镜头' : '单镜头'}</span><span>风格 {plan.content.options.style}</span></div>
        {plan.impacts.map(i => <div key={i.segment_id}>{i.reasons.length > 0 && <div className="plan-feedback warning"><strong>片段 {i.segment_id} 已受修改影响</strong><p>{i.reasons.map(r => IMPACTS[r]).join('；')}。请核对当前内容后创建新计划。</p></div>}{i.newer_version_references.length > 0 && <p className="plan-feedback warning">片段 {i.segment_id} 有 {i.newer_version_references.length} 处素材出现新版，当前计划仍使用保存时的版本。</p>}</div>)}
        {plan.content.targets.map(t => <section className="plan-target" key={t.segment_id}><h3>片段 {t.segment_id}<span>{t.duration} 秒</span></h3><ol className="plan-shots">{t.shots.map(s => <li key={s.shot_number}><span>镜头 {s.shot_number} · {s.duration} 秒</span><p>{s.content}</p></li>)}</ol><div className="plan-bindings">{t.bindings.map(b => <PinnedPreview key={`${b.reference_id}:${b.version.version_id}`} binding={b} />)}</div><details className="plan-prompt"><summary>查看固定提示词</summary><pre>{t.prompt}</pre></details></section>)}
        <div className="plan-execution"><ShieldCheck size={20} /><div><strong>真实费用待确认</strong><p>保存与确认只核对本地输入。实际生成前还需处理以下事项。</p><ul>{plan.execution_blockers.map(b => <li key={b}>{BLOCKERS[b]}</li>)}</ul></div></div>
        {!plan.confirmation ? <div className="plan-confirm"><label><input type="checkbox" checked={ack} disabled={!canWrite || !plan.current} onChange={e => setAck(e.target.checked)} /><span>已核对分镜、用途与固定版本；本次仅确认本地输入。</span></label><button className="plan-button primary" disabled={!canWrite || !plan.current || !ack} onClick={() => void act(async () => { const confirmed = await confirmPlan(plan); if (!mounted.current) return; setData(d => d ? { ...d, plans: d.plans.map(p => p.plan_id === confirmed.plan_id ? confirmed : p) } : d); setAck(false); setMessage('本地输入已确认。真实费用、素材授权和执行仍待处理。'); })}>确认本地输入</button></div> : <div className="plan-confirm"><p><Check size={16} />本地输入确认已保存，刷新后可继续查看。</p><button className="plan-button" disabled={!canWrite || !plan.current} onClick={() => void act(async () => { const result = await checkPlan(plan); if (mounted.current) setPreview({ plan: plan.plan_id, hash: plan.content_hash, ...result }); })}>检查本地输入</button></div>}
        {preview && preview.plan === plan.plan_id && preview.hash === plan.content_hash && <p role="status" className="plan-feedback success">本地输入检查通过：{preview.count} 个片段、{preview.images} 张固定图片。未创建生成任务。</p>}
        {!plan.current && <button className="plan-button" disabled={!canWrite || !segments.length} onClick={startNew}><Plus size={15} />创建新计划</button>}
      </> : <div className="plan-empty"><ClipboardList size={32} /><h2>{selected && data ? '这份计划未找到' : '先选一份计划'}</h2><p>{selected && data ? '记录可能已不存在，请重新读取，或从列表选择其他计划。' : '查看固定素材、修改影响和执行前需要确认的事项。'}</p></div>}
    </section></div>
  </main></>;
}
