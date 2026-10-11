'use client';

import { errorMessage } from '@/lib/workflowTypes';

import React, { useState, useRef, useEffect } from 'react';
import { Settings2, Clock, CheckCircle, Trash2, Globe, ListOrdered, Loader2, Sparkles, Plus, Search, FolderOpen, Clapperboard, Repeat2, UserRound, Layers, Bookmark, ArrowUpRight, X } from 'lucide-react';
import clsx from 'clsx';
import Link from 'next/link';
import MediaImage from '@/components/MediaImage';
import { INSPIRATION_CATEGORIES, filterPresets, matchesProject, projectSummary, type InspirationCategory } from '@/lib/studio';
import {
  STYLES,
  VIDEO_RATIOS,
  VIDEO_RESOLUTIONS,
  VIDEO_GENERATION_MODES,
  type ProviderGroup,
  type VideoGenerationMode,
} from '@/config/models';
import { STAGES } from './TopBar';
import { fetchModelGroupsByType, fetchVideoModelGroupsByAbility } from '@/lib/modelRegistry';

export interface ProjectParams {
  idea: string;
  project_name?: string;
  target_duration?: number;
  file_path?: string; // 上传的文件路径 (由后端返回的文件名)
  style: string;
  video_ratio: string;
  video_resolution: string;
  llm_model: string;
  vlm_model: string;
  image_t2i_model: string;
  image_it2i_model: string;
  video_model: string;
  video_first_frame_model: string;
  video_start_end_model: string;
  video_reference_model: string;
  video_generation_mode: VideoGenerationMode;
  expand_idea?: boolean;
  enable_concurrency?: boolean;
  web_search?: boolean;
  episodes?: number;
}

interface HistoryItem {
  id: string;
  idea: string;
  style?: string;
  date: string;
  status: string;
  stages?: Record<string, string>;
}

interface HomePageProps {
  onStartProject: (params: ProjectParams, autoMode?: boolean) => void;
  onSaveDraft: (params: ProjectParams) => Promise<void>;
  historyLoading: boolean;
  historyError: string;
  onRefreshHistory: () => void;
  onResumeProject: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => Promise<void>;
  history: HistoryItem[];
}

