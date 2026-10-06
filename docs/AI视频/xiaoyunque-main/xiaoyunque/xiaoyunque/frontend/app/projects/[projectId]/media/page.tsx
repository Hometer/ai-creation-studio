import ProjectMediaRevisions from '@/components/ProjectMediaRevisions';
import '../../assets.css';
import '../../media.css';
export default async function MediaPage({params}:{params:Promise<{projectId:string}>}){const {projectId}=await params;return <ProjectMediaRevisions key={projectId} projectId={projectId}/>;}
