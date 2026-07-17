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
        if ("owner".equals(role) || "admin".equals(role)) return true;
        if (username != null && username.equals(instance.getCreatedBy())) return true;
        return Arrays.stream(instance.getAllowedRoles().split(","))
                .map(String::trim)
                .anyMatch(r -> r.equalsIgnoreCase(role));
    }

    public AgentInstance create(CreateAgentInstanceRequest request, String username) {
        GatewayProperties.AgentType type = findType(request.agentType())
                .orElseThrow(() -> new UnknownAgentTypeException(request.agentType()));
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
                request.allowedRoles() == null || request.allowedRoles().isBlank() ? "owner" : request.allowedRoles(),
                username,
                request.color() == null || request.color().isBlank() ? type.getColor() : request.color(),
                request.icon() == null || request.icon().isBlank() ? type.getIcon() : request.icon()
        );
        return instances.save(instance);
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
            return summary;
        }
        summary.put("pending_drafts", safePendingDrafts(instance, username));
        summary.put("today_cost_eur", safeTodayCost(instance, username));
        return summary;
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
            @com.fasterxml.jackson.annotation.JsonProperty("icon") String icon
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
}
