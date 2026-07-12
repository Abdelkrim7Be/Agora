package com.agora.gateway.agent;

import org.springframework.stereotype.Service;

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
 *   1. If the caller's global JWT role is "owner" → effective role is "owner" for every instance.
 *   2. If the caller has an explicit grant for this instance → use that grant's role.
 *   3. If the instance's allowedRoles list includes the JWT role → use the JWT role.
 *   4. Otherwise → no access.
 */
@Service
public class InstanceGrantService {

    private static final Set<String> VALID_ROLES = Set.of("owner", "approver", "viewer");

    private final AgentInstanceGrantRepository grants;
    private final AgentInstanceRepository instances;

    public InstanceGrantService(AgentInstanceGrantRepository grants, AgentInstanceRepository instances) {
        this.grants = grants;
        this.instances = instances;
    }

    /**
     * Resolve the effective instance role for a caller. Returns empty if no access.
     */
    public Optional<String> effectiveRole(String agentInstanceId, String userId, String jwtRole) {
        // owner and admin (the IT superuser tier, above owner) are owner-equivalent on
        // every instance. The gateway SecurityConfig still gates which verbs admin can
        // reach, so this only broadens proxied reads, not approvals/mutations.
        if ("owner".equals(jwtRole) || "admin".equals(jwtRole)) return Optional.of("owner");

        Optional<AgentInstanceGrant> grant = grants.findByAgentInstanceIdAndUserId(agentInstanceId, userId);
        if (grant.isPresent()) return Optional.of(grant.get().getRole());

        Optional<AgentInstance> instance = instances.findById(agentInstanceId);
        if (instance.isPresent()) {
            boolean allowed = java.util.Arrays.stream(instance.get().getAllowedRoles().split(","))
                    .map(String::trim)
                    .anyMatch(r -> r.equalsIgnoreCase(jwtRole));
            if (allowed) return Optional.of(jwtRole);
        }

        return Optional.empty();
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
        return grants.findByAgentInstanceId(agentInstanceId);
    }

    public AgentInstanceGrant addGrant(String agentInstanceId, String userId, String role, String grantedBy) {
        if (!VALID_ROLES.contains(role)) {
            throw new InvalidGrantRoleException(role);
        }
        if (!instances.existsById(agentInstanceId)) {
            throw new AgentRegistryService.UnknownAgentTypeException(agentInstanceId);
        }
        Optional<AgentInstanceGrant> existing = grants.findByAgentInstanceIdAndUserId(agentInstanceId, userId);
        if (existing.isPresent()) {
            AgentInstanceGrant g = existing.get();
            g.setRole(role);
            return grants.save(g);
        }
        return grants.save(new AgentInstanceGrant(agentInstanceId, userId, role, grantedBy));
    }

    public void removeGrant(String agentInstanceId, String userId) {
        grants.findByAgentInstanceIdAndUserId(agentInstanceId, userId)
                .ifPresent(g -> grants.deleteById(g.getId()));
    }

    public static class InvalidGrantRoleException extends RuntimeException {
        public InvalidGrantRoleException(String role) {
            super("invalid grant role: " + role + ". Must be one of: owner, approver, viewer");
        }
    }
}
