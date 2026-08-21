import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { SyncProgressBar } from '../../components/ui/SyncProgressBar';
import { useStatus } from '../../contexts/StatusContext';
import { useApi } from '../../api/useApi';
import {
  usePersonaQuery,
  useSavePersona,
  useSuggestPersona,
  useConfigQuery,
  useSaveConfig,
  useInstanceSetupQuery,
} from '../../api/queries';

const EMPTY_PERSONA = {
  identite: { prenom: '', nom: '', fonction: '', entreprise: '', langue_reponse: 'fr' },
  mission: '',
  perimetre: { repond_a: [], ne_repond_jamais_a: [], escalade_vers: '' },
  ton: 'professionnel',
};

const TONE_LABELS = { professionnel: 'Professionnel', chaleureux: 'Chaleureux', direct: 'Direct', formel: 'Formel' };
const LANGUE_LABELS = { fr: 'Français', en: 'Anglais', auto: 'Langue de l’expéditeur' };

function ChipsField({ label, items, onAdd, onRemove, placeholder, suggestions = [] }) {
  const [draft, setDraft] = useState('');
  const [focused, setFocused] = useState(false);
  const inputId = useId();

  const commit = () => {
    const value = draft.trim().replace(/,+$/, '').trim();
    if (value && !items.includes(value)) onAdd(value);
    setDraft('');
  };
  const options = useMemo(() => {
    const query = draft.trim().toLowerCase();
    const seen = new Set();
    return suggestions
      .map((value) => String(value || '').trim())
      .filter(Boolean)
      .filter((value) => {
        const key = value.toLowerCase();
        if (seen.has(key)) return false;
        seen.add(key);
        return !query || key.includes(query);
      })
      .slice(0, 8);
  }, [draft, suggestions]);
  const showOptions = focused && options.length > 0;

  const pick = (value) => {
    setDraft('');
    if (!items.includes(value)) onAdd(value);
  };

  return (
    <div className="chips-field">
      <span>{label}</span>
      <div className="chips">
        {items.map((item, index) => (
          <span className="chip" key={item}>
            {item}
            <button type="button" className="chip-remove" aria-label={`Retirer ${item}`} onClick={() => onRemove(index)}>×</button>
          </span>
        ))}
      </div>
      <div className="chips-input-wrap">
        <input
          id={inputId}
          placeholder={placeholder}
          value={draft}
          onFocus={() => setFocused(true)}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ',') { event.preventDefault(); commit(); }
            if (event.key === 'Escape') setFocused(false);
          }}
          onBlur={() => {
            commit();
            window.setTimeout(() => setFocused(false), 120);
          }}
          aria-autocomplete="list"
          aria-expanded={showOptions}
          aria-controls={`${inputId}-options`}
        />
        {showOptions ? (
          <div className="chips-suggestion-menu" id={`${inputId}-options`} role="listbox">
            {options.map((option) => (
              <button
                type="button"
                key={option}
                role="option"
                aria-selected={items.includes(option)}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => pick(option)}
              >
                {option}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function suggestionRows(suggestion) {
  const rows = [];
  const identite = [suggestion.prenom, suggestion.nom].filter(Boolean).join(' ');
  if (identite) rows.push({ field: 'identite', label: 'Vous êtes', value: identite });
  if (suggestion.fonction) rows.push({ field: 'fonction', label: 'Votre fonction', value: suggestion.fonction });
  if (suggestion.entreprise) rows.push({ field: 'entreprise', label: 'Votre entreprise', value: suggestion.entreprise });
  if (suggestion.ton) rows.push({ field: 'ton', label: 'Votre ton', value: TONE_LABELS[suggestion.ton] || suggestion.ton });
  if (suggestion.langue_reponse) rows.push({ field: 'langue_reponse', label: 'Vous écrivez en', value: LANGUE_LABELS[suggestion.langue_reponse] || suggestion.langue_reponse });
  if (suggestion.mission) rows.push({ field: 'mission', label: 'Votre mission', value: suggestion.mission });
  if (suggestion.repond_a?.length) rows.push({ field: 'repond_a', label: 'Vous répondez à', value: suggestion.repond_a.join(', ') });
  return rows;
}

export default function PersonaPage() {
  const { setStatus } = useStatus();
  const { api } = useApi();

  const [persona, setPersona] = useState(EMPTY_PERSONA);
  const [compiled, setCompiled] = useState(null);
  const [progress, setProgress] = useState({ mode: 'idle', message: '', hidden: true });
  const [analyzing, setAnalyzing] = useState(false);
  const [suggestion, setSuggestion] = useState(null);
  const [suggestionChecks, setSuggestionChecks] = useState({});
  const [styleNote, setStyleNote] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false);
  const announcedInitialLoad = useRef(false);
  const backgroundRef = useRef(null);
  const triageRef = useRef(null);
  const responseRef = useRef(null);

  const query = usePersonaQuery();
  const savePersona = useSavePersona();
  const suggestPersona = useSuggestPersona();
  const configQuery = useConfigQuery(showAdvanced);
  const saveConfig = useSaveConfig();
  const setupQuery = useInstanceSetupQuery();
  const announcedError = useRef(null);
  const seededSuggestionRef = useRef(false);

  useEffect(() => {
    if (!query.data) return;
    const data = query.data;
    setPersona({
      identite: { ...EMPTY_PERSONA.identite, ...data.identite },
      mission: data.mission || '',
      perimetre: { ...EMPTY_PERSONA.perimetre, ...data.perimetre },
      ton: data.ton || 'professionnel',
    });
    setCompiled(data.compiled || null);
    if (!announcedInitialLoad.current) {
      announcedInitialLoad.current = true;
      setStatus('Persona chargé.', 'ok');
    }
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleReloadPersona = async () => {
    try {
      await query.refetch({ throwOnError: true });
      setStatus('Persona chargé.', 'ok');
    } catch (error) {
      setStatus(`Impossible de charger le persona : ${error.message}`, 'error');
    }
  };

  useEffect(() => {
    if (!query.error || announcedError.current === query.error.message) return;
    announcedError.current = query.error.message;
    setStatus(`Impossible de charger le persona : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  // The onboarding pipeline already runs suggest_persona and computes a real
  // suggestion, but nothing ever surfaced it — it just sat in the setup
  // record while this page's own "Analyser" button re-ran the same analysis
  // from scratch. Seed it once, only while the persona is still genuinely
  // empty and the user hasn't triggered their own analysis this session.
  useEffect(() => {
    if (seededSuggestionRef.current || suggestion || !query.data) return;
    const p = query.data;
    const isEmpty = !p.identite?.prenom && !p.identite?.nom && !p.identite?.fonction
      && !p.identite?.entreprise && !p.mission && !(p.perimetre?.repond_a?.length);
    if (!isEmpty) return;
    const step = (setupQuery.data?.steps || []).find((s) => s.step_key === 'suggest_persona');
    const onboardingSuggestion = step?.detail?.suggestion;
    if (!onboardingSuggestion) return;
    seededSuggestionRef.current = true;
    setSuggestion(onboardingSuggestion);
    setSuggestionChecks(Object.fromEntries(suggestionRows(onboardingSuggestion).map((row) => [row.field, true])));
    setStatus('Suggestion issue de la configuration initiale — validez les champs ci-dessous.', 'ok');
  }, [query.data, setupQuery.data, suggestion]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSave = async () => {
    try {
      const saved = await savePersona.mutateAsync(persona);
      setCompiled(saved.compiled || null);
      setStatus('Persona enregistré — instructions régénérées.', 'ok');
    } catch (error) {
      setStatus(`Impossible d’enregistrer le persona : ${error.message}`, 'error');
    }
  };

  const handleAnalyze = async () => {
    setAnalyzing(true);
    setStyleNote('');
    setProgress({ mode: 'syncing', message: 'Lecture de vos e-mails envoyés et reçus…', hidden: false });
    setStatus('Analyse de la boîte en cours…');
    try {
      const [suggestResult, styleResult] = await Promise.allSettled([
        suggestPersona.mutateAsync(),
        api('/api/agent/style/learn', { method: 'POST' }),
      ]);
      if (suggestResult.status === 'rejected') throw suggestResult.reason;
      const result = suggestResult.value;
      const nextSuggestion = result.suggestion || null;
      setSuggestion(nextSuggestion);
      setSuggestionChecks(Object.fromEntries(suggestionRows(nextSuggestion || {}).map((row) => [row.field, true])));
      if (styleResult.status === 'fulfilled') {
        setStyleNote(`✓ Style d’écriture appris à partir de ${styleResult.value.sample_count} e-mail(s) envoyé(s) — déjà actif pour les brouillons.`);
      } else {
        setStyleNote(`Style d’écriture non appris : ${styleResult.reason?.message || 'erreur'}`);
      }
      if (nextSuggestion) {
        setStatus(`Analyse terminée (${result.sent_sample_count} envoyés, ${result.received_sample_count} reçus).`, 'ok');
        setProgress({ mode: 'ok', message: 'Analyse terminée — validez les champs ci-dessous.', hidden: false });
        setTimeout(() => setProgress((p) => ({ ...p, hidden: true })), 2500);
      } else {
        setStatus('Analyse terminée — aucune suggestion exploitable trouvée.', 'ok');
        setProgress((p) => ({ ...p, hidden: true }));
      }
    } catch (error) {
      setStatus(`Impossible d’analyser la boîte : ${error.message}`, 'error');
      setProgress({ mode: 'error', message: `Analyse impossible : ${error.message}`, hidden: false });
      setTimeout(() => setProgress((p) => ({ ...p, hidden: true })), 4000);
    } finally {
      setAnalyzing(false);
    }
  };

  const applySuggestion = () => {
    if (!suggestion) return;
    const next = { ...persona, identite: { ...persona.identite }, perimetre: { ...persona.perimetre } };
    if (suggestionChecks.identite) {
      next.identite.prenom = suggestion.prenom || next.identite.prenom;
      next.identite.nom = suggestion.nom || next.identite.nom;
    }
    if (suggestionChecks.fonction && suggestion.fonction) next.identite.fonction = suggestion.fonction;
    if (suggestionChecks.entreprise && suggestion.entreprise) next.identite.entreprise = suggestion.entreprise;
    if (suggestionChecks.ton && suggestion.ton) next.ton = suggestion.ton;
    if (suggestionChecks.langue_reponse && suggestion.langue_reponse) next.identite.langue_reponse = suggestion.langue_reponse;
    if (suggestionChecks.mission && suggestion.mission) next.mission = suggestion.mission;
    if (suggestionChecks.repond_a && suggestion.repond_a?.length) {
      next.perimetre.repond_a = [...new Set([...next.perimetre.repond_a, ...suggestion.repond_a])];
    }
    setPersona(next);
    setSuggestion(null);
    setStatus('Champs appliqués — cliquez sur « Enregistrer le persona » pour activer.', 'ok');
  };

  const handleSaveConfig = async () => {
    try {
      const current = configQuery.data || (await api('/api/agent/config'));
      const next = {
        ...current,
        agent: {
          ...current.agent,
          background: backgroundRef.current?.value ?? current.agent?.background ?? '',
          triage_instructions: triageRef.current?.value ?? current.agent?.triage_instructions ?? '',
          response_preferences: responseRef.current?.value ?? current.agent?.response_preferences ?? '',
        },
      };
      await saveConfig.mutateAsync(next);
      setStatus('Configuration enregistrée.', 'ok');
    } catch (error) {
      setStatus(`Impossible d’enregistrer la configuration : ${error.message}`, 'error');
    }
  };

  const rows = suggestion ? suggestionRows(suggestion) : [];
  const scopeSuggestions = useMemo(() => ([
    ...(persona.perimetre.repond_a || []),
    ...(persona.perimetre.ne_repond_jamais_a || []),
    ...(suggestion?.repond_a || []),
    ...(suggestion?.ne_repond_jamais_a || []),
  ]), [persona.perimetre.repond_a, persona.perimetre.ne_repond_jamais_a, suggestion]);

  return (
    <>
      <PageHeading view="config" />
      <Card className="persona-hero">
        <div className="persona-hero-text">
          <strong>Qui êtes-vous ?</strong>
          <p className="muted">L’agent lit vos e-mails envoyés et reçus pour deviner votre identité, votre ton, votre mission et vos interlocuteurs — puis apprend votre style d’écriture. Vous validez chaque champ avant l’enregistrement.</p>
        </div>
        <button className="primary persona-hero-button" type="button" disabled={analyzing} onClick={handleAnalyze}>
          <span className="material-symbols-outlined" aria-hidden="true">auto_awesome</span>
          <span>Apprendre qui je suis depuis ma boîte</span>
        </button>
        {!progress.hidden && <SyncProgressBar label="Analyse de votre boîte" mode={progress.mode} message={progress.message} />}
      </Card>

      {suggestion && rows.length > 0 && (
        <Card className="persona-suggestion-card">
          <strong>Voici ce que j’ai compris — décochez ce qui est faux</strong>
          <p className="muted">Rien n’est appliqué sans votre confirmation.</p>
          <div className="persona-review-list">
            {rows.map((row) => (
              <label className="persona-review-row" key={row.field}>
                <input
                  type="checkbox"
                  checked={Boolean(suggestionChecks[row.field])}
                  onChange={(e) => setSuggestionChecks({ ...suggestionChecks, [row.field]: e.target.checked })}
                />
                <span className="persona-review-label">{row.label}</span>
                <span className="persona-review-value">{row.value}</span>
              </label>
            ))}
          </div>
          {styleNote && <p className="muted">{styleNote}</p>}
          <div className="toolbar">
            <button className="primary" type="button" onClick={applySuggestion}>
              <span className="material-symbols-outlined" aria-hidden="true">check</span><span>Appliquer la sélection</span>
            </button>
            <button type="button" onClick={() => setSuggestion(null)}>Ignorer</button>
          </div>
        </Card>
      )}

      <div className="toolbar">
        <button type="button" disabled={query.isFetching} onClick={handleReloadPersona}>
          <span className={`material-symbols-outlined${query.isFetching ? ' spin' : ''}`} aria-hidden="true">sync</span>
          <span>{query.isFetching ? 'Chargement…' : 'Charger le persona'}</span>
        </button>
        <button className="primary" type="button" onClick={handleSave}>
          <span className="material-symbols-outlined" aria-hidden="true">save</span><span>Enregistrer le persona</span>
        </button>
      </div>

      <form className="card persona-card" autoComplete="off" onSubmit={(e) => e.preventDefault()}>
        <fieldset className="persona-identity">
          <legend>Identité</legend>
          <div className="persona-grid">
            <label>Prénom <input placeholder="Karim" value={persona.identite.prenom} onChange={(e) => setPersona({ ...persona, identite: { ...persona.identite, prenom: e.target.value } })} /></label>
            <label>Nom <input placeholder="Bellagnech" value={persona.identite.nom} onChange={(e) => setPersona({ ...persona, identite: { ...persona.identite, nom: e.target.value } })} /></label>
            <label>Fonction <input placeholder="Chargé RH" value={persona.identite.fonction} onChange={(e) => setPersona({ ...persona, identite: { ...persona.identite, fonction: e.target.value } })} /></label>
            <label>Entreprise <input placeholder="Agora" value={persona.identite.entreprise} onChange={(e) => setPersona({ ...persona, identite: { ...persona.identite, entreprise: e.target.value } })} /></label>
          </div>
        </fieldset>
        <label className="persona-mission">Mission (une phrase)
          <input placeholder="répond aux candidats et aux demandes RH" value={persona.mission} onChange={(e) => setPersona({ ...persona, mission: e.target.value })} />
        </label>
        <div className="persona-grid">
          <ChipsField
            label="Je réponds à"
            items={persona.perimetre.repond_a}
            suggestions={scopeSuggestions}
            placeholder="candidats, clients… (Entrée pour ajouter)"
            onAdd={(value) => setPersona({ ...persona, perimetre: { ...persona.perimetre, repond_a: [...persona.perimetre.repond_a, value] } })}
            onRemove={(index) => setPersona({ ...persona, perimetre: { ...persona.perimetre, repond_a: persona.perimetre.repond_a.filter((_, i) => i !== index) } })}
          />
          <ChipsField
            label="Je ne réponds jamais à"
            items={persona.perimetre.ne_repond_jamais_a}
            suggestions={scopeSuggestions}
            placeholder="newsletters, démarchage… (Entrée pour ajouter)"
            onAdd={(value) => setPersona({ ...persona, perimetre: { ...persona.perimetre, ne_repond_jamais_a: [...persona.perimetre.ne_repond_jamais_a, value] } })}
            onRemove={(index) => setPersona({ ...persona, perimetre: { ...persona.perimetre, ne_repond_jamais_a: persona.perimetre.ne_repond_jamais_a.filter((_, i) => i !== index) } })}
          />
        </div>
        <div className="persona-grid">
          <label>Escalade vers (email ou rôle)
            <input placeholder="direction@entreprise.com" value={persona.perimetre.escalade_vers} onChange={(e) => setPersona({ ...persona, perimetre: { ...persona.perimetre, escalade_vers: e.target.value } })} />
          </label>
          <label>Ton
            <select value={persona.ton} onChange={(e) => setPersona({ ...persona, ton: e.target.value })}>
              <option value="professionnel">Professionnel</option>
              <option value="chaleureux">Chaleureux</option>
              <option value="direct">Direct</option>
              <option value="formel">Formel</option>
            </select>
          </label>
          <label>Langue de réponse
            <select value={persona.identite.langue_reponse} onChange={(e) => setPersona({ ...persona, identite: { ...persona.identite, langue_reponse: e.target.value } })}>
              <option value="fr">Français</option>
              <option value="en">Anglais</option>
              <option value="auto">Langue de l’expéditeur</option>
            </select>
          </label>
        </div>
      </form>

      <details className="card persona-preview-card">
        <summary>Aperçu des instructions générées</summary>
        <p className="muted">Généré automatiquement à partir du formulaire — lecture seule.</p>
        <div className="persona-preview-grid">
          <div><strong>Contexte</strong><pre>{compiled?.background || ''}</pre></div>
          <div><strong>Instructions de triage</strong><pre>{compiled?.triage_instructions || ''}</pre></div>
          <div><strong>Préférences de réponse</strong><pre>{compiled?.response_preferences || ''}</pre></div>
        </div>
      </details>

      <details className="card persona-advanced-card" onToggle={(e) => setShowAdvanced(e.target.open)}>
        <summary>Mode avancé (édition directe des instructions)</summary>
        <p className="muted">L’enregistrement du persona régénère ces instructions.</p>
        <div className="toolbar">
          <button type="button" onClick={() => configQuery.refetch()}>
            <span className="material-symbols-outlined" aria-hidden="true">sync</span><span>Charger la config</span>
          </button>
          <button className="primary" type="button" onClick={handleSaveConfig}>
            <span className="material-symbols-outlined" aria-hidden="true">save</span><span>Enregistrer la config</span>
          </button>
        </div>
        <div className="editor-grid">
          <label className="card editor-card">
            <strong>Contexte</strong>
            <textarea ref={backgroundRef} spellCheck={false} defaultValue={configQuery.data?.agent?.background || ''} key={configQuery.dataUpdatedAt} />
          </label>
          <label className="card editor-card">
            <strong>Instructions de triage</strong>
            <textarea ref={triageRef} spellCheck={false} defaultValue={configQuery.data?.agent?.triage_instructions || ''} key={`t-${configQuery.dataUpdatedAt}`} />
          </label>
          <label className="card editor-card editor-card-wide">
            <strong>Préférences de réponse</strong>
            <textarea ref={responseRef} spellCheck={false} defaultValue={configQuery.data?.agent?.response_preferences || ''} key={`r-${configQuery.dataUpdatedAt}`} />
          </label>
        </div>
      </details>
    </>
  );
}
