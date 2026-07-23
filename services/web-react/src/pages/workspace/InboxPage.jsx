import { useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageHeading } from '../../components/layout/PageHeading';
import { useInstance } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { useDialog } from '../../contexts/DialogContext';
import { useInboxQuery, useInboxAction } from '../../api/queries';

export default function InboxPage() {
  const { hasRole } = useInstance();
  const { setStatus } = useStatus();
  const { confirmDialog } = useDialog();
  const navigate = useNavigate();
  const canManage = hasRole('owner');
  const announcedInitialLoad = useRef(false);

  const query = useInboxQuery();
  const inboxAction = useInboxAction();

  const messages = query.data?.messages || [];
  const warning = query.data?.warning;

  useEffect(() => {
    if (query.data && !announcedInitialLoad.current) {
      announcedInitialLoad.current = true;
      setStatus(warning ? `Boîte indisponible : ${warning}` : (messages.length ? 'Boîte de réception chargée.' : 'Boîte de réception vide.'), warning ? 'error' : 'ok');
    }
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (query.error) setStatus(`Impossible de charger la boîte de réception : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleOpen = (msg) => {
    if (msg.run_status === 'pending_approval') {
      navigate('../validation');
    } else if (msg.run_id) {
      navigate(`../run/${msg.run_id}`);
    }
  };

  const handleAction = async (command, msgId) => {
    if (command === 'trash') {
      const confirmed = await confirmDialog({
        title: 'Mettre à la corbeille',
        message: 'Déplacer cet e-mail vers la corbeille de la boîte connectée ?',
        confirmLabel: 'Mettre à la corbeille',
        confirmIcon: 'delete',
        variant: 'danger',
      });
      if (!confirmed) return;
    }
    try {
      await inboxAction.mutateAsync({ msgId, command });
      setStatus(`Terminé : ${command}.`, 'ok');
    } catch (error) {
      setStatus(`Action sur la boîte de réception échouée : ${error.message}`, 'error');
    }
  };

  return (
    <>
      <PageHeading view="inbox" />
      <div className="toolbar">
        <button type="button" onClick={() => query.refetch()}>
          <span className="material-symbols-outlined" aria-hidden="true">refresh</span>
          <span>Actualiser</span>
        </button>
        <span className="counter">{messages.length} message{messages.length === 1 ? '' : 's'}</span>
      </div>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr><th>De</th><th>Sujet</th><th>Date</th><th>Agent</th><th>Actions</th></tr>
          </thead>
          <tbody>
            {!messages.length ? (
              <tr><td colSpan={5} className="empty-cell">
                {warning ? (
                  <div className="inbox-empty-state">
                    <strong>Cette boîte n’est pas encore connectée à Gmail.</strong>
                    <button className="primary" type="button" onClick={() => navigate('../gmail')}>
                      <span className="material-symbols-outlined" aria-hidden="true">add_link</span>
                      <span>Ouvrir la synchronisation Gmail</span>
                    </button>
                  </div>
                ) : 'Boîte de réception vide.'}
              </td></tr>
            ) : (
              messages.map((msg) => {
                const verdictLabel = msg.run_status === 'pending_approval'
                  ? 'Relire le brouillon'
                  : msg.run_id ? 'Détail de l’exécution' : 'aucun';
                return (
                  <tr key={msg.id}>
                    <td>{msg.from || 'Inconnu'}</td>
                    <td>{msg.unread ? <strong>{msg.subject || '(sans objet)'}</strong> : (msg.subject || '(sans objet)')}<div className="muted">{msg.snippet || ''}</div></td>
                    <td>{msg.date || ''}</td>
                    <td>{msg.run_id ? <button className="link-button" type="button" onClick={() => handleOpen(msg)}>{verdictLabel}</button> : <span className="muted">aucun</span>}</td>
                    <td>
                      <div className="actions">
                        {canManage && (
                          <button type="button" onClick={() => handleAction(msg.unread ? 'read' : 'unread', msg.id)}>
                            {msg.unread ? 'Marquer lu' : 'Marquer non lu'}
                          </button>
                        )}
                        {canManage && <button type="button" onClick={() => handleAction('archive', msg.id)}>Archiver</button>}
                        {canManage && <button className="danger" type="button" onClick={() => handleAction('trash', msg.id)}>Corbeille</button>}
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </>
  );
}
