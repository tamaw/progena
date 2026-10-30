import { useSelector } from 'react-redux';
import { selectAgentName } from '../settings/state/preferences';

import StructuredDocumentBody from '../../components/StructuredDocumentBody';
import { dispatchQuickChatOpen, QUICK_CHAT_MODE } from '../../lib/browserContracts';


function openAgent(message, instruction, { draft = false, inline = false, clientId = null } = {}) {
  const prompt = `For the inbox file /inbox/${message.id}.md: ${instruction}`;
  dispatchQuickChatOpen({
    mode: QUICK_CHAT_MODE.INBOX_EDIT, subtitle: message.title || message.id,
    [draft ? 'draft' : 'prompt']: prompt, inline, clientId,
  });
}

export default function StructuredInboxBody({ message }) {
  const agentName = useSelector(selectAgentName);
  return (
    <StructuredDocumentBody
      content={message.body || ''}
      onAskAgent={(instruction, options) => openAgent(message, instruction, options)}
      agentName={agentName}
      onAnswerQuestion={({ question, choice, content }) => {
        const block = content.slice(question.start, question.end);
        const id = question.attributes?.id;
        openAgent(message, `Read the latest /inbox/${message.id}.md and locate this exact question section${id ? ` (id ${JSON.stringify(id)})` : ''}:\n${block}\n\nThe user answered ${JSON.stringify(choice)}. Rewrite this <questions> section in your own words to include the question and its answer naturally in the document. Keep its <questions> wrapper, id, and <options> list so the user can change their answer later. Do not add an <answer> tag or simply append the selected text. Preserve all other sections and user-owned frontmatter. If this question has changed, ask before editing.`);
      }}
    />
  );
}
