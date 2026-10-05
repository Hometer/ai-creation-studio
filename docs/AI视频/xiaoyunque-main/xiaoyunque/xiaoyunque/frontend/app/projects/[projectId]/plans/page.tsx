import ProjectPlans from '@/components/ProjectPlans';
import '../../plans.css';

export default async function PlansPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  return <ProjectPlans key={projectId} projectId={projectId} />;
}
