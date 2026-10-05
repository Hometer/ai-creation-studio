import { isRecord, parseSessions } from './workflowTypes';
import { normalizeStageStatus } from './projectStatus';

export const SOURCES = { workflow: '项目流程', pipeline: '专项创作', generation: '本地生成任务', sandbox: '临时工作台' } as const;
export const STATUSES = { draft: '待开始', queued: '排队中', running: '进行中', waiting: '待确认', completed: '已完成', failed: '失败', review: '需核对', unknown: '状态未知' } as const;
export type TaskSource = keyof typeof SOURCES;
export type TaskStatus = keyof typeof STATUSES;
export interface TaskRecord {
  key: string; id: string; title: string; source: TaskSource; status: TaskStatus; date: string | null;
  href: string | null; projectId: string | null; phase: string; progress: number | null; note: string;
  simulation: boolean; resultUrl: string | null; billing: string | null; reasons: string[];
}
export interface SourceSnapshot { records: TaskRecord[]; failed: boolean; updatedAt: string | null }
export type CenterSnapshot = Record<TaskSource, SourceSnapshot>;
export const emptySnapshot = (): CenterSnapshot => ({ workflow: { records: [], failed: false, updatedAt: null }, pipeline: { records: [], failed: false, updatedAt: null }, generation: { records: [], failed: false, updatedAt: null }, sandbox: { records: [], failed: false, updatedAt: null } });
const stageNames: Record<string, string> = { script_generation: '剧本', character_design: '角色', storyboard: '分镜', reference_generation: '参考图', video_generation: '视频', post_production: '后期' };
const pipelineRoutes: Record<string, string> = { standard: 'standard', quick_create: 'standard', action_transfer: 'action-transfer', digital_human: 'digital-human' };
const pipelineLabels: Record<string, string> = { standard: '文艺短视频', quick_create: '文艺短视频', action_transfer: '动作迁移', digital_human: '数字人口播' };
const phaseNames: Record<string, string> = { blocked: '等待执行条件', queued: '已入队', submitting: '提交阶段', submitted: '已提交', running: '处理阶段', fetching: '结果归档', succeeded: '归档完成', failed: '执行失败', manual_review: '等待人工核对' };
const reasonNames: Record<string, string> = { quote_unavailable: '费用尚未确认', approval_required: '尚未保存费用确认', source_changed: '计划来源已变化', quote_expired: '报价已过期', project_budget_exceeded: '超出项目预算', provider_unavailable: '生成执行尚未接入' };
const errorNames: Record<string, string> = { PROVIDER_INIT_FAILED: '平台初始化失败，保留原任务待核对', SUBMISSION_UNCERTAIN: '提交结果不确定，请核对原任务', SUBMISSION_NOT_FOUND: '暂未查到原任务', QUERY_FAILED: '状态查询失败，原任务已保留', QUERY_LIMIT_REACHED: '查询次数达到上限', PROVIDER_FAILED: '平台报告任务失败', PROVIDER_CONTRACT_INVALID: '平台返回信息需人工核对', RESULT_FAILED: '结果读取或归档失败', SOURCE_CHANGED: '计划来源已变化', QUOTE_EXPIRED: '报价已过期' };
const invalid = () => new Error('任务数据格式异常');
const identifier = (v: unknown): string => { if (typeof v !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(v)) throw invalid(); return v; };
const id32 = (v: unknown): string => { if (typeof v !== 'string' || !/^[a-f0-9]{32}$/.test(v)) throw invalid(); return v; };
const timestamp = (v: unknown): string | null => { if (v === undefined || v === null) return null; if (typeof v !== 'string' || !Number.isFinite(Date.parse(v))) throw invalid(); return v; };
const title = (v: unknown, fallback: string): string => typeof v === 'string' && v.trim() ? v.trim().slice(0, 100) : fallback;
const percent = (v: unknown): number | null => { if (v === undefined || v === null) return null; if (typeof v !== 'number' || !Number.isFinite(v) || v < 0 || v > 100) throw invalid(); return Math.round(v); };
const minor = (v: unknown): number => { if (typeof v !== 'number' || !Number.isSafeInteger(v) || v < 0) throw invalid(); return v; };
function mappedStatus(v: string): TaskStatus {
  return ({ pending: 'queued', queued: 'queued', running: 'running', submitting: 'running', submitted: 'running', fetching: 'running', waiting: 'waiting', blocked: 'waiting', completed: 'completed', succeeded: 'completed', failed: 'failed', error: 'failed', stopped: 'review', manual_review: 'review' } as Record<string, TaskStatus>)[v] ?? 'unknown';
}
function base(source: TaskSource, id: string, status: TaskStatus, date: string | null): TaskRecord {
  return { key: `${source}:${id}`, id, source, status, date, title: SOURCES[source], href: null, projectId: null, phase: STATUSES[status], progress: null, note: '', simulation: false, resultUrl: null, billing: null, reasons: [] };
}
export function parseTaskSource(source: TaskSource, value: unknown): TaskRecord[] {
  let rows: TaskRecord[];
  if (source === 'workflow') {
    rows = parseSessions(value).map(session => {
      const id = identifier(session.id), statuses = session.status ?? {};
      const values = Object.keys(stageNames).map(s => normalizeStageStatus(statuses[s] ?? 'pending'));
      const status: TaskStatus = values.includes('running') ? 'running' : values.some(s => s === 'error' || s === 'failed') ? 'failed'
        : values.includes('waiting') ? 'waiting' : values.includes('stopped') ? 'review' : values.every(s => s === 'completed') ? 'completed'
          : values.some(s => !['pending', 'completed'].includes(s)) ? 'unknown' : 'draft';
      const record = base(source, id, status, session.date === undefined ? null : new Date(session.date * 1000).toISOString());
      record.title = title(session.title, '未命名项目'); record.projectId = id;
      const current = session.current_stage && stageNames[session.current_stage] ? session.current_stage : null;
      record.href = `/?session=${encodeURIComponent(id)}${current ? `&stage=${current}` : ''}`;
      record.phase = `${current ? stageNames[current] + ' · ' : ''}已完成 ${values.filter(s => s === 'completed').length} / ${values.length} 个阶段`;
      record.note = status === 'draft' ? '打开项目继续创作；打开页面不会自动生成。' : '状态来自保存的项目流程；服务重启后执行是否继续，请在项目中核对。';
      return record;
    });
  } else {
    const list = source === 'generation' ? value : isRecord(value) ? value.tasks : null;
    if (!Array.isArray(list)) throw invalid();
    rows = list.map(item => {
      if (!isRecord(item) || typeof item.status !== 'string') throw invalid();
      const id = source === 'generation' ? id32(item.task_id) : identifier(source === 'sandbox' ? item.id : item.task_id);
      const record = base(source, id, mappedStatus(item.status), timestamp(item.updated_at ?? item.created_at));
      if (source === 'pipeline') {
        if (typeof item.pipeline !== 'string') throw invalid();
        record.title = title(isRecord(item.output) ? item.output.title : null, pipelineLabels[item.pipeline] ?? '专项创作任务');
        record.phase = pipelineLabels[item.pipeline] ?? '未识别的创作流程'; record.progress = percent(item.progress);
        record.href = pipelineRoutes[item.pipeline] ? `/pipelines/${pipelineRoutes[item.pipeline]}?task=${encodeURIComponent(id)}` : null;
        record.note = record.status === 'failed' ? '生成未完成。请返回原工作台查看原因与已保留的结果。' : '详细过程与结果保留在原工作台；读取记录不会再次生成。';
      } else if (source === 'sandbox') {
        if (typeof item.tool !== 'string') throw invalid();
        record.title = ({ llm: '文字生成', vlm: '图像理解', t2i: '文字生图', i2i: '图像编辑', video: '视频生成' } as Record<string, string>)[item.tool] ?? '临时创作任务';
        record.href = `/sandbox?task=${encodeURIComponent(id)}`; record.progress = percent(item.progress);
        record.note = '仅当前进程的活动记录，服务重启后不保证保留。';
      } else {
        record.projectId = identifier(item.project_id); identifier(item.segment_id);
        if (typeof item.simulation_only !== 'boolean' || item.real_execution_allowed !== false || !Array.isArray(item.blockers) || !item.blockers.every(s => typeof s === 'string') || !isRecord(item.billing)) throw invalid();
        if (!Number.isSafeInteger(item.revision) || Number(item.revision) < 1) throw invalid();
        record.simulation = item.simulation_only;
        record.title = `${record.simulation ? '人工演练' : '片段任务'} · ${title(item.segment_id, '片段')}`;
        record.phase = phaseNames[item.status] ?? '未识别的执行阶段';
        record.reasons = item.blockers.map(code => reasonNames[code] ?? '执行条件需核对');
        if (item.error !== null && item.error !== undefined) {
          if (!isRecord(item.error) || typeof item.error.code !== 'string') throw invalid();
          record.reasons.unshift(errorNames[item.error.code] ?? '任务错误需在原记录中核对');
        }
        const billing = item.billing;
        if (!['unreserved', 'reserved', 'settled'].includes(String(billing.state))) throw invalid();
        const reserved = minor(billing.reserved_minor);
        const billed = billing.billed_minor == null ? null : minor(billing.billed_minor);
        if (billing.state === 'unreserved' && (reserved !== 0 || billed !== null) || billing.state === 'reserved' && billed !== null) throw invalid();
        if (billing.state === 'settled' && (billed === null || billed > reserved)) throw invalid();
        record.billing = record.simulation ? billing.state === 'settled' ? `演练已结算 ${billed} SIM` : billing.state === 'reserved' ? `演练预留 ${reserved} SIM` : '演练尚未预留 SIM' : '真实费用未确认';
        if (item.result !== null && item.result !== undefined) {
          if (!isRecord(item.result) || item.status !== 'succeeded') throw invalid();
          const expected = `/api/assets/${id32(item.result.asset_id)}/versions/${id32(item.result.version_id)}/media`;
          if (item.result_url !== expected) throw invalid();
          record.resultUrl = expected;
        } else if (item.result_url != null || item.status === 'succeeded') throw invalid();
        record.href = `/?session=${encodeURIComponent(record.projectId)}`;
        record.note = record.simulation ? 'SIM 为本地人工演练点数，结果不代表模型效果或真实费用。' : '生成执行尚未接入，查看任务不会提交或推进生成。';
      }
      return record;
    });
  }
  if (new Set(rows.map(r => r.key)).size !== rows.length) throw invalid();
  return rows;
}
export function taskRecords(snapshot: CenterSnapshot): TaskRecord[] {
  return Object.values(snapshot).flatMap(s => s.records).sort((a, b) => (b.date ? Date.parse(b.date) : 0) - (a.date ? Date.parse(a.date) : 0) || a.key.localeCompare(b.key));
}
export function filterTasks(rows: TaskRecord[], source: TaskSource | 'all', status: TaskStatus | 'all', query: string): TaskRecord[] {
  const q = query.trim().toLocaleLowerCase();
  return rows.filter(r => (source === 'all' || source === r.source) && (status === 'all' || status === r.status) && (!q || `${r.title} ${r.id} ${r.projectId ?? ''}`.toLocaleLowerCase().includes(q)));
}
export function nextReadDelay(snapshot: CenterSnapshot, failureCount: number): number {
  if (Object.values(snapshot).some(s => s.failed)) return Math.min(60000, 5000 * 2 ** Math.min(failureCount, 4));
  return taskRecords(snapshot).some(t => ['queued', 'running'].includes(t.status)) ? 5000 : 30000;
}
const endpoints: Record<TaskSource, string> = { workflow: '/api/sessions', pipeline: '/api/tasks?limit=500', sandbox: '/api/sandbox/tasks', generation: '/api/generation-tasks' };
export async function readTaskCenter(previous: CenterSnapshot, signal?: AbortSignal): Promise<CenterSnapshot> {
  const entries = await Promise.all((Object.keys(SOURCES) as TaskSource[]).map(async source => {
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) controller.abort();
    const timer = setTimeout(abort, 10000);
    try {
      const response = await fetch(endpoints[source], { signal: controller.signal, cache: 'no-store' });
      if (!response.ok) throw invalid();
      const records = parseTaskSource(source, await response.json());
      return [source, { records, failed: false, updatedAt: new Date().toISOString() }] as const;
    } catch {
      return [source, { ...previous[source], failed: true }] as const;
    } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
  }));
  return Object.fromEntries(entries) as CenterSnapshot;
}