export default function HomePage({ onStartProject, onResumeProject, onDeleteSession, history, onSaveDraft, historyLoading, historyError, onRefreshHistory }: HomePageProps) {
  const [idea, setIdea] = useState('');
  const [projectName,setProjectName]=useState('');
  const [targetDuration,setTargetDuration]=useState('60');
  const [category, setCategory] = useState<InspirationCategory>('全部');
  const [query, setQuery] = useState('');
  const [savingDraft, setSavingDraft] = useState(false);
  const [draftMessage, setDraftMessage] = useState('');
  const [draftError, setDraftError] = useState('');
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const draftBusyRef = useRef(false);
  const deleteDialogRef = useRef<HTMLDivElement>(null);
  const deleteTriggerRef = useRef<HTMLElement | null>(null);
  const deletingRef = useRef(false);
  const visibleProjects = history.filter(item => matchesProject(item.idea, query));
  const [showSettings, setShowSettings] = useState(false);
  const [selectedStyle, setSelectedStyle] = useState('realistic');
  const [selectedLLM, setSelectedLLM] = useState('');
  const [selectedVLM, setSelectedVLM] = useState('');
  const [selectedT2I, setSelectedT2I] = useState('');
  const [selectedI2I, setSelectedI2I] = useState('');
  const [selectedFirstFrameVideo, setSelectedFirstFrameVideo] = useState('');
  const [selectedStartEndVideo, setSelectedStartEndVideo] = useState('');
  const [selectedReferenceVideo, setSelectedReferenceVideo] = useState('');
  const [selectedVideoMode, setSelectedVideoMode] = useState<VideoGenerationMode>('first_frame');
  const [selectedRatio, setSelectedRatio] = useState('');
  const [selectedResolution, setSelectedResolution] = useState('720P');
  const [configLoading, setConfigLoading] = useState(true);
  const [configError, setConfigError] = useState('');
  const [enableConcurrency, setEnableConcurrency] = useState(true);
  const [webSearch, setWebSearch] = useState(false);
  const [episodes, setEpisodes] = useState(4);
  const [showEpisodesPanel, setShowEpisodesPanel] = useState(false);

  // 上传相关状态
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadedFile, setUploadedFile] = useState<{name: string, path: string} | null>(null);

  // 管理模式状态
  const [manageMode, setManageMode] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState('');
  const [deleting, setDeleting] = useState(false);
  const [llmProviders, setLlmProviders] = useState<ProviderGroup[]>([]);
  const [vlmProviders, setVlmProviders] = useState<ProviderGroup[]>([]);
  const [t2iProviders, setT2iProviders] = useState<ProviderGroup[]>([]);
  const [i2iProviders, setI2iProviders] = useState<ProviderGroup[]>([]);
  const [firstFrameVideoProviders, setFirstFrameVideoProviders] = useState<ProviderGroup[]>([]);
  const [startEndVideoProviders, setStartEndVideoProviders] = useState<ProviderGroup[]>([]);
  const [referenceVideoProviders, setReferenceVideoProviders] = useState<ProviderGroup[]>([]);
  const activeVideoModel =
    selectedVideoMode === 'start_end_frame'
      ? selectedStartEndVideo
      : selectedVideoMode === 'reference'
        ? selectedReferenceVideo
        : selectedFirstFrameVideo;
  const modelConfigReady = Boolean(selectedLLM && selectedVLM && selectedT2I && selectedI2I && activeVideoModel && selectedRatio && selectedResolution);
  const canStart = Boolean((idea.trim() || uploadedFile) && modelConfigReady && !configLoading && !savingDraft && !uploading && /^\d+$/.test(targetDuration) && Number(targetDuration)>=1 && Number(targetDuration)<=600);

  useEffect(() => {
    let cancelled = false;
    fetchModelGroupsByType('llm')
      .then(groups => { if (!cancelled) setLlmProviders(groups); })
      .catch(() => {});
    fetchModelGroupsByType('vlm')
      .then(groups => { if (!cancelled) setVlmProviders(groups); })
      .catch(() => {});
    fetchModelGroupsByType('t2i')
      .then(groups => { if (!cancelled) setT2iProviders(groups); })
      .catch(() => {});
    fetchModelGroupsByType('i2i')
      .then(groups => { if (!cancelled) setI2iProviders(groups); })
      .catch(() => {});
    fetchVideoModelGroupsByAbility('first_frame_i2v')
      .then(groups => { if (!cancelled) setFirstFrameVideoProviders(groups); })
      .catch(() => {});
    fetchVideoModelGroupsByAbility('start_end_frame_i2v')
      .then(groups => { if (!cancelled) setStartEndVideoProviders(groups); })
      .catch(() => {});
    fetchVideoModelGroupsByAbility('reference_to_video')
      .then(groups => { if (!cancelled) setReferenceVideoProviders(groups); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadDefaultConfig = async () => {
      setConfigLoading(true);
      setConfigError('');
      try {
        const resp = await fetch('/api/config');
        if (!resp.ok) throw new Error('读取默认模型配置失败');
        const data = await resp.json();
        const models = data.config?.models || {};
        const generation = data.config?.generation || {};
        // Legacy config compatibility: older config.yaml only has models.video, so treat it as first-frame video.
        const firstFrameModel = models.video_first_frame || models.video;
        const startEndModel = models.video_start_end || 'wan2.7-i2v';
        const referenceModel = models.video_reference || 'wan2.7-r2v';
        const videoMode = (generation.video_generation_mode || 'first_frame') as VideoGenerationMode;
        const selectedModel = videoMode === 'start_end_frame' ? startEndModel : videoMode === 'reference' ? referenceModel : firstFrameModel;
        if (!models.llm || !models.vlm || !models.image_t2i || !models.image_it2i || !selectedModel) {
          throw new Error('backend/config.yaml 缺少主流程默认模型');
        }
        if (cancelled) return;
        setSelectedStyle(generation.style || 'realistic');
        setSelectedLLM(models.llm);
        setSelectedVLM(models.vlm);
        setSelectedT2I(models.image_t2i);
        setSelectedI2I(models.image_it2i);
        setSelectedVideoMode(videoMode);
        setSelectedFirstFrameVideo(firstFrameModel);
        setSelectedStartEndVideo(startEndModel);
        setSelectedReferenceVideo(referenceModel);
        setSelectedRatio(generation.video_ratio || '16:9');
        setSelectedResolution(generation.video_resolution || '720P');
      } catch (e: unknown) {
        if (!cancelled) setConfigError(errorMessage(e) || '读取默认模型配置失败');
      } finally {
        if (!cancelled) setConfigLoading(false);
      }
    };
    loadDefaultConfig();
    return () => { cancelled = true; };
  }, []);

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setDeleteError('');
    try {
      await onDeleteSession(deleteTarget);
      setDeleteTarget(null);
    } catch (e: unknown) {
      setDeleteError(errorMessage(e) || '删除失败');
    } finally {
      setDeleting(false);
    }
  };

  const activeVideoProviders =
    selectedVideoMode === 'start_end_frame'
      ? startEndVideoProviders
      : selectedVideoMode === 'reference'
        ? referenceVideoProviders
        : firstFrameVideoProviders;

  const setActiveVideoModel = (value: string) => {
    if (selectedVideoMode === 'start_end_frame') {
      setSelectedStartEndVideo(value);
    } else if (selectedVideoMode === 'reference') {
      setSelectedReferenceVideo(value);
    } else {
      setSelectedFirstFrameVideo(value);
    }
  };

  const selectedVideoModeLabel = VIDEO_GENERATION_MODES.find(item => item.id === selectedVideoMode)?.label || '首帧生视频';

  const projectPayload = (): ProjectParams => ({
      idea,
      project_name: projectName.trim() || undefined,
      target_duration: Number(targetDuration),
      file_path: uploadedFile?.path, // 如果上传了文件，传给后端
      style: selectedStyle,
      video_ratio: selectedRatio,
      video_resolution: selectedResolution,
      llm_model: selectedLLM,
      vlm_model: selectedVLM,
      image_t2i_model: selectedT2I,
      image_it2i_model: selectedI2I,
      video_generation_mode: selectedVideoMode,
      video_first_frame_model: selectedFirstFrameVideo,
      video_start_end_model: selectedStartEndVideo,
      video_reference_model: selectedReferenceVideo,
      video_model: activeVideoModel,
      enable_concurrency: enableConcurrency,
      web_search: webSearch,
      episodes,
    });

  const handleStart = (auto?: boolean) => {
    if (!canStart || draftBusyRef.current) return;
    onStartProject(projectPayload(), auto);
  };

  const handleSaveDraft = async () => {
    if (!canStart || draftBusyRef.current) return;
    draftBusyRef.current = true;
    setSavingDraft(true); setDraftMessage(''); setDraftError('');
    try {
      await onSaveDraft(projectPayload());
      setDraftMessage('创作草稿已保存，在「我的项目」中继续创作。');
    } catch (error: unknown) {
      setDraftError(`${errorMessage(error)}。输入已保留，请先刷新项目列表确认是否已保存，再决定重试。`);
    } finally {
      draftBusyRef.current = false; setSavingDraft(false);
    }
  };

  useEffect(() => {
    if (!deleteTarget) return;
    deleteTriggerRef.current = document.activeElement as HTMLElement | null;
    const dialog = deleteDialogRef.current;
    dialog?.querySelector<HTMLButtonElement>('button')?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !deletingRef.current) { setDeleteTarget(null); setDeleteError(''); }
      if (event.key === 'Tab' && dialog) {
        const buttons = [...dialog.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
        const first = buttons[0], last = buttons[buttons.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener('keydown', keydown);
    return () => { document.removeEventListener('keydown', keydown); deleteTriggerRef.current?.focus(); };
  }, [deleteTarget]);

  useEffect(() => { deletingRef.current = deleting; }, [deleting]);

  const handleExampleClick = (text: string) => {
    setIdea(text); setDraftMessage(''); setDraftError('');
    promptRef.current?.focus();
    promptRef.current?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'center' });
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const allowedExtensions = ['.doc', '.docx', '.txt', '.md', '.pdf'];
    const extension = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
    
    if (!allowedExtensions.includes(extension)) {
      alert(`仅支持 ${allowedExtensions.join(', ')} 格式的文件`);
      return;
    }

    setUploading(true);
    const formData = new FormData();
    formData.append('file', file);

    try {
      const response = await fetch('/api/upload_file', {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        throw new Error('文件上传失败');
      }

      const data = await response.json();
      if (data.file_path) {
        // 记录已上传的文件信息，不修改输入框
        setUploadedFile({
          name: file.name,
          path: data.file_path
        });
      }
    } catch (error) {
      console.error('上传错误:', error);
      alert('上传提取内容失败，请重试');
    } finally {
      setUploading(false);
      // 清空 input 方便下次选择同一文件
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  return (
    <div className="xyq-home h-full flex flex-col items-center overflow-y-auto bg-gray-50/50">
      <header className="studio-header">
        <div className="studio-breadcrumb"><Sparkles size={16} /><span>映序</span><span className="studio-header-caption">让故事从这里开始</span></div>
        <nav aria-label="工作室分区"><a href="#create">创作</a><a href="#inspiration">探索灵感</a><a href="#projects">我的项目</a></nav>
      </header>
      <div className="xyq-home-main w-full max-w-6xl px-6 pt-16 pb-8 flex-shrink-0">
        <section className="studio-hero" aria-labelledby="studio-title">
          <div className="xyq-hero-title">
            <div className="studio-eyebrow"><span /> YOUR NEXT STORY</div>
            <h1 id="studio-title">让想象，<br />成为<span>作品。</span></h1>
            <p>从一句灵感到一部短片。<br />把脑海里的故事，慢慢变成画面。</p>
            <a className="studio-text-link" href="#create">开始我的故事 <ArrowUpRight size={16} /></a>
          </div>
          <button className="studio-feature" onClick={() => handleExampleClick('一位宇航员收到来自地球的最后一封信，在寂静的空间站里，决定踏上回家的旅程。制作一部充满希望的科幻短片。')} aria-label="使用太空来信灵感">
            <MediaImage src="/ui/inspiration-space.png" alt="宇航员隔着空间站窗口眺望地球的灵感封面" className="studio-feature-image" />
            <div className="studio-feature-shade" />
            <span className="studio-feature-tag">故事的可能，不止一种</span>
            <div className="studio-feature-copy"><span>FEATURED INSPIRATION / 01</span><strong>下一站，想象之外。</strong><span>用一句话，开启你的电影宇宙</span></div>
            <span className="studio-feature-arrow"><ArrowUpRight size={22} /></span>
            <span className="studio-feature-note">灵感封面</span>
          </button>
        </section>

        {/* 输入区域 */}
        <div id="create" className="xyq-composer bg-white rounded-2xl shadow-sm border border-gray-200 p-5 mb-6">
          <div className="studio-composer-heading"><span><Sparkles size={16} /> 故事从这里开始</span><span>先留住灵感，再细雕每个镜头</span></div>
          <div className="studio-goal-fields"><label>项目名称（可选）<input value={projectName} maxLength={100} disabled={savingDraft} onChange={e=>setProjectName(e.target.value)} placeholder="为这部作品命名"/></label><label>整片目标时长（秒）<input value={targetDuration} inputMode="numeric" maxLength={3} disabled={savingDraft} onChange={e=>setTargetDuration(e.target.value)}/></label></div>
          <label className="sr-only" htmlFor="studio-idea">创作想法</label>
          <textarea
            id="studio-idea"
            ref={promptRef}
            value={idea}
            onChange={e => { setIdea(e.target.value); setDraftMessage(''); setDraftError(''); }}
            placeholder="描述你的想法，输入一段故事、一个画面或一句灵感……"
            className="xyq-prompt-input w-full bg-transparent text-sm text-gray-800 placeholder-gray-400 resize-none outline-none min-h-[100px]"
            onKeyDown={e => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && !e.nativeEvent.isComposing) {
                e.preventDefault();
                void handleSaveDraft();
              }
            }}
          />

          <div className="xyq-composer-toolbar flex items-center justify-between mt-3 pt-3 border-t border-gray-100">
            <div className="flex items-center gap-3">
              <div className="relative">
                <button
                  onClick={() => setShowEpisodesPanel(!showEpisodesPanel)}
                  className={clsx(
                    'xyq-tool-button flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors',
                    showEpisodesPanel
                      ? 'bg-blue-50 text-blue-600'
                      : 'text-gray-400 hover:text-gray-600 hover:bg-gray-50'
                  )}
                >
                  <ListOrdered className="w-3.5 h-3.5" />
                  剧集：{episodes} 集
                </button>

                {showEpisodesPanel && (
                  <>
                    <div 
                      className="fixed inset-0 z-10" 
                      onClick={() => setShowEpisodesPanel(false)}
                    />
                    <div className="xyq-episodes-popover absolute bottom-full left-0 mb-2 w-48 bg-white rounded-xl shadow-xl border border-gray-100 p-4 z-20 animate-in fade-in slide-in-from-bottom-2">
                      <div className="flex flex-col gap-3">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-semibold text-gray-700">设置总集数</span>
                          <span className="xyq-episode-count text-[10px] text-blue-500 font-bold bg-blue-50 px-1.5 py-0.5 rounded-full">
                            {episodes} 集
                          </span>
                        </div>
                        
                        <div className="flex items-center gap-2">
                          <button 
                            onClick={(e) => { e.stopPropagation(); setEpisodes(Math.max(1, episodes - 1)); }}
                            className="w-7 h-7 flex items-center justify-center rounded-lg bg-gray-50 text-gray-600 hover:bg-gray-100 active:scale-95 transition-all text-sm font-bold"
                          >
                            -
                          </button>
                          <input
                            type="range"
                            min={1}
                            max={10}
                            value={episodes}
                            onChange={(e) => setEpisodes(parseInt(e.target.value))}
                            className="flex-1 h-1.5 bg-gray-100 rounded-lg appearance-none cursor-pointer accent-blue-500"
                          />
                          <button 
                            onClick={(e) => { e.stopPropagation(); setEpisodes(Math.min(10, episodes + 1)); }}
                            className="w-7 h-7 flex items-center justify-center rounded-lg bg-gray-50 text-gray-600 hover:bg-gray-100 active:scale-95 transition-all text-sm font-bold"
                          >
                            +
                          </button>
                        </div>

                        <div className="space-y-1 border-t border-gray-50 pt-2">
                          <p className="text-[10px] text-gray-400 leading-tight">
                            每集预估时长约 1–2 分钟
                          </p>
                          <p className="text-[10px] text-blue-400/80 leading-tight">
                            推荐设置 4–6 集
                          </p>
                        </div>
                      </div>
                    </div>
                  </>
                )}
              </div>

              <button
                onClick={() => setShowSettings(!showSettings)}
                className={clsx(
                  'xyq-tool-button flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors',
                  showSettings
                    ? 'bg-blue-50 text-blue-600'
                    : 'text-gray-400 hover:text-gray-600 hover:bg-gray-50'
                )}
              >
                <Settings2 className="w-3.5 h-3.5" />
                生成配置
              </button>
              <button
                onClick={() => setWebSearch(!webSearch)}
                className={clsx(
                  'xyq-tool-button flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors',
                  webSearch
                    ? 'bg-blue-50 text-blue-600'
                    : 'text-gray-400 hover:text-gray-600 hover:bg-gray-50'
                )}
              >
                <Globe className="w-3.5 h-3.5" />
                联网搜索
              </button>
            </div>
            <div className="flex items-center gap-2">
              {/* 隐藏的文件输入框 */}
              <input
                type="file"
                ref={fileInputRef}
                onChange={handleFileUpload}
                accept=".doc,.docx,.txt,.md,.pdf"
                className="hidden"
              />
              <button
                onClick={() => {
                  if (uploadedFile) {
                    setUploadedFile(null); // 已有文件则点击取消
                  } else {
                    fileInputRef.current?.click();
                  }
                }}
                disabled={uploading}
                className={clsx(
                  'xyq-upload-button flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium transition-colors border shadow-sm relative',
                  uploading
                    ? 'bg-gray-50 text-gray-400 cursor-not-allowed border-gray-100'
                    : uploadedFile
                    ? 'bg-blue-50 text-blue-600 border-blue-100 hover:bg-blue-100'
                    : 'bg-white text-gray-600 hover:bg-gray-50 border-gray-200'
                )}
                title={uploadedFile ? `已选择: ${uploadedFile.name} (点击取消)` : "上传文档 (Word/TXT/MD)"}
              >
                {uploading ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : uploadedFile ? (
                  <CheckCircle className="w-4 h-4" />
                ) : null}
                {uploading ? '上传中……' : uploadedFile ? `已选：${uploadedFile.name.length > 8 ? `${uploadedFile.name.substring(0, 8)}…` : uploadedFile.name}` : '上传文件'}
              </button>
              <button className="studio-save-button" onClick={() => void handleSaveDraft()} disabled={!canStart}>
                {savingDraft ? <Loader2 size={16} className="animate-spin" /> : <Plus size={17} />}
                {savingDraft ? '正在保存…' : '保存创作草稿'}
              </button>
              <button
                onClick={() => handleStart(false)}
                disabled={!canStart}
                className={clsx(
                  'xyq-action-secondary flex items-center gap-2 px-5 py-2 rounded-xl text-sm font-medium transition-colors',
                  canStart
                    ? 'bg-white text-gray-900 hover:bg-gray-50 border border-gray-300'
                    : 'bg-gray-100 text-gray-400 cursor-not-allowed'
                )}
              >
                逐步创作
              </button>
              <button
                onClick={() => handleStart(true)}
                disabled={!canStart}
                className={clsx(
                  'xyq-action-primary flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-medium transition-colors',
                  canStart
                    ? 'bg-black text-white hover:bg-gray-800 shadow-sm'
                    : 'bg-gray-100 text-gray-400 cursor-not-allowed'
                )}
                title="自动执行全部六个阶段，无需手动确认"
              >
                一键生成
              </button>
            </div>
          </div>
          <div className="studio-composer-footnote"><span><Bookmark size={13} /> 草稿保存后，可随时回来继续</span><span>Ctrl / ⌘ + Enter 保存</span></div>
          {draftMessage && <p className="studio-feedback success" role="status">{draftMessage}</p>}
          {draftError && <p className="studio-feedback error" role="alert">{draftError}</p>}
          {(configLoading || configError) && (
            <div className={clsx('mt-3 text-xs', configError ? 'text-red-500' : 'text-gray-400')}>
              {configError || '正在读取默认模型……'}
            </div>
          )}

          {/* 模型设置折叠面板 */}
          {showSettings && (
            <div className="xyq-generation-panel mt-4 p-4 bg-gray-50 rounded-xl space-y-4 text-xs">
              <div className="grid grid-cols-1 gap-3">
                <label className="flex flex-col gap-1">
                  <span className="text-gray-500 font-medium">风格</span>
                  <select
                    value={selectedStyle}
                    onChange={e => setSelectedStyle(e.target.value)}
                    className="bg-white border border-gray-200 rounded-lg px-2.5 py-2 text-gray-700 outline-none"
                  >
                    {STYLES.map(s => (
                      <option key={s.id} value={s.id}>{s.label}</option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <label className="flex flex-col gap-1">
                  <span className="text-gray-500 font-medium">视频分辨率</span>
                  <select
                    value={selectedResolution}
                    onChange={e => setSelectedResolution(e.target.value)}
                    className="bg-white border border-gray-200 rounded-lg px-2.5 py-2 text-gray-700 outline-none min-h-[40px]"
                  >
                    {VIDEO_RESOLUTIONS.map(item => (
                      <option key={item.id} value={item.id}>{item.label}</option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-gray-500 font-medium">视频长宽比</span>
                  <div className="flex gap-1">
                    {VIDEO_RATIOS.map(r => (
                      <button
                        key={r.id}
                        onClick={() => setSelectedRatio(r.id)}
                        className={`flex flex-col items-center gap-1 p-2 rounded-lg border transition-all ${
                          selectedRatio === r.id
                            ? 'border-indigo-500 bg-indigo-50'
                            : 'border-gray-200 hover:border-gray-300'
                        }`}
                        title={r.label}
                      >
                        <div
                          className="bg-gray-700 rounded-sm"
                          style={{
                            width: r.ratio === '16:9' ? '32px' :
                                   r.ratio === '9:16' ? '18px' :
                                   r.ratio === '1:1' ? '24px' :
                                   r.ratio === '4:3' ? '28px' :
                                   r.ratio === '3:4' ? '20px' :
                                   '36px',
                            height: r.ratio === '16:9' ? '18px' :
                                   r.ratio === '9:16' ? '32px' :
                                   r.ratio === '1:1' ? '24px' :
                                   r.ratio === '4:3' ? '21px' :
                                   r.ratio === '3:4' ? '28px' :
                                   '15px',
                          }}
                        />
                        <span className="text-[10px] text-gray-500">{r.label}</span>
                      </button>
                    ))}
                  </div>
                </label>
              </div>

              <div className="space-y-3 border-t border-gray-200/70 pt-4">
                <div className="flex items-center justify-between">
                  <span className="text-gray-500 font-semibold">模型配置</span>
                  <span className="text-[10px] text-gray-400">用于主流程各阶段调用</span>
                </div>
                <div className="grid grid-cols-2 gap-3">
                <label className="flex flex-col gap-1">
                  <span className="text-gray-500 font-medium">LLM 模型</span>
                  <select
                    value={selectedLLM}
                    onChange={e => setSelectedLLM(e.target.value)}
                    className="bg-white border border-gray-200 rounded-lg px-2.5 py-2 text-gray-700 outline-none"
                  >
                    {llmProviders.map(pg => (
                      <optgroup key={pg.provider} label={pg.label}>
                        {pg.models.map(m => (
                          <option key={m.id} value={m.id}>{m.label}</option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-gray-500 font-medium">VLM 评估模型</span>
                  <select
                    value={selectedVLM}
                    onChange={e => setSelectedVLM(e.target.value)}
                    className="bg-white border border-gray-200 rounded-lg px-2.5 py-2 text-gray-700 outline-none"
                  >
                    {vlmProviders.map(pg => (
                      <optgroup key={pg.provider} label={pg.label}>
                        {pg.models.map(m => (
                          <option key={m.id} value={m.id}>{m.label}</option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-gray-500 font-medium">文生图</span>
                  <select
                    value={selectedT2I}
                    onChange={e => setSelectedT2I(e.target.value)}
                    className="bg-white border border-gray-200 rounded-lg px-2.5 py-2 text-gray-700 outline-none"
                  >
                    {t2iProviders.map(pg => (
                      <optgroup key={pg.provider} label={pg.label}>
                        {pg.models.map(m => (
                          <option key={m.id} value={m.id}>{m.label}</option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-gray-500 font-medium">图生图</span>
                  <select
                    value={selectedI2I}
                    onChange={e => setSelectedI2I(e.target.value)}
                    className="bg-white border border-gray-200 rounded-lg px-2.5 py-2 text-gray-700 outline-none"
                  >
                    {i2iProviders.map(pg => (
                      <optgroup key={pg.provider} label={pg.label}>
                        {pg.models.map(m => (
                          <option key={m.id} value={m.id}>{m.label}</option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1 col-span-2">
                  <span className="text-gray-500 font-medium">视频生成方式</span>
                  <select
                    value={selectedVideoMode}
                    onChange={e => setSelectedVideoMode(e.target.value as VideoGenerationMode)}
                    className="bg-white border border-gray-200 rounded-lg px-2.5 py-2 text-gray-700 outline-none"
                  >
                    {VIDEO_GENERATION_MODES.map(item => (
                      <option key={item.id} value={item.id}>{item.label}</option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1 col-span-2">
                  <span className="text-gray-500 font-medium">{selectedVideoModeLabel}模型</span>
                  <select
                    value={activeVideoModel}
                    onChange={e => setActiveVideoModel(e.target.value)}
                    className="bg-white border border-gray-200 rounded-lg px-2.5 py-2 text-gray-700 outline-none"
                  >
                    {activeVideoProviders.map(pg => (
                      <optgroup key={pg.provider} label={pg.label}>
                        {pg.models.map(m => (
                          <option key={m.id} value={m.id}>{m.label}</option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                </label>
                <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={enableConcurrency}
                    onChange={e => setEnableConcurrency(e.target.checked)}
                    className="w-4 h-4 rounded border-gray-300 text-blue-500 focus:ring-blue-500/30"
                  />
                  <span className="text-gray-600">并发生成</span>
                </label>
                </div>
              </div>
            </div>
          )}
        </div>

        <section className="studio-tools" aria-label="创作工具">
          {[
            { href: '/sandbox', label: '临时工作台', note: '自由试验你的画面', icon: Layers },
            { href: '/pipelines/standard', label: '文艺短视频', note: '给文字一段影像', icon: Clapperboard },
            { href: '/pipelines/action-transfer', label: '动作迁移', note: '让角色动起来', icon: Repeat2 },
            { href: '/pipelines/digital-human', label: '数字人口播', note: '让表达更生动', icon: UserRound },
          ].map(tool => <Link href={tool.href} key={tool.href} className="studio-tool-card"><tool.icon size={21} /><div><strong>{tool.label}</strong><span>{tool.note}</span></div><ArrowUpRight size={15} /></Link>)}
        </section>

        <section id="inspiration" className="xyq-inspiration">
          <div className="studio-section-heading"><div><span className="studio-eyebrow">A LITTLE INSPIRATION</span><h2>好故事，始于一个好画面</h2></div><span className="studio-section-note">选择灵感，写下你的版本</span></div>
          <div className="studio-filters" aria-label="灵感分类">{INSPIRATION_CATEGORIES.map(value => <button key={value} aria-pressed={category === value} onClick={() => setCategory(value)}>{value}</button>)}<span>灵感封面 · 点击填入创意</span></div>
          <div className="xyq-inspiration-grid grid">
            {filterPresets(category).map(ex => (
              <button key={ex.title} onClick={() => handleExampleClick(ex.text)} className="xyq-inspiration-card text-left group" aria-label={`使用灵感：${ex.title}`}>
                <div className="xyq-inspiration-media"><MediaImage src={ex.image} alt={`${ex.title}灵感封面`} loading="lazy" /><span className="studio-genre">{ex.category}</span><span className="studio-use-prompt"><Plus size={15} />使用灵感</span></div>
                <div className="xyq-inspiration-copy"><div>{ex.title}<ArrowUpRight size={16} /></div><div>{ex.description}</div></div>
              </button>
            ))}
          </div>
        </section>

        <section id="projects" className="xyq-history">
          <div className="studio-section-heading"><div><span className="studio-eyebrow">YOUR CREATIVE SPACE</span><h2>我的项目 <span className="studio-project-count">{historyLoading ? '…' : historyError ? '—' : history.length}</span></h2></div><div className="studio-project-controls"><label className="studio-search"><Search size={15} /><input aria-label="搜索项目" placeholder="寻找你的故事" value={query} onChange={e => setQuery(e.target.value)} />{query && <button aria-label="清除项目搜索" onClick={() => setQuery('')}><X size={14} /></button>}</label><button className="studio-manage" onClick={() => setManageMode(m => !m)}>{manageMode ? '完成管理' : '管理'}</button></div></div>
          {historyLoading ? <div className="studio-project-skeleton" role="status" aria-label="正在读取项目"><span /><span /><span /></div>
            : historyError ? <div className="studio-empty" role="alert"><FolderOpen size={28} /><strong>暂时没有读到你的项目</strong><p>{historyError}</p><button onClick={onRefreshHistory}>重新读取项目</button></div>
            : visibleProjects.length === 0 ? <div className="studio-empty"><FolderOpen size={28} /><strong>{query ? '没有找到这个故事' : '你的下一部作品，从这里开始'}</strong><p>{query ? '换个关键词，或清除搜索看看全部项目。' : '把灵感写在上方，保存草稿后就能在这里继续。'}</p><button onClick={() => query ? setQuery('') : promptRef.current?.focus()}>{query ? '查看全部项目' : '写下第一个故事'}</button></div>
            : <div className="studio-project-grid">{visibleProjects.map(item => {
              const progress = projectSummary(item.stages, STAGES.map(stage => stage.id));
              return <article className="xyq-history-card studio-project-card" key={item.id}>
                <div className="studio-project-top"><span className="studio-project-icon"><Clapperboard size={22} /></span><span className={`studio-status ${progress.state}`}>{progress.label}</span></div>
                <button className="studio-project-open" onClick={() => onResumeProject(item.id)}><strong>{item.idea || '未命名故事'}</strong><span>{item.date || '日期待同步'}{item.style ? ` · ${STYLES.find(style => style.id === item.style)?.label || item.style}` : ''}</span></button>
                <div className="studio-project-bottom"><span><Clock size={12} /> {progress.completed} / {progress.total} 阶段</span>{manageMode ? <button className="studio-delete-project" aria-label={`删除项目：${item.idea}`} onClick={() => { setDeleteTarget(item.id); setDeleteError(''); }}><Trash2 size={16} /></button> : <Link className="studio-assets-link" href={`/projects/${encodeURIComponent(item.id)}/assets`}><Layers size={14} />素材与版本<ArrowUpRight size={14} /></Link>}</div>
                <div className="studio-project-progress" aria-label={`已完成 ${progress.completed} 个阶段`}><span style={{ width: `${progress.completed / progress.total * 100}%` }} /></div>
              </article>;
            })}</div>}
        </section>
        <footer className="studio-footer"><span>给每个故事一个开始</span><span>本地映序</span></footer>
      </div>

      {/* 删除确认弹窗 */}
      {deleteTarget && (
        <div className="xyq-dialog-scrim fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div ref={deleteDialogRef} className="xyq-delete-dialog bg-white rounded-2xl shadow-xl w-80 p-6 relative" role="dialog" aria-modal="true" aria-labelledby="delete-dialog-title">
            <div className="flex items-center gap-2 mb-4">
              <Trash2 className="w-4 h-4 text-red-500" />
              <h4 id="delete-dialog-title" className="text-sm font-semibold text-gray-700">删除项目</h4>
            </div>
            <p className="text-xs text-gray-500 mb-6">确认删除这个项目吗？此操作不可撤销。</p>
            {deleteError && <p className="text-xs text-red-500 mb-2">{deleteError}</p>}
            <div className="flex gap-2">
              <button
                onClick={() => { setDeleteTarget(null); setDeleteError(''); }}
                disabled={deleting}
                className="flex-1 text-sm py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50"
              >
                取消
              </button>
              <button
                onClick={handleDelete}
                disabled={deleting}
                className="flex-1 text-sm py-1.5 rounded-lg bg-red-500 text-white hover:bg-red-600 disabled:opacity-50"
              >
                {deleting ? '删除中…' : '确认删除'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
