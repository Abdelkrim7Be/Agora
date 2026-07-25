import { useEffect, useRef, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { Pager } from '../../components/ui/Pager';
import { useInstance } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { useDialog } from '../../contexts/DialogContext';
import { useApi } from '../../api/useApi';
import { usePager } from '../../hooks/usePager';
import {
  useContactsQuery,
  useSaveContact,
  useDeleteContact,
  useImportContacts,
  useUploadContactPhoto,
  useDeleteContactPhoto,
  useCategoriesQuery,
} from '../../api/queries';
import { compactText } from '../../utils/format';

const PAGE_SIZE = 5;
const AUDIENCES = ['employee', 'client', 'supplier', 'prospect', 'candidate', 'partner'];
const EMPTY_FORM = { email: '', name: '', audience: 'employee', gender: 'unknown', dept: '', company: '', lang: '', tags: '', active: true, category: '' };

function splitValues(value) {
  return String(value || '').split(/[\n,]/).map((item) => item.trim()).filter(Boolean);
}

function contactInitials(contact) {
  const source = String(contact?.name || contact?.email || '?').trim();
  const parts = source.split(/[\s@._-]+/).filter(Boolean);
  return (parts.length >= 2 ? parts[0][0] + parts[1][0] : source.slice(0, 2)).toUpperCase();
}

function contactAvatarColor(contact) {
  const key = String(contact?.email || contact?.name || '');
  let hash = 0;
  for (let i = 0; i < key.length; i += 1) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  return `hsl(${hash % 360} 55% 45%)`;
}

function ContactAvatar({ contact, apiBlob }) {
  const [photoUrl, setPhotoUrl] = useState('');

  useEffect(() => {
    let objectUrl = '';
    let cancelled = false;
    apiBlob(`/api/agent/contacts/${encodeURIComponent(contact.email)}/photo`).then((blob) => {
      if (cancelled || !blob) return;
      objectUrl = URL.createObjectURL(blob);
      setPhotoUrl(objectUrl);
    });
    return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [contact.email]); // eslint-disable-line react-hooks/exhaustive-deps

  if (photoUrl) return <span className="contact-avatar-initials has-photo" style={{ backgroundImage: `url(${photoUrl})` }} />;
  return <span className="contact-avatar-initials" style={{ background: contactAvatarColor(contact) }}>{contactInitials(contact)}</span>;
}

export default function ContactsPage() {
  const { hasRole } = useInstance();
  const { setStatus } = useStatus();
  const { confirmDialog } = useDialog();
  const { apiBlob } = useApi();
  const canManage = hasRole('owner');

  const [form, setForm] = useState(EMPTY_FORM);
  const [editingEmail, setEditingEmail] = useState('');
  const [hasPhoto, setHasPhoto] = useState(false);
  const [csvFile, setCsvFile] = useState(null);
  const [csvText, setCsvText] = useState('');
  const [importAudience, setImportAudience] = useState('client');
  const [categoryFilter, setCategoryFilter] = useState('');
  const fileInputRef = useRef(null);
  const pager = usePager(0);
  const announcedInitialLoad = useRef(false);
  const announcedError = useRef(null);

  const query = useContactsQuery();
  const categoriesQuery = useCategoriesQuery();
  const availableCategories = categoriesQuery.data?.parsed?.categories || [];
  const saveContact = useSaveContact();
  const deleteContact = useDeleteContact();
  const importContacts = useImportContacts();
  const uploadPhoto = useUploadContactPhoto();
  const deletePhoto = useDeleteContactPhoto();
  const contacts = query.data?.contacts || [];

  useEffect(() => {
    if (!query.data) return;
    if (!announcedInitialLoad.current) {
      announcedInitialLoad.current = true;
      setStatus('Répertoire des contacts chargé.', 'ok');
    }
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!query.error || announcedError.current === query.error.message) return;
    announcedError.current = query.error.message;
    setStatus(`Impossible de charger les contacts : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const resetForm = () => { setEditingEmail(''); setForm(EMPTY_FORM); setHasPhoto(false); };

  const startEdit = async (contact) => {
    setEditingEmail(contact.email);
    setForm({
      email: contact.email,
      name: contact.name || '',
      audience: contact.audience || 'client',
      gender: contact.fields?.gender || 'unknown',
      dept: contact.fields?.dept || '',
      company: contact.fields?.company || '',
      lang: contact.fields?.lang || '',
      tags: (contact.tags || []).join(', '),
      active: contact.active !== false,
      category: contact.category || '',
    });
    const blob = await apiBlob(`/api/agent/contacts/${encodeURIComponent(contact.email)}/photo`);
    setHasPhoto(Boolean(blob));
  };

  const draftFromForm = () => {
    const fields = {};
    if (form.gender) fields.gender = form.gender;
    if (form.dept.trim()) fields.dept = form.dept.trim();
    if (form.company.trim()) fields.company = form.company.trim();
    if (form.lang.trim()) fields.lang = form.lang.trim();
    return {
      email: form.email.trim().toLowerCase(),
      name: form.name.trim() || null,
      audience: form.audience,
      fields,
      tags: splitValues(form.tags).map((t) => t.toLowerCase()),
      active: Boolean(form.active),
      category: form.category || null,
      category_source: 'manual',
    };
  };

  const handleSave = async (e) => {
    e.preventDefault();
    const payload = draftFromForm();
    if (!payload.email || !payload.audience) return;
    try {
      await saveContact.mutateAsync({ email: editingEmail, payload });
      setStatus(editingEmail ? `Contact ${editingEmail} mis à jour.` : `Contact ${payload.email} créé.`, 'ok');
      resetForm();
    } catch (error) {
      setStatus(`Impossible d'enregistrer le contact : ${error.message}`, 'error');
    }
  };

  const handleDelete = async (email) => {
    const confirmed = await confirmDialog({
      title: 'Supprimer le contact',
      message: `${email} sera retiré du répertoire. Les segments dynamiques seront recalculés ensuite.`,
      confirmLabel: 'Supprimer',
      confirmIcon: 'delete',
      variant: 'danger',
    });
    if (!confirmed) return;
    try {
      await deleteContact.mutateAsync(email);
      if (editingEmail === email) resetForm();
      setStatus(`Contact ${email} supprimé.`, 'ok');
    } catch (error) {
      setStatus(`Impossible de supprimer le contact : ${error.message}`, 'error');
    }
  };

  const handleUploadPhoto = async (e) => {
    const file = e.target.files?.[0];
    if (!file || !editingEmail) return;
    try {
      await uploadPhoto.mutateAsync({ email: editingEmail, file });
      setStatus('Photo du contact enregistrée.', 'ok');
      setHasPhoto(true);
    } catch (error) {
      setStatus(`Impossible d'enregistrer la photo : ${error.message}`, 'error');
    } finally {
      e.target.value = '';
    }
  };

  const handleDeletePhoto = async () => {
    if (!editingEmail) return;
    try {
      await deletePhoto.mutateAsync(editingEmail);
      setStatus('Photo du contact retirée.', 'ok');
      setHasPhoto(false);
    } catch (error) {
      setStatus(`Impossible de retirer la photo : ${error.message}`, 'error');
    }
  };

  const handleImport = async () => {
    try {
      let text = csvText.trim();
      if (!text && csvFile) text = await csvFile.text();
      if (!text) return;
      const result = await importContacts.mutateAsync({ csvText: text, audienceDefault: importAudience });
      setCsvText('');
      setCsvFile(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
      setStatus(`Import CSV terminé: ${result.imported_count} importé(s), ${result.rejected_count} rejeté(s).`, result.rejected_count ? 'warn' : 'ok');
    } catch (error) {
      setStatus(`Impossible d'importer le CSV de contacts : ${error.message}`, 'error');
    }
  };

  const filteredContacts = categoryFilter ? contacts.filter((c) => c.category === categoryFilter) : contacts;
  const pageContacts = filteredContacts.slice(pager.page * PAGE_SIZE, pager.page * PAGE_SIZE + PAGE_SIZE);
  const hasMore = (pager.page + 1) * PAGE_SIZE < filteredContacts.length;

  return (
    <>
      <PageHeading view="contacts" />
      <div className="toolbar">
        <button type="button" onClick={() => query.refetch()}>
          <span className="material-symbols-outlined" aria-hidden="true">sync</span><span>Charger les contacts</span>
        </button>
        <span className="counter">{query.data?.storage || 'contacts-directory'}</span>
      </div>
      <div className="notice"><strong>Source unique :</strong> chaque personne est stockée une seule fois. Les segments et campagnes réutilisent ce répertoire.</div>
      <div className="editor-grid">
        {canManage && (
          <Card className="editor-card">
            <div className="card-header">
              <div><h2>Éditer un contact</h2><p>Formulaire simple, import CSV inclus, sans YAML côté métier.</p></div>
            </div>
            <form className="login-form" onSubmit={handleSave}>
              <label><span>E-mail</span><input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="contact@company.com" required /></label>
              <label><span>Nom</span><input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Zineb Bellagnech" /></label>
              <label><span>Audience</span>
                <select value={form.audience} onChange={(e) => setForm({ ...form, audience: e.target.value })}>
                  {AUDIENCES.map((a) => <option key={a} value={a}>{a}</option>)}
                </select>
              </label>
              <label><span>Catégorie (routage)</span>
                <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                  <option value="">Aucune</option>
                  {availableCategories.map((c) => <option key={c.name} value={c.name}>{c.display_name || c.name}</option>)}
                </select>
              </label>
              <label><span>Avatar</span>
                <select value={form.gender} onChange={(e) => setForm({ ...form, gender: e.target.value })}>
                  <option value="unknown">Auto / neutre</option>
                  <option value="male">Masculin</option>
                  <option value="female">Féminin</option>
                  <option value="neutral">Neutre</option>
                </select>
              </label>
              <label><span>Département</span><input value={form.dept} onChange={(e) => setForm({ ...form, dept: e.target.value })} placeholder="Finance" /></label>
              <label><span>Entreprise</span><input value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} placeholder="Agora" /></label>
              <label><span>Langue</span><input value={form.lang} onChange={(e) => setForm({ ...form, lang: e.target.value })} placeholder="fr" /></label>
              <label><span>Tags (virgules)</span><input value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })} placeholder="finance, approver" /></label>
              <label className="toggle-row"><input type="checkbox" checked={form.active} onChange={(e) => setForm({ ...form, active: e.target.checked })} /><span>Actif</span></label>
              {editingEmail && (
                <div className="signature-image-row">
                  <span>Photo du contact</span>
                  <input ref={fileInputRef} type="file" accept="image/png,image/jpeg" data-testid="contact-photo-file" onChange={handleUploadPhoto} />
                  {hasPhoto && <button type="button" className="ghost" onClick={handleDeletePhoto}>Retirer la photo</button>}
                </div>
              )}
              <div className="toolbar">
                <button className="primary" type="submit">
                  <span className="material-symbols-outlined" aria-hidden="true">save</span><span>Enregistrer</span>
                </button>
                <button type="button" onClick={resetForm}>
                  <span className="material-symbols-outlined" aria-hidden="true">restart_alt</span><span>Nouveau contact</span>
                </button>
              </div>
              <label><span>Importer un CSV</span><input type="file" accept=".csv,text/csv" onChange={(e) => setCsvFile(e.target.files?.[0] || null)} /></label>
              <label><span>Ou coller le CSV</span><textarea rows={5} value={csvText} onChange={(e) => setCsvText(e.target.value)} placeholder={"email,name,audience,tags\nclient@example.com,Client,client,vip"} /></label>
              <label><span>Audience par défaut à l'import</span>
                <select value={importAudience} onChange={(e) => setImportAudience(e.target.value)}>
                  {['client', 'employee', 'supplier', 'prospect', 'candidate', 'partner'].map((a) => <option key={a} value={a}>{a}</option>)}
                </select>
              </label>
              <button type="button" onClick={handleImport}>
                <span className="material-symbols-outlined" aria-hidden="true">upload_file</span><span>Importer le CSV</span>
              </button>
            </form>
          </Card>
        )}
        <Card className="editor-card">
          <div className="card-header">
            <div><h2>Contacts enregistrés</h2><p>Audience, champs métier et statut actif restent visibles sans exposer le YAML.</p></div>
            {availableCategories.length ? (
              <select value={categoryFilter} onChange={(e) => { setCategoryFilter(e.target.value); pager.reset(); }}>
                <option value="">Toutes catégories</option>
                {availableCategories.map((c) => <option key={c.name} value={c.name}>{c.display_name || c.name}</option>)}
              </select>
            ) : null}
          </div>
          <div className="rule-list">
            {!contacts.length ? <div className="empty">Aucun contact enregistré.</div> : (
              <>
                {pageContacts.map((contact) => {
                  const tags = (contact.tags || []).map((t) => `#${t}`);
                  const fields = Object.entries(contact.fields || {}).filter(([key]) => key !== 'gender').map(([key, value]) => `${key}: ${value}`);
                  return (
                    <div className={`directory-row contact-row ${contact.active === false ? 'inactive' : ''}`.trim()} key={contact.email}>
                      <ContactAvatar contact={contact} apiBlob={apiBlob} />
                      <div className="directory-main">
                        <strong>{contact.name || contact.email}</strong>
                        <span title={contact.email}>{compactText(contact.email, 42)}</span>
                        <div className="mini-chip-row">
                          <span className="mini-chip">{contact.fields?.gender ? `avatar: ${contact.fields.gender}` : 'avatar: auto'}</span>
                          <span className="mini-chip">{contact.audience}</span>
                          <span className="mini-chip">{contact.active === false ? 'inactive' : 'actif'}</span>
                          {contact.category ? (
                            <span className={`mini-chip category-chip ${contact.category_source === 'manual' ? 'manual' : 'inferred'}`}>
                              {contact.category}{contact.category_source && contact.category_source !== 'manual' ? ` (${contact.category_source})` : ''}
                            </span>
                          ) : null}
                          {fields.map((chip) => <span className="mini-chip" key={chip}>{chip}</span>)}
                          {tags.slice(0, 4).map((tag) => <span className="mini-chip" key={tag}>{tag}</span>)}
                          {tags.length > 4 && <span className="mini-chip">{`+${tags.length - 4} tag(s)`}</span>}
                        </div>
                      </div>
                      {canManage && (
                        <div className="directory-actions">
                          <button type="button" onClick={() => startEdit(contact)}>
                            <span className="material-symbols-outlined" aria-hidden="true">edit</span><span>Modifier</span>
                          </button>
                          <button className="danger" type="button" onClick={() => handleDelete(contact.email)}>
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
