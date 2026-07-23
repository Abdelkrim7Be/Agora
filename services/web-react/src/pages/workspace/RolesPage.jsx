import { useEffect, useRef, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { Pager } from '../../components/ui/Pager';
import { useInstance } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { useDialog } from '../../contexts/DialogContext';
import { usePager } from '../../hooks/usePager';
import { useRolesQuery, useSaveRole, useDeleteRole } from '../../api/queries';
import { compactText } from '../../utils/format';

const PAGE_SIZE = 5;
const EMPTY_FORM = { role_key: '', display_name: '', dept: '', emails: '' };

function normalizeRoleKey(value) {
  return String(value || '').trim().toLowerCase().replace(/\s+/g, ' ');
}

function parseRoleEmails(value) {
  return String(value || '').split(/[\n,]/).map((item) => item.trim().toLowerCase()).filter(Boolean);
}

export default function RolesPage() {
  const { hasRole } = useInstance();
  const { setStatus } = useStatus();
  const { confirmDialog } = useDialog();
  const canManage = hasRole('owner');

  const [form, setForm] = useState(EMPTY_FORM);
  const [editingKey, setEditingKey] = useState('');
  const pager = usePager(0);
  const announcedInitialLoad = useRef(false);
  const announcedError = useRef(null);

  const query = useRolesQuery();
  const saveRole = useSaveRole();
  const deleteRole = useDeleteRole();
  const roles = query.data?.roles || [];

  useEffect(() => {
    if (!query.data) return;
    if (!announcedInitialLoad.current) {
      announcedInitialLoad.current = true;
      setStatus('Annuaire des rôles chargé.', 'ok');
    }
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!query.error || announcedError.current === query.error.message) return;
    announcedError.current = query.error.message;
    setStatus(`Impossible de charger les rôles : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const draft = { role_key: normalizeRoleKey(form.role_key), display_name: form.display_name.trim(), dept: form.dept.trim(), emails: parseRoleEmails(form.emails) };
  const primary = draft.emails[0] || 'aucune cible';
  const extras = draft.emails.slice(1);

  const resetForm = () => { setEditingKey(''); setForm(EMPTY_FORM); };

  const startEdit = (role) => {
    setEditingKey(role.role_key);
    setForm({ role_key: role.role_key, display_name: role.display_name || '', dept: role.dept || '', emails: (role.emails || []).join('\n') });
  };

  const handleSave = async (e) => {
    e.preventDefault();
    if (!draft.role_key || !draft.display_name || !draft.emails.length) return;
    try {
      await saveRole.mutateAsync({ roleKey: editingKey, payload: draft });
      setStatus(editingKey ? 'Rôle mis à jour.' : `Rôle ${draft.role_key} créé.`, 'ok');
      resetForm();
    } catch (error) {
      setStatus(`Impossible d'enregistrer le rôle : ${error.message}`, 'error');
    }
  };

  const handleDelete = async (roleKey) => {
    const confirmed = await confirmDialog({
      title: 'Supprimer le rôle',
      message: `Le rôle ${roleKey} sera retiré de l'annuaire de routage.`,
      confirmLabel: 'Supprimer',
      confirmIcon: 'delete',
      variant: 'danger',
    });
    if (!confirmed) return;
    try {
      await deleteRole.mutateAsync(roleKey);
      if (editingKey === roleKey) resetForm();
      setStatus(`Rôle ${roleKey} supprimé.`, 'ok');
    } catch (error) {
      setStatus(`Impossible de supprimer le rôle : ${error.message}`, 'error');
    }
  };

  const pageRoles = roles.slice(pager.page * PAGE_SIZE, pager.page * PAGE_SIZE + PAGE_SIZE);
  const hasMore = (pager.page + 1) * PAGE_SIZE < roles.length;

  return (
    <>
      <PageHeading view="roles" />
      <div className="toolbar">
        <button type="button" onClick={() => query.refetch()}>
          <span className="material-symbols-outlined" aria-hidden="true">sync</span><span>Charger l'annuaire</span>
        </button>
        <span className="counter">{query.data?.storage || 'roles-directory'}</span>
      </div>
      <div className="notice"><strong>Usage :</strong> saisissez un rôle comme <code>hr</code> ou <code>finance</code> dans un workflow, et l'agent résoudra ici l'adresse principale de routage.</div>
      <div className="editor-grid">
        {canManage && (
          <Card className="editor-card">
            <div className="card-header">
              <div><h2>Éditer un rôle</h2><p>Forme courte, réversible, avec aperçu du routage avant sauvegarde.</p></div>
            </div>
            <form className="login-form" onSubmit={handleSave}>
              <label><span>Clé du rôle</span><input value={form.role_key} onChange={(e) => setForm({ ...form, role_key: e.target.value })} placeholder="finance" required /></label>
              <label><span>Nom affiché</span><input value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} placeholder="Finance" required /></label>
              <label><span>Département</span><input value={form.dept} onChange={(e) => setForm({ ...form, dept: e.target.value })} placeholder="Finance" /></label>
              <label><span>Emails (un par ligne ou séparés par des virgules)</span><textarea rows={4} value={form.emails} onChange={(e) => setForm({ ...form, emails: e.target.value })} placeholder={"finance@company.com\nbackup-finance@company.com"} required /></label>
              <div className="rules-preview">
                {!draft.role_key ? 'Aucun rôle saisi.' : (
                  <div className="route-preview">
                    <strong>Cette route enverra vers {primary}</strong>
                    <span>{draft.role_key}{draft.dept ? ` · ${draft.dept}` : ''}{extras.length ? ` · secondaires gardés: ${extras.join(', ')}` : ''}</span>
                  </div>
                )}
              </div>
              <div className="toolbar">
                <button className="primary" type="submit">
                  <span className="material-symbols-outlined" aria-hidden="true">save</span><span>Enregistrer</span>
                </button>
                <button type="button" onClick={resetForm}>
                  <span className="material-symbols-outlined" aria-hidden="true">restart_alt</span><span>Nouveau rôle</span>
                </button>
              </div>
            </form>
          </Card>
        )}
        <Card className="editor-card">
          <div className="card-header">
            <div><h2>Rôles configurés</h2><p>Le premier email est la cible active aujourd'hui. Les suivants restent visibles pour la future fan-out.</p></div>
          </div>
          <div className="rule-list">
            {!roles.length ? <div className="empty">Aucun rôle configuré.</div> : (
              <>
                {pageRoles.map((role) => {
                  const emails = role.emails || [];
                  return (
                    <div className="directory-row" key={role.role_key}>
                      <div className="directory-icon"><span className="material-symbols-outlined" aria-hidden="true">route</span></div>
                      <div className="directory-main">
                        <strong>{role.display_name || role.role_key}</strong>
                        <span title={emails[0] || 'Aucune cible active'}>{compactText(emails[0] || 'Aucune cible active', 42)}</span>
                        <div className="mini-chip-row">
                          <span className="mini-chip">{role.role_key}</span>
                          {role.dept && <span className="mini-chip">{`dept: ${role.dept}`}</span>}
                          <span className="mini-chip">{`${emails.length} cible(s)`}</span>
                          {emails.slice(0, 2).map((email) => <span className="mini-chip" key={email}>{email}</span>)}
                          {emails.length > 2 && <span className="mini-chip">{`+${emails.length - 2} autre(s)`}</span>}
                        </div>
                      </div>
                      {canManage && (
                        <div className="directory-actions">
                          <button type="button" onClick={() => startEdit(role)}>
                            <span className="material-symbols-outlined" aria-hidden="true">edit</span><span>Modifier</span>
                          </button>
                          <button className="danger" type="button" onClick={() => handleDelete(role.role_key)}>
                            <span className="material-symbols-outlined" aria-hidden="true">delete</span><span>Supprimer</span>
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
                <Pager page={pager.page} hasMore={hasMore} onPrev={pager.prev} onNext={pager.next} />
              </>
            )}
          </div>
        </Card>
      </div>
    </>
  );
}
