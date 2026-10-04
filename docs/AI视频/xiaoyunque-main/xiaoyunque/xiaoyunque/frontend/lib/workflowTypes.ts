import type { VideoGenerationMode } from '@/config/models';

export interface GenerationParams {
  idea?: string;
  user_textbox_input?: string;
  style?: string;
  video_ratio?: string;
  video_resolution?: string;
  llm_model?: string;
  vlm_model?: string;
  image_t2i_model?: string;
  image_it2i_model?: string;
  video_model?: string;
  video_first_frame_model?: string;
  video_start_end_model?: string;
  video_reference_model?: string;
  video_generation_mode?: VideoGenerationMode;
  expand_idea?: boolean;
  enable_concurrency?: boolean;
  web_search?: boolean;
  episodes?: number;
  scene_number?: number;
}

export interface MediaItem {
  id: string;
  name: string;
  description: string;
  selected: string;
  versions: string[];
  status?: 'pending' | 'done' | 'failed' | 'running';
  duration?: number;
  index?: number;
  is_new?: boolean;
  visual_prompt?: string;
  plot?: string;
  content?: string;
}

export interface WorkflowShot {
  shot_number: number;
  shot_type: string;
  duration: number;
  content: string;
  visual_prompt?: string;
  plot?: string;
}

export interface WorkflowSegment {
  segment_id: string;
  segment_number: number;
  episode_number: number;
  location: string;
  characters: string[];
  total_duration: number;
  shots: WorkflowShot[];
  is_new?: boolean;
}

export interface WorkflowEpisode {
  episode_number?: number;
  episode?: number;
  act_number?: number;
  act_title?: string;
  episode_title?: string;
  title?: string;
  content?: string;
  segments?: WorkflowSegment[];
}

export interface FinalVideo {
  episode?: number;
  name?: string;
  path: string;
}

export interface StageArtifact {
  session_id?: string;
  title?: string;
  phase?: string;
  characters?: MediaItem[];
  settings?: MediaItem[];
  scenes?: MediaItem[];
  clips?: MediaItem[];
  images?: MediaItem[];
  shots?: WorkflowShot[];
  segments?: WorkflowSegment[];
  episodes?: WorkflowEpisode[];
  scene_images?: Record<string, string>;
  video_clips?: Record<string, string>;
  final_videos?: FinalVideo[];
  final_video?: string;
  beat_sheet?: string;
  completed_acts?: unknown[];
  selected_logline?: unknown;
  payload?: StageArtifact;
  [field: string]: unknown;
}

export interface ProjectSnapshot extends GenerationParams {
  session_id: string;
  current_stage: string;
  status: Record<string, string>;
  error: string | null;
  meta?: GenerationParams;
  artifacts?: Record<string, StageArtifact>;
  stage_progress?: Record<string, { percent?: number; message?: string; step?: string }>;
  updated_at?: string;
}

export interface SessionSummary {
  id: string;
  title?: string;
  idea?: string;
  style?: string;
  date?: number;
  current_stage?: string;
  status?: Record<string, string>;
}

export function errorMessage(error: unknown, fallback = '操作失败，请重试'): string {
  return error instanceof Error ? error.message : fallback;
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function stringMap(value: unknown): Record<string, string> {
  if (!isRecord(value)) return {};
  return Object.fromEntries(Object.entries(value).filter((entry): entry is [string, string] => typeof entry[1] === 'string'));
}

/** Decode the status envelope before using it to restore navigation and model choices. */
export function parseProjectSnapshot(value: unknown): ProjectSnapshot {
  if (!isRecord(value) || typeof value.session_id !== 'string' || typeof value.current_stage !== 'string'
      || !isRecord(value.status) || !Object.values(value.status).every(item => typeof item === 'string')) {
    throw new Error('项目状态格式异常，请刷新后重试');
  }
  if (value.error !== undefined && value.error !== null && typeof value.error !== 'string') {
    throw new Error('项目错误状态格式异常');
  }
  if (value.meta !== undefined) {
    if (!isRecord(value.meta)) throw new Error('项目配置格式异常');
    for (const key of ['idea', 'user_textbox_input', 'style', 'video_ratio', 'video_resolution', 'llm_model', 'vlm_model', 'image_t2i_model', 'image_it2i_model', 'video_model', 'video_first_frame_model', 'video_start_end_model', 'video_reference_model']) {
      if (value.meta[key] !== undefined && typeof value.meta[key] !== 'string') throw new Error('项目配置格式异常');
    }
    for (const key of ['expand_idea', 'enable_concurrency', 'web_search']) {
      if (value.meta[key] !== undefined && typeof value.meta[key] !== 'boolean') throw new Error('项目配置格式异常');
    }
    for (const key of ['episodes', 'scene_number']) {
      if (value.meta[key] !== undefined && (typeof value.meta[key] !== 'number' || !Number.isFinite(value.meta[key]))) throw new Error('项目配置格式异常');
    }
    const mode = value.meta.video_generation_mode;
    if (mode !== undefined && !['first_frame', 'start_end_frame', 'reference'].includes(String(mode))) throw new Error('项目视频配置格式异常');
  }
  if (value.artifacts !== undefined && (!isRecord(value.artifacts) || !Object.values(value.artifacts).every(isRecord))) {
    throw new Error('项目产物格式异常');
  }
  if (value.stage_progress !== undefined) {
    if (!isRecord(value.stage_progress) || !Object.values(value.stage_progress).every(progress => isRecord(progress)
      && (progress.percent === undefined || typeof progress.percent === 'number')
      && (progress.message === undefined || typeof progress.message === 'string')
      && (progress.step === undefined || typeof progress.step === 'string'))) throw new Error('项目进度格式异常');
  }
  // Stage-specific payloads keep their existing schemas; this validates only the restoration envelope.
  return { ...value, error: value.error ?? null } as ProjectSnapshot;
}

export function parseSessions(value: unknown): SessionSummary[] {
  if (!isRecord(value) || !Array.isArray(value.sessions)) throw new Error('项目列表格式异常');
  return value.sessions.map(item => {
    if (!isRecord(item) || typeof item.id !== 'string') throw new Error('项目列表格式异常');
    for (const key of ['title', 'idea', 'style', 'current_stage']) {
      if (item[key] !== undefined && typeof item[key] !== 'string') throw new Error('项目列表格式异常');
    }
    if (item.date !== undefined && typeof item.date !== 'number') throw new Error('项目时间格式异常');
    if (item.status !== undefined && (!isRecord(item.status) || !Object.values(item.status).every(status => typeof status === 'string'))) throw new Error('项目列表状态格式异常');
    return { ...item, id: item.id } as SessionSummary;
  });
}

export function apiErrorMessage(value: unknown, fallback: string): string {
  if (!isRecord(value)) return fallback;
  if (isRecord(value.error) && typeof value.error.message === 'string') return value.error.message;
  if (typeof value.detail === 'string') return value.detail;
  return fallback;
}
