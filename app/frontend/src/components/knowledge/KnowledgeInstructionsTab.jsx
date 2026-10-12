import { useEffect, useRef, useState } from 'react';
import { useDispatch, useSelector } from 'react-redux';
import { fetchKnowledgePage, saveKnowledgeInstructions, selectKnowledgePage } from '../../store/uiDataSlice';

export default function KnowledgeInstructionsTab({ baseId, knowledgeUrl }) {
  const dispatch = useDispatch();
  const page = useSelector((state) => selectKnowledgePage(state, baseId, 'INSTRUCTIONS.md'));
  const [content, setContent] = useState(page?.content || '');
  const lastContentRef = useRef(page?.content);
  useEffect(() => { dispatch(fetchKnowledgePage({ knowledgeUrl, baseId, path: 'INSTRUCTIONS.md' })); }, [dispatch, knowledgeUrl, baseId]);
  if (page?.content !== lastContentRef.current) {
    lastContentRef.current = page?.content;
    setContent(page?.content || '');
  }
  return <section className="knowledge-instructions-tab">
    <p>Describe the purpose and curation rules for this knowledge base.</p>
    {!page ? <p className="task-meta-sub">No instructions yet. Write them below and save — agents will read this first when curating pages.</p> : null}
    <textarea rows={18} value={content} onChange={(event) => setContent(event.target.value)} />
    <button type="button" className="repo-btn" onClick={() => dispatch(saveKnowledgeInstructions({ knowledgeUrl, baseId, content }))}>Save</button>
  </section>;
}
