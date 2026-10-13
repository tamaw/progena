import { useDispatch } from 'react-redux';
import StructuredDocumentBody from '../components/common/StructuredDocumentBody';
import { patchInboxSection } from '../store/uiDataSlice';

function openAgent(message, instruction, { draft = false, inline = false, clientId = null } = {}) {
  const prompt = `For the inbox file /inbox/${message.id}.md: ${instruction}`;
  window.dispatchEvent(new CustomEvent('progenitor:open-quick-chat', {
    detail: {
      mode: 'inbox-edit',
      subtitle: message.title || message.id,
      [draft ? 'draft' : 'prompt']: prompt,
      inline,
      clientId,
    },
  }));
}

export default function StructuredInboxBody({ message, inboxUrl }) {
  const dispatch = useDispatch();
  return (
    <StructuredDocumentBody
      content={message.body || ''}
      onSaveSection={({ expectedBody, start, end, replacement }) => dispatch(patchInboxSection({
        inboxUrl, messageId: message.id, expectedBody, start, end, replacement,
      })).unwrap()}
      onAskAgent={(instruction, options) => openAgent(message, instruction, options)}
      onAnswerQuestion={({ question, choice, content }) => {
        const block = content.slice(question.start, question.end);
        const id = question.attributes?.id;
        openAgent(message, `Read the latest /inbox/${message.id}.md and locate this exact question section${id ? ` (id ${JSON.stringify(id)})` : ''}:\n${block}\n\nThe user answered ${JSON.stringify(choice)}. Rewrite this <questions> section in your own words to include the question and its answer naturally in the document. Keep its <questions> wrapper, id, and <options> list so the user can change their answer later. Do not add an <answer> tag or simply append the selected text. Preserve all other sections and user-owned frontmatter. If this question has changed, ask before editing.`);
      }}
    />
  );
}
