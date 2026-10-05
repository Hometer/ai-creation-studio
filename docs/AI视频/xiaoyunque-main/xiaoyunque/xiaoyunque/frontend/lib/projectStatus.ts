export type ProjectStatus = 'pending' | 'running' | 'waiting' | 'completed' | 'error' | 'stopped';

export const BUSINESS_STAGES = ['script_generation', 'character_design', 'storyboard', 'reference_generation', 'video_generation', 'post_production'] as const;
export function normalizeStageStatus(status: string): string {
  return ({ session_completed: 'completed', waiting_intervention: 'waiting', failed: 'error' } as Record<string, string>)[status] ?? status;
}

export function workflowProgress(statuses: Record<string, string>) {
  const values = BUSINESS_STAGES.map(id => normalizeStageStatus(statuses[id] ?? 'pending'));
  const completed = values.filter(value => value === 'completed').length;
  return { completed, total: values.length, percent: Math.round(completed / values.length * 100), allDone: values.every(value => value === 'completed') };
}

/** Only business stages count toward completion; init and missing stages do not. */
export function getProjectStatus(
  stages: Record<string, { status: string }>,
  stageOrder: readonly string[],
): ProjectStatus {
  const statuses = stageOrder.map(id => normalizeStageStatus(stages[id]?.status ?? 'pending'));
  for (const status of ['running', 'error', 'waiting', 'stopped'] as const) {
    if (statuses.includes(status)) return status;
  }
  return statuses.length > 0 && statuses.every(status => status === 'completed')
    ? 'completed'
    : 'pending';
}
