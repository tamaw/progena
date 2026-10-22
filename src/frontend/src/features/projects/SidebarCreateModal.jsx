import { useCallback, useRef, useState } from 'react';
import Modal from '../../components/Modal';

export function SidebarCreateModal({
  mode,
  solutionName,
  onClose,
  onCreateProject,
  onCreateSolution,
}) {
  const [name, setName] = useState('');
  const [summary, setSummary] = useState('');
  const nameInputRef = useRef(null);
  const isSolution = mode === 'solution';
  const title = isSolution ? 'New solution' : `New project in ${solutionName}`;
  const placeholder = isSolution ? 'Solution name' : 'Project name';

  const handleSubmit = useCallback((event) => {
    event.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) {
      return;
    }
    if (isSolution) {
      onCreateSolution(trimmedName, summary);
    } else {
      onCreateProject(trimmedName);
    }
  }, [isSolution, name, onCreateProject, onCreateSolution, summary]);

  return (
    <Modal
      className="sidebar-modal"
      backdropClassName="sidebar-modal-backdrop"
      ariaLabel={title}
      initialFocusRef={nameInputRef}
      onClose={onClose}
    >
      <form onSubmit={handleSubmit}>
        <header className="sidebar-modal__head">
          <div>
            <span className="sidebar-modal__eyebrow">{isSolution ? 'Solution' : 'Project'}</span>
            <h3>{title}</h3>
          </div>
          <button type="button" className="sidebar-modal__close" aria-label="Close" onClick={onClose}>×</button>
        </header>
        <label className="sidebar-modal__field">
          <span>{placeholder}</span>
          <input ref={nameInputRef} value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        {isSolution ? (
          <label className="sidebar-modal__field">
            <span>Short summary</span>
            <textarea rows="3" value={summary} onChange={(event) => setSummary(event.target.value)} placeholder="What does this solution group together?" />
          </label>
        ) : null}
        <footer className="sidebar-modal__foot">
          <button type="button" className="task-btn task-btn--secondary" onClick={onClose}>Cancel</button>
          <button type="submit" className="task-btn task-btn--primary">{isSolution ? 'Create solution' : 'Create project'}</button>
        </footer>
      </form>
    </Modal>
  );
}
