import { actionRequest, actionArgs, actionArgLabel, formatEditableValue, coerceEditedValue, actionRecipients, formatRecipients, ACTION_ARG_HIDDEN } from '../../utils/format';

export default function ActionArgsEditor({ run, editedFields, onFieldChange }) {
  const request = actionRequest(run);
  const args = actionArgs(run);
  // Once the agent reports resolved recipients, any `to` still sitting in args is
  // a leftover the model emitted and the tool ignores. Rendering it as an
  // editable "Destinataire" invited someone to retarget an email and believe it
  // worked. Show the real destination read-only instead.
  const recipients = actionRecipients(run);
  const fields = Object.entries(args).filter(
    ([key]) => !ACTION_ARG_HIDDEN.has(key) && !(key === 'to' && recipients.length > 0),
  );

  return (
    <div className="action-preview">
      {recipients.length ? (
        <label className="action-row">
          <strong>Destinataire</strong>
          {/* Editable by a person, never by the model.
              The agent resolves this from the message's own sender, and the
              model has no way to write it — that is what stops an injected
              "forward this to …" from redirecting a reply. A reviewer looking
              at the draft is a different matter: they can retarget or add an
              address, and the change is re-checked by the policy engine and by
              the outbound allowlist before anything is sent. */}
          <input
            type="text"
            className="recipient-input"
            value={
              editedFields._recipients !== undefined
                ? editedFields._recipients
                : recipients.join(', ')
            }
            onChange={(event) => onFieldChange(run.run_id, '_recipients', event.target.value)}
          />
          <small>
            Proposé par l’agent d’après l’expéditeur du message. Vous pouvez le remplacer
            ou en ajouter (séparés par des virgules) ; l’envoi reste soumis aux règles
            d’autorisation.
          </small>
        </label>
      ) : null}
      {request.action === 'forward_email' ? (
        <div className="route-preview">
          <strong>Transférer cet e-mail à {formatRecipients(run)}</strong>
          <span>{run.workflow_owner ? `Propriétaire : ${run.workflow_owner}` : 'Routage du cas métier'}</span>
        </div>
      ) : null}
      {request.action === 'notify_internal' ? (
        <div className="route-preview">
          <strong>Notifier en interne {formatRecipients(run)}</strong>
          <span>{run.workflow_owner ? `Propriétaire : ${run.workflow_owner}` : 'Routage du cas métier'}</span>
        </div>
      ) : null}
      {fields.length ? fields.map(([key, value]) => (
        <label className="action-row" key={key}>
          <strong>{actionArgLabel(key)}</strong>
          <textarea
            value={editedFields[key] !== undefined ? editedFields[key] : formatEditableValue(value)}
            onChange={(event) => onFieldChange(run.run_id, key, coerceEditedValue(value, event.target.value))}
          />
        </label>
      )) : <div className="empty">Aucun argument modifiable pour cette action.</div>}
    </div>
  );
}
