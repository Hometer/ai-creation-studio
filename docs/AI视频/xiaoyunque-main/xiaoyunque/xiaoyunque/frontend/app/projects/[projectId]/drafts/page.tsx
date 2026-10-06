import ProjectInitialDrafts from '@/components/ProjectInitialDrafts';
import '../../assets.css';
import '../../media.css';
import '../../initial.css';
export default async function DraftsPage({params}:{params:Promise<{projectId:string}>}){const {projectId}=await params;return <ProjectInitialDrafts key={projectId} projectId={projectId}/>;}
