import { useEffect, useState } from 'react';

// Edits the base's `instructions` brief in /config/knowledge.json. Agents see it
// at the top of the base's generated index.md.
export default function KnowledgeInstructionsTab({ instructions, onSave }) {
  const [content, setContent] = useState(instructions || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  useEffect(() => { setContent(instructions || ''); }, [instructions]);
  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave(content);
    } catch (err) {
      setError(err?.message || 'Failed to save instructions.');
    } finally {
      setSaving(false);
    }
  };
  return <section className="knowledge-instructions-tab">
    <p>Describe the purpose and curation rules for this knowledge base. Agents read this at the top of the base&apos;s index.</p>
    {!instructions ? <p className="task-meta-sub">No instructions yet. Write them below and save.</p> : null}
    <textarea rows={18} value={content} onChange={(event) => setContent(event.target.value)} />
    {error ? <p className="task-meta-sub" role="alert">{error}</p> : null}
    <button type="button" className="repo-btn" disabled={saving || content === (instructions || '')} onClick={save}>Save</button>
  </section>;
}
