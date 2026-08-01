import { useEffect, useRef, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { useInstance } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { useDialog } from '../../contexts/DialogContext';
import {
  useRulesQuery,
  useRuleSuggestionsQuery,
  useSaveRulesYaml,
  useSaveRule,
  useDeleteRule,
  useToggleRule,
  useSaveSectionConfig,
  useToggleSection,
  usePromoteSuggestion,
  useDismissSuggestion,
  useJunkQuery,
  useSaveJunk,
} from '../../api/queries';

const EMPTY_RULE = {
  name: '', enabled: true, senderContains: '', senderRegex: '', senderDomain: '', subjectContains: '', bodyContains: '', whenLabels: '',
  thenLabels: '', snoozeDays: '', respond: '', archive: false, markRead: false, notify: false,
};

const csvToList = (value) => String(value || '').split(',').map((item) => item.trim()).filter(Boolean);
const listToCsv = (list) => (Array.isArray(list) ? list.join(', ') : '');

function ruleWhenSummary(rule) {
  const w = rule.when || {};
  const parts = [];
  if (w.subject_contains?.length) parts.push(`sujet : ${w.subject_contains.join(', ')}`);
  if (w.body_contains?.length) parts.push(`corps : ${w.body_contains.join(', ')}`);
  if (w.sender_contains?.length) parts.push(`expéditeur : ${w.sender_contains.join(', ')}`);
  if (w.sender_regex?.length) parts.push(`regex expéditeur : ${w.sender_regex.join(', ')}`);
  if (w.sender_domain?.length) parts.push(`domaine : ${w.sender_domain.join(', ')}`);
  if (w.labels?.length) parts.push(`libellés : ${w.labels.join(', ')}`);
  return parts.join(' · ') || 'toujours';
}

function ruleThenSummary(rule) {
  const t = rule.then || {};
  const parts = [];
  if (t.labels?.length) parts.push(`libeller ${t.labels.join(', ')}`);
  if (t.archive) parts.push('archiver');
  if (t.mark_read) parts.push('marquer lu');
  if (t.notify) parts.push('notifier');
  if (t.snooze_days) parts.push(`veille ${t.snooze_days}j`);
  if (t.respond) parts.push('répondre');
  return parts.join(' · ') || 'aucune action';
}

function TogglePill({ label, enabled, onClick }) {
  return (
    <button className={`toggle-pill ${enabled ? 'on' : 'off'}`} type="button" onClick={onClick}>
      <span className="toggle-dot" aria-hidden="true" />
      <span>{label} : {enabled ? 'Activé' : 'Désactivé'}</span>
    </button>
  );
}

export default function RulesPage() {
  const { hasRole } = useInstance();
  const { setStatus } = useStatus();
  const { confirmDialog } = useDialog();
  const canManage = hasRole('owner');

  const [rule, setRule] = useState(EMPTY_RULE);
  const [editingName, setEditingName] = useState('');
  const [yamlText, setYamlText] = useState('');
  const [digest, setDigest] = useState({ hour: 18, statuses: '' });
  const [snooze, setSnooze] = useState({ labelPrefix: 'Snoozed', maxResurface: 20 });
  const [followUps, setFollowUps] = useState({ label: 'Awaiting Reply', afterDays: 3, maxResults: 10, nudge: 'Just following up on this.' });
  const [junk, setJunk] = useState({
    enabled: true, allowedSenders: '', allowedDomains: '', blockedSenders: '', blockedDomains: '',
    gmailCategories: true, bulkHeaders: true, senderHeuristics: true,
  });
  const announcedInitialLoad = useRef(false);
  const announcedError = useRef(null);

  const query = useRulesQuery();
  const junkQuery = useJunkQuery();
  const suggestionsQuery = useRuleSuggestionsQuery();
  const saveYaml = useSaveRulesYaml();
  const saveRule = useSaveRule();
  const deleteRule = useDeleteRule();
  const toggleRule = useToggleRule();
  const saveSectionConfig = useSaveSectionConfig();
  const toggleSection = useToggleSection();
  const promoteSuggestion = usePromoteSuggestion();
  const dismissSuggestion = useDismissSuggestion();
  const saveJunk = useSaveJunk();

  const parsed = query.data?.parsed;
  const rules = parsed?.rules || [];
  const suggestions = suggestionsQuery.data?.suggestions || [];

  useEffect(() => {
    if (!query.data) return;
    setYamlText(query.data.rules_yaml || '');
    const d = query.data.parsed?.digest || {};
    setDigest({ hour: d.hour ?? 18, statuses: listToCsv(d.statuses) });
    const s = query.data.parsed?.snooze || {};
    setSnooze({ labelPrefix: s.label_prefix ?? 'Snoozed', maxResurface: s.max_resurface_per_run ?? 20 });
    const f = query.data.parsed?.follow_ups || {};
    setFollowUps({ label: f.label ?? 'Awaiting Reply', afterDays: f.after_days ?? 3, maxResults: f.max_results ?? 10, nudge: f.nudge ?? '' });
    if (!announcedInitialLoad.current) {
      announcedInitialLoad.current = true;
      setStatus('Règles chargées.', 'ok');
    }
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!query.error || announcedError.current === query.error.message) return;
    announcedError.current = query.error.message;
    setStatus(`Impossible de charger les règles : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const cfg = junkQuery.data?.junk;
    if (!cfg) return;
    setJunk({
      enabled: cfg.enabled !== false,
      allowedSenders: listToCsv(cfg.allowed_senders),
      allowedDomains: listToCsv(cfg.allowed_domains),
      blockedSenders: listToCsv(cfg.blocked_senders),
      blockedDomains: listToCsv(cfg.blocked_domains),
      gmailCategories: cfg.gmail_categories !== false,
      bulkHeaders: cfg.bulk_headers !== false,
      senderHeuristics: cfg.sender_heuristics !== false,
    });
  }, [junkQuery.data]);

  const resetRuleForm = () => { setEditingName(''); setRule(EMPTY_RULE); };

  const startEdit = (r) => {
    const w = r.when || {};
    const t = r.then || {};
    setEditingName(r.name);
    setRule({
      name: r.name,
      enabled: r.enabled !== false,
      senderContains: listToCsv(w.sender_contains),
      senderRegex: listToCsv(w.sender_regex),
      senderDomain: listToCsv(w.sender_domain),
      subjectContains: listToCsv(w.subject_contains),
      bodyContains: listToCsv(w.body_contains),
      whenLabels: listToCsv(w.labels),
      thenLabels: listToCsv(t.labels),
      snoozeDays: t.snooze_days || '',
      respond: t.respond || '',
      archive: Boolean(t.archive),
      markRead: Boolean(t.mark_read),
      notify: Boolean(t.notify),
    });
  };

  const handleSaveRule = async (e) => {
    e.preventDefault();
    const name = rule.name.trim();
    if (!name) { setStatus('Le nom de la règle est requis.', 'error'); return; }
    const payload = {
      name,
      original_name: editingName || null,
      enabled: rule.enabled,
      when: {
        sender_contains: csvToList(rule.senderContains),
        sender_regex: csvToList(rule.senderRegex),
        sender_domain: csvToList(rule.senderDomain),
        subject_contains: csvToList(rule.subjectContains),
        body_contains: csvToList(rule.bodyContains),
        labels: csvToList(rule.whenLabels),
      },
      then: {
        labels: csvToList(rule.thenLabels),
        archive: rule.archive,
        mark_read: rule.markRead,
        notify: rule.notify,
        respond: rule.respond.trim() || null,
        snooze_days: rule.snoozeDays ? Number(rule.snoozeDays) : null,
      },
    };
    try {
      await saveRule.mutateAsync(payload);
      resetRuleForm();
      setStatus(`Règle « ${name} » enregistrée.`, 'ok');
    } catch (error) {
      setStatus(`Impossible d'enregistrer la règle : ${error.message}`, 'error');
    }
  };

  const handleDeleteRule = async (name) => {
    const confirmed = await confirmDialog({
      title: 'Supprimer la règle',
      message: `Supprimer la règle « ${name} » ?`,
      confirmLabel: 'Supprimer',
      confirmIcon: 'delete',
      variant: 'danger',
    });
    if (!confirmed) return;
    try {
      await deleteRule.mutateAsync(name);
      setStatus(`Règle « ${name} » supprimée.`, 'ok');
    } catch (error) {
      setStatus(`Impossible de supprimer la règle : ${error.message}`, 'error');
    }
  };

  const handleToggleRule = async (name, enabled) => {
    try {
      await toggleRule.mutateAsync({ name, enabled });
      setStatus(`Règle « ${name} » ${enabled ? 'activée' : 'désactivée'}.`, 'ok');
    } catch (error) {
      setStatus(`Impossible de basculer la règle : ${error.message}`, 'error');
    }
  };

  const handleToggleSection = async (section, enabled) => {
    try {
      await toggleSection.mutateAsync({ section, enabled });
      setStatus(`${section.replace('_', ' ')} ${enabled ? 'activé' : 'désactivé'}.`, 'ok');
    } catch (error) {
      setStatus(`Impossible de basculer ${section} : ${error.message}`, 'error');
    }
  };

  const handleSaveSection = (section) => async (e) => {
    e.preventDefault();
    const config = section === 'digest'
      ? { hour: Number(digest.hour || 18), statuses: csvToList(digest.statuses) }
      : section === 'snooze'
        ? { label_prefix: snooze.labelPrefix.trim() || 'Snoozed', max_resurface_per_run: Number(snooze.maxResurface || 20) }
        : { label: followUps.label.trim() || 'Awaiting Reply', after_days: Number(followUps.afterDays || 3), max_results: Number(followUps.maxResults || 10), nudge: followUps.nudge.trim() || 'Just following up on this.' };
    try {
      await saveSectionConfig.mutateAsync({ section, config });
      setStatus('Sous-système enregistré.', 'ok');
    } catch (error) {
      setStatus(`Impossible d'enregistrer : ${error.message}`, 'error');
    }
  };

  const handleSaveYaml = async () => {
    try {
      await saveYaml.mutateAsync(yamlText);
      setStatus('Règles enregistrées.', 'ok');
    } catch (error) {
      setStatus(`Impossible d'enregistrer les règles : ${error.message}`, 'error');
    }
  };

  const handleSaveJunk = async (e) => {
    e.preventDefault();
    try {
      await saveJunk.mutateAsync({
        enabled: junk.enabled,
        allowed_senders: csvToList(junk.allowedSenders),
        allowed_domains: csvToList(junk.allowedDomains),
        blocked_senders: csvToList(junk.blockedSenders),
        blocked_domains: csvToList(junk.blockedDomains),
        gmail_categories: junk.gmailCategories,
        bulk_headers: junk.bulkHeaders,
        sender_heuristics: junk.senderHeuristics,
      });
      setStatus('Filtre indésirable enregistré.', 'ok');
    } catch (error) {
      setStatus(`Impossible d'enregistrer le filtre indésirable : ${error.message}`, 'error');
    }
  };

  const handlePromote = async (index) => {
    try {
      const result = await promoteSuggestion.mutateAsync(index);
      setStatus(result.kind === 'workflow' ? 'Suggestion promue en workflow.' : 'Suggestion promue en règle active.', 'ok');
    } catch (error) {
      setStatus(`Impossible de promouvoir la suggestion : ${error.message}`, 'error');
    }
  };

  const handleDismiss = async (index) => {
    try {
      await dismissSuggestion.mutateAsync(index);
      setStatus('Suggestion ignorée.', 'ok');
    } catch (error) {
      setStatus(`Impossible d'ignorer la suggestion : ${error.message}`, 'error');
    }
  };

  return (
    <>
      <PageHeading view="rules" />
      <div className="toolbar">
        <button type="button" onClick={() => query.refetch()}>
          <span className="material-symbols-outlined" aria-hidden="true">sync</span><span>Charger les règles</span>
        </button>
        <button className="primary" type="button" onClick={handleSaveYaml}>
          <span className="material-symbols-outlined" aria-hidden="true">save</span><span>Enregistrer les règles</span>
        </button>
      </div>

      {canManage && (
        <Card className="workflow-builder">
          <div className="card-header">
            <div><h2>{editingName ? `Modifier « ${editingName} »` : 'Ajouter une règle'}</h2><div className="meta">Action déterministe exécutée avant le tri IA. Aucun YAML à écrire.</div></div>
          </div>
          <form className="workflow-form" onSubmit={handleSaveRule}>
            <label><span>Nom de la règle</span><input value={rule.name} onChange={(e) => setRule({ ...rule, name: e.target.value })} placeholder="Archiver les promotions" required /></label>
            <label className="workflow-checkbox"><input type="checkbox" checked={rule.enabled} onChange={(e) => setRule({ ...rule, enabled: e.target.checked })} /><span>Activée</span></label>
            <div className="workflow-wide instructions-fieldset">
              <strong>Condition (toutes les valeurs saisies doivent correspondre)</strong>
              <label><span>Expéditeur contient</span><input value={rule.senderContains} onChange={(e) => setRule({ ...rule, senderContains: e.target.value })} placeholder="newsletter, no-reply" /></label>
              <label><span>Regex expéditeur</span><input value={rule.senderRegex} onChange={(e) => setRule({ ...rule, senderRegex: e.target.value })} placeholder=".*@news\.example\.com" /></label>
              <label><span>Domaine expéditeur</span><input value={rule.senderDomain} onChange={(e) => setRule({ ...rule, senderDomain: e.target.value })} placeholder="mailchimp.com, list.example.com" /></label>
              <label><span>Sujet contient</span><input value={rule.subjectContains} onChange={(e) => setRule({ ...rule, subjectContains: e.target.value })} placeholder="promo, soldes" /></label>
              <label><span>Corps contient</span><input value={rule.bodyContains} onChange={(e) => setRule({ ...rule, bodyContains: e.target.value })} placeholder="demande de devis, facture" /></label>
              <label><span>Libellés Gmail</span><input value={rule.whenLabels} onChange={(e) => setRule({ ...rule, whenLabels: e.target.value })} placeholder="CATEGORY_PROMOTIONS, CATEGORY_SOCIAL" /></label>
            </div>
            <div className="workflow-wide instructions-fieldset">
              <strong>Action</strong>
              <label><span>Appliquer les libellés</span><input value={rule.thenLabels} onChange={(e) => setRule({ ...rule, thenLabels: e.target.value })} placeholder="Auto/Promotions" /></label>
              <label><span>Jours de mise en veille</span><input type="number" min="1" value={rule.snoozeDays} onChange={(e) => setRule({ ...rule, snoozeDays: e.target.value })} placeholder="ex. 3" /></label>
              <label className="workflow-wide"><span>Réponse automatique (optionnel)</span><input value={rule.respond} onChange={(e) => setRule({ ...rule, respond: e.target.value })} placeholder="Message envoyé automatiquement" /></label>
              <label className="workflow-checkbox"><input type="checkbox" checked={rule.archive} onChange={(e) => setRule({ ...rule, archive: e.target.checked })} /><span>Archiver</span></label>
              <label className="workflow-checkbox"><input type="checkbox" checked={rule.markRead} onChange={(e) => setRule({ ...rule, markRead: e.target.checked })} /><span>Marquer comme lu</span></label>
              <label className="workflow-checkbox"><input type="checkbox" checked={rule.notify} onChange={(e) => setRule({ ...rule, notify: e.target.checked })} /><span>Notifier</span></label>
            </div>
            <div className="workflow-form-actions workflow-wide">
              <button className="primary" type="submit">
                <span className="material-symbols-outlined" aria-hidden="true">{editingName ? 'save' : 'add'}</span>
                <span>{editingName ? 'Enregistrer' : 'Ajouter la règle'}</span>
              </button>
              {editingName && <button className="ghost" type="button" onClick={resetRuleForm}>Annuler</button>}
            </div>
          </form>
        </Card>
      )}

      <Card className="editor-card">
        <strong>Règles configurées</strong>
        <div className="rules-preview">
          <div className="rules-toggle-row">
            {canManage && (
              <>
                <TogglePill label="Automatisation" enabled={Boolean(parsed?.enabled)} onClick={() => handleToggleSection('automation', !parsed?.enabled)} />
                <TogglePill label={`Digest ${parsed?.digest?.hour ?? ''}:00`} enabled={Boolean(parsed?.digest?.enabled)} onClick={() => handleToggleSection('digest', !parsed?.digest?.enabled)} />
                <TogglePill label="Mise en veille" enabled={Boolean(parsed?.snooze?.enabled)} onClick={() => handleToggleSection('snooze', !parsed?.snooze?.enabled)} />
                <TogglePill label="Relances" enabled={Boolean(parsed?.follow_ups?.enabled)} onClick={() => handleToggleSection('follow_ups', !parsed?.follow_ups?.enabled)} />
                <TogglePill label="Apprentissage" enabled={Boolean(parsed?.learning?.enabled)} onClick={() => handleToggleSection('learning', !parsed?.learning?.enabled)} />
              </>
            )}
          </div>
          <div className="rule-list">
            {!rules.length ? <div className="empty">Aucune règle configurée.</div> : rules.map((r) => (
              <div className="rule-row" key={r.name}>
                <div>
                  <strong>{r.name}</strong>
                  <span>{ruleWhenSummary(r)} → {ruleThenSummary(r)}</span>
                </div>
                <div className="rule-row-actions">
                  <button
                    type="button"
                    className={`status-pill ${r.enabled ? 'ok' : 'error'}`}
                    onClick={() => handleToggleRule(r.name, !r.enabled)}
                  >
                    {r.enabled ? 'Activée' : 'Désactivée'}
                  </button>
                  {canManage && (
                    <div className="directory-actions">
                      <button type="button" onClick={() => startEdit(r)}>
                        <span className="material-symbols-outlined" aria-hidden="true">edit</span><span>Modifier</span>
                      </button>
                      <button className="danger" type="button" onClick={() => handleDeleteRule(r.name)}>
                        <span className="material-symbols-outlined" aria-hidden="true">delete</span><span>Supprimer</span>
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      </Card>

      {canManage && (
        <Card className="editor-card">
          <div className="card-header">
            <div><h2>Filtre indésirable</h2><p>Blocage déterministe avant tri IA pour les newsletters, promos et expéditeurs explicitement exclus.</p></div>
          </div>
          <form className="workflow-form" onSubmit={handleSaveJunk}>
            <label className="workflow-checkbox"><input type="checkbox" checked={junk.enabled} onChange={(e) => setJunk({ ...junk, enabled: e.target.checked })} /><span>Filtre actif</span></label>
            <label className="workflow-checkbox"><input type="checkbox" checked={junk.gmailCategories} onChange={(e) => setJunk({ ...junk, gmailCategories: e.target.checked })} /><span>Catégories Gmail</span></label>
            <label className="workflow-checkbox"><input type="checkbox" checked={junk.bulkHeaders} onChange={(e) => setJunk({ ...junk, bulkHeaders: e.target.checked })} /><span>En-têtes bulk</span></label>
            <label className="workflow-checkbox"><input type="checkbox" checked={junk.senderHeuristics} onChange={(e) => setJunk({ ...junk, senderHeuristics: e.target.checked })} /><span>Expéditeurs marketing</span></label>
            <label><span>Domaines bloqués</span><input value={junk.blockedDomains} onChange={(e) => setJunk({ ...junk, blockedDomains: e.target.value })} placeholder="temu.com, bershka.com" /></label>
            <label><span>Expéditeurs bloqués</span><input value={junk.blockedSenders} onChange={(e) => setJunk({ ...junk, blockedSenders: e.target.value })} placeholder="promo@example.com" /></label>
            <label><span>Domaines autorisés</span><input value={junk.allowedDomains} onChange={(e) => setJunk({ ...junk, allowedDomains: e.target.value })} placeholder="client-important.com" /></label>
            <label><span>Expéditeurs autorisés</span><input value={junk.allowedSenders} onChange={(e) => setJunk({ ...junk, allowedSenders: e.target.value })} placeholder="contact@client-important.com" /></label>
            <div className="workflow-form-actions workflow-wide">
              <button type="submit" disabled={saveJunk.isPending}>
                <span className="material-symbols-outlined" aria-hidden="true">save</span><span>Enregistrer le filtre</span>
              </button>
            </div>
          </form>
        </Card>
      )}

      {canManage && (
        <Card className="editor-card">
          <strong>Sous-systèmes</strong>
          <p className="muted">Réglez le digest, la mise en veille et les relances sans toucher au YAML. Utilisez les interrupteurs ci-dessus pour activer chaque sous-système.</p>
          <div className="section-config-grid">
            <form className="workflow-form" onSubmit={handleSaveSection('digest')}>
              <div className="workflow-wide"><strong>Digest quotidien</strong></div>
              <label><span>Heure (0-23)</span><input type="number" min="0" max="23" value={digest.hour} onChange={(e) => setDigest({ ...digest, hour: e.target.value })} /></label>
              <label><span>Statuts inclus</span><input value={digest.statuses} onChange={(e) => setDigest({ ...digest, statuses: e.target.value })} placeholder="notify, pending_approval" /></label>
              <div className="workflow-form-actions workflow-wide"><button type="submit"><span className="material-symbols-outlined" aria-hidden="true">save</span><span>Enregistrer le digest</span></button></div>
            </form>
            <form className="workflow-form" onSubmit={handleSaveSection('snooze')}>
              <div className="workflow-wide"><strong>Mise en veille</strong></div>
              <label><span>Préfixe de libellé</span><input value={snooze.labelPrefix} onChange={(e) => setSnooze({ ...snooze, labelPrefix: e.target.value })} placeholder="Snoozed" /></label>
              <label><span>Réémergences max / exécution</span><input type="number" min="1" value={snooze.maxResurface} onChange={(e) => setSnooze({ ...snooze, maxResurface: e.target.value })} /></label>
              <div className="workflow-form-actions workflow-wide"><button type="submit"><span className="material-symbols-outlined" aria-hidden="true">save</span><span>Enregistrer la veille</span></button></div>
            </form>
            <form className="workflow-form" onSubmit={handleSaveSection('follow_ups')}>
              <div className="workflow-wide"><strong>Relances</strong></div>
              <label><span>Libellé</span><input value={followUps.label} onChange={(e) => setFollowUps({ ...followUps, label: e.target.value })} placeholder="Awaiting Reply" /></label>
              <label><span>Après (jours)</span><input type="number" min="1" value={followUps.afterDays} onChange={(e) => setFollowUps({ ...followUps, afterDays: e.target.value })} /></label>
              <label><span>Résultats max</span><input type="number" min="1" value={followUps.maxResults} onChange={(e) => setFollowUps({ ...followUps, maxResults: e.target.value })} /></label>
              <label className="workflow-wide"><span>Message de relance</span><input value={followUps.nudge} onChange={(e) => setFollowUps({ ...followUps, nudge: e.target.value })} placeholder="Just following up on this." /></label>
              <div className="workflow-form-actions workflow-wide"><button type="submit"><span className="material-symbols-outlined" aria-hidden="true">save</span><span>Enregistrer les relances</span></button></div>
            </form>
          </div>
        </Card>
      )}

      {canManage && (
        <details className="advanced-yaml card editor-card">
          <summary>Éditer rules.yaml brut (avancé)</summary>
          <p className="muted">Réservé au réglage fin. « Enregistrer les règles » persiste ce contenu verbatim (commentaires conservés).</p>
          <textarea spellCheck={false} value={yamlText} onChange={(e) => setYamlText(e.target.value)} />
        </details>
      )}

      <Card className="editor-card">
        <strong>Suggestions apprises</strong>
        <p className="muted">Générées automatiquement à partir de vos corrections. Promouvez une suggestion pour activer une règle ou mettre à jour une route de workflow, ou ignorez-la si elle n'est pas utile.</p>
        <div className="rule-list">
          {!suggestionsQuery.data?.learning_enabled ? (
            <div className="empty">Apprentissage des règles désactivé. Activez learning.enabled: true dans rules.yaml pour collecter des suggestions.</div>
          ) : !suggestions.length ? (
            <div className="empty">Aucune suggestion pour le moment. Ignorez, modifiez ou commentez un brouillon pour entraîner une règle.</div>
          ) : suggestions.map((s) => {
            const workflow = s.suggested_workflow || null;
            const rSuggestion = s.suggested_rule || {};
            const when = workflow?.when || rSuggestion.when || {};
            const bits = [];
            if (when.sender_domain?.length) bits.push(`domaine: ${when.sender_domain.join(', ')}`);
            if (when.subject_contains?.length) bits.push(`objet: ${when.subject_contains.join(', ')}`);
            if (workflow?.route_to?.length) bits.push(`route: ${workflow.route_to.join(', ')}`);
            const title = workflow ? (workflow.display_name || workflow.name || 'workflow suggéré') : (rSuggestion.name || 'règle suggérée');
            return (
              <div className="rule-row" key={s.index}>
                <div>
                  <div className="meta-inline">{workflow ? <span className="status-pill warn">workflow</span> : <span className="status-pill">règle</span>}</div>
                  <strong>{title}</strong>
                  <span className="muted">{[s.correction_type || '', bits.join(' · ') || 'aucun prédicat'].filter(Boolean).join(' · ')}</span>
                </div>
                {canManage && (
                  <div className="rule-row-actions">
                    <button className="primary" type="button" onClick={() => handlePromote(s.index)}>Promouvoir</button>
                    <button type="button" onClick={() => handleDismiss(s.index)}>Ignorer</button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </Card>
    </>
  );
}
