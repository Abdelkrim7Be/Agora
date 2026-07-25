import { useEffect, useRef, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { useInstance } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { useApi } from '../../api/useApi';
import {
  useSignatureQuery,
  useSaveSignature,
  useUploadSignatureImage,
  useDeleteSignatureImage,
} from '../../api/queries';

const EMPTY_FORM = {
  enabled: false,
  mode: 'append_platform_signature',
  first_name: '',
  last_name: '',
  title: '',
  company: '',
  phone: '',
  website: '',
  text: '',
  image_alt: 'Signature',
  image_url: '',
};

const MODE_LABELS = {
  preserve_provider_signature: 'Conserver la signature du fournisseur (ne rien ajouter)',
  append_platform_signature: 'Ajouter la signature configurée ici',
  replace_detected_signature: 'Remplacer la signature détectée par celle configurée ici',
  ask_each_time: 'Demander à chaque brouillon',
};

function structuredLines(config) {
  const lines = [];
  const name = [config.first_name, config.last_name].filter(Boolean).join(' ');
  if (name) lines.push(name);
  const titleCompany = [config.title, config.company].filter(Boolean).join(' — ');
  if (titleCompany) lines.push(titleCompany);
  if (config.phone) lines.push(`Tél. : ${config.phone}`);
  if (config.website) lines.push(config.website);
  return lines;
}

export default function SignaturePage() {
  const { instanceId, hasRole } = useInstance();
  const { setStatus } = useStatus();
  const { apiBlob } = useApi();
  const canManage = hasRole('owner');

  const [form, setForm] = useState(EMPTY_FORM);
  const [imageObjectUrl, setImageObjectUrl] = useState('');
  const [hasImage, setHasImage] = useState(false);
  const fileInputRef = useRef(null);
  const announcedInitialLoad = useRef(false);
  const announcedError = useRef(null);

  const query = useSignatureQuery();
  const saveSignature = useSaveSignature();
  const uploadImage = useUploadSignatureImage();
  const deleteImage = useDeleteSignatureImage();

  const refreshImage = async () => {
    if (imageObjectUrl) URL.revokeObjectURL(imageObjectUrl);
    setImageObjectUrl('');
    setHasImage(false);
    const blob = await apiBlob('/api/agent/signature/image');
    if (blob) {
      setImageObjectUrl(URL.createObjectURL(blob));
      setHasImage(true);
    }
  };

  useEffect(() => {
    if (!query.data) return;
    setForm({
      enabled: Boolean(query.data.enabled),
      mode: query.data.mode || 'append_platform_signature',
      first_name: query.data.first_name || '',
      last_name: query.data.last_name || '',
      title: query.data.title || '',
      company: query.data.company || '',
      phone: query.data.phone || '',
      website: query.data.website || '',
      text: query.data.text || '',
      image_alt: query.data.image_alt || 'Signature',
      image_url: query.data.image_url || '',
    });
    refreshImage();
    if (!announcedInitialLoad.current) {
      announcedInitialLoad.current = true;
      setStatus('Signature chargée.', 'ok');
    }
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!query.error || announcedError.current === query.error.message) return;
    announcedError.current = query.error.message;
    setStatus(`Impossible de charger la signature : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => { if (imageObjectUrl) URL.revokeObjectURL(imageObjectUrl); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSave = async () => {
    try {
      await saveSignature.mutateAsync(form);
      setStatus('Signature enregistrée.', 'ok');
    } catch (error) {
      setStatus(`Impossible d’enregistrer la signature : ${error.message}`, 'error');
    }
  };

  const handleUpload = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      await uploadImage.mutateAsync(file);
      setStatus('Image de signature enregistrée.', 'ok');
      await refreshImage();
    } catch (error) {
      setStatus(`Impossible d’enregistrer l’image : ${error.message}`, 'error');
    } finally {
      event.target.value = '';
    }
  };

  const handleDeleteImage = async () => {
    try {
      await deleteImage.mutateAsync();
      setStatus('Image de signature retirée.', 'ok');
      await refreshImage();
    } catch (error) {
      setStatus(`Impossible de retirer l’image : ${error.message}`, 'error');
    }
  };

  const lines = structuredLines(form);
  const text = form.text.trim();
  const previewImageUrl = imageObjectUrl || form.image_url.trim();
  const previewLines = lines.length ? [...lines, ...(text ? [text] : [])] : (text ? [text] : []);

  return (
    <>
      <PageHeading view="signature" />
      <div className="toolbar">
        <button type="button" onClick={() => query.refetch()}>
          <span className="material-symbols-outlined" aria-hidden="true">sync</span><span>Charger la signature</span>
        </button>
        {canManage && (
          <button className="primary" type="button" onClick={handleSave}>
            <span className="material-symbols-outlined" aria-hidden="true">save</span><span>Enregistrer la signature</span>
          </button>
        )}
        <span className="counter">{instanceId}</span>
      </div>
      <div className="editor-grid signature-grid">
        <form className="card editor-card" onSubmit={(e) => e.preventDefault()}>
          <strong>Signature d’e-mail</strong>
          <label className="toggle-row">
            <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
            <span>Ajouter à chaque e-mail</span>
          </label>
          <label>
            <span>Mode</span>
            <select value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value })}>
              {(query.data?.available_modes || Object.keys(MODE_LABELS)).map((mode) => (
                <option key={mode} value={mode}>{MODE_LABELS[mode] || mode}</option>
              ))}
            </select>
          </label>
          {query.data?.detected_block ? (
            <p className="setup-step-detail">Une signature a été détectée dans vos e-mails envoyés lors de la configuration initiale.</p>
          ) : null}
          <div className="persona-grid">
            <label><span>Prénom</span><input placeholder="Karim" value={form.first_name} onChange={(e) => setForm({ ...form, first_name: e.target.value })} /></label>
            <label><span>Nom</span><input placeholder="Bellagnech" value={form.last_name} onChange={(e) => setForm({ ...form, last_name: e.target.value })} /></label>
            <label><span>Fonction</span><input placeholder="Chargé RH" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></label>
            <label><span>Entreprise</span><input placeholder="Agora" value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} /></label>
            <label><span>Téléphone</span><input placeholder="+212 6 00 00 00 00" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></label>
            <label><span>Site web</span><input type="url" placeholder="https://agora.example" value={form.website} onChange={(e) => setForm({ ...form, website: e.target.value })} /></label>
          </div>
          <label><span>Texte complémentaire (optionnel)</span><textarea rows={3} spellCheck={false} placeholder="L’humain d’abord." value={form.text} onChange={(e) => setForm({ ...form, text: e.target.value })} /></label>
          <div className="signature-image-row">
            <span>Logo / image</span>
            <input ref={fileInputRef} type="file" accept="image/png,image/jpeg" data-testid="signature-image-file" onChange={handleUpload} />
            {hasImage && <button type="button" className="ghost" onClick={handleDeleteImage}>Retirer l’image</button>}
          </div>
          <label><span>Texte alternatif de l’image</span><input type="text" placeholder="Logo Agora" value={form.image_alt} onChange={(e) => setForm({ ...form, image_alt: e.target.value })} /></label>
          <label className="signature-url-fallback"><span>Ou URL d’image externe</span><input type="url" placeholder="https://exemple.com/signature.png" value={form.image_url} onChange={(e) => setForm({ ...form, image_url: e.target.value })} /></label>
        </form>
        <Card>
          <strong>Aperçu</strong>
          {!form.enabled ? (
            <div className="signature-preview empty">Signature désactivée.</div>
          ) : !previewLines.length && !previewImageUrl ? (
            <div className="signature-preview empty">Aucune signature configurée.</div>
          ) : (
            <div className="signature-preview">
              {previewLines.length ? <div>{previewLines.map((line, i) => <span key={i}>{line}<br /></span>)}</div> : null}
              {previewImageUrl ? <img src={previewImageUrl} alt={form.image_alt || 'Signature'} /> : null}
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
