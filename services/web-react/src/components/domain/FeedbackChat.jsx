import { formatDateTimeFr } from '../../utils/format';

export default function FeedbackChat({ run, messages, draft, onDraftChange, onSubmit, busy }) {
  const handleSubmit = (event) => {
    event.preventDefault();
    if (!draft.trim()) return;
    onSubmit(draft);
  };

  // Enter sends, like chat inputs elsewhere; Shift+Enter still inserts a
  // newline for multi-line feedback.
  const handleKeyDown = (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      if (!busy) handleSubmit(event);
    }
  };

  return (
    <form className="feedback-chat" aria-label="Fil de retouche du brouillon" onSubmit={handleSubmit}>
      <div className="feedback-chat-header">
        <span className="material-symbols-outlined" aria-hidden="true">forum</span>
        <div>
          <strong>Retouches du brouillon</strong>
          <span>Décrivez le changement attendu, l’agent mettra à jour le brouillon dans cette carte.</span>
        </div>
      </div>
      <div className="feedback-log" aria-live="polite">
        {messages.length ? messages.map((message) => (
          <div key={message.id} className={`feedback-message ${message.role === 'user' ? 'user' : 'agent'}${message.live ? ' live' : ''}`} aria-busy={message.live || undefined}>
            <strong>{message.role === 'user' ? 'Vous' : 'Agent'}{message.at ? <time> {formatDateTimeFr(message.at)}</time> : null}</strong>
            <span>{message.content}</span>
            {message.live ? <span className="typing-dots" aria-hidden="true"><i></i><i></i><i></i></span> : null}
          </div>
        )) : <div className="feedback-empty">Aucun retour envoyé. Écrivez ce que vous voulez modifier dans le brouillon.</div>}
      </div>
      <div className="feedback-compose">
        <textarea
          data-testid={`feedback-input-${run.run_id}`}
          rows={2}
          placeholder="Ex. Rends le ton plus direct et ajoute la signature"
          value={draft}
          onChange={(event) => onDraftChange(run.run_id, event.target.value)}
          onKeyDown={handleKeyDown}
        />
        <button className="feedback-send" type="submit" title="Envoyer le retour" disabled={busy}>
          <span className="material-symbols-outlined" aria-hidden="true">send</span>
        </button>
      </div>
    </form>
  );
}
