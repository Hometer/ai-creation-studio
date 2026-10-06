import ProjectCompositions from '@/components/ProjectCompositions';
import '../../assets.css';
import '../../media.css';
import '../../compositions.css';
export default async function ComposePage({params}:{params:Promise<{projectId:string}>}){const {projectId}=await params;return <ProjectCompositions key={projectId} projectId={projectId}/>;}
