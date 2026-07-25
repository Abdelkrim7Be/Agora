import { actionRequest, actionArgs, actionArgLabel, formatEditableValue, coerceEditedValue, ACTION_ARG_HIDDEN } from '../../utils/format';

export default function ActionArgsEditor({ run, editedFields, onFieldChange }) {
  const request = actionRequest(run);
  const args = actionArgs(run);
  const fields = Object.entries(args).filter(([key]) => !ACTION_ARG_HIDDEN.has(key));

  return (
    <div className="action-preview">
      {request.action === 'forward_email' ? (
        <div className="route-preview">
          <strong>Transférer cet e-mail à {args.to || 'destinataire'}</strong>
          <span>{run.workflow_owner ? `Propriétaire : ${run.workflow_owner}` : 'Routage du workflow'}</span>
        </div>
      ) : null}
      {request.action === 'notify_internal' ? (
        <div className="route-preview">
          <strong>Notifier en interne {args.to || 'destinataire'}</strong>
          <span>{run.workflow_owner ? `Propriétaire : ${run.workflow_owner}` : 'Routage du workflow'}</span>
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
