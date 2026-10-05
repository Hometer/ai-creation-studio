import ProjectCoCreation from '@/components/ProjectCoCreation';
import '../../revisions.css';
import '../../coCreation.css';

export default async function CoCreationPage({params}:{params:Promise<{projectId:string}>}){
  const {projectId}=await params;
  return <ProjectCoCreation key={projectId} projectId={projectId}/>;
}
