package com.agora.gateway.agent;

import com.agora.gateway.config.GatewayProperties;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.net.URI;
import java.time.Instant;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

@Service
public class AgentRegistryService {

    private static final long HEALTH_CACHE_TTL_SECONDS = 5;
    private static final int SELF_SERVICE_EMAIL_INSTANCE_LIMIT = 5;
    private static final String EMAIL_AGENT_TYPE = "email-agent";

    private final GatewayProperties props;
    private final AgentInstanceRepository instances;
    private final AgentInstanceGrantRepository grants;
    private final RestClient restClient;
    private final ObjectMapper objectMapper;
    private final java.util.concurrent.ConcurrentHashMap<String, HealthCacheEntry> healthCache =
            new java.util.concurrent.ConcurrentHashMap<>();

    private record HealthCacheEntry(String health, Instant expiresAt) {}

    public AgentRegistryService(GatewayProperties props, AgentInstanceRepository instances,
                                AgentInstanceGrantRepository grants, RestClient.Builder builder,
                                ObjectMapper objectMapper) {
        this.props = props;
        this.instances = instances;
        this.grants = grants;
        this.restClient = builder.build();
        this.objectMapper = objectMapper;
    }

    public List<GatewayProperties.AgentType> agentTypes() {
        return props.getAgentTypes();
    }

    public Optional<GatewayProperties.AgentType> findType(String id) {
        return props.getAgentTypes().stream().filter(t -> t.getId().equals(id)).findFirst();
    }

    /** Fresh probe on every call — /agents keeps its live health semantics. */
    public String serviceHealth(GatewayProperties.AgentType type) {
        String health = probeServiceHealth(type);
        healthCache.put(type.getId(), new HealthCacheEntry(health, Instant.now().plusSeconds(HEALTH_CACHE_TTL_SECONDS)));
        return health;
    }

    /** Instance listings probe the same upstream once per instance; a short TTL
     *  collapses those duplicate probes without hiding a real outage for long. */
    public String cachedServiceHealth(GatewayProperties.AgentType type) {
        HealthCacheEntry cached = healthCache.get(type.getId());
        if (cached != null && cached.expiresAt().isAfter(Instant.now())) {
            return cached.health();
        }
        return serviceHealth(type);
    }

    private String probeServiceHealth(GatewayProperties.AgentType type) {
        try {
            var response = restClient.get()
                    .uri(URI.create(upstreamBase(type) + type.getHealthPath()))
                    .retrieve()
                    .toBodilessEntity();
            return response.getStatusCode().is2xxSuccessful() ? "healthy" : "unhealthy";
        } catch (UnknownAgentTypeException ex) {
            return "unconfigured";
        } catch (RestClientException ex) {
            return "unreachable";
        }
    }

    public Optional<AgentInstance> findInstance(String id) {
        return instances.findById(id);
    }

    public List<AgentInstance> visibleInstances(String username, String role) {
        return instances.findAll().stream()
                .filter(instance -> canView(instance, username, role))
                .filter(instance -> !"inactive".equalsIgnoreCase(instance.getStatus())
                        || "owner".equals(role)
                        || "admin".equals(role))
                .toList();
    }

    public boolean canView(AgentInstance instance, String username, String role) {
        if ("admin".equals(role)) return true;
        if (username != null && username.equals(instance.getCreatedBy())) return true;
        if (username != null && grants.findByAgentInstanceIdAndUserId(instance.getId(), username).isPresent()) return true;
        return Arrays.stream(instance.getAllowedRoles().split(","))
                .map(String::trim)
                .anyMatch(r -> r.equalsIgnoreCase(role));
    }

    public AgentInstance create(CreateAgentInstanceRequest request, String username, String role) {
        GatewayProperties.AgentType type = findType(request.agentType())
                .orElseThrow(() -> new UnknownAgentTypeException(request.agentType()));
        boolean admin = "admin".equals(role);
        if (!admin) {
            if (!EMAIL_AGENT_TYPE.equals(type.getId())) {
                throw new ForbiddenAgentInstanceOperationException("users can only create email-agent instances");
            }
            long ownedEmailAgents = instances.countByCreatedByAndAgentType(username, EMAIL_AGENT_TYPE);
            if (ownedEmailAgents >= SELF_SERVICE_EMAIL_INSTANCE_LIMIT) {
                throw new AgentInstanceLimitException(SELF_SERVICE_EMAIL_INSTANCE_LIMIT);
            }
        }
        String id = request.id() == null || request.id().isBlank()
                ? slug(request.displayName()) + "-" + UUID.randomUUID().toString().substring(0, 8)
                : slug(request.id());
        if (instances.existsById(id)) {
            throw new DuplicateAgentInstanceException(id);
        }
        AgentInstance instance = new AgentInstance(
                id,
                type.getId(),
                request.displayName(),
                request.mailboxIdentity(),
                request.description(),
                request.status() == null || request.status().isBlank() ? "active" : request.status(),
                type.getBasePath(),
                admin && request.allowedRoles() != null && !request.allowedRoles().isBlank() ? request.allowedRoles() : "owner",
                username,
                request.color() == null || request.color().isBlank() ? type.getColor() : request.color(),
                request.icon() == null || request.icon().isBlank() ? type.getIcon() : request.icon()
        );
        AgentInstance saved = instances.save(instance);
        String assignedTo = admin ? request.assignedTo() : null;
        if (assignedTo != null && !assignedTo.isBlank() && !assignedTo.equals(username)) {
            grants.save(new AgentInstanceGrant(saved.getId(), assignedTo, "owner", username));
        }
        return saved;
    }

