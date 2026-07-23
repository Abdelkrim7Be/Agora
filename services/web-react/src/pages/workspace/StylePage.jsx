import { useEffect, useRef, useState } from 'react';
import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { SyncProgressBar } from '../../components/ui/SyncProgressBar';
import { useInstance } from '../../contexts/InstanceContext';
import { useStatus } from '../../contexts/StatusContext';
import { useDialog } from '../../contexts/DialogContext';
import { useStyleQuery, useSaveStyle, useLearnStyle, useClearStyle } from '../../api/queries';

function styleFieldsFromText(text) {
  const fields = { greeting: '', tone: '', signoff: '', length: '', phrases: [], dos: [], donts: [] };
  if (!text || !text.includes('Greeting:')) return fields;
  let listKey = null;
  text.split('\n').forEach((line) => {
    const trimmed = line.trim();
    if (trimmed.startsWith('Greeting:')) { fields.greeting = trimmed.slice(9).trim(); listKey = null; }
    else if (trimmed.startsWith('Tone:')) { fields.tone = trimmed.slice(5).trim(); listKey = null; }
    else if (trimmed.startsWith('Sign-off:')) { fields.signoff = trimmed.slice(9).trim(); listKey = null; }
    else if (trimmed.startsWith('Typical length:')) { fields.length = trimmed.slice(15).trim(); listKey = null; }
    else if (trimmed.startsWith('Do not:')) { listKey = 'donts'; }
    else if (trimmed.startsWith('Do:')) { listKey = 'dos'; }
    else if (trimmed.startsWith('Recurring phrases:')) { listKey = 'phrases'; }
    else if (listKey && trimmed.startsWith('- ') && !trimmed.includes('none observed')) {
      fields[listKey].push(trimmed.slice(2).trim());
    }
  });
  ['greeting', 'tone', 'signoff', 'length'].forEach((key) => {
    if (fields[key] === 'not specified') fields[key] = '';
  });
  return fields;
}

function bullets(value) {
  const lines = (value || '').split('\n').map((l) => l.trim()).filter(Boolean);
  return lines.length ? lines.map((l) => `- ${l}`).join('\n') : '- none observed';
}

function styleTextFromFields(fields, phrases) {
  return (
    `Greeting: ${fields.greeting.trim() || 'not specified'}\n` +
    `Tone: ${fields.tone.trim() || 'not specified'}\n` +
    `Sign-off: ${fields.signoff.trim() || 'not specified'}\n` +
    `Typical length: ${fields.length || 'not specified'}\n\n` +
    `Recurring phrases:\n${phrases.length ? phrases.map((p) => `- ${p}`).join('\n') : '- none observed'}\n\n` +
    `Do:\n${bullets(fields.dos)}\n\n` +
    `Do not:\n${bullets(fields.donts)}`
  );
}

function StyleProfile({ profile }) {
  if (!profile) return <div className="empty">Aucun profil appris chargé.</div>;
  const list = (items) => (items?.length ? items.map((item, i) => <li key={i}>{item}</li>) : <li className="muted">rien observé</li>);
  return (
    <>
      <div className="profile-grid">
        <div><span>Salutation</span><strong>{profile.greeting || 'non précisé'}</strong></div>
        <div><span>Ton</span><strong>{profile.tone || 'non précisé'}</strong></div>
        <div><span>Formule de clôture</span><strong>{profile.sign_off || 'non précisé'}</strong></div>
        <div><span>Longueur</span><strong>{profile.typical_length || 'non précisé'}</strong></div>
      </div>
      <h3>Expressions récurrentes</h3><ul>{list(profile.recurring_phrases)}</ul>
      <h3>À faire</h3><ul>{list(profile.dos)}</ul>
      <h3>À éviter</h3><ul>{list(profile.donts)}</ul>
    </>
  );
}

