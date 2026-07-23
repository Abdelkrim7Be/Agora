import { useEffect, useRef, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { useInstance } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { useDialog } from '../../contexts/DialogContext';
import {
  useMemorySummaryQuery,
  useMemoryQuery,
  useSaveMemory,
  useDeleteMemoryItem,
  useClearMemory,
} from '../../api/queries';

const MEMORY_CARDS = [
  { kind: 'triage_preferences', label: 'Ce que l’agent a appris sur le tri' },
  { kind: 'response_preferences', label: 'Ce que l’agent a appris sur les réponses' },
  { kind: 'writing_style', label: 'Ce que l’agent a appris sur votre style' },
];

export default function MemoryPage() {
  const { hasRole } = useInstance();
  const { setStatus } = useStatus();
  const { confirmDialog } = useDialog();
  const canManage = hasRole('owner');

  const [triage, setTriage] = useState('');
  const [response, setResponse] = useState('');
  const announcedInitialLoad = useRef(false);
  const announcedError = useRef(null);

  const summaryQuery = useMemorySummaryQuery();
  const memoryQuery = useMemoryQuery();
  const saveMemory = useSaveMemory();
  const deleteMemoryItem = useDeleteMemoryItem();
  const clearMemory = useClearMemory();

  useEffect(() => {
    if (memoryQuery.data) {
      setTriage(memoryQuery.data.triage_preferences || '');
      setResponse(memoryQuery.data.response_preferences || '');
      if (!announcedInitialLoad.current) {
        announcedInitialLoad.current = true;
        setStatus('Mémoire chargée.', 'ok');
      }
    }
  }, [memoryQuery.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!memoryQuery.error || announcedError.current === memoryQuery.error.message) return;
    announcedError.current = memoryQuery.error.message;
    setStatus(`Impossible de charger la mémoire : ${memoryQuery.error.message}`, 'error');
  }, [memoryQuery.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleReload = () => {
    summaryQuery.refetch();
    memoryQuery.refetch();
  };

  const handleSave = async () => {
    try {
      await saveMemory.mutateAsync({ triage_preferences: triage, response_preferences: response });
      setStatus('Mémoire enregistrée.', 'ok');
    } catch (error) {
      setStatus(`Impossible d’enregistrer la mémoire : ${error.message}`, 'error');
    }
  };

  const handleDeleteItem = async (kind, id) => {
    const confirmed = await confirmDialog({
      title: 'Supprimer l’apprentissage',
      message: 'Supprimer cet apprentissage ? L’agent ne s’en souviendra plus.',
      confirmLabel: 'Supprimer',
      confirmIcon: 'delete',
      variant: 'danger',
    });
    if (!confirmed) return;
    try {
      await deleteMemoryItem.mutateAsync({ kind, id });
      setStatus('Apprentissage supprimé.', 'ok');
    } catch (error) {
      setStatus(`Impossible de supprimer l’apprentissage : ${error.message}`, 'error');
    }
  };

  const handleClearAll = async () => {
    const confirmed = await confirmDialog({
      title: 'Effacer toute la mémoire',
      message: 'L’agent oubliera tout ce qu’il a appris (tri, réponses, style). Cette action est irréversible.',
      confirmLabel: 'Effacer',
      confirmIcon: 'delete_forever',
      variant: 'danger',
    });
    if (!confirmed) return;
    try {
      await clearMemory.mutateAsync();
      setStatus('Mémoire effacée.', 'ok');
    } catch (error) {
      setStatus(`Impossible d’effacer la mémoire : ${error.message}`, 'error');
    }
  };

  const summary = summaryQuery.data || {};

  return (
    <>
      <PageHeading view="memory" />
      <div className="toolbar">
        <button type="button" onClick={handleReload}>
          <span className="material-symbols-outlined" aria-hidden="true">sync</span><span>Charger la mémoire</span>
        </button>
        <button className="primary" type="button" onClick={handleSave}>
          <span className="material-symbols-outlined" aria-hidden="true">save</span><span>Enregistrer la mémoire</span>
        </button>
        <button className="danger" type="button" onClick={handleClearAll}>
          <span className="material-symbols-outlined" aria-hidden="true">delete_forever</span><span>Effacer toute la mémoire</span>
        </button>
      </div>
      <div className="memory-cards-grid">
        {MEMORY_CARDS.map(({ kind, label }) => {
          const items = summary[kind] || [];
          return (
            <Card className="memory-card" key={kind}>
              <strong>{label}</strong>
              <ul className="memory-items">
                {items.length ? items.map((item) => (
                  <li className="memory-item" key={item.id}>
                    {item.display_text || item.text}
                    {canManage && (
                      <button type="button" className="memory-item-delete" aria-label="Supprimer cet apprentissage" onClick={() => handleDeleteItem(kind, item.id)}>×</button>
                    )}
                  </li>
                )) : <li className="memory-empty">Rien d’appris pour le moment.</li>}
              </ul>
            </Card>
          );
        })}
      </div>
      {canManage && (
        <details className="card persona-advanced-card">
          <summary>Données techniques</summary>
          <p className="muted">Contenu brut de la mémoire — l’édition directe écrase les apprentissages.</p>
          <div className="editor-grid">
            <label className="card editor-card">
              <strong>Préférences de triage</strong>
              <textarea spellCheck={false} value={triage} onChange={(event) => setTriage(event.target.value)} />
            </label>
            <label className="card editor-card">
              <strong>Préférences de réponse</strong>
              <textarea spellCheck={false} value={response} onChange={(event) => setResponse(event.target.value)} />
            </label>
          </div>
        </details>
      )}
    </>
  );
}
