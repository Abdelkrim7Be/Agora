package com.agora.gateway.agent;

import com.agora.gateway.audit.AuditService;
import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/agent-instances/{instanceId}/grants")
public class InstanceGrantController {

    private final InstanceGrantService grantService;
    private final AuditService auditService;

    public InstanceGrantController(InstanceGrantService grantService, AuditService auditService) {
        this.grantService = grantService;
        this.auditService = auditService;
    }

    @GetMapping
    public ResponseEntity<List<GrantResponse>> list(Authentication auth, @PathVariable String instanceId) {
        if (!canManageGrants(auth, instanceId)) {
            return ResponseEntity.status(HttpStatus.FORBIDDEN).build();
        }
        return ResponseEntity.ok(grantService.listGrants(instanceId).stream()
                .map(GrantResponse::from)
                .toList());
    }

    private boolean canManageGrants(Authentication auth, String instanceId) {
        // Platform admin can always reach the grants endpoint, even with no active
        // grant of their own — otherwise an admin locked out of an instance (grant
        // expired, or the owner who could re-grant it is gone) has no way back in
        // except a direct database edit. addGrant() still forces any grant back to
        // an admin user through the 24h cap, so this only restores a path to
        // request time-boxed access, never a standing one.
        String role = jwtRole(auth);
        if ("admin".equals(role)) {
            return true;
        }
        return "owner".equals(grantService.effectiveRole(instanceId, auth.getName(), role).orElse(""));
    }

    @PostMapping
    public ResponseEntity<GrantResponse> add(
            Authentication auth,
            @PathVariable String instanceId,
            @Valid @RequestBody GrantRequest request) {
        if (!canManageGrants(auth, instanceId)) {
            return ResponseEntity.status(HttpStatus.FORBIDDEN).build();
        }
        AgentInstanceGrant grant = grantService.addGrant(
                instanceId, request.userId(), request.role(), auth.getName(), request.expiresAt());
        auditService.record(auth.getName(), jwtRole(auth), "grant_add", "POST",
                "/agent-instances/" + instanceId + "/grants", null,
                "granted " + request.role() + " to " + request.userId());
        return ResponseEntity.status(HttpStatus.CREATED).body(GrantResponse.from(grant));
    }

    @DeleteMapping("/{userId}")
    public ResponseEntity<Void> remove(
            Authentication auth,
            @PathVariable String instanceId,
            @PathVariable String userId) {
        if (!canManageGrants(auth, instanceId)) {
            return ResponseEntity.status(HttpStatus.FORBIDDEN).build();
        }
        grantService.removeGrant(instanceId, userId);
        auditService.record(auth.getName(), jwtRole(auth), "grant_remove", "DELETE",
                "/agent-instances/" + instanceId + "/grants/" + userId, null,
                "revoked grant for " + userId);
        return ResponseEntity.noContent().build();
    }

    @ExceptionHandler(InstanceGrantService.InvalidGrantRoleException.class)
    ResponseEntity<Map<String, String>> invalidRole(InstanceGrantService.InvalidGrantRoleException ex) {
        return ResponseEntity.badRequest().body(Map.of("error", ex.getMessage()));
    }

    @ExceptionHandler(InstanceGrantService.AdminGrantRequiresExpiryException.class)
    ResponseEntity<Map<String, String>> adminGrantRequiresExpiry(InstanceGrantService.AdminGrantRequiresExpiryException ex) {
        return ResponseEntity.badRequest().body(Map.of("error", ex.getMessage()));
    }

    @ExceptionHandler(AgentRegistryService.UnknownAgentTypeException.class)
    ResponseEntity<Map<String, String>> unknownInstance(AgentRegistryService.UnknownAgentTypeException ex) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(Map.of("error", ex.getMessage()));
    }

    private String jwtRole(Authentication auth) {
        return auth.getAuthorities().stream()
                .map(GrantedAuthority::getAuthority)
                .map(a -> a.startsWith("ROLE_") ? a.substring(5).toLowerCase() : a)
                .findFirst().orElse("");
    }

    record GrantRequest(
            @NotBlank @JsonProperty("user_id") String userId,
            @NotBlank @JsonProperty("role") String role,
            @JsonProperty("expires_at") Instant expiresAt
    ) {}

    record GrantResponse(
            @JsonProperty("agent_instance_id") String agentInstanceId,
            @JsonProperty("user_id") String userId,
            String role,
            @JsonProperty("granted_by") String grantedBy,
            @JsonProperty("granted_at") Instant grantedAt,
            @JsonProperty("expires_at") Instant expiresAt
    ) {
        static GrantResponse from(AgentInstanceGrant g) {
            return new GrantResponse(g.getAgentInstanceId(), g.getUserId(), g.getRole(),
                    g.getGrantedBy(), g.getGrantedAt(), g.getExpiresAt());
        }
    }
}
