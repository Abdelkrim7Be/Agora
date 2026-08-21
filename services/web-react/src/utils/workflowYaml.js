// Categories has no structured "create" endpoint — new workflows are spliced into the
// raw categories.yaml text client-side, then persisted via the whole-YAML PUT. Editing an
// existing category instead uses the structured PUT /categories/{name} endpoint. These pure
// string helpers are ported verbatim from the vanilla app to keep the spliced YAML valid.

const COMBINING_DIACRITICS = /[̀-ͯ]/g;

export function workflowSlug(value) {
  return String(value || '')
    .normalize('NFD')
    .replace(COMBINING_DIACRITICS, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 48) || `workflow_${Date.now()}`;
}

export function yamlScalar(value) {
  return JSON.stringify(String(value || ''));
}

export function yamlList(items, indent = '    ') {
  const clean = items.map((item) => item.trim()).filter(Boolean);
  return clean.length ? clean.map((item) => `${indent}- ${yamlScalar(item)}`).join('\n') : `${indent}[]`;
}

/**
 * A block-sequence entry's `-` marker is allowed to sit at the same indent as
 * its parent key (that's the one special case in the YAML spec) — but an
 * empty list is written as the flow scalar `[]`, which is an ordinary value
 * and must either stay on the key's own line or be indented *more* than the
 * key, never at the same indent. Writing `key:\n<same-indent>[]` is invalid
 * and was rejected outright ("could not find expected ':'") the moment a form
 * field was left empty. Keeping `[]` inline on the key line sidesteps the
 * whole issue and matches how the rest of these files already write it
 * (`sender_contains: []`, `labels: []`).
 */
function yamlFieldList(key, items, keyIndent, itemIndent) {
  const clean = items.map((item) => item.trim()).filter(Boolean);
  if (!clean.length) return `${keyIndent}${key}: []`;
  return `${keyIndent}${key}:\n${yamlList(clean, itemIndent)}`;
}

export function appendWorkflowBlock(yaml, section, block) {
  const text = String(yaml || '').trimEnd();
  const sectionPattern = new RegExp(`(^|\\n)${section}:\\n`);
  if (!sectionPattern.test(text)) {
    return `${text}${text ? '\n' : ''}${section}:\n${block}\n`;
  }
  // Match the start of the next top-level key regardless of what follows the
  // colon on that line — an empty section serializes as `templates: []`, not
  // `templates:` on its own line, and the old pattern only matched the latter,
  // so the boundary was never found and new blocks landed after the whole file
  // (past `contacts: []` too), outside any YAML structure at all.
  const nextTopLevel = section === 'categories' ? /\ntemplates:|\ncontacts:/ : /\ncontacts:/;
  const match = text.match(nextTopLevel);
  if (match) {
    const index = match.index;
    return `${text.slice(0, index)}\n${block}${text.slice(index)}\n`;
  }
  return `${text}\n${block}\n`;
}

function workflowInstructionsYamlBlock(instructions, i1, i2, itemIndent) {
  if (!instructions) return `${i1}instructions: null`;
  return `${i1}instructions:
${i2}sla: ${instructions.sla ? yamlScalar(instructions.sla) : 'null'}
${yamlFieldList('required_data', instructions.required_data || [], i2, itemIndent)}
${i2}escalation: ${instructions.escalation ? yamlScalar(instructions.escalation) : 'null'}
${yamlFieldList('blocked_cases', instructions.blocked_cases || [], i2, itemIndent)}
${i2}ask_for_missing: ${instructions.ask_for_missing ? 'true' : 'false'}`;
}

/**
 * Different instances' categories.yaml were produced by different serializers
 * (a hand-authored/wizard-seeded style that nests sequence items one level
 * deeper than their key, vs. PyYAML's default `yaml.dump` style that keeps
 * sequence items at the same indent as their key) — both are valid YAML, but
 * mixing them in one list is not. Detect which style a given file already
 * uses so a spliced-in block matches, instead of hardcoding one style and
 * corking every instance that happens to use the other.
 */
