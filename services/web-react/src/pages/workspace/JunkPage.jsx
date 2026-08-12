import { useEffect, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { SyncProgressBar } from '../../components/ui/SyncProgressBar';
import { useInstance } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import {
  useJunkQuery,
  useSaveJunk,
  useJunkSuggestionsQuery,
  useSensitivityQuery,
  useSaveSensitivity,
} from '../../api/queries';

const LIST_FIELDS = [
  {
    key: 'allowed_senders',
    label: 'Expéditeurs toujours traités',
    hint: 'Adresses complètes. Priorité absolue : ces messages passent même s’ils ressemblent à du courrier automatique.',
  },
  {
    key: 'allowed_domains',
    label: 'Domaines toujours traités',
    hint: 'Un domaine par ligne, sans @. Couvre aussi les sous-domaines.',
  },
  {
    key: 'blocked_senders',
    label: 'Expéditeurs toujours écartés',
    hint: 'Adresses dont aucun message ne doit jamais être traité.',
  },
  {
    key: 'blocked_domains',
    label: 'Domaines toujours écartés',
    hint: 'Un domaine par ligne, sans @.',
  },
];

const TOGGLES = [
  {
    key: 'enabled',
    label: 'Filtre anti-bruit actif',
    hint: 'Désactivé, tout message atteint l’analyse, y compris les newsletters.',
  },
  {
    key: 'gmail_categories',
    label: 'Utiliser les catégories Gmail',
    hint: 'Écarte ce que Gmail range déjà dans Promotions, Réseaux sociaux ou Forums.',
  },
  {
    key: 'bulk_headers',
    label: 'Utiliser les en-têtes de diffusion',
    hint: 'List-Unsubscribe, List-Id, Precedence et Auto-Submitted : ce que porte le courrier de masse et les réponses automatiques.',
  },
  {
    key: 'sender_heuristics',
    label: 'Analyser la forme de l’expéditeur',
    hint: 'Adresses en noreply@, marketing@, plateformes d’emailing et sous-domaines de campagne.',
  },
];

const SENSITIVITY_FIELDS = [
  {
    key: 'allowed_senders',
    label: 'Expéditeurs exemptés',
    hint: 'Adresses complètes. Priorité absolue : ces messages peuvent être traités malgré une règle sensible.',
  },
  {
    key: 'allowed_domains',
    label: 'Domaines exemptés',
    hint: 'Un domaine par ligne, sans @.',
  },
  {
    key: 'blocked_senders',
    label: 'Expéditeurs sensibles',
    hint: 'Adresses dont le corps ne doit jamais être ouvert par l’agent.',
  },
  {
    key: 'blocked_domains',
    label: 'Domaines sensibles',
    hint: 'Un domaine par ligne. Couvre aussi les sous-domaines.',
  },
  {
    key: 'subject_keywords',
    label: 'Mots-clés de sujet sensibles',
    hint: 'Un mot ou fragment par ligne. Comparaison insensible à la casse.',
  },
];

function toLines(value) {
  return (value || []).join('\n');
}

function fromLines(text) {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}

export default function JunkPage() {
  const { hasRole } = useInstance();
  const { setStatus } = useStatus();
  const canManage = hasRole('owner');

  const query = useJunkQuery();
  const save = useSaveJunk();
  const sensitivityQuery = useSensitivityQuery();
  const saveSensitivity = useSaveSensitivity();
  const [form, setForm] = useState(null);
  const [sensitivityForm, setSensitivityForm] = useState(null);

  useEffect(() => {
    if (!query.data?.junk) return;
    const junk = query.data.junk;
    setForm({
      enabled: junk.enabled !== false,
      gmail_categories: junk.gmail_categories !== false,
      bulk_headers: junk.bulk_headers !== false,
      sender_heuristics: junk.sender_heuristics !== false,
      allowed_senders: toLines(junk.allowed_senders),
      allowed_domains: toLines(junk.allowed_domains),
      blocked_senders: toLines(junk.blocked_senders),
      blocked_domains: toLines(junk.blocked_domains),
    });
  }, [query.data]);

  useEffect(() => {
    if (!sensitivityQuery.data?.sensitivity) return;
    const sensitivity = sensitivityQuery.data.sensitivity;
    setSensitivityForm({
      enabled: sensitivity.enabled === true,
      allowed_senders: toLines(sensitivity.allowed_senders),
      allowed_domains: toLines(sensitivity.allowed_domains),
      blocked_senders: toLines(sensitivity.blocked_senders),
      blocked_domains: toLines(sensitivity.blocked_domains),
      subject_keywords: toLines(sensitivity.subject_keywords),
    });
  }, [sensitivityQuery.data]);

  useEffect(() => {
    if (query.error) setStatus(`Filtre indisponible : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (sensitivityQuery.error) setStatus(`Filtre sensibilité indisponible : ${sensitivityQuery.error.message}`, 'error');
  }, [sensitivityQuery.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const [scanRequested, setScanRequested] = useState(false);
  const suggestionsQuery = useJunkSuggestionsQuery(scanRequested);
  const suggestions = suggestionsQuery.data?.suggestions || [];

  const addBlockedSender = (address) => {
    if (!form) return;
    const current = String(form.blocked_senders || '').split('\n').map((l) => l.trim()).filter(Boolean);
    if (current.includes(address)) return;
    setForm({ ...form, blocked_senders: [...current, address].join('\n') });
  };

  const handleSave = async () => {
    if (!form) return;
    try {
      await save.mutateAsync({
        enabled: form.enabled,
        gmail_categories: form.gmail_categories,
        bulk_headers: form.bulk_headers,
        sender_heuristics: form.sender_heuristics,
        allowed_senders: fromLines(form.allowed_senders),
        allowed_domains: fromLines(form.allowed_domains),
        blocked_senders: fromLines(form.blocked_senders),
        blocked_domains: fromLines(form.blocked_domains),
      });
      setStatus('Filtre anti-bruit enregistré.', 'ok');
    } catch (error) {
      setStatus(`Enregistrement impossible : ${error.message}`, 'error');
    }
  };

  const handleSaveSensitivity = async () => {
    if (!sensitivityForm) return;
    try {
      await saveSensitivity.mutateAsync({
        enabled: sensitivityForm.enabled,
        allowed_senders: fromLines(sensitivityForm.allowed_senders),
        allowed_domains: fromLines(sensitivityForm.allowed_domains),
        blocked_senders: fromLines(sensitivityForm.blocked_senders),
        blocked_domains: fromLines(sensitivityForm.blocked_domains),
        subject_keywords: fromLines(sensitivityForm.subject_keywords),
      });
      setStatus('Filtre de sensibilité enregistré.', 'ok');
    } catch (error) {
      setStatus(`Enregistrement impossible : ${error.message}`, 'error');
    }
  };

  return (
    <div className="junk-page">
      <PageHeading view="junk" />
      <Card>
        <div className="card-header">
          <div>
            <h2>Filtre indésirable</h2>
            <div className="meta">
              <span>
                Le filtre jette, les règles rangent. Ce qui est écarté ici ne reçoit jamais de réponse et ne consomme aucun appel au modèle.
              </span>
            </div>
          </div>
          <div className="toolbar">
            <button className="primary" type="button" disabled={!canManage || !form || save.isPending} onClick={handleSave}>
              {save.isPending ? 'Enregistrement...' : 'Enregistrer'}
            </button>
          </div>
        </div>

        {query.isLoading || !form ? (
          <p className="empty-cell">Chargement du filtre...</p>
        ) : (
          <>
            <p className="muted">
              Une catégorie peut réclamer du courrier automatique malgré ce filtre, mais seulement si
              elle le déclare explicitement. Sans cela, une alerte ou une newsletter venant d’un
              contact connu reste écartée.
            </p>

            <fieldset className="junk-toggles">
              <legend>Signaux utilisés</legend>
              {TOGGLES.map((toggle) => (
                <label key={toggle.key} className="junk-toggle">
                  <input
                    type="checkbox"
                    checked={Boolean(form[toggle.key])}
                    disabled={!canManage}
                    onChange={(event) => setForm({ ...form, [toggle.key]: event.target.checked })}
                  />
                  <span>
                    <strong>{toggle.label}</strong>
                    <span className="muted">{toggle.hint}</span>
                  </span>
                </label>
              ))}
            </fieldset>

            <div className="junk-lists">
              {LIST_FIELDS.map((field) => (
                <label key={field.key} className="junk-list">
                  <strong>{field.label}</strong>
                  <span className="muted">{field.hint}</span>
                  <textarea
                    rows={5}
                    value={form[field.key]}
                    disabled={!canManage}
                    onChange={(event) => setForm({ ...form, [field.key]: event.target.value })}
                  />
                </label>
              ))}
            </div>

            {canManage ? (
              <div className="junk-suggestions">
                <div className="card-header">
                  <div>
                    <h3>Expéditeurs de masse détectés</h3>
                    <div className="meta">Tirés de votre boîte, pas d'exemples génériques.</div>
                  </div>
                  <button type="button" onClick={() => (scanRequested ? suggestionsQuery.refetch() : setScanRequested(true))}>
                    <span className="material-symbols-outlined" aria-hidden="true">frame_inspect</span>
                    <span>{scanRequested ? 'Relancer l’analyse' : 'Analyser la boîte'}</span>
                  </button>
                </div>
                {suggestionsQuery.isFetching ? (
                  <SyncProgressBar
                    compact
                    label="Analyse de la boîte"
                    mode="syncing"
                    message="Détection des expéditeurs de masse en cours..."
                  />
                ) : null}
                {suggestionsQuery.error ? (
                  <p className="muted">Analyse indisponible : {suggestionsQuery.error.message}</p>
                ) : null}
                {scanRequested && !suggestionsQuery.isFetching && !suggestionsQuery.error && !suggestions.length ? (
                  <p className="muted">Aucun expéditeur de masse à proposer.</p>
                ) : null}
                {suggestions.length ? (
                  <ul className="junk-suggestion-list">
                    {suggestions.map((item) => (
                      <li key={item.address}>
                        <span className="junk-suggestion-copy">
                          <strong>{item.address}</strong>
                          <small>{item.count} message(s) · {item.reason}</small>
                        </span>
                        <button type="button" onClick={() => addBlockedSender(item.address)}>
                          <span className="material-symbols-outlined" aria-hidden="true">block</span>
                          <span>Écarter</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : null}
                <p className="muted">Les ajouts ne prennent effet qu'après « Enregistrer ».</p>
              </div>
            ) : null}

            {!canManage ? <p className="muted">Un propriétaire de l’instance peut modifier ce filtre.</p> : null}
          </>
        )}
      </Card>

      <Card>
        <div className="card-header">
          <div>
            <h2>Courrier sensible</h2>
            <div className="meta">
              <span>
                Les correspondances connues comme sensibles sont arrêtées sur les en-têtes : l’agent ne télécharge pas le corps du message.
              </span>
            </div>
          </div>
          <div className="toolbar">
            <button
              className="primary"
              type="button"
              disabled={!canManage || !sensitivityForm || saveSensitivity.isPending}
              onClick={handleSaveSensitivity}
            >
              {saveSensitivity.isPending ? 'Enregistrement...' : 'Enregistrer'}
            </button>
          </div>
        </div>

        {sensitivityQuery.isLoading || !sensitivityForm ? (
          <p className="empty-cell">Chargement du filtre...</p>
        ) : (
          <>
            <fieldset className="junk-toggles">
              <legend>Activation</legend>
              <label className="junk-toggle">
                <input
                  type="checkbox"
                  checked={Boolean(sensitivityForm.enabled)}
                  disabled={!canManage}
                  onChange={(event) => setSensitivityForm({ ...sensitivityForm, enabled: event.target.checked })}
                />
                <span>
                  <strong>Filtre de sensibilité actif</strong>
                  <span className="muted">Désactivé, aucune règle sensible n’est appliquée.</span>
                </span>
              </label>
            </fieldset>

            <div className="junk-lists">
              {SENSITIVITY_FIELDS.map((field) => (
                <label key={field.key} className="junk-list">
                  <strong>{field.label}</strong>
                  <span className="muted">{field.hint}</span>
                  <textarea
                    rows={5}
                    value={sensitivityForm[field.key]}
                    disabled={!canManage}
                    onChange={(event) => setSensitivityForm({ ...sensitivityForm, [field.key]: event.target.value })}
                  />
                </label>
              ))}
            </div>

            {!canManage ? <p className="muted">Un propriétaire de l’instance peut modifier ce filtre.</p> : null}
          </>
        )}
      </Card>
    </div>
  );
}
