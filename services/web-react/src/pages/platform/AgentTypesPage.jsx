import { useEffect } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { StatusBadge, Badge } from '../../components/ui/Badge';
import { EmptyState } from '../../components/ui/EmptyState';
import { useStatus } from '../../contexts/StatusContext';
import { useAgentTypesQuery } from '../../api/queries';
import { capabilityLabelFr } from '../../utils/format';

const COMING_SOON_AGENT_TYPES = [
  {
    id: 'prospection-agent',
    display_name: 'Agent de prospection',
    icon: 'travel_explore',
    description: 'Recherche des prospects, prépare des prises de contact, suit les relances et garde chaque envoi sous validation.',
    capabilities: ['recherche de prospects', 'brouillons de prise de contact', 'relances', 'validation requise'],
    comingSoon: true,
  },
  {
    id: 'invoice-manager',
    display_name: 'Gestionnaire de factures',
    icon: 'receipt_long',
    description: 'Lit les factures, extrait les informations de paiement, route les validations et signale les exceptions finance.',
    capabilities: ['extraction de factures', 'routage de paiement', 'validation finance', 'journal d’audit'],
    comingSoon: true,
  },
];

export default function AgentTypesPage() {
  const { setStatus } = useStatus();
  const query = useAgentTypesQuery();

  useEffect(() => {
    if (query.data) setStatus('Types d’agents chargés.', 'ok');
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (query.error) setStatus(`Impossible de charger les types d’agents : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const allTypes = [...(query.data || []), ...COMING_SOON_AGENT_TYPES];

  return (
    <>
      <PageHeading view="agentTypes" />
      <div className="toolbar">
        <button type="button" onClick={() => query.refetch()}>
          <span className="material-symbols-outlined" aria-hidden="true">refresh</span>
          <span>Actualiser</span>
        </button>
      </div>
      <div className="type-grid">
        {!allTypes.length ? (
          <EmptyState message="Aucun type d’agent enregistré." />
        ) : (
          allTypes.map((type) => {
            const comingSoon = Boolean(type.comingSoon);
            const activeHealthy = !comingSoon && (type.health === 'healthy' || type.health === 'active' || !type.health);
            return (
              <Card
                key={type.id}
                className={`type-card ${comingSoon ? 'coming-soon' : activeHealthy ? 'active-healthy' : ''}`.trim()}
                aria-disabled={comingSoon ? 'true' : 'false'}
              >
                <div className="card-header">
                  <div>
                    <h2>{type.display_name || type.id}</h2>
                    <div className="meta">
                      <span>{type.id}</span>
                      <StatusBadge
                        status={type.health}
                        label={comingSoon ? 'bientôt disponible' : activeHealthy ? 'actif et sain' : undefined}
                        classFn={comingSoon ? () => 'warn' : undefined}
                      />
                      <span>{type.base_path || ''}</span>
                    </div>
                  </div>
                  <span className="material-symbols-outlined type-icon" aria-hidden="true">{type.icon || 'extension'}</span>
                </div>
                <p>{type.description || ''}</p>
                <div className="chip-row">
                  {(type.capabilities || []).map((capability) => <Badge key={capability}>{capabilityLabelFr(capability)}</Badge>)}
                </div>
              </Card>
            );
          })
        )}
      </div>
    </>
  );
}
