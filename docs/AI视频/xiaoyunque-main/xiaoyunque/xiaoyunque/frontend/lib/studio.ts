import { PROMPT_EXAMPLES } from '@/config/examples';
import { getProjectStatus } from '@/lib/projectStatus';
import { startProject } from '@/lib/workflowApi';
import { isRecord } from '@/lib/workflowTypes';
import type { ProjectParams } from '@/components/HomePage';

export const INSPIRATION_CATEGORIES = ['全部', '科幻', '国风', '悬疑', '治愈'] as const;
export type InspirationCategory = typeof INSPIRATION_CATEGORIES[number];
const categories: InspirationCategory[] = ['科幻', '国风', '悬疑', '治愈', '科幻', '国风'];
const names = ['space', 'ink', 'detective', 'cat', 'mars', 'wuxia'];

export const STUDIO_PRESETS = PROMPT_EXAMPLES.map((example, index) => ({
  ...example, category: categories[index], image: `/ui/inspiration-${names[index]}.png`,
}));

export function filterPresets(category: InspirationCategory) {
  return STUDIO_PRESETS.filter(preset => category === '全部' || preset.category === category);
}

export function matchesProject(idea: string, query: string) {
  return idea.toLocaleLowerCase('zh-CN').includes(query.trim().toLocaleLowerCase('zh-CN'));
}

export function projectSummary(statuses: Record<string, string> | undefined, order: readonly string[]) {
  const stages = Object.fromEntries(Object.entries(statuses || {}).map(([id, status]) => [id, { status: status === 'session_completed' ? 'completed' : status }]));
  const state = getProjectStatus(stages, order);
  const completed = order.filter(id => ['completed', 'session_completed'].includes(statuses?.[id] || '')).length;
  const labels = { pending: '创作草稿', running: '创作中', waiting: '等待确认', completed: '已完成', error: '需要处理', stopped: '已停止' };
  const hasUnknown = order.some(id => statuses?.[id] !== undefined && !['pending', 'running', 'waiting', 'completed', 'session_completed', 'error', 'stopped'].includes(statuses[id]));
  return { state, label: state === 'pending' && hasUnknown ? '状态待同步' : state === 'pending' && completed > 0 ? '待继续' : labels[state], completed, total: order.length };
}

/** Persist a project only. Never execute a workflow stage or a model. */
export async function saveStudioDraft(params: ProjectParams) {
  const result = await startProject({ ...params, expand_idea: false, web_search: false });
  if (!result || typeof result.session_id !== 'string' || !/^[a-f0-9]{32}$/.test(result.session_id)
    || !isRecord(result.status) || !Object.values(result.status).every(status => typeof status === 'string')) {
    throw new Error('保存结果暂时无法确认，请重新读取项目列表');
  }
  return result;
}
