import ProjectAssets from '@/components/ProjectAssets';
import '../../assets.css';

export default async function AssetsPage({ params }: { params: Promise<{ projectId: string }> }) {
  const { projectId } = await params;
  return <ProjectAssets key={projectId} projectId={projectId} />;
}