    public Map<String, Object> summary(AgentInstance instance, String username) {
        Map<String, Object> summary = new LinkedHashMap<>();
        if ("inactive".equalsIgnoreCase(instance.getStatus())) {
            summary.put("service_health", "inactive");
        } else {
            findType(instance.getAgentType()).ifPresent(type -> summary.put("service_health", cachedServiceHealth(type)));
        }
        if (!"email-agent".equals(instance.getAgentType())) {
            return summary;
        }

        summary.put("mailbox_connection", instance.getMailboxIdentity() == null || instance.getMailboxIdentity().isBlank()
                ? "unknown" : "configured");
        summary.put("sync_status", "unknown");
        if ("inactive".equalsIgnoreCase(instance.getStatus())) {
            summary.put("pending_drafts", 0);
            summary.put("today_cost_eur", 0.0);
            summary.put("setup_status", "unknown");
            return summary;
        }
        summary.put("pending_drafts", safePendingDrafts(instance, username));
        summary.put("today_cost_eur", safeTodayCost(instance, username));
        putSetupStatus(summary, instance, username);
        return summary;
    }

    /** Instance listing must not fail if the agent is unreachable — same
     *  degrade-to-"unknown" contract as the existing health lookup. */
    private void putSetupStatus(Map<String, Object> summary, AgentInstance instance, String username) {
        try {
            JsonNode root = getJson(instance, username, "/instance-setup");
            summary.put("setup_status", root.path("status").asText("unknown"));
            summary.put("setup_percent", root.path("progress").path("percent").asInt(0));
            java.util.List<String> failedSteps = new java.util.ArrayList<>();
            JsonNode steps = root.path("steps");
            if (steps.isArray()) {
                for (JsonNode step : steps) {
                    if ("failed".equals(step.path("status").asText())) {
                        failedSteps.add(step.path("step_key").asText());
                    }
                }
            }
            summary.put("setup_failed_steps", failedSteps);
        } catch (RuntimeException ex) {
            summary.put("setup_status", "unknown");
            summary.put("setup_percent", 0);
            summary.put("setup_failed_steps", java.util.List.of());
        }
    }

    /**
     * Mailbox connection state for the overview console.
     *
     * Degrades per instance rather than per request: a mailbox whose agent is
     * unreachable comes back with `reachable=false` and a reason, so one broken
     * mailbox shows as one bad row instead of failing the whole page.
     */
    public Map<String, Object> mailboxStatus(AgentInstance instance, String username) {
        Map<String, Object> status = new LinkedHashMap<>();
        status.put("provider", "gmail");
        if ("inactive".equalsIgnoreCase(instance.getStatus())) {
            status.put("reachable", false);
            status.put("connection_status", "inactive");
            status.put("pending_drafts", 0);
            return status;
        }
        try {
            JsonNode root = getJson(instance, username, "/sync/status");
            status.put("provider", root.path("provider").asText("gmail"));
            status.put("connection_status", root.path("connection_status").asText("unknown"));
            status.put("sync_mode", root.path("sync_mode").asText(""));
            status.put("paused", root.path("paused").asBoolean(false));
            status.put("last_success_at", nullableText(root.path("last_success_at")));
            status.put("last_failure_at", nullableText(root.path("last_failure_at")));
            status.put("last_error", nullableText(root.path("last_error")));
            status.put("reachable", true);
        } catch (RuntimeException ex) {
            status.put("reachable", false);
            status.put("connection_status", "unknown");
            status.put("error", ex.getMessage() == null ? ex.getClass().getSimpleName() : ex.getMessage());
        }
        status.put("pending_drafts", safePendingDrafts(instance, username));
        return status;
    }

    private static String nullableText(JsonNode node) {
        return node.isMissingNode() || node.isNull() ? null : node.asText();
    }

    private int safePendingDrafts(AgentInstance instance, String username) {
        try {
            JsonNode root = getJson(instance, username, "/drafts");
            JsonNode drafts = root.get("drafts");
            return drafts != null && drafts.isArray() ? drafts.size() : 0;
        } catch (RuntimeException ex) {
            return 0;
        }
    }

