import { useEffect, useRef, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { Pager } from '../../components/ui/Pager';
import { EmptyState } from '../../components/ui/EmptyState';
import { useStatus } from '../../contexts/StatusContext';
import { compactText } from '../../utils/format';
import { useCapabilitiesQuery, useSaveCapabilities, usePolicyQuery } from '../../api/queries';

const CAPABILITY_LABELS = [
  {
    key: 'email',
    label: 'E-mail',
    wired: true,
    description: 'Autorise l’envoi, la réponse, le transfert et la notification interne. La politique de sécurité peut encore bloquer ou demander une validation.',
  },
  {
    key: 'calendar',
    label: 'Calendrier',
    wired: false,
    description: 'Documenté pour plus tard : aucun outil calendrier n’est branché dans cette version.',
  },
  {
    key: 'inbox',
    label: 'Messages',
    wired: true,
    description: 'Autorise les actions de rangement sur le message courant : libellé, archive, lecture/non-lu et corbeille.',
  },
  {
    key: 'drafts',
    label: 'Brouillons',
    wired: true,
    description: 'Autorise la création de brouillons Gmail sur le fil courant, toujours soumise aux règles de sécurité applicables.',
  },
];

const POLICY_PAGE_SIZE = 8;

function decisionPill(decision) {
  const map = { allow: 'ok', hitl: 'warn', deny: 'error' };
  const label = { allow: 'Automatique', hitl: 'Validation humaine', deny: 'Bloqué' };
  return <span className={`status-pill ${map[decision] || 'warn'}`}>{label[decision] || decision || 'deny'}</span>;
}

export default function CapabilitiesPage() {
  const { setStatus } = useStatus();
  const [capabilities, setCapabilities] = useState({});
  const [page, setPage] = useState(0);

  const query = useCapabilitiesQuery();
  const policyQuery = usePolicyQuery();
  const saveCapabilities = useSaveCapabilities();
  const announcedInitialLoad = useRef(false);
  const announcedPolicyLoad = useRef(false);
  const announcedError = useRef(null);
  const announcedPolicyError = useRef(null);

  useEffect(() => {
    if (query.data) {
      setCapabilities(query.data.capabilities || {});
      if (!announcedInitialLoad.current) {
        announcedInitialLoad.current = true;
        setStatus('Capacités chargées.', 'ok');
      }
    }
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (policyQuery.data && !announcedPolicyLoad.current) {
      announcedPolicyLoad.current = true;
      setStatus('Politique chargée.', 'ok');
    }
  }, [policyQuery.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!query.error || announcedError.current === query.error.message) return;
    announcedError.current = query.error.message;
    setStatus(`Impossible de charger les capacités : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!policyQuery.error || announcedPolicyError.current === policyQuery.error.message) return;
    announcedPolicyError.current = policyQuery.error.message;
    setStatus(`Impossible de charger la politique : ${policyQuery.error.message}`, 'error');
  }, [policyQuery.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (key) => setCapabilities((prev) => ({ ...prev, [key]: !prev[key] }));

  const handleSave = async () => {
    try {
      await saveCapabilities.mutateAsync(capabilities);
      setStatus('Capacités enregistrées.', 'ok');
    } catch (error) {
      setStatus(`Impossible d’enregistrer les capacités : ${error.message}`, 'error');
    }
  };

  const tools = policyQuery.data?.parsed?.tools || {};
  const names = Object.keys(tools);
  const defaultDecision = policyQuery.data?.parsed?.default || 'deny';
  const totalPages = Math.max(1, Math.ceil(names.length / POLICY_PAGE_SIZE));
  const currentPage = Math.min(page, totalPages - 1);
  const pageNames = names.slice(currentPage * POLICY_PAGE_SIZE, currentPage * POLICY_PAGE_SIZE + POLICY_PAGE_SIZE);

  return (
    <>
      <PageHeading view="capabilities" />
      <div className="toolbar">
        <button
          type="button"
          onClick={async () => { await query.refetch(); setStatus('Capacités chargées.', 'ok'); }}
        >
          <span className="material-symbols-outlined" aria-hidden="true">sync</span><span>Charger les capacités</span>
        </button>
        <button className="primary" type="button" onClick={handleSave}>
          <span className="material-symbols-outlined" aria-hidden="true">save</span><span>Enregistrer les capacités</span>
        </button>
        <button
          type="button"
          onClick={async () => { await policyQuery.refetch(); setStatus('Politique chargée.', 'ok'); }}
        >
          <span className="material-symbols-outlined" aria-hidden="true">policy</span><span>Charger la politique</span>
        </button>
      </div>
      <div className="editor-grid">
        <Card className="editor-card capability-card">
          <strong>Interrupteurs de capacités</strong>
          <p className="muted">Une capacité expose des outils à l’agent. La politique de sécurité reste appliquée au-dessus de ces interrupteurs.</p>
          {CAPABILITY_LABELS.map(({ key, label, wired, description }) => (
            <label className={`toggle-row capability-toggle ${wired ? '' : 'disabled'}`.trim()} key={key}>
              <input type="checkbox" checked={Boolean(capabilities[key]) && wired} disabled={!wired} onChange={() => toggle(key)} />
              <span>
                <strong>{label}</strong>
                <small>{wired ? 'branché' : 'bientôt disponible'}</small>
                <span className="muted">{description}</span>
              </span>
            </label>
          ))}
        </Card>
        <Card className="security-rules-card">
          <strong>Règles de sécurité</strong>
          <p className="muted">Décision appliquée à chaque action de l'agent avant exécution. <code>allow</code> = automatique, <code>hitl</code> = validation humaine requise, <code>deny</code> = bloqué.</p>
          {!names.length ? (
            <EmptyState message="Aucune règle de sécurité disponible (service de sécurité injoignable ?)." />
          ) : (
            <div className="rules-preview">
              <div className="notice">Action non listée : <strong>{defaultDecision}</strong> par défaut.</div>
              <div className="rule-list policy-list">
                {pageNames.map((name) => {
                  const tool = tools[name] || {};
                  const caps = [];
                  const limits = tool.limits || {};
                  if (limits.max_content_chars) caps.push(`${limits.max_content_chars} caractères max`);
                  if (limits.max_per_run) caps.push(`${limits.max_per_run}/exécution`);
                  if (limits.max_per_day) caps.push(`${limits.max_per_day}/jour`);
                  const recips = tool.recipients || {};
                  if (recips.allow_domains?.length) caps.push(`domaines autorisés : ${recips.allow_domains.join(', ')}`);
                  if (recips.deny_domains?.length) caps.push(`domaines bloqués : ${recips.deny_domains.join(', ')}`);
                  const capsText = caps.join(' · ') || 'Aucune limite spécifique';
                  return (
                    <div className="directory-row compact policy-row" key={name}>
                      <div className="directory-icon"><span className="material-symbols-outlined" aria-hidden="true">security</span></div>
                      <div className="directory-main">
                        <strong title={name}>{compactText(name, 42)}</strong>
                        <span title={capsText}>{compactText(capsText, 90)}</span>
                      </div>
                      {decisionPill(tool.decision)}
                    </div>
                  );
                })}
                <Pager page={currentPage} hasMore={currentPage < totalPages - 1} onPrev={() => setPage((p) => Math.max(0, p - 1))} onNext={() => setPage((p) => p + 1)} />
              </div>
            </div>
          )}
          <details className="advanced-yaml">
            <summary>Afficher le YAML brut (avancé)</summary>
            <pre className="policy-viewer">{policyQuery.data?.policy_yaml || 'Politique non chargée.'}</pre>
          </details>
        </Card>
      </div>
    </>
  );
}