export function detectYamlListStyle(yamlText) {
  const text = String(yamlText || '');
  const markerMatch = text.match(/(?:^|\n)categories:\n([ ]*)-/);
  const markerIndent = markerMatch ? markerMatch[1].length : 2;
  const probes = ['subject_contains', 'route_to', 'required_data', 'blocked_cases'];
  let seqExtraIndent = 2;
  for (const probe of probes) {
    const probeMatch = text.match(new RegExp(`\\n([ ]*)${probe}:\\n([ ]*)-`));
    if (probeMatch) {
      seqExtraIndent = probeMatch[2].length - probeMatch[1].length;
      break;
    }
  }
  return { markerIndent, seqExtraIndent };
}

export function buildWorkflowYamlSnippet({ name, description, keywords, policy, priority, templateBody, owner, approver, routeTo, instructions, requireApproval, externalSendAllowed, existingYaml }) {
  const slug = workflowSlug(name);
  const routeTargets = String(routeTo || '').split(',').map((item) => item.trim()).filter(Boolean);
  const templateName = policy === 'auto_draft' && templateBody.trim() ? `${slug}_reply` : null;
  const { markerIndent, seqExtraIndent } = detectYamlListStyle(existingYaml);
  const i0 = ' '.repeat(markerIndent);
  const i1 = ' '.repeat(markerIndent + 2);
  const i2 = ' '.repeat(markerIndent + 4);
  const routeItemIndent = ' '.repeat(markerIndent + 2 + seqExtraIndent);
  const subjectItemIndent = ' '.repeat(markerIndent + 4 + seqExtraIndent);
  const variablesItemIndent = ' '.repeat(markerIndent + 2 + seqExtraIndent);
  const bodyIndent = ' '.repeat(markerIndent + 4);
  const categoryBlock = `${i0}- name: ${slug}
${i1}display_name: ${yamlScalar(name)}
${i1}description: ${description ? yamlScalar(description) : 'null'}
${i1}priority: ${priority}
${i1}owner: ${owner ? yamlScalar(owner) : 'null'}
${i1}approver: ${approver ? yamlScalar(approver) : 'null'}
${yamlFieldList('route_to', routeTargets, i1, routeItemIndent)}
${i1}when:
${i2}sender_contains: []
${i2}sender_domain: []
${yamlFieldList('subject_contains', keywords.split(','), i2, subjectItemIndent)}
${i2}labels: []
${i1}template: ${templateName || 'null'}
${i1}policy: ${policy}
${i1}labels: []
${i1}require_approval: ${requireApproval ? 'true' : 'false'}
${i1}external_send_allowed: ${externalSendAllowed === false ? 'false' : 'true'}
${workflowInstructionsYamlBlock(instructions, i1, i2, subjectItemIndent)}`;
  const templateBlock = templateName ? `${i0}- name: ${templateName}
${i1}subject: null
${i1}body: |
${templateBody.split('\n').map((line) => `${bodyIndent}${line}`).join('\n')}
${yamlFieldList('variables', ['name'], i1, variablesItemIndent)}` : '';
  return { categoryBlock, templateBlock };
}

export function setCategoriesYamlEnabled(yaml, enabled) {
  const next = String(yaml || '').trimStart();
  if (/^enabled:\s*(true|false)\s*$/m.test(next)) {
    return next.replace(/^enabled:\s*(true|false)\s*$/m, `enabled: ${enabled ? 'true' : 'false'}`);
  }
  return `enabled: ${enabled ? 'true' : 'false'}
${next}`;
}

export function workflowActionLabel(policy) {
  return policy === 'auto_draft'
    ? 'Rédiger une réponse à approuver'
    : policy === 'organize'
      ? 'Étiqueter / classer'
      : policy === 'ignore'
        ? 'Ignorer ou archiver'
        : 'Notifier le propriétaire';
}

export function workflowInstructionsSummary(instructions) {
  if (!instructions) return '';
  const parts = [];
  if (instructions.sla) parts.push(`SLA: ${instructions.sla}`);
  if (instructions.required_data?.length) parts.push(`required: ${instructions.required_data.join(', ')}`);
  if (instructions.escalation) parts.push(`escalation: ${instructions.escalation}`);
  if (instructions.blocked_cases?.length) parts.push(`blocked: ${instructions.blocked_cases.join(', ')}`);
  if (instructions.ask_for_missing) parts.push('asks for missing data');
  return parts.join(' / ');
}

export function splitDirectoryValues(value) {
  return String(value || '').split(/[\n,]/).map((item) => item.trim()).filter(Boolean);
}
