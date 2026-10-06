'use client';

import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import Link from 'next/link';
import { ArrowLeft, ArrowUpRight, Check, Download, Film, FolderOpen, History, ImageIcon, Layers, Link2, Loader2, Plus, RefreshCw, Search, Upload, UserRound, X } from 'lucide-react';
import BrandHeader from './BrandHeader';
import MediaImage from './MediaImage';
import { errorMessage, type ProjectSnapshot } from '@/lib/workflowTypes';
import { ASSET_KINDS, AssetApiError, addTextVersion, bindAsset, createTextAsset, displayDefinition, fetchAssetProject, fetchAssets, fetchReferences, fetchUsage, filterAssets, importAsset, importOptions, mediaUrl, sourceLabel, targetOptions, updateReference, uploadAsset, type Asset, type AssetKind, type AssetReference, type AssetVersion, type TextKind } from '@/lib/assets';

interface LibraryData { project: ProjectSnapshot; assets: Asset[]; refs: AssetReference[] }
type Mode = 'new' | 'edit' | 'upload' | 'add-media' | 'import';
interface Editor { mode: Mode; name: string; definition: string; kind: TextKind; file: File | null; importKey: string; uploadId: string; baseName: string; baseDefinition: string }
const dateLabel = (s: string) => new Date(s).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
const newUploadId = () => crypto.randomUUID().replaceAll('-', '');
const kindIcon = (kind: AssetKind, size = 22) => kind === 'video' ? <Film size={size} /> : kind === 'image' ? <ImageIcon size={size} /> : kind === 'character' ? <UserRound size={size} /> : <Layers size={size} />;

function AssetCover({ asset, version }: { asset: Asset; version: AssetVersion }) {
  const [failed, setFailed] = useState(false);
  return <div className={`asset-card-cover ${asset.kind}`}><span>{kindIcon(asset.kind, 32)}</span>{asset.kind === 'image' && version.media && !failed && <MediaImage src={mediaUrl(asset.asset_id, version.version_id)} alt="" loading="lazy" width={version.media.width} height={version.media.height} onError={() => setFailed(true)} />}<small>{ASSET_KINDS[asset.kind]}</small><span className="asset-version-badge">v{version.number}</span></div>;
}

function AssetPreview({ asset, version }: { asset: Asset; version: AssetVersion }) {
  const [failed, setFailed] = useState(false), [attempt, setAttempt] = useState(0);
  if (!version.media) return <div className="asset-text-preview"><span>{ASSET_KINDS[asset.kind]}定义</span><p>{displayDefinition(version)}</p></div>;
  const url = mediaUrl(asset.asset_id, version.version_id);
  return <>
    <div className="asset-media-preview">
      {failed ? <div className="asset-preview-error" role="alert"><ImageIcon size={28} /><p>媒体暂时无法读取</p><button className="asset-button" onClick={() => { setFailed(false); setAttempt(a => a + 1); }}>重新加载</button></div>
        : asset.kind === 'video' ? <video key={attempt} src={url + (attempt ? `?retry=${attempt}` : '')} controls preload="metadata" onError={() => setFailed(true)} aria-label={`${version.name}，版本 ${version.number}`} />
          : <MediaImage key={attempt} src={url + (attempt ? `?retry=${attempt}` : '')} alt={version.name} width={version.media.width} height={version.media.height} onError={() => setFailed(true)} />}
    </div>
    <div className="asset-media-meta"><span>{version.media.width} × {version.media.height} · {(version.media.byte_size / 1024 / 1024).toFixed(2)} MiB{version.media.duration ? ` · ${version.media.duration.toFixed(1)} 秒` : ''}</span><a href={url} download={`${asset.kind}-v${version.number}${asset.kind === 'video' ? '.mp4' : version.media.mime_type === 'image/jpeg' ? '.jpg' : '.' + version.media.mime_type.split('/')[1]}`}><Download size={14} />下载此版本</a></div>
    <p className="asset-description">{version.definition}</p>
  </>;
}

