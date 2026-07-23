import { useEffect, useRef, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { Pager } from '../../components/ui/Pager';
import { useInstance } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { useDialog } from '../../contexts/DialogContext';
import { usePager } from '../../hooks/usePager';
import {
  useCategoriesQuery,
  useSaveCategoriesYaml,
  useSaveCategoryEdit,
  useDuplicateCategory,
  useDeleteCategory,
  useTestCategoryMatch,
} from '../../api/queries';
import { compactText } from '../../utils/format';
import {
  buildWorkflowYamlSnippet,
  appendWorkflowBlock,
  setCategoriesYamlEnabled,
  workflowActionLabel,
  workflowInstructionsSummary,
  splitDirectoryValues,
} from '../../utils/workflowYaml';

const PAGE_SIZE = 5;
const EMPTY_FORM = {
  name: '', keywords: '', policy: 'auto_draft', priority: 'normal', owner: '', approver: '', routeTo: '', template: '',
  sla: '', requiredData: '', escalation: '', blockedCases: '', askForMissing: false,
};

function instructionsFromForm(form) {
  const requiredData = splitDirectoryValues(form.requiredData);
  const blockedCases = splitDirectoryValues(form.blockedCases);
  if (!form.sla.trim() && !requiredData.length && !form.escalation.trim() && !blockedCases.length && !form.askForMissing) return null;
  return {
    sla: form.sla.trim() || null,
    required_data: requiredData,
    escalation: form.escalation.trim() || null,
    blocked_cases: blockedCases,
    ask_for_missing: form.askForMissing,
  };
}

export default function CategoriesPage() {
  const { hasRole } = useInstance();
  const { setStatus } = useStatus();
  const { confirmDialog } = useDialog();
  const canManage = hasRole('owner');

  const [form, setForm] = useState(EMPTY_FORM);
  const [editingName, setEditingName] = useState('');
  const [yamlText, setYamlText] = useState('');
  const [testAuthor, setTestAuthor] = useState('');
  const [testSubject, setTestSubject] = useState('');
  const [testResult, setTestResult] = useState(null);
  const [testError, setTestError] = useState('');
  const pager = usePager(0);
  const announcedInitialLoad = useRef(false);
  const announcedError = useRef(null);

  const query = useCategoriesQuery();
  const saveYaml = useSaveCategoriesYaml();
  const saveEdit = useSaveCategoryEdit();
  const duplicateCategory = useDuplicateCategory();
  const deleteCategory = useDeleteCategory();
  const testMatch = useTestCategoryMatch();

  const parsed = query.data?.parsed;
  const categories = parsed?.categories || [];
  const templates = parsed?.templates || [];
  const contacts = parsed?.contacts || [];

  useEffect(() => {
    if (!query.data) return;
    setYamlText(query.data.categories_yaml || '');
    if (!announcedInitialLoad.current) {
      announcedInitialLoad.current = true;
      setStatus('Workflows chargés.', 'ok');
    }
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!query.error || announcedError.current === query.error.message) return;
    announcedError.current = query.error.message;
    setStatus(`Impossible de charger les workflows : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const resetForm = () => { setEditingName(''); setForm(EMPTY_FORM); };

  const startEdit = (category) => {
    setEditingName(category.name);
    const instructions = category.instructions || {};
    setForm({
      name: category.display_name || category.name,
      keywords: (category.when?.subject_contains || []).join(', '),
      policy: category.policy || 'notify',
      priority: category.priority || 'normal',
      owner: category.owner || '',
      approver: category.approver || '',
      routeTo: (category.route_to || []).join(', '),
      template: '',
      sla: instructions.sla || '',
      requiredData: (instructions.required_data || []).join(', '),
      escalation: instructions.escalation || '',
      blockedCases: (instructions.blocked_cases || []).join(', '),
      askForMissing: Boolean(instructions.ask_for_missing),
    });
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (editingName) {
      const routeTargets = splitDirectoryValues(form.routeTo);
      const keywords = splitDirectoryValues(form.keywords);
      const currentEnabled = categories.find((item) => item.name === editingName)?.enabled !== false;
      const payload = {
        display_name: form.name.trim() || editingName,
        enabled: currentEnabled,
        priority: form.priority,
        policy: form.policy,
        owner: form.owner.trim() || null,
        approver: form.approver.trim() || null,
        route_to: routeTargets,
        instructions: instructionsFromForm(form),
        when: { subject_contains: keywords },
        template: null,
      };
      try {
        await saveEdit.mutateAsync({ name: editingName, payload });
        setStatus(`Workflow « ${payload.display_name} » mis à jour.`, 'ok');
        resetForm();
      } catch (error) {
        setStatus(`Impossible de mettre à jour le workflow : ${error.message}`, 'error');
      }
      return;
    }
    if (!form.name.trim() || !form.keywords.trim()) return;
    const { categoryBlock, templateBlock } = buildWorkflowYamlSnippet({
      name: form.name.trim(),
      keywords: form.keywords.trim(),
      policy: form.policy,
      priority: form.priority,
      templateBody: form.template.trim(),
      owner: form.owner.trim(),
      approver: form.approver.trim(),
      routeTo: form.routeTo.trim(),
      instructions: instructionsFromForm(form),
    });
    let nextYaml = appendWorkflowBlock(yamlText, 'categories', categoryBlock);
    if (templateBlock) nextYaml = appendWorkflowBlock(nextYaml, 'templates', templateBlock);
    nextYaml = nextYaml.trimEnd() + '\n';
    setYamlText(nextYaml);
    try {
      await saveYaml.mutateAsync(nextYaml);
      setStatus(`Workflow « ${form.name.trim()} » ajouté.`, 'ok');
      resetForm();
    } catch (error) {
      setStatus(`Impossible d'enregistrer les workflows : ${error.message}`, 'error');
    }
  };

  const categoryUpdatePayload = (category, overrides = {}) => ({
    display_name: category.display_name || category.name,
    enabled: category.enabled !== false,
    priority: category.priority || 'normal',
    policy: category.policy || 'notify',
    owner: category.owner || null,
    approver: category.approver || null,
    route_to: Array.isArray(category.route_to) ? category.route_to : [],
    instructions: category.instructions || null,
    when: category.when || null,
    template: category.template || null,
    ...overrides,
  });

  const handleToggleEnabled = async (category) => {
    const enabled = category.enabled === false;
    try {
      await saveEdit.mutateAsync({ name: category.name, payload: categoryUpdatePayload(category, { enabled }) });
      setStatus(`Workflow ${category.name} ${enabled ? 'activé' : 'désactivé'}.`, 'ok');
    } catch (error) {
      setStatus(`Impossible de ${enabled ? 'activer' : 'désactiver'} le workflow : ${error.message}`, 'error');
    }
  };

  const handleToggleGlobalEnabled = async () => {
    if (!canManage) return;
    const nextYaml = setCategoriesYamlEnabled(yamlText, !parsed?.enabled);
    setYamlText(nextYaml);
    try {
      await saveYaml.mutateAsync(nextYaml);
      setStatus(`Workflows ${!parsed?.enabled ? 'activés' : 'désactivés'}.`, 'ok');
    } catch (error) {
      setStatus(`Impossible d'enregistrer les workflows : ${error.message}`, 'error');
    }
  };

  const handleDuplicate = async (name) => {
    try {
      const result = await duplicateCategory.mutateAsync(name);
      setStatus(`Workflow dupliqué sous « ${result.new_name} ».`, 'ok');
    } catch (error) {
      setStatus(`Impossible de dupliquer le workflow : ${error.message}`, 'error');
    }
  };

  const handleDelete = async (name) => {
    const confirmed = await confirmDialog({
      title: 'Supprimer le workflow',
      message: `Supprimer le workflow « ${name} » ? Irréversible.`,
      confirmLabel: 'Supprimer le workflow',
      confirmIcon: 'delete',
      variant: 'danger',
    });
    if (!confirmed) return;
    try {
      await deleteCategory.mutateAsync(name);
      if (editingName === name) resetForm();
      setStatus(`Workflow « ${name} » supprimé.`, 'ok');
    } catch (error) {
      setStatus(`Impossible de supprimer le workflow : ${error.message}`, 'error');
    }
  };

  const handleTestMatch = async () => {
    setTestError('');
    try {
      const result = await testMatch.mutateAsync({ author: testAuthor.trim(), subject: testSubject.trim() });
      setTestResult(result);
    } catch (error) {
      setTestResult(null);
      setTestError(error.message);
    }
  };

  const handleSaveYamlRaw = async () => {
    try {
      await saveYaml.mutateAsync(yamlText);
      setStatus('Workflows enregistrés.', 'ok');
    } catch (error) {
      setStatus(`Impossible d'enregistrer les workflows : ${error.message}`, 'error');
    }
  };

  const pageCategories = categories.slice(pager.page * PAGE_SIZE, pager.page * PAGE_SIZE + PAGE_SIZE);
  const hasMore = (pager.page + 1) * PAGE_SIZE < categories.length;
  const activeCount = categories.filter((item) => item.enabled !== false).length;

  return (
    <>
      <PageHeading view="categories" />
      <div className="toolbar">
        <button type="button" onClick={() => query.refetch()}>
          <span className="material-symbols-outlined" aria-hidden="true">sync</span><span>Charger les workflows</span>
        </button>
        <button className="primary" type="button" onClick={handleSaveYamlRaw}>
          <span className="material-symbols-outlined" aria-hidden="true">save</span><span>Enregistrer les workflows</span>
        </button>
        <span className="counter">{query.data?.storage || 'default-instance-yaml'}</span>
      </div>
      <div className="notice"><strong>Ordre de correspondance :</strong> les contacts (e-mail exact, puis domaine) sont évalués avant les prédicats sujet/expéditeur des workflows. Un domaine de contact large peut donc primer sur un mot-clé de sujet ; préférez les e-mails exacts sauf si ce comportement est voulu.</div>

      <Card className="workflow-builder">
        <div className="card-header">
          <div>
            <h2>{editingName ? `Modifier le workflow : ${form.name}` : 'Ajouter un workflow'}</h2>
            <div className="meta">Créez un playbook métier sans éditer le YAML manuellement.</div>
          </div>
        </div>
        <form className="workflow-form" onSubmit={handleSubmit}>
          <label><span>Cas métier</span><input value={form.name} disabled={Boolean(editingName)} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Attestation de travail" required /></label>
          <label><span>Mots-clés déclencheurs</span><input value={form.keywords} onChange={(e) => setForm({ ...form, keywords: e.target.value })} placeholder="attestation, certificat de travail" required /></label>
          <label><span>Action</span>
            <select value={form.policy} onChange={(e) => setForm({ ...form, policy: e.target.value })}>
              <option value="auto_draft">Rédiger une réponse à approuver</option>
              <option value="notify">Notifier le propriétaire</option>
              <option value="organize">Étiqueter / classer</option>
              <option value="ignore">Ignorer ou archiver</option>
            </select>
          </label>
          <label><span>Priorité</span>
            <select value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>
              <option value="normal">Normal</option>
              <option value="urgent">Urgent</option>
              <option value="low">Basse</option>
            </select>
          </label>
          <label><span>Propriétaire</span><input value={form.owner} onChange={(e) => setForm({ ...form, owner: e.target.value })} placeholder="HR team or hr@company.com" /></label>
          <label><span>Approbateur</span><input value={form.approver} onChange={(e) => setForm({ ...form, approver: e.target.value })} placeholder="manager@company.com" /></label>
          <label className="workflow-wide"><span>Acheminer vers</span><input value={form.routeTo} onChange={(e) => setForm({ ...form, routeTo: e.target.value })} placeholder="hr, finance ou accounting@company.com" /></label>
          {!editingName && (
            <label className="workflow-wide"><span>Instructions de réponse / modèle</span>
              <textarea value={form.template} onChange={(e) => setForm({ ...form, template: e.target.value })} placeholder={'Bonjour {{name}},\n\nMerci pour votre demande...\n\nBien cordialement,'} />
            </label>
          )}
          <div className="workflow-wide instructions-fieldset">
            <strong>Instructions opérationnelles (optionnel)</strong>
            <div className="meta">Transmis à l'agent comme contexte cadré pour ce workflow uniquement — pas un texte libre.</div>
            <label><span>SLA</span><input value={form.sla} onChange={(e) => setForm({ ...form, sla: e.target.value })} placeholder="Répondre sous 24h" /></label>
            <label><span>Données requises</span><input value={form.requiredData} onChange={(e) => setForm({ ...form, requiredData: e.target.value })} placeholder="numéro de commande, date d'achat" /></label>
            <label><span>Règle d'escalade</span><input value={form.escalation} onChange={(e) => setForm({ ...form, escalation: e.target.value })} placeholder="Notifier le responsable finance pour les remboursements > 1000 EUR" /></label>
            <label><span>Cas bloqués</span><input value={form.blockedCases} onChange={(e) => setForm({ ...form, blockedCases: e.target.value })} placeholder="remboursements demandés après 30 jours" /></label>
            <label className="workflow-checkbox"><input type="checkbox" checked={form.askForMissing} onChange={(e) => setForm({ ...form, askForMissing: e.target.checked })} /><span>Demander à l'expéditeur les données manquantes</span></label>
          </div>
          <div className="workflow-form-actions workflow-wide">
            <button className="primary" type="submit">
              <span className="material-symbols-outlined" aria-hidden="true">{editingName ? 'save' : 'add'}</span>
              <span>{editingName ? 'Enregistrer les modifications' : 'Ajouter le workflow'}</span>
            </button>
            {editingName && <button className="ghost" type="button" onClick={resetForm}>Annuler la modification</button>}
          </div>
        </form>
      </Card>

      <Card className="workflow-test-match">
        <div className="card-header">
          <div><h2>Tester avec un e-mail</h2><div className="meta">Prévisualisez quel workflow un e-mail correspondrait, sans rien envoyer.</div></div>
        </div>
        <div className="workflow-form">
          <label><span>De</span><input value={testAuthor} onChange={(e) => setTestAuthor(e.target.value)} placeholder="client@example.com" /></label>
          <label><span>Sujet</span><input value={testSubject} onChange={(e) => setTestSubject(e.target.value)} placeholder="Demande de remboursement commande 123" /></label>
          <div className="workflow-form-actions workflow-wide">
            <button type="button" onClick={handleTestMatch}>
              <span className="material-symbols-outlined" aria-hidden="true">play_arrow</span><span>Tester la correspondance</span>
            </button>
          </div>
          <div className="workflow-wide rules-preview">
            {testError ? <div className="empty">{`Impossible de tester la correspondance : ${testError}`}</div> : !testResult ? 'Aucun test lancé.' : !testResult.matched ? (
              <div className="empty">Aucun workflow ne correspond à cet e-mail — il passerait au triage IA.</div>
            ) : (
              <div className="rule-row workflow-row">
                <div>
                  <strong>{testResult.category_display_name || testResult.category}</strong>
                  <span>{workflowActionLabel(testResult.policy)} / {testResult.priority || 'normal'}{testResult.route_to?.length ? ` / route: ${testResult.route_to.filter(Boolean).join(', ')}` : ''}</span>
                  {workflowInstructionsSummary(testResult.instructions) && <span>{workflowInstructionsSummary(testResult.instructions)}</span>}
                </div>
              </div>
            )}
          </div>
        </div>
      </Card>

      <Card className="editor-card">
        <strong>Workflows configurés</strong>
        <div className="rules-preview">
          <div className="rules-toggle-row">
            {canManage && (
              <button className={`toggle-pill ${parsed?.enabled ? 'on' : 'off'}`} type="button" onClick={handleToggleGlobalEnabled}>
                <span className="toggle-dot" aria-hidden="true" />
                <span>Moteur workflows : {parsed?.enabled ? 'Activé' : 'Désactivé'}</span>
              </button>
            )}
            <span className="counter">{activeCount}/{categories.length} actifs</span>
          </div>
          <div className="summary-grid compact-summary">
            <div><strong>{categories.length}</strong><span>Cas métier</span></div>
            <div><strong>{templates.length}</strong><span>Modèles</span></div>
            <div><strong>{contacts.length}</strong><span>Acteurs</span></div>
            <div><strong>{parsed?.enabled ? 'On' : 'Off'}</strong><span>Correspondance</span></div>
          </div>
          <div className="rule-list directory-list">
            {!categories.length ? <div className="empty">Aucun workflow configuré.</div> : (
              <>
                {pageCategories.map((category) => {
                  const policy = category.policy || 'notify';
                  const trigger = category.when?.subject_contains?.length
                    ? `Sujet : ${category.when.subject_contains.join(', ')}`
                    : category.when?.sender_contains?.length
                      ? `Expéditeur : ${category.when.sender_contains.join(', ')}`
                      : 'Correspondance IA';
                  const routeTargets = Array.isArray(category.route_to) ? category.route_to.filter(Boolean) : [];
                  return (
                    <div className={`directory-row workflow-directory-row ${category.enabled === false ? 'inactive' : ''}`.trim()} key={category.name}>
                      <div className="directory-icon"><span className="material-symbols-outlined" aria-hidden="true">account_tree</span></div>
                      <div className="directory-main">
                        <strong title={category.display_name || category.name}>{category.display_name || category.name}</strong>
                        <span title={trigger}>{compactText(trigger, 100)}</span>
                        <div className="mini-chip-row">
                          <span className="mini-chip">{category.enabled === false ? 'désactivé' : 'actif'}</span>
                          <span className="mini-chip">{category.priority || 'normal'}</span>
                          <span className="mini-chip">{workflowActionLabel(policy)}</span>
                          {category.owner && <span className="mini-chip">{`owner: ${category.owner}`}</span>}
                          {category.approver && <span className="mini-chip">{`approver: ${category.approver}`}</span>}
                          {routeTargets.length > 0 && <span className="mini-chip">{`route: ${compactText(routeTargets.join(', '), 42)}`}</span>}
                        </div>
                      </div>
                      {canManage && (
                        <div className="directory-actions">
                          <button className={`toggle-pill ${category.enabled === false ? 'off' : 'on'}`} type="button" onClick={() => handleToggleEnabled(category)}>
                            <span className="toggle-dot" aria-hidden="true" /><span>{category.enabled === false ? 'Activer' : 'Désactiver'}</span>
                          </button>
                          <button type="button" onClick={() => startEdit(category)}>
                            <span className="material-symbols-outlined" aria-hidden="true">edit</span><span>Modifier</span>
                          </button>
                          <button type="button" onClick={() => handleDuplicate(category.name)}>
                            <span className="material-symbols-outlined" aria-hidden="true">content_copy</span><span>Cloner</span>
                          </button>
                          <button className="danger" type="button" onClick={() => handleDelete(category.name)}>
                            <span className="material-symbols-outlined" aria-hidden="true">delete</span><span>Supprimer</span>
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
                <Pager page={pager.page} hasMore={hasMore} onPrev={pager.prev} onNext={pager.next} />
              </>
            )}
          </div>
        </div>
        <details className="advanced-yaml">
          <summary>Éditer le YAML brut (avancé)</summary>
          <p className="muted">Réservé au réglage fin (commentaires, champs non exposés par le formulaire). « Enregistrer les workflows » persiste ce contenu.</p>
          <textarea spellCheck={false} value={yamlText} onChange={(e) => setYamlText(e.target.value)} />
        </details>
      </Card>
    </>
  );
}
