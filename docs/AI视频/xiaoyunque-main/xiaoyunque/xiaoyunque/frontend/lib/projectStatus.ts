export type ProjectStatus = 'pending' | 'running' | 'waiting' | 'completed' | 'error' | 'stopped';

/** Only business stages count toward completion; init and missing stages do not. */
export function getProjectStatus(
  stages: Record<string, { status: string }>,
  stageOrder: readonly string[],
): ProjectStatus {
  const statuses = stageOrder.map(id => stages[id]?.status ?? 'pending');
  for (const status of ['running', 'error', 'waiting', 'stopped'] as const) {
    if (statuses.includes(status)) return status;
  }
  return statuses.length > 0 && statuses.every(status => status === 'completed')
    ? 'completed'
    : 'pending';
}
