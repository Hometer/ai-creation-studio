import ProjectImpactReviews from '@/components/ProjectImpactReviews';
import '../../revisions.css';
import '../../impact-reviews.css';
export default async function ReviewsPage({params}:{params:Promise<{projectId:string}>}) {
 const {projectId}=await params;
 return <ProjectImpactReviews key={projectId} projectId={projectId}/>;
}
