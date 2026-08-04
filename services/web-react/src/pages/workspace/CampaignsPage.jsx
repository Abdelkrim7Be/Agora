import { useEffect, useMemo, useRef, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { Pager } from '../../components/ui/Pager';
import { useStatus } from '../../contexts/StatusContext';
import { useDialog } from '../../contexts/DialogContext';
import { usePager } from '../../hooks/usePager';
import {
  useSegmentsQuery,
  useCampaignTemplatesQuery,
  useSaveCampaignTemplate,
  useDeleteCampaignTemplate,
  usePendingCampaignsQuery,
  useCampaignPreviewQuery,
  usePrepareCampaign,
  useApproveCampaign,
  useRejectCampaign,
} from '../../api/queries';
import { statusLabelFr } from '../../utils/format';
import { splitDirectoryValues } from '../../utils/workflowYaml';

const SEGMENTS_PAGE_SIZE = 5;
const TEMPLATES_PAGE_SIZE = 4;
const EMPTY_TEMPLATE_FORM = { name: '', category: '', audience: '', variables: '', subject: '', body: '' };

function campaignAudienceList(value) {
  return String(value || '').split(/[,\n]/).map((item) => item.trim().toLowerCase()).filter(Boolean);
}

function campaignSegmentAudiences(segment) {
  const explicit = segment?.match?.audience ? [String(segment.match.audience).toLowerCase()] : [];
  const resolved = (segment?.resolved_members || []).map((member) => String(member.audience || '').toLowerCase()).filter(Boolean);
  return [...new Set([...resolved, ...explicit])];
}

function campaignAudienceLabel(audiences) {
  const values = [...new Set((audiences || []).filter(Boolean))];
  return values.length ? values.join(', ') : 'non définie';
}

function campaignTemplateMatchesSegment(template, segment) {
  const allowed = template?.audience || [];
  const target = campaignSegmentAudiences(segment);
  return !allowed.length || !target.length || allowed.some((item) => target.includes(item));
}

function campaignStatusClass(status) {
  if (['sent'].includes(status)) return 'ok';
  if (['failed', 'cancelled'].includes(status)) return 'error';
  if (['draft', 'scheduled', 'sending', 'pending_approval'].includes(status)) return 'warn';
  return 'muted';
}

export default function CampaignsPage() {
  const { setStatus } = useStatus();
  const { confirmDialog } = useDialog();

  const [segmentId, setSegmentId] = useState('');
  const [templateName, setTemplateName] = useState('');
  const [campaignName, setCampaignName] = useState('');
  const [subjectOverride, setSubjectOverride] = useState('');
  const [bodyOverride, setBodyOverride] = useState('');
  const [scheduledAt, setScheduledAt] = useState('');
  const [templateForm, setTemplateForm] = useState(EMPTY_TEMPLATE_FORM);
  const segmentsPager = usePager(0);
  const templatesPager = usePager(0);
  const announcedInitialLoad = useRef(false);
  const announcedError = useRef(null);

  const segmentsQuery = useSegmentsQuery();
  const templatesQuery = useCampaignTemplatesQuery();
  const pendingQuery = usePendingCampaignsQuery();
  const previewQuery = useCampaignPreviewQuery(segmentId, templateName, { subject: subjectOverride, bodyMarkdown: bodyOverride });
  const saveTemplate = useSaveCampaignTemplate();
  const deleteTemplate = useDeleteCampaignTemplate();
  const prepareCampaign = usePrepareCampaign();
  const approveCampaign = useApproveCampaign();
  const rejectCampaign = useRejectCampaign();

  const segments = useMemo(() => segmentsQuery.data?.segments || [], [segmentsQuery.data]);
  const templates = useMemo(() => templatesQuery.data?.templates || [], [templatesQuery.data]);
  const campaigns = pendingQuery.data?.campaigns || [];
  const preview = previewQuery.data || null;

  const loaded = segmentsQuery.data && templatesQuery.data && pendingQuery.data;
  useEffect(() => {
    if (!loaded) return;
    if (!announcedInitialLoad.current) {
      announcedInitialLoad.current = true;
      setStatus('Campagnes chargées.', 'ok');
    }
  }, [loaded]); // eslint-disable-line react-hooks/exhaustive-deps

  const firstError = segmentsQuery.error || templatesQuery.error || pendingQuery.error;
  useEffect(() => {
    if (!firstError || announcedError.current === firstError.message) return;
    announcedError.current = firstError.message;
    setStatus(`Impossible de charger les campagnes : ${firstError.message}`, 'error');
  }, [firstError]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!segmentId && segments.length) setSegmentId(segments[0].id);
  }, [segments, segmentId]);

  const referenceSegment = segments.find((s) => s.id === segmentId) || segments[0] || null;
  const filteredTemplates = useMemo(
    () => templates.filter((t) => campaignTemplateMatchesSegment(t, referenceSegment)),
    [templates, referenceSegment],
  );

  useEffect(() => {
    if (!filteredTemplates.some((t) => t.name === templateName)) {
      setTemplateName(filteredTemplates[0]?.name || '');
    }
  }, [filteredTemplates]); // eslint-disable-line react-hooks/exhaustive-deps

  const missing = preview?.missing_variables || [];
  const recipients = Number(preview?.recipient_count || 0);
  const preparation = !segmentId
    ? { ready: false, reason: 'Choisissez un segment cible avant de mettre la campagne en file.' }
    : !templateName
      ? { ready: false, reason: 'Choisissez un modèle compatible avant de mettre la campagne en file.' }
      : !preview
        ? { ready: false, reason: 'Attendez une prévisualisation valide avant de mettre la campagne en file.' }
        : preview.audience_match === false
          ? { ready: false, reason: preview.guard_message || "L'audience du modèle ne correspond pas au segment." }
          : missing.length
            ? { ready: false, reason: 'Complétez les variables manquantes avant de mettre la campagne en file.' }
            : recipients <= 0
              ? { ready: false, reason: 'Le segment sélectionné ne contient aucun destinataire.' }
              : { ready: true, reason: 'Mettre cette campagne en file pour approbation' };

  const handlePrepare = async (e) => {
    e.preventDefault();
    const saveAsDraft = e.nativeEvent?.submitter?.value === 'draft';
    if (!saveAsDraft && !preparation.ready) {
      setStatus(preparation.reason, 'error');
      return;
    }
    try {
      const result = await prepareCampaign.mutateAsync({
        segmentId,
        templateName,
        name: campaignName,
        subject: subjectOverride,
        bodyMarkdown: bodyOverride,
        scheduledAt,
        saveAsDraft,
      });
      const message = result.status === 'draft'
        ? `Campagne ${result.campaign_name} enregistrée en brouillon.`
        : result.scheduled_at
          ? `Campagne ${result.campaign_name} prête à être programmée après validation.`
          : `Campagne ${result.campaign_name} mise en attente.`;
      setStatus(message, 'ok');
    } catch (error) {
      setStatus(`Impossible de mettre la campagne en file : ${error.message}`, 'error');
    }
  };

  const handleSaveTemplate = async (e) => {
    e.preventDefault();
    const payload = {
      name: templateForm.name.trim(),
      category: templateForm.category.trim() || null,
      audience: campaignAudienceList(templateForm.audience),
      variables: splitDirectoryValues(templateForm.variables),
      subject: templateForm.subject.trim(),
      body_markdown: templateForm.body.trim(),
    };
    if (!payload.name || !payload.subject || !payload.body_markdown || !payload.audience.length) return;
    try {
      await saveTemplate.mutateAsync(payload);
      setTemplateForm(EMPTY_TEMPLATE_FORM);
      setStatus(`Modèle ${payload.name} enregistré.`, 'ok');
    } catch (error) {
      setStatus(`Impossible d'enregistrer le modèle de campagne : ${error.message}`, 'error');
    }
  };

  const handleDeleteTemplate = async (name) => {
    const confirmed = await confirmDialog({
      title: 'Supprimer le modèle',
      message: `Supprimer le modèle "${name}" ?`,
      confirmLabel: 'Supprimer',
      confirmIcon: 'delete',
      variant: 'danger',
    });
    if (!confirmed) return;
    try {
      await deleteTemplate.mutateAsync(name);
      setStatus(`Modèle ${name} supprimé.`, 'ok');
    } catch (error) {
      setStatus(`Impossible de supprimer le modèle de campagne : ${error.message}`, 'error');
    }
  };

  const handleApprove = async (campaignId) => {
    try {
      const result = await approveCampaign.mutateAsync(campaignId);
      const message = result.status === 'scheduled'
        ? 'Campagne approuvée et programmée.'
        : result.status === 'sent'
          ? 'Campagne approuvée et envoyée.'
          : 'Campagne approuvée.';
      setStatus(message, 'ok');
    } catch (error) {
      setStatus(`Impossible d'approuver la campagne : ${error.message}`, 'error');
    }
  };

  const handleReject = async (campaignId) => {
    try {
      await rejectCampaign.mutateAsync(campaignId);
      setStatus('Campagne annulée.', 'ok');
    } catch (error) {
      setStatus(`Impossible de rejeter la campagne : ${error.message}`, 'error');
    }
  };

  const pageSegments = segments.slice(segmentsPager.page * SEGMENTS_PAGE_SIZE, segmentsPager.page * SEGMENTS_PAGE_SIZE + SEGMENTS_PAGE_SIZE);
  const segmentsHasMore = (segmentsPager.page + 1) * SEGMENTS_PAGE_SIZE < segments.length;
  const pageTemplates = templates.slice(templatesPager.page * TEMPLATES_PAGE_SIZE, templatesPager.page * TEMPLATES_PAGE_SIZE + TEMPLATES_PAGE_SIZE);
  const templatesHasMore = (templatesPager.page + 1) * TEMPLATES_PAGE_SIZE < templates.length;

  const previewWarnings = missing.map((item) => `${item.email} (${(item.unresolved || []).join(', ')})`).join(' · ');

  return (
    <>
      <PageHeading view="campaigns" />
      <div className="notice"><strong>Campagne :</strong> envoi sortant groupé. Vous choisissez un segment de contacts, un modèle, une date éventuelle, puis la campagne attend une validation humaine avant envoi.</div>
      <div className="campaigns-grid">
        <Card>
          <div className="card-header">
            <div><h2>Nouvelle campagne</h2><p>Ciblez un segment, voyez l'aperçu en direct, puis mettez l'envoi en file d'approbation.</p></div>
            <button type="button" onClick={() => { segmentsQuery.refetch(); templatesQuery.refetch(); pendingQuery.refetch(); }}>
              <span className="material-symbols-outlined" aria-hidden="true">refresh</span><span>Actualiser</span>
            </button>
          </div>
          <form className="login-form" onSubmit={handlePrepare}>
            <label><span>Segment cible</span>
              <select value={segmentId} onChange={(e) => setSegmentId(e.target.value)} required>
                {!segments.length && <option value="">Aucun segment</option>}
                {segments.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.resolved_count || 0})</option>)}
              </select>
            </label>
            <label><span>Modèle compatible</span>
              <select value={templateName} onChange={(e) => setTemplateName(e.target.value)} required>
                {!filteredTemplates.length && <option value="">Aucun modèle compatible</option>}
                {filteredTemplates.map((t) => <option key={t.name} value={t.name}>{t.name} · {campaignAudienceLabel(t.audience)}</option>)}
              </select>
            </label>
            <label><span>Nom de campagne</span><input value={campaignName} onChange={(e) => setCampaignName(e.target.value)} placeholder="Annonce clients août" /></label>
            <label><span>Objet personnalisé</span><input value={subjectOverride} onChange={(e) => setSubjectOverride(e.target.value)} placeholder="Utilise l’objet du modèle si vide" /></label>
            <label><span>Corps personnalisé (Markdown)</span><textarea rows={6} value={bodyOverride} onChange={(e) => setBodyOverride(e.target.value)} placeholder="Utilise le corps du modèle si vide" /></label>
            <label><span>Planifier le</span><input type="datetime-local" value={scheduledAt} onChange={(e) => setScheduledAt(e.target.value)} /></label>
            <button className="primary" type="submit" value="prepare" disabled={!preparation.ready} title={preparation.reason}>
              <span className="material-symbols-outlined" aria-hidden="true">playlist_add_check</span>
              <span>Préparer pour validation</span>
            </button>
            <button type="submit" value="draft" disabled={!segmentId || !templateName}>
              <span className="material-symbols-outlined" aria-hidden="true">draft</span>
              <span>Enregistrer brouillon</span>
            </button>
          </form>
          <p className="hint">Le modèle est filtré selon l'audience du segment. Une variable manquante ou une audience incompatible bloque la mise en file.</p>
          {previewQuery.isError ? (
            <div className="campaign-preview error">
              <strong>Prévisualisation indisponible</strong>
              <p>{previewQuery.error.message}</p>
            </div>
          ) : !preview ? (
            <div className="campaign-preview empty">Choisissez un segment et un modèle pour afficher l'aperçu.</div>
          ) : (
            <div className={`campaign-preview ${preview.audience_match ? 'ok' : 'warn'}`}>
              <div className="campaign-preview-meta">
                <span className={`status-pill ${preview.audience_match ? 'ok' : 'warn'}`}>{preview.audience_match ? 'Audience compatible' : 'Audience bloquée'}</span>
                <span>{preview.recipient_count || 0} destinataire(s)</span>
                <span>{preview.template_category || 'sans catégorie'}</span>
              </div>
              {preview.guard_message && <p className="campaign-warning">{preview.guard_message}</p>}
              {missing.length > 0 && <p className="campaign-warning">Variables manquantes : {previewWarnings}</p>}
              <div className="campaign-preview-email">
                <strong>{preview.preview?.subject || 'Aucun aperçu'}</strong>
                <div className="campaign-preview-html" dangerouslySetInnerHTML={{ __html: preview.preview?.html || '<p class="muted">Aucun rendu disponible.</p>' }} />
              </div>
            </div>
          )}
        </Card>

        <Card>
          <div className="card-header">
            <div><h2>Segments disponibles</h2><p>Les campagnes réutilisent les segments du répertoire de contacts.</p></div>
          </div>
          <div className="campaign-list">
            {!segments.length ? <div className="empty">Aucun segment disponible. Créez-en un dans la vue Segments.</div> : (
              <>
                {pageSegments.map((segment) => (
                  <div className="directory-row compact" key={segment.id}>
                    <div className="directory-icon"><span className="material-symbols-outlined" aria-hidden="true">groups</span></div>
                    <div className="directory-main">
                      <strong>{segment.name || segment.id}</strong>
                      <span>Segment réutilisable pour les campagnes</span>
                      <div className="mini-chip-row">
                        <span className="mini-chip">{segment.id}</span>
                        <span className="mini-chip">{`${segment.resolved_count || 0} destinataire(s)`}</span>
                        <span className="mini-chip">{`Audience : ${campaignAudienceLabel(campaignSegmentAudiences(segment))}`}</span>
                      </div>
                    </div>
                  </div>
                ))}
                <Pager page={segmentsPager.page} hasMore={segmentsHasMore} onPrev={segmentsPager.prev} onNext={segmentsPager.next} />
              </>
            )}
          </div>
        </Card>

        <Card>
          <div className="card-header">
            <div><h2>Modèles de campagne</h2><p>Chaque modèle déclare son audience et sa catégorie avant toute diffusion.</p></div>
          </div>
          <div className="campaign-list">
            {!templates.length ? <div className="empty">Aucun modèle enregistré.</div> : (
              <>
                {pageTemplates.map((template) => (
                  <div className="directory-row compact" key={template.name}>
                    <div className="directory-icon"><span className="material-symbols-outlined" aria-hidden="true">campaign</span></div>
                    <div className="directory-main">
                      <strong>{template.name}</strong>
                      <span>{template.subject || 'Modèle sans objet'}</span>
                      <div className="mini-chip-row">
                        <span className="mini-chip">{template.category || 'sans catégorie'}</span>
                        <span className="mini-chip">{`Audience : ${campaignAudienceLabel(template.audience)}`}</span>
                        {template.variables?.length > 0 && <span className="mini-chip">{`Variables : ${template.variables.join(', ')}`}</span>}
                      </div>
                    </div>
                    <div className="directory-actions">
                      <button className="danger" type="button" onClick={() => handleDeleteTemplate(template.name)}>
                        <span className="material-symbols-outlined" aria-hidden="true">delete</span><span>Supprimer</span>
                      </button>
                    </div>
                  </div>
                ))}
                <Pager page={templatesPager.page} hasMore={templatesHasMore} onPrev={templatesPager.prev} onNext={templatesPager.next} />
              </>
            )}
          </div>
          <details className="campaign-editor">
            <summary>Ajouter un modèle</summary>
            <form className="login-form" onSubmit={handleSaveTemplate}>
              <label><span>Nom</span><input value={templateForm.name} onChange={(e) => setTemplateForm({ ...templateForm, name: e.target.value })} placeholder="newsletter_clients" required /></label>
              <label><span>Catégorie</span><input value={templateForm.category} onChange={(e) => setTemplateForm({ ...templateForm, category: e.target.value })} placeholder="newsletter" /></label>
              <label><span>Audience (séparée par des virgules)</span><input value={templateForm.audience} onChange={(e) => setTemplateForm({ ...templateForm, audience: e.target.value })} placeholder="client, partner" required /></label>
              <label><span>Variables attendues</span><input value={templateForm.variables} onChange={(e) => setTemplateForm({ ...templateForm, variables: e.target.value })} placeholder="name, company" /></label>
              <label><span>Objet</span><input value={templateForm.subject} onChange={(e) => setTemplateForm({ ...templateForm, subject: e.target.value })} placeholder="Nouvelles pour {{company}}" required /></label>
              <label><span>Corps (Markdown)</span>
                <textarea rows={7} value={templateForm.body} onChange={(e) => setTemplateForm({ ...templateForm, body: e.target.value })} placeholder={'## Bonjour {{name}}\n\nVoici les nouveautés pour {{company}}...'} required />
              </label>
              <button className="primary" type="submit"><span>Enregistrer le modèle</span></button>
            </form>
          </details>
        </Card>
      </div>

      <Card>
        <div className="card-header">
          <div><h2>Campagnes</h2><p>Vérifiez les brouillons, les programmations, les envois terminés et les erreurs par destinataire.</p></div>
        </div>
        <div className="campaign-pending">
          {!campaigns.length ? 'Aucune campagne préparée.' : campaigns.map((campaign) => {
            const campaignWarnings = (campaign.missing_variables || []).map((item) => `${item.email} (${(item.unresolved || []).join(', ')})`).join(' · ');
            const canApprove = ['draft', 'pending_approval'].includes(campaign.status) && campaign.audience_match && !(campaign.missing_variables || []).length;
            const canCancel = ['draft', 'pending_approval', 'scheduled'].includes(campaign.status);
            return (
              <article className="card campaign-pending-card" key={campaign.campaign_id}>
                <div className="card-header">
                  <div>
                    <h2>{campaign.campaign_name || campaign.template_name}</h2>
                    <div className="meta">
                      <span>{campaign.segment_name || campaign.segment_id || 'segment inconnu'}</span>
                      <span>{campaign.recipient_count || 0} destinataire(s)</span>
                      <span>{campaign.template_category || 'sans catégorie'}</span>
                      {campaign.scheduled_at ? <span>programmée : {campaign.scheduled_at}</span> : null}
                    </div>
                  </div>
                  <span className={`status-pill ${campaignStatusClass(campaign.status)}`}>{statusLabelFr(campaign.status)}</span>
                </div>
                {campaign.guard_message && <p className="campaign-warning">{campaign.guard_message}</p>}
                {campaignWarnings && <p className="campaign-warning">Variables manquantes : {campaignWarnings}</p>}
                <div className="campaign-preview-email" dangerouslySetInnerHTML={{ __html: campaign.preview?.html || '<p class="muted">Aucun aperçu.</p>' }} />
                {campaign.recipients?.length > 0 && (
                  <div className="campaign-recipient-results">
                    {campaign.recipients.slice(0, 8).map((recipient) => (
                      <span className={`mini-chip ${campaignStatusClass(recipient.status)}`} key={recipient.email}>
                        {recipient.email} · {statusLabelFr(recipient.status)}
                      </span>
                    ))}
                    {campaign.recipients.length > 8 && <span className="mini-chip">+{campaign.recipients.length - 8}</span>}
                  </div>
                )}
                {campaign.result?.failed?.length > 0 && (
                  <p className="campaign-warning">Erreurs : {campaign.result.failed.map((item) => `${item.email || 'campagne'} (${item.error})`).join(' · ')}</p>
                )}
                <div className="actions">
                  <button className="primary" type="button" disabled={!canApprove} onClick={() => handleApprove(campaign.campaign_id)}>Approuver</button>
                  <button className="danger" type="button" disabled={!canCancel} onClick={() => handleReject(campaign.campaign_id)}>Annuler</button>
                </div>
              </article>
            );
          })}
        </div>
      </Card>
    </>
  );
}
