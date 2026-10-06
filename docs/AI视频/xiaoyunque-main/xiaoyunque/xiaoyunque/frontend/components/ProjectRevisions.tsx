'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { ArrowLeft, History, RefreshCw, FilePenLine, ShieldCheck } from 'lucide-react';
import BrandHeader from './BrandHeader';
import RevisionComparison from './RevisionComparison';
import RevisionExport from './RevisionExport';
import RevisionImportPrecheck from './RevisionImportPrecheck';
import { STAGES, RevisionError, editInput, pathKey, previewRevision, readRevisions, restoreInput, saveRevision, type Input, type Preview, type Stage, type Version, type Workspace, type Impact } from '@/lib/contentRevisions';

const sources = { baseline: '首次归档', external: '原入口更新归档', edit: '手动修改', restore: '恢复旧版' };
const date = (s: string) => new Date(s).toLocaleString('zh-CN');
function Impacts({ items }: { items: Impact[] }) {
  return items.length ? <ul className="revision-impacts">{items.map((i,n) => <li key={n}><strong>{i.segment_id}{i.shot_number === null ? '' : ` · 镜头 ${i.shot_number}`}</strong><span>{i.reason}</span></li>)}</ul> : <p className="revision-muted">尚无可关联的分镜。后续制作前请人工核对文字。</p>;
}

