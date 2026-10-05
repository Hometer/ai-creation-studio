import ProjectPreparation from '@/components/ProjectPreparation';
import '../../../../plans.css';
import '../../../../preparation.css';

export default async function PreparationPage({params}:{params:Promise<{projectId:string;planId:string}>}) {
  const {projectId,planId}=await params;
  return <ProjectPreparation key={`${projectId}:${planId}`} projectId={projectId} planId={planId}/>;
}
