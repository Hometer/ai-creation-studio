import ProjectRevisions from '@/components/ProjectRevisions';
import '../../revisions.css';

export default async function RevisionsPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  return <ProjectRevisions key={projectId} projectId={projectId} />;
}
