package com.agora.gateway.user;

import com.agora.gateway.agent.AgentInstance;
import com.agora.gateway.agent.AgentInstanceGrant;
import com.agora.gateway.agent.AgentInstanceGrantRepository;
import com.agora.gateway.agent.AgentInstanceRepository;
import com.agora.gateway.audit.AuditService;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.security.SecureRandom;
import java.time.Instant;
import java.util.Base64;
import java.util.List;

/**
 * Right to erasure for a platform account.
 *
 * <p>Anonymization rather than deletion: the row and its id survive, so audit
 * events, grants history and instance metadata stay internally consistent, but
 * nothing that identifies a person remains. Deleting the row outright would
 * either orphan the audit trail or cascade into it, and the trail is the thing
 * the platform sells.
 *
 * <p>This does not touch the agent's own data. Runs, drafts and learned memory
 * are keyed by username inside the email-agent and are erased there, through
 * {@code POST /gdpr/erase}. The response says so rather than the two services
 * reaching into each other.
 */
@Service
public class UserAnonymizationService {

    private final UserRepository users;
    private final UserInvitationRepository invitations;
    private final AgentInstanceGrantRepository grants;
    private final AgentInstanceRepository instances;
    private final AuditService auditService;
    private final PasswordEncoder passwordEncoder;
    private final SecureRandom random = new SecureRandom();

    public UserAnonymizationService(UserRepository users,
                                    UserInvitationRepository invitations,
                                    AgentInstanceGrantRepository grants,
                                    AgentInstanceRepository instances,
                                    AuditService auditService,
                                    PasswordEncoder passwordEncoder) {
        this.users = users;
        this.invitations = invitations;
        this.grants = grants;
        this.instances = instances;
        this.auditService = auditService;
        this.passwordEncoder = passwordEncoder;
    }

    public record Result(
            String pseudonym,
            int grantsRevoked,
            int invitationsRevoked,
            int instancesReattributed,
            int auditEventsRenamed,
            String note
    ) {}

    static String pseudonymFor(Long id) {
        return "deleted-user-" + id;
    }

    @Transactional
    public Result anonymize(AppUser user) {
        String previousUsername = user.getUsername();
        String pseudonym = pseudonymFor(user.getId());

        int invitationsRevoked = revokeInvitations(user);
        int grantsRevoked = revokeGrants(previousUsername);
        int instancesReattributed = reattributeInstances(previousUsername, pseudonym);
        int auditEventsRenamed = auditService.anonymizeActor(previousUsername, pseudonym);

        user.anonymizeUsername(pseudonym);
        user.setEmail(null);
        user.setDisplayName(null);
        user.setDepartment(null);
        user.setEnabled(false);
        // Not merely unusable — unknowable. A blank or predictable hash would
        // leave the account one bad comparison away from being reachable.
        user.setPasswordHash(passwordEncoder.encode(unguessableSecret()));
        user.setMfaEnabled(false);
        user.setMfaSecret(null);
        user.getMfaRecoveryCodeHashes().clear();
        users.save(user);

        // Recorded after the reseal so this event links to the resealed tip, and
        // under the pseudonym: the record of an erasure must not re-record the name.
        auditService.record(pseudonym, user.getRole(), "anonymize_user", "POST",
                "/users/" + user.getId() + "/anonymize", null, "success");

        return new Result(
                pseudonym,
                grantsRevoked,
                invitationsRevoked,
                instancesReattributed,
                auditEventsRenamed,
                "Agent-side data (runs, drafts, learned memory) is erased separately via POST /gdpr/erase."
        );
    }

    private int revokeInvitations(AppUser user) {
        List<UserInvitation> open = invitations.findByUserIdAndConsumedAtIsNull(user.getId());
        for (UserInvitation invitation : open) {
            // Same retirement the resend path uses: an outstanding setup link
            // would otherwise let the account be brought back to life.
            invitation.setConsumedAt(Instant.now());
            invitations.save(invitation);
        }
        return open.size();
    }

    private int revokeGrants(String username) {
        List<AgentInstanceGrant> held = grants.findByUserIdIgnoreCase(username);
        for (AgentInstanceGrant grant : held) {
            grants.deleteById(grant.getId());
        }
        return held.size();
    }

    private int reattributeInstances(String username, String pseudonym) {
        int changed = 0;
        for (AgentInstance instance : instances.findAll()) {
            if (username.equalsIgnoreCase(instance.getCreatedBy())) {
                instance.setCreatedBy(pseudonym);
                instances.save(instance);
                changed++;
            }
        }
        return changed;
    }

    private String unguessableSecret() {
        byte[] raw = new byte[32];
        random.nextBytes(raw);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(raw);
    }
}
