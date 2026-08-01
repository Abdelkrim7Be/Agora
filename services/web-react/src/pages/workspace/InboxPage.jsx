import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageHeading } from '../../components/layout/PageHeading';
import { useInstance } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { useDialog } from '../../contexts/DialogContext';
import { useInboxQuery, useInboxAction, useCategorizeContact, useCategoriesQuery, useContactsQuery } from '../../api/queries';
import { parseSenderEmail, senderDomain } from '../../utils/format';

export default function InboxPage() {
  const { hasRole } = useInstance();
  const { setStatus } = useStatus();
  const { confirmDialog, promptDialog, selectDialog } = useDialog();
  const navigate = useNavigate();
  const canManage = hasRole('owner');
  const announcedInitialLoad = useRef(false);
  const [categoryFilter, setCategoryFilter] = useState('all');
  const [selectedIds, setSelectedIds] = useState(() => new Set());

  const query = useInboxQuery();
  const inboxAction = useInboxAction();
  const categorizeContact = useCategorizeContact();
  const categoriesQuery = useCategoriesQuery();
  const contactsQuery = useContactsQuery();
  const availableCategories = categoriesQuery.data?.parsed?.categories || [];

  const messages = query.data?.messages || [];
  const warning = query.data?.warning;

  const contacts = contactsQuery.data?.contacts || [];
  const categoryByEmail = useMemo(() => {
    const exact = new Map();
    const domains = new Map();
    contacts.forEach((contact) => {
      if (!contact.category) return;
      if (contact.email) exact.set(String(contact.email).toLowerCase(), contact.category);
      if (contact.domain) domains.set(String(contact.domain).toLowerCase(), contact.category);
    });
    return { exact, domains };
  }, [contacts]);

  const categoryForMessage = (msg) => {
    const email = parseSenderEmail(msg.from || '').toLowerCase();
    const domain = senderDomain(msg.from || '').toLowerCase();
    return categoryByEmail.exact.get(email) || categoryByEmail.domains.get(domain) || msg.category || 'uncategorized';
  };

  const folderOptions = useMemo(() => {
    const base = availableCategories.map((category) => ({
      value: category.name,
      label: category.display_name || category.name,
    }));
    return [{ value: 'all', label: 'Tous' }, ...base, { value: 'uncategorized', label: 'Non classés' }];
  }, [availableCategories]);

  const folderCounts = useMemo(() => {
    const counts = { all: messages.length, uncategorized: 0 };
    availableCategories.forEach((category) => { counts[category.name] = 0; });
    messages.forEach((msg) => {
      const category = categoryForMessage(msg);
      counts[category] = (counts[category] || 0) + 1;
    });
    return counts;
  }, [messages, availableCategories, categoryByEmail]); // eslint-disable-line react-hooks/exhaustive-deps

  const filteredMessages = useMemo(() => (
    categoryFilter === 'all' ? messages : messages.filter((msg) => categoryForMessage(msg) === categoryFilter)
  ), [messages, categoryFilter, categoryByEmail]); // eslint-disable-line react-hooks/exhaustive-deps

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

  const handleCategorize = async (msg, domainOnly) => {
    const email = parseSenderEmail(msg.from);
    if (!email) {
      setStatus('Adresse expéditeur introuvable pour cet e-mail.', 'error');
      return;
    }
    const target = domainOnly ? senderDomain(msg.from) : email;
    const title = domainOnly ? `Catégoriser le domaine ${target}` : `Catégoriser ${target}`;
    const category = availableCategories.length
      ? await selectDialog({
          title,
          message: 'Choisissez une catégorie.',
          options: availableCategories.map((c) => ({ value: c.name, label: c.display_name || c.name })),
          placeholder: 'Sélectionner une catégorie…',
          confirmLabel: 'Catégoriser',
          required: true,
        })
      : await promptDialog({
          title,
          message: 'Saisissez le nom exact de la catégorie.',
          placeholder: 'Nom de la catégorie',
          confirmLabel: 'Catégoriser',
          required: true,
        });
    if (!category) return;
    try {
      await categorizeContact.mutateAsync({ email, category, domainOnly });
      setStatus(domainOnly ? `Domaine ${target} catégorisé.` : `Expéditeur ${target} catégorisé.`, 'ok');
    } catch (error) {
      setStatus(`Impossible de catégoriser : ${error.message}`, 'error');
    }
  };

  const toggleSelected = (msgId) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(msgId)) next.delete(msgId);
      else next.add(msgId);
      return next;
    });
  };

  const allVisibleSelected = filteredMessages.length > 0
    && filteredMessages.every((msg) => selectedIds.has(msg.id));

  const toggleSelectAll = () => {
    setSelectedIds((current) => {
      if (allVisibleSelected) {
        const next = new Set(current);
        filteredMessages.forEach((msg) => next.delete(msg.id));
        return next;
      }
      const next = new Set(current);
      filteredMessages.forEach((msg) => next.add(msg.id));
      return next;
    });
  };

  // Filing a backlog one message at a time is the slow part of onboarding: after
  // the first load there are a couple of hundred messages and only a handful of
  // distinct senders. Selecting a batch and assigning one category files every
  // sender behind it at once.
  const handleBulkCategorize = async () => {
    const chosen = filteredMessages.filter((msg) => selectedIds.has(msg.id));
    if (!chosen.length) return;
    const senders = [...new Set(chosen.map((msg) => parseSenderEmail(msg.from)).filter(Boolean))];
    if (!senders.length) {
      setStatus('Aucune adresse expéditeur exploitable dans la sélection.', 'error');
      return;
    }
    const category = availableCategories.length
      ? await selectDialog({
          title: `Classer ${chosen.length} message(s)`,
          message: `${senders.length} expéditeur(s) seront classés dans cette catégorie.`,
          options: availableCategories.map((c) => ({ value: c.name, label: c.display_name || c.name })),
          placeholder: 'Sélectionner une catégorie…',
          confirmLabel: 'Classer',
          required: true,
        })
      : await promptDialog({
          title: `Classer ${chosen.length} message(s)`,
          message: 'Saisissez le nom exact de la catégorie.',
          placeholder: 'Nom de la catégorie',
          confirmLabel: 'Classer',
          required: true,
        });
    if (!category) return;

    let done = 0;
    const failed = [];
    for (const email of senders) {
      try {
        await categorizeContact.mutateAsync({ email, category, domainOnly: false });
        done += 1;
      } catch (error) {
        failed.push(email);
      }
    }
    setSelectedIds(new Set());
    if (failed.length) {
      setStatus(`${done} expéditeur(s) classés, ${failed.length} en échec : ${failed.join(', ')}`, 'error');
    } else {
      setStatus(`${done} expéditeur(s) classés dans « ${category} ».`, 'ok');
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
        <span className="counter">{filteredMessages.length}/{messages.length} message{messages.length === 1 ? '' : 's'}</span>
        {selectedIds.size > 0 && canManage ? (
          <>
            <span className="counter">{selectedIds.size} sélectionné{selectedIds.size === 1 ? '' : 's'}</span>
            <button className="primary" type="button" onClick={handleBulkCategorize}>
              <span className="material-symbols-outlined" aria-hidden="true">category</span>
              <span>Classer la sélection</span>
            </button>
            <button type="button" onClick={() => setSelectedIds(new Set())}>Annuler la sélection</button>
          </>
        ) : null}
      </div>
      <div className="folder-tabs" role="tablist" aria-label="Dossiers de catégories">
        {folderOptions.map((folder) => (
          <button
            key={folder.value}
            type="button"
            className={categoryFilter === folder.value ? 'active' : ''}
            onClick={() => setCategoryFilter(folder.value)}
          >
            <span>{folder.label}</span>
            <strong>{folderCounts[folder.value] || 0}</strong>
          </button>
        ))}
      </div>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th className="select-cell">
                <input
                  type="checkbox"
                  aria-label="Tout sélectionner"
                  checked={allVisibleSelected}
                  onChange={toggleSelectAll}
                  disabled={!filteredMessages.length}
                />
              </th>
              <th>De</th><th>Sujet</th><th>Date</th><th>Agent</th><th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {!filteredMessages.length ? (
              <tr><td colSpan={6} className="empty-cell">
                {warning ? (
                  <div className="inbox-empty-state">
                    <strong>Cette boîte n’est pas encore connectée à Gmail.</strong>
                    <button className="primary" type="button" onClick={() => navigate('../gmail')}>
                      <span className="material-symbols-outlined" aria-hidden="true">add_link</span>
                      <span>Ouvrir la synchronisation Gmail</span>
                    </button>
                  </div>
                ) : (categoryFilter === 'all' ? 'Boîte de réception vide.' : 'Aucun message dans ce dossier.')}
              </td></tr>
            ) : (
              filteredMessages.map((msg) => {
                const verdictLabel = msg.run_status === 'pending_approval'
                  ? 'Relire le brouillon'
                  : msg.run_id ? 'Détail de l’exécution' : 'aucun';
                return (
                  <tr key={msg.id} className={selectedIds.has(msg.id) ? 'row-selected' : undefined}>
                    <td className="select-cell">
                      <input
                        type="checkbox"
                        aria-label={`Sélectionner ${msg.subject || 'ce message'}`}
                        checked={selectedIds.has(msg.id)}
                        onChange={() => toggleSelected(msg.id)}
                      />
                    </td>
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
                        {canManage && (
                          <button type="button" title="Catégoriser l’expéditeur" onClick={() => handleCategorize(msg, false)}>
                            Catégoriser l’expéditeur
                          </button>
                        )}
                        {canManage && (
                          <button type="button" title="Catégoriser le domaine" onClick={() => handleCategorize(msg, true)}>
                            Catégoriser le domaine
                          </button>
                        )}
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
