import { useState } from 'react';
import { useUploadRunAttachment } from '../../api/queries';
import { FileField } from '../ui/FileField';
import { actionRequest, actionArgs, actionArgLabel, formatEditableValue, coerceEditedValue, actionRecipients, formatRecipients, ACTION_ARG_HIDDEN } from '../../utils/format';

// Attachments only make sense on the one HITL-gated tool that supports them —
// create_draft accepts include_attachments too, but it never pauses for
// approval, so this UI never has a chance to run for it.
const ATTACHMENT_CAPABLE_ACTIONS = new Set(['write_email']);

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

  const uploadAttachment = useUploadRunAttachment();
  const [staged, setStaged] = useState([]);
  const [uploadError, setUploadError] = useState('');

  const syncStagedIds = (next) => {
    onFieldChange(run.run_id, '_attachments', next.map((item) => item.attachment_id));
  };

  const handleFilePick = async (event) => {
    const files = Array.from(event.target.files || []);
    event.target.value = '';
    for (const file of files) {
      try {
        const entry = await uploadAttachment.mutateAsync({ runId: run.run_id, file });
        setStaged((prev) => {
          const next = [...prev, entry];
          syncStagedIds(next);
          return next;
        });
        setUploadError('');
      } catch (error) {
        setUploadError(error.message || 'Échec de l’envoi du fichier.');
      }
    }
  };

  const removeStaged = (attachmentId) => {
    setStaged((prev) => {
      const next = prev.filter((item) => item.attachment_id !== attachmentId);
      syncStagedIds(next);
      return next;
    });
  };

  const includeOriginalAttachments = editedFields.include_attachments !== undefined
    ? editedFields.include_attachments
    : Boolean(args.include_attachments);

  return (
    <div className="action-preview">
      {recipients.length ? (
        <label className="action-row">
          <strong>
            Destinataire
            <span
              className="material-symbols-outlined field-hint-icon"
              aria-hidden="true"
              title="Proposé par l’agent d’après l’expéditeur du message. Vous pouvez le remplacer ou en ajouter (séparés par des virgules) ; l’envoi reste soumis aux règles d’autorisation."
            >
              info
            </span>
          </strong>
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
      {ATTACHMENT_CAPABLE_ACTIONS.has(request.action) ? (
        <div className="action-row attachment-controls">
          <strong>Pièces jointes</strong>
          <label className="attachment-toggle">
            <input
              type="checkbox"
              checked={includeOriginalAttachments}
              onChange={(event) => onFieldChange(run.run_id, 'include_attachments', event.target.checked)}
            />
            Joindre les pièces jointes du message original
          </label>
          <div className="attachment-upload">
            <FileField
              multiple
              onChange={handleFilePick}
              disabled={uploadAttachment.isPending}
              label="Joindre un fichier"
              aria-label="Joindre un fichier"
            />
            {uploadAttachment.isPending ? <small className="metric-hint">Envoi en cours…</small> : null}
            {uploadError ? <small className="field-error">{uploadError}</small> : null}
          </div>
          {staged.length ? (
            <ul className="attachment-chip-list">
              {staged.map((item) => (
                <li key={item.attachment_id} className="chip">
                  <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: '14px' }}>attach_file</span>
                  <span>{item.filename}</span>
                  <button
                    type="button"
                    className="chip-remove"
                    onClick={() => removeStaged(item.attachment_id)}
                    aria-label={`Retirer ${item.filename}`}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
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
