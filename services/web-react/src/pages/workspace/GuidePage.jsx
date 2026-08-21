import { PageHeading } from '../../components/layout/PageHeading';
import { Card } from '../../components/ui/Card';
import { useInstance } from '../../contexts/InstanceContext';
import { useAuth } from '../../contexts/AuthContext';

// What the agent does day to day. Everyone with access to a workspace sees this.
const EVERYDAY_SECTIONS = [
  {
    title: 'Comment un message est traité',
    icon: 'route',
    body: [
      'La boîte est relevée automatiquement. Chaque nouveau message passe d’abord par le filtre anti-bruit : newsletters, alertes, notifications automatiques et accusés de réception sont écartés sans jamais recevoir de réponse.',
      'Ce qui reste est rangé dans un de vos cas métier. La règle du cas (mots-clés du sujet ou du corps) décide en premier ; si aucune ne correspond, le cas métier du contact expéditeur s’applique.',
      'Selon la politique du cas métier, l’agent prépare un brouillon de réponse, prévient une personne en interne, ou classe et archive.',
    ],
  },
  {
    title: 'À valider',
    icon: 'inbox',
    body: [
      'Rien ne part sans vous. Un brouillon vous attend dans « À valider » : vous pouvez l’accepter, le modifier avant envoi, demander une nouvelle version en expliquant ce qui ne va pas, ou l’ignorer.',
      'Chaque correction est apprise : modifier un brouillon ajuste le style des suivants, en ignorer un ajuste le tri.',
      'Cette page regroupe tout ce qui est en attente, avec filtres par cas métier, priorité, texte et date.',
    ],
  },
  {
    title: 'Messages',
    icon: 'mail',
    body: [
      'La vue Messages montre la boîte réelle, lue et non lue, avec le verdict de l’agent en face de chaque message.',
      'Vous pouvez y rattacher un message à un cas métier : c’est la façon la plus rapide d’apprendre à l’agent une famille de messages qu’il n’avait pas reconnue.',
    ],
  },
];

// Where the owner shapes the agent's behavior.
const OWNER_SECTIONS = [
  {
    title: 'Cas métier',
    icon: 'category',
    body: [
      'Un cas métier décrit une famille de messages : banque, fournisseurs, réclamations, candidatures. Il porte des mots-clés, une priorité, et une politique.',
      'Politiques disponibles : brouillon automatique (l’agent rédige), notification interne (l’agent prévient une personne sans répondre), classement (étiquette et archive), ignorer.',
      'La politique d’approbation permet d’exiger une validation humaine même pour une action normalement automatique, et d’interdire tout envoi hors de vos domaines internes.',
      'Un cas métier dont l’expéditeur est une machine (facturation automatique, tickets) doit accepter le courrier automatique, sinon le filtre anti-bruit l’écarte.',
    ],
  },
  {
    title: 'Contacts, segments et rôles',
    icon: 'contact_mail',
    body: [
      'Contacts : l’annuaire des expéditeurs connus. Un contact peut porter un cas métier par défaut et une priorité, appliqués quand aucune règle de sujet ne correspond.',
      'Segments : des regroupements de contacts, utilisés pour les campagnes.',
      'Annuaire des rôles : associe un nom de rôle (Support, RH, Direction) à des adresses, pour que les cas métier routent vers un rôle plutôt qu’une adresse en dur.',
    ],
  },
  {
    title: 'Persona, style et signature',
    icon: 'person',
    body: [
      'Persona : qui parle — la fonction, le ton, la langue par défaut.',
      'Style : appris depuis vos messages envoyés lors de la configuration, modifiable à tout moment.',
      'Signature : ajoutée automatiquement en fin de message ; n’en écrivez pas une dans les modèles.',
    ],
  },
  {
    title: 'Règles et mémoire',
    icon: 'rule',
    body: [
      'Règles : automatismes déclenchés avant l’analyse — sur l’expéditeur, le sujet, le corps ou les étiquettes. Utiles pour les relances et la mise en sommeil.',
      'Mémoire : ce que l’agent a retenu de vos corrections. Vous pouvez la relire et la corriger.',
    ],
  },
];

