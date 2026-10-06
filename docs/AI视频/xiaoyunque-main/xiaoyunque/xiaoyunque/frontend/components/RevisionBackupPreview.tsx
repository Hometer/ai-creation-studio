import {STAGES,pathKey,type Stage} from '@/lib/contentRevisions';
import {type ValueType} from '@/lib/revisionComparison';
import {type RestoreBackupPreview} from '@/lib/revisionImportPreview';
const kinds:Record<ValueType,string>={text:'文字',number:'数字',boolean:'是／否',object:'对象结构',array:'列表结构',null:'空值',missing:'无此字段'};
export default function RevisionBackupPreview({preview}:{preview:RestoreBackupPreview}){
  return <section className="revision-backup-preview" aria-label="备份恢复差异" role="status"><h3>恢复预览 · 尚未写入</h3>
    <p>{preview.total_changes?`两阶段合计 ${preview.total_changes} 处差异。左侧为项目当前正式正文，右侧为备份中的拟恢复文字。`:'两阶段当前内容与备份相同，无需恢复。'}</p>
    <p className="revision-muted">只比较备份的当前剧本与分镜，不导入历史、共创或媒体，也不会保存本页编辑草稿。摘要不能证明来源可信；实际恢复与确认将在后续提供。</p>
    {(Object.keys(STAGES) as Stage[]).map(stage=>{const s=preview.stages[stage];return <section key={stage} className="revision-backup-preview-stage"><h4>{STAGES[stage]} · {s.changes.length} 处差异</h4><p>当前 {s.before_field_count} 个文字字段 → 备份 {s.after_field_count} 个文字字段</p>
      {!s.changes.length?<p className="revision-empty">本阶段内容相同，无需恢复。</p>:s.changes.map(c=><details className="revision-change revision-comparison-change" key={pathKey(c.path)} open={s.changes.length<=6}><summary>{c.label} · {kinds[c.before_type]} → {kinds[c.after_type]}{!c.before_present?' · 备份新增字段':!c.after_present?' · 备份将移除字段':''}</summary><div className="revision-diff"><section><h4>项目当前正文 · {kinds[c.before_type]}</h4><pre className="revision-text">{c.before_present?(c.before||'（空白文字）'):'（无此字段）'}</pre></section><section><h4>备份拟恢复文字 · {kinds[c.after_type]}</h4><pre className="revision-text">{c.after_present?(c.after||'（空白文字）'):'（无此字段）'}</pre></section></div></details>)}
      {!!s.impacts.length&&<div className="revision-review"><h4>恢复本阶段后需人工核对的范围</h4><ul>{s.impacts.map((i,index)=><li key={index}>{i.segment_id}{i.shot_number!==null?` · 镜头 ${i.shot_number}`:''}：{i.reason}</li>)}</ul><p className="revision-muted">含当前及备份分镜的保守影响，不表示图片、视频、计划或成片已重做／审核。</p></div>}
    </section>;})}
    <p className="revision-muted">本次仅预览，无恢复写入或授权。换文件、重新检查、重新读取或刷新后需重新预览。</p>
  </section>;
}