    private double safeTodayCost(AgentInstance instance, String username) {
        try {
            JsonNode root = getJson(instance, username, "/costs/summary?period=day");
            JsonNode cost = root.path("totals").path("cost_eur");
            return cost.isNumber() ? cost.asDouble() : 0.0;
        } catch (RuntimeException ex) {
            return 0.0;
        }
    }

    private JsonNode getJson(AgentInstance instance, String username, String path) {
        String body = restClient.get()
                .uri(URI.create(upstreamBase(instance.getAgentType()) + path))
                .header("X-Agora-User", username == null ? "" : username)
                .header("X-Agora-Agent-Instance", instance.getId())
                .retrieve()
                .body(String.class);
        try {
            return objectMapper.readTree(body == null ? "{}" : body);
        } catch (Exception ex) {
            throw new IllegalStateException("Invalid upstream JSON", ex);
        }
    }

    private String upstreamBase(GatewayProperties.AgentType type) {
        return upstreamBase(type.getId());
    }

    private String upstreamBase(String agentType) {
        if ("email-agent".equals(agentType)) {
            return props.getUpstream().getEmailAgentUrl();
        }
        throw new UnknownAgentTypeException(agentType);
    }

    private String slug(String value) {
        String slug = value == null ? "agent-instance" : value.toLowerCase(Locale.ROOT)
                .replaceAll("[^a-z0-9]+", "-")
                .replaceAll("^-|-$", "");
        return slug.isBlank() ? "agent-instance" : slug;
    }

    public AgentInstance deactivate(String instanceId) {
        AgentInstance instance = instances.findById(instanceId)
                .orElseThrow(() -> new UnknownAgentTypeException(instanceId));
        instance.setStatus("inactive");
        return instances.save(instance);
    }

    public AgentInstance activate(String instanceId) {
        AgentInstance instance = instances.findById(instanceId)
                .orElseThrow(() -> new UnknownAgentTypeException(instanceId));
        instance.setStatus("active");
        return instances.save(instance);
    }

    public AgentInstance update(String instanceId, UpdateAgentInstanceRequest request) {
        AgentInstance instance = instances.findById(instanceId)
                .orElseThrow(() -> new UnknownAgentTypeException(instanceId));
        instance.setDisplayName(request.displayName());
        return instances.save(instance);
    }

    public void delete(String instanceId) {
        AgentInstance instance = instances.findById(instanceId)
                .orElseThrow(() -> new UnknownAgentTypeException(instanceId));
        grants.findByAgentInstanceId(instanceId).forEach(grant -> grants.deleteById(grant.getId()));
        instances.delete(instance);
    }

    public record CreateAgentInstanceRequest(
            @com.fasterxml.jackson.annotation.JsonProperty("id") String id,
            @jakarta.validation.constraints.NotBlank @com.fasterxml.jackson.annotation.JsonProperty("agent_type") String agentType,
            @jakarta.validation.constraints.NotBlank @com.fasterxml.jackson.annotation.JsonProperty("display_name") String displayName,
            @com.fasterxml.jackson.annotation.JsonProperty("mailbox_identity") String mailboxIdentity,
            @com.fasterxml.jackson.annotation.JsonProperty("description") String description,
            @com.fasterxml.jackson.annotation.JsonProperty("status") String status,
            @com.fasterxml.jackson.annotation.JsonProperty("allowed_roles") String allowedRoles,
            @com.fasterxml.jackson.annotation.JsonProperty("color") String color,
            @com.fasterxml.jackson.annotation.JsonProperty("icon") String icon,
            // Who this instance is actually for. Left blank, it belongs to no one but
            // global owner/admin (who see every instance regardless) — an explicit
            // pick here is what grants a specific person ownership of it.
            @com.fasterxml.jackson.annotation.JsonProperty("assigned_to") String assignedTo
    ) {}

    public record UpdateAgentInstanceRequest(
            @jakarta.validation.constraints.NotBlank @com.fasterxml.jackson.annotation.JsonProperty("display_name") String displayName
    ) {}

    public static class UnknownAgentTypeException extends RuntimeException {
        public UnknownAgentTypeException(String agentType) {
            super("unknown agent type: " + agentType);
        }
    }

    public static class DuplicateAgentInstanceException extends RuntimeException {
        public DuplicateAgentInstanceException(String id) {
            super("agent instance already exists: " + id);
        }
    }

    public static class AgentInstanceLimitException extends RuntimeException {
        public AgentInstanceLimitException(int limit) {
            super("email-agent instance limit reached: " + limit);
        }
    }

    public static class ForbiddenAgentInstanceOperationException extends RuntimeException {
        public ForbiddenAgentInstanceOperationException(String message) {
            super(message);
        }
    }
}