// Platform surface. Admin only.
const ADMIN_SECTIONS = [
  {
    title: 'Capacités',
    icon: 'shield',
    body: [
      'Active ou désactive les outils que l’agent peut utiliser : envoi, brouillons, étiquettes, archivage, transfert, réponse à tous.',
      'Un outil désactivé disparaît complètement du champ d’action de l’agent, quels que soient les cas métier.',
    ],
  },
  {
    title: 'Permissions',
    icon: 'admin_panel_settings',
    body: [
      'Gère les comptes et leurs rôles. L’administration de la plateforme — création d’instances, droits, audit, coûts — est réservée au rôle administrateur.',
      'Le propriétaire d’une instance gère le contenu de son espace de travail : cas métier, contacts, règles, validations.',
    ],
  },
  {
    title: 'Politique de sécurité',
    icon: 'policy',
    body: [
      'Chaque action proposée par l’agent est autorisée, refusée ou soumise à validation humaine par la politique de sécurité, avant exécution.',
      'Les destinataires sont vérifiés à ce moment-là, ainsi que les plafonds de taille et de fréquence d’envoi.',
      'Le contenu entrant est analysé pour détecter les tentatives d’injection : un message suspect est retenu et ne produit jamais d’envoi.',
    ],
  },
  {
    title: 'File d’échec et coûts',
    icon: 'monitoring',
    body: [
      'DLQ : les messages dont le traitement a échoué après plusieurs tentatives, avec possibilité de les remettre en file.',
      'Coûts : consommation des modèles par période, pour suivre la dépense réelle de l’instance.',
    ],
  },
  {
    title: 'Signalements et audit plateforme',
    icon: 'flag',
    body: [
      'N’importe quel utilisateur connecté peut signaler un problème depuis le bouton drapeau, en haut de l’écran. Les administrateurs en sont notifiés par e-mail (si un relais SMTP est configuré) et retrouvent chaque signalement dans « Signalements », avec une suggestion d’action générée par IA.',
      '« Audit plateforme » lance en un clic une vérification de toutes les instances d’agents (santé, file d’erreurs) et de l’accès à la base de la plateforme, avec un verdict sain / avertissement / critique et un historique des audits passés.',
    ],
  },
];

/** Stable anchor id from a section title, so a link survives a copy-paste. */
function sectionId(title) {
  return 'guide-' + String(title)
    .toLowerCase()
    .normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
}

function GuideSection({ section }) {
  return (
    <section className="guide-section" id={sectionId(section.title)}>
      <h3>
        <span className="material-symbols-outlined" aria-hidden="true">{section.icon}</span>
        <span>{section.title}</span>
      </h3>
      {section.body.map((paragraph, index) => (
        <p key={index}>{paragraph}</p>
      ))}
    </section>
  );
}

/**
 * Contents, not tabs.
 *
 * A guide is scanned, not stepped through: you arrive knowing roughly what you
 * are after and want to see whether it is covered. Tabs hide every section but
 * one, so the reader has to open each in turn to find out what exists. A list
 * of anchors shows the whole map and jumps.
 */
function GuideContents({ groups }) {
  return (
    <nav className="guide-contents" aria-label="Sommaire du guide">
      {groups.map((group) => (
        <div key={group.label}>
          <span className="label-mono">{group.label}</span>
          <ul>
            {group.sections.map((section) => (
              <li key={section.title}>
                <a href={`#${sectionId(section.title)}`}>{section.title}</a>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}

export default function GuidePage() {
  const { currentInstance, hasRole } = useInstance();
  const { globalRole } = useAuth();
  const isAdmin = (globalRole || '').toLowerCase() === 'admin';
  const isOwner = hasRole('owner');

  const groups = [
    { label: 'Au quotidien', sections: EVERYDAY_SECTIONS },
    ...(isOwner || isAdmin ? [{ label: 'Configurer l’agent', sections: OWNER_SECTIONS }] : []),
    ...(isAdmin ? [{ label: 'Administration', sections: ADMIN_SECTIONS }] : []),
  ];

  return (
    <div className="guide-page guide-layout">
      <PageHeading view="guide" />
      <GuideContents groups={groups} />
      <Card>
        <div className="card-header">
          <div>
            <h2>Guide de {currentInstance?.display_name || 'l’espace de travail'}</h2>
            <div className="meta">
              <span>
                {isAdmin
                  ? 'Vue administrateur : usage quotidien, configuration et plateforme.'
                  : isOwner
                    ? 'Vue propriétaire : usage quotidien et configuration de l’agent.'
                    : 'Vue lecture : ce que fait l’agent au quotidien.'}
              </span>
            </div>
          </div>
        </div>

        {EVERYDAY_SECTIONS.map((section) => (
          <GuideSection key={section.title} section={section} />
        ))}

        {isOwner || isAdmin ? (
          <>
            <h2 className="guide-divider">Configurer l’agent</h2>
            {OWNER_SECTIONS.map((section) => (
              <GuideSection key={section.title} section={section} />
            ))}
          </>
        ) : null}

        {isAdmin ? (
          <>
            <h2 className="guide-divider">Administration de la plateforme</h2>
            {ADMIN_SECTIONS.map((section) => (
              <GuideSection key={section.title} section={section} />
            ))}
          </>
        ) : null}
      </Card>
    </div>
  );
}