export default function ProjectRevisions({ projectId }: { projectId: string }) {
  const [data, setData] = useState<Workspace | null>(null);
  const [reading, setReading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [backupSaving,setBackupSaving]=useState(false);
  const [backupUncertain,setBackupUncertain]=useState(false);
  const lock = useRef(false);
  const [blocked, setBlocked] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState(0);
  const [notice, setNotice] = useState('');
  const [stage, setStage] = useState<Stage>('script_generation');
  const [fieldKey, setFieldKey] = useState('');
  const [value, setValue] = useState('');
  const [selectedId, setSelectedId] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [input, setInput] = useState<Input | null>(null);
  const [ack, setAck] = useState(false);
  const dirtyRef = useRef(false);
  const mounted = useRef(true);
  const selection = data?.versions.find(v => v.version_id === selectedId);
  const field = data?.fields[stage].find(f => pathKey(f.path) === fieldKey);
  const dirty = !!field && value !== field.value;
  useEffect(() => { dirtyRef.current = dirty || !!preview; }, [dirty, preview]);
  const resetPreview = () => { setPreview(null); setInput(null); setAck(false); };
  const selectUrl = (id: string) => {
    setSelectedId(id);
    const url = new URL(location.href);
    if (id) url.searchParams.set('version', id); else url.searchParams.delete('version');
    history.pushState(null, '', url);
  };
  const reload = useCallback(async () => {
    if (lock.current) return;
    if (dirtyRef.current && !window.confirm('重新读取会放弃本页未保存的文字或预览。确认重新读取？')) return;
    lock.current = true; setReading(true); setError(''); setNotice('');
    try {
      const next = await readRevisions(projectId);
      if (!mounted.current) return;
      setData(next); setBlocked(false); setStatus(0);
      setFieldKey(''); setValue('');
      setPreview(null); setInput(null); setAck(false);
      setSelectedId(new URL(location.href).searchParams.get('version') ?? '');
      setNotice('已读取持久化版本。请核对当前文字与历史后继续。');
    } catch (e) {
      if (mounted.current) { setError(e instanceof Error ? e.message : '读取失败，请重试'); setStatus(e instanceof RevisionError ? e.status : 0); setBlocked(true); }
    } finally { lock.current = false; if (mounted.current) setReading(false); }
  }, [projectId]);
  useEffect(() => { mounted.current = true; const initial = window.setTimeout(() => { void reload(); }, 0); const navigate = (e: MouseEvent) => { if (e.target instanceof Element && e.target.closest('a[href]:not([href^="#"]):not([download])') && (lock.current || (dirtyRef.current && !window.confirm('尚有未保存的文字或预览，离开会丢失本页草稿。确认离开？')))) { e.preventDefault(); e.stopImmediatePropagation(); } }; document.addEventListener('click', navigate, true); const pop = () => setSelectedId(new URL(location.href).searchParams.get('version') ?? ''); const leave = (e: BeforeUnloadEvent) => { if (dirtyRef.current || lock.current) e.preventDefault(); }; window.addEventListener('popstate',pop); window.addEventListener('beforeunload',leave); return () => { mounted.current = false; window.clearTimeout(initial); document.removeEventListener('click', navigate, true); window.removeEventListener('popstate',pop); window.removeEventListener('beforeunload',leave); }; }, [reload]);
  const disabled = reading || busy || backupSaving || backupUncertain || blocked || !!data?.running;
  const canLeave = () => !lock.current && (!dirtyRef.current || window.confirm('尚有未保存的文字或预览，离开会丢失本页草稿。确认离开？'));
  const previewInput = async (next: Input) => {
    if (lock.current || backupUncertain || blocked) return;
    lock.current = true; setBusy(true); setError(''); setNotice(''); resetPreview();
    try { const result = await previewRevision(projectId, next); setPreview(result); setInput(next); }
    catch (e) { setError(e instanceof Error ? e.message : '预览未完成'); if (e instanceof RevisionError && (e.status === 409 || e.status === 503)) setBlocked(true); }
    finally { lock.current = false; setBusy(false); }
  };
  const editPreview = () => { if (!data) return; try { void previewInput(editInput(data, stage, fieldKey, value)); } catch (e) { setError(e instanceof Error ? e.message : '请检查输入'); } };
  const restorePreview = (version: Version) => { if (!data || !canLeave()) return; try { void previewInput(restoreInput(data, version)); } catch (e) { setError(e instanceof Error ? e.message : '请检查版本'); } };
  const save = async () => {
    if (lock.current || backupUncertain || blocked || !ack || !preview || !input) return;
    lock.current = true; setBusy(true); setError(''); setNotice('');
    try {
      const result = await saveRevision(projectId, { ...input, preview_token: preview.preview_token, request_id: crypto.randomUUID().replaceAll('-',''), acknowledged: true });
      setData(result.workspace); setBlocked(false); selectUrl(result.version_id); resetPreview();
      const current = result.workspace.fields[stage].find(f => pathKey(f.path) === fieldKey); if (current) setValue(current.value);
      setNotice(`已保存为 v${result.workspace.versions.find(v => v.version_id === result.version_id)?.number}，旧内容与媒体候选已保留。`);
    } catch (e) {
      setError(e instanceof Error ? e.message : '保存结果未确认，请重新读取');
      if (!(e instanceof RevisionError) || e.uncertain || e.status === 409) setBlocked(true);
      else resetPreview();
    } finally { lock.current = false; setBusy(false); }
  };
  return <div><BrandHeader /><div className="revision-shell">
    <nav className="revision-nav"><Link href={`/projects/${encodeURIComponent(projectId)}/assets`}><ArrowLeft size={16} />素材与版本</Link><Link href={`/projects/${encodeURIComponent(projectId)}/reviews`}>修改影响与人工核对</Link><Link href={`/projects/${encodeURIComponent(projectId)}/co-creation`}>对话共创</Link><a href="#revision-comparison">两版对比</a><a href="#revision-export">导出与备份</a><a href="#revision-import-precheck">检查文字备份</a><Link href={`/?session=${encodeURIComponent(projectId)}`}>返回项目</Link><button onClick={reload} disabled={reading || busy || backupSaving}><RefreshCw size={16} />重新读取版本</button></nav>
    <div className="revision-heading"><div><span className="revision-eyebrow">创作工作室 · 文字版本</span><h1>每次修改，都有来处</h1><p>选一处文字，核对影响再保存。需要时，恢复旧版继续创作。</p></div><span className="revision-local"><ShieldCheck size={18} />本地修改 · 不生成、不收费</span></div>
    <p className="revision-boundary">版本从首次接线保存开始记录。原剧本、分镜手动保存、旧补丁与工作流结果保存都会归档；接线前的中间修改无法补回。素材、视频和已有计划保留；修改后需重新核对相关内容。</p>
    {error && <div className="revision-alert" role="alert">{error}{data && <p>已显示的数据保留，暂停保存；重新读取后核对。</p>}</div>}
    {notice && <p className="revision-notice" role="status">{notice}</p>}
    {reading && <p role="status">正在读取文字与版本…</p>}
    {!reading && !data && <section className="revision-card"><h2>{status === 404 ? '找不到这个项目' : '暂未读取到文字'}</h2><p>返回项目核对，或启动本地服务后重新读取。</p></section>}
    {data && <>
      {data.running && <div className="revision-alert">项目正在执行。任务结束后重新读取，再修改或恢复文字。</div>}
      {!!data.untracked_stages.length && <div className="revision-alert">{data.untracked_stages.map(s => STAGES[s]).join('、')}已在其他入口更新。历史不代表当前内容；下一次本地保存会归档这次更新。</div>}
      <div className="revision-grid"><section className="revision-card">
        <h2><FilePenLine size={20} />定向修改</h2><div className="revision-tabs">{(Object.keys(STAGES) as Stage[]).map(s => <button key={s} aria-pressed={stage === s} disabled={reading || busy || backupSaving} onClick={() => { if (stage === s || !canLeave()) return; setStage(s); setFieldKey(''); setValue(''); resetPreview(); }}>{STAGES[s]}</button>)}</div>
        {!data.fields[stage].length ? <div className="revision-empty"><h3>这里还没有可编辑的{STAGES[stage]}文字</h3><p>本轮支持已有文字字段。未生成内容或不兼容的结构，暂时保持只读。</p></div> : <>
          <label className="revision-field">修改对象<select value={fieldKey} disabled={disabled} onChange={e => { if (dirty && !window.confirm('切换对象会丢失未保存的草稿，确认切换？')) return; setFieldKey(e.target.value); setValue(data.fields[stage].find(f => pathKey(f.path) === e.target.value)?.value ?? ''); resetPreview(); }}><option value="">请选择一处文字</option>{data.fields[stage].map(f => <option key={pathKey(f.path)} value={pathKey(f.path)}>{f.label}</option>)}</select></label>
          {field && <><h3>当前文字</h3><pre className="revision-text">{field.value || '（空白）'}</pre><label className="revision-field">新内容<textarea value={value} maxLength={10000} rows={7} disabled={disabled} onChange={e => { setValue(e.target.value); resetPreview(); }} /></label><p className="revision-muted">{value.length} / 10000 字 · 只修改选定字段，其他内容保留</p></>}
          <button className="revision-primary" disabled={disabled || !dirty} onClick={editPreview}>{busy ? '正在处理…' : '预览修改与影响'}</button>
        </>}
        {preview && input && <section className="revision-preview" aria-label="修改预览"><h3>{preview.restore_version_id ? `恢复 ${STAGES[preview.stage]}旧版前，请核对` : '保存前，请核对这次修改'}</h3>{preview.restore_version_id && <p>将 v{data.versions.find(v => v.version_id === preview.restore_version_id)?.number} 恢复为新的当前{STAGES[preview.stage]}版本。恢复覆盖该阶段的全部变化；另一阶段和媒体保留。</p>}{preview.changes.map(c => <div className="revision-change" key={pathKey(c.path)}><p>{data.fields[preview.stage].find(f => pathKey(f.path) === pathKey(c.path))?.label ?? '历史内容变化'}</p><div><section><h4>修改前</h4><pre>{c.before_present===false?'（无此字段）':c.before || '（空白）'}</pre></section><section><h4>修改后</h4><pre>{c.after_present===false?'（已移除字段）':c.after || '（空白）'}</pre></section></div></div>)}<h4>需要重新核对</h4><Impacts items={preview.impacts} /><p className="revision-muted">仅记录影响，不自动重生成，不改变既有确认或媒体选择。</p><label className="revision-check"><input type="checkbox" checked={ack} disabled={disabled} onChange={e => setAck(e.target.checked)} />我已核对修改与影响，确认保存为新的当前文字版本</label><div className="revision-actions"><button disabled={busy || backupSaving || reading} onClick={resetPreview}>取消预览</button><button className="revision-primary" disabled={disabled || !ack} onClick={save}>{busy ? '正在保存…' : '确认保存新版本'}</button></div></section>}
      </section><aside className="revision-card"><h2><History size={20} />版本记录</h2><p className="revision-muted">恢复会新增一个版本，旧版本继续留存。</p>{!data.versions.length && <div className="revision-empty"><h3>还没有版本记录</h3><p>第一次保存时，会先归档修改前的剧本和分镜。</p></div>}<div className="revision-history">{[...data.versions].reverse().map(v => <button key={v.version_id} aria-pressed={selectedId === v.version_id} disabled={busy || backupSaving || reading} onClick={() => selectUrl(v.version_id)}><strong>v{v.number} · {STAGES[v.stage]}<span>{v.current ? '当前文字' : sources[v.source]}</span></strong><small>{date(v.created_at)}</small></button>)}</div>
      {selectedId && !selection && <p className="revision-alert">所选历史版本不存在。<button onClick={() => selectUrl('')}>返回版本列表</button></p>}
      {selection && <section className="revision-version"><h3>v{selection.number} · {STAGES[selection.stage]} · {sources[selection.source]}</h3>{selection.restored_from && <p>恢复自 v{data.versions.find(v => v.version_id === selection.restored_from)?.number}</p>}{selection.fields.map(f => <details key={pathKey(f.path)}><summary>{f.label}</summary><pre className="revision-text">{f.value || '（空白）'}</pre></details>)}<button className="revision-primary" disabled={disabled || selection.current} onClick={() => restorePreview(selection)}>{selection.current ? '已经是当前文字' : '预览恢复这一版'}</button></section>}
      </aside></div>
      <RevisionExport workspace={data} disabled={reading || busy || backupSaving || blocked} />
      <RevisionImportPrecheck workspace={data} disabled={reading || busy || blocked} onSaving={saving=>{lock.current=saving;setBackupSaving(saving);}} onBlocked={()=>{setBlocked(true);setBackupUncertain(true);}} onBeforeRestore={()=>!dirtyRef.current||window.confirm('恢复成功后，本页未保存文字与原预览会失效。已复制需要保留的草稿，确认继续恢复？')} onBeforeRead={()=>!dirtyRef.current||window.confirm('读取恢复记录与版本后，若正文已变化，本页未保存编辑与原预览会失效。确认读取核对？')} onRestored={next=>{setBackupUncertain(false);const changed=next.token!==data.token;setData(next);setBlocked(false);if(changed){setFieldKey('');setValue('');resetPreview();}setNotice('已读取恢复后的持久文字与版本；请核对相关内容。');}} />
      <RevisionComparison workspace={data} disabled={reading || busy || backupSaving || blocked} />
      {!!data.impacts.length && <section className="revision-card revision-review"><h2>修改留下的待核对记录</h2><p className="revision-muted">汇总已归档修改的保守影响，回退后仍保留。此列表不表示后续内容已重做或已审核。</p><Impacts items={data.impacts} /></section>}
    </>}
  </div></div>;
}