export default function ProjectAssets({ projectId }: { projectId: string }) {
  const [data, setData] = useState<LibraryData | null>(null), [reading, setReading] = useState(true), [readError, setReadError] = useState('');
  const [scope, setScope] = useState<'project' | 'all'>('project'), [kind, setKind] = useState<AssetKind | 'all'>('all'), [query, setQuery] = useState('');
  const [selection, setSelection] = useState({ asset: '', version: '' }), [editor, setEditor] = useState<Editor | null>(null);
  const [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [actionError, setActionError] = useState(''), [needsRead, setNeedsRead] = useState(false);
  const [targetKey, setTargetKey] = useState('project'), [confirmation, setConfirmation] = useState<{ ref: AssetReference; version: AssetVersion; label: string } | null>(null);
  const [usage, setUsage] = useState<{ asset: string; refs: AssetReference[]; error: string } | null>(null);
  const busyRef = useRef(false), dialogRef = useRef<HTMLDialogElement>(null), triggerRef = useRef<HTMLElement | null>(null), requestOrder = useRef<object>({});
  const nameRef = useRef<HTMLInputElement>(null);
  const detailRef = useRef<HTMLElement>(null);
  const selected = data?.assets.find(a => a.asset_id === selection.asset);
  const version = selected?.versions.find(v => v.version_id === selection.version);
  const targets = data ? targetOptions(data.project) : [], imports = data ? importOptions(data.project) : [];
  const target = targets.find(t => t.key === targetKey) ?? targets[0];
  const ownRefs = data?.refs.filter(r => r.asset_id === selection.asset) ?? [];
  const activeRef = ownRefs.find(r => r.segment_id === target?.segment_id && r.shot_number === target?.shot_number);
  const visible = data ? filterAssets(data.assets, data.refs, projectId, scope, kind, query) : [];
  const projectAssets = data ? filterAssets(data.assets, data.refs, projectId, 'project', 'all', '') : [];
  const dirty = Boolean(editor && (editor.name !== editor.baseName || editor.definition !== editor.baseDefinition || editor.file || editor.importKey));
  const ready = Boolean(data && !reading && !readError && !busy);

  const requestData = useCallback(async (): Promise<LibraryData> => {
    const [project, assets, refs] = await Promise.all([fetchAssetProject(projectId), fetchAssets(), fetchReferences(projectId)]);
    if (project.session_id !== projectId || refs.some(r => r.project_id !== projectId || !assets.some(a => a.asset_id === r.asset_id && a.versions.some(v => v.version_id === r.version_id)))) throw new Error('项目素材数据未能一致读取，请重新读取');
    return { project, assets, refs };
  }, [projectId]);
  useEffect(() => {
    let active = true;
    const order = {}; requestOrder.current = order;
    requestData().then(result => {
      if (!active || order !== requestOrder.current) return;
      setData(result);
      const url = new URL(window.location.href), asset = url.searchParams.get('asset'), requestedVersion = url.searchParams.get('version');
      if (asset) {
        const found = result.assets.find(a => a.asset_id === asset);
        setSelection({ asset, version: requestedVersion ?? found?.versions.at(-1)?.version_id ?? '' });
        if (found && !filterAssets(result.assets, result.refs, projectId, 'project', 'all', '').some(a => a.asset_id === asset)) setScope('all');
      }
    }).catch(e => { if (active && order === requestOrder.current) setReadError(errorMessage(e)); }).finally(() => { if (active && order === requestOrder.current) setReading(false); });
    return () => { active = false; requestOrder.current = {}; };
  }, [projectId, requestData]);
  useEffect(() => {
    if (!selection.asset) return;
    let active = true;
    fetchUsage(selection.asset).then(refs => { if (active) setUsage({ asset: selection.asset, refs, error: '' }); }).catch(e => { if (active) setUsage({ asset: selection.asset, refs: [], error: errorMessage(e) }); });
    return () => { active = false; };
  }, [selection.asset, data]);
  useEffect(() => {
    if (confirmation) dialogRef.current?.showModal();
  }, [confirmation]);
  const editorMode = editor?.mode;
  useEffect(() => {
    if (editorMode) nameRef.current?.focus();
    if ((selection.asset || editorMode) && window.matchMedia('(max-width: 1000px)').matches) detailRef.current?.scrollIntoView({ block: 'start' });
  }, [editorMode, selection.asset]);
  useEffect(() => {
    const beforeUnload = (e: BeforeUnloadEvent) => { if (dirty || busy) { e.preventDefault(); e.returnValue = ''; } };
    const beforeLink = (e: MouseEvent) => {
      const link = e.target instanceof Element ? e.target.closest('a') : null;
      if ((dirty || busy) && link && !link.hasAttribute('download') && !link.href.startsWith(window.location.href + '#') && !window.confirm(busy ? '保存正在进行，离开后请重新读取核对结果。仍要离开吗？' : '有未保存的编辑，离开会放弃这些输入。仍要离开吗？')) { e.preventDefault(); e.stopPropagation(); }
    };
    window.addEventListener('beforeunload', beforeUnload); document.addEventListener('click', beforeLink, true);
    return () => { window.removeEventListener('beforeunload', beforeUnload); document.removeEventListener('click', beforeLink, true); };
  }, [dirty, busy]);

  async function reload() {
    const order = {}; requestOrder.current = order;
    setReading(true); setReadError('');
    try { const result = await requestData(); if (order === requestOrder.current) { setData(result); setNeedsRead(false); } }
    catch (e) { if (order === requestOrder.current) setReadError(errorMessage(e)); }
    finally { if (order === requestOrder.current) setReading(false); }
  }
  function canDiscard() { return !busyRef.current && (!dirty || window.confirm('放弃未保存的编辑吗？已保存的素材与历史版本会保留。')); }
  function choose(asset: string, ver: string) {
    if (!canDiscard()) return;
    setEditor(null); setSelection({ asset, version: ver }); setActionError(''); setMessage(''); setNeedsRead(false);
    const url = new URL(window.location.href); url.searchParams.set('asset', asset); url.searchParams.set('version', ver);
    window.history.replaceState(window.history.state, '', url);
  }
  function openEditor(mode: Mode) {
    if (!ready || !canDiscard()) return;
    const editing = mode === 'edit' || mode === 'add-media';
    const name = editing ? version?.name ?? '' : '', definition = editing && version ? displayDefinition(version) : mode === 'upload' ? '本地上传素材' : '';
    setEditor({ mode, name, definition, baseName: name, baseDefinition: definition, file: null, kind: 'character', importKey: '', uploadId: newUploadId() });
    setActionError(''); setMessage(''); setNeedsRead(false);
  }
  function changeEditor(values: Partial<Editor>) { setEditor(e => e ? { ...e, ...values, uploadId: newUploadId() } : e); setActionError(''); }
  function begin() { if (busyRef.current) return false; busyRef.current = true; setBusy(true); setActionError(''); setMessage(''); return true; }
  function end() { busyRef.current = false; setBusy(false); }
  async function joinProject(asset: Asset, ver: AssetVersion) {
    try { await bindAsset(projectId, asset.asset_id, ver.version_id, { segment_id: null, shot_number: null }); return ''; }
    catch (e) { return e instanceof AssetApiError && e.code === 'REFERENCE_EXISTS' ? '素材已保存，项目已有引用；新版本请在详情中明确切换。' : '素材已存入素材库，加入项目的结果未确认。请重新读取后查看引用，或从全部素材中选择加入。'; }
  }
  async function submit(e: FormEvent) {
    e.preventDefault(); if (!editor || !ready || needsRead) return;
    const mode = editor.mode, name = editor.name.trim(), definition = editor.definition.trim();
    if (mode !== 'import' && (!name || !definition || [...name].length > 100 || [...definition].length > 10000)) { setActionError('请填写名称（1–100 字）与定义／说明（1–10000 字）'); return; }
    if ((mode === 'upload' || mode === 'add-media') && !editor.file) { setActionError('请先选择图片或 MP4 视频'); return; }
    if (!begin()) return;
    try {
      let saved: Asset;
      if (mode === 'new') saved = await createTextAsset(editor.kind, name, definition);
      else if (mode === 'edit' && selected) saved = await addTextVersion(selected.asset_id, name, definition);
      else if ((mode === 'upload' || mode === 'add-media') && editor.file) saved = await uploadAsset(projectId, editor.uploadId, editor.file, name, definition, mode === 'add-media' ? selected?.asset_id : undefined);
      else if (mode === 'import') {
        const option = imports.find(i => i.key === editor.importKey); if (!option) throw new Error('请选择项目已有的文字或已选媒体');
        saved = await importAsset(projectId, option);
      } else throw new Error('当前素材不可编辑，请重新读取');
      const savedVersion = mode === 'upload' || mode === 'add-media' ? saved.versions.find(v => v.source.upload_id === editor.uploadId)! : saved.versions.at(-1)!;
      const partial = ['new', 'upload', 'import'].includes(mode) ? await joinProject(saved, savedVersion) : '';
      setEditor(null); setSelection({ asset: saved.asset_id, version: savedVersion.version_id });
      const url = new URL(window.location.href); url.searchParams.set('asset', saved.asset_id); url.searchParams.set('version', savedVersion.version_id); window.history.replaceState(window.history.state, '', url);
      setMessage(`已保存「${savedVersion.name}」v${savedVersion.number}。${mode === 'edit' || mode === 'add-media' ? '现有引用仍使用原版本。' : '可在详情中查看项目引用。'}`);
      if (partial) setActionError(partial);
      await reload();
    } catch (e) {
      setActionError(errorMessage(e));
      if (e instanceof AssetApiError && e.uncertain && ['new', 'edit'].includes(mode)) setNeedsRead(true);
    } finally { end(); }
  }
  async function applyBinding() {
    if (!selected || !version || !target || !ready || !begin()) return;
    try { await bindAsset(projectId, selected.asset_id, version.version_id, target); setMessage(`已将 v${version.number} 引用到${target.label}。`); await reload(); }
    catch (e) { setActionError(errorMessage(e)); if (e instanceof AssetApiError && (e.status === 409 || e.uncertain)) await reload(); }
    finally { end(); }
  }
  async function confirmSwitch() {
    if (!confirmation || !begin()) return;
    try { await updateReference(confirmation.ref, confirmation.version.version_id); setMessage(`已切换到 v${confirmation.version.number}，历史版本与其他位置的引用已保留。`); await reload(); }
    catch (e) { setActionError(errorMessage(e)); await reload(); }
    finally { end(); dialogRef.current?.close(); }
  }
  function closeDialog() { if (busyRef.current) return; dialogRef.current?.close(); }
  const editorTitle = editor ? { new: '新建文字素材', edit: '保存新的文字版本', upload: '上传媒体素材', 'add-media': '上传新的媒体版本', import: '导入项目已有素材' }[editor.mode] : '';
  const title = data?.project.meta?.idea ?? data?.project.idea ?? '当前创作项目';
  return <>
    <BrandHeader />
    <div className="asset-workspace">
      <div className="asset-breadcrumb"><Link href={`/?session=${encodeURIComponent(projectId)}`}><ArrowLeft size={15} />返回项目</Link><span>/</span><span>素材与版本</span><Link href={`/projects/${encodeURIComponent(projectId)}/goal`} className="asset-button">项目名称与目标<ArrowUpRight size={15} /></Link><Link href={`/projects/${encodeURIComponent(projectId)}/compose`} className="asset-button">合成与导出<ArrowUpRight size={15} /></Link><Link href={`/projects/${encodeURIComponent(projectId)}/plans`} className="asset-button">生成计划<ArrowUpRight size={15} /></Link><Link href={`/projects/${encodeURIComponent(projectId)}/revisions`} className="asset-button">文字修改与版本<ArrowUpRight size={15} /></Link><Link href={`/projects/${encodeURIComponent(projectId)}/co-creation`} className="asset-button">对话共创<ArrowUpRight size={15} /></Link><Link href={`/projects/${encodeURIComponent(projectId)}/media`} className="asset-button">媒体修改与候选<ArrowUpRight size={15} /></Link><button className="asset-button asset-refresh" onClick={reload} disabled={reading || busy} aria-label="重新读取素材"><RefreshCw size={15} />重新读取</button></div>
      <header className="asset-heading"><div><span className="studio-eyebrow">YOUR STORY, YOUR ASSETS</span><h1>让每个角色与画面，<br /><span>都有来处。</span></h1><p title={title}>{title}</p></div><div className="asset-heading-stat"><Layers size={28} /><strong>{data ? projectAssets.length : '—'}</strong><span>本项目素材</span><small>版本独立保存 · 引用由你决定</small></div></header>
      <div className="asset-toolbar"><div className="asset-tabs" aria-label="素材范围"><button aria-pressed={scope === 'project'} onClick={() => setScope('project')}>项目素材</button><button aria-pressed={scope === 'all'} onClick={() => setScope('all')}>全部素材</button></div><div className="asset-toolbar-actions"><button className="asset-button" disabled={!ready} onClick={() => openEditor('import')}><FolderOpen size={15} />从项目导入</button><button className="asset-button" disabled={!ready} onClick={() => openEditor('new')}><Plus size={15} />新建文字</button><button className="asset-button primary" disabled={!ready} onClick={() => openEditor('upload')}><Upload size={15} />上传媒体</button></div></div>
      {message && <p className="studio-feedback success" role="status">{message}</p>}
      {actionError && <div className="studio-feedback error" role="alert">{actionError}{needsRead && <button className="asset-button" onClick={reload} disabled={busy || reading}>重新读取并核对</button>}</div>}
      {reading && data && <p className="asset-help" role="status">正在重新读取素材…</p>}
      {readError ? <div className="studio-empty" role="alert"><FolderOpen size={32} /><strong>素材未能读取</strong><p>{readError}</p><button onClick={reload}>重新读取</button></div> : reading && !data ? <div className="asset-loading" role="status"><Loader2 size={24} className="animate-spin" />正在读取项目与素材…</div> : data && <>
        <div className="asset-filter-row"><div className="studio-filters" aria-label="素材分类">{(['all', ...Object.keys(ASSET_KINDS)] as (AssetKind | 'all')[]).map(k => <button key={k} aria-pressed={kind === k} onClick={() => setKind(k)}>{k === 'all' ? '全部' : ASSET_KINDS[k]}</button>)}</div><label className="studio-search"><Search size={15} /><input aria-label="搜索素材" placeholder="搜索名称或历史名称" value={query} onChange={e => setQuery(e.target.value)} />{query && <button aria-label="清除素材搜索" onClick={() => setQuery('')}><X size={14} /></button>}</label></div>
        <div className="asset-content">
          <section className="asset-list" aria-label="素材列表" aria-busy={reading}>
            <div className="asset-list-caption"><span>{scope === 'project' ? '本项目的创作素材' : '可复用的全部素材'}</span><span>{visible.length} 项</span></div>
            {visible.length ? <div className="asset-grid">{visible.map(asset => {
              const latest = asset.versions.at(-1)!, refs = data.refs.filter(r => r.asset_id === asset.asset_id);
              return <button className="asset-card" key={asset.asset_id} aria-pressed={selection.asset === asset.asset_id} aria-label={`查看素材：${latest.name}`} onClick={() => choose(asset.asset_id, latest.version_id)} disabled={busy}>
                <AssetCover key={latest.version_id} asset={asset} version={latest} /><div className="asset-card-copy"><strong>{latest.name}</strong><p>{displayDefinition(latest)}</p><div><span>{sourceLabel(latest.source)}</span><span>{refs.length ? `${refs.length} 处引用` : '未引用'}</span></div>{refs.some(r => r.has_newer_version) && <span className="asset-update-note">有新版本，原引用保持</span>}</div>
              </button>;
            })}</div> : <div className="studio-empty asset-empty"><Layers size={36} /><strong>{query || kind !== 'all' ? '没有匹配的素材' : scope === 'project' ? '为故事准备第一份素材' : '素材库还没有内容'}</strong><p>{query || kind !== 'all' ? '试试其他分类或名称。历史名称也可以检索。' : '从角色设定开始，或上传一张图片、一段视频。每次修改都会留下版本。'}</p>{!query && kind === 'all' && <button onClick={() => openEditor('new')} disabled={!ready}><Plus size={15} />新建文字素材</button>}</div>}
          </section>
          <aside ref={detailRef} className="asset-detail" aria-label="素材详情">
            {editor ? <form onSubmit={submit} className="asset-editor"><div className="asset-detail-heading"><div><span className="studio-eyebrow">MAKE IT YOURS</span><h2>{editorTitle}</h2></div><button type="button" aria-label="关闭素材编辑" className="asset-icon-button" disabled={busy} onClick={() => { if (canDiscard()) setEditor(null); }}><X size={18} /></button></div>
              <p className="asset-help">{editor.mode === 'edit' || editor.mode === 'add-media' ? '保存为新版本，现有引用与旧版本继续保留。' : '保存到本地素材库并加入当前项目。'}</p>
              {editor.mode === 'import' ? <><label htmlFor="asset-import">已有文字或选用媒体</label><select id="asset-import" value={editor.importKey} onChange={e => changeEditor({ importKey: e.target.value })} disabled={busy}><option value="">{imports.length ? '选择项目已有素材' : '项目还没有可导入的产物'}</option>{imports.map(i => <option key={i.key} value={i.key}>{i.label}</option>)}</select><p className="asset-help">导入独立副本，不改变原项目产物。相同内容重复导入不会追加版本。</p></> : <>
                {editor.mode === 'new' && <><label htmlFor="asset-kind">素材类型</label><select id="asset-kind" value={editor.kind} disabled={busy} onChange={e => changeEditor({ kind: e.target.value as TextKind })}>{(['character', 'setting', 'prop'] as TextKind[]).map(k => <option key={k} value={k}>{ASSET_KINDS[k]}</option>)}</select></>}
                <label htmlFor="asset-name">素材名称 <small>最多 100 字</small></label><input ref={nameRef} id="asset-name" value={editor.name} required disabled={busy} onChange={e => changeEditor({ name: e.target.value })} placeholder="例如：林舟 · 雨夜外套" />
                <label htmlFor="asset-definition">{editor.mode === 'new' || editor.mode === 'edit' ? '文字定义' : '素材说明'} <small>最多 10000 字</small></label><textarea id="asset-definition" value={editor.definition} required disabled={busy} onChange={e => changeEditor({ definition: e.target.value })} placeholder="记录外观、性格、场景或这份素材的用途…" rows={6} />
                {(editor.mode === 'upload' || editor.mode === 'add-media') && <div className="asset-upload-field"><Upload size={24} /><label htmlFor="asset-file">选择本地文件</label><input id="asset-file" type="file" disabled={busy} accept={editor.mode === 'add-media' && selected?.kind === 'video' ? '.mp4' : editor.mode === 'add-media' ? '.jpg,.jpeg,.png,.webp,.bmp' : '.jpg,.jpeg,.png,.webp,.bmp,.mp4'} onChange={e => {
                  const file = e.target.files?.[0] ?? null;
                  if (file && (!/\.(jpe?g|png|webp|bmp|mp4)$/i.test(file.name) || file.size > (/\.mp4$/i.test(file.name) ? 100 : 10) * 1024 * 1024)) { changeEditor({ file: null }); setActionError('图片支持 JPG、PNG、WebP、BMP，最大 10 MiB；MP4 最大 100 MiB'); e.target.value = ''; return; }
                  changeEditor({ file, name: editor.name || [...(file?.name.replace(/\.[^.]+$/, '') ?? '')].slice(0, 100).join('') });
                }} />{editor.file && <span className="asset-file-name">{editor.file.name}</span>}<p>图片最大 10 MiB · MP4 最大 100 MiB<br />后端会核对真实格式与内容。</p></div>}
              </>}
              <div className="asset-editor-actions"><button type="button" className="asset-button" disabled={busy} onClick={() => { if (canDiscard()) setEditor(null); }}>取消编辑</button><button className="asset-button primary" disabled={!ready || needsRead || editor.mode === 'import' && !editor.importKey}>{busy ? <Loader2 size={15} className="animate-spin" /> : <Check size={15} />}{busy ? '正在保存…' : editor.mode === 'edit' || editor.mode === 'add-media' ? '保存新版本' : '保存并加入项目'}</button></div><p className="asset-help">未提交内容不会自动保存。这里的操作不会调用模型。</p>
            </form> : selected && version ? <>
              <div className="asset-detail-heading"><div><span className="studio-eyebrow">{ASSET_KINDS[selected.kind]} / VERSION {version.number}</span><h2>{version.name}</h2></div>{kindIcon(selected.kind)}</div>
              <div className="asset-version-select"><History size={15} /><label htmlFor="asset-version">查看版本</label><select id="asset-version" value={version.version_id} disabled={busy} onChange={e => choose(selected.asset_id, e.target.value)}>{[...selected.versions].reverse().map(v => <option key={v.version_id} value={v.version_id}>v{v.number}{v.number === selected.versions.length ? ' · 最新' : ''} · {dateLabel(v.created_at)}</option>)}</select></div>
              <div className="asset-version-source"><span>{sourceLabel(version.source)}</span><span>{dateLabel(version.created_at)}</span></div>
              <AssetPreview key={version.version_id} asset={selected} version={version} />
              <button className="asset-button asset-edit-button" disabled={!ready} onClick={() => openEditor(selected.kind === 'image' || selected.kind === 'video' ? 'add-media' : 'edit')}><Plus size={15} />{version.media ? '上传新版本' : '编辑为新版本'}</button>
              <section className="asset-reference-panel"><div className="asset-panel-title"><Link2 size={16} /><h3>选用到项目</h3></div><p>当前查看 v{version.number}。选择位置后再引用，不会自动重新生成。</p><label htmlFor="asset-target">使用位置</label><select id="asset-target" value={target?.key ?? 'project'} onChange={e => setTargetKey(e.target.value)} disabled={busy}>{targets.map(t => <option key={t.key} value={t.key}>{t.label}</option>)}</select>
                {activeRef && <div className="asset-current-reference"><span>此处实际使用 <strong>v{selected.versions.find(v => v.version_id === activeRef.version_id)?.number}</strong></span>{activeRef.has_newer_version && <small>有更新可选择</small>}{!activeRef.target_exists && <small>原位置已不存在</small>}</div>}
                <button className="asset-button primary" disabled={!ready || Boolean(activeRef && (!activeRef.target_exists || activeRef.version_id === version.version_id))} onClick={e => {
                  if (activeRef) { triggerRef.current = e.currentTarget; setConfirmation({ ref: activeRef, version, label: target?.label ?? '选用位置' }); }
                  else void applyBinding();
                }}>{activeRef?.version_id === version.version_id ? <Check size={15} /> : <Link2 size={15} />}{activeRef?.version_id === version.version_id ? '已使用此版本' : activeRef ? `切换到 v${version.number}` : `引用 v${version.number}`}</button>
                {activeRef && <details className="asset-reference-history"><summary>查看此处引用历史 · {activeRef.history.length} 次选择</summary><ol>{activeRef.history.map((h, i) => <li key={i}><span>v{selected.versions.find(v => v.version_id === h.version_id)?.number}</span><time>{dateLabel(h.selected_at)}</time></li>)}</ol></details>}
              </section>
              <section className="asset-usage"><div className="asset-panel-title"><Layers size={16} /><h3>使用位置与影响</h3></div>{usage?.asset !== selected.asset_id ? <p role="status">正在读取使用位置…</p> : usage.error ? <p role="alert">使用位置未能读取。请点击页面上方重新读取。</p> : usage.refs.length ? <ul>{usage.refs.map(ref => <li key={ref.reference_id}><div><strong>{ref.project_id === projectId ? targets.find(t => t.segment_id === ref.segment_id && t.shot_number === ref.shot_number)?.label ?? '原分镜位置' : '其他项目'}</strong><span>使用 v{selected.versions.find(v => v.version_id === ref.version_id)?.number}{!ref.target_exists ? ' · 原位置已不存在' : ref.has_newer_version ? ' · 有新版本' : ''}</span></div><Link2 size={14} /></li>)}</ul> : <p>还没有项目或镜头引用此素材。</p>}<p className="asset-help">创建新版本后，所有旧引用继续使用原版本。</p></section>
            </> : <div className="asset-detail-placeholder"><FolderOpen size={38} /><h2>{selection.asset ? '素材或版本不存在' : '选择一份素材'}</h2><p>{selection.asset ? '请从左侧列表重新选择，查看已保存版本。' : '预览内容、查看版本，决定你的故事使用哪一版。'}</p></div>}
          </aside>
        </div>
      </>}
      <footer className="studio-footer"><span>留下每次创作的选择</span><Link href={`/?session=${encodeURIComponent(projectId)}`}>回到创作 <ArrowUpRight size={13} /></Link></footer>
    </div>
    {confirmation && <dialog ref={dialogRef} className="asset-confirm-dialog" aria-labelledby="asset-confirm-title" onCancel={e => { if (busyRef.current) e.preventDefault(); }} onClose={() => { setConfirmation(null); triggerRef.current?.focus(); }}><div className="asset-dialog-content"><Link2 size={26} /><h2 id="asset-confirm-title">切换选用版本</h2><p>将「{confirmation.label}」从 v{selected?.versions.find(v => v.version_id === confirmation.ref.version_id)?.number} 切换为 <strong>v{confirmation.version.number}</strong>。</p><p>只更新这个位置的引用，旧版本与其他位置继续保留。已有生成计划可能需要重新确认，不会自动重新生成。</p><div><button className="asset-button" onClick={closeDialog} disabled={busy}>保留原版本</button><button className="asset-button primary" onClick={confirmSwitch} disabled={busy}>{busy ? '正在切换…' : '确认切换'}</button></div></div></dialog>}
  </>;
}
