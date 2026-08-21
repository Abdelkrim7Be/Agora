import { useEffect, useRef, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { TablePager } from '../../components/ui/TablePager';
import { usePagination } from '../../hooks/usePagination';
import { useInstance } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { useDialog } from '../../contexts/DialogContext';
import { useSegmentsQuery, useSaveSegment, useDeleteSegment } from '../../api/queries';
import { compactText } from '../../utils/format';

const PAGE_SIZE = 5;
const AUDIENCES = ['', 'employee', 'client', 'supplier', 'prospect', 'candidate', 'partner'];
const EMPTY_FORM = { id: '', name: '', audience: '', fieldKey: '', fieldValue: '', tags: '', members: '' };

function splitValues(value) {
  return String(value || '').split(/[\n,]/).map((item) => item.trim()).filter(Boolean);
}

export default function SegmentsPage() {
  const { hasRole } = useInstance();
  const { setStatus } = useStatus();
  const { confirmDialog } = useDialog();
  const canManage = hasRole('owner');

  const [form, setForm] = useState(EMPTY_FORM);
  const [editingId, setEditingId] = useState('');
  const announcedInitialLoad = useRef(false);
  const announcedError = useRef(null);

  const query = useSegmentsQuery();
  const saveSegment = useSaveSegment();
  const deleteSegment = useDeleteSegment();
  const segments = query.data?.segments || [];

  useEffect(() => {
    if (!query.data) return;
    if (!announcedInitialLoad.current) {
      announcedInitialLoad.current = true;
      setStatus('Segments chargés.', 'ok');
    }
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!query.error || announcedError.current === query.error.message) return;
    announcedError.current = query.error.message;
    setStatus(`Impossible de charger les segments : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const resetForm = () => { setEditingId(''); setForm(EMPTY_FORM); };

  const startEdit = (segment) => {
    setEditingId(segment.id);
    const fieldEntry = Object.entries(segment.match || {}).find(([key]) => key.startsWith('fields.'));
    setForm({
      id: segment.id,
      name: segment.name || '',
      audience: segment.match?.audience || '',
      fieldKey: fieldEntry ? fieldEntry[0].slice(7) : '',
      fieldValue: fieldEntry ? fieldEntry[1] : '',
      tags: segment.match?.tags || '',
      members: (segment.members || []).join('\n'),
    });
  };

  const draftFromForm = () => {
    const members = splitValues(form.members).map((m) => m.toLowerCase());
    const match = {};
    if (!members.length) {
      if (form.audience) match.audience = form.audience;
      const fieldKey = form.fieldKey.trim();
      const fieldValue = form.fieldValue.trim();
      if (fieldKey && fieldValue) match[`fields.${fieldKey}`] = fieldValue;
      const tags = splitValues(form.tags).map((t) => t.toLowerCase());
      if (tags.length) match.tags = tags.join(',');
    }
    return { id: form.id.trim(), name: form.name.trim(), match, members };
  };

  const handleSave = async (e) => {
    e.preventDefault();
    const payload = draftFromForm();
    if (!payload.id || !payload.name) return;
    try {
      await saveSegment.mutateAsync({ id: editingId, payload });
      setStatus(editingId ? `Segment ${editingId} mis à jour.` : `Segment ${payload.id} créé.`, 'ok');
      resetForm();
    } catch (error) {
      setStatus(`Impossible d'enregistrer le segment : ${error.message}`, 'error');
    }
  };

  const handleDelete = async (id) => {
    const confirmed = await confirmDialog({
      title: 'Supprimer le segment',
      message: `Le segment ${id} sera retiré des vues et campagnes disponibles.`,
      confirmLabel: 'Supprimer',
      confirmIcon: 'delete',
      variant: 'danger',
    });
    if (!confirmed) return;
    try {
      await deleteSegment.mutateAsync(id);
      if (editingId === id) resetForm();
      setStatus(`Segment ${id} supprimé.`, 'ok');
    } catch (error) {
      setStatus(`Impossible de supprimer le segment : ${error.message}`, 'error');
    }
  };

  const pager = usePagination(segments, PAGE_SIZE);
  const pageSegments = pager.visible;

  return (
    <>
      <PageHeading view="segments" />
      <div className="toolbar">
        <button type="button" onClick={() => query.refetch()}>
          <span className="material-symbols-outlined" aria-hidden="true">sync</span><span>Charger les segments</span>
        </button>
        <span className="counter">{query.data?.storage || 'contacts-directory'}</span>
      </div>
      <div className="notice"><strong>Segment :</strong> groupe réutilisable de contacts. Il peut être une liste fixe d e-mails ou une vue dynamique basée sur audience, tags ou champs métier. Les campagnes utilisent les segments pour choisir leurs destinataires.</div>
      <div className="editor-grid">
        {canManage && (
          <Card className="editor-card">
            <div className="card-header">
              <div><h2>Éditer un segment</h2><p>Vous pouvez cibler une audience, un champ métier, des tags, ou une liste d'emails explicite.</p></div>
            </div>
            <form className="login-form" onSubmit={handleSave}>
              <label><span>Identifiant</span><input value={form.id} onChange={(e) => setForm({ ...form, id: e.target.value })} placeholder="finance_team" required /></label>
              <label><span>Nom affiché</span><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Équipe Finance" required /></label>
              <label><span>Audience</span>
                <select value={form.audience} onChange={(e) => setForm({ ...form, audience: e.target.value })}>
                  {AUDIENCES.map((a) => <option key={a || 'none'} value={a}>{a || 'Aucune'}</option>)}
                </select>
              </label>
              <label><span>Champ métier</span><input value={form.fieldKey} onChange={(e) => setForm({ ...form, fieldKey: e.target.value })} placeholder="dept" /></label>
              <label><span>Valeur du champ</span><input value={form.fieldValue} onChange={(e) => setForm({ ...form, fieldValue: e.target.value })} placeholder="Finance" /></label>
              <label><span>Tags requis (virgules)</span><input value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} placeholder="approver, finance" /></label>
              <label className="workflow-wide"><span>Membres explicites (emails, un par ligne ou séparés par des virgules)</span><textarea rows={5} value={form.members} onChange={(e) => setForm({ ...form, members: e.target.value })} placeholder={"finance@company.com\nbackup@company.com"} /></label>
              <div className="toolbar">
                <button className="primary" type="submit">
                  <span className="material-symbols-outlined" aria-hidden="true">save</span><span>Enregistrer</span>
                </button>
                <button type="button" onClick={resetForm}>
                  <span className="material-symbols-outlined" aria-hidden="true">restart_alt</span><span>Nouveau segment</span>
                </button>
              </div>
            </form>
          </Card>
        )}
        <Card className="editor-card">
          <div className="card-header">
            <div><h2>Segments configurés</h2><p>Le compteur montre combien de contacts actifs correspondent aujourd'hui.</p></div>
          </div>
          <div className="rule-list">
            {!segments.length ? <div className="empty">Aucun segment configuré.</div> : (
              <>
                {pageSegments.map((segment) => {
                  const matchBits = Object.entries(segment.match || {}).map(([key, value]) => `${key}: ${value}`);
                  const members = segment.members || [];
                  return (
                    <div className="directory-row segment-row" key={segment.id}>
                      <div className="directory-icon"><span className="material-symbols-outlined" aria-hidden="true">group_work</span></div>
                      <div className="directory-main">
                        <strong>{segment.name || segment.id}</strong>
                        <span title={members.join(', ') || 'Vue dynamique du répertoire'}>{compactText(members.join(', ') || 'Vue dynamique du répertoire', 70)}</span>
                        <div className="mini-chip-row">
                          <span className="mini-chip">{segment.id}</span>
                          <span className="mini-chip">{`${segment.resolved_count || 0} contact(s) actif(s)`}</span>
                          {matchBits.map((chip) => <span className="mini-chip" key={chip}>{chip}</span>)}
                          {members.length > 0 && <span className="mini-chip">{`${members.length} membre(s) explicite(s)`}</span>}
                        </div>
                      </div>
                      {canManage && (
                        <div className="directory-actions">
                          <button type="button" onClick={() => startEdit(segment)}>
                            <span className="material-symbols-outlined" aria-hidden="true">edit</span><span>Modifier</span>
                          </button>
                          <button className="danger" type="button" onClick={() => handleDelete(segment.id)}>
                            <span className="material-symbols-outlined" aria-hidden="true">delete</span><span>Supprimer</span>
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
                <TablePager
                  page={pager.page}
                  pageCount={pager.pageCount}
                  total={pager.total}
                  size={pager.size}
                  onPage={pager.setPage}
                  onSize={pager.setSize}
                  unit="segments"
                />
              </>
            )}
          </div>
        </Card>
      </div>
    </>
  );
}