export default function StylePage() {
  const { instanceId } = useInstance();
  const { setStatus } = useStatus();
  const { confirmDialog } = useDialog();

  const [fields, setFields] = useState({ greeting: '', tone: '', signoff: '', length: '', dos: '', donts: '' });
  const [rawText, setRawText] = useState('');
  const [phrases, setPhrases] = useState([]);
  const [profile, setProfile] = useState(null);
  const [visual, setVisual] = useState({ mode: 'idle', message: 'Apprentissage du style en veille.' });
  const announcedInitialLoad = useRef(false);
  const announcedError = useRef(null);

  const query = useStyleQuery();
  const saveStyle = useSaveStyle();
  const learnStyle = useLearnStyle();
  const clearStyle = useClearStyle();

  useEffect(() => {
    if (!query.data) return;
    const data = query.data;
    setProfile(data.profile || null);
    const parsed = styleFieldsFromText(data.writing_style || '');
    setPhrases(parsed.phrases);
    setFields({ greeting: parsed.greeting, tone: parsed.tone, signoff: parsed.signoff, length: parsed.length, dos: parsed.dos.join('\n'), donts: parsed.donts.join('\n') });
    setRawText(data.writing_style || '');
    const learningOn = data.enabled === undefined ? null : Boolean(data.enabled);
    if (learningOn === false) setVisual({ mode: 'error', message: 'Apprentissage du style désactivé dans la configuration de l’agent.' });
    else setVisual({ mode: 'idle', message: data.source === 'learned' ? 'Style de rédaction appris actif.' : 'Prêt à apprendre des mails envoyés.' });
    if (!announcedInitialLoad.current) {
      announcedInitialLoad.current = true;
      setStatus('Style chargé.', 'ok');
    }
  }, [query.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!query.error || announcedError.current === query.error.message) return;
    announcedError.current = query.error.message;
    setStatus(`Impossible de charger le style : ${query.error.message}`, 'error');
  }, [query.error]); // eslint-disable-line react-hooks/exhaustive-deps

  const fieldsAreEmpty = !Object.values(fields).some((v) => v.trim());
  const learningEnabled = query.data?.enabled !== false;

  const handleSave = async () => {
    const writing_style = fieldsAreEmpty ? rawText : styleTextFromFields(fields, phrases);
    try {
      const data = await saveStyle.mutateAsync(writing_style);
      setStatus('Style enregistré.', 'ok');
      setProfile(data.profile || profile);
    } catch (error) {
      setStatus(`Impossible d’enregistrer le style : ${error.message}`, 'error');
    }
  };

  const handleLearn = async () => {
    setVisual({ mode: 'syncing', message: 'Lecture des mails envoyés et distillation du style...' });
    setStatus('Apprentissage du style depuis les mails envoyés...');
    try {
      const data = await learnStyle.mutateAsync();
      setVisual({ mode: 'ok', message: `Style appris à partir de ${data.sample_count || 0} mails envoyés.` });
      setStatus(`Style appris à partir de ${data.sample_count || 0} mails envoyés.`, 'ok');
    } catch (error) {
      setVisual({ mode: 'error', message: `Échec de l’apprentissage du style : ${error.message}` });
      setStatus(`Impossible d’apprendre le style : ${error.message}`, 'error');
    }
  };

  const handleClear = async () => {
    const confirmed = await confirmDialog({
      title: 'Supprimer le style appris',
      message: 'Le style de rédaction appris sera supprimé. L’agent utilisera le style par défaut.',
      confirmLabel: 'Supprimer',
      confirmIcon: 'delete_forever',
      variant: 'danger',
    });
    if (!confirmed) return;
    try {
      await clearStyle.mutateAsync();
      setStatus('Style de rédaction appris supprimé.', 'ok');
    } catch (error) {
      setStatus(`Impossible de supprimer le style : ${error.message}`, 'error');
    }
  };

  return (
    <>
      <PageHeading view="style" />
      <div className="toolbar">
        <button type="button" onClick={() => query.refetch()}>
          <span className="material-symbols-outlined" aria-hidden="true">sync</span><span>Charger le style</span>
        </button>
        <button className="primary" type="button" disabled={!learningEnabled} onClick={handleLearn}>
          <span className="material-symbols-outlined" aria-hidden="true">auto_awesome</span><span>Apprendre des mails envoyés</span>
        </button>
        <button type="button" onClick={handleSave}>
          <span className="material-symbols-outlined" aria-hidden="true">save</span><span>Enregistrer le style</span>
        </button>
        <button className="danger" type="button" onClick={handleClear}>
          <span className="material-symbols-outlined" aria-hidden="true">delete_forever</span><span>Supprimer le style appris</span>
        </button>
        <span className="counter">{instanceId}</span>
        <span className="counter">{learningEnabled ? 'apprentissage actif' : 'apprentissage désactivé'}</span>
      </div>
      <div className="notice">Les mails envoyés sont analysés par le LLM configuré. Agora AI conserve le profil de style distillé, pas le contenu brut des e-mails.</div>
      <SyncProgressBar label="Apprentissage du style" mode={visual.mode} message={visual.message} />
      <div className="editor-grid style-grid">
        <Card className="editor-card">
          <strong>Style de rédaction</strong>
          <div className="persona-grid">
            <label><span>Salutation habituelle</span><input placeholder="Bonjour Madame, Monsieur" value={fields.greeting} onChange={(e) => setFields({ ...fields, greeting: e.target.value })} /></label>
            <label><span>Ton</span><input placeholder="professionnel, vouvoiement" value={fields.tone} onChange={(e) => setFields({ ...fields, tone: e.target.value })} /></label>
            <label><span>Formule de clôture</span><input placeholder="Cordialement, Karim" value={fields.signoff} onChange={(e) => setFields({ ...fields, signoff: e.target.value })} /></label>
            <label><span>Longueur type</span>
              <select value={fields.length} onChange={(e) => setFields({ ...fields, length: e.target.value })}>
                <option value="">—</option>
                <option value="courte">Courte</option>
                <option value="moyenne">Moyenne</option>
                <option value="détaillée">Détaillée</option>
              </select>
            </label>
          </div>
          <label><span>À toujours faire (une règle par ligne)</span><textarea rows={3} spellCheck={false} value={fields.dos} onChange={(e) => setFields({ ...fields, dos: e.target.value })} /></label>
          <label><span>À ne jamais faire (une règle par ligne)</span><textarea rows={3} spellCheck={false} value={fields.donts} onChange={(e) => setFields({ ...fields, donts: e.target.value })} /></label>
          <details className="advanced-fold">
            <summary>Texte brut du style (avancé)</summary>
            <textarea rows={10} spellCheck={false} value={rawText} onChange={(e) => setRawText(e.target.value)} />
          </details>
        </Card>
        <Card>
          <strong>Profil appris</strong>
          <div className={`profile-panel ${profile ? '' : 'empty'}`.trim()}>
            <StyleProfile profile={profile} />
          </div>
        </Card>
      </div>
    </>
  );
}
