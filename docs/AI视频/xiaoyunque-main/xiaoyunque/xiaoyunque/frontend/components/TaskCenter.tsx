'use client';

import Link from 'next/link';
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { Activity, ArrowLeft, ArrowUpRight, CheckCircle2, CircleHelp, Clock3, Download, Film, FolderOpen, Inbox, Layers, ListChecks, Loader2, RefreshCw, Search, ShieldCheck } from 'lucide-react';
import BrandHeader from './BrandHeader';
import { emptySnapshot, filterTasks, nextReadDelay, readTaskCenter, SOURCES, STATUSES, taskRecords, type CenterSnapshot, type TaskRecord, type TaskSource, type TaskStatus } from '@/lib/taskCenter';

const dateLabel = (date: string | null) => date ? new Date(date).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '时间未记录';
const selectionSnapshot = () => new URLSearchParams(window.location.search).get('task') ?? '';
const subscribeSelection = (callback: () => void) => { window.addEventListener('popstate', callback); window.addEventListener('task-selection', callback); return () => { window.removeEventListener('popstate', callback); window.removeEventListener('task-selection', callback); }; };
const serverSelection = () => '';
const statusIcon = (status: TaskStatus) => status === 'completed' ? <CheckCircle2 size={14} /> : status === 'running' ? <Activity size={14} /> : status === 'unknown' ? <CircleHelp size={14} /> : <Clock3 size={14} />;

export function TaskResult({ task }: { task: TaskRecord }) {
  const [failed, setFailed] = useState(false), [attempt, setAttempt] = useState(0);
  if (!task.resultUrl) return null;
  return <section className="task-result" aria-label="归档结果">
    <div className="task-result-title"><Film size={16} /><span>{task.simulation ? '人工演练视频' : '归档视频'}</span></div>
    {failed ? <div className="task-media-error" role="alert"><Film size={28} /><p>视频暂时无法读取，任务记录仍保留。</p><button className="task-button" onClick={() => { setFailed(false); setAttempt(a => a + 1); }}>重新加载视频</button></div>
      : <video key={attempt} src={task.resultUrl + (attempt ? `?retry=${attempt}` : '')} controls preload="metadata" aria-label={task.simulation ? '人工演练视频预览' : '归档视频预览'} onError={() => setFailed(true)} />}
    <a className="task-button" href={task.resultUrl} download={`${task.simulation ? 'simulation' : 'result'}-${task.id.slice(0, 8)}.mp4`}><Download size={15} />下载归档视频</a>
    {task.simulation && <p>本地人工色块样例，仅用于流程验收。</p>}
  </section>;
}

export function TaskDetail({ task, projectExists }: { task: TaskRecord; projectExists: boolean }) {
  const available=task.projectAvailable??projectExists;
  const projectLink = !['generation','first_release'].includes(task.source) || available;
  return <>
    <div className="task-detail-top"><span className="task-eyebrow">TASK DETAILS</span><span className={`task-status ${task.status}`}>{statusIcon(task.status)}{STATUSES[task.status]}</span></div>
    <h2>{task.title}</h2>
    <div className="task-tags"><span>{SOURCES[task.source]}</span>{task.simulation && <span className="simulation">{task.source==='generation'?'人工演练 · SIM':'人工接口样例'}</span>}</div>
    <dl className="task-metadata"><div><dt>当前阶段</dt><dd>{task.phase}</dd></div><div><dt>最近记录</dt><dd>{dateLabel(task.date)}</dd></div><div><dt>任务编号</dt><dd className="task-id">{task.id}</dd></div>{task.projectId && <div><dt>所属项目</dt><dd className="task-id">{task.projectId}</dd></div>}{task.billing && <div><dt>费用记录</dt><dd>{task.billing}</dd></div>}</dl>
    {task.progress !== null && <div className="task-progress"><div><span>工作台上报进度</span><strong>{task.progress}%</strong></div><progress max={100} value={task.progress} /></div>}
    {task.reasons.length > 0 && <div className="task-reasons"><strong>需要留意</strong><ul>{[...new Set(task.reasons)].map(reason => <li key={reason}>{reason}</li>)}</ul></div>}
    <p className="task-note">{task.note}</p>
    {task.status === 'review' && <p className="task-note">先核对原记录，避免重复提交生成。</p>}
    <TaskResult key={task.key + task.resultUrl} task={task} />
    <div className="task-detail-actions">{task.href && projectLink && <Link className="task-button primary" href={task.href}><ArrowUpRight size={16} />{task.projectId ? '打开项目' : '返回原工作台'}</Link>}{task.projectId && available && <Link className="task-button" href={`/projects/${encodeURIComponent(task.projectId)}/assets`}><Layers size={16} />素材与版本</Link>}</div>
    {['generation','first_release'].includes(task.source) && !available && <p className="task-note">所属项目暂未读到，保留任务与归档结果。</p>}
  </>;
}

