import ProjectGoal from '@/components/ProjectGoal';
import '../../assets.css';
import '../../media.css';
export default async function GoalPage({params}:{params:Promise<{projectId:string}>}){const {projectId}=await params;return <ProjectGoal key={projectId} projectId={projectId}/>;}
