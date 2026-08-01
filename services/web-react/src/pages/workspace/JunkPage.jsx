import { useEffect, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { useInstance } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { useJunkQuery, useSaveJunk } from '../../api/queries';

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
  const [form, setForm] = useState(null);

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
    if (query.error) setStatus(`Filtre indisponible : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

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

  return (
    <div className="junk-page">
      <PageHeading view="junk" />
      <Card>
        <div className="card-header">
          <div>
            <h2>Filtre anti-bruit</h2>
            <div className="meta">
              <span>
                Ce qui est écarté ici ne reçoit jamais de réponse et ne consomme aucun appel au modèle.
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

            {!canManage ? <p className="muted">Un propriétaire de l’instance peut modifier ce filtre.</p> : null}
          </>
        )}
      </Card>
    </div>
  );
}
