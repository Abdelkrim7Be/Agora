package com.agora.gateway.agent;

import com.agora.gateway.user.UserRepository;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.Optional;
import java.util.Set;

/**
 * Manages per-instance role grants and resolves the effective role a caller holds
 * for a specific agent instance.
 *
 * Role hierarchy (highest to lowest): owner > approver > viewer.
 *
 * Effective role resolution order:
 *   1. If the caller created this instance → effective role is "owner".
 *   2. If the caller has an explicit grant for this instance → use that grant's role.
 *   3. If the instance's allowedRoles list includes the JWT role → use the JWT role.
 *   4. Otherwise → no access.
 */
@Service
public class InstanceGrantService {

    private static final Set<String> VALID_ROLES = Set.of("owner", "approver", "viewer");
    private static final long DEFAULT_VIEWER_GRANT_HOURS = 24;

    private final AgentInstanceGrantRepository grants;
    private final AgentInstanceRepository instances;
    private final UserRepository users;

    public InstanceGrantService(AgentInstanceGrantRepository grants, AgentInstanceRepository instances, UserRepository users) {
        this.grants = grants;
        this.instances = instances;
        this.users = users;
    }

    /**
     * Resolve the effective instance role for a caller. Returns empty if no access.
     */
    public Optional<String> effectiveRole(String agentInstanceId, String userId, String jwtRole) {
        // Platform admin remains a platform role. Mailbox access is scoped by
        // creator ownership, explicit grants, or deliberately widened roles.
        Optional<AgentInstance> instance = instances.findById(agentInstanceId);
        if (instance.isPresent()) {
            if (userId != null && userId.equals(instance.get().getCreatedBy())) return Optional.of("owner");
            Optional<AgentInstanceGrant> grant = grants.findByAgentInstanceIdAndUserId(agentInstanceId, userId);
            if (grant.isPresent() && active(grant.get())) return Optional.of(grant.get().getRole());
            boolean allowed = java.util.Arrays.stream(instance.get().getAllowedRoles().split(","))
                    .map(String::trim)
                    .anyMatch(r -> r.equalsIgnoreCase(jwtRole));
            if (allowed) return Optional.of(jwtRole);
        }

        return Optional.empty();
    }

    /**
     * Resolve the role allowed to read the mailbox itself, as opposed to seeing
     * that the instance exists and administering it. Two steps of effectiveRole()
     * deliberately do not carry over:
     *
     *   - allowedRoles. Naming a global role there is a visibility setting, and
     *     letting it double as message access meant any other account holding
     *     that role could read the mailbox by naming the instance in
     *     X-Agora-Agent-Instance.
     *   - a grant held by a platform admin. Admins can grant themselves one to
     *     get an instance unstuck; that is an operations path, not a licence to
     *     read someone's mail, and the attempt is audited as a denial.
     *
     * What is left is the creator plus whoever the creator deliberately
     * delegated to.
     */
    public Optional<String> contentRole(String agentInstanceId, String userId) {
        Optional<AgentInstance> instance = instances.findById(agentInstanceId);
        if (instance.isPresent()) {
            if (userId != null && userId.equals(instance.get().getCreatedBy())) return Optional.of("owner");
            if (isPlatformAdmin(userId)) return Optional.empty();
            Optional<AgentInstanceGrant> grant = grants.findByAgentInstanceIdAndUserId(agentInstanceId, userId);
            if (grant.isPresent() && active(grant.get())) return Optional.of(grant.get().getRole());
        }
        return Optional.empty();
    }

    private boolean isPlatformAdmin(String userId) {
        return users.findByUsername(userId)
                .map(user -> "admin".equalsIgnoreCase(user.getRole()))
                .orElse(false);
    }

    /**
     * True when the effective role is sufficient for the requested operation tier.
     * "write" requires owner; "approve" requires approver or owner; "read" requires any granted role.
     */
    public boolean isAuthorized(String effectiveRole, String tier) {
        return switch (tier) {
            case "write"   -> "owner".equals(effectiveRole);
            case "approve" -> "owner".equals(effectiveRole) || "approver".equals(effectiveRole);
            case "read"    -> effectiveRole != null;
            default        -> false;
        };
    }

