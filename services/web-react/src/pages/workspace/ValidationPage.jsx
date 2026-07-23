import { useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { PageHeading } from '../../components/layout/PageHeading';
import { Pager } from '../../components/ui/Pager';
import { EmptyState } from '../../components/ui/EmptyState';
import ValidationCard from '../../components/domain/ValidationCard';
import { useInstance } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { useDialog } from '../../contexts/DialogContext';
import { useApi } from '../../api/useApi';
import { actionArgs } from '../../utils/format';
import {
  usePendingRunsQuery,
  useApproveRun,
  useRejectRun,
  useClaimRun,
  useAssignRun,
  useToneRun,
  useBulkDecision,
  useSyncGmail,
} from '../../api/queries';
import { useRunEvents } from '../../hooks/useRunEvents';

function feedbackMessageId() {
  return `feedback-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export default function ValidationPage() {
  const { hasRole } = useInstance();
  const { setStatus } = useStatus();
  const { confirmDialog, promptDialog } = useDialog();
  const { api, streamApi } = useApi();
  const queryClient = useQueryClient();
  const canApprove = hasRole('approver');

  const [page, setPage] = useState(0);
  const [selectedRuns, setSelectedRuns] = useState(new Set());
  const [activeRunId, setActiveRunId] = useState(null);
  const [editedFields, setEditedFields] = useState({});
  const [feedbackThreads, setFeedbackThreads] = useState({});
  const [feedbackDrafts, setFeedbackDrafts] = useState({});
  const [busyRuns, setBusyRuns] = useState(new Set());
  const announcedInitialLoad = useRef(false);

  const query = usePendingRunsQuery(page);
  useRunEvents();
  const approveRun = useApproveRun();
  const rejectRun = useRejectRun();
  const claimRun = useClaimRun();
  const assignRun = useAssignRun();
  const toneRun = useToneRun();
  const bulkDecision = useBulkDecision();
  const syncGmail = useSyncGmail();

  const runs = query.data?.runs || [];

  useEffect(() => {
    if (query.data && !announcedInitialLoad.current) {
      announcedInitialLoad.current = true;
      setStatus('Validation chargée.', 'ok');
    }
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (query.error) setStatus(`Impossible de charger la validation : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  // Prune selection/edits to runs still present.
  useEffect(() => {
    const present = new Set(runs.map((run) => run.run_id));
    setSelectedRuns((prev) => new Set([...prev].filter((id) => present.has(id))));
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

  const handleDecision = async (command, runId, options = {}) => {
    const run = runs.find((item) => item.run_id === runId);
    if (!run) return;

    if (command === 'tone') {
      setStatus('Reformulation en cours...');
      try {
        const result = await toneRun.mutateAsync({ runId, tone: options.tone });
        handleFieldChange(runId, result.field || options.field || 'content', result.content);
        setStatus('Brouillon reformulé — vérifiez avant d’approuver.', 'ok');
      } catch (error) {
        setStatus(`Échec de la reformulation : ${error.message}`, 'error');
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
        await assignRun.mutateAsync({ runId, assignee: value || null });
        setStatus(value ? `Assigné à ${value}.` : 'Assignation retirée.', 'ok');
      } catch (error) {
        setStatus(`Échec de l’assignation : ${error.message}`, 'error');
      }
      return;
    }

    if (command === 'claim') {
      setBusy(runId, true);
      try {
        await claimRun.mutateAsync(runId);
        setStatus('Pris en charge.', 'ok');
      } catch (error) {
        setStatus(`Échec de la prise en charge : ${error.message}`, 'error');
      } finally {
        setBusy(runId, false);
      }
      return;
    }

    if (command === 'accept') {
      setBusy(runId, true);
      try {
        await approveRun.mutateAsync({ runId, args: editedArgsFor(run) });
        setEditedFields((prev) => { const next = { ...prev }; delete next[runId]; return next; });
        setStatus('Envoyé.', 'ok');
      } catch (error) {
        setStatus(`Décision échouée : ${error.message}`, 'error');
      } finally {
        setBusy(runId, false);
      }
      return;
    }

    if (command === 'ignore') {
      setBusy(runId, true);
      try {
        await rejectRun.mutateAsync(runId);
        setStatus('E-mail ignoré.', 'ok');
      } catch (error) {
        setStatus(`Décision échouée : ${error.message}`, 'error');
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
          setStatus(`Décision échouée : ${fallbackError.message}`, 'error');
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

  const handleBulk = async (decision) => {
    const runIds = [...selectedRuns];
    if (!runIds.length) return;
    const confirmed = await confirmDialog({
      title: decision === 'approve' ? 'Approuver la sélection' : 'Rejeter la sélection',
      message: `${runIds.length} approbation(s) sélectionnée(s) — cette action s'applique à chacune individuellement.`,
      confirmLabel: decision === 'approve' ? 'Approuver' : 'Rejeter',
      variant: decision === 'approve' ? '' : 'danger',
    });
    if (!confirmed) return;
    try {
      const result = await bulkDecision.mutateAsync({ runIds, decision });
      const errors = (result.results || []).filter((item) => item.status === 'error');
      setSelectedRuns(new Set());
      setStatus(errors.length ? `${errors.length} échec(s) sur ${runIds.length}.` : `${runIds.length} décision(s) appliquée(s).`, errors.length ? 'error' : 'ok');
    } catch (error) {
      setStatus(`Action groupée échouée : ${error.message}`, 'error');
    }
  };

  const handleSync = async () => {
    setStatus('Vérification Gmail en cours...');
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

  return (
    <>
      <PageHeading view="validation" />
      <div className="toolbar">
        <button type="button" onClick={() => query.refetch()}>
          <span className="material-symbols-outlined" aria-hidden="true">refresh</span>
          <span>Actualiser</span>
        </button>
        <button className="primary" type="button" onClick={handleSync}>
          <span className="material-symbols-outlined" aria-hidden="true">mark_email_read</span>
          <span>Vérifier Gmail</span>
        </button>
        <span className="counter">{runs.length} en attente</span>
        <span className="kbd-legend">Raccourcis : j/k naviguer · a approuver · r rejeter · e retoucher · x sélectionner</span>
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
