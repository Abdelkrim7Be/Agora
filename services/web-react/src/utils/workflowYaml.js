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

export function appendWorkflowBlock(yaml, section, block) {
  const text = String(yaml || '').trimEnd();
  const sectionPattern = new RegExp(`(^|\\n)${section}:\\n`);
  if (!sectionPattern.test(text)) {
    return `${text}${text ? '\n' : ''}${section}:\n${block}\n`;
  }
  const nextTopLevel = section === 'categories' ? /\ntemplates:\n|\ncontacts:\n/ : /\ncontacts:\n/;
  const match = text.match(nextTopLevel);
  if (match) {
    const index = match.index;
    return `${text.slice(0, index)}\n${block}${text.slice(index)}\n`;
  }
  return `${text}\n${block}\n`;
}

function workflowInstructionsYamlBlock(instructions) {
  if (!instructions) return '  instructions: null';
  return `  instructions:
    sla: ${instructions.sla ? yamlScalar(instructions.sla) : 'null'}
    required_data:
${yamlList(instructions.required_data || [], '      ')}
    escalation: ${instructions.escalation ? yamlScalar(instructions.escalation) : 'null'}
    blocked_cases:
${yamlList(instructions.blocked_cases || [], '      ')}
    ask_for_missing: ${instructions.ask_for_missing ? 'true' : 'false'}`;
}

export function buildWorkflowYamlSnippet({ name, description, keywords, policy, priority, templateBody, owner, approver, routeTo, instructions, requireApproval, externalSendAllowed }) {
  const slug = workflowSlug(name);
  const routeTargets = String(routeTo || '').split(',').map((item) => item.trim()).filter(Boolean);
  const templateName = policy === 'auto_draft' && templateBody.trim() ? `${slug}_reply` : null;
  const categoryBlock = `- name: ${slug}
  display_name: ${yamlScalar(name)}
  description: ${description ? yamlScalar(description) : 'null'}
  priority: ${priority}
  owner: ${owner ? yamlScalar(owner) : 'null'}
  approver: ${approver ? yamlScalar(approver) : 'null'}
  route_to:
${yamlList(routeTargets, '    ')}
  when:
    sender_contains: []
    sender_domain: []
    subject_contains:
${yamlList(keywords.split(','), '    ')}
    labels: []
  template: ${templateName || 'null'}
  policy: ${policy}
  labels: []
  require_approval: ${requireApproval ? 'true' : 'false'}
  external_send_allowed: ${externalSendAllowed === false ? 'false' : 'true'}
${workflowInstructionsYamlBlock(instructions)}`;
  const templateBlock = templateName ? `- name: ${templateName}
  subject: null
  body: |
${templateBody.split('\n').map((line) => `    ${line}`).join('\n')}
  variables:
  - name` : '';
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