export default function TaskCenter() {
  const [snapshot, setSnapshot] = useState<CenterSnapshot>(emptySnapshot), [initialized, setInitialized] = useState(false), [reading, setReading] = useState(true), [reload, setReload] = useState(0);
  const [source, setSource] = useState<TaskSource | 'all'>('all'), [status, setStatus] = useState<TaskStatus | 'all'>('all'), [query, setQuery] = useState('');
  const snapshotRef = useRef(snapshot), detailRef = useRef<HTMLElement>(null), listRef = useRef<HTMLDivElement>(null);
  const selectedKey = useSyncExternalStore(subscribeSelection, selectionSnapshot, serverSelection);
  useEffect(() => {
    let active = true, running = false, failures = 0;
    let timer: ReturnType<typeof setTimeout> | undefined, controller: AbortController | undefined;
    const load = async () => {
      if (!active || running || document.hidden) return;
      running = true; controller = new AbortController();
      const request = controller;
      setReading(true);
      const result = await readTaskCenter(snapshotRef.current, request.signal);
      running = false;
      if (!active) return;
      setReading(false);
      if (!request.signal.aborted) {
        snapshotRef.current = result; setSnapshot(result); setInitialized(true);
        failures = Object.values(result).some(s => s.failed) ? failures + 1 : 0;
      }
      if (!document.hidden) timer = setTimeout(load, nextReadDelay(snapshotRef.current, failures));
    };
    const visibility = () => {
      clearTimeout(timer);
      if (document.hidden) controller?.abort(); else void load();
    };
    document.addEventListener('visibilitychange', visibility);
    void load();
    return () => { active = false; clearTimeout(timer); controller?.abort(); document.removeEventListener('visibilitychange', visibility); };
  }, [reload]);
  const records = taskRecords(snapshot), visible = filterTasks(records, source, status, query);
  const selected = records.find(t => t.key === selectedKey);
  const failedSources = (Object.keys(SOURCES) as TaskSource[]).filter(s => snapshot[s].failed);
  const allFailed = failedSources.length === Object.keys(SOURCES).length;
  const unavailable = allFailed || (source !== 'all' && snapshot[source].failed && !snapshot[source].updatedAt);
  const anyFilter = source !== 'all' || status !== 'all' || Boolean(query.trim());
  const activeCount = records.filter(t => ['queued', 'running'].includes(t.status)).length;
  const waitingCount = records.filter(t => ['waiting', 'review'].includes(t.status)).length;
  const completedCount = records.filter(t => t.status === 'completed').length;
  const reset = () => { setSource('all'); setStatus('all'); setQuery(''); };
  const select = (task: TaskRecord) => {
    const url = new URL(window.location.href); url.searchParams.set('task', task.key); window.history.replaceState(null, '', url); window.dispatchEvent(new Event('task-selection'));
    requestAnimationFrame(() => { detailRef.current?.focus({ preventScroll: true }); if (window.matchMedia('(max-width: 1100px)').matches) detailRef.current?.scrollIntoView({ behavior: 'instant', block: 'start' }); });
  };

  return <><BrandHeader /><div className="task-workspace">
    <div className="task-breadcrumb"><Link href="/"><ArrowLeft size={14} />创作首页</Link><span>/</span><span>任务中心</span><button className="task-button" disabled={reading} onClick={() => setReload(n => n + 1)}>{reading ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}刷新记录</button></div>
    <header className="task-heading"><div><span className="task-eyebrow">YOUR CREATIVE PROCESS</span><h1>每一次创作，<br /><span>都有迹可循。</span></h1><p>找到进展、确认点与结果，接着完成你的故事。</p></div><div className="task-heading-mark" aria-hidden="true"><ListChecks size={48} /><span>CREATE. KEEP. CONTINUE.</span></div></header>
    <div className="task-stats" aria-label="任务统计">{[['全部记录', records.length, <ListChecks size={19} key="all" />], ['进行中', activeCount, <Activity size={19} key="active" />], ['待确认 / 核对', waitingCount, <Clock3 size={19} key="waiting" />], ['已完成', completedCount, <CheckCircle2 size={19} key="done" />]].map(([label, count, icon]) => <div key={String(label)}>{icon}<strong>{initialized ? count : '—'}</strong><span>{label}</span></div>)}</div>
    <div className="task-read-status" role="status" aria-live="polite">{reading ? initialized ? '正在更新记录…' : '正在读取任务记录…' : failedSources.length ? '部分记录未更新，请查看来源提示。' : '记录已同步；查看与刷新不会启动生成。'}</div>
    {failedSources.length > 0 && <div className="task-read-error" role="alert"><strong>{allFailed ? '任务记录暂时无法读取' : '部分来源读取失败'}</strong>{failedSources.map(s => <p key={s}>{SOURCES[s]}：{snapshot[s].updatedAt ? `读取失败，保留上次记录（${dateLabel(snapshot[s].updatedAt)}）` : '尚未读到记录，请检查本地服务后刷新'}</p>)}</div>}
    <div className="task-toolbar"><div className="task-source-tabs" aria-label="任务来源"><button aria-pressed={source === 'all'} onClick={() => setSource('all')}>全部来源</button>{(Object.keys(SOURCES) as TaskSource[]).map(s => <button key={s} aria-pressed={source === s} onClick={() => setSource(s)}>{SOURCES[s]}</button>)}</div><div className="task-filter-controls"><label><span className="sr-only">任务状态</span><select value={status} onChange={e => setStatus(e.target.value as TaskStatus | 'all')}><option value="all">全部状态</option>{(Object.keys(STATUSES) as TaskStatus[]).map(s => <option key={s} value={s}>{STATUSES[s]}</option>)}</select></label><label className="task-search"><Search size={16} /><input aria-label="搜索任务" placeholder="搜索名称或编号" value={query} onChange={e => setQuery(e.target.value)} /></label></div></div>
    <div className="task-content"><div ref={listRef} className="task-list" tabIndex={-1}><div className="task-list-caption"><span>{initialized ? `${visible.length} 条${anyFilter ? '匹配' : ''}记录` : '读取中'}</span>{anyFilter && <button onClick={reset}>清除筛选</button>}</div>
      {!initialized ? <div className="task-loading" role="status"><Loader2 size={26} className="animate-spin" /><p>正在读取项目与任务</p><div /><div /><div /></div>
        : visible.length === 0 ? <div className="task-empty"><Inbox size={32} /><h2>{unavailable ? '连接服务后继续查看' : anyFilter ? '没有匹配的任务' : failedSources.length ? '已读取来源暂无记录' : '还没有任务记录'}</h2><p>{unavailable ? '记录读取失败，不能判断任务是否为空。' : anyFilter ? '试试其他名称、来源或状态。' : '你的项目与创作任务会出现在这里。'}</p>{anyFilter ? <button className="task-button" onClick={reset}>清除筛选</button> : <Link className="task-button" href="/"><FolderOpen size={16} />回到创作首页</Link>}</div>
          : <div className="task-rows">{visible.map(task => <button key={task.key} className={`task-row ${selectedKey === task.key ? 'selected' : ''}`} aria-pressed={selectedKey === task.key} aria-label={`查看任务：${task.title}（${task.id.slice(0, 8)}）`} onClick={() => select(task)}><span className={`task-row-icon ${task.status}`}>{task.source === 'workflow' ? <FolderOpen size={21} /> : task.source === 'generation' ? <Film size={21} /> : <Activity size={21} />}</span><span className="task-row-copy"><strong>{task.title}</strong><span>{SOURCES[task.source]}{task.simulation ? ' · 人工演练' : ''} · {task.phase}</span><small>{dateLabel(task.date)}</small></span><span className={`task-status ${task.status}`}>{statusIcon(task.status)}{STATUSES[task.status]}</span><ArrowUpRight className="task-row-arrow" size={16} /></button>)}</div>}
    </div><aside ref={detailRef} tabIndex={-1} className="task-detail" aria-label="任务详情"><button className="task-back-list" onClick={() => { listRef.current?.focus({ preventScroll: true }); listRef.current?.scrollIntoView({ behavior: 'instant', block: 'start' }); }}><ArrowLeft size={15} />回到任务列表</button>{selected ? <TaskDetail key={selected.key} task={selected} projectExists={snapshot.workflow.records.some(t => t.id === selected.projectId)} /> : <div className="task-detail-empty"><ListChecks size={35} /><h2>{selectedKey && initialized ? '暂未找到这条记录' : '看看故事走到了哪一步'}</h2><p>{selectedKey && initialized ? '记录可能尚未同步，或属于临时活动任务。请核对来源后刷新。' : '选择左侧任务，查看进展、等待原因与已保留的结果。'}</p></div>}</aside></div>
    <div className="task-source-note"><ShieldCheck size={17} /><div><p>项目与本地任务读取已保存记录；临时工作台仅显示当前活动任务，重启后不保证保留。</p><p>专项创作显示最近最多 500 条。{snapshot.pipeline.records.length >= 500 ? '已达到读取上限，请在原工作台查看其他历史。' : ''}生成与费用操作继续在原流程中处理。</p></div></div>
    <footer className="task-footer"><span>保留进展，让创作继续</span><Link href="/">回到创作 <ArrowUpRight size={14} /></Link></footer>
  </div></>;
}
