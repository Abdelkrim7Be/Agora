import { useEffect, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { PageHeading } from '../../components/layout/PageHeading';
import { Pager } from '../../components/ui/Pager';
import { EmptyState } from '../../components/ui/EmptyState';
import ValidationCard from '../../components/domain/ValidationCard';
import { useInstance } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { useDialog } from '../../contexts/DialogContext';
import { useBusy } from '../../contexts/BusyContext';
import { useApi } from '../../api/useApi';
import { actionArgs, workflowLabelFr } from '../../utils/format';
import {
  usePendingRunsQuery,
  useApproveRun,
  useRejectRun,
  useClaimRun,
  useAssignRun,
  useToneRun,
  useBulkDecision,
  useSyncGmail,
  useCategoriesQuery,
} from '../../api/queries';
import { useRunEvents } from '../../hooks/useRunEvents';

function feedbackMessageId() {
  return `feedback-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export default function ValidationPage() {
  const { hasRole } = useInstance();
  const { setStatus } = useStatus();
  const { confirmDialog, promptDialog } = useDialog();
  const { runBusy, holdOverlay } = useBusy();
  const { api, streamApi } = useApi();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const canApprove = hasRole('approver');

  const [page, setPage] = useState(0);
  const [priority, setPriority] = useState('');
  const [category, setCategory] = useState('');
  const [search, setSearch] = useState('');
  const [since, setSince] = useState('');
  const [selectedRuns, setSelectedRuns] = useState(new Set());
  const [activeRunId, setActiveRunId] = useState(null);
  const [editedFields, setEditedFields] = useState({});
  const [feedbackThreads, setFeedbackThreads] = useState({});
  const [feedbackDrafts, setFeedbackDrafts] = useState({});
  const [busyRuns, setBusyRuns] = useState(new Set());
  const announcedInitialLoad = useRef(false);

  const query = usePendingRunsQuery(page, { category, priority, q: search.trim(), since });
  const categoriesQuery = useCategoriesQuery();
  const availableCategories = categoriesQuery.data?.parsed?.categories || [];
  useRunEvents();
  const approveRun = useApproveRun();
  const rejectRun = useRejectRun();
  const claimRun = useClaimRun();
  const assignRun = useAssignRun();
  const toneRun = useToneRun();
  const bulkDecision = useBulkDecision();
  const syncGmail = useSyncGmail();

  const runs = query.data?.runs || [];

  useEffect(() => { setPage(0); }, [category, priority, search, since]);

  useEffect(() => {
    if (query.data && !announcedInitialLoad.current) {
      announcedInitialLoad.current = true;
      setStatus('Validations à jour.', 'ok');
    }
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (query.error) setStatus(`Impossible de charger la validation : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  // Arriving from "Forcer l'agent" with ?run=<id>: put the person on the draft
  // that was just written for them instead of the top of a queue where they have
  // to find it. Cleared from the URL afterwards so a refresh is an ordinary visit.
  const requestedRunId = searchParams.get('run');
  useEffect(() => {
    if (!requestedRunId || !runs.length) return;
    if (!runs.some((run) => run.run_id === requestedRunId)) return;
    setActiveRunId(requestedRunId);
    document.querySelector(`[data-card="${requestedRunId}"]`)
      ?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    setSearchParams({}, { replace: true });
  }, [requestedRunId, runs]); // eslint-disable-line react-hooks/exhaustive-deps

  // Prune selection/edits to runs still present.
  useEffect(() => {
    const present = new Set(runs.map((run) => run.run_id));
    setSelectedRuns((prev) => {
      const kept = [...prev].filter((id) => present.has(id));
      if (kept.length === prev.size) return prev;
      return new Set(kept);
    });
  }, [runs]); // eslint-disable-line react-hooks/exhaustive-deps

  const setBusy = (runId, busy) => {
    setBusyRuns((prev) => {
      const next = new Set(prev);
      if (busy) next.add(runId); else next.delete(runId);
      return next;
    });
  };

  const handleFieldChange = (runId, field, value) => {
    setEditedFields((prev) => ({ ...prev, [runId]: { ...prev[runId], [field]: value } }));
  };

  const handleFeedbackDraftChange = (runId, value) => {
    setFeedbackDrafts((prev) => ({ ...prev, [runId]: value }));
  };

  const addFeedbackMessage = (runId, role, content, options = {}) => {
    const id = options.id || feedbackMessageId();
    const message = { id, role, content, at: new Date().toISOString(), live: options.live };
    setFeedbackThreads((prev) => ({ ...prev, [runId]: [...(prev[runId] || []), message].slice(-20) }));
    return id;
  };

  const updateFeedbackMessage = (runId, messageId, content) => {
    setFeedbackThreads((prev) => ({
      ...prev,
      [runId]: (prev[runId] || []).map((message) => (message.id === messageId ? { ...message, content, live: true } : message)),
    }));
  };

  const settleFeedbackMessage = (runId, messageId, content) => {
    setFeedbackThreads((prev) => ({
      ...prev,
      [runId]: (prev[runId] || []).map((message) => (message.id === messageId ? { ...message, content, live: false } : message)),
    }));
  };

  const editedArgsFor = (run) => (Object.keys(editedFields[run.run_id] || {}).length
    ? { ...actionArgs(run), ...editedFields[run.run_id] }
    : null);

  // A run whose state is gone answers 410 and has just been retired server-side:
  // refresh so the card leaves the queue instead of sitting there un-actionable.
  const reportFailure = (message, error) => {
    setStatus(`${message} : ${error.message}`, 'error');
    if (error.message.includes('expiré')) {
      queryClient.invalidateQueries({ queryKey: ['pending-runs'] });
    }
  };

  const handleDecision = async (command, runId, options = {}) => {
    const run = runs.find((item) => item.run_id === runId);
    if (!run) return;

    if (command === 'detail') {
      navigate(`../run/${runId}`);
      return;
    }

    if (command === 'tone') {
      try {
        const result = await runBusy(
          `Reformulation du brouillon (${options.tone})`,
          () => toneRun.mutateAsync({ runId, tone: options.tone }),
        );
        handleFieldChange(runId, result.field || options.field || 'content', result.content);
        setStatus('Brouillon reformulé — vérifiez avant d’approuver.', 'ok');
      } catch (error) {
        reportFailure('Échec de la reformulation', error);
      }
      return;
    }

    if (command === 'assign') {
      const value = await promptDialog({
        title: 'Assigner cette approbation',
        message: 'Identifiant (e-mail) de la personne à qui assigner cette validation. Laissez vide pour retirer l’assignation.',
        placeholder: run.assignee || 'ex : alice@example.com',
        confirmLabel: 'Assigner',
        defaultValue: run.assignee || '',
        required: false,
      });
      if (value === null) return;
      try {
        await runBusy('Assignation de la validation', () => assignRun.mutateAsync({ runId, assignee: value || null }));
        setStatus(value ? `Assigné à ${value}.` : 'Assignation retirée.', 'ok');
      } catch (error) {
        reportFailure('Échec de l’assignation', error);
      }
      return;
    }

    if (command === 'claim') {
      setBusy(runId, true);
      try {
        await runBusy('Prise en charge de la validation', () => claimRun.mutateAsync(runId));
        setStatus('Cette validation vous est maintenant assignée.', 'ok');
      } catch (error) {
        reportFailure('Échec de la prise en charge', error);
      } finally {
        setBusy(runId, false);
      }
      return;
    }

    if (command === 'accept') {
      setBusy(runId, true);
      try {
        await runBusy('Envoi en cours', () => approveRun.mutateAsync({ runId, args: editedArgsFor(run) }));
        setEditedFields((prev) => { const next = { ...prev }; delete next[runId]; return next; });
        setStatus('Envoyé.', 'ok');
      } catch (error) {
        reportFailure('Décision échouée', error);
      } finally {
        setBusy(runId, false);
      }
      return;
    }

    if (command === 'ignore') {
      setBusy(runId, true);
      try {
        await runBusy('Rejet en cours', () => rejectRun.mutateAsync(runId));
        setStatus('E-mail ignoré.', 'ok');
      } catch (error) {
        reportFailure('Décision échouée', error);
      } finally {
        setBusy(runId, false);
      }
      return;
    }

    if (command === 'respond') {
      const feedback = options.feedback || '';
      if (!feedback.trim()) {
        document.querySelector(`[data-testid="feedback-input-${runId}"]`)?.focus();
        setStatus('Saisissez votre retour dans le fil du brouillon.', 'ok');
        return;
      }
      addFeedbackMessage(runId, 'user', feedback);
      const agentMessageId = addFeedbackMessage(runId, 'agent', 'L’agent rédige une nouvelle version…', { live: true });
      handleFeedbackDraftChange(runId, '');
      setBusy(runId, true);
      setStatus('Régénération du brouillon. Cela peut prendre quelques secondes...');

      const respondDraft = editedArgsFor(run);
      let streamedDraft = '';
      let streamedError = null;
      try {
        await streamApi(`/api/agent/run/${runId}/respond/stream`, {
          method: 'POST',
          body: JSON.stringify({ feedback, draft: respondDraft }),
        }, ({ event, data }) => {
          if (event === 'draft') {
            streamedDraft = data?.content || `${streamedDraft}${data?.delta || ''}`;
            handleFieldChange(runId, 'content', streamedDraft);
            updateFeedbackMessage(runId, agentMessageId, 'Rédaction en direct dans le brouillon');
            setStatus('Diffusion du brouillon révisé...', 'ok');
          } else if (event === 'error') {
            streamedError = new Error(data?.message || 'Diffusion du brouillon échouée.');
          }
        });
        if (streamedError) throw streamedError;
      } catch (_error) {
        try {
          await api(`/api/agent/run/${runId}/respond`, { method: 'POST', body: JSON.stringify({ feedback, draft: respondDraft }) });
        } catch (fallbackError) {
          settleFeedbackMessage(runId, agentMessageId, `La retouche a échoué : ${fallbackError.message}`);
          reportFailure('Décision échouée', fallbackError);
          setBusy(runId, false);
          return;
        }
      }
      setEditedFields((prev) => { const next = { ...prev }; delete next[runId]; return next; });
      settleFeedbackMessage(runId, agentMessageId, streamedDraft ? 'Version révisée appliquée dans le brouillon.' : 'Retour pris en compte.');
      setStatus('Le brouillon reste en attente de relecture.', 'ok');
      queryClient.invalidateQueries({ queryKey: ['pending-runs'] });
      setBusy(runId, false);
      return;
    }
  };

  const toggleSelect = (runId) => {
    setSelectedRuns((prev) => {
      const next = new Set(prev);
      if (next.has(runId)) next.delete(runId); else next.add(runId);
      return next;
    });
  };

  const handleBulk = async (decision, explicitRunIds = null) => {
    const runIds = explicitRunIds || [...selectedRuns];
    if (!runIds.length) return;
    const confirmed = await confirmDialog({
      title: decision === 'approve' ? 'Approuver la sélection' : 'Rejeter la sélection',
      message: `${runIds.length} approbation(s) sélectionnée(s) — cette action s'applique à chacune individuellement.`,
      confirmLabel: decision === 'approve' ? 'Approuver' : 'Rejeter',
      variant: decision === 'approve' ? '' : 'danger',
    });
    if (!confirmed) return;
    try {
      const result = await runBusy(
        `${decision === 'approve' ? 'Approbation' : 'Rejet'} de ${runIds.length} validation(s)`,
        () => bulkDecision.mutateAsync({ runIds, decision }),
      );
      const errors = (result.results || []).filter((item) => item.status === 'error');
      setSelectedRuns((prev) => {
        if (!explicitRunIds) return new Set();
        const next = new Set(prev);
        runIds.forEach((id) => next.delete(id));
        return next;
      });
      setStatus(errors.length ? `${errors.length} échec(s) sur ${runIds.length}.` : `${runIds.length} décision(s) appliquée(s).`, errors.length ? 'error' : 'ok');
    } catch (error) {
      setStatus(`Action groupée échouée : ${error.message}`, 'error');
    }
  };

  // The toolbar already draws its own progress bar for this sync — holding
  // the overlay off keeps it from covering the exact bar being watched.
  useEffect(() => {
    if (!syncGmail.isPending || !holdOverlay) return undefined;
    return holdOverlay();
  }, [syncGmail.isPending, holdOverlay]);

  const handleSync = async () => {
    try {
      await syncGmail.mutateAsync();
      setStatus('Synchronisation terminée.', 'ok');
    } catch (error) {
      setStatus(`Synchronisation échouée : ${error.message}`, 'error');
    }
  };

  // Keyboard shortcuts: j/k navigate, a approve, r ignore, e focus feedback, x toggle select.
  useEffect(() => {
    const handler = (event) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target;
      if (target?.matches?.('input, textarea, select, [contenteditable="true"]')) return;
      if (document.querySelector('#app-dialog:not([hidden])')) return;
      if (!runs.length) return;
      const currentIndex = Math.max(0, runs.findIndex((run) => run.run_id === activeRunId));
      if (event.key === 'j' || event.key === 'k') {
        const nextIndex = event.key === 'j'
          ? Math.min(runs.length - 1, currentIndex + 1)
          : Math.max(0, currentIndex - 1);
        const nextRun = runs[nextIndex];
        setActiveRunId(nextRun.run_id);
        document.querySelector(`[data-card="${nextRun.run_id}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
        event.preventDefault();
        return;
      }
      if (!canApprove || !activeRunId) return;
      if (event.key === 'a') { handleDecision('accept', activeRunId); event.preventDefault(); }
      else if (event.key === 'r') { handleDecision('ignore', activeRunId); event.preventDefault(); }
      else if (event.key === 'e') { document.querySelector(`[data-testid="feedback-input-${activeRunId}"]`)?.focus(); event.preventDefault(); }
      else if (event.key === 'x') { toggleSelect(activeRunId); event.preventDefault(); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }); // intentionally no deps — always reads latest runs/activeRunId/canApprove via closure

  const hasMore = query.data?.hasMore ?? false;
  const selectedCount = selectedRuns.size;
  const categoryGroups = runs.reduce((acc, run) => {
    const label = workflowLabelFr({ display_name: run.category_display_name, category: run.category });
    (acc[label] ||= []).push(run);
    return acc;
  }, {});
  const categoryGroupEntries = Object.entries(categoryGroups);
  const selectCategory = (items) => {
    setSelectedRuns((prev) => {
      const next = new Set(prev);
      items.forEach((run) => next.add(run.run_id));
      return next;
    });
  };

  return (
    <>
      <PageHeading view="validation" />
      <div className="notice">
        <strong>Décisions à prendre :</strong> seules les actions qui attendent une validation humaine apparaissent ici.
      </div>
      <div className="toolbar">
        <button type="button" onClick={() => query.refetch()}>
          <span className="material-symbols-outlined" aria-hidden="true">refresh</span>
          <span>Actualiser</span>
        </button>
        <button className="primary" type="button" onClick={handleSync} disabled={syncGmail.isPending}>
          <span className="material-symbols-outlined" aria-hidden="true">mark_email_read</span>
          <span>Vérifier Gmail</span>
        </button>
        {syncGmail.isPending ? (
          <div className="progress-track">
            <span className="progress-track-dot" aria-hidden="true" />
            <div className="progress-bar-indeterminate" role="progressbar" aria-label="Vérification Gmail en cours" />
            <span className="progress-track-label">Synchronisation...</span>
          </div>
        ) : null}
        <select aria-label="Filtre de cas métier" value={category} onChange={(event) => setCategory(event.target.value)}>
          <option value="">Tous les cas métier</option>
          {availableCategories.map((c) => <option key={c.name} value={c.name}>{c.display_name || c.name}</option>)}
        </select>
        <select aria-label="Filtre de priorité" value={priority} onChange={(event) => setPriority(event.target.value)}>
          <option value="">Toutes priorités</option>
          <option value="urgent">Urgent</option>
          <option value="normal">Normal</option>
          <option value="low">Basse</option>
        </select>
        <input aria-label="Recherche expéditeur ou sujet" placeholder="Rechercher" value={search} onChange={(event) => setSearch(event.target.value)} />
        <input aria-label="Depuis le" type="date" value={since} onChange={(event) => setSince(event.target.value)} />
        {canApprove && category && runs.length ? (
          <button
            className="primary"
            type="button"
            title={`Approuver et envoyer les ${runs.length} validation(s) de ce cas métier`}
            onClick={() => handleBulk('approve', runs.map((run) => run.run_id))}
          >
            <span className="material-symbols-outlined" aria-hidden="true">done_all</span>
            <span>
              Approuver tout « {availableCategories.find((c) => c.name === category)?.display_name || category} » ({runs.length})
            </span>
          </button>
        ) : null}
        <span className="counter">{runs.length} en attente</span>
        <span className="kbd-legend" title="Raccourcis clavier disponibles">Clavier disponible</span>
        <span className="toolbar-spacer"></span>
        <Pager page={page} hasMore={hasMore} onPrev={() => setPage((p) => Math.max(0, p - 1))} onNext={() => setPage((p) => p + 1)} />
      </div>

      {canApprove && selectedCount > 0 && (
        <div className="bulk-bar">
          <span>{selectedCount} sélectionnée(s)</span>
          <button type="button" onClick={() => setSelectedRuns(new Set())}>Effacer</button>
          <button className="primary" type="button" onClick={() => handleBulk('approve')}>Approuver la sélection</button>
          <button className="danger" type="button" onClick={() => handleBulk('reject')}>Rejeter la sélection</button>
        </div>
      )}

      {canApprove && categoryGroupEntries.length > 1 ? (
        <div className="category-bulk-panel" aria-label="Actions groupées par cas métier">
          {categoryGroupEntries.map(([label, items]) => {
            const ids = items.map((run) => run.run_id);
            const allSelected = ids.every((id) => selectedRuns.has(id));
            return (
              <div className="category-bulk-row" key={label}>
                <div>
                  <strong>{label}</strong>
                  <span>{items.length} brouillon{items.length > 1 ? 's' : ''}</span>
                </div>
                <button type="button" onClick={() => selectCategory(items)} disabled={allSelected}>
                  <span className="material-symbols-outlined" aria-hidden="true">select_check_box</span>
                  <span>{allSelected ? 'Sélectionnée' : 'Sélectionner'}</span>
                </button>
                <button className="primary" type="button" onClick={() => handleBulk('approve', ids)}>
                  <span className="material-symbols-outlined" aria-hidden="true">send</span>
                  <span>Approuver ce cas</span>
                </button>
              </div>
            );
          })}
        </div>
      ) : null}

      <div id="pending-list">
        {!runs.length ? (
          <EmptyState message="Aucune approbation en attente." />
        ) : (
          runs.map((run) => (
            <ValidationCard
              key={run.run_id}
              run={run}
              selected={selectedRuns.has(run.run_id)}
              onToggleSelect={toggleSelect}
              editedFields={editedFields[run.run_id] || {}}
              onFieldChange={handleFieldChange}
              feedbackMessages={feedbackThreads[run.run_id] || []}
              feedbackDraft={feedbackDrafts[run.run_id] || ''}
              onFeedbackDraftChange={handleFeedbackDraftChange}
              busy={busyRuns.has(run.run_id)}
              onDecision={(command, options) => handleDecision(command, run.run_id, options)}
              isActive={activeRunId === run.run_id}
            />
          ))
        )}
      </div>
    </>
  );
}
