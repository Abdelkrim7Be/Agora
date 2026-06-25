package com.agora.gateway.agent;

import com.agora.gateway.config.GatewayProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.agora.gateway.audit.AuditService;
import jakarta.validation.Valid;
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
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.List;
import java.util.Map;

@RestController
public class AgentRegistryController {

    private final AgentRegistryService service;
    private final AuditService auditService;

    public AgentRegistryController(AgentRegistryService service, AuditService auditService) {
        this.service = service;
        this.auditService = auditService;
    }

    @GetMapping("/agents")
    public List<AgentTypeResponse> agents() {
        return service.agentTypes().stream()
                .map(type -> AgentTypeResponse.from(type, service.serviceHealth(type)))
                .toList();
    }

    @GetMapping("/agent-instances")
    public List<AgentInstanceResponse> instances(Authentication auth) {
        String username = auth.getName();
        String role = role(auth);
        return service.visibleInstances(username, role).stream()
                .map(instance -> AgentInstanceResponse.from(instance, service.summary(instance, username)))
                .toList();
    }

    @PostMapping("/agent-instances")
    public ResponseEntity<AgentInstanceResponse> create(Authentication auth,
            @Valid @RequestBody AgentRegistryService.CreateAgentInstanceRequest request) {
        AgentInstance instance = service.create(request, auth.getName());
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(AgentInstanceResponse.from(instance, service.summary(instance, auth.getName())));
    }

    @DeleteMapping("/agent-instances/{instanceId}")
    public ResponseEntity<AgentInstanceResponse> deactivate(Authentication auth,
            @PathVariable String instanceId) {
        AgentInstance instance = service.deactivate(instanceId);
        auditService.record(auth.getName(), role(auth), "deactivate_instance", "DELETE",
                "/agent-instances/" + instanceId, null, "deactivated");
        return ResponseEntity.ok(AgentInstanceResponse.from(instance, Map.of()));
    }

    @ExceptionHandler(AgentRegistryService.UnknownAgentTypeException.class)
    ResponseEntity<Map<String, String>> unknownAgentType(AgentRegistryService.UnknownAgentTypeException ex) {
        return ResponseEntity.badRequest().body(Map.of("error", ex.getMessage()));
    }

    @ExceptionHandler(AgentRegistryService.DuplicateAgentInstanceException.class)
    ResponseEntity<Map<String, String>> duplicateAgentInstance(AgentRegistryService.DuplicateAgentInstanceException ex) {
        return ResponseEntity.status(HttpStatus.CONFLICT).body(Map.of("error", ex.getMessage()));
    }

    private String role(Authentication auth) {
        return auth.getAuthorities().stream()
                .map(GrantedAuthority::getAuthority)
                .map(a -> a.startsWith("ROLE_") ? a.substring(5).toLowerCase() : a)
                .findFirst()
                .orElse("");
    }

    record AgentTypeResponse(
            String id,
            @JsonProperty("display_name") String displayName,
            String description,
            List<String> capabilities,
            @JsonProperty("base_path") String basePath,
            @JsonProperty("health_path") String healthPath,
            String color,
            String icon,
            String health
    ) {
        static AgentTypeResponse from(GatewayProperties.AgentType type, String health) {
            return new AgentTypeResponse(type.getId(), type.getDisplayName(), type.getDescription(),
                    type.getCapabilities(), type.getBasePath(), type.getHealthPath(),
                    type.getColor(), type.getIcon(), health);
        }
    }

    record AgentInstanceResponse(
            String id,
            @JsonProperty("agent_type") String agentType,
            @JsonProperty("display_name") String displayName,
            @JsonProperty("mailbox_identity") String mailboxIdentity,
            String description,
            String status,
            @JsonProperty("base_path") String basePath,
            @JsonProperty("allowed_roles") String allowedRoles,
            @JsonProperty("created_by") String createdBy,
            String color,
            String icon,
            @JsonProperty("created_at") Instant createdAt,
            Map<String, Object> summary
    ) {
        static AgentInstanceResponse from(AgentInstance instance, Map<String, Object> summary) {
            return new AgentInstanceResponse(instance.getId(), instance.getAgentType(), instance.getDisplayName(),
                    instance.getMailboxIdentity(), instance.getDescription(), instance.getStatus(),
                    instance.getBasePath(), instance.getAllowedRoles(), instance.getCreatedBy(),
                    instance.getColor(), instance.getIcon(), instance.getCreatedAt(), summary);
        }
    }
}