    public List<AgentInstanceGrant> listGrants(String agentInstanceId) {
        return grants.findByAgentInstanceId(agentInstanceId).stream()
                .filter(InstanceGrantService::active)
                .toList();
    }

    public AgentInstanceGrant addGrant(String agentInstanceId, String userId, String role, String grantedBy) {
        return addGrant(agentInstanceId, userId, role, grantedBy, null);
    }

    public AgentInstanceGrant addGrant(String agentInstanceId, String userId, String role, String grantedBy, Instant expiresAt) {
        if (!VALID_ROLES.contains(role)) {
            throw new InvalidGrantRoleException(role);
        }
        if (!instances.existsById(agentInstanceId)) {
            throw new AgentRegistryService.UnknownAgentTypeException(agentInstanceId);
        }
        Instant resolvedExpiresAt = resolveExpiry(role, expiresAt);
        requireAdminGrantExpiry(userId, expiresAt, resolvedExpiresAt);
        Optional<AgentInstanceGrant> existing = grants.findByAgentInstanceIdAndUserId(agentInstanceId, userId);
        if (existing.isPresent()) {
            AgentInstanceGrant g = existing.get();
            g.setRole(role);
            g.setExpiresAt(resolvedExpiresAt);
            return grants.save(g);
        }
        AgentInstanceGrant grant = new AgentInstanceGrant(agentInstanceId, userId, role, grantedBy);
        grant.setExpiresAt(resolvedExpiresAt);
        return grants.save(grant);
    }

    public void removeGrant(String agentInstanceId, String userId) {
        grants.findByAgentInstanceIdAndUserId(agentInstanceId, userId)
                .ifPresent(g -> grants.deleteById(g.getId()));
    }

    /**
     * Retroactively applies the admin-grant expiry cap. requireAdminGrantExpiry()
     * only runs when a grant is created or edited — a user promoted to admin after
     * already holding a permanent (or long-dated) instance grant would otherwise
     * keep standing tenant access forever, silently defeating the cap. Only ever
     * shrinks an expiry, never extends one.
     */
    public void capGrantsForNewAdmin(String userId) {
        Instant latestAllowed = Instant.now().plus(DEFAULT_VIEWER_GRANT_HOURS, ChronoUnit.HOURS);
        grants.findByUserIdIgnoreCase(userId).stream()
                .filter(InstanceGrantService::active)
                .filter(g -> g.getExpiresAt() == null || g.getExpiresAt().isAfter(latestAllowed))
                .forEach(g -> {
                    g.setExpiresAt(latestAllowed);
                    grants.save(g);
                });
    }

    public static class InvalidGrantRoleException extends RuntimeException {
        public InvalidGrantRoleException(String role) {
            super("invalid grant role: " + role + ". Must be one of: owner, approver, viewer");
        }
    }

    public static class AdminGrantRequiresExpiryException extends RuntimeException {
        public AdminGrantRequiresExpiryException() {
            super("grants to admin users must include expires_at within 24 hours");
        }
    }

    public static boolean active(AgentInstanceGrant grant) {
        return grant.getExpiresAt() == null || grant.getExpiresAt().isAfter(Instant.now());
    }

    private Instant resolveExpiry(String role, Instant expiresAt) {
        if (expiresAt != null) return expiresAt;
        if ("viewer".equals(role)) return Instant.now().plus(DEFAULT_VIEWER_GRANT_HOURS, ChronoUnit.HOURS);
        return null;
    }

    private void requireAdminGrantExpiry(String userId, Instant requestedExpiresAt, Instant resolvedExpiresAt) {
        if (!isPlatformAdmin(userId)) {
            return;
        }
        Instant latestAllowed = Instant.now().plus(DEFAULT_VIEWER_GRANT_HOURS, ChronoUnit.HOURS);
        if (requestedExpiresAt == null || resolvedExpiresAt == null || resolvedExpiresAt.isAfter(latestAllowed)) {
            throw new AdminGrantRequiresExpiryException();
        }
    }
}
